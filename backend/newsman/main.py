from uuid import UUID
from datetime import date, datetime
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from newsman.agents.graph import run_daily_graph
from newsman.config import get_settings
from newsman.db import get_db, init_db
from newsman.models import Article, Source, SourceHealthState, Feedback
from newsman.services.preferences import preferences, set_preference, clear_preferences
from newsman.schemas import (
    ArticleRead,
    DailyRunResponse,
    DigestRead,
    FeedbackCreate,
    SchedulerStatus,
    SourceCreate,
    SourceHealthRead,
    SourceRead,
    SourceEnabledUpdate,
    DigestRollback,
)
from newsman.services.revisions import (
    history,
    read_digest,
    serialize_digest,
    compare,
    rollback_head,
)
from newsman.services.runs import run_status
from newsman.services.run_lock import RunBusy, RunConflict
from newsman.services.source_health import build_source_health, activation_allowed
from newsman.services.collector import probe_source
from newsman.services.sources import sync_default_sources
from newsman.services.selection import news_window
from newsman.tasks import run_daily_digest
from newsman.runtime_identity import workspace_id, INSTANCE_ID
from newsman.services.runtime import runtime_status

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    import os

    return {
        "status": "ok",
        "app": settings.app_name,
        "product": "newsman-agent",
        "role": "backend",
        "workspace_id": workspace_id(),
        "instance_id": INSTANCE_ID,
        "pid": os.getpid(),
        "env": settings.env,
        "liveness_only": True,
        "daily_configured": settings.daily_update_enabled,
    }


@app.get("/api/runtime/status")
def dependencies(db: Session = Depends(get_db)):
    return {**health(), **runtime_status(db.get_bind(), settings)}


@app.get("/api/scheduler/status", response_model=SchedulerStatus)
def scheduler_status(db: Session = Depends(get_db)) -> SchedulerStatus:
    digests = history(db, 1)
    digest = digests[0] if digests else None
    day = news_window(settings).target_date
    state = run_status(db, day)
    return SchedulerStatus(
        enabled=settings.daily_update_enabled,
        task_name="newsman-daily-ai-brief",
        timezone=settings.daily_timezone,
        daily_time=f"{settings.daily_update_hour:02d}:{settings.daily_update_minute:02d}",
        daily_update_hour=settings.daily_update_hour,
        daily_update_minute=settings.daily_update_minute,
        latest_digest_date=digest["digest_date"] if digest else None,
        latest_digest_created_at=digest["created_at"] if digest else None,
        backend_scheduler="Celery Beat",
        target_date=day,
        run_status=state["status"],
        pipeline_version=state["pipeline_version"],
    )


@app.post("/api/sources/seed")
def seed_sources(db: Session = Depends(get_db)) -> dict:
    result = sync_default_sources(db)
    return {"inserted": result.inserted, "updated": result.updated}


@app.get("/api/sources", response_model=list[SourceRead])
def list_sources(db: Session = Depends(get_db)) -> list[Source]:
    return db.query(Source).order_by(Source.name).all()


@app.get("/api/sources/health", response_model=list[SourceHealthRead])
def list_source_health(db: Session = Depends(get_db)) -> list[dict]:
    return build_source_health(db)


@app.post("/api/sources", response_model=SourceRead)
def create_source(payload: SourceCreate, db: Session = Depends(get_db)) -> Source:
    if payload.enabled:
        raise HTTPException(
            status_code=422, detail="Create disabled, check the source URL, then enable it."
        )
    source = Source(**payload.model_dump(mode="json"))
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@app.post("/api/sources/{source_id}/probe")
async def check_source(source_id: UUID, db: Session = Depends(get_db)) -> dict:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    state = await probe_source(db, source)
    return {
        "status": state.status,
        "activation_allowed": activation_allowed(source, state),
        "last_attempt_at": state.attempted_at,
        "next_retry_at": state.next_retry_at,
    }


@app.patch("/api/sources/{source_id}", response_model=SourceRead)
def set_source_enabled(
    source_id: UUID, payload: SourceEnabledUpdate, db: Session = Depends(get_db)
) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    if payload.enabled and not activation_allowed(source, db.get(SourceHealthState, source_id)):
        raise HTTPException(
            status_code=409,
            detail="Current feed URL and usable body must be checked before enabling.",
        )
    source.enabled = payload.enabled
    db.commit()
    db.refresh(source)
    return source


@app.get("/api/articles", response_model=list[ArticleRead])
def list_articles(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[Article]:
    return (
        db.query(Article)
        .order_by(Article.importance_score.desc(), Article.published_at.desc().nullslast())
        .limit(limit)
        .all()
    )


@app.get("/api/digests/latest", response_model=DigestRead)
def latest_digest(db: Session = Depends(get_db)) -> dict:
    digests = history(db, 1)
    if not digests:
        raise HTTPException(status_code=404, detail="No digest has been generated yet.")
    return digests[0]


@app.get("/api/digests", response_model=list[DigestRead])
def digest_history(limit: int = Query(100, ge=1, le=365), db: Session = Depends(get_db)):
    return history(db, limit)


@app.get("/api/digests/{day}/versions")
def digest_versions(day: date, db: Session = Depends(get_db)):
    from newsman.models import DigestRevision

    versions = (
        db.query(DigestRevision).filter_by(digest_date=day).order_by(DigestRevision.revision).all()
    )
    legacy = read_digest(db, day, 0)
    return [serialize_digest(d) for d in ([legacy] if legacy else []) + versions]


@app.get("/api/digests/{day}/compare")
def compare_digests(
    day: date, left: int = Query(ge=0), right: int = Query(ge=0), db: Session = Depends(get_db)
):
    try:
        return compare(db, day, left, right)
    except RunConflict as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/digests/{day}", response_model=DigestRead)
def dated_digest(
    day: date, revision: int | None = Query(None, ge=0), db: Session = Depends(get_db)
):
    result = read_digest(db, day, revision)
    if result is None:
        raise HTTPException(404, "Unknown date/revision")
    return serialize_digest(result)


@app.post("/api/digests/{day}/rollback", response_model=DigestRead)
def restore_digest(day: date, payload: DigestRollback, db: Session = Depends(get_db)):
    try:
        return rollback_head(db.get_bind(), day, payload.revision, payload.expected_revision)
    except (RunConflict, RunBusy) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/runs/{day}")
def daily_run_status(day: date, db: Session = Depends(get_db)):
    return run_status(db, day)


@app.post("/api/feedback")
def create_feedback(payload: FeedbackCreate, db: Session = Depends(get_db)) -> dict:
    article = db.get(Article, payload.article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found.")
    from sqlalchemy import text

    db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": payload.article_id.int % (2**63)}
    )
    existing = (
        db.query(Feedback)
        .filter_by(article_id=payload.article_id, vote=payload.vote, note=payload.note)
        .first()
    )
    if existing is None:
        db.add(Feedback(**payload.model_dump()))
    set_preference(
        db,
        payload.article_id,
        {"follow": "saved", "noisy": "irrelevant", "useful": "useful"}[payload.vote.value],
    )
    return {"status": "recorded"}


@app.get("/api/preferences")
def list_preferences(db: Session = Depends(get_db)):
    return preferences(db)


@app.put("/api/preferences/{article_id}")
def put_preference(
    article_id: UUID,
    vote: Literal["saved", "irrelevant", "duplicate"],
    db: Session = Depends(get_db),
):
    if db.get(Article, article_id) is None:
        raise HTTPException(404, "Article not found")
    return set_preference(db, article_id, vote)


@app.delete("/api/preferences")
def reset_preferences(db: Session = Depends(get_db)):
    return clear_preferences(db)


@app.delete("/api/preferences/{article_id}")
def reset_preference(article_id: UUID, db: Session = Depends(get_db)):
    return clear_preferences(db, article_id)


@app.post("/api/runs/daily", response_model=DailyRunResponse)
def start_daily_run(
    sync: bool = Query(default=False),
    target_date: date | None = Query(default=None),
    cutoff: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    mode: Literal["ensure", "retry", "revise"] = "ensure",
    request_key: str | None = None,
) -> DailyRunResponse:
    try:
        window = news_window(settings, target_date, cutoff)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if sync:
        options = dict(target_date=target_date, cutoff=cutoff)
        if mode != "ensure" or request_key:
            options.update(mode=mode, request_key=request_key)
        try:
            state = run_daily_graph(db, settings, **options)
        except RunConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        digest_id = UUID(state["digest_id"]) if state.get("digest_id") else None
        return DailyRunResponse(
            mode="sync",
            digest_id=digest_id,
            collected=state.get("collected"),
            summarized=state.get("summarized"),
            window=state.get("window"),
            **{
                key: state[key]
                for key in (
                    "status",
                    "run_id",
                    "attempt",
                    "revision",
                    "stage",
                    "diagnostics",
                    "retry_at",
                    "retry_deferred",
                    "error_type",
                )
                if key in state
            },
        )

    if mode != "ensure" and not request_key:
        raise HTTPException(422, "Retry/revise requires a stable request_key")
    try:
        task = run_daily_digest.delay(
            target_date=window.target_date.isoformat(),
            cutoff=cutoff.isoformat() if cutoff else None,
            mode=mode,
            request_key=request_key,
        )
    except Exception as exc:
        raise HTTPException(
            503,
            "Queue unavailable; no delivery was confirmed. Check runtime status before retrying.",
        ) from exc
    return DailyRunResponse(mode="async", task_id=task.id)

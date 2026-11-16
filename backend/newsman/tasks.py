from newsman.agents.graph import run_daily_graph
from newsman.celery_app import celery_app
from newsman.config import get_settings
from newsman.db import SessionLocal, init_db


@celery_app.task(
    name="newsman.tasks.run_daily_digest",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def run_daily_digest(
    self,
    target_date: str | None = None,
    cutoff: str | None = None,
    mode: str = "ensure",
    request_key: str | None = None,
) -> dict:
    init_db()
    settings = get_settings()
    db = SessionLocal()
    try:
        options = dict(target_date=target_date, cutoff=cutoff)
        if mode != "ensure" or request_key:
            options.update(mode=mode, request_key=request_key)
        result = dict(run_daily_graph(db, settings, **options))
    finally:
        db.close()
    if result.get("status") in {"busy", "failed", "partial", "pending_summary"}:
        if self.request.retries < self.max_retries:
            options["target_date"] = result.get("window", {}).get("target_date", target_date)
            if result["status"] in {"partial", "pending_summary"} and not result.get(
                "retry_deferred"
            ):
                options.update(
                    mode="retry", request_key=f"{self.request.id}:repair:{self.request.retries + 1}"
                )
            raise self.retry(kwargs=options, countdown=900)
    return result

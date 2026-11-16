from __future__ import annotations

from datetime import datetime, timedelta, timezone

from newsman.models import DailyRun, DigestRevision
from newsman.services.revisions import read_digest
from newsman.services.run_lock import RunBusy, RunConflict, locked_session
from newsman.services.selection import news_window

PIPELINE_VERSION = "daily-v3"
TERMINAL = {"succeeded", "partial", "pending_summary"}
GOOD_SOURCES = {"healthy", "stale", "quiet"}


def run_result(run):
    values = {}
    for stage in run.checkpoints.values():
        values.update(stage)
    return {
        **values,
        "run_id": str(run.id),
        "attempt": run.attempt,
        "pipeline_version": run.pipeline_version,
        "status": run.status,
        "stage": run.stage,
        "window": run.window,
        "diagnostics": run.diagnostics,
        "retry_at": run.retry_at.astimezone(timezone.utc).isoformat() if run.retry_at else None,
        "error_type": run.error_type,
    }


def completion_status(run):
    issues = run.diagnostics
    if issues.get("cutoff_not_reached"):
        return "partial"
    sources = issues.get("sources", {})
    if not sources or any(s["status"] not in GOOD_SOURCES for s in sources.values()):
        return "partial"
    if issues.get("pending_summary", 0):
        return "pending_summary"
    if issues.get("pending_insights", 0):
        return "partial"
    return "succeeded"


def execute_daily(
    engine,
    settings,
    *,
    target_date=None,
    cutoff=None,
    now=None,
    output_dir=None,
    mode="ensure",
    request_key=None,
):
    if mode not in {"ensure", "retry", "revise"}:
        raise RunConflict("Unknown run mode")
    if mode != "ensure" and not request_key:
        raise RunConflict("Retry/revise requires a stable request_key")
    if request_key and (len(str(request_key)) > 80 or not str(request_key).isascii()):
        raise RunConflict("Invalid request_key")
    requested = news_window(settings, target_date, cutoff, now)
    clock = now or datetime.now(timezone.utc)
    try:
        with locked_session(engine) as db:
            run = (
                db.query(DailyRun)
                .filter_by(target_date=requested.target_date, pipeline_version=PIPELINE_VERSION)
                .one_or_none()
            )
            if run is None:
                # Pin existing published/legacy boundary without migrating old records.
                previous = read_digest(db, requested.target_date)
                if previous and cutoff is None:
                    for section in previous.sections:
                        if section.get("key") == "run-context" and section.get("window"):
                            saved = section["window"]
                            pinned = settings.model_copy(
                                update={"daily_timezone": saved["timezone"]}
                            )
                            requested = news_window(
                                pinned, requested.target_date, saved["cutoff"], clock
                            )
                run = DailyRun(
                    target_date=requested.target_date,
                    pipeline_version=PIPELINE_VERSION,
                    attempt=1,
                    status="running",
                    stage="ensure_sources",
                    window=requested.as_dict(),
                    checkpoints={},
                    commands={},
                    diagnostics={},
                )
                db.add(run)
                db.flush()
            changed_cutoff = (
                cutoff is not None
                and datetime.fromisoformat(run.window["cutoff"]) != requested.cutoff
            )
            signature = {
                "mode": mode,
                "cutoff": requested.cutoff.astimezone(timezone.utc).isoformat() if cutoff else None,
            }
            key = str(request_key) if request_key else None
            if key and key in run.commands:
                command = run.commands[key]
                if command["signature"] != signature:
                    raise RunConflict("Request key payload conflict")
                if command.get("result"):
                    return command["result"]
            if changed_cutoff and (mode != "revise" or run.status not in TERMINAL):
                raise RunConflict(
                    "A changed cutoff requires a new explicit revision after completion"
                )
            if run.status in TERMINAL:
                if mode == "ensure" or (mode == "retry" and run.status == "succeeded"):
                    return run_result(run)
                if run.retry_at and run.retry_at > clock:
                    return {**run_result(run), "retry_deferred": True}
                run.attempt += 1
                # Retry only failed sources; revise deliberately refreshes all sources.
                sources = run.diagnostics.get("sources", {})
                good = {k: v for k, v in sources.items() if v["status"] in GOOD_SOURCES}
                if run.diagnostics.get("cutoff_not_reached"):
                    good = {}  # A pre-cutoff observation did not cover the full publication window.
                proposals = run.diagnostics.get("insight_proposals", {}) if mode == "retry" else {}
                run.diagnostics = {
                    "sources": good if mode == "retry" else {},
                    "insight_proposals": proposals,
                }
                run.checkpoints = {}
                run.status, run.stage = "running", "ensure_sources"
                run.retry_at = None
                if changed_cutoff:
                    run.window = requested.as_dict()
            if key and key not in run.commands:
                if run.commands and run.status in {"running", "failed"}:
                    active = [
                        v
                        for v in run.commands.values()
                        if v["attempt"] == run.attempt and not v.get("result")
                    ]
                    if active:
                        raise RunConflict("Resume the current command before starting another")
                run.commands = {
                    **run.commands,
                    key: {"signature": signature, "attempt": run.attempt},
                }
            if run.status == "failed" and run.retry_at and run.retry_at > clock:
                return {**run_result(run), "retry_deferred": True}
            run.status, run.error_type = "running", None
            run.diagnostics = {
                **run.diagnostics,
                "cutoff_not_reached": run.diagnostics.get(
                    "cutoff_not_reached", clock < datetime.fromisoformat(run.window["cutoff"])
                ),
            }
            run.updated_at = clock
            db.commit()
            try:
                from newsman.agents.graph import build_newsman_graph

                build_newsman_graph(
                    db,
                    settings,
                    target_date=run.target_date,
                    now=now,
                    output_dir=output_dir,
                    run=run,
                ).invoke({})
                run.status = completion_status(run)
                run.stage = "complete"
                run.updated_at = datetime.now(timezone.utc)
                run.retry_at = None if run.status == "succeeded" else clock + timedelta(minutes=15)
                if run.diagnostics.get("cutoff_not_reached"):
                    run.retry_at = max(run.retry_at, datetime.fromisoformat(run.window["cutoff"]))
                result = run_result(run)
                run.commands = {
                    k: {**v, "result": result} if v["attempt"] == run.attempt else v
                    for k, v in run.commands.items()
                }
                db.commit()
                return result
            except Exception as exc:
                db.rollback()
                run.status = "failed"
                run.error_type = type(exc).__name__[:80]
                run.retry_at = clock + timedelta(minutes=1)
                run.updated_at = datetime.now(timezone.utc)
                db.commit()
                return run_result(run)
    except RunBusy:
        return {
            "status": "busy",
            "pipeline_version": PIPELINE_VERSION,
            "window": requested.as_dict(),
        }


def run_status(db, day):
    run = (
        db.query(DailyRun)
        .filter_by(target_date=day, pipeline_version=PIPELINE_VERSION)
        .one_or_none()
    )
    result = (
        run_result(run) if run else {"status": "not_started", "pipeline_version": PIPELINE_VERSION}
    )
    revision = read_digest(db, day)
    result["active_revision"] = revision.revision if isinstance(revision, DigestRevision) else 0
    return result

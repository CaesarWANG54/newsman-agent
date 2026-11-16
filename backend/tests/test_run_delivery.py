from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry

from newsman import tasks, main
from newsman.services.run_lock import RunConflict


@pytest.mark.parametrize("status", ["partial", "pending_summary", "failed", "busy"])
def test_celery_bounded_repair_without_broker(settings, monkeypatch, status):
    db = SimpleNamespace(close=Mock())
    monkeypatch.setattr(tasks, "init_db", lambda: None)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(
        tasks,
        "run_daily_graph",
        Mock(
            return_value={
                "status": status,
                "window": {"target_date": "2026-10-25"},
            }
        ),
    )
    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(tasks.run_daily_digest, "retry", retry)
    tasks.run_daily_digest.push_request(id="offline-delivery", retries=0)
    try:
        with pytest.raises(Retry):
            tasks.run_daily_digest.run()
        options = retry.call_args.kwargs
        assert options["countdown"] == 900
        assert options["kwargs"]["target_date"] == "2026-10-25"
        if status in {"partial", "pending_summary"}:
            assert options["kwargs"]["mode"] == "retry"
            assert options["kwargs"]["request_key"] == "offline-delivery:repair:1"
        db.close.assert_called_once()
    finally:
        tasks.run_daily_digest.pop_request()
    assert tasks.run_daily_digest.acks_late
    assert tasks.run_daily_digest.reject_on_worker_lost


def test_celery_does_not_schedule_unbounded_repair(settings, monkeypatch):
    monkeypatch.setattr(tasks, "init_db", lambda: None)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "run_daily_graph", lambda *a, **k: {"status": "pending_summary"})
    retry = Mock()
    monkeypatch.setattr(tasks.run_daily_digest, "retry", retry)
    tasks.run_daily_digest.push_request(id="offline", retries=3)
    try:
        assert tasks.run_daily_digest.run()["status"] == "pending_summary"
        retry.assert_not_called()
    finally:
        tasks.run_daily_digest.pop_request()


def test_api_conflict_is_reported_without_raw_error(settings, monkeypatch):
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(
        main, "run_daily_graph", Mock(side_effect=RunConflict("Existing run cutoff is immutable"))
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        main.start_daily_run(
            sync=True,
            target_date=None,
            cutoff=None,
            db="fake",
            mode="revise",
            request_key="safe-key",
        )
    assert exc.value.status_code == 409

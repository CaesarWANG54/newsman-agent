from types import SimpleNamespace
import os
from unittest.mock import MagicMock, Mock, patch

import pytest

from newsman.services.runtime import runtime_status


@pytest.fixture(autouse=True)
def isolate_managed_components(monkeypatch):
    if os.name == "posix":
        from newsman.cli import manage_runtime

        monkeypatch.setattr(manage_runtime, "component_status", lambda role: {
            "component": role, "state": "unregistered", "owned_running": False,
        })


@pytest.mark.parametrize(
    "registered,queue,expected",
    [
        ({"other": ["other.task"]}, "celery", "not_observed"),
        ({"other": ["notnewsman.tasks.run_daily_digestFake"]}, "celery", "not_observed"),
        ({"other": ["newsman.tasks.run_daily_digest"]}, "celery", "ready"),
        ({"other": ["newsman.tasks.run_daily_digest"]}, "foreign_queue", "not_observed"),
    ],
)
def test_only_registered_newsman_workers_count(settings, registered, queue, expected):
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="sqlite"),
        connect=Mock(side_effect=RuntimeError("private secret")),
    )
    client = MagicMock()
    connection = MagicMock()
    inspector = SimpleNamespace(
        ping=lambda: {"other": {"ok": "pong"}},
        registered=lambda: registered,
        active_queues=lambda: {"other": [{"name": queue}]},
    )
    from newsman.celery_app import celery_app

    with (
        patch("redis.Redis.from_url", return_value=client),
        patch.object(celery_app, "connection_for_write", return_value=connection),
        patch.object(celery_app.control, "inspect", return_value=inspector),
    ):
        result = runtime_status(engine, settings)
    assert result["worker"] == expected
    assert result["database"] == "unavailable"
    assert result["scheduler"] == "not_observed"
    assert "private secret" not in str(result)


def test_reader_probe_never_connects_to_celery(settings):
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="sqlite"),
        connect=Mock(side_effect=RuntimeError("fixture offline")),
    )
    from newsman.celery_app import celery_app

    with (
        patch("redis.Redis.from_url", return_value=MagicMock()),
        patch.object(celery_app, "connection_for_write", side_effect=AssertionError("No Celery probe")) as broker,
    ):
        result = runtime_status(engine, settings, probe_worker=False)
    assert result["redis"] == "ready"
    assert result["worker"] == "not_probed"
    assert "worker_pids" not in result
    broker.assert_not_called()

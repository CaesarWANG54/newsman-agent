from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from newsman import main, tasks
from newsman.schemas import DigestRead, DigestItemRead
from newsman.services.selection import news_window


def test_api_forwards_target_and_explicit_cutoff_without_running_graph(settings, monkeypatch):
    monkeypatch.setattr(main, "settings", settings)
    day = date(2026, 10, 25)
    cutoff = datetime.fromisoformat("2026-10-25T09:00:00+08:00")
    state = dict(digest_id=str(uuid4()), window=news_window(settings, day, cutoff).as_dict())
    fake_graph = Mock(return_value=state)
    monkeypatch.setattr(main, "run_daily_graph", fake_graph)
    response = main.start_daily_run(sync=True, target_date=day, cutoff=cutoff, db="fake-db")
    fake_graph.assert_called_once_with("fake-db", settings, target_date=day, cutoff=cutoff)
    assert response.window["cutoff"] == cutoff.isoformat()


def test_api_rejects_naive_cutoff_before_queueing(settings, monkeypatch):
    monkeypatch.setattr(main, "settings", settings)
    delayed = Mock()
    monkeypatch.setattr(main.run_daily_digest, "delay", delayed)
    with pytest.raises(HTTPException) as exc:
        main.start_daily_run(
            sync=False,
            target_date=date(2026, 10, 25),
            cutoff=datetime(2026, 10, 25, 8),
            db="fake-db",
        )
    assert exc.value.status_code == 422
    delayed.assert_not_called()


def test_task_entry_forwards_optional_window_without_real_database(settings, monkeypatch):
    db = SimpleNamespace(close=Mock())
    fake_graph = Mock(return_value={"digest_id": "offline"})
    monkeypatch.setattr(tasks, "init_db", lambda: None)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "run_daily_graph", fake_graph)
    result = tasks.run_daily_digest.run(target_date="2026-10-25", cutoff=None)
    fake_graph.assert_called_once_with(db, settings, target_date="2026-10-25", cutoff=None)
    assert result == {"digest_id": "offline"}
    db.close.assert_called_once()


def test_digest_schema_preserves_context_and_status_without_schema_migration():
    digest = SimpleNamespace(
        id=uuid4(),
        digest_date=date(2026, 10, 25),
        title="Fixture",
        brief="",
        markdown="",
        created_at=datetime.fromisoformat("2026-10-25T08:00:00+08:00"),
        sections=[
            dict(
                key="general",
                title="News",
                items=[
                    dict(
                        id="fixture",
                        title="Title",
                        url="https://example.com/a",
                        score=0.5,
                        summary="已开源。",
                        source="Example",
                        summary_mode="source_excerpt",
                        quality_status="degraded",
                        translation_status="pending_en",
                        source_excerpt="已开源。",
                        excerpt_language="zh",
                    )
                ],
            ),
            dict(key="run-context", title="Window", items=[], window={"cutoff": "fixed"}),
        ],
    )
    payload = DigestRead.model_validate(digest).model_dump(mode="json")
    item = payload["sections"][0]["items"][0]
    assert item["summary_mode"] == "source_excerpt"
    assert item["source_excerpt"] == "已开源。"
    assert item["verification_status"] == "not_verified"
    assert payload["sections"][1]["window"]["cutoff"] == "fixed"


def test_legacy_nullable_language_fields_remain_readable():
    item = DigestItemRead.model_validate(
        dict(
            id="legacy",
            title="Legacy",
            url="https://example.com/legacy",
            score=0.4,
            summary_en=None,
            summary_zh=None,
            why_it_matters_en=None,
            why_it_matters_zh=None,
        )
    )
    assert item.summary_en == item.summary_zh == ""
    assert item.summary_mode == "legacy_unknown"

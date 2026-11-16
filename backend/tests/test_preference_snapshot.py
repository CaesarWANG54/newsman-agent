from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from newsman.agents import graph
from newsman.services import preferences
from newsman.services.selection import NewsSelection, news_window
from test_selection import FakeDB, article, DAY, NOW


def test_private_preferences_are_not_embedded_in_digest_exports():
    from newsman.services.revisions import create_revision

    query = SimpleNamespace(one_or_none=lambda: None, scalar=lambda: 0)
    query.filter_by = lambda **kw: query
    db = SimpleNamespace(query=lambda *a: query, add=lambda x: None, flush=lambda: None)
    run = SimpleNamespace(
        id=uuid4(),
        target_date=DAY,
        attempt=1,
        pipeline_version="daily-v3",
        diagnostics={
            "preference_snapshot": {"private-reader-choice": "saved"},
            "selection_version": "feedback-diversity-v1",
        },
    )
    content = {
        "title": "Fixture",
        "brief": "",
        "markdown": "",
        "sections": [{"key": "run-context", "title": "Window", "items": []}],
    }
    revision = create_revision(db, run, content, "succeeded")
    assert "preference_snapshot" not in revision.sections[0]["diagnostics"]
    assert revision.sections[0]["diagnostics"]["selection_version"] == "feedback-diversity-v1"


@pytest.mark.parametrize("legacy_summary_checkpoint", [False, True])
def test_preferences_are_frozen_across_summary_and_digest(
    settings, monkeypatch, tmp_path, model_client, legacy_summary_checkpoint
):
    db = FakeDB()
    run = SimpleNamespace(
        target_date=DAY,
        window=news_window(settings, DAY, now=NOW).as_dict(),
        diagnostics={},
        checkpoints={"summarize": {"summarized": 1}} if legacy_summary_checkpoint else {},
        stage="",
        updated_at=None,
    )
    item = article()
    revision = SimpleNamespace(id=uuid4(), revision=1)
    snapshots = []
    current_votes = {str(item.id): "saved"}
    provider = Mock(side_effect=lambda db: dict(current_votes))
    monkeypatch.setattr(preferences, "preferences", provider)

    def selection(db, window, limit, votes):
        snapshots.append(deepcopy(votes))
        return NewsSelection([item], {}, [])

    monkeypatch.setattr(graph, "select_news", selection)

    def summaries(settings, items):
        current_votes[str(item.id)] = "duplicate"
        item.metadata_json = {"ai_summary": {"quality_status": "ready"}}
        return 1

    monkeypatch.setattr(graph, "summarize_ranked_articles", summaries)
    monkeypatch.setattr(graph, "ensure_default_sources", lambda db: None)
    monkeypatch.setattr(graph, "cluster_recent_articles", lambda db: 0)
    monkeypatch.setattr(graph, "rank_recent_articles", lambda db: 0)

    # Reuse the non-I/O coroutine execution pattern; no event-loop network self-pipe.
    def immediate(coroutine):
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        finally:
            coroutine.close()
        raise AssertionError("Unexpected fixture I/O")

    monkeypatch.setattr(graph.asyncio, "run", immediate)
    monkeypatch.setattr(graph, "digest_content", lambda *a, **kw: {})
    monkeypatch.setattr(graph, "create_revision", lambda *a, **kw: revision)
    monkeypatch.setattr(graph, "export_revision", lambda *a, **kw: None)
    monkeypatch.setattr(graph, "promote", lambda *a, **kw: None)
    db.get = lambda *a: revision
    graph.build_newsman_graph(
        db, settings, target_date=DAY, now=NOW, output_dir=tmp_path, run=run
    ).invoke({})
    assert snapshots == [{str(item.id): "saved"}] * 2
    assert provider.call_count == 1
    model_client.responses.create.assert_not_called()
    model_client.embeddings.create.assert_not_called()

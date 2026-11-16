import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from newsman.agents import graph
from newsman.models import Article, Digest
from newsman.services.collector import CollectionResult
from newsman.services.newsletter import upsert_digest
from newsman.services.selection import article_time_status, news_window, select_news

DAY = date(2026, 10, 25)
NOW = datetime.fromisoformat("2026-10-25T10:00:00+08:00")


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *criteria):
        return self

    def order_by(self, *criteria):
        return self

    def all(self):
        return sorted(self.rows, key=lambda a: -(a.importance_score or 0))

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    """No engine or real tables; production Postgres types are never created."""

    def __init__(self, articles=()):
        self.articles = list(articles)
        self.digests = []

    def query(self, model):
        assert model in (Article, Digest)
        return FakeQuery(self.articles if model is Article else self.digests)

    def scalars(self, statement):
        from newsman.models import ReadingPreference, Feedback

        assert statement.column_descriptions[0]["entity"] in (ReadingPreference, Feedback)
        return SimpleNamespace(all=lambda: [])

    def add(self, digest):
        assert isinstance(digest, Digest)
        digest.id = uuid4()
        self.digests.append(digest)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def article(
    key="fresh",
    published="2026-10-25T07:00:00+08:00",
    fetched="2026-10-25T07:30:00+08:00",
    score=0.4,
):
    return Article(
        id=uuid4(),
        title=key,
        published_at=datetime.fromisoformat(published) if published else None,
        fetched_at=datetime.fromisoformat(fetched) if fetched else None,
        importance_score=score,
        text_content="This source text is an offline fixture about a product.",
        raw_excerpt="",
        canonical_url=f"https://example.com/{key}",
        category="general",
        tags=[],
        metadata_json={},
        summary=None,
        why_it_matters=None,
        embedding=None,
    )


@pytest.mark.parametrize(
    "sample",
    json.loads(
        (Path(__file__).parent / "fixtures" / "news_windows.json").read_text(encoding="utf-8"),
    ),
)
def test_fixed_window_samples_and_dst_elapsed_hours(settings, sample):
    configured = settings.model_copy(update={"daily_timezone": sample["timezone"]})
    window = news_window(configured, sample["date"], now=datetime.fromisoformat(sample["now"]))
    assert window.start.isoformat() == sample["start"]
    assert window.cutoff.isoformat() == sample["cutoff"]
    assert window.cutoff.astimezone(timezone.utc) - window.start.astimezone(
        timezone.utc
    ) == timedelta(hours=24)


@pytest.mark.parametrize(
    "published,fetched,status",
    [
        ("2026-10-24T08:00:00+08:00", "2026-10-24T08:01:00+08:00", "in_window"),
        ("2026-10-25T00:00:00+08:00", "2026-10-25T07:00:00+08:00", "in_window"),
        ("2026-10-25T07:59:59+08:00", "2026-10-25T09:00:00+08:00", "late_arrival"),
        ("2026-10-25T08:00:00+08:00", "2026-10-25T09:00:00+08:00", "after_cutoff"),
        ("2026-09-01T12:00:00+08:00", "2026-10-25T09:00:00+08:00", "old_news"),
        ("2026-10-25T12:00:00+08:00", "2026-10-25T09:00:00+08:00", "future_publication"),
        (None, "2026-10-25T09:00:00+08:00", "publication_unknown"),
        ("2026-10-25T07:00:00", "2026-10-25T09:00:00+08:00", "publication_timezone_unknown"),
        ("2026-10-25T07:00:00+08:00", "2026-10-25T12:00:00+08:00", "not_observed_yet"),
        ("2026-10-25T07:00:00+08:00", None, "fetch_time_unknown"),
    ],
)
def test_publication_and_fetch_are_distinct(settings, published, fetched, status):
    assert (
        article_time_status(
            article(published=published, fetched=fetched), news_window(settings, DAY, now=NOW)
        )
        == status
    )


def test_old_high_score_cannot_displace_current_news_or_fill_a_gap(settings):
    old = article("old", "2026-05-28T07:00:00+08:00", score=1.0)
    missing = article("undated", None, score=0.99)
    current = article()
    window = news_window(settings, DAY, now=NOW)
    selected = select_news(FakeDB([old, missing, current]), window, 1)
    assert selected.articles == [current]
    assert selected.excluded_counts == {"old_news": 1, "publication_unknown": 1}
    assert select_news(FakeDB([old]), window, 15).articles == []


def test_rerun_midnight_and_cutoff_override(settings):
    first = news_window(settings, DAY, now=NOW)
    rerun = news_window(settings, DAY, now=NOW + timedelta(days=2))
    assert (first.start, first.cutoff) == (rerun.start, rerun.cutoff)
    explicit = news_window(settings, DAY, "2026-10-25T09:30:00+08:00", NOW)
    assert explicit.as_dict()["cutoff"].endswith("09:30:00+08:00")
    with pytest.raises(ValueError):
        news_window(settings, DAY, "2026-10-26T08:00:00+08:00", NOW)
    with pytest.raises(ValueError):
        news_window(settings, DAY, "2026-10-25T08:00:00", NOW)


def test_empty_window_is_exported_as_empty_with_no_old_items(settings, tmp_path):
    db = FakeDB()
    digest = upsert_digest(
        db,
        [article(published="2026-05-28T00:00:00+08:00")],
        DAY,
        window=news_window(settings, DAY, now=NOW),
        output_dir=tmp_path,
    )
    assert digest.sections[0]["status"] == "empty"
    assert not digest.sections[0]["items"]
    assert "无符合发布时间窗口" in digest.brief
    assert "fresh" not in digest.markdown
    assert (tmp_path / "2026-10-25-ai-daily.md").read_text(encoding="utf-8") == digest.markdown


def test_existing_window_is_pinned_across_config_changes(settings):
    db = FakeDB()
    saved = news_window(settings, DAY, now=NOW).as_dict()
    db.digests.append(SimpleNamespace(sections=[dict(key="run-context", window=saved)]))
    changed = settings.model_copy(update={"daily_update_hour": 12, "daily_timezone": "UTC"})
    result = graph.resolve_news_window(db, changed, DAY, now=NOW + timedelta(days=1))
    assert result.cutoff.isoformat() == saved["cutoff"]
    assert result.timezone == "Asia/Shanghai"
    override = graph.resolve_news_window(db, settings, DAY, "2026-10-25T09:00:00+08:00", NOW)
    assert override.cutoff.hour == 9


def test_compiled_pipeline_with_fake_collector_reuses_selector_for_both_stages(
    settings,
    tmp_path,
    monkeypatch,
    model_client,
):
    db = FakeDB([article("old", "2026-05-28T00:00:00+08:00", score=1.0)])
    fresh = article()
    late = article("late", fetched="2026-10-25T09:00:00+08:00")
    calls = []

    async def fake_collect(*args):
        db.articles.extend([fresh, late])
        return CollectionResult(2, 0)

    original = graph.select_news

    def selector(*args):
        calls.append(args[1].as_dict())
        return original(*args)

    def run_fake_coroutine(coroutine):
        # This fake has no I/O/awaits. Avoid Windows asyncio's TCP-based self pipe.
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        finally:
            coroutine.close()
        raise AssertionError("Fake collector unexpectedly performed asynchronous I/O")

    monkeypatch.setattr(graph, "collect_enabled_sources", fake_collect)
    monkeypatch.setattr(graph, "ensure_default_sources", lambda db: None)
    monkeypatch.setattr(graph, "cluster_recent_articles", lambda db: 0)
    monkeypatch.setattr(graph, "rank_recent_articles", lambda db: 0)
    monkeypatch.setattr(graph, "select_news", selector)
    monkeypatch.setattr(graph.asyncio, "run", run_fake_coroutine)
    # Invoke only the compiled graph with fully fake stages/DB, never run_daily_graph.
    compiled = graph.build_newsman_graph(
        db, settings, target_date=DAY, now=NOW, output_dir=tmp_path
    )
    result = compiled.invoke({})
    assert result["collected"] == 2 and result["summarized"] == 2
    assert len(calls) == 2 and calls[0] == calls[1]
    digest = db.digests[0]
    items = digest.sections[0]["items"]
    assert [item["title"] for item in items] == ["fresh", "late"]
    assert items[1]["time_status"] == "late_arrival"
    assert items[0]["event_at"] is None
    assert items[0]["summary_mode"] == "source_excerpt"
    assert items[0]["summary_zh"] == ""
    assert "old" not in [item["title"] for section in digest.sections for item in section["items"]]
    model_client.responses.create.assert_not_called()
    model_client.embeddings.create.assert_not_called()


def test_live_observation_is_frozen_after_fake_collection(settings, tmp_path, monkeypatch):
    db = FakeDB()
    initial = news_window(settings, DAY, now=NOW - timedelta(hours=1))
    monkeypatch.setattr(graph, "resolve_news_window", lambda *args: initial)
    monkeypatch.setattr(graph, "datetime", SimpleNamespace(now=lambda tz: NOW))

    async def fake_collect(*args):
        db.articles.append(article(fetched="2026-10-25T09:30:00+08:00"))
        return CollectionResult(1, 0)

    def immediate(coroutine):
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        finally:
            coroutine.close()
        raise AssertionError("Fake collector unexpectedly awaited")

    monkeypatch.setattr(graph, "collect_enabled_sources", fake_collect)
    monkeypatch.setattr(graph.asyncio, "run", immediate)
    monkeypatch.setattr(graph, "ensure_default_sources", lambda db: None)
    monkeypatch.setattr(graph, "cluster_recent_articles", lambda db: 0)
    monkeypatch.setattr(graph, "rank_recent_articles", lambda db: 0)
    result = graph.build_newsman_graph(db, settings, target_date=DAY, output_dir=tmp_path).invoke(
        {}
    )
    assert result["summarized"] == 1
    assert result["window"]["observed_at"] == NOW.isoformat()
    assert result["window"]["cutoff"] == initial.cutoff.isoformat()

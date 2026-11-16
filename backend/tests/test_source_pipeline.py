from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import feedparser
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from newsman import main
from newsman.models import Article, Source, SourceHealthState, SourceType
from newsman.schemas import SourceCreate, SourceEnabledUpdate, SourceHealthRead
from newsman.services import cleaner, collector
from newsman.services.source_health import activation_allowed, build_source_health, record_attempt
from newsman.services.sources import sync_default_sources

NOW = datetime(2026, 10, 25, 2, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures"
COLLECT_ENABLED = collector.collect_enabled_sources


def run(coroutine):
    # All awaited HTTP/browser functions in these tests are immediate fake implementations.
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("Unexpected asynchronous I/O")


def source(**overrides):
    payload = dict(
        id=uuid4(),
        name="Fixture",
        url="https://fixture.example/",
        feed_url="https://fixture.example/feed",
        source_type=SourceType.rss,
        category_hint="general",
        credibility=0.9,
        enabled=True,
    )
    payload.update(overrides)
    return Source(**payload)


class Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, expression):
        column = expression.left.name
        value = getattr(expression.right, "value", str(expression.right) == "true")
        self.rows = [row for row in self.rows if getattr(row, column) == value]
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return self.rows

    def one_or_none(self):
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def first(self):
        return self.rows[0] if self.rows else None

    def count(self):
        return len(self.rows)


class FakeDB:
    def __init__(self, sources=()):
        self.sources, self.articles, self.states = list(sources), [], {}

    def get(self, model, key):
        if model is SourceHealthState:
            return self.states.get(key)
        assert model is Source
        return next((s for s in self.sources if s.id == key), None)

    def query(self, model):
        return Query(self.sources if model is Source else self.articles)

    def add(self, item):
        if isinstance(item, SourceHealthState):
            self.states[item.source_id] = item
        elif isinstance(item, Source):
            self.sources.append(item)
        else:
            assert isinstance(item, Article)
            item.id = item.id or uuid4()
            self.articles.append(item)

    def commit(self):
        pass

    def refresh(self, item):
        pass


@pytest.fixture
def feed_and_body(monkeypatch):
    feed = feedparser.parse((FIXTURES / "source_valid.xml").read_bytes())
    feed["http_status"] = 200
    calls = []

    async def fake_feed(url):
        calls.append(url)
        return feed

    async def fake_body(url, **kwargs):
        return cleaner.ExtractionResult(
            "Fixture article prose. " * 20, "available", http_status=200
        )

    monkeypatch.setattr(collector, "parse_feed", fake_feed)
    monkeypatch.setattr(collector, "extract_article", fake_body)
    return feed, calls


@pytest.mark.parametrize("code,delay", [(403, 3600), (404, 3600), (429, 3600), (429, 172800)])
def test_http_failure_is_visible_and_backs_off(monkeypatch, code, delay):
    src, db = source(), FakeDB()
    request = httpx.Request("GET", src.feed_url)

    async def failure(url):
        response = httpx.Response(code, request=request, headers={"Retry-After": str(delay)})
        raise httpx.HTTPStatusError("fixture", request=request, response=response)

    monkeypatch.setattr(collector, "parse_feed", failure)
    state = run(collector.probe_source(db, src, now=NOW))
    assert state.status == "http_error" and state.http_status == code
    assert state.last_success_at is None
    assert state.next_retry_at >= NOW + timedelta(seconds=delay)
    assert not activation_allowed(src, state, NOW)
    assert run(collector.probe_source(db, src, now=NOW + timedelta(minutes=1))) is state
    assert state.failure_count == 1


def test_timeout_invalid_feed_and_empty_feed_are_not_healthy(monkeypatch):
    src, db = source(), FakeDB()

    async def timeout(url):
        raise httpx.ReadTimeout("fixture")

    monkeypatch.setattr(collector, "parse_feed", timeout)
    assert run(collector.probe_source(db, src, now=NOW)).status == "timeout"

    async def invalid(url):
        return {"http_status": 200, "entries": [], "version": ""}

    monkeypatch.setattr(collector, "parse_feed", invalid)
    state = run(collector.probe_source(db, src, now=NOW + timedelta(hours=1)))
    assert state.status == "invalid_feed" and state.http_status == 200

    async def empty(url):
        return feedparser.FeedParserDict(version="rss20", bozo=False, entries=[], http_status=200)

    monkeypatch.setattr(collector, "parse_feed", empty)
    assert run(collector.probe_source(db, src, now=NOW + timedelta(hours=2))).status == "empty_feed"


@pytest.mark.parametrize(
    "publication,body_status,expected",
    [
        ("Sun, 25 Oct 2026 00:30:00 GMT", "available", "healthy"),
        ("Sun, 25 Oct 2020 00:30:00 GMT", "available", "stale"),
        (None, "available", "undated"),
        ("Mon, 26 Oct 2026 00:30:00 GMT", "available", "future_date"),
        ("Sun, 25 Oct 2026 00:30:00 GMT", "insufficient", "empty_body"),
        ("Sun, 25 Oct 2026 00:30:00 GMT", "timeout", "empty_body"),
    ],
)
def test_http_200_still_requires_body_and_publication(
    feed_and_body, monkeypatch, publication, body_status, expected
):
    feed, _ = feed_and_body
    feed.entries[0].pop("published", None)
    if publication:
        feed.entries[0]["published"] = publication

    async def body(url, **kwargs):
        return cleaner.ExtractionResult(
            "Body. " * 50 if body_status == "available" else "", body_status, http_status=200
        )

    monkeypatch.setattr(collector, "extract_article", body)
    src, db = source(enabled=False), FakeDB()
    state = run(collector.probe_source(db, src, now=NOW))
    assert state.status == expected and state.http_status == 200
    assert src.enabled is False
    assert state.details["fact_verification_status"] == "not_verified"
    assert state.details["body_results"][body_status] == 1


def test_updated_and_naive_dates_do_not_become_confirmed_publication():
    assert collector.parse_entry_datetime({"updated": "2026-10-25T01:00:00Z"}) is None
    assert collector.parse_entry_datetime({"published": "2026-10-25T01:00:00"}).tzinfo is None


def test_last_success_survives_failure_and_changed_url_needs_new_check(feed_and_body):
    src, db = source(), FakeDB()
    state = run(collector.probe_source(db, src, now=NOW))
    assert activation_allowed(src, state, NOW)
    record_attempt(db, src, "timeout", now=NOW + timedelta(hours=1), details={})
    assert state.last_success_at == NOW and state.last_body_success_at == NOW
    assert not activation_allowed(src, state, NOW + timedelta(hours=1))
    src.feed_url = "https://fixture.example/new-feed"
    assert not activation_allowed(src, state, NOW)


def test_collector_preserves_body_provenance_and_missing_content(feed_and_body, monkeypatch):
    feed, calls = feed_and_body
    feed.entries.append(
        feedparser.FeedParserDict(
            title="Fixture second story",
            link="https://fixture.example/empty",
            published="Sun, 25 Oct 2026 00:30:00 GMT",
            summary="Feed excerpt only.",
        )
    )

    async def body(url, **kwargs):
        return cleaner.ExtractionResult(
            "" if url.endswith("empty") else "Body. " * 60,
            "insufficient" if url.endswith("empty") else "available",
        )

    monkeypatch.setattr(collector, "extract_article", body)
    src = source()
    db = FakeDB([src])
    result = run(collector.collect_from_source(db, src, now=NOW))
    assert result.collected == 2
    assert db.states[src.id].status == "degraded"
    assert db.articles[1].text_content == ""
    assert db.articles[1].raw_excerpt == "Feed excerpt only."
    assert db.articles[1].metadata_json["body_complete"] is False
    assert db.articles[1].metadata_json["verification_status"] == "not_verified"
    assert (
        run(collector.collect_from_source(db, src, now=NOW + timedelta(minutes=1))).collected == 0
    )
    assert len(calls) == 2


def test_dashboard_uses_observation_not_old_article_or_fetch_time(feed_and_body):
    src = source()
    db = FakeDB([src])
    db.articles.append(
        SimpleNamespace(source_id=src.id, title="Undated", published_at=None, fetched_at=NOW)
    )
    assert build_source_health(db, NOW)[0]["health_status"] == "unverified"
    assert build_source_health(db, NOW)[0]["latest_article_at"] is None
    run(collector.probe_source(db, src, now=NOW))
    health = build_source_health(db, NOW)[0]
    assert health["health_status"] == "healthy"
    assert health["last_body_success_at"] == NOW
    assert health["url_verification"] == "feed_and_body_observed"
    assert SourceHealthRead.model_validate(health).fact_verification_status == "not_verified"
    src.url = "https://changed.example/"
    assert build_source_health(db, NOW)[0]["health_status"] == "unverified"


def test_activation_api_requires_real_observation_and_preserves_disable(feed_and_body, monkeypatch):
    src = source(enabled=False)
    db = FakeDB([src])
    monkeypatch.setattr(
        main, "activation_allowed", lambda s, state: activation_allowed(s, state, NOW)
    )
    with pytest.raises(main.HTTPException) as exc:
        main.set_source_enabled(src.id, SourceEnabledUpdate(enabled=True), db)
    assert exc.value.status_code == 409
    run(collector.probe_source(db, src, now=NOW))
    assert main.set_source_enabled(src.id, SourceEnabledUpdate(enabled=True), db).enabled
    assert not main.set_source_enabled(src.id, SourceEnabledUpdate(enabled=False), db).enabled
    with pytest.raises(main.HTTPException):
        main.create_source(
            SourceCreate(name="New", url="https://fixture.example/", enabled=True), db
        )


def test_new_defaults_are_disabled_and_sync_does_not_reenable_user_choice():
    db = FakeDB()
    sync_default_sources(db)
    assert len(db.sources) >= 10 and not any(s.enabled for s in db.sources)
    db.sources[0].enabled = True
    sync_default_sources(db)
    assert db.sources[0].enabled


def test_collection_prioritizes_official_and_keeps_a_per_source_budget(monkeypatch):
    editorial = source(name="Editorial", url="https://editorial.example/", credibility=0.99)
    official = source(name="Official", url="https://openai.com/news/", credibility=0.5)
    calls = []

    async def fake_collect(db, src, limit, *, now=None):
        assert now is None
        calls.append((src.name, limit))
        return collector.CollectionResult(limit, 0)

    monkeypatch.setattr(collector, "collect_from_source", fake_collect)
    result = run(COLLECT_ENABLED(FakeDB([editorial, official]), 10))
    assert calls == [("Official", 5), ("Editorial", 5)]
    assert result.collected == 10


def test_extractor_uses_real_local_html_and_rejects_challenge_page():
    html = (FIXTURES / "source_article.html").read_text(encoding="utf-8")
    good = run(cleaner.extract_article("https://fixture.example/story", html, allow_dynamic=False))
    assert good.status == "available" and "local fixture" in good.text
    assert good.http_status is None
    bad = run(
        cleaner.extract_article(
            "https://fixture.example/denied",
            "<title>Access Denied</title><p>blocked</p>",
            allow_dynamic=False,
        )
    )
    assert bad.status == "blocked_body" and not bad.text
    empty = run(
        cleaner.extract_article(
            "https://fixture.example/empty", "<html></html>", allow_dynamic=False
        )
    )
    assert empty.status == "insufficient"


def test_new_health_table_roundtrips_in_temporary_sqlite_and_compiles_postgres(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'source-state.sqlite').as_posix()}")
    try:
        Source.__table__.create(engine)
        SourceHealthState.__table__.create(engine)
        with Session(engine) as db:
            src = source()
            db.add(src)
            db.commit()
            record_attempt(
                db,
                src,
                "healthy",
                now=NOW,
                http_status=200,
                details={
                    "feed_valid": True,
                    "body_usable_count": 1,
                    "url_verification": "feed_and_body_observed",
                },
            )
            source_id = src.id
        with Session(engine) as db:
            loaded = db.get(SourceHealthState, source_id)
            assert loaded.http_status == 200 and loaded.details["body_usable_count"] == 1
            assert activation_allowed(db.get(Source, source_id), loaded, NOW)
        ddl = str(CreateTable(SourceHealthState.__table__).compile(dialect=postgresql.dialect()))
        assert "JSONB" in ddl and "TIMESTAMP WITH TIME ZONE" in ddl
    finally:
        engine.dispose()

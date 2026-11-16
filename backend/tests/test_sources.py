from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from newsman.models import SourceType
from newsman.services.source_health import classify_source_health, publication_freshness
from newsman.services.sources import _apply_source_payload


def test_apply_source_payload_updates_existing_defaults() -> None:
    source = SimpleNamespace(
        url="https://example.com/old",
        feed_url="https://example.com/old.xml",
        source_type=SourceType.rss,
        category_hint="general",
        credibility=0.5,
        enabled=True,
    )

    changed = _apply_source_payload(
        source,
        {
            "url": "https://example.com/new",
            "feed_url": None,
            "source_type": SourceType.web,
            "category_hint": "models-products",
            "credibility": 0.9,
            "enabled": False,
        },
    )

    assert changed is True
    assert source.url == "https://example.com/new"
    assert source.feed_url is None
    assert source.source_type == SourceType.web
    assert source.enabled is False


def test_classify_source_health_marks_stale_and_disabled_sources() -> None:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)

    assert classify_source_health(False, 10, now, now=now) == "disabled"
    assert classify_source_health(True, 0, None, now=now) == "unverified"
    assert classify_source_health(True, 3, now - timedelta(days=2), now=now) == "unverified"
    assert publication_freshness(now - timedelta(days=2), now) == "fresh"
    assert publication_freshness(now - timedelta(days=8), now) == "stale"
    assert publication_freshness(now - timedelta(days=30), now) == "quiet"

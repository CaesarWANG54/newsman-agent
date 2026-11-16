from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from newsman.models import Article, Source, SourceHealthState
from newsman.services.sources import origin_kind


def utc(value: datetime | None) -> datetime | None:
    # State timestamps are written in UTC; SQLite drops timezone metadata in isolated tests.
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def publication_freshness(value: datetime | None, now: datetime) -> str:
    if value is None or value.tzinfo is None:
        return "undated"
    age = (now - value).total_seconds() / 86400
    if age < 0:
        return "future_date"
    return "fresh" if age <= 3 else "stale" if age <= 14 else "quiet"


def activation_allowed(source: Source, state: SourceHealthState | None, now=None) -> bool:
    now = now or datetime.now(timezone.utc)
    if state is None or state.checked_url != (source.feed_url or source.url):
        return False
    details = state.details or {}
    return bool(
        details.get("configured_url") == source.url
        and details.get("url_verification") == "feed_and_body_observed"
        and state.status in {"healthy", "stale", "degraded"}
        and state.last_body_success_at
        and timedelta(0) <= now - utc(state.last_body_success_at) <= timedelta(days=7)
    )


def record_attempt(
    db: Session,
    source: Source,
    status: str,
    *,
    now: datetime,
    http_status=None,
    latency_ms=None,
    details=None,
    retry_after=0,
) -> SourceHealthState:
    state = db.get(SourceHealthState, source.id)
    if state is None:
        state = SourceHealthState(
            source_id=source.id,
            checked_url=source.feed_url or source.url,
            failure_count=0,
            details={},
        )
        db.add(state)
    details = {**(details or {}), "configured_url": source.url}
    state.checked_url = source.feed_url or source.url
    state.status, state.attempted_at = status, now
    state.http_status, state.latency_ms, state.details = http_status, latency_ms, details
    if details.get("feed_valid"):
        state.last_success_at = now
    if details.get("body_usable_count", 0):
        state.last_body_success_at = now
    failed = status in {
        "http_error",
        "timeout",
        "network_error",
        "invalid_feed",
        "empty_body",
        "empty_feed",
        "unsupported",
        "undated",
        "future_date",
    }
    state.failure_count = (state.failure_count or 0) + 1 if failed else 0
    state.next_retry_at = (
        now
        + timedelta(
            seconds=max(
                min(31536000, retry_after),
                min(21600, 60 * (2 ** min(state.failure_count, 8))),
            )
        )
        if failed
        else None
    )
    db.commit()
    return state


def classify_source_health(enabled, article_count, latest_article_at, now=None, state=None) -> str:
    if not enabled:
        return "disabled"
    if state is None:
        return "unverified"
    now = now or datetime.now(timezone.utc)
    if state.attempted_at is None or now - utc(state.attempted_at) > timedelta(days=3):
        return "observation_stale"
    return state.status


def build_source_health(db: Session, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    sources = db.query(Source).order_by(Source.enabled.desc(), Source.name).all()
    health = []
    for source in sources:
        latest = (
            db.query(Article)
            .filter(Article.source_id == source.id)
            .order_by(
                Article.published_at.desc().nullslast(),
                Article.fetched_at.desc(),
            )
            .first()
        )
        count = db.query(Article).filter(Article.source_id == source.id).count()
        state = db.get(SourceHealthState, source.id)
        details = state.details or {} if state else {}
        current_url = state is not None and state.checked_url == (source.feed_url or source.url)
        current_url = current_url and details.get("configured_url") == source.url
        published = latest.published_at if latest else None
        health.append(
            {
                "id": source.id,
                "name": source.name,
                "url": source.url,
                "feed_url": source.feed_url,
                "enabled": source.enabled,
                "source_type": source.source_type,
                "category_hint": source.category_hint,
                "credibility": source.credibility,
                "article_count": count,
                "latest_article_at": published,
                "latest_article_title": latest.title if latest else None,
                "health_status": classify_source_health(
                    source.enabled, count, published, now, state
                )
                if current_url or not source.enabled
                else "unverified",
                "observed_status": state.status if current_url else "unverified",
                "freshness_status": details.get("freshness_status", "unknown")
                if current_url
                else "unknown",
                "last_attempt_at": state.attempted_at if state else None,
                "last_success_at": state.last_success_at if current_url else None,
                "last_body_success_at": state.last_body_success_at if current_url else None,
                "next_retry_at": state.next_retry_at if current_url else None,
                "failure_count": state.failure_count if current_url else 0,
                "http_status": state.http_status if current_url else None,
                "latency_ms": state.latency_ms if current_url else None,
                "body_status": details.get("body_status", "unknown") if current_url else "unknown",
                "body_results": details.get("body_results", {}) if current_url else {},
                "body_http_statuses": details.get("body_http_statuses", []) if current_url else [],
                "origin_kind": origin_kind(source),
                "url_verification": details.get("url_verification", "not_verified")
                if current_url
                else "not_verified",
                "activation_allowed": activation_allowed(source, state, now),
                "fact_verification_status": "not_verified",
            }
        )
    return health

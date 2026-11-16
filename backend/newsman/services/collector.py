from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import perf_counter

import feedparser
import httpx
from dateutil import parser as date_parser
from sqlalchemy.orm import Session

from newsman.models import Article, ArticleStatus, Source, SourceType, SourceHealthState
from newsman.services.cleaner import canonicalize_url, clean_text, extract_article, ExtractionResult
from newsman.services.source_health import (
    activation_allowed,
    publication_freshness,
    record_attempt,
    utc,
)
from newsman.services.sources import origin_kind


@dataclass(frozen=True)
class CollectionResult:
    collected: int
    skipped: int
    outcomes: dict = field(default_factory=dict)


async def parse_feed(feed_url: str):
    started = perf_counter()
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        response = await client.get(feed_url, headers={"User-Agent": "NewsManAgent/0.1"})
        response.raise_for_status()
    parsed = feedparser.parse(response.content)
    parsed["http_status"] = response.status_code
    parsed["latency_ms"] = (perf_counter() - started) * 1000
    return parsed


def parse_entry_datetime(entry: dict) -> datetime | None:
    # Atom's updated is not a publication timestamp; do not silently substitute it.
    raw = entry.get("published") or entry.get("created")
    if not raw:
        return None
    try:
        return date_parser.parse(raw)
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass
class SourceInspection:
    status: str
    http_status: int | None = None
    latency_ms: float = 0
    retry_after: int = 0
    details: dict = field(default_factory=dict)
    candidates: list[dict] = field(default_factory=list)


def retry_seconds(value: str | None, now: datetime) -> int:
    try:
        return max(0, int(value or "0"))
    except ValueError:
        try:
            return max(0, int((parsedate_to_datetime(value) - now).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return 0


async def inspect_source(
    source: Source, limit=3, *, now=None, allow_dynamic=True
) -> SourceInspection:
    now = now or datetime.now(timezone.utc)
    started = perf_counter()
    base = {
        "origin_kind": origin_kind(source),
        "fact_verification_status": "not_verified",
        "url_verification": "not_verified",
        "feed_valid": False,
    }
    if source.source_type != SourceType.rss:
        return SourceInspection("unsupported", details=base)
    try:
        parsed = await parse_feed(source.feed_url or source.url)
    except httpx.TimeoutException:
        return SourceInspection(
            "timeout", latency_ms=(perf_counter() - started) * 1000, details=base
        )
    except httpx.HTTPStatusError as exc:
        return SourceInspection(
            "http_error",
            http_status=exc.response.status_code,
            latency_ms=(perf_counter() - started) * 1000,
            details=base,
            retry_after=retry_seconds(exc.response.headers.get("Retry-After"), now),
        )
    except httpx.RequestError:
        return SourceInspection(
            "network_error", latency_ms=(perf_counter() - started) * 1000, details=base
        )
    except Exception:
        return SourceInspection(
            "invalid_feed", latency_ms=(perf_counter() - started) * 1000, details=base
        )
    http_status = parsed.get("http_status")
    if (
        not parsed.get("version")
        or parsed.get("bozo")
        or not isinstance(parsed.get("entries"), list)
    ):
        return SourceInspection(
            "invalid_feed", http_status, (perf_counter() - started) * 1000, details=base
        )
    base["feed_valid"] = True
    candidates = []
    for entry in parsed.entries[:limit]:
        link, title = entry.get("link"), clean_text(entry.get("title", ""))
        if not link or not title:
            continue
        try:
            canonical = canonicalize_url(link)
        except (TypeError, ValueError):
            continue
        if not canonical.startswith(("https://", "http://")):
            continue
        try:
            extraction = await extract_article(link, allow_dynamic=allow_dynamic)
        except Exception:
            extraction = ExtractionResult("", "extractor_error")
        published = parse_entry_datetime(entry)
        # A cited original URL is attribution, not proof of independent corroboration.
        original_url = (
            entry.get("source", {}).get("href") if isinstance(entry.get("source"), dict) else None
        )
        candidates.append(
            {
                "entry": entry,
                "url": link,
                "canonical": canonical,
                "title": title,
                "published": published,
                "extraction": extraction,
                "original_url": original_url,
            }
        )
    dates = [c["published"] for c in candidates if c["published"] and c["published"].tzinfo]
    latest = max(dates) if dates else None
    freshness = publication_freshness(latest, now)
    body_counts = Counter(c["extraction"].status for c in candidates)
    usable = body_counts.get("available", 0)
    status = "healthy"
    if not candidates:
        status = "empty_feed"
    elif not usable:
        status = "empty_body"
    elif freshness in {"undated", "future_date"}:
        status = freshness
    elif freshness in {"stale", "quiet"}:
        status = "stale"
    elif usable < len(candidates) or len(dates) < len(candidates):
        status = "degraded"
    base.update(
        {
            "freshness_status": freshness,
            "latest_published_at": latest.isoformat() if latest else None,
            "body_usable_count": usable,
            "sampled_entries": len(candidates),
            "body_status": "available"
            if usable == len(candidates) and candidates
            else "partial"
            if usable
            else "insufficient",
            "body_results": dict(body_counts),
            "undated_count": len(candidates) - len(dates),
            "body_http_statuses": [c["extraction"].http_status for c in candidates],
            "url_verification": "feed_and_body_observed" if usable else "feed_only",
        }
    )
    return SourceInspection(
        status, http_status, (perf_counter() - started) * 1000, details=base, candidates=candidates
    )


async def probe_source(db: Session, source: Source, *, now=None, allow_dynamic=True):
    now = now or datetime.now(timezone.utc)
    previous = db.get(SourceHealthState, source.id)
    if (
        previous
        and previous.checked_url == (source.feed_url or source.url)
        and previous.next_retry_at
    ):
        if utc(previous.next_retry_at) > now:
            return previous
    inspection = await inspect_source(source, now=now, allow_dynamic=allow_dynamic)
    return record_attempt(
        db,
        source,
        inspection.status,
        now=now,
        http_status=inspection.http_status,
        latency_ms=inspection.latency_ms,
        details=inspection.details,
        retry_after=inspection.retry_after,
    )


async def collect_from_source(
    db: Session, source: Source, limit: int = 20, *, now=None
) -> CollectionResult:
    now = now or datetime.now(timezone.utc)
    if not source.enabled:
        return CollectionResult(0, 0)
    previous = db.get(SourceHealthState, source.id)
    if (
        previous
        and previous.checked_url == (source.feed_url or source.url)
        and previous.next_retry_at
    ):
        if utc(previous.next_retry_at) > now:
            return CollectionResult(0, 0)
    inspected = await inspect_source(source, limit, now=now)
    state = record_attempt(
        db,
        source,
        inspected.status,
        now=now,
        http_status=inspected.http_status,
        latency_ms=inspected.latency_ms,
        details=inspected.details,
        retry_after=inspected.retry_after,
    )
    if not activation_allowed(source, state, now):
        return CollectionResult(0, len(inspected.candidates))
    collected, skipped = 0, 0
    for candidate in inspected.candidates:
        canonical = candidate["canonical"]
        existing = db.query(Article).filter(Article.canonical_url == canonical).one_or_none()
        if existing is not None:
            # Retain a same-origin feed mention without counting it as a second source article.
            metadata = existing.metadata_json or {}
            mentions = list(metadata.get("source_mentions") or [])
            mention = {
                "source_id": str(source.id),
                "source_name": source.name,
                "url": canonical,
                "verification_status": "not_verified",
            }
            if mention not in mentions:
                existing.metadata_json = {**metadata, "source_mentions": mentions + [mention]}
            skipped += 1
            continue
        extraction = candidate["extraction"]
        entry = candidate["entry"]
        db.add(
            Article(
                source_id=source.id,
                url=candidate["url"],
                canonical_url=canonical,
                title=candidate["title"],
                author=clean_text(entry.get("author")) or None,
                published_at=candidate["published"],
                fetched_at=now,
                raw_excerpt=clean_text(entry.get("summary") or entry.get("description")),
                text_content=extraction.text,
                category=source.category_hint,
                status=ArticleStatus.cleaned,
                metadata_json={
                    "feed_url": source.feed_url or source.url,
                    "source_name": source.name,
                    "entry_id": entry.get("id"),
                    "origin_kind": origin_kind(source),
                    "original_url": candidate["original_url"],
                    "is_reprint": bool(
                        candidate["original_url"] and candidate["original_url"] != canonical
                    ),
                    "extraction_status": extraction.status,
                    "extraction_method": extraction.method,
                    "body_complete": False if extraction.status != "available" else None,
                    "date_basis": "published"
                    if entry.get("published")
                    else "created"
                    if entry.get("created")
                    else "unknown",
                    "verification_status": "not_verified",
                },
            )
        )
        collected += 1
    db.commit()
    return CollectionResult(collected, skipped)


async def collect_enabled_sources(
    db: Session, max_articles: int, *, progress=None, checkpoint=None, now=None
) -> CollectionResult:
    sources = (
        db.query(Source)
        .filter(Source.enabled.is_(True))
        .order_by(Source.credibility.desc(), Source.name)
        .all()
    )
    sources.sort(
        key=lambda source: (origin_kind(source) != "official", -source.credibility, source.name)
    )
    per_source = max(1, max_articles // max(len(sources), 1))
    outcomes = dict(progress or {})
    total = CollectionResult(
        sum(v.get("collected", 0) for v in outcomes.values()),
        sum(v.get("skipped", 0) for v in outcomes.values()),
    )
    for source in sources:
        source_id = str(source.id)
        if source_id in outcomes:
            continue
        remaining = max_articles - total.collected
        if remaining <= 0:
            break
        try:
            result = await collect_from_source(
                db, source, limit=min(per_source, remaining), now=now
            )
            health = db.get(SourceHealthState, source.id)
            status = health.status if health else "unverified"
            outcome = dict(collected=result.collected, skipped=result.skipped, status=status)
            if health and health.next_retry_at:
                outcome["retry_at"] = utc(health.next_retry_at).isoformat()
        except Exception as exc:
            db.rollback()
            result = CollectionResult(0, 0)
            outcome = dict(collected=0, skipped=0, status="failed", error_type=type(exc).__name__)
        outcomes[source_id] = outcome
        if checkpoint:
            checkpoint(outcomes)
        total = CollectionResult(total.collected + result.collected, total.skipped + result.skipped)
    return CollectionResult(total.collected, total.skipped, outcomes)

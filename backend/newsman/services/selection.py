from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session

from newsman.config import Settings
from newsman.models import Article
from newsman.services.dedupe import EventGroup, group_articles


def aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


@dataclass(frozen=True)
class NewsWindow:
    target_date: date
    timezone: str
    start: datetime
    cutoff: datetime
    observed_at: datetime

    def as_dict(self) -> dict:
        return {
            "target_date": self.target_date.isoformat(),
            "timezone": self.timezone,
            "start": self.start.isoformat(),
            "cutoff": self.cutoff.isoformat(),
            "observed_at": self.observed_at.isoformat(),
            "bounds": "[start, cutoff)",
        }


def news_window(
    settings: Settings,
    target_date: date | str | None = None,
    cutoff: datetime | str | None = None,
    now: datetime | None = None,
) -> NewsWindow:
    zone = ZoneInfo(settings.daily_timezone)
    now = now or datetime.now(timezone.utc)
    if not aware(now):
        raise ValueError("now must have an explicit timezone")
    day = date.fromisoformat(target_date) if isinstance(target_date, str) else target_date
    day = day or now.astimezone(zone).date()
    boundary = datetime.fromisoformat(cutoff) if isinstance(cutoff, str) else cutoff
    boundary = boundary or datetime.combine(
        day,
        time(settings.daily_update_hour, settings.daily_update_minute),
        zone,
    )
    if not aware(boundary) or boundary.astimezone(zone).date() != day:
        raise ValueError("cutoff must be timezone-aware and fall on target_date in daily_timezone")
    boundary = boundary.astimezone(zone)
    # Subtract elapsed hours in UTC so DST days still cover exactly 24 hours.
    start = (boundary.astimezone(timezone.utc) - timedelta(hours=24)).astimezone(zone)
    return NewsWindow(day, settings.daily_timezone, start, boundary, now.astimezone(zone))


def article_time_status(article: Article, window: NewsWindow) -> str:
    published = article.published_at
    fetched = article.fetched_at
    if published is None:
        return "publication_unknown"
    if not aware(published):
        return "publication_timezone_unknown"
    if published > window.observed_at:
        return "future_publication"
    if published < window.start:
        return "old_news"
    if published >= window.cutoff:
        return "after_cutoff"
    if fetched is None or not aware(fetched):
        return "fetch_time_unknown"
    if fetched > window.observed_at:
        return "not_observed_yet"
    return "late_arrival" if fetched >= window.cutoff else "in_window"


@dataclass
class NewsSelection:
    articles: list[Article]
    excluded_counts: dict[str, int]
    events: list[EventGroup]


def select_news(
    db: Session, window: NewsWindow, limit: int, preference_snapshot=None
) -> NewsSelection:
    # Include recently fetched old/undated rows for diagnostics, never for filling slots.
    candidates = (
        db.query(Article)
        .filter(or_(Article.published_at >= window.start, Article.fetched_at >= window.start))
        .order_by(
            Article.importance_score.desc(),
            Article.published_at.desc().nullslast(),
            Article.id,
        )
        .all()
    )
    eligible = []
    excluded: Counter = Counter()
    for article in candidates:
        status = article_time_status(article, window)
        if status in {"in_window", "late_arrival"}:
            eligible.append(article)
        else:
            excluded[status] += 1
    from newsman.services.preferences import preferences
    from newsman.services.ranker import diverse_events

    votes = preferences(db) if preference_snapshot is None else preference_snapshot
    events = diverse_events(group_articles(eligible), limit, votes)
    return NewsSelection([event.primary for event in events], dict(excluded), events)

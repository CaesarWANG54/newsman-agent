from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from newsman.models import Source, SourceType


SOURCE_SYNC_FIELDS = ("url", "feed_url", "source_type", "category_hint", "credibility", "enabled")


@dataclass(frozen=True)
class SourceSyncResult:
    inserted: int
    updated: int


DEFAULT_SOURCES = [
    {
        "name": "OpenAI Blog",
        "url": "https://openai.com/news/",
        "feed_url": "https://openai.com/news/rss.xml",
        "source_type": SourceType.rss,
        "category_hint": "models-products",
        "credibility": 0.98,
    },
    {
        "name": "Google AI Blog",
        "url": "https://blog.google/technology/ai/",
        "feed_url": "https://blog.google/technology/ai/rss/",
        "source_type": SourceType.rss,
        "category_hint": "models-products",
        "credibility": 0.95,
    },
    {
        "name": "Microsoft AI Blog",
        "url": "https://blogs.microsoft.com/ai/",
        "feed_url": "https://blogs.microsoft.com/ai/feed/",
        "source_type": SourceType.rss,
        "category_hint": "models-products",
        "credibility": 0.94,
    },
    {
        "name": "Anthropic News",
        "url": "https://www.anthropic.com/news",
        "feed_url": None,
        "source_type": SourceType.web,
        "category_hint": "models-products",
        "credibility": 0.96,
        "enabled": False,
    },
    {
        "name": "Hugging Face Blog",
        "url": "https://huggingface.co/blog",
        "feed_url": "https://huggingface.co/blog/feed.xml",
        "source_type": SourceType.rss,
        "category_hint": "open-source",
        "credibility": 0.9,
    },
    {
        "name": "NVIDIA Developer Blog",
        "url": "https://developer.nvidia.com/blog/",
        "feed_url": "https://developer.nvidia.com/blog/feed",
        "source_type": SourceType.rss,
        "category_hint": "industry",
        "credibility": 0.9,
    },
    {
        "name": "AWS Machine Learning Blog",
        "url": "https://aws.amazon.com/blogs/machine-learning/",
        "feed_url": "https://aws.amazon.com/blogs/machine-learning/feed/",
        "source_type": SourceType.rss,
        "category_hint": "industry",
        "credibility": 0.88,
    },
    {
        "name": "MIT Technology Review AI",
        "url": "https://www.technologyreview.com/topic/artificial-intelligence/",
        "feed_url": "https://www.technologyreview.com/feed/",
        "source_type": SourceType.rss,
        "category_hint": "industry",
        "credibility": 0.84,
    },
    {
        "name": "VentureBeat AI",
        "url": "https://venturebeat.com/category/ai/",
        "feed_url": "https://venturebeat.com/category/ai/feed/",
        "source_type": SourceType.rss,
        "category_hint": "industry",
        "credibility": 0.72,
    },
    {
        "name": "The Decoder",
        "url": "https://the-decoder.com/",
        "feed_url": "https://the-decoder.com/feed/",
        "source_type": SourceType.rss,
        "category_hint": "industry",
        "credibility": 0.76,
    },
    {
        "name": "arXiv AI",
        "url": "https://arxiv.org/list/cs.AI/recent",
        "feed_url": "https://rss.arxiv.org/rss/cs.AI",
        "source_type": SourceType.rss,
        "category_hint": "research",
        "credibility": 0.88,
    },
    {
        "name": "SEC Press Releases",
        "url": "https://www.sec.gov/news/pressreleases",
        "feed_url": "https://www.sec.gov/news/pressreleases.rss",
        "source_type": SourceType.rss,
        "category_hint": "policy-safety",
        "credibility": 0.9,
    },
    {
        "name": "NIST Artificial Intelligence News",
        "url": "https://www.nist.gov/artificial-intelligence",
        "feed_url": "https://www.nist.gov/news-events/artificial%20intelligence/rss.xml",
        "source_type": SourceType.rss,
        "category_hint": "policy-safety",
        "credibility": 0.92,
    },
]


def origin_kind(source: Source) -> str:
    host = (urlsplit(source.url).hostname or "").lower()
    official = {
        "openai.com",
        "blog.google",
        "blogs.microsoft.com",
        "anthropic.com",
        "developer.nvidia.com",
        "aws.amazon.com",
        "sec.gov",
        "nist.gov",
        "huggingface.co",
    }
    if any(host == domain or host.endswith("." + domain) for domain in official):
        return "official"
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        return "research_repository"
    return "editorial"


def _apply_source_payload(source: Source, payload: dict) -> bool:
    changed = False
    for field in SOURCE_SYNC_FIELDS:
        if field not in payload:
            continue
        if getattr(source, field) != payload[field]:
            setattr(source, field, payload[field])
            changed = True
    return changed


def sync_default_sources(db: Session) -> SourceSyncResult:
    inserted = 0
    updated = 0
    for payload in DEFAULT_SOURCES:
        existing = db.query(Source).filter(Source.name == payload["name"]).one_or_none()
        if existing is None:
            db.add(Source(**{**payload, "enabled": False}))
            inserted += 1
        elif _apply_source_payload(existing, {k: v for k, v in payload.items() if k != "enabled"}):
            updated += 1
    db.commit()
    return SourceSyncResult(inserted=inserted, updated=updated)


def ensure_default_sources(db: Session) -> int:
    return sync_default_sources(db).inserted

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from newsman.models import Article

HIGH_SIGNAL_KEYWORDS = {
    "model": 0.08,
    "agent": 0.08,
    "open source": 0.07,
    "api": 0.07,
    "benchmark": 0.06,
    "safety": 0.06,
    "regulation": 0.05,
    "funding": 0.04,
    "acquisition": 0.05,
    "chips": 0.04,
    "robotics": 0.04,
}

COMPANY_KEYWORDS = {
    "openai": 0.1,
    "anthropic": 0.1,
    "google": 0.08,
    "deepmind": 0.08,
    "meta": 0.07,
    "microsoft": 0.07,
    "nvidia": 0.07,
    "hugging face": 0.06,
    "xai": 0.06,
}


def explain_article(article: Article, now=None) -> dict:
    text = f"{article.title} {article.text_content[:1200]}".lower()
    published = article.published_at
    age = None
    if published and published.tzinfo is not None:
        age = ((now or datetime.now(timezone.utc)) - published).total_seconds() / 3600
    parts = {
        "base": 0.2,
        "source_quality": max(0, min(1, article.source.credibility)) * 0.25
        if article.source
        else 0,
        "signals": min(0.3, sum(w for k, w in HIGH_SIGNAL_KEYWORDS.items() if k in text)),
        "company_context": min(0.03, sum(w for k, w in COMPANY_KEYWORDS.items() if k in text)),
        "recency": 0.12
        if age is not None and 0 <= age < 24
        else 0.06
        if age is not None and 24 <= age < 72
        else 0,
        "body_penalty": -0.08 if len(article.text_content) < 300 else 0,
    }
    uncertainty = ["source_facts_not_verified"]
    if age is None:
        uncertainty.append("publication_unknown")
    if parts["body_penalty"]:
        uncertainty.append("limited_body")
    return {
        "version": "rank-v2",
        "score": max(0.0, min(1.0, round(sum(parts.values()), 3))),
        "components": {k: round(v, 3) for k, v in parts.items()},
        "novelty_basis": "publication_recency_only_not_event_novelty",
        "affected_audience": "researchers"
        if categorize_article(article) == "research"
        else "AI practitioners",
        "uncertainty": uncertainty,
    }


def score_article(article: Article) -> float:
    return explain_article(article)["score"]


def diverse_events(events, limit, votes=None):
    """Preferences affect ordering only; neither source evidence nor stored snapshots change."""
    votes = votes or {}
    remaining = list(events)
    chosen, counts = [], {}

    def origin(event):
        return str(
            event.primary.source_id
            or (event.primary.metadata_json or {}).get("source_name")
            or "unknown"
        )

    def score(event):
        shifts = {"saved": 0.08, "useful": 0.04, "irrelevant": -0.22, "duplicate": -0.35}
        shift = min((shifts.get(votes.get(str(a.id)), 0) for a in event.members), default=0)
        if all(votes.get(str(a.id)) not in {"irrelevant", "duplicate"} for a in event.members):
            shift = max((shifts.get(votes.get(str(a.id)), 0) for a in event.members), default=0)
        return event.primary.importance_score + shift - counts.get(origin(event), 0) * 0.15

    while remaining and len(chosen) < limit:
        # Prefer a different origin after two placements while alternatives remain.
        alternatives = [e for e in remaining if counts.get(origin(e), 0) < 2]
        pool = alternatives or remaining
        best = min(pool, key=lambda e: (-score(e), e.primary.canonical_url))
        remaining.remove(best)
        chosen.append(best)
        counts[origin(best)] = counts.get(origin(best), 0) + 1
    return chosen


def categorize_article(article: Article) -> str:
    text = f"{article.title} {article.text_content[:1000]}".lower()
    if any(token in text for token in ["arxiv", "paper", "research", "benchmark"]):
        return "research"
    if any(token in text for token in ["open source", "github", "hugging face", "weights"]):
        return "open-source"
    if any(token in text for token in ["funding", "revenue", "acquisition", "startup"]):
        return "business"
    if any(token in text for token in ["regulation", "policy", "safety", "copyright"]):
        return "policy-safety"
    if any(token in text for token in ["api", "model", "agent", "assistant", "product"]):
        return "models-products"
    return article.category or "general"


def tag_article(article: Article) -> list[str]:
    text = f"{article.title} {article.text_content[:1000]}".lower()
    tags = []
    for keyword in [*COMPANY_KEYWORDS.keys(), *HIGH_SIGNAL_KEYWORDS.keys()]:
        if keyword in text:
            tags.append(keyword.replace(" ", "-"))
    return sorted(set(tags))[:8]


def rank_recent_articles(db: Session, limit: int = 200) -> int:
    articles = (
        db.query(Article)
        .order_by(Article.published_at.desc().nullslast(), Article.fetched_at.desc())
        .limit(limit)
        .all()
    )

    for article in articles:
        article.category = categorize_article(article)
        article.tags = tag_article(article)
        explanation = explain_article(article)
        article.importance_score = explanation["score"]
        article.metadata_json = {**(article.metadata_json or {}), "ranking": explanation}

    db.commit()
    return len(articles)

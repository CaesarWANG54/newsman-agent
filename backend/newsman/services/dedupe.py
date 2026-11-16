from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from newsman.models import Article
from newsman.services.cleaner import canonicalize_url

VERSION = "event-v2"
NOISE_WORDS = {
    "ai",
    "artificial",
    "intelligence",
    "the",
    "and",
    "for",
    "with",
    "from",
    "new",
    "launches",
    "announces",
    "a",
    "an",
}
ALIASES = {
    "微软": "microsoft",
    "谷歌": "google",
    "英伟达": "nvidia",
    "脸书": "meta",
    "开源": "open source",
    "模型": "model",
    "智能体": "agents",
    "代理": "agents",
    "发布": "releases",
    "推出": "releases",
    "上线": "releases",
    "收购": "acquires",
    "融资": "funding",
    "裁员": "layoffs",
    "新款": "",
    "全新": "",
}
COMPANIES = {"openai", "microsoft", "google", "nvidia", "meta", "anthropic", "amazon"}
ACTIONS = {"releases", "acquires", "funding", "layoffs", "outage", "lawsuit"}
WORD_ALIASES = {
    "launches": "releases",
    "launched": "releases",
    "announces": "releases",
    "announced": "releases",
    "unveils": "releases",
    "released": "releases",
    "launch": "releases",
    "agent": "agents",
    "models": "model",
}


def tokens(title: str, concepts: bool = False) -> list[str]:
    text = unicodedata.normalize("NFKC", title or "").lower()
    if concepts:
        for word, replacement in sorted(ALIASES.items(), key=lambda pair: -len(pair[0])):
            text = text.replace(word, f" {replacement} ")
    words = re.findall(r"[a-z0-9]+(?:[.-][a-z0-9]+)*|[\u4e00-\u9fff]", text)
    if concepts:
        words = [WORD_ALIASES.get(word, word) for word in words]
    return [word for word in words if word not in NOISE_WORDS]


def normalize_title(title: str) -> str:
    return " ".join(tokens(title))


def title_fingerprint(title: str) -> str:
    return hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:16]


def compatible_titles(left: str, right: str) -> bool:
    lt, rt = set(tokens(left, True)), set(tokens(right, True))
    companies_left, companies_right = lt & COMPANIES, rt & COMPANIES
    if companies_left and companies_right and companies_left != companies_right:
        return False

    def model_ids(title):
        return {
            re.sub(r"[ -]", "", m.lower())
            for m in re.findall(
                r"(?:gpt|gemini|claude|llama|qwen|deepseek|sonnet|haiku|opus)[ -]*v?\d+(?:[.-]\d+)*",
                title,
                flags=re.I,
            )
        }

    models_left = model_ids(left)
    models_right = model_ids(right)
    if models_left and models_right and models_left != models_right:
        return False
    variants = {"mini", "pro", "flash", "lite", "nano", "preview"}
    if models_left and models_right and lt & variants != rt & variants:
        return False
    actions_left, actions_right = lt & ACTIONS, rt & ACTIONS
    return not (actions_left and actions_right and actions_left.isdisjoint(actions_right))


def are_similar_titles(left: str, right: str, threshold: float = 0.82) -> bool:
    if (
        not normalize_title(left)
        or not normalize_title(right)
        or not compatible_titles(left, right)
    ):
        return False
    lt, rt = tokens(left, True), tokens(right, True)
    if SequenceMatcher(None, " ".join(lt), " ".join(rt)).ratio() >= threshold:
        return True
    shared = set(lt) & set(rt)
    union = set(lt) | set(rt)
    anchors = shared & COMPANIES or {t for t in shared if re.search(r"[a-z].*\d", t)}
    return bool(anchors and shared & ACTIONS and len(shared) / max(1, len(union)) >= 0.65)


def safe_url(article: Article) -> str:
    try:
        raw = getattr(article, "canonical_url", "") or getattr(article, "url", "")
        parts = urlsplit(raw)
        return (
            canonicalize_url(raw)
            if parts.scheme.lower() in {"https", "http"} and parts.hostname
            else ""
        )
    except (TypeError, ValueError):
        return ""


def _same_period(left: Article, right: Article) -> bool:
    a, b = getattr(left, "published_at", None), getattr(right, "published_at", None)
    if a is None or b is None:
        return False
    try:
        return abs(a - b) <= timedelta(days=2)
    except TypeError:
        return False


def _cosine(left, right) -> float:
    try:
        if not left or not right or len(left) != len(right):
            return 0.0
        if not all(math.isfinite(n) for n in [*left, *right]):
            return 0.0
        norm = math.sqrt(sum(n * n for n in left) * sum(n * n for n in right))
        return sum(a * b for a, b in zip(left, right)) / norm if norm else 0.0
    except (TypeError, ValueError):
        return 0.0


def match_articles(left: Article, right: Article, embedding_provider=None) -> tuple[bool, str]:
    url_left, url_right = safe_url(left), safe_url(right)
    if url_left and url_left == url_right:
        return True, "canonical_url"
    if not _same_period(left, right) or not compatible_titles(left.title, right.title):
        return False, "distinct_event"
    if are_similar_titles(left.title, right.title):
        return True, "lexical_bilingual"
    shared = set(tokens(left.title, True)) & set(tokens(right.title, True))
    if not shared & COMPANIES or not shared & ACTIONS:
        return False, "lexical_only"
    try:
        lvec = embedding_provider(left) if embedding_provider else getattr(left, "embedding", None)
        rvec = (
            embedding_provider(right) if embedding_provider else getattr(right, "embedding", None)
        )
        if _cosine(lvec, rvec) >= 0.94:
            return True, "embedding_assisted"
    except Exception:
        return False, "embedding_unavailable"
    return False, "lexical_only"


@dataclass
class EventGroup:
    primary: Article
    members: list[Article] = field(default_factory=list)
    reasons: set[str] = field(default_factory=set)

    @property
    def key(self) -> str:
        identity = min(safe_url(a) or str(a.id) for a in self.members)
        return f"{VERSION}-{hashlib.sha1(identity.encode()).hexdigest()[:16]}"

    def evidence(self) -> list[dict]:
        evidence = []
        seen = set()
        body_origins = {}
        for article in self.members:
            url = safe_url(article)
            meta = article.metadata_json or {}
            origin = meta.get("original_url") or url
            try:
                if (
                    urlsplit(origin).scheme not in {"http", "https"}
                    or not urlsplit(origin).hostname
                ):
                    origin = url
            except ValueError:
                origin = url
            origin_group = urlsplit(origin).hostname if origin else None
            body = re.sub(r"\s+", " ", getattr(article, "text_content", "") or "").strip()
            same_content = False
            if len(body) >= 200:
                signature = hashlib.sha256(body.encode()).hexdigest()
                same_content = signature in body_origins
                origin_group = body_origins.setdefault(signature, origin_group)
            key = (str(getattr(article, "source_id", None) or meta.get("source_name")), url)
            if key in seen:
                continue
            seen.add(key)
            published = getattr(article, "published_at", None)
            evidence.append(
                {
                    "article_id": str(article.id),
                    "source_id": str(getattr(article, "source_id", "") or ""),
                    "source": meta.get("source_name") or urlsplit(url).hostname or "Unknown",
                    "url": url,
                    "original_url": origin,
                    "title": article.title,
                    "published_at": published.isoformat() if published else None,
                    "is_primary": article is self.primary,
                    "is_reprint": bool(meta.get("is_reprint")),
                    "verification_status": "not_verified",
                    "independence_group": origin_group,
                    "same_content": same_content,
                }
            )
            for mention in meta.get("source_mentions") or []:
                mention_key = (mention.get("source_id"), url)
                if mention_key in seen:
                    continue
                seen.add(mention_key)
                evidence.append(
                    {
                        "article_id": str(article.id),
                        "source_id": mention.get("source_id", ""),
                        "source": mention.get("source_name", "Unknown"),
                        "url": url,
                        "original_url": origin,
                        "title": article.title,
                        "published_at": published.isoformat() if published else None,
                        "is_primary": False,
                        "is_reprint": True,
                        "same_document": True,
                        "verification_status": "not_verified",
                        "independence_group": origin_group,
                    }
                )
        return evidence

    def as_dict(self) -> dict:
        evidence = self.evidence()
        origins = {e["independence_group"] for e in evidence}
        origins.discard(None)
        numeric_variants = {tuple(re.findall(r"\d+(?:\.\d+)?", a.title)) for a in self.members}
        negations = {
            bool(re.search(r"\b(?:not|no|never)\b|否认|不会|不支持", a.title, re.I))
            for a in self.members
        }
        return {
            "cluster_id": self.key,
            "clustering_version": VERSION,
            "cluster_method": ",".join(sorted(self.reasons)) or "single_document",
            "event_sources": evidence,
            "document_count": len({safe_url(a) or str(a.id) for a in self.members}),
            "source_count": len(origins),
            "coverage_status": "multiple_origins" if len(origins) >= 2 else "limited_sources",
            "evidence_review_required": len(numeric_variants) > 1 or len(negations) > 1,
        }


def group_articles(articles: list[Article], embedding_provider=None) -> list[EventGroup]:
    ordered = sorted(
        articles,
        key=lambda a: (
            -(getattr(a, "importance_score", 0) or 0),
            safe_url(a),
            str(a.id),
        ),
    )
    groups: list[EventGroup] = []
    for article in ordered:
        for group in groups:
            # Do not let an intermediate headline bridge two incompatible events.
            same_url = safe_url(article) and safe_url(article) == safe_url(group.primary)
            if not same_url and not all(
                compatible_titles(article.title, member.title) for member in group.members
            ):
                continue
            matched, reason = match_articles(article, group.primary, embedding_provider)
            if matched:
                group.members.append(article)
                group.reasons.add(reason)
                break
        else:
            groups.append(EventGroup(article, [article]))
    return groups


def cluster_recent_articles(db: Session, limit: int = 200) -> int:
    articles = (
        db.query(Article)
        .order_by(
            Article.published_at.desc().nullslast(),
            Article.fetched_at.desc(),
        )
        .limit(limit)
        .all()
    )
    updated = 0
    for group in group_articles(articles):
        for article in group.members:
            if article.cluster_key != group.key:
                article.cluster_key = group.key
                updated += 1
            article.metadata_json = {
                **(article.metadata_json or {}),
                "event_cluster": {
                    "cluster_id": group.key,
                    "clustering_version": VERSION,
                    "cluster_method": ",".join(sorted(group.reasons)) or "single_document",
                    "primary_id": str(group.primary.id),
                },
            }
    db.commit()
    return updated

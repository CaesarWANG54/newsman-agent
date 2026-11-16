from __future__ import annotations

import json
import re
from html import escape
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from newsman.models import Article, Digest
from newsman.config import get_settings
from newsman.services.insights import build_opportunity_sections
from newsman.services.dedupe import EventGroup, group_articles
from newsman.services.selection import NewsWindow, article_time_status, news_window
from newsman.services.summarizer import summary_payload


SECTION_LABELS = {
    "models-products": "Models and Products",
    "research": "Research",
    "open-source": "Open Source",
    "business": "Business and Funding",
    "policy-safety": "Policy and Safety",
    "industry": "Industry Signals",
    "general": "Watchlist",
}


def _article_summary_payload(article: Article) -> dict:
    return summary_payload(
        (article.metadata_json or {}).get("ai_summary"),
        article.summary,
        article.why_it_matters,
    )


def _ensure_bilingual_item(item: dict) -> dict:
    payload = summary_payload(item, item.get("summary"), item.get("why_it_matters"))
    if item.get("source") == "NewsMan Synthesis" and not item.get("insight"):
        payload.update(
            summary_mode="rule_synthesis",
            quality_status="needs_review",
            consistency_status="not_checked",
            verification_status="not_verified",
        )
    return {**item, **payload}


def build_sections(
    articles: list[Article],
    window: NewsWindow | None = None,
    events: list[EventGroup] | None = None,
    opportunity_sections: list[dict] | None = None,
) -> list[dict]:
    events = events if events is not None else group_articles(articles)
    articles = [event.primary for event in events]
    event_by_id = {str(event.primary.id): event for event in events}
    sections_by_key: dict[str, list[dict]] = {}
    for article in articles:
        section_key = article.category or "general"
        sections_by_key.setdefault(section_key, []).append(
            {
                "id": str(article.id),
                "title": article.title,
                "url": article.canonical_url,
                "source": (article.metadata_json or {}).get("source_name"),
                "score": article.importance_score,
                "tags": article.tags,
                "ranking": (article.metadata_json or {}).get("ranking"),
                "published_at": article.published_at.isoformat()
                if getattr(article, "published_at", None)
                else None,
                "fetched_at": article.fetched_at.isoformat()
                if getattr(article, "fetched_at", None)
                else None,
                "event_at": None,
                "time_status": article_time_status(article, window) if window else "legacy_unknown",
                **event_by_id[str(article.id)].as_dict(),
                **_article_summary_payload(article),
            }
        )

    sections = []
    for key, label in SECTION_LABELS.items():
        items = sections_by_key.get(key, [])
        if items:
            sections.append({"key": key, "title": label, "items": items})

    # Preserve every event source for evidence review, not only its main reading card.
    evidence_articles = [member for event in events for member in event.members]
    conflict_ids = {
        str(member.id)
        for event in events
        if event.as_dict()["evidence_review_required"]
        for member in event.members
    }
    sections.extend(
        opportunity_sections
        if opportunity_sections is not None
        else build_opportunity_sections(
            evidence_articles, window, conflict_article_ids=conflict_ids
        )
    )
    for section in sections:
        section["items"] = [_ensure_bilingual_item(item) for item in section["items"]]
    return sections


def _render_evidence(item: dict) -> list[str]:
    evidence = item.get("evidence") or []
    if not evidence:
        return []
    lines = ["- Evidence:"]
    for source in evidence:
        title = source.get("title") or "Untitled"
        url = source.get("url") or ""
        source_name = source.get("source") or "Unknown"
        lines.append(f"  - [{title}]({url}) - {source_name}")
    return lines


def render_markdown(digest_date: date, sections: list[dict]) -> str:
    lines = [f"# NewsMan Agent Daily Brief - {digest_date.isoformat()}", ""]
    top_items = [
        item
        for section in sections
        for item in section["items"]
        if item.get("source") != "NewsMan Synthesis"
    ][:3]
    if top_items:
        lines.append("## Top Signals")
        for item in top_items:
            summary = item.get("summary_zh") or item.get("summary") or ""
            lines.append(f"- [{item['title']}]({item['url']}): {summary}")
        lines.append("")

    for section in sections:
        lines.append(f"## {section['title']}")
        if section.get("window"):
            lines.append(f"- Window: {json.dumps(section['window'], ensure_ascii=False)}")
            lines.append(f"- Status: {section['status']}")
            lines.append(section.get("message", ""))
            lines.append(f"- Excluded: {json.dumps(section.get('excluded_counts', {}))}")
        for item in section["items"]:
            lines.append(f"### {item['title']}")
            lines.append(f"- Source: {item.get('source') or 'Unknown'}")
            lines.append(f"- Score: {item['score']}")
            lines.append(f"- Published: {item.get('published_at') or 'unknown'}")
            lines.append(f"- Fetched: {item.get('fetched_at') or 'unknown'}")
            lines.append(f"- Event time: {item.get('event_at') or 'unknown'}")
            lines.append(f"- Time status: {item.get('time_status') or 'legacy_unknown'}")
            if item.get("cluster_id"):
                lines.append(f"- Event: {item['cluster_id']} ({item['cluster_method']})")
                lines.append(
                    f"- Coverage: {item['coverage_status']}; {item['source_count']} origins"
                )
                lines.append("- Event sources (not fact-verified):")
                for source in item.get("event_sources", []):
                    lines.append(f"  - [{source['title']}]({source['url']}) - {source['source']}")
            for key in (
                "summary_mode",
                "quality_status",
                "translation_status",
                "body_status",
                "verification_status",
                "consistency_status",
            ):
                lines.append(f"- {key}: {item.get(key) or 'legacy_unknown'}")
            if item.get("quality_issues"):
                lines.append(f"- Quality issues: {', '.join(item['quality_issues'])}")
            lines.append(
                f"- Summary EN: {item.get('summary_en') or '[pending generation/translation]'}"
            )
            lines.append(f"- Summary ZH: {item.get('summary_zh') or '[待生成或翻译]'}")
            if item.get("source_excerpt"):
                lines.append(
                    f"- Source excerpt ({item.get('excerpt_language', 'unknown')}): "
                    f"{item['source_excerpt']}"
                )
            lines.append(f"- Why it matters EN: {item.get('why_it_matters_en') or ''}")
            lines.append(
                f"- Why it matters ZH: "
                f"{item.get('why_it_matters_zh') or item.get('why_it_matters') or ''}"
            )
            if item.get("confidence"):
                lines.append(f"- Confidence: {item['confidence']}")
            if item.get("conservative_assumption"):
                lines.append(f"- Conservative assumption: {item['conservative_assumption']}")
            analysis = item.get("analysis") or {}
            if analysis:
                lines.append(f"- Background: {analysis.get('background') or ''}")
                lines.append(f"- Outlook: {analysis.get('outlook') or ''}")
                lines.append(f"- Risks: {analysis.get('risks') or ''}")
            if item.get("sectors"):
                lines.append(f"- Sectors: {', '.join(item['sectors'])}")
            if item.get("watchlist"):
                lines.append(f"- Watchlist: {', '.join(item['watchlist'])}")
            if not item.get("insight"):
                lines.extend(_render_evidence(item))
            if item.get("insight"):
                insight = item["insight"]
                sources = (insight.get("evidence_context") or {}).get("sources", [])
                references = {
                    fact["id"]: (f"S{si + 1}.{fi + 1}", f"{insight['kind']}-s{si + 1}-f{fi + 1}")
                    for si, source in enumerate(sources)
                    for fi, fact in enumerate(source["facts"])
                }

                def citations(ids):
                    return ", ".join(
                        f"[{references[fid][0]}](#{references[fid][1]})"
                        if fid in references
                        else "[missing reference]"
                        for fid in ids
                    )

                lines.append(
                    f"- Insight status: {insight['status']}; {insight['reason']}; publishable=false"
                )
                lines.append(f"- As of: {insight['as_of']}; valid until: {insight['valid_until']}")
                lines.append(
                    f"- Confidence basis: {json.dumps(insight['confidence_basis'], ensure_ascii=False)}"
                )
                for claim in insight["claims"]:
                    lines.extend(
                        [
                            f"- {claim['role']} ({claim['kind']}) EN: {claim['text_en']}",
                            f"  ZH: {claim['text_zh']}",
                            f"  References: {citations(claim['fact_ids'])}; counterevidence: {citations(claim['counter_fact_ids'])}",
                        ]
                    )
                for si, source in enumerate(sources):
                    lines.append(
                        f"#### S{si + 1} · {escape(source['source'])}: [{escape(source['title'])}]({source['url']})"
                    )
                    lines.append(
                        f"- Published: {source['published_at']}; {'Machine-extracted source report' if source['method'] == 'machine_source_extraction' else 'Human-supplied source review'}; not independently verified"
                    )
                    if source.get("body_scope") == "observed_extracted_text_completeness_unknown":
                        lines.append(
                            "- Quoted from observed extracted text; full-page completeness unknown."
                        )
                    for fact in source["facts"]:
                        label, anchor = references[fact["id"]]
                        lines.extend(
                            [
                                f'<a id="{anchor}"></a>',
                                f"**{label}**",
                                "> " + escape(fact["quote"]).replace("\n", "\n> "),
                            ]
                        )
                # Portable replay keeps the typed proposal, source snapshots and generation/hash boundary.
                audit = json.dumps(insight, ensure_ascii=False, indent=2)
                fence = "`" * max(
                    3, max((len(part) for part in re.findall(r"`+", audit)), default=0) + 1
                )
                lines.extend(
                    [
                        "<details><summary>Insight evidence record / Advanced audit</summary>",
                        "",
                        *[
                            f"- {references[f['id']][0]}: source_id={s['source_id']} chars={f['start']}:{f['end']}"
                            for s in sources
                            for f in s["facts"]
                        ],
                        "",
                        fence + "json",
                        audit,
                        fence,
                        "</details>",
                    ]
                )
            if item.get("source") == "NewsMan Synthesis":
                lines.append(
                    "- Original: multiple evidence sources above; no single original article."
                )
            else:
                lines.append(f"- Link: {item['url']}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_markdown_digest(
    digest_date: date,
    markdown: str,
    output_dir: Path | None = None,
) -> Path:
    project_root = Path(__file__).resolve().parents[3]
    output_dir = output_dir if output_dir is not None else project_root / "digests"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{digest_date.isoformat()}-ai-daily.md"
    # Legacy names are immutable; production revisions use the versioned exporter.
    if output_path.exists():
        if output_path.read_text(encoding="utf-8") != markdown:
            raise FileExistsError("Legacy digest is read-only; create a revision instead")
    else:
        with output_path.open("x", encoding="utf-8") as handle:
            handle.write(markdown)
    return output_path


def digest_content(
    articles: list[Article],
    digest_date: date,
    *,
    window: NewsWindow | None = None,
    excluded_counts: dict | None = None,
    events: list[EventGroup] | None = None,
    opportunity_sections: list[dict] | None = None,
) -> dict:
    window = window or news_window(get_settings(), digest_date)
    if window.target_date != digest_date:
        raise ValueError("window.target_date must match digest_date")
    # Guard direct callers too; a missing summary never widens the publication window.
    articles = [
        a for a in articles if article_time_status(a, window) in {"in_window", "late_arrival"}
    ]
    selected_ids = {str(a.id) for a in articles}
    if events is not None:
        events = [event for event in events if str(event.primary.id) in selected_ids]
    sections = build_sections(articles, window, events, opportunity_sections)
    brief = (
        "本期无符合发布时间窗口的新闻。"
        if not articles
        else "本期新闻按固定发布时间窗口筛选；生成与核验状态见各条目。"
    )
    sections.append(
        {
            "key": "run-context",
            "title": "News Window",
            "items": [],
            "window": window.as_dict(),
            "status": "ready" if articles else "empty",
            "message": brief,
            "excluded_counts": excluded_counts or {},
        }
    )
    markdown = render_markdown(digest_date, sections)
    return dict(
        title=f"NewsMan Agent AI Daily - {digest_date.isoformat()}",
        brief=brief,
        sections=sections,
        markdown=markdown,
    )


def upsert_digest(
    db: Session,
    articles: list[Article],
    digest_date: date,
    *,
    window: NewsWindow | None = None,
    excluded_counts: dict | None = None,
    events: list[EventGroup] | None = None,
    output_dir: Path | None = None,
    opportunity_sections: list[dict] | None = None,
) -> Digest:
    """Legacy caller compatibility. Existing records are never overwritten."""
    content = digest_content(
        articles,
        digest_date,
        window=window,
        excluded_counts=excluded_counts,
        events=events,
        opportunity_sections=opportunity_sections,
    )

    digest = db.query(Digest).filter(Digest.digest_date == digest_date).one_or_none()
    if digest is not None:
        raise ValueError("Legacy digest is read-only; use the versioned daily entry")
    digest = Digest(digest_date=digest_date, **content)
    db.add(digest)
    db.commit()
    write_markdown_digest(digest_date, content["markdown"], output_dir)
    db.refresh(digest)
    return digest

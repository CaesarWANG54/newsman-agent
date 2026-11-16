from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TypedDict
from uuid import UUID

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from newsman.config import Settings
from newsman.models import Digest, DigestRevision
from newsman.services.collector import collect_enabled_sources
from newsman.services.dedupe import cluster_recent_articles
from newsman.services.newsletter import upsert_digest, digest_content
from newsman.services.revisions import create_revision, export_revision, promote, content_hash
from newsman.services.ranker import rank_recent_articles
from newsman.services.sources import ensure_default_sources
from newsman.services.summarizer import summarize_ranked_articles
from newsman.services.selection import NewsWindow, news_window, select_news


class NewsManState(TypedDict, total=False):
    collected: int
    skipped: int
    clustered: int
    ranked: int
    summarized: int
    digest_id: str
    window: dict
    revision: int


def resolve_news_window(
    db: Session,
    settings: Settings,
    target_date: date | str | None = None,
    cutoff: datetime | str | None = None,
    now: datetime | None = None,
) -> NewsWindow:
    window = news_window(settings, target_date, cutoff, now)
    if cutoff is None:
        previous = db.query(Digest).filter(Digest.digest_date == window.target_date).one_or_none()
        for section in previous.sections if previous else []:
            saved = section.get("window") if section.get("key") == "run-context" else None
            if saved:
                pinned = settings.model_copy(update={"daily_timezone": saved["timezone"]})
                return news_window(pinned, window.target_date, saved["cutoff"], now)
    return window


def build_newsman_graph(
    db: Session,
    settings: Settings,
    *,
    target_date: date | str | None = None,
    cutoff: datetime | str | None = None,
    now: datetime | None = None,
    output_dir: Path | None = None,
    run=None,
):
    if run:
        saved = run.window
        pinned = settings.model_copy(update={"daily_timezone": saved["timezone"]})
        window = news_window(
            pinned, run.target_date, saved["cutoff"], datetime.fromisoformat(saved["observed_at"])
        )
    else:
        window = resolve_news_window(db, settings, target_date, cutoff, now)
    graph = StateGraph(NewsManState)

    def selected_news():
        if run:
            if "preference_snapshot" not in run.diagnostics:
                from newsman.services.preferences import preferences

                run.diagnostics = {
                    **run.diagnostics,
                    "preference_snapshot": preferences(db),
                    "selection_version": "feedback-diversity-v1",
                }
                db.commit()
            return select_news(
                db, window, settings.digest_article_limit, run.diagnostics["preference_snapshot"]
            )
        return select_news(db, window, settings.digest_article_limit)

    def ensure_sources(state: NewsManState) -> NewsManState:
        ensure_default_sources(db)
        return state

    def collect(state: NewsManState) -> NewsManState:
        def save_sources(outcomes):
            run.diagnostics = {**run.diagnostics, "sources": outcomes}
            db.commit()

        kwargs = (
            {"progress": run.diagnostics.get("sources", {}), "checkpoint": save_sources, "now": now}
            if run
            else {}
        )
        result = asyncio.run(collect_enabled_sources(db, settings.max_articles_per_run, **kwargs))
        if run:
            save_sources(result.outcomes)
            run.diagnostics = {
                **run.diagnostics,
                "cutoff_not_reached": (now or datetime.now(timezone.utc)) < window.cutoff,
            }
        return {**state, "collected": result.collected, "skipped": result.skipped}

    def dedupe(state: NewsManState) -> NewsManState:
        clustered = cluster_recent_articles(db)
        return {**state, "clustered": clustered}

    def rank(state: NewsManState) -> NewsManState:
        ranked = rank_recent_articles(db)
        return {**state, "ranked": ranked}

    def summarize(state: NewsManState) -> NewsManState:
        nonlocal window
        if run and not run.diagnostics.get("availability_frozen"):
            observed = now or datetime.now(timezone.utc)
            window = replace(window, observed_at=observed.astimezone(window.cutoff.tzinfo))
            run.window = window.as_dict()
            run.diagnostics = {**run.diagnostics, "availability_frozen": True}
            db.commit()
        elif not run and now is None:
            # Collection finishes after graph construction. Freeze availability only now,
            # while keeping the publication boundaries fixed for this target date.
            window = replace(
                window, observed_at=datetime.now(timezone.utc).astimezone(window.cutoff.tzinfo)
            )
        selection = selected_news()
        if run:
            progress = dict(run.diagnostics.get("summaries", {}))
            for article in selection.articles:
                fingerprint = content_hash(
                    {
                        "title": article.title,
                        "body": article.text_content,
                        "excerpt": article.raw_excerpt,
                        "model": settings.openai_model,
                        "provider": settings.llm_provider,
                        "version": "bilingual-v2",
                    }
                )
                summary = (article.metadata_json or {}).get("ai_summary", {})
                ready = (
                    summary.get("quality_status") == "ready"
                    and summary.get("input_hash") == fingerprint
                )
                if progress.get(str(article.id)) == fingerprint or ready:
                    continue
                summarize_ranked_articles(settings, [article])
                article.metadata_json = {
                    **article.metadata_json,
                    "ai_summary": {
                        **article.metadata_json["ai_summary"],
                        "input_hash": fingerprint,
                    },
                }
                progress[str(article.id)] = fingerprint
                run.diagnostics = {**run.diagnostics, "summaries": progress}
                db.commit()
            pending = sum(
                (a.metadata_json or {}).get("ai_summary", {}).get("quality_status") != "ready"
                for a in selection.articles
            )
            run.diagnostics = {**run.diagnostics, "pending_summary": pending}
            summarized = len(selection.articles)
        else:
            summarized = summarize_ranked_articles(settings, selection.articles)
        db.commit()
        return {**state, "summarized": summarized}

    def digest(state: NewsManState) -> NewsManState:
        if run and "preference_snapshot" not in run.diagnostics:
            # Older interrupted runs had no preference snapshot. Repair the chosen
            # summaries before creating a new revision; committed revisions stay untouched.
            summarize(state)
        selection = selected_news()
        from newsman.services.machine_evidence import prepare_sections

        opportunity_sections = prepare_sections(db, selection.events, window, settings, run)
        if run:
            from newsman.services.runs import completion_status

            content = digest_content(
                selection.articles,
                window.target_date,
                window=window,
                excluded_counts=selection.excluded_counts,
                events=selection.events,
                opportunity_sections=opportunity_sections,
            )
            revision = create_revision(db, run, content, completion_status(run))
            return {
                **state,
                "digest_id": str(revision.id),
                "revision": revision.revision,
                "window": window.as_dict(),
            }
        daily_digest = upsert_digest(
            db,
            selection.articles,
            window.target_date,
            window=window,
            excluded_counts=selection.excluded_counts,
            events=selection.events,
            output_dir=output_dir,
            opportunity_sections=opportunity_sections,
        )
        return {**state, "digest_id": str(daily_digest.id), "window": window.as_dict()}

    def export(state):
        revision = db.get(DigestRevision, UUID(state["digest_id"]))
        export_revision(revision, output_dir)
        revision.exported_at = datetime.now(timezone.utc)
        promote(db, revision)
        return state

    def checkpoint(name, operation):
        if run is None:
            return operation

        def invoke(state):
            if name in run.checkpoints:
                return {**state, **run.checkpoints[name]}
            run.stage = name
            run.updated_at = datetime.now(timezone.utc)
            db.commit()
            result = operation(state)
            run.checkpoints = {**run.checkpoints, name: dict(result)}
            db.commit()
            return result

        return invoke

    for name, operation in [
        ("ensure_sources", ensure_sources),
        ("collect", collect),
        ("dedupe", dedupe),
        ("rank", rank),
        ("summarize", summarize),
        ("digest", digest),
    ]:
        graph.add_node(name, checkpoint(name, operation))

    graph.set_entry_point("ensure_sources")
    graph.add_edge("ensure_sources", "collect")
    graph.add_edge("collect", "dedupe")
    graph.add_edge("dedupe", "rank")
    graph.add_edge("rank", "summarize")
    graph.add_edge("summarize", "digest")
    if run:
        graph.add_node("export", checkpoint("export", export))
        graph.add_edge("digest", "export")
        graph.add_edge("export", END)
    else:
        graph.add_edge("digest", END)
    return graph.compile()


def run_daily_graph(
    db: Session,
    settings: Settings,
    *,
    target_date: date | str | None = None,
    cutoff: datetime | str | None = None,
    now: datetime | None = None,
    output_dir: Path | None = None,
    mode: str = "ensure",
    request_key: str | None = None,
) -> NewsManState:
    from newsman.services.runs import execute_daily

    # Never use the caller's transaction for ownership; the executor binds all writes
    # to one locked physical connection and closes it before returning.
    return execute_daily(
        db.get_bind(),
        settings,
        target_date=target_date,
        cutoff=cutoff,
        now=now,
        output_dir=output_dir,
        mode=mode,
        request_key=request_key,
    )

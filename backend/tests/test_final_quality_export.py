"""Curated content outcomes and portable evidence, not online fact approval."""

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from newsman.services import revisions
from newsman.services.newsletter import build_sections, render_markdown
from newsman.services.summarizer import summarize_article

CASES = json.loads(
    (Path(__file__).parent / "fixtures/content_quality_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_curated_summary_outcomes(case, settings, model_client):
    source = case["source"] + " The source describes the release and its current limitations."
    article = SimpleNamespace(
        title=case["source"],
        canonical_url="https://source.invalid/report",
        text_content=source * 3,
        raw_excerpt="",
        metadata_json={"extraction_status": "available"},
        summary=None,
        why_it_matters=None,
    )
    model_client.responses.create.return_value = SimpleNamespace(
        output_text=json.dumps(
            dict(
                summary_en=case["en"],
                summary_zh=case["zh"],
                why_it_matters_en="The source describes the release and its current limitations.",
                why_it_matters_zh="来源描述了发布情况及其当前限制。",
            )
        )
    )
    result = summarize_article(
        settings.model_copy(update={"llm_provider": "openai", "openai_api_key": "offline-fixture"}),
        article,
    )
    assert result.quality_status == case["expected"]
    assert result.verification_status == "not_verified"
    if case["issue"]:
        assert case["issue"] in result.quality_issues
        assert result.summary_mode == "source_excerpt"
        assert result.source_excerpt in article.text_content
    else:
        assert result.summary_en == case["en"] and result.summary_zh == case["zh"]
    model_client.responses.create.assert_called_once()


def test_readable_markdown_keeps_exact_audit_and_bilingual_order(monkeypatch):
    from qa.insight_fixtures import fixtures, MockProvider
    from newsman.services import insights

    articles, window = fixtures()
    monkeypatch.setattr(insights, "ConfiguredProvider", MockProvider)
    sections = build_sections(articles, window)
    markdown = render_markdown(window.target_date, sections)
    insight = next(s for s in sections if s["key"] == "investment-lens")["items"][0]["insight"]
    section = markdown.split("## Investment Lens", 1)[1]
    visible, audit = section.split("<details>", 1)
    ref = insight["claims"][0]["fact_ids"][0]
    source_index, fact_index = next(
        (si + 1, fi + 1)
        for si, source in enumerate(insight["evidence_context"]["sources"])
        for fi, fact in enumerate(source["facts"])
        if fact["id"] == ref
    )
    assert f"[S{source_index}.{fact_index}](#investment-s{source_index}-f{fact_index})" in visible
    assert '<a id="investment-s1-f1"></a>' in visible
    for source in insight["evidence_context"]["sources"]:
        assert source["source_id"] not in visible
        assert source["body_sha256"] not in visible
        assert source["body_sha256"] in audit
        for fact in source["facts"]:
            assert fact["quote"] in visible
            assert f"chars={fact['start']}:{fact['end']}" in audit
    snapshot = json.loads(audit.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert snapshot == insight
    assert visible.index("Summary EN:") < visible.index("Summary ZH:")
    assert "publishable=false" in visible


def test_compare_keeps_both_synthetic_columns_and_article_urls(monkeypatch):
    def value(change=False):
        return SimpleNamespace(
            title="QA",
            brief="",
            markdown="",
            sections=[
                {
                    "key": "startup-opportunity",
                    "items": [{"url": "", "summary": "changed" if change else "original"}],
                },
                {"key": "investment-lens", "items": [{"url": "", "summary": "unchanged"}]},
                {
                    "key": "general",
                    "items": [{"url": "https://source.invalid/a", "summary": "unchanged"}],
                },
            ],
        )

    old, new = value(), value(True)
    monkeypatch.setattr(
        revisions, "read_digest", lambda db, day, revision: old if revision == 1 else new
    )
    comparison = revisions.compare(None, date(2026, 10, 25), 1, 2)
    assert comparison["changed"] == ["insight:startup-opportunity"]
    assert comparison["added"] == comparison["removed"] == []


def test_markdown_untrusted_quote_cannot_close_audit_fence(monkeypatch):
    from qa.insight_fixtures import fixtures, MockProvider
    from newsman.services import insights

    articles, window = fixtures()
    monkeypatch.setattr(insights, "ConfiguredProvider", MockProvider)
    sections = deepcopy(build_sections(articles, window))
    item = sections[-1]["items"][0]
    item["insight"]["evidence_context"]["sources"][0]["facts"][0]["quote"] = (
        "```\n<script>untrusted</script>"
    )
    markdown = render_markdown(window.target_date, [sections[-1]])
    visible, audit = markdown.split("<details>", 1)
    assert "<script>" not in visible
    assert "&lt;script&gt;" in visible
    assert "````json" in audit

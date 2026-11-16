from copy import deepcopy
from datetime import timedelta
import json

import pytest

from qa.insight_fixtures import fixtures, MockProvider
from newsman.services.insights import build_opportunity_sections, validate_proposal
from newsman.services.newsletter import build_sections, render_markdown
from newsman.schemas import DigestItemRead


def test_positive_fixture_is_conditional_located_bilingual_and_replayable():
    articles, window = fixtures()
    first = build_opportunity_sections(articles, window, provider=MockProvider())
    second = build_opportunity_sections(list(reversed(articles)), window, provider=MockProvider())
    assert first == second
    for section in first:
        item = section["items"][0]
        result = item["insight"]
        assert result["status"] == "review_required" and not result["publishable"]
        assert item["url"] == "" and item["summary_en"] and item["summary_zh"]
        assert result["confidence"] == "not_calibrated"
        assert len(item["evidence"]) == 2
        replay = validate_proposal(result["kind"], result["proposal"], result["evidence_context"])
        assert replay["claims"] == result["claims"]
        DigestItemRead.model_validate(item)
        for claim in result["claims"]:
            assert claim["fact_ids"]
    assert first[1]["items"][0]["insight"]["companies"][0]["entity"] == "ExampleCo"
    assert "NVDA" not in json.dumps(first)


@pytest.mark.parametrize(
    "case",
    [
        "unreviewed",
        "old",
        "future",
        "missing_date",
        "naive_date",
        "wrong_url",
        "unsafe_url",
        "source_id",
        "body_changed",
        "body_incomplete",
        "quote",
        "locator",
        "empty_facts",
        "future_review",
        "unknown_conflict",
        "conflict",
        "expired",
        "future_applicability",
        "reprint",
        "same_origin",
        "same_publisher",
        "duplicate_body",
        "duplicate_fact",
        "wrong_entity",
        "material_adverse",
        "already_priced",
        "contradiction",
        "unrelated",
    ],
)
def test_bad_or_insufficient_evidence_never_calls_provider(case):
    articles, window = fixtures()
    a, b = articles
    meta = b.metadata_json
    review = meta["insight_evidence"]
    fact = review["facts"][0]
    if case == "unreviewed":
        meta.pop("insight_evidence")
    elif case == "old":
        b.published_at -= timedelta(days=4)
    elif case == "future":
        b.published_at += timedelta(days=2)
    elif case == "missing_date":
        b.published_at = None
    elif case == "naive_date":
        b.published_at = b.published_at.replace(tzinfo=None)
    elif case == "wrong_url":
        review["url"] += "/invented"
    elif case == "unsafe_url":
        review["url"] = b.canonical_url = "javascript:alert(1)"
    elif case == "source_id":
        review["source_id"] = "not-this-source"
    elif case == "body_changed":
        b.text_content += " changed"
    elif case == "body_incomplete":
        meta["body_complete"] = False
    elif case == "quote":
        fact["quote"] = "Invented quote"
    elif case == "locator":
        fact["start"] += 1
    elif case == "empty_facts":
        review["facts"] = []
    elif case == "future_review":
        review["reviewed_at"] = "2026-10-26T10:00:00+08:00"
    elif case == "unknown_conflict":
        review["conflict_status"] = "not_reviewed"
    elif case == "conflict":
        review["conflict_status"] = "unresolved"
    elif case == "expired":
        fact["valid_until"] = "2026-10-25T09:30:00+08:00"
    elif case == "future_applicability":
        fact["valid_from"] = "2026-10-26T08:00:00+08:00"
    elif case == "reprint":
        meta["original_url"] = a.canonical_url
    elif case == "same_origin":
        review["independence_group"] = a.metadata_json["insight_evidence"]["independence_group"]
    elif case == "same_publisher":
        b.canonical_url = review["url"] = a.canonical_url + "/different"
    elif case == "duplicate_body":
        b.text_content = a.text_content
        review["body_sha256"] = a.metadata_json["insight_evidence"]["body_sha256"]
        review["facts"] = deepcopy(a.metadata_json["insight_evidence"]["facts"])
    elif case == "duplicate_fact":
        fact["id"] = "demand"
    elif case == "wrong_entity":
        fact["entity"] = "Invented Corp"
    elif case == "material_adverse":
        fact["material_adverse"] = True
    elif case == "already_priced":
        fact["already_priced"] = True
    elif case == "contradiction":
        fact["contradicts"] = ["event"]
    elif case == "unrelated":
        review["event_id"] = "different-event"
    provider = MockProvider()
    result = build_opportunity_sections(articles, window, provider=provider)
    assert not provider.calls
    assert all(s["items"][0]["insight"]["status"] == "no_conclusion" for s in result)


@pytest.mark.parametrize(
    "case",
    [
        "unknown_ref",
        "empty_ref",
        "invented_fact",
        "missing_role",
        "no_counter",
        "false_counter",
        "company",
        "symbol",
        "company_ref",
        "extra_forecast",
        "return_text",
        "template_horizon",
        "not_bilingual",
        "invalid_json_type",
        "duplicate_claim",
        "refusal_payload",
    ],
)
def test_invalid_provider_output_fails_closed(case):
    articles, window = fixtures()

    class Bad(MockProvider):
        def propose(self, kind, context):
            proposal = super().propose(kind, context)
            if kind != "investment":
                return proposal
            claim = next(c for c in proposal["claims"] if c["role"] == "transmission")
            if case == "unknown_ref":
                claim["fact_ids"] = ["invented"]
            elif case == "empty_ref":
                claim["fact_ids"] = []
            elif case == "invented_fact":
                proposal["claims"][0]["text_en"] = "Fabricated fact"
            elif case == "missing_role":
                proposal["claims"] = proposal["claims"][:-1]
            elif case == "no_counter":
                claim["counter_fact_ids"] = []
            elif case == "false_counter":
                claim["counter_fact_ids"] = ["event"]
            elif case == "company":
                proposal["companies"][0]["entity"] = "Unrelated Corp"
            elif case == "symbol":
                proposal["companies"][0]["symbol"] = "NVDA"
            elif case == "company_ref":
                proposal["companies"][0]["fact_ids"] = ["event"]
            elif case == "extra_forecast":
                proposal["price_target"] = 500
            elif case == "return_text":
                claim["text_en"] = "Expect strong investment returns."
            elif case == "template_horizon":
                claim["text_zh"] = "未来12-24个月增长。"
            elif case == "not_bilingual":
                claim["text_zh"] = "No translation here."
            elif case == "invalid_json_type":
                return []
            elif case == "duplicate_claim":
                proposal["claims"].append(proposal["claims"][0])
            elif case == "refusal_payload":
                proposal.update(decision="no_conclusion", reason="risk_dominates")
            return proposal

    item = build_opportunity_sections(articles, window, provider=Bad())[1]["items"][0]
    assert item["insight"]["status"] == "no_conclusion"
    assert item["insight"]["reason"] == "invalid_proposal"
    assert item["insight"]["proposal"] is None


def test_source_conflict_flag_and_missing_window_are_not_bypassed():
    articles, window = fixtures()
    provider = MockProvider()
    assert (
        build_opportunity_sections(articles, provider=provider)[0]["items"][0]["insight"]["reason"]
        == "missing_window"
    )
    result = build_opportunity_sections(
        articles, window, provider=provider, conflict_article_ids={str(articles[0].id)}
    )
    assert all(s["items"][0]["insight"]["reason"] == "conflicting_evidence" for s in result)
    assert not provider.calls


def test_newsletter_members_schema_and_export_keep_evidence(monkeypatch):
    from newsman.services import insights

    articles, window = fixtures()
    monkeypatch.setattr(insights, "ConfiguredProvider", MockProvider)
    sections = build_sections(articles, window)
    item = next(s for s in sections if s["key"] == "investment-lens")["items"][0]
    assert item["summary_mode"] == "evidence_synthesis"
    assert item["insight"]["status"] == "review_required"
    assert len(item["evidence"]) == 2
    DigestItemRead.model_validate(item)
    markdown = render_markdown(window.target_date, sections)
    assert markdown.index("Summary EN:") < markdown.index("Summary ZH:")
    assert "chars=" in markdown and "source_id=" in markdown
    assert "Insight evidence record" in markdown and "publishable=false" in markdown
    assert "Original: multiple evidence sources" in markdown


def test_configured_client_uses_mock_and_handles_failure(settings, monkeypatch, model_client):
    from newsman.services import insights

    articles, window = fixtures()
    settings.llm_provider = "openai"
    settings.openai_api_key = "offline-only"
    monkeypatch.setattr("newsman.config.get_settings", lambda: settings)
    provider = MockProvider()

    def reply(**kwargs):
        from types import SimpleNamespace

        data = json.loads(kwargs["input"][1]["content"])
        return SimpleNamespace(
            output_text=json.dumps(provider.propose(data["kind"], data["evidence"]))
        )

    model_client.responses.create.side_effect = reply
    result = insights.build_opportunity_sections(articles, window)
    assert all(s["items"][0]["insight"]["status"] == "review_required" for s in result)
    assert model_client.responses.create.call_count == 2
    model_client.embeddings.create.assert_not_called()
    model_client.responses.create.side_effect = TimeoutError("sensitive provider error")
    result = insights.build_opportunity_sections(articles, window)
    assert "sensitive" not in json.dumps(result)
    assert all(s["items"][0]["insight"]["reason"] == "provider_unavailable" for s in result)


@pytest.mark.parametrize(
    "reason", ["insufficient_evidence", "risk_dominates", "already_priced", "conflicting_evidence"]
)
def test_provider_may_decline_without_forcing_positive_conclusion(reason):
    articles, window = fixtures()

    class Decline(MockProvider):
        def propose(self, *args):
            return {"decision": "no_conclusion", "reason": reason}

    result = build_opportunity_sections(articles, window, provider=Decline())
    assert all(s["items"][0]["insight"]["reason"] == reason for s in result)
    assert all(s["items"][0]["insight"]["companies"] == [] for s in result)


def test_adverse_evidence_is_preserved_when_conclusion_withheld():
    articles, window = fixtures()
    articles[1].metadata_json["insight_evidence"]["facts"][0]["material_adverse"] = True
    item = build_opportunity_sections(articles, window, provider=MockProvider())[1]["items"][0]
    assert item["insight"]["reason"] == "risk_dominates"
    assert len(item["evidence"]) == 2
    assert any(
        f["material_adverse"]
        for s in item["insight"]["evidence_context"]["sources"]
        for f in s["facts"]
    )


def test_sector_analysis_does_not_require_or_inherit_a_stock_list():
    articles, window = fixtures()
    review = articles[0].metadata_json["insight_evidence"]
    review["facts"] = [f for f in review["facts"] if f["role"] != "company_exposure"]

    class SectorOnly(MockProvider):
        def propose(self, kind, context):
            result = super().propose(kind, context)
            result["companies"] = []
            if kind == "investment":
                next(c for c in result["claims"] if c["role"] == "transmission")["fact_ids"] = [
                    "event",
                    "sector",
                ]
            return result

    item = build_opportunity_sections(articles, window, provider=SectorOnly())[1]["items"][0]
    assert item["insight"]["status"] == "review_required"
    assert item["insight"]["companies"] == [] and "watchlist" not in item


def test_invalid_provider_identity_cannot_break_serialization():
    articles, window = fixtures()
    provider = MockProvider()
    provider.name = object()
    sections = build_opportunity_sections(articles, window, provider=provider)
    assert all(s["items"][0]["insight"]["reason"] == "provider_unavailable" for s in sections)
    assert all(s["items"][0]["insight"]["proposal"] is None for s in sections)
    json.dumps(sections)


def test_temporary_database_and_export_roundtrip_without_graph(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, JSON
    from sqlalchemy.orm import Session
    from newsman.models import Digest
    from newsman.schemas import DigestRead
    from newsman.services import insights
    from newsman.services.newsletter import digest_content, write_markdown_digest

    articles, window = fixtures()
    monkeypatch.setattr(insights, "ConfiguredProvider", MockProvider)
    content = digest_content(articles, window.target_date, window=window)
    engine = create_engine(f"sqlite:///{tmp_path / 'insight.db'}")
    try:
        # Test only the JSON/API roundtrip; PostgreSQL concurrency/migrations are out of scope.
        column = Digest.__table__.c.sections
        monkeypatch.setattr(column, "type", column.type.with_variant(JSON(), "sqlite"))
        Digest.__table__.create(engine)
        with Session(engine) as db:
            row = Digest(digest_date=window.target_date, **content)
            db.add(row)
            db.commit()
            db.refresh(row)
            result = DigestRead.model_validate(row)
            investment = next(s for s in result.sections if s.key == "investment-lens").items[0]
            assert investment.insight.status == "review_required"
            assert investment.insight.evidence_context["sources"][0]["facts"][0]["quote"]
            path = write_markdown_digest(window.target_date, row.markdown, tmp_path / "exports")
            assert "Insight evidence record" in path.read_text(encoding="utf-8")
            assert result.model_dump(mode="json")["sections"][-1]["key"] == "run-context"
    finally:
        engine.dispose()

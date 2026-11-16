import json
from datetime import date
from types import SimpleNamespace

import pytest

from newsman.services.newsletter import _ensure_bilingual_item, build_sections, render_markdown
from newsman.services.summarizer import (
    consistency_issues,
    looks_like_chinese_summary,
    rule_based_summary,
    summary_payload,
    summarize_article,
    summarize_ranked_articles,
)


def make_article(**overrides):
    data = dict(
        id="article-1",
        title="OpenAI releases Codex",
        canonical_url="https://example.com/codex",
        text_content=(
            "OpenAI releases Codex with 10 ms latency. The documentation describes "
            "how developers can evaluate the tool using the provided API examples."
        ),
        raw_excerpt="",
        category="models-products",
        tags=["agent"],
        importance_score=0.82,
        summary=None,
        why_it_matters=None,
        embedding=None,
        metadata_json={"source_name": "Example"},
        source=SimpleNamespace(credibility=0.98),
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def model_settings(settings):
    return settings.model_copy(update={"llm_provider": "openai", "openai_api_key": "fake-offline"})


def good_response():
    return dict(
        summary_en="OpenAI releases Codex with 10 ms latency.",
        summary_zh="OpenAI 发布 Codex，延迟为 10 毫秒。",
        why_it_matters_en="Developers can evaluate Codex using the API examples.",
        why_it_matters_zh="开发者可以通过 API 示例评估 Codex。",
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("已开源。", True),
        ("Claude Sonnet 4.5 已发布。", True),
        ("Gemini Flash-Lite 支持图像。", True),
        ("Google DeepMind Gemini Flash Lite 已发布。", True),
        ("消息：OpenAI builds models。", False),
        ("消息显示，OpenAI releases a new model for developers。", False),
        ("OpenAI released a new model.", False),
        ("", False),
    ],
)
def test_language_gate_allows_names_but_rejects_english_sentences(text, expected):
    assert looks_like_chinese_summary(text) is expected


def test_fallback_is_an_attributed_excerpt_with_translation_pending():
    article = make_article()
    summary = rule_based_summary(article)
    assert summary.summary_en == article.text_content
    assert summary.source_excerpt == article.text_content
    assert summary.summary_zh == summary.why_it_matters_zh == ""
    assert summary.summary_mode == "source_excerpt"
    assert summary.excerpt_language == "en"
    assert summary.translation_status == "pending_zh"
    assert summary.verification_status == "not_verified"


def test_chinese_source_is_not_duplicated_as_english():
    summary = rule_based_summary(make_article(text_content="已开源。"))
    assert summary.summary_zh == "已开源。" and not summary.summary_en
    assert summary.translation_status == "pending_en"


@pytest.mark.parametrize(
    "response",
    [
        "not JSON including invented 999",
        "null",
        "[]",
        "true",
        '"text"',
        '{"summary_en": 123}',
        '{"summary_en": {}}',
        json.dumps({**good_response(), "summary_zh": []}),
        json.dumps({**good_response(), "summary_en": ""}),
    ],
)
def test_invalid_provider_responses_never_become_summary(settings, model_client, response):
    model_client.responses.create.return_value = SimpleNamespace(output_text=response)
    result = summarize_article(model_settings(settings), make_article())
    assert result.summary_mode == "source_excerpt"
    assert result.quality_issues == ("invalid_response",)
    assert result.summary_en == make_article().text_content and not result.summary_zh


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("provider failure")])
def test_provider_exceptions_degrade_without_leaking_error(settings, model_client, error):
    model_client.responses.create.side_effect = error
    result = summarize_article(model_settings(settings), make_article())
    assert result.quality_issues == ("provider_error",)
    assert result.verification_status == "not_verified"


@pytest.mark.parametrize(
    "body,metadata,status",
    [
        ("", {}, "insufficient"),
        ("Short body.", {}, "insufficient"),
        ("Full body. " * 700, {}, "truncated"),
        ("Full body. " * 20, {"content_truncated": True}, "truncated"),
        ("Full body. " * 20, {"body_complete": False}, "insufficient"),
    ],
)
def test_incomplete_body_does_not_call_model(settings, model_client, body, metadata, status):
    result = summarize_article(
        model_settings(settings),
        make_article(
            text_content=body,
            metadata_json=metadata,
        ),
    )
    model_client.responses.create.assert_not_called()
    assert result.body_status == status
    assert result.summary_mode == "source_excerpt"


def test_both_provider_surfaces_are_mocked_and_chinese_compatibility_is_kept(
    settings, model_client
):
    model_client.responses.create.return_value = SimpleNamespace(
        output_text=json.dumps(good_response())
    )
    article = make_article()
    assert summarize_ranked_articles(model_settings(settings), [article]) == 1
    assert article.summary == good_response()["summary_zh"]
    assert article.why_it_matters == good_response()["why_it_matters_zh"]
    payload = article.metadata_json["ai_summary"]
    assert payload["summary_mode"] == "model" and payload["quality_status"] == "ready"
    assert payload["consistency_status"] == "no_issues_detected"
    assert payload["verification_status"] == "not_verified"
    model_client.responses.create.assert_called_once()
    model_client.embeddings.create.assert_called_once()
    assert len(article.embedding) == 1536


def test_embedding_error_does_not_discard_the_summary(settings, model_client):
    model_client.responses.create.return_value = SimpleNamespace(
        output_text=json.dumps(good_response())
    )
    model_client.embeddings.create.side_effect = TimeoutError()
    article = make_article()
    summarize_ranked_articles(model_settings(settings), [article])
    assert article.embedding is None
    assert article.metadata_json["ai_summary"]["summary_mode"] == "model"


@pytest.mark.parametrize(
    "source,output,issue",
    [
        ("OpenAI latency is 10 ms.", "OpenAI latency is 20 ms.", "number_mismatch"),
        ("OpenAI latency is 10 ms.", "OpenAI latency is 10 s.", "unit_mismatch"),
        ("OpenAI does not support images.", "OpenAI supports images.", "negation_mismatch"),
        ("OpenAI released Codex.", "Microsoft released Codex.", "subject_mismatch"),
        ("OpenAI发布了模型。", "Microsoft发布了模型。", "subject_mismatch"),
        ("延迟10毫秒。", "延迟20毫秒。", "number_mismatch"),
        ("Read https://example.com/a", "Read https://example.com/b", "link_mismatch"),
    ],
)
def test_detectable_inconsistencies_are_flagged(source, output, issue):
    assert issue in consistency_issues(source, output)


def test_inconsistent_model_output_is_withheld(settings, model_client):
    bad = {**good_response(), "summary_en": "Microsoft releases Codex with 999 s latency."}
    model_client.responses.create.return_value = SimpleNamespace(output_text=json.dumps(bad))
    result = summarize_article(model_settings(settings), make_article())
    assert result.quality_status == "needs_review"
    assert result.consistency_status == "issues_detected"
    assert "999" not in result.summary_en
    assert result.verification_status == "not_verified"


def test_untranslated_model_response_degrades_to_source(settings, model_client):
    bad = {**good_response(), "summary_zh": "OpenAI releases a new model for developers."}
    model_client.responses.create.return_value = SimpleNamespace(output_text=json.dumps(bad))
    result = summarize_article(model_settings(settings), make_article())
    assert result.quality_issues == ("invalid_translation",)
    assert result.translation_status == "pending_zh"
    assert not result.summary_zh


@pytest.mark.parametrize(
    "legacy,lang", [("已开源。", "zh"), ("OpenAI releases Codex.", "en"), (None, None)]
)
def test_both_newsletter_fallbacks_preserve_legacy_language_without_inventing(legacy, lang):
    article = make_article(summary=legacy)
    item = build_sections([article])[0]["items"][0]
    assert item["summary_mode"] == "legacy_unknown"
    assert item["verification_status"] == "not_verified"
    assert item["summary_zh"] == (legacy if lang == "zh" else "")
    assert item["summary_en"] == (legacy if lang == "en" else "")
    again = _ensure_bilingual_item(item)
    assert again == item
    md = render_markdown(date(2026, 10, 25), [dict(title="News", items=[item])])
    assert md.index("Summary EN") < md.index("Summary ZH")
    assert "legacy_unknown" in md and "not_verified" in md


def test_invalid_legacy_translation_stays_pending():
    payload = summary_payload(
        {"summary_zh": "OpenAI releases a model for all developers.", "summary_en": "已开源。"}
    )
    assert payload["summary_zh"] == payload["summary_en"] == ""
    assert payload["translation_status"] == "pending_both"
    assert payload["quality_status"] == "needs_review"


def test_pending_translation_does_not_resurrect_old_compatibility_summary():
    payload = summary_payload(
        {"summary_mode": "source_excerpt", "summary_en": "Source text."},
        legacy_summary="此前的中文模板。",
        legacy_why="以前的影响分析。",
    )
    assert payload["summary_zh"] == payload["why_it_matters_zh"] == ""
    assert payload["translation_status"] == "pending_zh"


def test_downgrade_status_survives_both_newsletter_paths(settings):
    article = make_article()
    summarize_ranked_articles(settings, [article])
    item = build_sections([article])[0]["items"][0]
    assert item["summary_mode"] == "source_excerpt"
    assert item["quality_status"] == "degraded"
    assert item["translation_status"] == "pending_zh"
    assert not item["summary_zh"]

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from newsman.services.cleaner import canonicalize_url
from newsman.services.dedupe import group_articles, match_articles
from newsman.services.newsletter import build_sections, render_markdown
from newsman.services.selection import news_window, select_news
from test_selection import FakeDB, DAY, NOW


def story(title, url, **kwargs):
    values = dict(
        id=uuid4(),
        title=title,
        canonical_url=url,
        url=url,
        published_at=datetime.fromisoformat("2026-10-25T07:00:00+08:00"),
        fetched_at=NOW - timedelta(hours=1),
        importance_score=0.7,
        metadata_json={"source_name": url.split("/")[2] if url else "Unknown"},
        source_id=uuid4(),
        source=None,
        text_content="Fixed source text.",
        raw_excerpt="",
        category="general",
        tags=[],
        summary=None,
        why_it_matters=None,
        embedding=None,
    )
    values.update(kwargs)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "left,right",
    [
        ("OpenAI发布GPT-5模型", "OpenAI releases GPT-5 model"),
        ("微软发布Gemini 2.5模型", "Microsoft releases Gemini 2.5 model"),
        ("模型正式开源", "模型正式开源"),
        ("OpenAI releases a new API for agents", "OpenAI releases API for agent apps"),
    ],
)
def test_same_event_multilingual_and_paraphrased_titles(left, right):
    groups = group_articles(
        [story(left, "https://one.example/a"), story(right, "https://two.example/b")]
    )
    assert len(groups) == 1
    assert groups[0].as_dict()["source_count"] == 2
    assert len(groups[0].evidence()) == 2


@pytest.mark.parametrize(
    "left,right",
    [
        ("OpenAI releases GPT-5 model", "OpenAI releases GPT-6 model"),
        ("OpenAI releases GPT-5 mini", "OpenAI releases GPT-5 pro"),
        ("Google releases Gemini 2.5 model", "Google releases Gemini 3.0 model"),
        ("微软发布新模型", "谷歌发布新模型"),
        ("OpenAI acquires a company", "OpenAI releases a model"),
        ("模型正式开源", "芯片供应短缺"),
        ("", ""),
    ],
)
def test_conflicting_models_companies_actions_and_empty_titles_do_not_merge(left, right):
    assert (
        len(
            group_articles(
                [story(left, "https://one.example/a"), story(right, "https://two.example/b")]
            )
        )
        == 2
    )


def test_empty_title_without_url_does_not_collapse_all_rows():
    assert len(group_articles([story("", ""), story("", "")])) == 2


def test_url_normalization_preserves_identity_query_and_deduplicates_tracking():
    assert (
        canonicalize_url("https://EXAMPLE.com:443/story/?b=2&UTM_source=x&a=1#part")
        == "https://example.com/story?a=1&b=2"
    )
    assert canonicalize_url("https://example.com/story?id=1") != canonicalize_url(
        "https://example.com/story?id=2"
    )
    groups = group_articles(
        [
            story("Old title", "https://example.com/story?utm_source=a"),
            story("Updated title", "https://EXAMPLE.com/story#new"),
        ]
    )
    assert len(groups) == 1 and groups[0].as_dict()["source_count"] == 1


def test_reprints_keep_links_but_do_not_inflate_independent_origin_count():
    a = story("OpenAI releases GPT-5 model", "https://original.example/article")
    b = story(
        "OpenAI发布GPT-5模型",
        "https://reprint.example/article",
        metadata_json={"source_name": "Reprint", "original_url": a.url, "is_reprint": True},
    )
    group = group_articles([a, b])[0]
    assert group.as_dict()["source_count"] == 1
    assert group.as_dict()["coverage_status"] == "limited_sources"
    assert any(item["is_reprint"] for item in group.evidence())


def test_identical_full_body_is_not_independent_corroboration():
    a = story(
        "OpenAI releases GPT-5 model",
        "https://one.example/a",
        text_content="Same source prose. " * 30,
    )
    b = story("OpenAI发布GPT-5模型", "https://two.example/b", text_content=a.text_content)
    group = group_articles([a, b])[0]
    assert group.as_dict()["source_count"] == 1
    assert len(group.evidence()) == 2
    assert any(item["same_content"] for item in group.evidence())


def test_same_headline_far_apart_is_not_assumed_same_event():
    a = story("OpenAI releases new API", "https://one.example/a")
    b = story(a.title, "https://two.example/b", published_at=a.published_at - timedelta(days=20))
    assert len(group_articles([a, b])) == 2


def test_optional_embeddings_are_gated_and_fail_back_to_lexical():
    a = story("OpenAI releases interface for private workspaces", "https://one.example/a")
    b = story("OpenAI releases toolkit for team repositories", "https://two.example/b")
    assert len(group_articles([a, b])) == 2
    merged = group_articles([a, b], embedding_provider=lambda _: [1.0, 0.0, 0.0])
    assert len(merged) == 1 and "embedding_assisted" in merged[0].reasons

    def unavailable(_):
        raise TimeoutError("offline provider")

    assert len(group_articles([a, b], unavailable)) == 2
    assert match_articles(a, b, unavailable)[1] == "embedding_unavailable"
    c = story("Google releases toolkit for team repositories", "https://three.example/c")
    assert len(group_articles([a, c], embedding_provider=lambda _: [1.0, 0.0])) == 2


def test_selector_limits_events_not_articles_and_preserves_all_evidence(settings):
    a = story("OpenAI releases GPT-5 model", "https://one.example/a", importance_score=0.9)
    b = story("OpenAI发布GPT-5模型", "https://two.example/b", importance_score=0.8)
    c = story("NVIDIA reports supply shortage", "https://three.example/c", importance_score=0.7)
    window = news_window(settings, DAY, now=NOW)
    selected = select_news(FakeDB([a, b, c]), window, 2)
    assert selected.articles == [a, c]
    assert len(selected.events[0].members) == 2
    sections = build_sections(selected.articles, window, selected.events)
    news_items = [
        i
        for s in sections
        if s["key"] not in {"startup-opportunity", "investment-lens"}
        for i in s["items"]
    ]
    assert len(news_items) == 2
    item = news_items[0]
    assert item["source_count"] == 2 and item["document_count"] == 2
    assert all(e["verification_status"] == "not_verified" for e in item["event_sources"])
    markdown = render_markdown(DAY, sections)
    assert b.url in markdown
    assert [g.key for g in group_articles([a, b, c])] == [g.key for g in group_articles([c, b, a])]


def test_disagreeing_numbers_remain_visible_as_evidence_review():
    a = story("OpenAI releases API with 10 tools", "https://one.example/a")
    b = story("OpenAI releases API with 12 tools", "https://two.example/b")
    group = group_articles([a, b])[0]
    assert group.as_dict()["evidence_review_required"]

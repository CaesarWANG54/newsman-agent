from types import SimpleNamespace

from newsman.services.ranker import score_article


def test_score_article_rewards_primary_ai_sources() -> None:
    article = SimpleNamespace(
        title="OpenAI launches a new model API",
        text_content="The model improves agent workflows and safety tooling.",
        source=SimpleNamespace(credibility=0.98),
        published_at=None,
    )

    assert score_article(article) > 0.5

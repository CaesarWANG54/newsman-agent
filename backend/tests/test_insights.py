from types import SimpleNamespace

from newsman.services.insights import build_opportunity_sections


def make_article(title: str, text: str, score: float = 0.7):
    return SimpleNamespace(
        id=title,
        title=title,
        summary=None,
        text_content=text,
        canonical_url=f"https://example.com/{title.replace(' ', '-').lower()}",
        importance_score=score,
        metadata_json={"source_name": "Example Source"},
        source=SimpleNamespace(credibility=0.9),
    )


def test_build_opportunity_sections_adds_startup_and_investment_lenses() -> None:
    articles = [
        make_article(
            "OpenAI expands enterprise agent workflow automation",
            "Agent automation and developer productivity tools are moving into enterprise operations.",
        ),
        make_article(
            "Cloud provider expands GPU data center infrastructure",
            "GPU, cloud, data center and inference demand continue to shape AI infrastructure.",
        ),
    ]

    sections = build_opportunity_sections(articles)

    assert [section["key"] for section in sections] == ["startup-opportunity", "investment-lens"]
    # Keyword matches alone no longer justify a recommendation or a stock list.
    assert all(section["items"][0]["insight"]["status"] == "no_conclusion" for section in sections)
    assert not sections[0]["items"][0]["evidence"]
    assert all(not section["items"][0]["url"] for section in sections)

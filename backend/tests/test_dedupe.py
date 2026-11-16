from newsman.services.dedupe import are_similar_titles, normalize_title, title_fingerprint


def test_normalize_title_removes_noise_words() -> None:
    assert normalize_title("OpenAI announces new AI model for agents") == "openai model agents"


def test_title_similarity_groups_related_headlines() -> None:
    assert are_similar_titles(
        "OpenAI releases a new API for agents", "OpenAI releases API for agent apps"
    )


def test_fingerprint_is_stable() -> None:
    assert title_fingerprint("Google launches Gemini model") == title_fingerprint(
        "Google launches Gemini model"
    )


def test_unrelated_english_events_and_empty_titles_do_not_match():
    assert not are_similar_titles("OpenAI releases Codex", "NVIDIA reports chip supply shortage")
    assert not are_similar_titles("", "")


def test_chinese_unrelated_titles_must_not_share_fingerprint():
    assert title_fingerprint("模型正式开源") != title_fingerprint("芯片供应短缺")


def test_same_chinese_event_matches():
    assert are_similar_titles("模型正式开源", "模型正式开源")

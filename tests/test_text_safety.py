from app.core.text_safety import sanitize_llm_text


def test_sanitizer_removes_bracket_citations() -> None:
    text = "Вот ответ.[1][2]"
    sanitized, meta = sanitize_llm_text(text)

    assert "[1]" not in sanitized
    assert "[2]" not in sanitized
    assert "Вот ответ." in sanitized
    assert meta["failed"] is False


def test_sanitizer_blocks_sources_section() -> None:
    text = "Основной ответ.\n\nИсточники:\n1) https://example.com"
    sanitized, meta = sanitize_llm_text(text)

    assert "Источники" not in sanitized
    assert "https://example.com" not in sanitized
    assert sanitized == "Основной ответ."
    assert meta["failed"] is False


def test_no_sources_markers() -> None:
    text = "Ответ со ссылкой https://example.com [1] doi:10.1234/abc arxiv pubmed"
    sanitized, meta = sanitize_llm_text(text)

    assert "http" not in sanitized
    assert "[1]" not in sanitized
    assert "doi" not in sanitized.lower()
    assert "arxiv" not in sanitized.lower()
    assert "pubmed" not in sanitized.lower()
    assert meta["failed"] is False

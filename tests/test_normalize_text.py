from audiobookify.normalize.text import chunk_text, normalize_text, split_sentences


def test_expand_abbreviations():
    assert normalize_text("Dr. Smith arrived.") == "Doctor Smith arrived."


def test_expand_numbers():
    assert normalize_text("I have 42 apples.") == "I have forty-two apples."


def test_split_sentences():
    result = split_sentences("First sentence. Second sentence! Third one?")
    assert result == ["First sentence.", "Second sentence!", "Third one?"]


def test_chunk_text_respects_max_chars():
    text = " ".join(f"Sentence number {i}." for i in range(20))
    chunks = chunk_text(text, max_chars=50)
    assert all(len(c) <= 50 for c in chunks)
    assert len(chunks) > 1


def test_chunk_text_does_not_split_a_single_short_paragraph():
    chunks = chunk_text("Just one short sentence.", max_chars=300)
    assert chunks == ["Just one short sentence."]


def test_expand_numbers_leaves_hyphenated_identifiers_alone():
    """Ensure numbers adjacent to hyphens in identifiers are not expanded."""
    assert normalize_text("She had COVID-19 last year.") == "She had COVID-19 last year."


def test_expand_numbers_still_expands_plain_numbers_near_hyphens():
    """Confirm number expansion still works after hyphen fix."""
    assert normalize_text("Room 7 - the attic.") == "Room seven - the attic."

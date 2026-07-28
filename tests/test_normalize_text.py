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


def test_expand_numbers_expands_hyphenated_number_modifiers():
    """Ensure hyphenated number modifiers like '5-year-old' are expanded correctly."""
    assert normalize_text("She met a 5-year-old girl.") == "She met a five-year-old girl."
    assert normalize_text("A 10-minute break.") == "A ten-minute break."
    assert normalize_text("A 3-day trip.") == "A three-day trip."


def test_expand_numbers_leaves_numeric_ranges_intact():
    """A digit-hyphen-digit range is atomic: neither side may be half-expanded."""
    assert normalize_text("See pages 12-34.") == "See pages 12-34."
    assert normalize_text("They won 3-2.") == "They won 3-2."
    assert normalize_text("Chapters 1-3 cover it.") == "Chapters 1-3 cover it."
    # a range next to an ordinary number: only the ordinary number expands
    assert normalize_text("Read 12-34 then take 5 minutes.") == "Read 12-34 then take five minutes."


def test_chunk_text_hard_splits_a_long_unpunctuated_run():
    """A single 'sentence' longer than max_chars must be split on word boundaries."""
    text = " ".join(["word"] * 200)  # 999 chars, no sentence-ending punctuation at all
    chunks = chunk_text(text, max_chars=100)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    assert " ".join(chunks) == text  # nothing lost, nothing split mid-word


def test_chunk_text_splits_a_sentence_that_starts_with_a_number():
    """Number expansion must not destroy the sentence boundary before it."""
    chunks = chunk_text("Six went home. 7 more came.", max_chars=20)
    assert chunks == ["Six went home.", "seven more came."]

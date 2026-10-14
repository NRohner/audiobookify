import random

from audiobookify.normalize.text import Chunk, chunk_text, normalize_text, split_paragraphs, split_sentences


def _texts(chunks):
    return [c.text for c in chunks]


def test_expand_abbreviations():
    assert normalize_text("Dr. Smith arrived.") == "Doctor Smith arrived."


def test_expand_numbers():
    assert normalize_text("I have 42 apples.") == "I have forty-two apples."


def test_split_sentences():
    result = split_sentences("First sentence. Second sentence! Third one?")
    assert result == ["First sentence.", "Second sentence!", "Third one?"]


def test_chunk_text_respects_max_chars():
    text = " ".join(f"Sentence number {i}." for i in range(20))
    chunks = _texts(chunk_text(text, max_chars=50))
    assert all(len(c) <= 50 for c in chunks)
    assert len(chunks) > 1


def test_chunk_text_does_not_split_a_single_short_paragraph():
    chunks = chunk_text("Just one short sentence.", max_chars=300)
    assert chunks == [Chunk("Just one short sentence.", "paragraph")]


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


def test_expand_numbers_leaves_numeric_ranges_intact_when_followed_by_a_word_char():
    """A range whose right operand is glued to a trailing word char ("12-34b") can't
    close its \\b, so _NUMBER_RE falls back to matching only the left operand. That
    left operand must not be half-expanded either."""
    assert normalize_text("See pages 12-34b for details.") == "See pages 12-34b for details."
    assert normalize_text("The 1980-90s recession.") == "The 1980-90s recession."
    assert normalize_text("Table 3-2a shows it.") == "Table 3-2a shows it."


def test_chunk_text_hard_splits_a_long_unpunctuated_run():
    """A single 'sentence' longer than max_chars must be split on word boundaries."""
    text = " ".join(["word"] * 200)  # 999 chars, no sentence-ending punctuation at all
    chunks = _texts(chunk_text(text, max_chars=100))
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    assert " ".join(chunks) == text  # nothing lost, nothing split mid-word


def test_chunk_text_splits_a_sentence_that_starts_with_a_number():
    """Number expansion must not destroy the sentence boundary before it."""
    chunks = _texts(chunk_text("Six went home. 7 more came.", max_chars=20))
    assert chunks == ["Six went home.", "seven more came."]


# --- sentence boundaries next to quotes and brackets ---

def test_split_sentences_after_a_closing_quote():
    assert split_sentences('"I\'m leaving." She turned away.') == ['"I\'m leaving."', "She turned away."]


def test_split_sentences_after_closing_curly_quote_and_bracket():
    assert split_sentences("“Go now!” He ran. (It was late.) Then dawn came.") == [
        "“Go now!”", "He ran.", "(It was late.)", "Then dawn came."]


def test_split_sentences_before_an_opening_quote():
    assert split_sentences('He waited. "Hello?" No answer.') == ["He waited.", '"Hello?"', "No answer."]


def test_split_sentences_keeps_dialogue_tag_attached():
    assert split_sentences("'Why?' he asked. She shrugged.") == ["'Why?' he asked.", "She shrugged."]


# --- paragraphs ---

def test_split_paragraphs_on_blank_lines_and_collapses_inner_whitespace():
    assert split_paragraphs("One\nstill one.\n\n  Two.  \n \n\nThree.") == ["One still one.", "Two.", "Three."]


def test_text_without_blank_lines_is_one_paragraph():
    assert split_paragraphs("Line one.\nLine two.") == ["Line one. Line two."]


def test_a_paragraph_always_ends_a_chunk():
    chunks = chunk_text("First paragraph here.\n\nSecond paragraph here.", max_chars=300)
    assert chunks == [Chunk("First paragraph here.", "paragraph"), Chunk("Second paragraph here.", "paragraph")]


def test_sentences_packed_inside_a_paragraph_end_with_sentence():
    chunks = chunk_text("Aaaa aaaa aaaa. Bbbb bbbb bbbb. Cccc cccc cccc.", max_chars=32)
    assert chunks == [Chunk("Aaaa aaaa aaaa. Bbbb bbbb bbbb.", "sentence"), Chunk("Cccc cccc cccc.", "paragraph")]


# --- long sentences split at clauses before words ---

def test_long_sentence_splits_at_semicolon_before_comma():
    text = "He walked to the old mill, slowly; the river ran past it, cold and grey."
    chunks = chunk_text(text, max_chars=50)
    assert chunks == [Chunk("He walked to the old mill, slowly;", "clause"),
                      Chunk("the river ran past it, cold and grey.", "paragraph")]


def test_long_sentence_splits_at_comma_when_no_stronger_punctuation():
    text = "He walked to the old mill by the river, and the water ran past it cold and grey."
    chunks = chunk_text(text, max_chars=50)
    assert chunks == [Chunk("He walked to the old mill by the river,", "clause"),
                      Chunk("and the water ran past it cold and grey.", "paragraph")]


def test_clause_split_never_leaves_a_piece_under_the_minimum():
    # the only semicolon would leave "Yes;" alone; it must not be used
    text = "Yes; he walked to the old mill by the river and the water ran past it cold and grey all day."
    chunks = chunk_text(text, max_chars=60, min_chars=20)
    assert all(len(c.text) >= 20 for c in chunks)
    assert " ".join(_texts(chunks)) == text


# --- minimum chunk length ---

def test_one_word_paragraph_merges_forward():
    chunks = chunk_text('"No."\n\nShe looked at him for a long moment before answering.', max_chars=300, min_chars=20)
    assert chunks == [Chunk('"No." She looked at him for a long moment before answering.', "paragraph")]


def test_short_final_paragraph_merges_backward():
    chunks = chunk_text("She looked at him for a long moment.\n\nNo.", max_chars=300, min_chars=20)
    assert chunks == [Chunk("She looked at him for a long moment. No.", "paragraph")]


def test_merged_heading_gets_a_period_so_the_model_pauses():
    chunks = chunk_text("Chapter One\n\nIt was a dark and stormy night.", max_chars=300, min_chars=20)
    assert _texts(chunks) == ["Chapter One. It was a dark and stormy night."]


def test_consecutive_short_paragraphs_accumulate_until_long_enough():
    chunks = chunk_text("No.\n\nYes.\n\nMaybe.\n\nThat settles it then, I suppose.", max_chars=300, min_chars=15)
    assert _texts(chunks) == ["No. Yes. Maybe.", "That settles it then, I suppose."]


def test_input_shorter_than_minimum_is_still_one_chunk():
    assert _texts(chunk_text("Hi.", max_chars=300, min_chars=80)) == ["Hi."]


def test_chunk_lengths_stay_within_bounds_on_varied_text():
    rng = random.Random(1234)
    words = ["the", "a", "river", "old", "mill", "she", "said", "quietly", "darkness", "No", "Yes"]
    punct = [".", ".", "!", "?", ",", ";", ""]
    for _ in range(200):
        paragraphs = []
        for _ in range(rng.randint(1, 8)):
            sentences = []
            for _ in range(rng.randint(1, 6)):
                n = rng.randint(1, 40)
                body = " ".join(rng.choice(words) + rng.choice(punct) for _ in range(n))
                sentences.append(body.rstrip(".,;!?") + ".")
            paragraphs.append(" ".join(sentences))
        text = "\n\n".join(paragraphs)
        chunks = chunk_text(text, max_chars=120, min_chars=30)
        assert chunks, text
        if len(chunks) > 1:
            assert all(len(c.text) >= 30 for c in chunks), text
        # ponytail: a short last paragraph merging backward can push the final chunk past max+min
        assert all(len(c.text) <= 120 + 30 for c in chunks[:-1]), text
        assert len(chunks[-1].text) <= 120 + 2 * 30, text


# --- scene breaks ---

def test_scene_break_is_dropped_and_marks_the_previous_chunk():
    text = "The door closed behind him for good.\n\n* * *\n\nMorning came slowly over the hills."
    chunks = chunk_text(text, max_chars=300, min_chars=10)
    assert chunks == [Chunk("The door closed behind him for good.", "scene"),
                      Chunk("Morning came slowly over the hills.", "paragraph")]


def test_short_chunk_before_a_scene_break_merges_backward_not_across_the_break():
    text = "The door closed behind him for good.\n\nThe end.\n\n#\n\nMorning came slowly over the hills."
    chunks = chunk_text(text, max_chars=300, min_chars=20)
    assert chunks == [Chunk("The door closed behind him for good. The end.", "scene"),
                      Chunk("Morning came slowly over the hills.", "paragraph")]


def test_text_that_is_only_a_scene_break_produces_no_chunks():
    assert chunk_text("* * *", max_chars=300) == []


def test_a_whole_scene_under_the_minimum_merges_across_the_break_rather_than_stand_alone():
    # a tiny chunk sounds worse than a lost scene pause; only happens when the entire scene is short
    text = "The door closed behind him for good.\n\n* * *\n\nThe End."
    chunks = chunk_text(text, max_chars=300, min_chars=20)
    assert chunks == [Chunk("The door closed behind him for good. The End.", "paragraph")]

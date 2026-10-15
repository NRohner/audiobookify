//! Port of tests/test_normalize_text.py.

use audiobookify_core::text::{normalize_text, split_paragraphs, split_sentences};

#[test]
fn expand_abbreviations() {
    assert_eq!(normalize_text("Dr. Smith arrived."), "Doctor Smith arrived.");
}

#[test]
fn expand_numbers() {
    assert_eq!(normalize_text("I have 42 apples."), "I have forty-two apples.");
}

#[test]
fn split_sentences_basic() {
    assert_eq!(split_sentences("First sentence. Second sentence! Third one?"), ["First sentence.", "Second sentence!", "Third one?"]);
}

#[test]
fn expand_numbers_leaves_hyphenated_identifiers_alone() {
    assert_eq!(normalize_text("She had COVID-19 last year."), "She had COVID-19 last year.");
}

#[test]
fn expand_numbers_still_expands_plain_numbers_near_hyphens() {
    assert_eq!(normalize_text("Room 7 - the attic."), "Room seven - the attic.");
}

#[test]
fn expand_numbers_expands_hyphenated_number_modifiers() {
    assert_eq!(normalize_text("She met a 5-year-old girl."), "She met a five-year-old girl.");
    assert_eq!(normalize_text("A 10-minute break."), "A ten-minute break.");
    assert_eq!(normalize_text("A 3-day trip."), "A three-day trip.");
}

#[test]
fn expand_numbers_leaves_numeric_ranges_intact() {
    assert_eq!(normalize_text("See pages 12-34."), "See pages 12-34.");
    assert_eq!(normalize_text("They won 3-2."), "They won 3-2.");
    assert_eq!(normalize_text("Chapters 1-3 cover it."), "Chapters 1-3 cover it.");
    assert_eq!(normalize_text("Read 12-34 then take 5 minutes."), "Read 12-34 then take five minutes.");
}

#[test]
fn expand_numbers_leaves_numeric_ranges_intact_when_followed_by_a_word_char() {
    assert_eq!(normalize_text("See pages 12-34b for details."), "See pages 12-34b for details.");
    assert_eq!(normalize_text("The 1980-90s recession."), "The 1980-90s recession.");
    assert_eq!(normalize_text("Table 3-2a shows it."), "Table 3-2a shows it.");
}

#[test]
fn expand_numbers_uses_num2words_phrasing() {
    assert_eq!(normalize_text("In 1984, 105 of 2,500,000 paid 27.50."),
        "In one thousand, nine hundred and eighty-four, one hundred and five of two million, five hundred thousand paid twenty-seven point five.");
}

#[test]
fn expand_numbers_uses_pythons_word_boundaries() {
    // Python's \w includes "½", so "4½" has no boundary after the 4 (Gutenberg #1661).
    assert_eq!(normalize_text("paying 4½ per cent"), "paying 4½ per cent");
}

#[test]
fn split_sentences_after_a_closing_quote() {
    assert_eq!(split_sentences("\"I'm leaving.\" She turned away."), ["\"I'm leaving.\"", "She turned away."]);
}

#[test]
fn split_sentences_after_closing_curly_quote_and_bracket() {
    assert_eq!(split_sentences("“Go now!” He ran. (It was late.) Then dawn came."), ["“Go now!”", "He ran.", "(It was late.)", "Then dawn came."]);
}

#[test]
fn split_sentences_before_an_opening_quote() {
    assert_eq!(split_sentences("He waited. \"Hello?\" No answer."), ["He waited.", "\"Hello?\"", "No answer."]);
}

#[test]
fn split_sentences_keeps_dialogue_tag_attached() {
    assert_eq!(split_sentences("'Why?' he asked. She shrugged."), ["'Why?' he asked.", "She shrugged."]);
}

#[test]
fn split_paragraphs_on_blank_lines_and_collapses_inner_whitespace() {
    assert_eq!(split_paragraphs("One\nstill one.\n\n  Two.  \n \n\nThree."), ["One still one.", "Two.", "Three."]);
}

#[test]
fn text_without_blank_lines_is_one_paragraph() {
    assert_eq!(split_paragraphs("Line one.\nLine two."), ["Line one. Line two."]);
}

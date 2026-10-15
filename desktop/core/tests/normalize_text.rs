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

use audiobookify_core::text::{chunk_text, Chunk, EndsWith};
use EndsWith::{Clause, Paragraph, Scene, Sentence};

fn texts(chunks: &[Chunk]) -> Vec<&str> {
    chunks.iter().map(|c| c.text.as_str()).collect()
}

fn chunk(text: &str, ends_with: EndsWith) -> Chunk {
    Chunk::new(text, ends_with)
}

#[test]
fn chunk_text_respects_max_chars() {
    let text = (0..20).map(|i| format!("Sentence number {i}.")).collect::<Vec<_>>().join(" ");
    let chunks = chunk_text(&text, 50, 0);
    assert!(chunks.iter().all(|c| c.text.chars().count() <= 50));
    assert!(chunks.len() > 1);
}

#[test]
fn chunk_text_does_not_split_a_single_short_paragraph() {
    assert_eq!(chunk_text("Just one short sentence.", 300, 0), [chunk("Just one short sentence.", Paragraph)]);
}

#[test]
fn chunk_text_hard_splits_a_long_unpunctuated_run() {
    let text = vec!["word"; 200].join(" ");
    let chunks = chunk_text(&text, 100, 0);
    assert!(chunks.len() > 1);
    assert!(chunks.iter().all(|c| c.text.chars().count() <= 100));
    assert_eq!(texts(&chunks).join(" "), text);
}

#[test]
fn chunk_text_splits_a_sentence_that_starts_with_a_number() {
    assert_eq!(texts(&chunk_text("Six went home. 7 more came.", 20, 0)), ["Six went home.", "seven more came."]);
}

#[test]
fn a_paragraph_always_ends_a_chunk() {
    assert_eq!(chunk_text("First paragraph here.\n\nSecond paragraph here.", 300, 0),
        [chunk("First paragraph here.", Paragraph), chunk("Second paragraph here.", Paragraph)]);
}

#[test]
fn sentences_packed_inside_a_paragraph_end_with_sentence() {
    assert_eq!(chunk_text("Aaaa aaaa aaaa. Bbbb bbbb bbbb. Cccc cccc cccc.", 32, 0),
        [chunk("Aaaa aaaa aaaa. Bbbb bbbb bbbb.", Sentence), chunk("Cccc cccc cccc.", Paragraph)]);
}

#[test]
fn long_sentence_splits_at_semicolon_before_comma() {
    let text = "He walked to the old mill, slowly; the river ran past it, cold and grey.";
    assert_eq!(chunk_text(text, 50, 0),
        [chunk("He walked to the old mill, slowly;", Clause), chunk("the river ran past it, cold and grey.", Paragraph)]);
}

#[test]
fn long_sentence_splits_at_comma_when_no_stronger_punctuation() {
    let text = "He walked to the old mill by the river, and the water ran past it cold and grey.";
    assert_eq!(chunk_text(text, 50, 0),
        [chunk("He walked to the old mill by the river,", Clause), chunk("and the water ran past it cold and grey.", Paragraph)]);
}

#[test]
fn clause_split_never_leaves_a_piece_under_the_minimum() {
    let text = "Yes; he walked to the old mill by the river and the water ran past it cold and grey all day.";
    let chunks = chunk_text(text, 60, 20);
    assert!(chunks.iter().all(|c| c.text.chars().count() >= 20));
    assert_eq!(texts(&chunks).join(" "), text);
}

#[test]
fn one_word_paragraph_merges_forward() {
    assert_eq!(chunk_text("\"No.\"\n\nShe looked at him for a long moment before answering.", 300, 20),
        [chunk("\"No.\" She looked at him for a long moment before answering.", Paragraph)]);
}

#[test]
fn short_final_paragraph_merges_backward() {
    assert_eq!(chunk_text("She looked at him for a long moment.\n\nNo.", 300, 20),
        [chunk("She looked at him for a long moment. No.", Paragraph)]);
}

#[test]
fn merged_heading_gets_a_period_so_the_model_pauses() {
    assert_eq!(texts(&chunk_text("Chapter One\n\nIt was a dark and stormy night.", 300, 20)), ["Chapter One. It was a dark and stormy night."]);
}

#[test]
fn consecutive_short_paragraphs_accumulate_until_long_enough() {
    assert_eq!(texts(&chunk_text("No.\n\nYes.\n\nMaybe.\n\nThat settles it then, I suppose.", 300, 15)),
        ["No. Yes. Maybe.", "That settles it then, I suppose."]);
}

#[test]
fn input_shorter_than_minimum_is_still_one_chunk() {
    assert_eq!(texts(&chunk_text("Hi.", 300, 80)), ["Hi."]);
}

/// The Python test draws from random.Random(1234); Rust can't replay that stream, so
/// this keeps the property (bounds) with its own generator, and cases.json holds the
/// Python stream's exact outputs for parity.
#[test]
fn chunk_lengths_stay_within_bounds_on_varied_text() {
    let mut seed: u64 = 1234;
    let mut next = |n: usize| {
        seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        (seed >> 33) as usize % n
    };
    let words = ["the", "a", "river", "old", "mill", "she", "said", "quietly", "darkness", "No", "Yes"];
    let punct = [".", ".", "!", "?", ",", ";", ""];
    for _ in 0..200 {
        let mut paragraphs = Vec::new();
        for _ in 0..1 + next(8) {
            let mut sentences = Vec::new();
            for _ in 0..1 + next(6) {
                let body: Vec<String> = (0..1 + next(40)).map(|_| format!("{}{}", words[next(words.len())], punct[next(punct.len())])).collect();
                sentences.push(format!("{}.", body.join(" ").trim_end_matches(['.', ',', ';', '!', '?'])));
            }
            paragraphs.push(sentences.join(" "));
        }
        let text = paragraphs.join("\n\n");
        let chunks = chunk_text(&text, 120, 30);
        assert!(!chunks.is_empty(), "{text}");
        let len = |c: &Chunk| c.text.chars().count();
        if chunks.len() > 1 {
            assert!(chunks.iter().all(|c| len(c) >= 30), "{text}");
        }
        // ponytail: a short last paragraph merging backward can push the final chunk past max+min
        assert!(chunks[..chunks.len() - 1].iter().all(|c| len(c) <= 120 + 30), "{text}");
        assert!(len(chunks.last().unwrap()) <= 120 + 2 * 30, "{text}");
    }
}

#[test]
fn scene_break_is_dropped_and_marks_the_previous_chunk() {
    let text = "The door closed behind him for good.\n\n* * *\n\nMorning came slowly over the hills.";
    assert_eq!(chunk_text(text, 300, 10),
        [chunk("The door closed behind him for good.", Scene), chunk("Morning came slowly over the hills.", Paragraph)]);
}

#[test]
fn short_chunk_before_a_scene_break_merges_backward_not_across_the_break() {
    let text = "The door closed behind him for good.\n\nThe end.\n\n#\n\nMorning came slowly over the hills.";
    assert_eq!(chunk_text(text, 300, 20),
        [chunk("The door closed behind him for good. The end.", Scene), chunk("Morning came slowly over the hills.", Paragraph)]);
}

#[test]
fn text_that_is_only_a_scene_break_produces_no_chunks() {
    assert_eq!(chunk_text("* * *", 300, 0), []);
}

#[test]
fn a_whole_scene_under_the_minimum_merges_across_the_break_rather_than_stand_alone() {
    let text = "The door closed behind him for good.\n\n* * *\n\nThe End.";
    assert_eq!(chunk_text(text, 300, 20), [chunk("The door closed behind him for good. The End.", Paragraph)]);
}

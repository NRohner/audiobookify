//! Port of tests/test_lexicon_substitution.py.

use audiobookify_core::text::apply_lexicon;

#[test]
fn apply_lexicon_substitutes_whole_word_case_insensitive() {
    assert_eq!(apply_lexicon("Cadeyrn walked into the hall.", [("cadeyrn", "KAH-dern")]), "KAH-dern walked into the hall.");
}

#[test]
fn apply_lexicon_does_not_substitute_partial_word_matches() {
    assert_eq!(apply_lexicon("Anne-Marie waved at Ann.", [("Ann", "ANN-override")]), "Anne-Marie waved at ANN-override.");
}

#[test]
fn apply_lexicon_supports_multi_word_terms() {
    assert_eq!(apply_lexicon("She flew to New York yesterday.", [("New York", "Noo Yawk")]), "She flew to Noo Yawk yesterday.");
}

#[test]
fn apply_lexicon_leaves_unmatched_text_unchanged() {
    assert_eq!(apply_lexicon("Nothing to substitute here.", [("Cadeyrn", "KAH-dern")]), "Nothing to substitute here.");
}

#[test]
fn apply_lexicon_processes_entries_in_given_order_first_match_wins() {
    let entries = [("Cadeyrn", "book-specific-version"), ("Cadeyrn", "global-version")];
    assert_eq!(apply_lexicon("Cadeyrn spoke.", entries), "book-specific-version spoke.");
}

#[test]
fn apply_lexicon_treats_override_value_as_a_literal_replacement() {
    assert_eq!(apply_lexicon("Cadeyrn spoke.", [("Cadeyrn", "KAH\\dern")]), "KAH\\dern spoke.");
    assert_eq!(apply_lexicon("Cadeyrn spoke.", [("Cadeyrn", "$1 ${0}")]), "$1 ${0} spoke.");
}

#[test]
fn apply_lexicon_prefers_longer_overlapping_term_regardless_of_order() {
    let text = "She flew to New York yesterday.";
    assert_eq!(apply_lexicon(text, [("York", "Yorrk"), ("New York", "Noo Yawk")]), "She flew to Noo Yawk yesterday.");
    assert_eq!(apply_lexicon(text, [("New York", "Noo Yawk"), ("York", "Yorrk")]), "She flew to Noo Yawk yesterday.");
}

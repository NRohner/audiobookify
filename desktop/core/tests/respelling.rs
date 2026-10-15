//! Port of tests/test_respelling.py.

use audiobookify_core::text::phonemes_to_respelling;

#[test]
fn phonemes_to_respelling_maps_known_ipa_tokens() {
    assert_eq!(phonemes_to_respelling("k ə d eɪ ɹ n"), "kuhdayrn");
}

#[test]
fn phonemes_to_respelling_passes_through_unknown_tokens_unchanged() {
    assert_eq!(phonemes_to_respelling("k ə ? n"), "kuh?n");
}

#[test]
fn phonemes_to_respelling_handles_empty_string() {
    assert_eq!(phonemes_to_respelling(""), "");
}

#[test]
fn phonemes_to_respelling_handles_diphthongs_and_consonant_clusters() {
    assert_eq!(phonemes_to_respelling("h ə l oʊ"), "huhloh");
    assert_eq!(phonemes_to_respelling("tʃ ɪ p"), "chihp");
    assert_eq!(phonemes_to_respelling("dʒ ʌ m p"), "juhmp");
}

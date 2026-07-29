from dataclasses import dataclass

from audiobookify.lexicon.substitution import apply_lexicon


@dataclass
class _Entry:
    term: str
    override_value: str


def test_apply_lexicon_substitutes_whole_word_case_insensitive():
    entries = [_Entry(term="cadeyrn", override_value="KAH-dern")]

    result = apply_lexicon("Cadeyrn walked into the hall.", entries)

    assert result == "KAH-dern walked into the hall."


def test_apply_lexicon_does_not_substitute_partial_word_matches():
    entries = [_Entry(term="Ann", override_value="ANN-override")]

    result = apply_lexicon("Anne-Marie waved at Ann.", entries)

    assert result == "Anne-Marie waved at ANN-override."


def test_apply_lexicon_supports_multi_word_terms():
    entries = [_Entry(term="New York", override_value="Noo Yawk")]

    result = apply_lexicon("She flew to New York yesterday.", entries)

    assert result == "She flew to Noo Yawk yesterday."


def test_apply_lexicon_leaves_unmatched_text_unchanged():
    entries = [_Entry(term="Cadeyrn", override_value="KAH-dern")]

    result = apply_lexicon("Nothing to substitute here.", entries)

    assert result == "Nothing to substitute here."


def test_apply_lexicon_processes_entries_in_given_order_first_match_wins():
    # Simulates the "book-scoped before global" precedence rule from the plan's
    # Global Constraints: once the first entry substitutes a term, the text no
    # longer contains the original word, so a later entry for the same term
    # (case-insensitively) is a no-op rather than a second substitution.
    entries = [
        _Entry(term="Cadeyrn", override_value="book-specific-version"),
        _Entry(term="Cadeyrn", override_value="global-version"),
    ]

    result = apply_lexicon("Cadeyrn spoke.", entries)

    assert result == "book-specific-version spoke."

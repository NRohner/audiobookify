from audiobookify.phoneme.respelling import phonemes_to_respelling


def test_phonemes_to_respelling_maps_known_ipa_tokens():
    assert phonemes_to_respelling("k ə d eɪ ɹ n") == "kuhdayrn"


def test_phonemes_to_respelling_passes_through_unknown_tokens_unchanged():
    assert phonemes_to_respelling("k ə ? n") == "kuh?n"


def test_phonemes_to_respelling_handles_empty_string():
    assert phonemes_to_respelling("") == ""


def test_phonemes_to_respelling_handles_diphthongs_and_consonant_clusters():
    # "hello" -> h ə l oʊ
    assert phonemes_to_respelling("h ə l oʊ") == "huhloh"
    # affricates
    assert phonemes_to_respelling("tʃ ɪ p") == "chihp"
    assert phonemes_to_respelling("dʒ ʌ m p") == "juhmp"

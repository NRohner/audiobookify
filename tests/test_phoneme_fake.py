from audiobookify.phoneme.fake import FakePhonemeExtractor


def test_fake_phoneme_extractor_full_lifecycle():
    extractor = FakePhonemeExtractor()
    extractor.load("cpu")

    assert extractor.extract("/any/path.wav") == "k ə d eɪ ɹ n"

    extractor.teardown()  # must not raise


def test_fake_phoneme_extractor_accepts_custom_canned_phonemes():
    extractor = FakePhonemeExtractor(canned_phonemes="h ə l oʊ")
    extractor.load("cpu")

    assert extractor.extract("/any/path.wav") == "h ə l oʊ"

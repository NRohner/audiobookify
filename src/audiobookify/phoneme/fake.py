class FakePhonemeExtractor:
    def __init__(self, canned_phonemes: str = "k ə d eɪ ɹ n"):
        self.canned_phonemes = canned_phonemes
        self.device = None

    def load(self, device: str) -> None:
        self.device = device

    def extract(self, audio_path: str) -> str:
        return self.canned_phonemes

    def teardown(self) -> None:
        pass

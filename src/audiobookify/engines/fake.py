import numpy as np

from .base import VoiceProfileHandle


class FakeEngine:
    name = "fake"
    supports_cloning = True
    needs_transcript = False

    def __init__(self):
        self._sr = 24000
        self.device = None

    def load(self, device: str = "auto") -> None:
        self.device = device

    def clone_profile(self, reference_audio, reference_text, params) -> VoiceProfileHandle:
        return VoiceProfileHandle(data={"reference_audio": reference_audio})

    def synthesize(self, text_chunk: str, profile_handle, params) -> np.ndarray:
        duration_s = max(0.1, len(text_chunk) * 0.05)
        return np.zeros(int(self._sr * duration_s), dtype=np.float32)

    def max_chunk_chars(self) -> int:
        return 300

    def min_chunk_chars(self) -> int:
        return 20

    def preferred_phoneme_scheme(self):
        return None

    def sample_rate(self) -> int:
        return self._sr

    def teardown(self) -> None:
        pass

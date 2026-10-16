import json
import os
import time

import numpy as np

from .base import VoiceProfileHandle


class FakeEngine:
    """Silence, no weights. The AUDIOBOOKIFY_FAKE_* env vars are test knobs for the
    Rust runner tests, which drive this engine through a real engine_worker child."""

    name = "fake"
    supports_cloning = True
    needs_transcript = False

    def __init__(self):
        self._sr = 24000
        self.device = None
        if os.environ.get("AUDIOBOOKIFY_FAKE_NEEDS_TRANSCRIPT") == "1":
            self.needs_transcript = True

    def load(self, device: str = "auto") -> None:
        self.device = device

    def clone_profile(self, reference_audio, reference_text, params) -> VoiceProfileHandle:
        return VoiceProfileHandle(data={"reference_audio": reference_audio})

    def synthesize(self, text_chunk: str, profile_handle, params) -> np.ndarray:
        log = os.environ.get("AUDIOBOOKIFY_FAKE_LOG")
        if log:
            with open(log, "a") as f:
                f.write(json.dumps(text_chunk) + "\n")
        time.sleep(float(os.environ.get("AUDIOBOOKIFY_FAKE_DELAY", "0")))
        crash_on = os.environ.get("AUDIOBOOKIFY_FAKE_CRASH_ON")
        if crash_on and crash_on in text_chunk.lower():
            os._exit(3)
        fail_on = os.environ.get("AUDIOBOOKIFY_FAKE_FAIL_ON")
        if fail_on and fail_on in text_chunk.lower():
            raise RuntimeError("synthesis blew up")
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

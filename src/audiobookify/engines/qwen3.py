from typing import Optional

import numpy as np

from .base import VoiceProfileHandle
from .mlx_util import free_memory, join_segments, load_model as _load_model

REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"


class Qwen3Engine:
    """Qwen3-TTS 1.7B Base on MLX: in-context cloning from reference audio plus
    its transcript. It copies the reference's pace. Sampling defaults are the
    model's own; its `speed` argument is not implemented by the port."""

    name = "qwen3"
    supports_cloning = True

    def __init__(self):
        self.model = None

    def load(self, device: str = "auto") -> None:
        # device is unused: MLX always runs on the Apple GPU.
        self.model = _load_model(REPO)

    def clone_profile(self, reference_audio: Optional[str], reference_text: Optional[str],
                       params: dict) -> VoiceProfileHandle:
        if not reference_audio or not (reference_text or "").strip():
            raise ValueError("Qwen3 needs a voice with reference audio and its transcript")
        return VoiceProfileHandle(data={"ref_audio": reference_audio, "ref_text": reference_text})

    def synthesize(self, text_chunk: str, profile_handle: Optional[VoiceProfileHandle],
                    params: dict) -> np.ndarray:
        # The port reloads and re-encodes ref_audio on every call.
        return join_segments(self.model.generate(
            text_chunk, ref_audio=profile_handle.data["ref_audio"],
            ref_text=profile_handle.data["ref_text"], lang_code="english",  # not "en"
        ))

    def max_chunk_chars(self) -> int:
        return 300

    def min_chunk_chars(self) -> int:
        return 80

    def preferred_phoneme_scheme(self):
        return None

    def sample_rate(self) -> int:
        return self.model.sample_rate

    def teardown(self) -> None:
        self.model = None
        free_memory()

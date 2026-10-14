from typing import Optional

import numpy as np

from .base import VoiceProfileHandle
from .mlx_util import free_memory, join_segments, load_model as _load_model

REPO = "mlx-community/chatterbox-turbo-fp16"


class ChatterboxTurboEngine:
    """Chatterbox Turbo (350M) on MLX, Apple Silicon only. Turbo ignores
    exaggeration/cfg; its own sampling defaults are used (temperature 0.8,
    top_p 0.95, top_k 1000, repetition_penalty 1.2, norm_loudness on)."""

    name = "chatterbox-turbo"
    supports_cloning = True
    needs_transcript = False

    def __init__(self):
        self.model = None
        self._default_conds = None

    def load(self, device: str = "auto") -> None:
        # device is unused: MLX always runs on the Apple GPU.
        self.model = _load_model(REPO)
        self._default_conds = self.model._conds  # built-in voice (conds.safetensors)

    def clone_profile(self, reference_audio: Optional[str], reference_text: Optional[str],
                       params: dict) -> VoiceProfileHandle:
        # Encode the reference once per profile. The port stores the result on
        # model._conds, so keep it in the handle and restore it per chunk.
        if reference_audio:
            self.model.prepare_conditionals(reference_audio)
            conds = self.model._conds
        else:
            conds = self._default_conds
        return VoiceProfileHandle(data={"conds": conds})

    def synthesize(self, text_chunk: str, profile_handle: Optional[VoiceProfileHandle],
                    params: dict) -> np.ndarray:
        self.model._conds = profile_handle.data["conds"] if profile_handle else self._default_conds
        return join_segments(self.model.generate(text_chunk))

    def max_chunk_chars(self) -> int:
        return 300

    def min_chunk_chars(self) -> int:
        return 80  # ~5 s of speech; shorter inputs come out garbled

    def preferred_phoneme_scheme(self):
        return None

    def sample_rate(self) -> int:
        return self.model.sample_rate

    def teardown(self) -> None:
        self.model = None
        self._default_conds = None
        free_memory()

from typing import Optional, Protocol

import numpy as np


class VoiceProfileHandle:
    def __init__(self, data: Optional[dict] = None):
        self.data = data or {}


class TTSEngine(Protocol):
    name: str
    supports_cloning: bool

    def load(self, device: str) -> None: ...
    def clone_profile(self, reference_audio: Optional[str], reference_text: Optional[str],
                       params: dict) -> VoiceProfileHandle: ...
    def synthesize(self, text_chunk: str, profile_handle: Optional[VoiceProfileHandle],
                    params: dict) -> np.ndarray: ...
    def max_chunk_chars(self) -> int: ...
    def preferred_phoneme_scheme(self) -> Optional[str]: ...
    def sample_rate(self) -> int: ...
    def teardown(self) -> None: ...


def detect_device() -> str:
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"

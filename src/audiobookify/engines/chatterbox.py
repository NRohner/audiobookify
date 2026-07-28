from typing import Optional

import numpy as np

from .base import VoiceProfileHandle, detect_device


class ChatterboxEngine:
    name = "chatterbox"
    supports_cloning = True

    def __init__(self):
        self.model = None
        self.device = None

    def load(self, device: str = "auto") -> None:
        from chatterbox.tts import ChatterboxTTS

        self.device = detect_device() if device == "auto" else device
        try:
            self.model = ChatterboxTTS.from_pretrained(device=self.device)
        except Exception:
            if self.device == "cpu":
                raise
            self.device = "cpu"
            self.model = ChatterboxTTS.from_pretrained(device="cpu")

    def clone_profile(self, reference_audio: Optional[str], reference_text: Optional[str],
                       params: dict) -> VoiceProfileHandle:
        return VoiceProfileHandle(data={"audio_prompt_path": reference_audio})

    def synthesize(self, text_chunk: str, profile_handle: Optional[VoiceProfileHandle],
                    params: dict) -> np.ndarray:
        audio_prompt_path = profile_handle.data.get("audio_prompt_path") if profile_handle else None
        wav = self.model.generate(
            text_chunk,
            audio_prompt_path=audio_prompt_path,
            exaggeration=params.get("exaggeration", 0.5),
            cfg_weight=params.get("cfg", 0.5),
        )
        return wav.squeeze().cpu().numpy()

    def max_chunk_chars(self) -> int:
        return 300

    def preferred_phoneme_scheme(self):
        return None

    def sample_rate(self) -> int:
        return self.model.sr

    def teardown(self) -> None:
        self.model = None

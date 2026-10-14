from typing import Optional

import numpy as np

from .base import VoiceProfileHandle, detect_device


class ChatterboxEngine:
    name = "chatterbox"
    supports_cloning = True

    def __init__(self):
        self.model = None
        self.device = None
        self._default_conds = None

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
        self._default_conds = self.model.conds  # built-in voice, for profiles with no reference

    def clone_profile(self, reference_audio: Optional[str], reference_text: Optional[str],
                       params: dict) -> VoiceProfileHandle:
        # Encode the reference once per profile. Passing audio_prompt_path to
        # generate() instead re-decodes and re-encodes it on every chunk (~0.7 s each).
        if reference_audio:
            self.model.prepare_conditionals(reference_audio, exaggeration=params.get("exaggeration", 0.5))
            conds = self.model.conds
        else:
            conds = self._default_conds
        return VoiceProfileHandle(data={"conds": conds})

    def synthesize(self, text_chunk: str, profile_handle: Optional[VoiceProfileHandle],
                    params: dict) -> np.ndarray:
        self.model.conds = profile_handle.data["conds"] if profile_handle else self._default_conds
        wav = self.model.generate(
            text_chunk,
            audio_prompt_path=None,  # uses self.model.conds; generate() adjusts exaggeration itself
            exaggeration=params.get("exaggeration", 0.5),
            cfg_weight=params.get("cfg", 0.5),
            temperature=params.get("temperature", 0.8),
        )
        return wav.squeeze().cpu().numpy()

    def max_chunk_chars(self) -> int:
        return 300

    def min_chunk_chars(self) -> int:
        return 80  # ~5 s of speech; shorter inputs come out garbled

    def preferred_phoneme_scheme(self):
        return None

    def sample_rate(self) -> int:
        return self.model.sr

    def teardown(self) -> None:
        self.model = None

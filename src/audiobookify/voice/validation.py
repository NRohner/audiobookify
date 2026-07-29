from dataclasses import dataclass, field
from typing import List

import numpy as np
import soundfile as sf

ABSOLUTE_MIN_SECONDS = 1.0
RECOMMENDED_MIN_SECONDS = 10.0
RECOMMENDED_MAX_SECONDS = 30.0

CLIP_AMPLITUDE = 0.999
CLIP_FRACTION_WARN = 0.001

FRAME_SECONDS = 0.02
SILENCE_RMS_THRESHOLD = 0.02
SILENCE_FRACTION_MIN = 0.02


@dataclass
class ReferenceAudioInfo:
    path: str
    sample_rate: int
    channels: int
    duration_seconds: float
    warnings: List[str] = field(default_factory=list)


def _quiet_frame_fraction(mono: np.ndarray, sample_rate: int) -> float:
    frame_len = max(1, int(FRAME_SECONDS * sample_rate))
    n_frames = len(mono) // frame_len
    if n_frames == 0:
        return 1.0  # too short to frame - don't false-alarm on noise
    frames = mono[: n_frames * frame_len].reshape(n_frames, frame_len)
    frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return float(np.mean(frame_rms < SILENCE_RMS_THRESHOLD))


def validate_reference_audio(path: str) -> ReferenceAudioInfo:
    data, sample_rate = sf.read(path, always_2d=True, dtype="float32")
    channels = data.shape[1]
    duration_seconds = len(data) / sample_rate

    if duration_seconds < ABSOLUTE_MIN_SECONDS:
        raise ValueError(
            f"reference audio is too short to clone from ({duration_seconds:.2f}s); "
            f"need at least {ABSOLUTE_MIN_SECONDS:.0f}s"
        )

    warnings: List[str] = []
    if duration_seconds < RECOMMENDED_MIN_SECONDS:
        warnings.append(
            f"reference audio is {duration_seconds:.1f}s, shorter than the recommended "
            f"{RECOMMENDED_MIN_SECONDS:.0f}-{RECOMMENDED_MAX_SECONDS:.0f}s window"
        )
    elif duration_seconds > RECOMMENDED_MAX_SECONDS:
        warnings.append(
            f"reference audio is {duration_seconds:.1f}s, longer than the recommended "
            f"{RECOMMENDED_MIN_SECONDS:.0f}-{RECOMMENDED_MAX_SECONDS:.0f}s window"
        )

    if channels > 1:
        warnings.append(f"reference audio has {channels} channels; mono is recommended")

    mono = data.mean(axis=1)

    clipped_fraction = float(np.mean(np.abs(mono) >= CLIP_AMPLITUDE))
    if clipped_fraction > CLIP_FRACTION_WARN:
        warnings.append(
            f"possible clipping detected ({clipped_fraction * 100:.2f}% of samples at peak amplitude)"
        )

    # ponytail: crude quiet-frame heuristic (no sustained quiet passage found =
    # probable constant background noise); swap for a spectral noise estimate
    # if this produces false positives on real recordings.
    if _quiet_frame_fraction(mono, sample_rate) < SILENCE_FRACTION_MIN:
        warnings.append("no quiet passages detected; recording may have constant background noise")

    return ReferenceAudioInfo(
        path=path, sample_rate=sample_rate, channels=channels,
        duration_seconds=duration_seconds, warnings=warnings,
    )

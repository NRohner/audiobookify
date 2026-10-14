"""Reference-clip analysis: Whisper transcript with word timings, and the
reference trim. Pure helpers except transcribe(), which runs Whisper on MLX."""
import re
from pathlib import Path
from typing import Optional

import soundfile as sf

WHISPER_REPO = "mlx-community/whisper-large-v3-turbo-asr-fp16"
MAX_REFERENCE_SECONDS = 15.0  # Qwen3 encodes the whole reference per chunk; 36 s ran ~15% slower than 15 s
SENTENCE_WINDOW_SECONDS = 5.0  # cut at a sentence end if one falls this close to the limit
TRAIL_SECONDS = 0.15  # air kept after the last word

_SENTENCE_END_RE = re.compile(r'[.!?…]["\'”’)\]]*$')


def _load_whisper():
    from mlx_audio.stt.utils import load_model
    return load_model(WHISPER_REPO)


def transcribe(path: str) -> dict:
    """One Whisper pass: {"text": str, "words": [{"word", "start", "end"}, ...]}.
    Loads Whisper per call (~1-3 s once cached) so it never sits next to a TTS model."""
    model = _load_whisper()
    try:
        out = model.generate(path, word_timestamps=True)
        words = [{"word": w["word"], "start": w["start"], "end": w["end"]}
                 for seg in out.segments for w in seg["words"]]
        return {"text": out.text.strip(), "words": words}
    finally:
        del model
        from ..engines.mlx_util import free_memory
        free_memory()


def trim_point(words: list, max_seconds: float = MAX_REFERENCE_SECONDS) -> Optional[tuple]:
    """(end_seconds, transcript_of_kept_words) for a clip longer than max_seconds,
    cut at a word boundary (a sentence end if one is near the limit); None if the
    clip already fits."""
    if not words or words[-1]["end"] <= max_seconds:
        return None
    fits = [i for i, w in enumerate(words) if w["end"] <= max_seconds]
    if not fits:
        return None  # ponytail: one word longer than the limit; use the clip whole
    last = fits[-1]
    sentence_ends = [i for i in fits if _SENTENCE_END_RE.search(words[i]["word"].strip())
                     and words[i]["end"] >= max_seconds - SENTENCE_WINDOW_SECONDS]
    if sentence_ends:
        last = sentence_ends[-1]
    end = min(words[last]["end"] + TRAIL_SECONDS, words[last + 1]["start"])
    return end, "".join(w["word"] for w in words[:last + 1]).strip()


def reference_clip(reference_path: str, trim_end: Optional[float]) -> str:
    """The clip the engines clone from: the original, or its first trim_end
    seconds. The trimmed file is a cache next to the original (the original
    never changes for a voice); the trim point on the voice is the source of truth."""
    if trim_end is None:
        return reference_path
    ref = Path(reference_path)
    out = ref.with_name(f"{ref.stem}.trim-{trim_end:.2f}s.wav")
    if not out.is_file():
        data, sr = sf.read(str(ref), dtype="float32")
        sf.write(str(out), data[: int(round(trim_end * sr))], sr)
    return str(out)

"""Reference-clip helpers: Whisper transcript with word timings (transcribe) and
the derived clip the engines clone from (reference_clip). Trim point and pace are
computed in Rust."""
import re
import subprocess
from pathlib import Path
from typing import Optional

import soundfile as sf

WHISPER_REPO = "mlx-community/whisper-large-v3-turbo-asr-fp16"
# Whisper sometimes returns a clip as unpunctuated lowercase. A short punctuated
# prompt brings punctuation back; content-bearing prompts leak words into the
# transcript, and some short ones made Whisper skip most of a clip.
PUNCTUATION_PROMPT = "Hello. Yes, I see."
MIN_RETRY_WORD_RATIO = 0.85  # keep the prompted pass only if it kept nearly all the words


def _load_whisper():
    from mlx_audio.stt.utils import load_model
    return load_model(WHISPER_REPO)


def transcribe(path: str) -> dict:
    """One Whisper pass: {"text": str, "words": [{"word", "start", "end"}, ...]}.
    Loads Whisper per call (~1-3 s once cached) so it never sits next to a TTS model."""
    model = _load_whisper()
    try:
        result = _result(model.generate(path, word_timestamps=True))
        if not re.search(r"[.,!?;:]", result["text"]):
            retry = _result(model.generate(path, word_timestamps=True, initial_prompt=PUNCTUATION_PROMPT))
            if len(retry["words"]) >= MIN_RETRY_WORD_RATIO * len(result["words"]):
                result = retry
        return result
    finally:
        del model
        from ..engines.mlx_util import free_memory
        free_memory()


def _result(out) -> dict:
    words = [{"word": w["word"], "start": w["start"], "end": w["end"]}
             for seg in out.segments for w in seg["words"]]
    return {"text": out.text.strip(), "words": words}


def reference_clip(reference_path: str, trim_end: Optional[float], tempo: float = 1.0) -> str:
    """The clip the engines clone from: the original, or its first trim_end
    seconds, played at tempo (ffmpeg atempo keeps the pitch). Derived clips are
    caches next to the original (the original never changes for a voice); the
    trim point and tempo on the voice are the source of truth."""
    ref = Path(reference_path)
    clip = reference_path
    if trim_end is not None:
        clip = str(ref.with_name(f"{ref.stem}.trim-{trim_end:.2f}s.wav"))
        if not Path(clip).is_file():
            data, sr = sf.read(str(ref), dtype="float32")
            sf.write(clip, data[: int(round(trim_end * sr))], sr)
    if round(tempo, 2) == 1.0:
        return clip
    out = Path(clip).with_name(f"{Path(clip).stem}.tempo-{tempo:.2f}.wav")
    if not out.is_file():
        tmp = out.with_name(f".{out.name}")  # never leave a half-written clip under the cached name
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", clip,
                        "-filter:a", f"atempo={tempo:.2f}", str(tmp)], check=True)
        tmp.replace(out)
    return str(out)

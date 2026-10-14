"""Reference-clip analysis: Whisper transcript with word timings, and the
reference trim. Pure helpers except transcribe(), which runs Whisper on MLX."""
import re
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from ..normalize.text import expand_numbers

WHISPER_REPO = "mlx-community/whisper-large-v3-turbo-asr-fp16"
MAX_REFERENCE_SECONDS = 15.0  # Qwen3 encodes the whole reference per chunk; 36 s ran ~15% slower than 15 s
SENTENCE_WINDOW_SECONDS = 5.0  # cut at a sentence end if one falls this close to the limit
TRAIL_SECONDS = 0.15  # air kept after the last word
TEMPO_RANGE = (0.75, 1.25)  # beyond this atempo starts to sound processed
# Whisper sometimes returns a clip as unpunctuated lowercase. A short punctuated
# prompt brings punctuation back; content-bearing prompts leak words into the
# transcript, and some short ones made Whisper skip most of a clip.
PUNCTUATION_PROMPT = "Hello. Yes, I see."
MIN_RETRY_WORD_RATIO = 0.85  # keep the prompted pass only if it kept nearly all the words

PAUSE_MIN_SECONDS = 0.15  # shorter gaps are part of continuous speech
_SENTENCE_END_RE = re.compile(r'[.!?…]["\'”’)\]]*$')
_CLAUSE_END_RE = re.compile(r'[,;:—–]["\'”’)\]]*$')


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


def _words(text: str) -> list:
    text = expand_numbers(text.replace("’", "'")).lower()
    return re.sub(r"[^a-z0-9' ]+", " ", text.replace("-", " ")).split()


def word_error_rate(reference: str, hypothesis: str) -> float:
    """(substitutions + deletions + insertions) / reference words, after normalizing
    case, punctuation, hyphens and numbers (Whisper may write digits back)."""
    ref, hyp = _words(reference), _words(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1] / max(len(ref), 1)


def cadence_stats(words: list) -> dict:
    """Pace and pauses from Whisper word timestamps. Pauses are classified by the
    punctuation Whisper attaches to the word before them (same bias for every model)."""
    gaps = [(round(b["start"] - a["end"], 3), a["word"].strip()) for a, b in zip(words, words[1:])]
    pauses = [(g, w) for g, w in gaps if g >= PAUSE_MIN_SECONDS]
    sentence = [g for g, w in pauses if _SENTENCE_END_RE.search(w)]
    clause = [g for g, w in pauses if _CLAUSE_END_RE.search(w)]
    span = words[-1]["end"] - words[0]["start"]
    median = lambda xs: float(np.median(xs)) if xs else None
    return {
        "articulation_wpm": len(words) / (span - sum(g for g, _ in pauses)) * 60,
        "overall_wpm": len(words) / span * 60,
        "sentence_pauses": sentence, "comma_pauses": clause,
        "sentence_pause_median": median(sentence), "comma_pause_median": median(clause),
        "other_pause_median": median([g for g, w in pauses
                                      if not (_SENTENCE_END_RE.search(w) or _CLAUSE_END_RE.search(w))]),
    }


def reference_pace(words: Optional[list], trim_end: Optional[float]) -> Optional[dict]:
    """Speaking (pauses excluded) and overall wpm of the clip the engines clone
    from; None without at least two words to measure between."""
    kept = [w for w in words or [] if trim_end is None or w["end"] <= trim_end]
    if len(kept) < 2:
        return None
    stats = cadence_stats(kept)
    return {"speaking_wpm": stats["articulation_wpm"], "overall_wpm": stats["overall_wpm"]}

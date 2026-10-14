"""TTS benchmark: run one engine variant over a passage through the app's real
chunking/joining and record load time, per-chunk generation time and memory.

    python -m audiobookify.bench bench/passage.txt --voice "voices/7_Jim Dale/reference.mp3"

    python -m audiobookify.bench bench/passage.txt --variant qwen3-tts-1.7b --voice ...

ponytail: one variant per process; no orchestrator.
Word-accuracy scoring (Whisper) runs later against the saved WAVs.
"""
import argparse
import functools
import hashlib
import json
import re
import resource
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from .engines.base import VoiceProfileHandle
from .engines.turbo import ChatterboxTurboEngine
from .normalize.text import chunk_text, expand_numbers, split_paragraphs
from .postprocess.audio import concatenate_chunks

DEFAULT_MAX_CHARS = 4500  # ~5 min of audio
TRANSCRIPT_CACHE = Path("bench/transcripts")
WHISPER_REPO = "mlx-community/whisper-large-v3-turbo-asr-fp16"


@functools.lru_cache(maxsize=1)
def _whisper():
    from mlx_audio.stt.utils import load_model
    return load_model(WHISPER_REPO)


def whisper_transcribe(path: str) -> str:
    return _whisper().generate(path).text


def reference_transcript(path: str, cache_dir: Path = TRANSCRIPT_CACHE, transcribe=whisper_transcribe) -> str:
    """Transcript of a reference clip, keyed on the file's contents so a replaced
    recording gets re-transcribed. Kept out of voices/ (app data)."""
    key = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    cached = Path(cache_dir) / f"{key}.txt"
    if not cached.is_file():
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(transcribe(path).strip(), encoding="utf-8")
    return cached.read_text(encoding="utf-8")


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


class MLXAudioEngine:
    """Generic upstream mlx-audio model. Subclasses set REPO and GEN_KWARGS (the
    model's own defaults unless noted); chunk limits match the app's Turbo engine
    so every model gets the same chunks."""

    REPO = ""
    GEN_KWARGS: dict = {}
    NEEDS_REF_TEXT = False
    supports_cloning = True

    def __init__(self):
        self.model = None
        self.device = None

    def load(self, device: str = "auto") -> None:
        from mlx_audio.tts.utils import load_model
        self.model = load_model(self.REPO)
        self.device = "mlx"

    def clone_profile(self, reference_audio, reference_text, params):
        return VoiceProfileHandle(data={"ref_audio": reference_audio, "ref_text": reference_text})

    def _reference_kwargs(self, profile_handle) -> dict:
        kwargs = {"ref_audio": profile_handle.data["ref_audio"]}
        if self.NEEDS_REF_TEXT:
            kwargs["ref_text"] = profile_handle.data["ref_text"]
        return kwargs

    def synthesize(self, text_chunk, profile_handle, params):
        # Some models split text themselves and yield several segments; keep them all.
        segments = self.model.generate(text_chunk, **self._reference_kwargs(profile_handle), **self.GEN_KWARGS)
        return np.concatenate([np.asarray(s.audio, dtype=np.float32).reshape(-1) for s in segments])

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


class Qwen3TTS(MLXAudioEngine):
    """Qwen3-TTS Base: in-context voice cloning from reference audio + transcript.
    Its MLX port loads and encodes the reference on every call."""

    REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
    NEEDS_REF_TEXT = True
    GEN_KWARGS = {"lang_code": "english"}  # skip auto-detection; sampling left at its defaults


class BreezeTTS2(MLXAudioEngine):
    """Breeze TTS 2 (3B, research/non-commercial license): reference audio +
    transcript, re-encoded on every call by the port. Model defaults otherwise."""

    REPO = "mlx-community/Breeze-TTS-2-mlx"
    NEEDS_REF_TEXT = True


VARIANTS = {
    "chatterbox-turbo": ChatterboxTurboEngine,  # the app's engine, as-is
    "qwen3-tts-1.7b": Qwen3TTS,
    "breeze-tts-2": BreezeTTS2,
}

_RUNTIME_PACKAGES = ["mlx", "mlx-audio", "librosa", "soundfile"]


def leading_paragraphs(text: str, max_chars: Optional[int]) -> str:
    """Whole paragraphs from the start, stopping once max_chars is reached (None = all)."""
    kept, total = [], 0
    for paragraph in split_paragraphs(text):
        if max_chars is not None and kept and total >= max_chars:
            break
        kept.append(paragraph)
        total += len(paragraph)
    return "\n\n".join(kept)


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3  # bytes on macOS, KiB on Linux


def _package_versions() -> dict:
    from importlib.metadata import PackageNotFoundError, version
    found = {}
    for name in _RUNTIME_PACKAGES:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            pass
    return found


def _mlx_peak_mb() -> Optional[float]:
    mx = sys.modules.get("mlx.core")
    return mx.get_peak_memory() / 1e6 if mx is not None else None


def run_variant(name: str, engine, text: str, reference_audio: Optional[str], out_dir: Path,
                max_chars: Optional[int] = None, device: str = "auto",
                reference_text: Optional[str] = None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    t = time.perf_counter()
    engine.load(device)
    load_seconds = time.perf_counter() - t

    max_chars = max_chars or engine.max_chunk_chars()
    chunks = chunk_text(text, max_chars, engine.min_chunk_chars())
    profile = engine.clone_profile(reference_audio, reference_text, {})
    sr = engine.sample_rate()

    audio, records = [], []
    for i, chunk in enumerate(chunks):
        t = time.perf_counter()
        wav = engine.synthesize(chunk.text, profile, {})
        gen = time.perf_counter() - t
        audio.append(wav)
        records.append({"index": i, "chars": len(chunk.text), "gen_seconds": gen,
                        "audio_seconds": len(wav) / sr, "text": chunk.text})
        print(f"  [{name}] chunk {i + 1}/{len(chunks)}: {len(wav) / sr:.1f}s audio in {gen:.1f}s "
              f"(rtf {gen / max(len(wav) / sr, 1e-9):.2f})", flush=True)

    gen_seconds = sum(r["gen_seconds"] for r in records)
    audio_seconds = sum(r["audio_seconds"] for r in records)
    result = {
        "variant": name, "device": getattr(engine, "device", None), "max_chars": max_chars,
        "min_chars": engine.min_chunk_chars(), "text_chars": len(text), "n_chunks": len(chunks),
        "load_seconds": load_seconds, "gen_seconds": gen_seconds, "audio_seconds": audio_seconds,
        "rtf": gen_seconds / audio_seconds if audio_seconds else None,
        "peak_rss_mb": _peak_rss_mb(), "mlx_peak_mb": _mlx_peak_mb(),
        "packages": _package_versions(),
        "chunks": records,
    }
    sf.write(str(out_dir / f"{name}.wav"), concatenate_chunks(audio, [c.ends_with for c in chunks], sr), sr)
    (out_dir / f"{name}.json").write_text(json.dumps(result, indent=2))
    engine.teardown()
    return result


PAUSE_MIN_SECONDS = 0.15  # shorter gaps are part of continuous speech
_SENTENCE_END_RE = re.compile(r'[.!?…]["\'”’)\]]*$')
_CLAUSE_END_RE = re.compile(r'[,;:—–]["\'”’)\]]*$')


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


def measure_cadence(out_dir: Path) -> dict:
    """Word-timestamped Whisper pass over each variant's WAV; stores cadence in its JSON."""
    found = {}
    for result_path in sorted(Path(out_dir).glob("*.json")):
        result = json.loads(result_path.read_text())
        out = _whisper().generate(str(result_path.with_suffix(".wav")), word_timestamps=True)
        result["cadence"] = cadence_stats([w for seg in out.segments for w in seg["words"]])
        result_path.write_text(json.dumps(result, indent=2))
        found[result["variant"]] = result["cadence"]
    return found


def score_run(out_dir: Path, transcribe=whisper_transcribe) -> dict:
    """Transcribe each variant's WAV and store its word error rate against the
    chunk texts the engine was actually given (lexicon/number expansion applied)."""
    scores = {}
    for result_path in sorted(Path(out_dir).glob("*.json")):
        result = json.loads(result_path.read_text())
        transcript = transcribe(str(result_path.with_suffix(".wav")))
        result["transcript"] = transcript
        result["wer"] = word_error_rate(" ".join(c["text"] for c in result["chunks"]), transcript)
        result_path.write_text(json.dumps(result, indent=2))
        scores[result["variant"]] = result["wer"]
    return scores


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("passage", nargs="?")
    parser.add_argument("--voice", help="reference audio for cloning")
    parser.add_argument("--score", metavar="DIR", help="add word error rates to every result in DIR")
    parser.add_argument("--cadence", metavar="DIR", help="add pace/pause stats to every result in DIR")
    parser.add_argument("--variant", default="chatterbox-turbo", choices=sorted(VARIANTS))
    parser.add_argument("--chunk-chars", type=int, help="override the engine's max chunk size")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="passage length to use")
    parser.add_argument("--full", action="store_true", help="use the whole passage")
    parser.add_argument("--out", default=f"bench/out/{datetime.now():%Y%m%d-%H%M%S}")
    args = parser.parse_args(argv)
    if args.score:
        for variant, wer in score_run(Path(args.score)).items():
            print(f"{variant:30} WER {wer:6.2%}")
        return
    if args.cadence:
        fmt = lambda x: f"{x:.2f}s" if x is not None else "  -  "
        print(f"{'variant':30} {'wpm (speaking)':>14} {'wpm (overall)':>13} {'sentence pause':>14} {'comma pause':>11}")
        for variant, c in measure_cadence(Path(args.cadence)).items():
            print(f"{variant:30} {c['articulation_wpm']:14.0f} {c['overall_wpm']:13.0f} "
                  f"{fmt(c['sentence_pause_median']):>14} {fmt(c['comma_pause_median']):>11}")
        return
    if not (args.passage and args.voice):
        parser.error("passage and --voice are required unless --score is given")

    text = leading_paragraphs(Path(args.passage).read_text(encoding="utf-8"), None if args.full else args.max_chars)
    name = args.variant + (f"-{args.chunk_chars}" if args.chunk_chars else "")
    print(f"{name}: {len(text)} chars -> {args.out}", flush=True)
    engine = VARIANTS[args.variant]()
    ref_text = reference_transcript(args.voice) if getattr(engine, "NEEDS_REF_TEXT", False) else None
    if ref_text:
        print(f"reference transcript: {ref_text!r}", flush=True)
    r = run_variant(name, engine, text, args.voice, Path(args.out), args.chunk_chars, reference_text=ref_text)
    warm = r["chunks"][1:] or r["chunks"]
    warm_rtf = sum(c["gen_seconds"] for c in warm) / sum(c["audio_seconds"] for c in warm)
    first = r["chunks"][0]
    print(f"\n{name} on {r['device']}: load {r['load_seconds']:.1f}s | {r['audio_seconds'] / 60:.1f} min audio "
          f"in {r['gen_seconds'] / 60:.1f} min | rtf {r['rtf']:.2f} (first chunk "
          f"{first['gen_seconds'] / first['audio_seconds']:.2f}, rest {warm_rtf:.2f}) | "
          f"peak rss {r['peak_rss_mb']:.0f} MB"
          + (f" | mlx peak {r['mlx_peak_mb']:.0f} MB" if r["mlx_peak_mb"] else ""))


if __name__ == "__main__":
    main()

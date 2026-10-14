import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf
from mutagen.id3 import TALB, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3

BACKUP_OUTPUT_DIR = str(Path(__file__).resolve().parents[3] / "output")


# Silence inserted after a chunk, by why the chunk ended (see normalize.text.Chunk).
PAUSE_SECONDS = {"word": 0.1, "clause": 0.2, "sentence": 0.35, "paragraph": 0.75, "scene": 1.5}

_FRAME_SECONDS = 0.01
_SILENCE_BELOW_PEAK_DB = 40.0
_KEEP_MARGIN_SECONDS = 0.03  # keeps breaths and soft consonant onsets
_FADE_SECONDS = 0.01
_LEVEL_TOLERANCE_DB = 3.0
_MAX_LEVEL_GAIN_DB = 6.0
_PEAK_CEILING = 0.99


def trim_silence(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Cut the model's own leading/trailing silence (so pauses come only from
    PAUSE_SECONDS) and fade the edges so joins don't click."""
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    frame = max(1, int(sample_rate * _FRAME_SECONDS))
    n_frames = len(audio) // frame
    if peak == 0.0 or n_frames == 0:
        return audio
    rms = np.sqrt(np.mean(audio[:n_frames * frame].reshape(n_frames, frame).astype(np.float64) ** 2, axis=1))
    loud = np.nonzero(rms > peak * 10 ** (-_SILENCE_BELOW_PEAK_DB / 20))[0]
    if not loud.size:
        return audio
    margin = int(sample_rate * _KEEP_MARGIN_SECONDS)
    start = max(0, loud[0] * frame - margin)
    end = min(len(audio), (loud[-1] + 1) * frame + margin)
    out = audio[start:end].astype(np.float32, copy=True)
    fade = min(int(sample_rate * _FADE_SECONDS), len(out) // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        out[:fade] *= ramp
        out[-fade:] *= ramp[::-1]
    return out


def _level_db(audio: np.ndarray) -> Optional[float]:
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size else 0.0
    return 20 * np.log10(rms) if rms > 0 else None


def level_chunks(chunks: List[np.ndarray]) -> List[np.ndarray]:
    """Nudge chunks more than _LEVEL_TOLERANCE_DB off the median level toward it
    (capped, never clipping). The overall level is left as the model made it."""
    levels = [_level_db(c) for c in chunks]
    known = [lv for lv in levels if lv is not None]
    if not known:
        return chunks
    median = float(np.median(known))
    out = []
    for chunk, level in zip(chunks, levels):
        if level is None or abs(median - level) <= _LEVEL_TOLERANCE_DB:
            out.append(chunk)
            continue
        gain = 10 ** (float(np.clip(median - level, -_MAX_LEVEL_GAIN_DB, _MAX_LEVEL_GAIN_DB)) / 20)
        gain = min(gain, _PEAK_CEILING / float(np.abs(chunk).max()))
        out.append((chunk * gain).astype(np.float32))
    return out


def concatenate_chunks(chunks: List[np.ndarray], ends_with: List[str], sample_rate: int) -> np.ndarray:
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    chunks = level_chunks([trim_silence(c, sample_rate) for c in chunks])
    parts = []
    for i, chunk in enumerate(chunks):
        parts.append(chunk)
        if i < len(chunks) - 1:
            parts.append(np.zeros(int(sample_rate * PAUSE_SECONDS[ends_with[i]]), dtype=np.float32))
    return np.concatenate(parts)


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name).strip()


def chapter_filename(order_index: int, title: str) -> str:
    return f"{order_index + 1:02d} - {sanitize_filename(title)}.mp3"


def _export_chapter_mp3_to(output_dir: str, audio: np.ndarray, sample_rate: int, order_index: int,
                            chapter_title: str, book_title: str, narrator: str) -> str:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    wav_path = output_path / f".tmp_{order_index}.wav"
    sf.write(str(wav_path), audio, sample_rate)

    mp3_path = output_path / chapter_filename(order_index, chapter_title)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-qscale:a", "2", str(mp3_path)],
        check=True, capture_output=True,
    )
    wav_path.unlink()

    tags = MP3(str(mp3_path))
    if tags.tags is None:
        tags.add_tags()
    tags.tags.add(TIT2(encoding=3, text=chapter_title))
    tags.tags.add(TALB(encoding=3, text=book_title))
    tags.tags.add(TRCK(encoding=3, text=str(order_index + 1)))
    tags.tags.add(TPE1(encoding=3, text=narrator))
    tags.save()

    return str(mp3_path)


def export_chapter_mp3(audio: np.ndarray, sample_rate: int, output_dir: str, order_index: int,
                        chapter_title: str, book_title: str, narrator: str,
                        fallback_dir: Optional[str] = None) -> Tuple[str, bool]:
    try:
        return _export_chapter_mp3_to(output_dir, audio, sample_rate, order_index,
                                       chapter_title, book_title, narrator), False
    except (OSError, sf.LibsndfileError):
        if fallback_dir is None:
            raise
        return _export_chapter_mp3_to(fallback_dir, audio, sample_rate, order_index,
                                       chapter_title, book_title, narrator), True


def _export_mp3_to(output_path: Path, audio: np.ndarray, sample_rate: int,
                    title: Optional[str], artist: Optional[str]) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wav_path = output_path.with_suffix(".tmp.wav")
    sf.write(str(wav_path), audio, sample_rate)

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-qscale:a", "2", str(output_path)],
        check=True, capture_output=True,
    )
    wav_path.unlink()

    if title or artist:
        tags = MP3(str(output_path))
        if tags.tags is None:
            tags.add_tags()
        if title:
            tags.tags.add(TIT2(encoding=3, text=title))
        if artist:
            tags.tags.add(TPE1(encoding=3, text=artist))
        tags.save()

    return str(output_path)


def export_mp3(audio: np.ndarray, sample_rate: int, output_path: str,
                title: Optional[str] = None, artist: Optional[str] = None,
                fallback_dir: Optional[str] = None) -> Tuple[str, bool]:
    primary_path = Path(output_path)
    try:
        return _export_mp3_to(primary_path, audio, sample_rate, title, artist), False
    except (OSError, sf.LibsndfileError):
        if fallback_dir is None:
            raise
        fallback_path = Path(fallback_dir) / primary_path.name
        return _export_mp3_to(fallback_path, audio, sample_rate, title, artist), True

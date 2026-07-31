import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf
from mutagen.id3 import TALB, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3

BACKUP_OUTPUT_DIR = str(Path(__file__).resolve().parents[3] / "output")


def concatenate_chunks(chunks: List[np.ndarray], sample_rate: int, pause_seconds: float = 0.4) -> np.ndarray:
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    pause = np.zeros(int(sample_rate * pause_seconds), dtype=np.float32)
    parts = []
    for i, chunk in enumerate(chunks):
        parts.append(chunk)
        if i < len(chunks) - 1:
            parts.append(pause)
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

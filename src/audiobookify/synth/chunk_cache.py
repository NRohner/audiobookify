import hashlib
import json
import shutil
from pathlib import Path
from typing import List

import numpy as np
import soundfile as sf

CHUNK_CACHE_DIR = str(Path(__file__).resolve().parents[3] / ".cache" / "synth")


def compute_cache_key(text: str, max_chunk_chars: int, voice_params: dict) -> str:
    payload = json.dumps(
        {"text": text, "max_chunk_chars": max_chunk_chars, "voice_params": voice_params},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def chapter_cache_dir(chapter_id: int) -> Path:
    return Path(CHUNK_CACHE_DIR) / f"chapter-{chapter_id}"


def load_cached_chunks(chapter_id: int, cache_key: str) -> List[np.ndarray]:
    cache_dir = chapter_cache_dir(chapter_id)
    manifest_path = cache_dir / "manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if manifest.get("cache_key") != cache_key:
        return []

    chunks = []
    i = 0
    while True:
        chunk_path = cache_dir / f"chunk-{i:04d}.wav"
        if not chunk_path.is_file():
            break
        audio, _ = sf.read(str(chunk_path), dtype="float32")
        chunks.append(audio)
        i += 1
    return chunks


def save_chunk(chapter_id: int, index: int, audio: np.ndarray, sample_rate: int) -> None:
    cache_dir = chapter_cache_dir(chapter_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    sf.write(str(cache_dir / f"chunk-{index:04d}.wav"), audio, sample_rate)


def write_manifest(chapter_id: int, cache_key: str) -> None:
    cache_dir = chapter_cache_dir(chapter_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "manifest.json").write_text(json.dumps({"cache_key": cache_key}))


def clear_cache(chapter_id: int) -> None:
    shutil.rmtree(chapter_cache_dir(chapter_id), ignore_errors=True)

import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from mutagen.mp3 import MP3

from audiobookify.postprocess.audio import (
    chapter_filename,
    concatenate_chunks,
    export_chapter_mp3,
    export_mp3,
    sanitize_filename,
)

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_concatenate_chunks_inserts_pause_between_each():
    sample_rate = 1000
    chunks = [np.ones(100, dtype=np.float32), np.ones(200, dtype=np.float32)]
    result = concatenate_chunks(chunks, sample_rate, pause_seconds=0.1)
    assert len(result) == 100 + 200 + int(sample_rate * 0.1)


def test_sanitize_filename_strips_illegal_characters():
    assert sanitize_filename('Chapter One: "The Beginning"?') == "Chapter One The Beginning"


def test_chapter_filename_zero_pads_and_orders():
    assert chapter_filename(0, "Chapter One") == "01 - Chapter One.mp3"
    assert chapter_filename(9, "Chapter Ten") == "10 - Chapter Ten.mp3"


@requires_ffmpeg
def test_export_chapter_mp3_writes_tagged_file(tmp_path):
    sample_rate = 22050
    audio = np.sin(np.linspace(0, 440 * 2 * np.pi, sample_rate)).astype(np.float32)

    mp3_path, used_fallback = export_chapter_mp3(
        audio, sample_rate, str(tmp_path), order_index=0,
        chapter_title="Chapter One", book_title="My Book", narrator="Chatterbox Default",
    )

    assert used_fallback is False
    assert mp3_path.endswith("01 - Chapter One.mp3")

    tags = MP3(mp3_path)
    assert str(tags.get("TIT2")) == "Chapter One"
    assert str(tags.get("TALB")) == "My Book"
    assert str(tags.get("TRCK")) == "1"
    assert str(tags.get("TPE1")) == "Chatterbox Default"


@requires_ffmpeg
def test_export_chapter_mp3_falls_back_when_primary_dir_is_unwritable(tmp_path):
    sample_rate = 22050
    audio = np.sin(np.linspace(0, 440 * 2 * np.pi, sample_rate)).astype(np.float32)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    fallback = tmp_path / "fallback"

    try:
        mp3_path, used_fallback = export_chapter_mp3(
            audio, sample_rate, str(locked), order_index=0,
            chapter_title="Chapter One", book_title="My Book", narrator="Narrator",
            fallback_dir=str(fallback),
        )
    finally:
        os.chmod(locked, 0o700)

    assert used_fallback is True
    assert mp3_path.startswith(str(fallback))
    assert Path(mp3_path).is_file()


@requires_ffmpeg
def test_export_chapter_mp3_raises_when_unwritable_and_no_fallback_given(tmp_path):
    sample_rate = 22050
    audio = np.sin(np.linspace(0, 440 * 2 * np.pi, sample_rate)).astype(np.float32)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)

    try:
        with pytest.raises((OSError, sf.LibsndfileError)):
            export_chapter_mp3(
                audio, sample_rate, str(locked), order_index=0,
                chapter_title="Chapter One", book_title="My Book", narrator="Narrator",
            )
    finally:
        os.chmod(locked, 0o700)


@requires_ffmpeg
def test_export_mp3_falls_back_when_primary_dir_is_unwritable(tmp_path):
    sample_rate = 22050
    audio = np.sin(np.linspace(0, 440 * 2 * np.pi, sample_rate)).astype(np.float32)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    fallback = tmp_path / "fallback"

    try:
        mp3_path, used_fallback = export_mp3(
            audio, sample_rate, str(locked / "clip.mp3"), fallback_dir=str(fallback),
        )
    finally:
        os.chmod(locked, 0o700)

    assert used_fallback is True
    assert mp3_path == str(fallback / "clip.mp3")
    assert Path(mp3_path).is_file()

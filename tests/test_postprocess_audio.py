import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from mutagen.mp3 import MP3

from audiobookify.postprocess.audio import (
    PAUSE_SECONDS,
    chapter_filename,
    level_chunks,
    trim_silence,
    concatenate_chunks,
    export_chapter_mp3,
    export_mp3,
    sanitize_filename,
)

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


SR = 1000


def _speech(n, amp=0.5):
    """A loud, non-silent signal standing in for speech."""
    return (amp * np.sin(np.arange(n) * 0.3)).astype(np.float32)


def _padded(n_speech, n_pad, amp=0.5):
    pad = np.zeros(n_pad, dtype=np.float32)
    return np.concatenate([pad, _speech(n_speech, amp), pad])


def test_trim_silence_removes_padding_but_keeps_speech_and_a_small_margin():
    trimmed = trim_silence(_padded(500, 400), SR)
    margin = int(SR * 0.03)
    assert 500 <= len(trimmed) <= 500 + 2 * margin + 2 * 10  # frame rounding


def test_trim_silence_fades_edges_to_zero():
    trimmed = trim_silence(_speech(500), SR)
    assert trimmed[0] == 0.0 and trimmed[-1] == 0.0


def test_trim_silence_leaves_an_all_silent_chunk_alone():
    silent = np.zeros(300, dtype=np.float32)
    assert np.array_equal(trim_silence(silent, SR), silent)


def test_level_chunks_nudges_an_outlier_toward_the_median():
    quiet = _speech(500, amp=0.05)  # ~20 dB below the others
    out = level_chunks([_speech(500), _speech(500), quiet, _speech(500)])
    gain_db = 20 * np.log10(np.abs(out[2]).max() / np.abs(quiet).max())
    assert gain_db == pytest.approx(6.0, abs=0.01)  # capped at +6 dB
    assert np.array_equal(out[0], _speech(500))  # in-range chunks untouched


def test_level_chunks_leaves_chunks_within_tolerance_alone():
    chunks = [_speech(500, 0.5), _speech(500, 0.4)]  # ~2 dB apart
    out = level_chunks(chunks)
    assert all(np.array_equal(a, b) for a, b in zip(out, chunks))


def test_level_chunks_never_clips():
    spiky = _speech(500, amp=0.05)
    spiky[250] = 0.95  # quiet overall (wants +6 dB) but one near-full-scale peak
    out = level_chunks([_speech(500), _speech(500), spiky])
    assert np.abs(out[2]).max() <= 0.99


def test_concatenate_chunks_pauses_by_boundary_type():
    chunks = [_speech(200), _speech(200), _speech(200)]
    result = concatenate_chunks(chunks, ["sentence", "paragraph", "paragraph"], SR)
    expected = 600 + int(SR * PAUSE_SECONDS["sentence"]) + int(SR * PAUSE_SECONDS["paragraph"])
    assert len(result) == expected  # unpadded speech: trimming removes nothing


def test_concatenate_chunks_replaces_model_silence_with_the_table_pause():
    chunks = [_padded(200, 300), _padded(200, 300)]
    result = concatenate_chunks(chunks, ["scene", "paragraph"], SR)
    margin = int(SR * 0.03)
    assert len(result) <= 400 + int(SR * PAUSE_SECONDS["scene"]) + 4 * margin + 4 * 10


def test_concatenate_chunks_handles_empty_input():
    assert len(concatenate_chunks([], [], SR)) == 0


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

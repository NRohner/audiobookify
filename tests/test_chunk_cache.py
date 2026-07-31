import numpy as np
import pytest
import soundfile as sf

import audiobookify.synth.chunk_cache as chunk_cache
from audiobookify.synth.chunk_cache import (
    chapter_cache_dir,
    clear_cache,
    compute_cache_key,
    load_cached_chunks,
    save_chunk,
    write_manifest,
)


def test_compute_cache_key_is_deterministic_for_the_same_inputs():
    key1 = compute_cache_key("Hello.", 300, {"voice_name": "fake"})
    key2 = compute_cache_key("Hello.", 300, {"voice_name": "fake"})
    assert key1 == key2


def test_compute_cache_key_differs_when_text_changes():
    key1 = compute_cache_key("Hello.", 300, {"voice_name": "fake"})
    key2 = compute_cache_key("Goodbye.", 300, {"voice_name": "fake"})
    assert key1 != key2


def test_compute_cache_key_differs_when_voice_params_change():
    key1 = compute_cache_key("Hello.", 300, {"voice_name": "fake"})
    key2 = compute_cache_key("Hello.", 300, {"voice_name": "other"})
    assert key1 != key2


def test_save_chunk_and_load_cached_chunks_round_trips(tmp_path):
    chunk0 = np.linspace(-0.5, 0.5, 1000, dtype=np.float32)
    chunk1 = np.linspace(0.5, -0.5, 500, dtype=np.float32)
    key = compute_cache_key("Hello.", 300, {})

    save_chunk(1, 0, chunk0, sample_rate=22050)
    save_chunk(1, 1, chunk1, sample_rate=22050)
    write_manifest(1, key)

    loaded = load_cached_chunks(1, key)

    assert len(loaded) == 2
    np.testing.assert_allclose(loaded[0], chunk0, atol=1e-4)
    np.testing.assert_allclose(loaded[1], chunk1, atol=1e-4)


def test_load_cached_chunks_returns_empty_when_no_cache_exists(tmp_path):
    assert load_cached_chunks(999, compute_cache_key("x", 300, {})) == []


def test_load_cached_chunks_returns_empty_when_cache_key_does_not_match(tmp_path):
    chunk0 = np.zeros(100, dtype=np.float32)
    save_chunk(2, 0, chunk0, sample_rate=22050)
    write_manifest(2, compute_cache_key("old text", 300, {}))

    result = load_cached_chunks(2, compute_cache_key("new text", 300, {}))

    assert result == []


def test_clear_cache_removes_the_directory(tmp_path):
    save_chunk(3, 0, np.zeros(10, dtype=np.float32), sample_rate=22050)
    assert chapter_cache_dir(3).is_dir()

    clear_cache(3)

    assert not chapter_cache_dir(3).exists()


def test_clear_cache_is_a_noop_for_a_chapter_with_no_cache():
    clear_cache(999)  # must not raise

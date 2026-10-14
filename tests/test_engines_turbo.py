from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from audiobookify.engines import turbo
from audiobookify.engines.turbo import ChatterboxTurboEngine


def test_turbo_engine_declares_cloning_support_and_chunk_limits():
    engine = ChatterboxTurboEngine()
    assert engine.name == "chatterbox-turbo"
    assert engine.supports_cloning is True
    assert (engine.max_chunk_chars(), engine.min_chunk_chars()) == (300, 80)
    assert engine.preferred_phoneme_scheme() is None


def test_turbo_engine_loads_the_fp16_weights_and_keeps_the_built_in_voice(monkeypatch):
    model = MagicMock(_conds="default-voice", sample_rate=24000)
    loaded = []
    monkeypatch.setattr(turbo, "_load_model", lambda repo: loaded.append(repo) or model)

    engine = ChatterboxTurboEngine()
    engine.load("auto")

    assert loaded == ["mlx-community/chatterbox-turbo-fp16"]
    assert engine.sample_rate() == 24000
    assert engine.clone_profile(None, None, {}).data["conds"] == "default-voice"


def _engine_with_mock_model():
    """Engine whose model records which conditionals each generate() saw; the
    port's prepare_conditionals stores its result on model._conds."""
    engine = ChatterboxTurboEngine()
    engine.model = MagicMock(_conds="default-voice")
    engine._default_conds = "default-voice"
    engine.model.prepare_conditionals.side_effect = (
        lambda path: setattr(engine.model, "_conds", f"conds:{path}"))
    used = []

    def generate(text, **kwargs):
        used.append(engine.model._conds)
        # The port may split text itself and yield several segments.
        yield SimpleNamespace(audio=np.ones(3, dtype=np.float32))
        yield SimpleNamespace(audio=np.zeros((1, 2), dtype=np.float32))
    engine.model.generate.side_effect = generate
    return engine, used


def test_turbo_engine_prepares_the_voice_once_per_profile_and_joins_all_segments():
    engine, used = _engine_with_mock_model()

    handle = engine.clone_profile("/refs/narrator.wav", "transcript", {"exaggeration": 0.7})
    for text in ("One.", "Two.", "Three."):
        audio = engine.synthesize(text, handle, {"exaggeration": 0.7, "cfg": 0.3})

    engine.model.prepare_conditionals.assert_called_once_with("/refs/narrator.wav")
    assert used == ["conds:/refs/narrator.wav"] * 3
    # No ref_audio (would re-encode per chunk); Turbo has no exaggeration/cfg.
    engine.model.generate.assert_called_with("Three.")
    assert audio.dtype == np.float32 and audio.tolist() == [1, 1, 1, 0, 0]


def test_turbo_engine_switches_voices_between_profiles():
    engine, used = _engine_with_mock_model()
    a = engine.clone_profile("/refs/a.wav", None, {})
    b = engine.clone_profile("/refs/b.wav", None, {})

    for handle in (a, b, a):
        engine.synthesize("Hi.", handle, {})

    assert used == ["conds:/refs/a.wav", "conds:/refs/b.wav", "conds:/refs/a.wav"]
    assert engine.model.prepare_conditionals.call_count == 2


def test_turbo_engine_without_reference_uses_the_built_in_voice_even_after_another_voice():
    engine, used = _engine_with_mock_model()
    narrator = engine.clone_profile("/refs/narrator.wav", None, {})
    default = engine.clone_profile(None, None, {})

    engine.synthesize("Hi.", narrator, {})
    engine.synthesize("Hi.", default, {})
    engine.synthesize("Hi.", None, {})

    assert used == ["conds:/refs/narrator.wav", "default-voice", "default-voice"]


def test_turbo_engine_teardown_drops_the_model():
    engine, _ = _engine_with_mock_model()
    engine.teardown()
    assert engine.model is None and engine._default_conds is None


@pytest.mark.integration
def test_turbo_engine_loads_and_synthesizes():
    # Requires downloaded weights (`make models`). Run with: pytest -m integration
    engine = ChatterboxTurboEngine()
    engine.load("auto")
    audio = engine.synthesize("Hello world, this is a short test.", engine.clone_profile(None, None, {}), {})
    assert audio.dtype == np.float32 and len(audio) > engine.sample_rate()  # over a second of speech
    engine.teardown()

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from audiobookify.engines import ENGINES, qwen3
from audiobookify.engines.qwen3 import Qwen3Engine
from audiobookify.engines.turbo import ChatterboxTurboEngine


def test_registry_names_both_engines_with_user_facing_labels():
    assert ENGINES["qwen3"].cls is Qwen3Engine
    assert ENGINES["chatterbox-turbo"].cls is ChatterboxTurboEngine
    assert ENGINES["qwen3"].label == "Quality (Qwen3)"
    assert ENGINES["chatterbox-turbo"].label == "Speed (Turbo)"
    assert all(name == spec.cls.name for name, spec in ENGINES.items())


def test_qwen3_engine_declares_cloning_support_and_chunk_limits():
    engine = Qwen3Engine()
    assert engine.name == "qwen3"
    assert engine.supports_cloning is True
    assert (engine.max_chunk_chars(), engine.min_chunk_chars()) == (300, 80)
    assert engine.preferred_phoneme_scheme() is None


def test_qwen3_engine_loads_the_base_bf16_weights(monkeypatch):
    loaded = []
    monkeypatch.setattr(qwen3, "_load_model", lambda repo: loaded.append(repo) or MagicMock(sample_rate=24000))

    engine = Qwen3Engine()
    engine.load()

    assert loaded == ["mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"]
    assert engine.sample_rate() == 24000


def _engine_with_mock_model():
    engine = Qwen3Engine()
    engine.model = MagicMock(sample_rate=24000)
    engine.model.generate.side_effect = lambda text, **kw: iter([
        SimpleNamespace(audio=np.ones(2, dtype=np.float32)),
        SimpleNamespace(audio=np.zeros((1, 3), dtype=np.float32)),
    ])
    return engine


def test_qwen3_engine_clones_from_reference_audio_and_transcript_in_english():
    engine = _engine_with_mock_model()
    handle = engine.clone_profile("/refs/jim.wav", "a small son called", {"exaggeration": 0.7})

    audio = engine.synthesize("Hello there.", handle, {"cfg": 0.3})

    # "english", not "en": the port's language names are spelled out.
    engine.model.generate.assert_called_once_with(
        "Hello there.", ref_audio="/refs/jim.wav", ref_text="a small son called", lang_code="english")
    assert audio.dtype == np.float32 and audio.tolist() == [1, 1, 0, 0, 0]


@pytest.mark.parametrize("audio, text", [(None, None), ("/refs/jim.wav", None), ("/refs/jim.wav", "  ")])
def test_qwen3_engine_refuses_a_voice_without_reference_audio_and_transcript(audio, text):
    # Base has no built-in voice, and cloning without the transcript is unreliable.
    with pytest.raises(ValueError, match="Qwen3"):
        _engine_with_mock_model().clone_profile(audio, text, {})


def test_qwen3_engine_teardown_drops_the_model():
    engine = _engine_with_mock_model()
    engine.teardown()
    assert engine.model is None


@pytest.mark.integration
def test_qwen3_engine_loads_and_synthesizes(tmp_path):
    # Requires downloaded weights. Uses the Turbo built-in voice as a reference clip.
    import soundfile as sf

    turbo = ChatterboxTurboEngine()
    turbo.load()
    ref_text = "The quick brown fox jumps over the lazy dog, and then it runs away."
    ref = tmp_path / "ref.wav"
    sf.write(ref, turbo.synthesize(ref_text, None, {}), turbo.sample_rate())
    turbo.teardown()

    engine = Qwen3Engine()
    engine.load()
    audio = engine.synthesize("Hello world, this is a short test.", engine.clone_profile(str(ref), ref_text, {}), {})
    assert audio.dtype == np.float32 and len(audio) > engine.sample_rate()
    engine.teardown()

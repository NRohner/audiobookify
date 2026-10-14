from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from audiobookify.engines.base import detect_device
from audiobookify.engines.chatterbox import ChatterboxEngine


def test_detect_device_prefers_cuda_then_mps_then_cpu(monkeypatch):
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert detect_device() == "cuda"

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    assert detect_device() == "mps"

    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
    assert detect_device() == "cpu"


def test_chatterbox_engine_declares_cloning_support():
    engine = ChatterboxEngine()
    assert engine.supports_cloning is True
    assert engine.name == "chatterbox"


def test_chatterbox_engine_never_takes_very_short_chunks():
    engine = ChatterboxEngine()
    assert engine.min_chunk_chars() == 80
    assert engine.min_chunk_chars() < engine.max_chunk_chars()


def _patch_from_pretrained(monkeypatch, side_effect):
    """Replace ChatterboxTTS.from_pretrained with a recording stub. No weights needed."""
    import chatterbox.tts

    mock = MagicMock(side_effect=side_effect)
    monkeypatch.setattr(chatterbox.tts.ChatterboxTTS, "from_pretrained", mock)
    return mock


def test_chatterbox_engine_falls_back_to_cpu_when_the_requested_device_fails(monkeypatch):
    sentinel_model = SimpleNamespace(conds=None)  # real ChatterboxTTS always has .conds

    def loader(device):
        if device != "cpu":
            raise RuntimeError(f"no {device} backend here")
        return sentinel_model

    mock = _patch_from_pretrained(monkeypatch, loader)

    engine = ChatterboxEngine()
    engine.load("mps")  # must not raise

    assert engine.device == "cpu"
    assert engine.model is sentinel_model
    assert mock.call_count == 2
    assert [c.kwargs["device"] for c in mock.call_args_list] == ["mps", "cpu"]


def test_chatterbox_engine_reraises_when_cpu_itself_fails(monkeypatch):
    mock = _patch_from_pretrained(monkeypatch, RuntimeError("cpu load failed"))

    engine = ChatterboxEngine()
    with pytest.raises(RuntimeError, match="cpu load failed"):
        engine.load("cpu")

    assert mock.call_count == 1  # no second attempt, no infinite fallback loop


def _engine_with_mock_model():
    """Engine whose model records calls; prepare_conditionals stores a per-path marker in .conds."""
    engine = ChatterboxEngine()
    engine.model = MagicMock()
    engine.model.conds = "default-voice"
    engine._default_conds = "default-voice"
    engine.model.prepare_conditionals.side_effect = (
        lambda path, exaggeration: setattr(engine.model, "conds", f"conds:{path}"))
    used = []
    def generate(text, **kwargs):
        used.append(engine.model.conds)
        return MagicMock(**{"squeeze.return_value.cpu.return_value.numpy.return_value": np.zeros(5, dtype=np.float32)})
    engine.model.generate.side_effect = generate
    return engine, used


def test_chatterbox_engine_prepares_the_voice_once_per_profile_not_per_chunk():
    engine, used = _engine_with_mock_model()

    handle = engine.clone_profile("/refs/narrator.wav", "transcript text", {"exaggeration": 0.7})
    for text in ("One.", "Two.", "Three."):
        audio = engine.synthesize(text, handle, {"exaggeration": 0.7, "cfg": 0.3, "temperature": 0.9})

    assert isinstance(audio, np.ndarray)
    engine.model.prepare_conditionals.assert_called_once_with("/refs/narrator.wav", exaggeration=0.7)
    assert used == ["conds:/refs/narrator.wav"] * 3
    engine.model.generate.assert_called_with(
        "Three.", audio_prompt_path=None, exaggeration=0.7, cfg_weight=0.3, temperature=0.9,
    )


def test_chatterbox_engine_switches_voices_between_profiles():
    engine, used = _engine_with_mock_model()
    a = engine.clone_profile("/refs/a.wav", None, {})
    b = engine.clone_profile("/refs/b.wav", None, {})

    for handle in (a, b, a):
        engine.synthesize("Hi.", handle, {})

    assert used == ["conds:/refs/a.wav", "conds:/refs/b.wav", "conds:/refs/a.wav"]
    assert engine.model.prepare_conditionals.call_count == 2


def test_chatterbox_engine_without_reference_uses_the_built_in_voice_even_after_another_voice():
    engine, used = _engine_with_mock_model()
    narrator = engine.clone_profile("/refs/narrator.wav", None, {})
    default = engine.clone_profile(None, None, {})

    engine.synthesize("Hi.", narrator, {})
    engine.synthesize("Hi.", default, {})

    assert used == ["conds:/refs/narrator.wav", "default-voice"]


@pytest.mark.integration
def test_chatterbox_engine_loads_and_synthesizes_on_cpu():
    # Requires the real chatterbox-tts package + downloaded weights (`make models`).
    # Run explicitly with: pytest tests/test_engines_chatterbox.py -m integration
    engine = ChatterboxEngine()
    engine.load("cpu")
    handle = engine.clone_profile(None, None, {})
    audio = engine.synthesize("Hello world.", handle, {})
    assert len(audio) > 0
    engine.teardown()

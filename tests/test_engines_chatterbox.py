from unittest.mock import MagicMock

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


def _patch_from_pretrained(monkeypatch, side_effect):
    """Replace ChatterboxTTS.from_pretrained with a recording stub. No weights needed."""
    import chatterbox.tts

    mock = MagicMock(side_effect=side_effect)
    monkeypatch.setattr(chatterbox.tts.ChatterboxTTS, "from_pretrained", mock)
    return mock


def test_chatterbox_engine_falls_back_to_cpu_when_the_requested_device_fails(monkeypatch):
    sentinel_model = object()

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

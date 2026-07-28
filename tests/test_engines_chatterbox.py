import shutil

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

from unittest.mock import MagicMock

import numpy as np
import pytest
import soundfile as sf
import torch

from audiobookify.phoneme.wav2vec2 import Wav2Vec2PhonemeExtractor


def _patch_transformers(monkeypatch, decoded_phonemes="k ə d eɪ ɹ n"):
    """Replace Wav2Vec2ForCTC/Wav2Vec2Processor.from_pretrained with stubs. No weights needed."""
    import transformers

    mock_model = MagicMock()
    mock_model.to.return_value = mock_model
    mock_model.return_value.logits = torch.zeros(1, 1, 5)

    mock_inputs = MagicMock()
    mock_processor = MagicMock(return_value=mock_inputs)
    mock_processor.batch_decode.return_value = [decoded_phonemes]

    monkeypatch.setattr(
        transformers.Wav2Vec2ForCTC, "from_pretrained", MagicMock(return_value=mock_model)
    )
    monkeypatch.setattr(
        transformers.Wav2Vec2Processor, "from_pretrained", MagicMock(return_value=mock_processor)
    )
    return mock_model, mock_processor


def test_wav2vec2_extractor_loads_model_and_processor_on_requested_device(monkeypatch):
    mock_model, mock_processor = _patch_transformers(monkeypatch)

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")

    assert extractor.model is mock_model
    assert extractor.processor is mock_processor
    assert extractor.device == "cpu"


def test_wav2vec2_extractor_extract_returns_decoded_phoneme_string(tmp_path, monkeypatch):
    _patch_transformers(monkeypatch, decoded_phonemes="k ə d eɪ ɹ n")

    clip_path = tmp_path / "clip.wav"
    sf.write(str(clip_path), np.zeros(1600, dtype="float32"), 16000)

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")
    phonemes = extractor.extract(str(clip_path))

    assert phonemes == "k ə d eɪ ɹ n"


def test_wav2vec2_extractor_skips_resampling_when_already_16khz(tmp_path, monkeypatch):
    _, mock_processor = _patch_transformers(monkeypatch)

    clip_path = tmp_path / "clip.wav"
    sf.write(str(clip_path), np.zeros(1600, dtype="float32"), 16000)

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")
    extractor.extract(str(clip_path))

    passed_audio = mock_processor.call_args.args[0]
    assert len(passed_audio) == 1600


def test_wav2vec2_extractor_resamples_when_native_rate_differs(tmp_path, monkeypatch):
    _, mock_processor = _patch_transformers(monkeypatch)

    clip_path = tmp_path / "clip.wav"
    sf.write(str(clip_path), np.zeros(4410, dtype="float32"), 44100)  # 0.1s at 44.1kHz

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")
    extractor.extract(str(clip_path))

    passed_audio = mock_processor.call_args.args[0]
    # 0.1s resampled to 16kHz should land near 1600 samples, not the original 4410
    assert abs(len(passed_audio) - 1600) < 50


def test_wav2vec2_extractor_teardown_clears_model_and_processor(monkeypatch):
    _patch_transformers(monkeypatch)

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")
    extractor.teardown()

    assert extractor.model is None
    assert extractor.processor is None


@pytest.mark.integration
def test_wav2vec2_extractor_runs_end_to_end_on_a_real_clip(tmp_path):
    # Requires the real transformers weights (`make models`).
    # Run explicitly with: pytest tests/test_phoneme_wav2vec2.py -m integration
    clip_path = tmp_path / "clip.wav"
    sf.write(str(clip_path), (0.01 * np.random.randn(8000)).astype("float32"), 16000)

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")
    phonemes = extractor.extract(str(clip_path))
    extractor.teardown()

    assert isinstance(phonemes, str)

import numpy as np
import pytest
import soundfile as sf

from audiobookify.voice.validation import validate_reference_audio


def _write_wav(path, samples, sample_rate, channels=1):
    if channels > 1:
        samples = np.tile(samples.reshape(-1, 1), (1, channels))
    sf.write(str(path), samples, sample_rate)


def _clean_tone(total_duration_s, sample_rate=22050, amplitude=0.6):
    """A tone followed by a silent tail (~10% of the clip) - like real speech
    with a pause, so the noise-floor check has a genuine quiet passage to find."""
    n_total = int(total_duration_s * sample_rate)
    n_silence = int(n_total * 0.1)
    n_tone = n_total - n_silence
    t = np.linspace(0, n_tone / sample_rate, n_tone, endpoint=False)
    tone = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    silence = np.zeros(n_silence, dtype=np.float32)
    return np.concatenate([tone, silence])


def test_validate_reference_audio_reports_rate_channels_and_duration(tmp_path):
    path = tmp_path / "clean.wav"
    samples = _clean_tone(15.0)
    _write_wav(path, samples, 22050)

    info = validate_reference_audio(str(path))

    assert info.sample_rate == 22050
    assert info.channels == 1
    assert info.duration_seconds == pytest.approx(15.0, rel=1e-3)
    assert info.warnings == []


def test_validate_reference_audio_raises_for_too_short_clip(tmp_path):
    path = tmp_path / "tiny.wav"
    _write_wav(path, np.zeros(int(0.3 * 22050), dtype=np.float32), 22050)

    with pytest.raises(ValueError, match="too short"):
        validate_reference_audio(str(path))


def test_validate_reference_audio_raises_for_unreadable_file(tmp_path):
    path = tmp_path / "does-not-exist.wav"

    with pytest.raises(ValueError, match="could not read"):
        validate_reference_audio(str(path))


def test_validate_reference_audio_warns_when_shorter_than_recommended(tmp_path):
    path = tmp_path / "short.wav"
    _write_wav(path, _clean_tone(5.0), 22050)

    info = validate_reference_audio(str(path))

    assert any("shorter than the recommended" in w for w in info.warnings)


def test_validate_reference_audio_warns_when_longer_than_recommended(tmp_path):
    path = tmp_path / "long.wav"
    _write_wav(path, _clean_tone(40.0), 22050)

    info = validate_reference_audio(str(path))

    assert any("longer than the recommended" in w for w in info.warnings)


def test_validate_reference_audio_warns_on_stereo(tmp_path):
    path = tmp_path / "stereo.wav"
    _write_wav(path, _clean_tone(15.0), 22050, channels=2)

    info = validate_reference_audio(str(path))

    assert info.channels == 2
    assert any("channels" in w for w in info.warnings)


def test_validate_reference_audio_warns_on_clipping(tmp_path):
    path = tmp_path / "clipped.wav"
    samples = _clean_tone(15.0)
    samples[:1000] = 1.0  # force a run of full-scale samples
    _write_wav(path, samples, 22050)

    info = validate_reference_audio(str(path))

    assert any("clipping" in w for w in info.warnings)


def test_validate_reference_audio_warns_on_constant_background_noise(tmp_path):
    path = tmp_path / "noisy.wav"
    rng = np.random.default_rng(42)
    samples = rng.normal(0, 0.05, int(15.0 * 22050)).astype(np.float32)
    _write_wav(path, samples, 22050)

    info = validate_reference_audio(str(path))

    assert any("background noise" in w for w in info.warnings)


def test_validate_reference_audio_warns_on_low_sample_rate(tmp_path):
    path = tmp_path / "low_rate.wav"
    _write_wav(path, _clean_tone(15.0, sample_rate=8000), 8000)

    info = validate_reference_audio(str(path))

    assert any("sample rate" in w for w in info.warnings)

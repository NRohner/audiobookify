import numpy as np

from audiobookify.engines.fake import FakeEngine


def test_fake_engine_full_lifecycle():
    engine = FakeEngine()
    engine.load("cpu")

    handle = engine.clone_profile(None, None, {})
    short_audio = engine.synthesize("Hi.", handle, {})
    long_audio = engine.synthesize("This is a much longer sentence than the other one.", handle, {})

    assert isinstance(short_audio, np.ndarray)
    assert len(long_audio) > len(short_audio)
    assert 0 < engine.min_chunk_chars() < engine.max_chunk_chars()
    assert engine.sample_rate() > 0
    assert engine.preferred_phoneme_scheme() is None

    engine.teardown()  # must not raise

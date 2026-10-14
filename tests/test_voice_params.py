from audiobookify.models import VoiceProfile
from audiobookify.voice.validation import params_from_profile


def test_params_from_profile_maps_profile_fields_to_engine_params():
    profile = VoiceProfile(
        id=1, name="Nova", engine="chatterbox", reference_audio_path="/clips/nova.wav",
        reference_transcript="Hello there.", exaggeration=0.6, cfg_weight=0.4,
        temperature=0.9, created_at=0.0,
    )

    params = params_from_profile(profile)

    assert params == {
        "reference_audio": "/clips/nova.wav",
        "reference_text": "Hello there.",
        "reference_trim_end": None,
        "exaggeration": 0.6,
        "cfg": 0.4,
        "temperature": 0.9,
    }


def test_params_from_profile_points_engines_at_the_trimmed_clip(tmp_path):
    import numpy as np
    import soundfile as sf

    ref = tmp_path / "reference.wav"
    sf.write(ref, np.zeros(24000 * 20, dtype=np.float32), 24000)
    profile = VoiceProfile(
        id=1, name="Nova", engine="chatterbox", reference_audio_path=str(ref),
        reference_transcript="First words.", exaggeration=0.5, cfg_weight=0.5,
        temperature=0.8, created_at=0.0, reference_trim_end=14.95,
    )

    params = params_from_profile(profile)

    assert params["reference_audio"] == str(tmp_path / "reference.trim-14.95s.wav")
    assert params["reference_trim_end"] == 14.95  # part of the chunk-cache key

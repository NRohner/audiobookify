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
        "exaggeration": 0.6,
        "cfg": 0.4,
        "temperature": 0.9,
    }

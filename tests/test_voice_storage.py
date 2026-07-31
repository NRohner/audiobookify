from pathlib import Path

import pytest

import audiobookify.voice.storage as storage
from audiobookify.voice.storage import (
    delete_voice_storage,
    permanent_voice_dir,
    store_reference_audio,
)


@pytest.fixture(autouse=True)
def _isolate_voices_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "VOICES_DIR", str(tmp_path / "voices"))


def test_permanent_voice_dir_includes_id_and_sanitized_name():
    result = permanent_voice_dir(3, 'Nova: "The Narrator"')

    assert result.name == "3_Nova The Narrator"


def test_permanent_voice_dir_is_collision_free_for_same_name_different_ids():
    first = permanent_voice_dir(1, "Nova")
    second = permanent_voice_dir(2, "Nova")

    assert first != second


def test_store_reference_audio_copies_file_into_permanent_location(tmp_path):
    staged = tmp_path / "staged" / "clip.wav"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"fake wav bytes")

    result = store_reference_audio(str(staged), profile_id=5, name="Nova")

    assert result.is_file()
    assert result.read_bytes() == b"fake wav bytes"
    assert result.parent == permanent_voice_dir(5, "Nova")


def test_store_reference_audio_preserves_extension(tmp_path):
    staged = tmp_path / "staged" / "clip.mp3"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"fake mp3 bytes")

    result = store_reference_audio(str(staged), profile_id=5, name="Nova")

    assert result.suffix == ".mp3"
    assert result.name == "reference.mp3"


def test_delete_voice_storage_removes_the_directory(tmp_path):
    staged = tmp_path / "staged" / "clip.wav"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"fake wav bytes")
    store_reference_audio(str(staged), profile_id=7, name="Nova")
    assert permanent_voice_dir(7, "Nova").is_dir()

    delete_voice_storage(7, "Nova")

    assert not permanent_voice_dir(7, "Nova").exists()


def test_delete_voice_storage_is_a_noop_for_a_profile_with_no_storage():
    delete_voice_storage(999, "Nobody")  # must not raise

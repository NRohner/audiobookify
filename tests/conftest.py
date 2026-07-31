import pytest

import audiobookify.synth.chunk_cache as chunk_cache
import audiobookify.voice.storage as voice_storage


@pytest.fixture(autouse=True)
def _isolate_chunk_cache(tmp_path, monkeypatch):
    # synthesize_chapter unconditionally writes each chunk to CHUNK_CACHE_DIR as it
    # goes (Task 3) - without this, every test that calls it would write real files
    # into this repo's actual .cache/synth/ directory instead of a throwaway tmp_path.
    monkeypatch.setattr(chunk_cache, "CHUNK_CACHE_DIR", str(tmp_path / ".cache" / "synth"))


@pytest.fixture(autouse=True)
def _isolate_voices_dir(tmp_path, monkeypatch):
    # Database.__init__ runs a migration that copies any voice profile's reference
    # audio into VOICES_DIR if it isn't already there - without this, any test in
    # ANY test file that creates a voice profile (not just the ones that already
    # monkeypatch this explicitly) would write real files into this repo's actual
    # voices/ directory instead of a throwaway tmp_path.
    monkeypatch.setattr(voice_storage, "VOICES_DIR", str(tmp_path / "voices"))

import pytest

import audiobookify.synth.chunk_cache as chunk_cache


@pytest.fixture(autouse=True)
def _isolate_chunk_cache(tmp_path, monkeypatch):
    # synthesize_chapter unconditionally writes each chunk to CHUNK_CACHE_DIR as it
    # goes (Task 3) - without this, every test that calls it would write real files
    # into this repo's actual .cache/synth/ directory instead of a throwaway tmp_path.
    monkeypatch.setattr(chunk_cache, "CHUNK_CACHE_DIR", str(tmp_path / ".cache" / "synth"))

# Audiobookify — Voice Reference Audio Safety & Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every cloned voice's reference audio a permanent, organized home under a
project-root `voices/` directory — copied there immediately on creation, migrated there
for existing profiles — so a voice profile's only copy of its source audio is never
sitting in the transient `uploads/` staging area where a reasonable cleanup could
silently delete it.

**Architecture:** A new `src/audiobookify/voice/storage.py` module owns the
`voices/<profile-id>_<sanitized-name>/reference<ext>` layout (copy-in, delete-on-remove
— no new logic, reuses the existing `sanitize_filename` helper). `POST /api/voices`
copies the validated upload into permanent storage right after the profile row is
created (needs the assigned id for the folder name), repoints
`reference_audio_path`, then deletes the staging directory. `DELETE
/api/voices/{id}` removes the permanent folder alongside the DB row. A one-time,
additive migration in `Database.__init__` walks every existing profile whose
`reference_audio_path` doesn't already live under `voices/` and migrates it the same
way — skipping (with a logged warning, not a crash) any profile whose recorded source
file no longer exists.

**Tech Stack:** No new dependencies — `shutil` (stdlib) for copy/cleanup, reusing
`postprocess.audio.sanitize_filename` (already a dependency-free helper in this repo).

**Reference:** `docs/superpowers/specs/2026-07-30-audiobookify-voice-reference-safety-design.md`
(approved design spec — read it for full rationale) and
`docs/superpowers/plans/2026-07-30-audiobookify-generation-control.md` (this project's
most recent plan/task conventions, including the module-constant +
monkeypatch-in-tests pattern for redirecting filesystem writes).

## Global Constraints

- **No new dependencies, Python or JS.**
- **No new database columns.** `reference_audio_path` is reused as-is — the migration
  only ever rewrites its VALUE, never touches the schema.
- **Copy, never symlink, never leave the upload in `uploads/`.** After
  `POST /api/voices` succeeds, there must be zero lingering copy of the reference
  audio anywhere under `uploads/` — the permanent copy under `voices/` is the only
  one.
- **The migration must never crash app startup.** A profile whose recorded source
  file no longer exists is skipped with a logged warning, not an exception — one
  missing file must not block every other profile from migrating.
- **This is a backend-only sub-project.** No frontend changes — the design spec has no
  "Frontend changes" section, and nothing here is user-facing beyond the reliability
  improvement itself.
- **Testing conventions (established through the prior two sub-projects, keep
  following them):** `tmp_path` for all filesystem/DB tests; module-level constants
  that point at real filesystem locations (like `BACKUP_OUTPUT_DIR`, `CHUNK_CACHE_DIR`)
  get monkeypatched to a `tmp_path` location in tests, referenced via the owning
  module (`import ... as storage_module; monkeypatch.setattr(storage_module,
  "VOICES_DIR", ...)`) rather than imported as a bare name anywhere that needs to
  observe the patched value at call time — a bare `from .voice.storage import
  VOICES_DIR` import would freeze a stale copy at import time that monkeypatching
  couldn't reach.
- **No new frontend test framework** (not applicable — no frontend work in this
  sub-project).

---

## File Structure

```
audiobookify/
  src/audiobookify/
    voice/
      storage.py                    # new: VOICES_DIR, permanent_voice_dir,
                                     #   store_reference_audio, delete_voice_storage
    db.py                           # + voice import, update_voice_profile_reference_audio_path,
                                     #   _migrate_voice_reference_audio (runs in __init__)
    api/app.py                      # create_voice/delete_voice wired to voice/storage.py
  tests/
    test_voice_storage.py           # new: storage.py unit tests
    test_db.py                      # + update method tests, migration tests
    test_api.py                     # + create/delete voice storage-location tests,
                                     #   new file-scoped autouse VOICES_DIR isolation fixture
```

---

### Task 1: `voice/storage.py` module

**Files:**
- Create: `src/audiobookify/voice/storage.py`
- Test: `tests/test_voice_storage.py` (new)

**Interfaces:**
- Consumes: `sanitize_filename` (`audiobookify.postprocess.audio`, already exists).
- Produces: `VOICES_DIR: str` (module constant, `audiobookify.voice.storage`,
  `<repo-root>/voices`). `permanent_voice_dir(profile_id: int, name: str) -> Path`.
  `store_reference_audio(staged_path: str, profile_id: int, name: str) -> Path`
  (copies the file, creates the folder if needed, returns the new path).
  `delete_voice_storage(profile_id: int, name: str) -> None` (safe to call on a
  profile with no storage folder at all). Task 2 (`db.py`'s migration) and Task 3
  (`api/app.py`) both consume all four directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voice_storage.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_voice_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.voice.storage'`

- [ ] **Step 3: Write `src/audiobookify/voice/storage.py`**

```python
import shutil
from pathlib import Path

from ..postprocess.audio import sanitize_filename

VOICES_DIR = str(Path(__file__).resolve().parents[3] / "voices")


def permanent_voice_dir(profile_id: int, name: str) -> Path:
    return Path(VOICES_DIR) / f"{profile_id}_{sanitize_filename(name)}"


def store_reference_audio(staged_path: str, profile_id: int, name: str) -> Path:
    dest_dir = permanent_voice_dir(profile_id, name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(staged_path).suffix
    dest_path = dest_dir / f"reference{suffix}"
    shutil.copy2(staged_path, dest_path)
    return dest_path


def delete_voice_storage(profile_id: int, name: str) -> None:
    shutil.rmtree(permanent_voice_dir(profile_id, name), ignore_errors=True)
```

(`parents[3]` from `src/audiobookify/voice/storage.py`: `parents[0]`=`voice`,
`[1]`=`audiobookify`, `[2]`=`src`, `[3]`=repo root — the same depth as
`postprocess/audio.py`'s `BACKUP_OUTPUT_DIR` and `synth/chunk_cache.py`'s
`CHUNK_CACHE_DIR`, both one directory level under `src/audiobookify/`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_voice_storage.py -v`
Expected: all pass (6)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (272 + 6 = 278), 2 deselected

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/voice/storage.py tests/test_voice_storage.py
git commit -m "feat: permanent per-voice storage folder module"
```

---

### Task 2: Database — `update_voice_profile_reference_audio_path` and the migration

**Files:**
- Modify: `src/audiobookify/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `permanent_voice_dir`/`store_reference_audio`/`VOICES_DIR` (Task 1, via
  `from .voice import storage as voice_storage` — imported as a module, not individual
  names, specifically so tests can monkeypatch `voice_storage.VOICES_DIR` and have
  `db.py`'s migration observe the patched value).
- Produces: `Database.update_voice_profile_reference_audio_path(profile_id: int,
  reference_audio_path: str) -> None` (raises `KeyError` for an unknown id). A new
  migration, `_migrate_voice_reference_audio`, called from `Database.__init__`
  alongside the existing migration calls. Task 3 (`api/app.py`) consumes
  `update_voice_profile_reference_audio_path` directly.

- [ ] **Step 1: Write the failing tests**

Change the top of `tests/test_db.py` — find the existing import line for models
(`from audiobookify.models import SourceFormat, ChapterStatus, JobStatus`) and add a
new import right after it:

```python
import audiobookify.voice.storage as voice_storage
```

Append to `tests/test_db.py`:

```python
def test_update_voice_profile_reference_audio_path_updates_the_path(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    profile = db.create_voice_profile(
        name="Nova", engine="chatterbox", reference_audio_path=str(tmp_path / "old.wav"),
        reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8,
    )

    db.update_voice_profile_reference_audio_path(profile.id, str(tmp_path / "new.wav"))

    assert db.get_voice_profile_by_id(profile.id).reference_audio_path == str(tmp_path / "new.wav")
    db.close()


def test_update_voice_profile_reference_audio_path_raises_keyerror_for_missing_id(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    with pytest.raises(KeyError):
        db.update_voice_profile_reference_audio_path(999, "/some/path.wav")
    db.close()


def test_existing_voice_profile_gets_migrated_to_permanent_voices_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_storage, "VOICES_DIR", str(tmp_path / "voices"))
    source_audio = tmp_path / "original-upload" / "clip.wav"
    source_audio.parent.mkdir(parents=True)
    source_audio.write_bytes(b"fake audio bytes")

    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.create_voice_profile(
        name="Nova", engine="chatterbox", reference_audio_path=str(source_audio),
        reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8,
    )
    db.close()

    reopened = Database(db_path)  # a fresh open triggers the migration

    profile = reopened.find_voice_profile_by_name("Nova")
    assert profile.reference_audio_path.startswith(str((tmp_path / "voices").resolve()))
    assert Path(profile.reference_audio_path).is_file()
    reopened.close()


def test_migration_skips_a_voice_profile_whose_source_file_no_longer_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_storage, "VOICES_DIR", str(tmp_path / "voices"))
    missing_path = str(tmp_path / "does-not-exist.wav")

    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.create_voice_profile(
        name="Ghost", engine="chatterbox", reference_audio_path=missing_path,
        reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8,
    )
    db.close()

    reopened = Database(db_path)  # must not raise even though the source file is gone

    profile = reopened.find_voice_profile_by_name("Ghost")
    assert profile.reference_audio_path == missing_path  # left unchanged, nothing to migrate
    reopened.close()
```

Also add `from pathlib import Path` to `tests/test_db.py`'s imports if not already
present (check the top of the file first — if it's already there, skip this).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v -k "reference_audio_path or migrated_to_permanent or migration_skips"`
Expected: FAIL — `AttributeError: 'Database' object has no attribute
'update_voice_profile_reference_audio_path'` (and the migration tests fail because the
profile's path is never rewritten, since no migration exists yet).

- [ ] **Step 3: Update `src/audiobookify/db.py`**

Change the imports from:

```python
import sqlite3
import time
from typing import List, Optional

from .models import (
    Book, Chapter, ChapterStatus, Job, JobStatus, LexiconEntry, SourceFormat, TextClip, VoiceProfile,
)
from .ingest.base import RawChapter
```

to:

```python
import sqlite3
import sys
import time
from pathlib import Path
from typing import List, Optional

from .models import (
    Book, Chapter, ChapterStatus, Job, JobStatus, LexiconEntry, SourceFormat, TextClip, VoiceProfile,
)
from .ingest.base import RawChapter
from .voice import storage as voice_storage
```

Add the migration function right after `_migrate_cancel_requested_column`:

```python
def _migrate_voice_reference_audio(conn: sqlite3.Connection) -> None:
    voices_root = str(Path(voice_storage.VOICES_DIR).resolve())
    rows = conn.execute("SELECT id, name, reference_audio_path FROM voice_profiles").fetchall()
    for row in rows:
        current_path = row["reference_audio_path"]
        if current_path.startswith(voices_root):
            continue
        if not Path(current_path).is_file():
            print(
                f"Warning: voice profile {row['id']} ({row['name']})'s reference audio "
                f"'{current_path}' no longer exists - skipping migration for this profile",
                file=sys.stderr,
            )
            continue
        new_path = voice_storage.store_reference_audio(current_path, row["id"], row["name"])
        conn.execute(
            "UPDATE voice_profiles SET reference_audio_path = ? WHERE id = ?",
            (str(new_path), row["id"]),
        )
```

Change `Database.__init__` from:

```python
        self.conn.executescript(SCHEMA)
        _migrate_lexicon_entries_columns(self.conn)
        _migrate_fallback_columns(self.conn)
        _migrate_cancel_requested_column(self.conn)
        self.conn.commit()
```

to:

```python
        self.conn.executescript(SCHEMA)
        _migrate_lexicon_entries_columns(self.conn)
        _migrate_fallback_columns(self.conn)
        _migrate_cancel_requested_column(self.conn)
        _migrate_voice_reference_audio(self.conn)
        self.conn.commit()
```

Add the new method after `get_voice_profile_by_id` (right before `delete_voice_profile`):

```python
    def update_voice_profile_reference_audio_path(self, profile_id: int,
                                                    reference_audio_path: str) -> None:
        cur = self.conn.execute(
            "UPDATE voice_profiles SET reference_audio_path = ? WHERE id = ?",
            (reference_audio_path, profile_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"no voice profile with id {profile_id}")
        self.conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: all pass (existing + 4 new)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (278 + 4 = 282), 2 deselected

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/db.py tests/test_db.py
git commit -m "feat: migrate existing voice profiles to permanent storage on startup"
```

---

### Task 3: Wire permanent storage into `create_voice`/`delete_voice`

**Files:**
- Modify: `src/audiobookify/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `store_reference_audio`/`delete_voice_storage` (Task 1),
  `Database.update_voice_profile_reference_audio_path`/`get_voice_profile_by_id`
  (Task 2 and earlier).
- Produces: `POST /api/voices`'s created profile always has `reference_audio_path`
  under `voices/`, never `uploads/`; the staging directory is deleted afterward.
  `DELETE /api/voices/{id}` removes the profile's permanent storage folder alongside
  the DB row.

- [ ] **Step 1: Write the failing tests**

Change the top of `tests/test_api.py` from:

```python
import io
import os
import shutil

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from audiobookify.api.app import create_app
from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, JobStatus, SourceFormat
from audiobookify.worker import run_worker_once

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"), upload_dir=str(tmp_path / "uploads"))
    return TestClient(app)
```

to:

```python
import io
import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

import audiobookify.voice.storage as voice_storage
from audiobookify.api.app import create_app
from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, JobStatus, SourceFormat
from audiobookify.worker import run_worker_once

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def _isolate_voices_dir(tmp_path, monkeypatch):
    # POST /api/voices now copies reference audio into voice_storage.VOICES_DIR - without
    # this, every test in this file that creates a voice would write into this repo's
    # real voices/ directory instead of a throwaway tmp_path.
    monkeypatch.setattr(voice_storage, "VOICES_DIR", str(tmp_path / "voices"))


def _client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"), upload_dir=str(tmp_path / "uploads"))
    return TestClient(app)
```

(`if not already imported` note: `pathlib.Path` was not previously imported at the top
of `tests/test_api.py` — this adds it. If a later diff shows it's already there, skip
re-adding it.)

Append to `tests/test_api.py`:

```python
def test_create_voice_stores_reference_audio_under_voices_dir_not_uploads(tmp_path):
    client = _client(tmp_path)

    created = client.post(
        "/api/voices",
        files={"file": ("ref.wav", _clean_tone_wav_bytes(), "audio/wav")},
        data={"name": "narrator-1"},
    ).json()

    db = Database(str(tmp_path / "test.db"))
    profile = db.get_voice_profile_by_id(created["id"])
    db.close()

    assert profile.reference_audio_path.startswith(str((tmp_path / "voices").resolve()))
    assert Path(profile.reference_audio_path).is_file()
    assert list((tmp_path / "uploads").iterdir()) == []  # staging dir cleaned up


def test_delete_voice_removes_the_permanent_voice_storage_folder(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/voices",
        files={"file": ("a.wav", _clean_tone_wav_bytes(), "audio/wav")},
        data={"name": "a"},
    ).json()
    db = Database(str(tmp_path / "test.db"))
    voice_dir = Path(db.get_voice_profile_by_id(created["id"]).reference_audio_path).parent
    db.close()
    assert voice_dir.is_dir()

    client.delete(f"/api/voices/{created['id']}")

    assert not voice_dir.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v -k "stores_reference_audio_under_voices_dir or removes_the_permanent_voice_storage"`
Expected: FAIL — `profile.reference_audio_path` still points into `uploads/`, and the
delete test fails since no permanent folder is created yet to be removed.

- [ ] **Step 3: Update `create_voice` and `delete_voice` in `src/audiobookify/api/app.py`**

Add the import — change:

```python
from ..voice.validation import validate_reference_audio
```

to:

```python
from ..voice.storage import delete_voice_storage, store_reference_audio
from ..voice.validation import validate_reference_audio
```

Change `create_voice` from:

```python
    @app.post("/api/voices")
    def create_voice(file: UploadFile = File(...), name: str = Form(...),
                      reference_transcript: Optional[str] = Form(None),
                      exaggeration: float = Form(0.5), cfg_weight: float = Form(0.5),
                      temperature: float = Form(0.8), db: Database = Depends(get_db)):
        staged_dir = Path(upload_dir) / uuid.uuid4().hex
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_path = staged_dir / Path(file.filename).name
        staged_path.write_bytes(file.file.read())

        try:
            info = validate_reference_audio(str(staged_path.resolve()))
        except ValueError as exc:
            shutil.rmtree(staged_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc))

        try:
            profile = db.create_voice_profile(
                name=name, engine="chatterbox", reference_audio_path=str(staged_path.resolve()),
                reference_transcript=reference_transcript, exaggeration=exaggeration,
                cfg_weight=cfg_weight, temperature=temperature,
            )
        except ValueError as exc:
            shutil.rmtree(staged_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc))

        return _voice_to_dict(profile, warnings=info.warnings)
```

to:

```python
    @app.post("/api/voices")
    def create_voice(file: UploadFile = File(...), name: str = Form(...),
                      reference_transcript: Optional[str] = Form(None),
                      exaggeration: float = Form(0.5), cfg_weight: float = Form(0.5),
                      temperature: float = Form(0.8), db: Database = Depends(get_db)):
        staged_dir = Path(upload_dir) / uuid.uuid4().hex
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_path = staged_dir / Path(file.filename).name
        staged_path.write_bytes(file.file.read())

        try:
            info = validate_reference_audio(str(staged_path.resolve()))
        except ValueError as exc:
            shutil.rmtree(staged_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc))

        try:
            profile = db.create_voice_profile(
                name=name, engine="chatterbox", reference_audio_path=str(staged_path.resolve()),
                reference_transcript=reference_transcript, exaggeration=exaggeration,
                cfg_weight=cfg_weight, temperature=temperature,
            )
        except ValueError as exc:
            shutil.rmtree(staged_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc))

        permanent_path = store_reference_audio(str(staged_path.resolve()), profile.id, profile.name)
        db.update_voice_profile_reference_audio_path(profile.id, str(permanent_path))
        shutil.rmtree(staged_dir, ignore_errors=True)
        profile = db.get_voice_profile_by_id(profile.id)

        return _voice_to_dict(profile, warnings=info.warnings)
```

Change `delete_voice` from:

```python
    @app.delete("/api/voices/{voice_id}")
    def delete_voice(voice_id: int, db: Database = Depends(get_db)):
        try:
            db.delete_voice_profile(voice_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="voice profile not found")
        return {"ok": True}
```

to:

```python
    @app.delete("/api/voices/{voice_id}")
    def delete_voice(voice_id: int, db: Database = Depends(get_db)):
        profile = db.get_voice_profile_by_id(voice_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="voice profile not found")
        db.delete_voice_profile(voice_id)
        delete_voice_storage(voice_id, profile.name)
        return {"ok": True}
```

(`get_voice_profile_by_id` returns `Optional[VoiceProfile]`, not a `KeyError`-raising
lookup — fetching the profile first, before deleting, is what makes the profile's
`name` available to compute its storage folder path in `delete_voice_storage`. This
also means `delete_voice`'s 404 no longer relies on `db.delete_voice_profile`'s own
`KeyError`, which is still raised for any other direct caller of that method but is no
longer reachable from this route now that existence is checked up front.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: all pass (existing + 2 new) — including all pre-existing voice tests
(`test_create_voice_uploads_reference_audio_and_returns_profile`,
`test_create_voice_rejects_duplicate_name`, `test_list_voices_returns_all_profiles`,
`test_delete_voice_removes_profile`, `test_delete_voice_returns_404_for_unknown_id`),
which need no code changes themselves — the new autouse `_isolate_voices_dir` fixture
covers them automatically.

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (282 + 2 = 284), 2 deselected

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/api/app.py tests/test_api.py
git commit -m "feat: create_voice/delete_voice use permanent per-voice storage"
```

---

## Self-Review Notes

- **Spec coverage:** "copy, don't just reference" + "delete the staging copy" →
  Task 3. "One folder per voice under `voices/`" → Task 1. "Existing profiles get
  migrated too" (including the observed-stale-path tolerance) → Task 2. "No new
  columns" → confirmed, `reference_audio_path` is the only column touched anywhere in
  this plan, only its VALUE changes. The design spec's explicit "out of scope" items
  (per-voice generated sample clips, output-path-safety, generation-control) are
  untouched by every task above.
- **Placeholder scan:** no TBD/TODO markers; every step has complete, runnable code.
- **Type consistency:** `store_reference_audio`/`permanent_voice_dir` consistently
  return `Path` (not `str`) from their first definition (Task 1) through every later
  caller (Task 2's migration, Task 3's `create_voice`) — callers that need a string
  for a DB column explicitly wrap with `str(...)`, matching how the rest of this
  codebase already handles `Path`-vs-`str` at DB boundaries (e.g. `output_dir`
  handling in `api/app.py`). `delete_voice_storage(profile_id: int, name: str) ->
  None` keeps the same two-positional-argument shape everywhere it's called (Task 1's
  own tests, Task 3's `delete_voice`).

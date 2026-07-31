# Audiobookify — Generation Control (Pause / Cancel / Resume) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user pause generation globally, cancel a specific chapter or a whole
book's queued/in-flight work, and resume later without losing already-synthesized
audio chunks — so long-running generation survives being interrupted intentionally or
by a crash.

**Architecture:** A new `worker_state` table holds a single global pause flag; `jobs`
gains a `cancel_requested` flag and a new `CANCELLED` status. A new
`synth/chunk_cache.py` module persists each synthesized chunk to
`<repo-root>/.cache/synth/chapter-<id>/` as it's produced (keyed by a hash of the
post-lexicon text + chunk size + voice params, so a lexicon/voice change invalidates a
stale cache). `synthesize_chapter` resumes from cached chunks and raises one of two new
control-flow exceptions (`GenerationPaused`, `GenerationCancelled`) from inside the
existing `on_chunk_done` callback when the worker observes a pause/cancel signal;
`worker.py` catches those and translates them into the right `Job`/`Chapter`
transition — `synthesize_chapter` itself never touches `Job` rows or pause state,
consistent with the existing pipeline/worker boundary. New API endpoints expose
pause/resume/status and per-chapter/per-book cancel; the frontend gets a global
pause toggle and cancel buttons reusing the existing 2-second polling.

**Tech Stack:** No new dependencies — `hashlib`/`json` (stdlib) for the cache key,
`soundfile` (already a dependency) for chunk WAV files, `shutil` (stdlib) for cache
cleanup.

**Reference:** `docs/superpowers/specs/2026-07-30-audiobookify-generation-control-design.md`
(approved design spec — read it for full rationale) and
`docs/superpowers/plans/2026-07-30-audiobookify-output-path-safety.md` (this project's
most recent plan/task conventions, including the module-constant + monkeypatch pattern
for redirecting filesystem writes in tests).

## Global Constraints

- **No new dependencies, Python or JS.**
- **Cancelled/paused chapters revert to `ChapterStatus.PENDING`.** No new
  `ChapterStatus` value is added — every part of the UI/API that already understands
  "not yet generated" keeps working unchanged.
- **`synthesize_chapter` never touches `Job` rows or pause/cancel state directly.** It
  only raises `GenerationPaused`/`GenerationCancelled` (defined in
  `synth/pipeline.py`) from inside the `on_chunk_done` callback it's given — the
  callback's implementation (in `worker.py`) is the only place that reads
  `db.is_paused()`/`db.is_job_cancel_requested()`, and `worker.py`'s
  `_process_next_chapter_job` is the only place that translates the exception into a
  `Job`/`Chapter` state transition.
- **Pause is global, cancel is per-chapter and per-book.** No per-book pause state.
- **The chunk cache lives under `<repo-root>/.cache/synth/`, never under the
  user-supplied `output_dir`** — chunk cache durability must never depend on the same
  writability the output-path-safety sub-project already guards separately.
- **Testing conventions (established through the output-path-safety sub-project, keep
  following them):** `FakeEngine` backs every non-integration test; `requires_ffmpeg =
  pytest.mark.skipif(shutil.which("ffmpeg") is None, ...)` marks any test whose path
  reaches `export_chapter_mp3`; module-level constants that point at real filesystem
  locations (like `BACKUP_OUTPUT_DIR`) get monkeypatched to a `tmp_path` location in
  tests rather than passed as function parameters — the new `CHUNK_CACHE_DIR` constant
  follows the same pattern, via a new `tests/conftest.py` autouse fixture (Task 2)
  since, unlike the fallback-only `BACKUP_OUTPUT_DIR`, chunk caching is unconditional
  and would otherwise touch this repo's real `.cache/` directory on every single test
  that calls `synthesize_chapter`.
- **Pausing/cancelling `TextClip` generation is out of scope.** `_process_next_text_clip`
  is not touched by this plan.
- **No new frontend test framework** (established precedent) — Tasks 6 and 7 are
  verified by `npm run build` succeeding plus manual/controller browser verification,
  not automated tests.

---

## File Structure

```
audiobookify/
  src/audiobookify/
    models.py                     # + JobStatus.CANCELLED
    db.py                         # + worker_state table, is_paused/set_paused,
                                   #   jobs.cancel_requested column + migration,
                                   #   request_job_cancel/is_job_cancel_requested/requeue_job
    synth/
      chunk_cache.py               # new: CHUNK_CACHE_DIR, compute_cache_key,
                                   #   chapter_cache_dir, load_cached_chunks, save_chunk,
                                   #   write_manifest, clear_cache
      pipeline.py                  # + GenerationPaused/GenerationCancelled, synthesize_chapter
                                   #   rewritten for chunk-cache-aware resume
    worker.py                      # run_worker_once pause-aware;
                                   # _process_next_chapter_job's on_chunk_done raises
                                   # pause/cancel signals, except-clause handles them
    api/app.py                     # + worker pause/resume/status, chapter/book cancel endpoints
  frontend/
    src/lib/api.js                 # + getWorkerStatus/pauseWorker/resumeWorker/cancelChapter/cancelBook
    src/components/AppShell.jsx    # + global pause/resume toggle in the header
    src/pages/BookDetailPage.jsx   # + per-chapter cancel button, "Cancel all" button
  .gitignore                       # + .cache/
  tests/
    conftest.py                    # new: autouse fixture isolating CHUNK_CACHE_DIR to tmp_path
    test_db.py                     # + worker_state, cancel_requested, requeue_job tests
    test_chunk_cache.py            # new: chunk_cache module unit tests
    test_pipeline.py               # + resume/stale-cache/cleanup/reraise tests
    test_worker.py                 # + pause/cancel-mid-chapter tests
    test_api.py                    # + worker pause/resume/status, cancel endpoint tests
```

---

### Task 1: Data model — pause flag, cancel flag, `CANCELLED` status

**Files:**
- Modify: `src/audiobookify/models.py`
- Modify: `src/audiobookify/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: existing `Database` class, `SCHEMA` string, existing migration pattern
  (`_migrate_fallback_columns`).
- Produces: `JobStatus.CANCELLED` (`audiobookify.models`).
  `Database.is_paused() -> bool`, `Database.set_paused(paused: bool) -> None`,
  `Database.request_job_cancel(job_id: int) -> None`,
  `Database.is_job_cancel_requested(job_id: int) -> bool` (raises `KeyError` for an
  unknown job id), `Database.requeue_job(job_id: int) -> None` (sets a job back to
  `QUEUED` and clears `started_at`, for the pause case — distinct from `finish_job`,
  which is only for terminal statuses). Task 4 (worker) consumes all of these directly;
  Task 5 (API) consumes `is_paused`/`set_paused`/`request_job_cancel`/`JobStatus.CANCELLED`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_is_paused_defaults_to_false(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    assert db.is_paused() is False
    db.close()


def test_set_paused_toggles_the_flag(tmp_path):
    db = Database(str(tmp_path / "test.db"))

    db.set_paused(True)
    assert db.is_paused() is True

    db.set_paused(False)
    assert db.is_paused() is False
    db.close()


def test_is_job_cancel_requested_defaults_to_false(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    job = db.create_job(chapter.id)

    assert db.is_job_cancel_requested(job.id) is False
    db.close()


def test_request_job_cancel_sets_the_flag(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    job = db.create_job(chapter.id)

    db.request_job_cancel(job.id)

    assert db.is_job_cancel_requested(job.id) is True
    db.close()


def test_requeue_job_resets_status_and_clears_started_at(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    job = db.create_job(chapter.id)
    db.pop_next_queued_job()  # -> generating, started_at set

    db.requeue_job(job.id)

    requeued = db.get_job(job.id)
    assert requeued.status == JobStatus.QUEUED
    assert requeued.started_at is None
    db.close()


def test_existing_pre_cancel_database_gains_cancel_requested_column_via_migration(tmp_path):
    # Simulates opening a database file created before this change (no
    # cancel_requested column on jobs) - the migration in Database.__init__ must add
    # it without raising.
    import sqlite3

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            progress REAL,
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL,
            error TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)  # must not raise
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    job = db.create_job(chapter.id)
    assert db.is_job_cancel_requested(job.id) is False
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v -k "paused or cancel_requested or requeue"`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'is_paused'` (and
similarly for the other new methods).

- [ ] **Step 3: Add `JobStatus.CANCELLED` to `src/audiobookify/models.py`**

Change:

```python
class JobStatus(str, Enum):
    QUEUED = "queued"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"
```

to:

```python
class JobStatus(str, Enum):
    QUEUED = "queued"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

- [ ] **Step 4: Update `src/audiobookify/db.py`**

Add the `worker_state` table to `SCHEMA`, right after the `jobs` table's closing `);`
and before `text_clips`:

```sql

CREATE TABLE IF NOT EXISTS worker_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    paused INTEGER NOT NULL DEFAULT 0
);
```

Add `cancel_requested INTEGER NOT NULL DEFAULT 0` to the `jobs` table in `SCHEMA`.
Change:

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    error TEXT
);
```

to:

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0
);
```

Add the migration function right after `_migrate_fallback_columns`:

```python
def _migrate_cancel_requested_column(conn: sqlite3.Connection) -> None:
    job_cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "cancel_requested" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
```

Change `Database.__init__` from:

```python
        self.conn.executescript(SCHEMA)
        _migrate_lexicon_entries_columns(self.conn)
        _migrate_fallback_columns(self.conn)
        self.conn.commit()
```

to:

```python
        self.conn.executescript(SCHEMA)
        _migrate_lexicon_entries_columns(self.conn)
        _migrate_fallback_columns(self.conn)
        _migrate_cancel_requested_column(self.conn)
        self.conn.commit()
```

Add these methods at the end of the `Database` class, after `reset_stale_generating_text_clips`:

```python
    def is_paused(self) -> bool:
        row = self.conn.execute("SELECT paused FROM worker_state WHERE id = 1").fetchone()
        return bool(row["paused"]) if row else False

    def set_paused(self, paused: bool) -> None:
        self.conn.execute(
            "INSERT INTO worker_state (id, paused) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET paused = excluded.paused",
            (int(paused),),
        )
        self.conn.commit()

    def request_job_cancel(self, job_id: int) -> None:
        self.conn.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
        self.conn.commit()

    def is_job_cancel_requested(self, job_id: int) -> bool:
        row = self.conn.execute(
            "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no job with id {job_id}")
        return bool(row["cancel_requested"])

    def requeue_job(self, job_id: int) -> None:
        self.conn.execute(
            "UPDATE jobs SET status = ?, started_at = NULL WHERE id = ?",
            (JobStatus.QUEUED.value, job_id),
        )
        self.conn.commit()
```

(`worker_state`'s single-row-by-construction upsert relies on the `CHECK (id = 1)`
constraint plus SQLite's `ON CONFLICT(id) DO UPDATE` — no separate seed/migration step
needed, since `CREATE TABLE IF NOT EXISTS` alone is sufficient for both a brand-new
database and one opened for the first time after this change.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: all pass (existing + 6 new)

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (234 + 6 = 240), 2 deselected

- [ ] **Step 7: Commit**

```bash
git add src/audiobookify/models.py src/audiobookify/db.py tests/test_db.py
git commit -m "feat: worker_state pause flag, job cancel_requested flag, CANCELLED status"
```

---

### Task 2: Chunk cache module

**Files:**
- Create: `src/audiobookify/synth/chunk_cache.py`
- Create: `tests/conftest.py`
- Modify: `.gitignore`
- Test: `tests/test_chunk_cache.py` (new)

**Interfaces:**
- Consumes: `soundfile` (already a dependency).
- Produces: `CHUNK_CACHE_DIR: str` (module constant, `audiobookify.synth.chunk_cache`,
  `<repo-root>/.cache/synth`). `compute_cache_key(text: str, max_chunk_chars: int,
  voice_params: dict) -> str`. `chapter_cache_dir(chapter_id: int) -> Path`.
  `load_cached_chunks(chapter_id: int, cache_key: str) -> List[np.ndarray]` (empty list
  if no cache or a stale/mismatched key). `save_chunk(chapter_id: int, index: int,
  audio: np.ndarray, sample_rate: int) -> None`. `write_manifest(chapter_id: int,
  cache_key: str) -> None`. `clear_cache(chapter_id: int) -> None` (safe to call on a
  chapter with no cache dir at all). Task 3 (`synthesize_chapter`) consumes all of
  these directly.

- [ ] **Step 1: Create `tests/conftest.py`**

```python
import pytest

import audiobookify.synth.chunk_cache as chunk_cache


@pytest.fixture(autouse=True)
def _isolate_chunk_cache(tmp_path, monkeypatch):
    # synthesize_chapter unconditionally writes each chunk to CHUNK_CACHE_DIR as it
    # goes (Task 3) - without this, every test that calls it would write real files
    # into this repo's actual .cache/synth/ directory instead of a throwaway tmp_path.
    monkeypatch.setattr(chunk_cache, "CHUNK_CACHE_DIR", str(tmp_path / ".cache" / "synth"))
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_chunk_cache.py`:

```python
import numpy as np
import pytest
import soundfile as sf

import audiobookify.synth.chunk_cache as chunk_cache
from audiobookify.synth.chunk_cache import (
    chapter_cache_dir,
    clear_cache,
    compute_cache_key,
    load_cached_chunks,
    save_chunk,
    write_manifest,
)


def test_compute_cache_key_is_deterministic_for_the_same_inputs():
    key1 = compute_cache_key("Hello.", 300, {"voice_name": "fake"})
    key2 = compute_cache_key("Hello.", 300, {"voice_name": "fake"})
    assert key1 == key2


def test_compute_cache_key_differs_when_text_changes():
    key1 = compute_cache_key("Hello.", 300, {"voice_name": "fake"})
    key2 = compute_cache_key("Goodbye.", 300, {"voice_name": "fake"})
    assert key1 != key2


def test_compute_cache_key_differs_when_voice_params_change():
    key1 = compute_cache_key("Hello.", 300, {"voice_name": "fake"})
    key2 = compute_cache_key("Hello.", 300, {"voice_name": "other"})
    assert key1 != key2


def test_save_chunk_and_load_cached_chunks_round_trips(tmp_path):
    chunk0 = np.linspace(-0.5, 0.5, 1000, dtype=np.float32)
    chunk1 = np.linspace(0.5, -0.5, 500, dtype=np.float32)
    key = compute_cache_key("Hello.", 300, {})

    save_chunk(1, 0, chunk0, sample_rate=22050)
    save_chunk(1, 1, chunk1, sample_rate=22050)
    write_manifest(1, key)

    loaded = load_cached_chunks(1, key)

    assert len(loaded) == 2
    np.testing.assert_allclose(loaded[0], chunk0, atol=1e-4)
    np.testing.assert_allclose(loaded[1], chunk1, atol=1e-4)


def test_load_cached_chunks_returns_empty_when_no_cache_exists(tmp_path):
    assert load_cached_chunks(999, compute_cache_key("x", 300, {})) == []


def test_load_cached_chunks_returns_empty_when_cache_key_does_not_match(tmp_path):
    chunk0 = np.zeros(100, dtype=np.float32)
    save_chunk(2, 0, chunk0, sample_rate=22050)
    write_manifest(2, compute_cache_key("old text", 300, {}))

    result = load_cached_chunks(2, compute_cache_key("new text", 300, {}))

    assert result == []


def test_clear_cache_removes_the_directory(tmp_path):
    save_chunk(3, 0, np.zeros(10, dtype=np.float32), sample_rate=22050)
    assert chapter_cache_dir(3).is_dir()

    clear_cache(3)

    assert not chapter_cache_dir(3).exists()


def test_clear_cache_is_a_noop_for_a_chapter_with_no_cache():
    clear_cache(999)  # must not raise
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chunk_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.synth.chunk_cache'`

- [ ] **Step 4: Write `src/audiobookify/synth/chunk_cache.py`**

```python
import hashlib
import json
import shutil
from pathlib import Path
from typing import List

import numpy as np
import soundfile as sf

CHUNK_CACHE_DIR = str(Path(__file__).resolve().parents[3] / ".cache" / "synth")


def compute_cache_key(text: str, max_chunk_chars: int, voice_params: dict) -> str:
    payload = json.dumps(
        {"text": text, "max_chunk_chars": max_chunk_chars, "voice_params": voice_params},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def chapter_cache_dir(chapter_id: int) -> Path:
    return Path(CHUNK_CACHE_DIR) / f"chapter-{chapter_id}"


def load_cached_chunks(chapter_id: int, cache_key: str) -> List[np.ndarray]:
    cache_dir = chapter_cache_dir(chapter_id)
    manifest_path = cache_dir / "manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if manifest.get("cache_key") != cache_key:
        return []

    chunks = []
    i = 0
    while True:
        chunk_path = cache_dir / f"chunk-{i:04d}.wav"
        if not chunk_path.is_file():
            break
        audio, _ = sf.read(str(chunk_path), dtype="float32")
        chunks.append(audio)
        i += 1
    return chunks


def save_chunk(chapter_id: int, index: int, audio: np.ndarray, sample_rate: int) -> None:
    cache_dir = chapter_cache_dir(chapter_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    sf.write(str(cache_dir / f"chunk-{index:04d}.wav"), audio, sample_rate)


def write_manifest(chapter_id: int, cache_key: str) -> None:
    cache_dir = chapter_cache_dir(chapter_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "manifest.json").write_text(json.dumps({"cache_key": cache_key}))


def clear_cache(chapter_id: int) -> None:
    shutil.rmtree(chapter_cache_dir(chapter_id), ignore_errors=True)
```

- [ ] **Step 5: Add `.cache/` to `.gitignore`**

Change:

```
.run/
output/
```

to:

```
.run/
output/
.cache/
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_chunk_cache.py -v`
Expected: all pass (8)

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (240 + 8 = 248), 2 deselected — the new `tests/conftest.py`
autouse fixture must not break any existing test (it only redirects a constant that no
existing code reads yet).

- [ ] **Step 8: Commit**

```bash
git add src/audiobookify/synth/chunk_cache.py tests/conftest.py tests/test_chunk_cache.py .gitignore
git commit -m "feat: per-chunk synthesis cache for mid-chapter resume"
```

---

### Task 3: `synthesize_chapter` chunk-cache integration and control-flow exceptions

**Files:**
- Modify: `src/audiobookify/synth/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `compute_cache_key`/`load_cached_chunks`/`save_chunk`/`write_manifest`/
  `clear_cache`/`chapter_cache_dir` (Task 2).
- Produces: `GenerationPaused(Exception)`, `GenerationCancelled(Exception)`
  (`audiobookify.synth.pipeline`) — raised only by a caller-supplied `on_chunk_done`
  callback (never by `synthesize_chapter` itself), and re-raised by
  `synthesize_chapter` rather than being caught into `ChapterStatus.FAILED`.
  `synthesize_chapter`'s signature is unchanged. Task 4 (worker) imports both
  exception classes, raises them from its own `on_chunk_done` implementation, and
  catches them around its call to `synthesize_chapter`.

- [ ] **Step 1: Write the failing tests**

Change the top of `tests/test_pipeline.py` from:

```python
import os
import shutil

import pytest

import audiobookify.synth.pipeline as pipeline
from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, JobStatus, SourceFormat
from audiobookify.synth.pipeline import generate_book, synthesize_chapter, synthesize_text_clip
```

to:

```python
import os
import shutil

import pytest

import audiobookify.synth.chunk_cache as chunk_cache
import audiobookify.synth.pipeline as pipeline
from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, JobStatus, SourceFormat
from audiobookify.synth.pipeline import generate_book, synthesize_chapter, synthesize_text_clip
```

Append to `tests/test_pipeline.py`:

```python
@requires_ffmpeg
def test_synthesize_chapter_writes_each_chunk_to_disk_as_it_is_synthesized(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    sentence1 = "Alpha " * 30 + "beta."
    sentence2 = "Gamma " * 30 + "delta."
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text=f"{sentence1} {sentence2}")])
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    seen = {}

    def check_disk_after_first_chunk(done, total):
        if done == 1:
            cache_dir = chunk_cache.chapter_cache_dir(chapter.id)
            seen["chunk_0_exists"] = (cache_dir / "chunk-0000.wav").is_file()
            seen["chunk_1_exists"] = (cache_dir / "chunk-0001.wav").is_file()

    result = synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
                                 on_chunk_done=check_disk_after_first_chunk)

    assert result.status == ChapterStatus.DONE
    assert seen == {"chunk_0_exists": True, "chunk_1_exists": False}
    db.close()


@requires_ffmpeg
def test_synthesize_chapter_resumes_from_cached_chunks_after_a_pause(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    sentence1 = "Alpha " * 30 + "beta."
    sentence2 = "Gamma " * 30 + "delta."
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text=f"{sentence1} {sentence2}")])
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    calls = []

    def pause_after_first_chunk(done, total):
        calls.append((done, total))
        if done == 1:
            raise pipeline.GenerationPaused()

    with pytest.raises(pipeline.GenerationPaused):
        synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
                            on_chunk_done=pause_after_first_chunk)

    assert calls == [(1, 2)]
    assert db.get_chapter(chapter.id).status == ChapterStatus.GENERATING

    class CountingEngine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.call_count = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.call_count += 1
            return super().synthesize(text_chunk, profile_handle, params)

    second_engine = CountingEngine()
    second_engine.load("cpu")
    second_profile_handle = second_engine.clone_profile(None, None, {})

    result = synthesize_chapter(chapter, book, second_engine, second_profile_handle,
                                 {"voice_name": "fake"}, [], db)

    assert result.status == ChapterStatus.DONE
    assert second_engine.call_count == 1  # only the second (uncached) chunk needed synthesis
    db.close()


@requires_ffmpeg
def test_synthesize_chapter_discards_stale_cache_when_the_lexicon_changes(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    [chapter] = db.list_chapters(book.id)
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    def raise_paused(done, total):
        raise pipeline.GenerationPaused()

    with pytest.raises(pipeline.GenerationPaused):
        synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
                            on_chunk_done=raise_paused)

    db.create_lexicon_entry(term="chapter", override_value="CHAP-ter", scope="global")
    lexicon_entries = db.list_lexicon_entries(book.id)

    class CountingEngine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.call_count = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.call_count += 1
            return super().synthesize(text_chunk, profile_handle, params)

    second_engine = CountingEngine()
    second_engine.load("cpu")
    second_profile_handle = second_engine.clone_profile(None, None, {})

    result = synthesize_chapter(chapter, book, second_engine, second_profile_handle,
                                 {"voice_name": "fake"}, lexicon_entries, db)

    assert result.status == ChapterStatus.DONE
    assert second_engine.call_count == 1  # the stale-key cache was discarded, not reused
    db.close()


@requires_ffmpeg
def test_synthesize_chapter_deletes_cache_dir_after_successful_export(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    [chapter] = db.list_chapters(book.id)
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    result = synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db)

    assert result.status == ChapterStatus.DONE
    assert not chunk_cache.chapter_cache_dir(chapter.id).exists()
    db.close()


def test_synthesize_chapter_reraises_generation_paused_instead_of_marking_failed(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    [chapter] = db.list_chapters(book.id)
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    def raise_paused(done, total):
        raise pipeline.GenerationPaused()

    with pytest.raises(pipeline.GenerationPaused):
        synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
                            on_chunk_done=raise_paused)

    assert db.get_chapter(chapter.id).status != ChapterStatus.FAILED
    db.close()


def test_synthesize_chapter_reraises_generation_cancelled_instead_of_marking_failed(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    [chapter] = db.list_chapters(book.id)
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    def raise_cancelled(done, total):
        raise pipeline.GenerationCancelled()

    with pytest.raises(pipeline.GenerationCancelled):
        synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
                            on_chunk_done=raise_cancelled)

    assert db.get_chapter(chapter.id).status != ChapterStatus.FAILED
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pipeline.py -v -k "chunk or paused or cancelled or resumes or stale"`
Expected: FAIL with `AttributeError: module 'audiobookify.synth.pipeline' has no
attribute 'GenerationPaused'` (and similarly for `GenerationCancelled`); the
disk/resume/stale-cache tests fail because no chunk is ever written to
`chunk_cache.chapter_cache_dir(...)`.

- [ ] **Step 3: Rewrite `synthesize_chapter` in `src/audiobookify/synth/pipeline.py`**

Change the imports from:

```python
from pathlib import Path
from typing import Callable, List, Optional

from ..db import Database
from ..lexicon.substitution import apply_lexicon
from ..models import Book, Chapter, ChapterStatus, JobStatus, TextClip
from ..normalize.text import chunk_text
from ..postprocess.audio import BACKUP_OUTPUT_DIR, concatenate_chunks, export_chapter_mp3, export_mp3
```

to:

```python
from pathlib import Path
from typing import Callable, List, Optional

from ..db import Database
from ..lexicon.substitution import apply_lexicon
from ..models import Book, Chapter, ChapterStatus, JobStatus, TextClip
from ..normalize.text import chunk_text
from ..postprocess.audio import BACKUP_OUTPUT_DIR, concatenate_chunks, export_chapter_mp3, export_mp3
from .chunk_cache import clear_cache, compute_cache_key, load_cached_chunks, save_chunk, write_manifest


class GenerationPaused(Exception):
    pass


class GenerationCancelled(Exception):
    pass
```

Change `synthesize_chapter` from:

```python
def synthesize_chapter(chapter: Chapter, book: Book, engine, profile_handle, voice_params: dict,
                        lexicon_entries: list, db: Database,
                        on_chunk_done: Optional[Callable[[int, int], None]] = None) -> Chapter:
    db.update_chapter_status(chapter.id, ChapterStatus.GENERATING)
    try:
        text = apply_lexicon(chapter.text, lexicon_entries)
        chunks = chunk_text(text, engine.max_chunk_chars())
        total = len(chunks)
        audio_chunks = []
        for i, c in enumerate(chunks):
            audio_chunks.append(engine.synthesize(c, profile_handle, voice_params))
            if on_chunk_done is not None:
                on_chunk_done(i + 1, total)
        combined = concatenate_chunks(audio_chunks, engine.sample_rate())
        mp3_path, used_fallback = export_chapter_mp3(
            combined, engine.sample_rate(), book.output_dir, chapter.order_index,
            chapter.title, book.title, voice_params.get("voice_name", engine.name),
            fallback_dir=str(Path(BACKUP_OUTPUT_DIR) / str(book.id)),
        )
        db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=mp3_path,
                                  used_fallback=used_fallback)
    except Exception as exc:
        db.update_chapter_status(chapter.id, ChapterStatus.FAILED, error=str(exc))

    return db.get_chapter(chapter.id)
```

to:

```python
def synthesize_chapter(chapter: Chapter, book: Book, engine, profile_handle, voice_params: dict,
                        lexicon_entries: list, db: Database,
                        on_chunk_done: Optional[Callable[[int, int], None]] = None) -> Chapter:
    db.update_chapter_status(chapter.id, ChapterStatus.GENERATING)
    try:
        text = apply_lexicon(chapter.text, lexicon_entries)
        chunks = chunk_text(text, engine.max_chunk_chars())
        total = len(chunks)

        cache_key = compute_cache_key(text, engine.max_chunk_chars(), voice_params)
        audio_chunks = load_cached_chunks(chapter.id, cache_key)
        if not audio_chunks:
            clear_cache(chapter.id)
            write_manifest(chapter.id, cache_key)

        for i in range(len(audio_chunks), total):
            chunk_audio = engine.synthesize(chunks[i], profile_handle, voice_params)
            save_chunk(chapter.id, i, chunk_audio, engine.sample_rate())
            audio_chunks.append(chunk_audio)
            if on_chunk_done is not None:
                on_chunk_done(i + 1, total)

        combined = concatenate_chunks(audio_chunks, engine.sample_rate())
        mp3_path, used_fallback = export_chapter_mp3(
            combined, engine.sample_rate(), book.output_dir, chapter.order_index,
            chapter.title, book.title, voice_params.get("voice_name", engine.name),
            fallback_dir=str(Path(BACKUP_OUTPUT_DIR) / str(book.id)),
        )
        db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=mp3_path,
                                  used_fallback=used_fallback)
        clear_cache(chapter.id)
    except (GenerationPaused, GenerationCancelled):
        raise
    except Exception as exc:
        db.update_chapter_status(chapter.id, ChapterStatus.FAILED, error=str(exc))

    return db.get_chapter(chapter.id)
```

(A genuine synthesis failure — the `except Exception` branch — deliberately does NOT
clear the chunk cache: any chunks already synthesized before the failure stay cached,
so retrying the same chapter later resumes from them instead of re-synthesizing
everything. `GenerationPaused`/`GenerationCancelled` are re-raised, not returned from —
the caller (Task 4's `worker.py`) is the one that knows how to translate each into a
`Job`/`Chapter` transition; this function doesn't reach its final `return
db.get_chapter(...)` line in that case, by design.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: all pass (existing 13 + 6 new = 19)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (248 + 6 = 254), 2 deselected

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/synth/pipeline.py tests/test_pipeline.py
git commit -m "feat: synthesize_chapter resumes from cached chunks, raises pause/cancel signals"
```

---

### Task 4: Worker — pause-aware polling, mid-chapter pause/cancel handling

**Files:**
- Modify: `src/audiobookify/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `Database.is_paused/is_job_cancel_requested/requeue_job/request_job_cancel`
  (Task 1), `GenerationPaused`/`GenerationCancelled` (Task 3),
  `clear_cache`/`chapter_cache_dir` (Task 2).
- Produces: `run_worker_once` now returns `False` immediately (without popping any
  job) whenever `db.is_paused()` is true. A chapter job interrupted mid-generation by
  a pause ends `QUEUED`/chapter `PENDING` with its chunk cache intact; one interrupted
  by a cancel ends `CANCELLED`/chapter `PENDING` with its chunk cache deleted. No
  public signature changes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_worker.py` (add `import audiobookify.synth.chunk_cache as
chunk_cache` and `from audiobookify.ingest.base import RawChapter` is already
imported; no other new imports needed):

```python
def test_run_worker_once_returns_false_when_paused(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter, job = _make_book_with_job(tmp_path, db)
    db.set_paused(True)
    engine = FakeEngine()
    engine.load("cpu")

    assert run_worker_once(db, engine, {}) is False
    assert db.get_job(job.id).status == JobStatus.QUEUED
    db.close()


@requires_ffmpeg
def test_run_worker_once_requeues_job_and_keeps_cache_when_paused_mid_chapter(tmp_path):
    import audiobookify.synth.chunk_cache as chunk_cache

    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    sentence1 = "Alpha " * 30 + "beta."
    sentence2 = "Gamma " * 30 + "delta."
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text=f"{sentence1} {sentence2}")])
    job = db.create_job(chapter.id)

    class PausesAfterFirstChunkEngine(FakeEngine):
        def __init__(self, db):
            super().__init__()
            self._db = db
            self.calls = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.calls += 1
            if self.calls == 1:
                self._db.set_paused(True)
            return super().synthesize(text_chunk, profile_handle, params)

    engine = PausesAfterFirstChunkEngine(db)
    engine.load("cpu")

    did_work = run_worker_once(db, engine, {})

    assert did_work is True
    assert db.get_job(job.id).status == JobStatus.QUEUED
    assert db.get_chapter(chapter.id).status == ChapterStatus.PENDING
    assert (chunk_cache.chapter_cache_dir(chapter.id) / "chunk-0000.wav").is_file()
    db.close()


@requires_ffmpeg
def test_run_worker_once_cancels_job_and_clears_cache_when_cancel_requested_mid_chapter(tmp_path):
    import audiobookify.synth.chunk_cache as chunk_cache

    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    sentence1 = "Alpha " * 30 + "beta."
    sentence2 = "Gamma " * 30 + "delta."
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text=f"{sentence1} {sentence2}")])
    job = db.create_job(chapter.id)

    class CancelsAfterFirstChunkEngine(FakeEngine):
        def __init__(self, db, job_id):
            super().__init__()
            self._db = db
            self._job_id = job_id
            self.calls = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.calls += 1
            if self.calls == 1:
                self._db.request_job_cancel(self._job_id)
            return super().synthesize(text_chunk, profile_handle, params)

    engine = CancelsAfterFirstChunkEngine(db, job.id)
    engine.load("cpu")

    did_work = run_worker_once(db, engine, {})

    assert did_work is True
    assert db.get_job(job.id).status == JobStatus.CANCELLED
    assert db.get_chapter(chapter.id).status == ChapterStatus.PENDING
    assert not chunk_cache.chapter_cache_dir(chapter.id).exists()
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_worker.py -v -k "paused or cancel"`
Expected: FAIL — the paused-queue test fails because `run_worker_once` still pops and
runs the job (`did_work` differs); the mid-chapter tests fail because
`PausesAfterFirstChunkEngine`/`CancelsAfterFirstChunkEngine`'s side effects
(`set_paused(True)`/`request_job_cancel(...)`) are never observed — the job still ends
`DONE`, not `QUEUED`/`CANCELLED`.

- [ ] **Step 3: Update `src/audiobookify/worker.py`**

Change the imports from:

```python
from .db import Database
from .models import ChapterStatus, JobStatus
from .synth.pipeline import synthesize_chapter, synthesize_text_clip
from .voice.validation import params_from_profile
```

to:

```python
from .db import Database
from .models import ChapterStatus, JobStatus
from .synth.chunk_cache import clear_cache
from .synth.pipeline import (
    GenerationCancelled, GenerationPaused, synthesize_chapter, synthesize_text_clip,
)
from .voice.validation import params_from_profile
```

Change `_process_next_chapter_job` from:

```python
def _process_next_chapter_job(db: Database, engine, profile_cache: dict) -> bool:
    job = db.pop_next_queued_job()
    if job is None:
        return False

    try:
        chapter = db.get_chapter(job.chapter_id)
        book = db.get_book(chapter.book_id)
        voice_params, profile = _voice_params_for_book(db, book)
        _ensure_profile_cached(engine, profile_cache, book.voice_profile_id, profile, voice_params)

        lexicon_entries = db.list_lexicon_entries(book.id)

        def on_chunk_done(done: int, total: int) -> None:
            db.update_job_progress(job.id, done / total)

        result = synthesize_chapter(chapter, book, engine, profile_cache["handle"], voice_params,
                                     lexicon_entries, db, on_chunk_done=on_chunk_done)

        status = JobStatus.DONE if result.status == ChapterStatus.DONE else JobStatus.FAILED
        db.finish_job(job.id, status, error=result.error)
    except Exception as exc:
        db.finish_job(job.id, JobStatus.FAILED, error=str(exc))

    return True
```

to:

```python
def _process_next_chapter_job(db: Database, engine, profile_cache: dict) -> bool:
    job = db.pop_next_queued_job()
    if job is None:
        return False

    try:
        chapter = db.get_chapter(job.chapter_id)
        book = db.get_book(chapter.book_id)
        voice_params, profile = _voice_params_for_book(db, book)
        _ensure_profile_cached(engine, profile_cache, book.voice_profile_id, profile, voice_params)

        lexicon_entries = db.list_lexicon_entries(book.id)

        def on_chunk_done(done: int, total: int) -> None:
            db.update_job_progress(job.id, done / total)
            if db.is_paused():
                raise GenerationPaused()
            if db.is_job_cancel_requested(job.id):
                raise GenerationCancelled()

        result = synthesize_chapter(chapter, book, engine, profile_cache["handle"], voice_params,
                                     lexicon_entries, db, on_chunk_done=on_chunk_done)

        status = JobStatus.DONE if result.status == ChapterStatus.DONE else JobStatus.FAILED
        db.finish_job(job.id, status, error=result.error)
    except GenerationPaused:
        db.update_chapter_status(job.chapter_id, ChapterStatus.PENDING)
        db.requeue_job(job.id)
    except GenerationCancelled:
        db.update_chapter_status(job.chapter_id, ChapterStatus.PENDING)
        db.finish_job(job.id, JobStatus.CANCELLED)
        clear_cache(job.chapter_id)
    except Exception as exc:
        db.finish_job(job.id, JobStatus.FAILED, error=str(exc))

    return True
```

Change `run_worker_once` from:

```python
def run_worker_once(db: Database, engine, profile_cache: dict,
                     text_clip_output_dir: str = DEFAULT_TEXT_CLIP_OUTPUT_DIR) -> bool:
    if _process_next_chapter_job(db, engine, profile_cache):
        return True
    return _process_next_text_clip(db, engine, profile_cache, text_clip_output_dir)
```

to:

```python
def run_worker_once(db: Database, engine, profile_cache: dict,
                     text_clip_output_dir: str = DEFAULT_TEXT_CLIP_OUTPUT_DIR) -> bool:
    if db.is_paused():
        return False
    if _process_next_chapter_job(db, engine, profile_cache):
        return True
    return _process_next_text_clip(db, engine, profile_cache, text_clip_output_dir)
```

(`run_forever`'s existing loop — `if not did_work: time.sleep(poll_interval)` — needs
no change: when paused, `run_worker_once` now returns `False` immediately, which
already triggers the existing poll-sleep behavior.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_worker.py -v`
Expected: all pass (existing 7 + 3 new = 10)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (254 + 3 = 257), 2 deselected

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/worker.py tests/test_worker.py
git commit -m "feat: worker pauses/cancels mid-chapter generation via the chunk-boundary checkpoint"
```

---

### Task 5: API — pause/resume/status, per-chapter and per-book cancel

**Files:**
- Modify: `src/audiobookify/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Database.is_paused/set_paused/latest_job_for_chapter/finish_job/
  request_job_cancel/update_chapter_status/list_chapters` (Tasks 1 and earlier),
  `JobStatus.CANCELLED` (Task 1).
- Produces: `GET /api/worker/status` → `{"paused": bool}`. `POST /api/worker/pause` →
  `{"paused": true}`. `POST /api/worker/resume` → `{"paused": false}`.
  `POST /api/books/{book_id}/chapters/{chapter_id}/cancel` → `{"ok": true}`.
  `POST /api/books/{book_id}/cancel` → `{"ok": true}`. Task 6 (frontend) calls the
  worker endpoints; Task 7 (frontend) calls the cancel endpoints.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_worker_status_reports_not_paused_by_default(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/worker/status")

    assert response.status_code == 200
    assert response.json() == {"paused": False}


def test_worker_pause_then_status_reports_paused(tmp_path):
    client = _client(tmp_path)

    pause_response = client.post("/api/worker/pause")
    status_response = client.get("/api/worker/status")

    assert pause_response.json() == {"paused": True}
    assert status_response.json() == {"paused": True}


def test_worker_resume_after_pause_reports_not_paused(tmp_path):
    client = _client(tmp_path)
    client.post("/api/worker/pause")

    resume_response = client.post("/api/worker/resume")
    status_response = client.get("/api/worker/status")

    assert resume_response.json() == {"paused": False}
    assert status_response.json() == {"paused": False}


def test_cancel_chapter_with_a_queued_job_marks_it_cancelled_and_resets_the_chapter(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    job = db.create_job(chapter.id)
    db.close()

    response = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/cancel")

    assert response.status_code == 200
    db = Database(str(tmp_path / "test.db"))
    assert db.get_job(job.id).status == JobStatus.CANCELLED
    assert db.get_chapter(chapter.id).status == ChapterStatus.PENDING
    db.close()


def test_cancel_chapter_with_a_generating_job_sets_cancel_requested_without_changing_status_yet(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    job = db.create_job(chapter.id)
    db.pop_next_queued_job()  # -> generating, simulating a worker mid-chapter
    db.close()

    response = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/cancel")

    assert response.status_code == 200
    db = Database(str(tmp_path / "test.db"))
    assert db.is_job_cancel_requested(job.id) is True
    assert db.get_job(job.id).status == JobStatus.GENERATING  # not yet transitioned - worker's job
    db.close()


def test_cancel_chapter_with_no_active_job_is_a_noop(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    db.close()

    response = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/cancel")

    assert response.status_code == 200


def test_cancel_chapter_returns_404_for_a_chapter_in_a_different_book(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book_a = db.create_book(
        title="A", author=None, source_path=str(tmp_path / "a.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    book_b = db.create_book(
        title="B", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter_b] = db.add_chapters(book_b.id, [RawChapter(title="Ch 1", text="Body.")])
    db.close()

    response = client.post(f"/api/books/{book_a.id}/chapters/{chapter_b.id}/cancel")

    assert response.status_code == 404


def test_cancel_book_cancels_every_chapter_with_an_active_job(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    chapters = db.add_chapters(book.id, [RawChapter(title=f"Ch {i}", text=f"Body {i}.")
                                          for i in range(2)])
    job0 = db.create_job(chapters[0].id)
    job1 = db.create_job(chapters[1].id)
    db.close()

    response = client.post(f"/api/books/{book.id}/cancel")

    assert response.status_code == 200
    db = Database(str(tmp_path / "test.db"))
    assert db.get_job(job0.id).status == JobStatus.CANCELLED
    assert db.get_job(job1.id).status == JobStatus.CANCELLED
    assert db.get_chapter(chapters[0].id).status == ChapterStatus.PENDING
    assert db.get_chapter(chapters[1].id).status == ChapterStatus.PENDING
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v -k "worker_status or worker_pause or worker_resume or cancel"`
Expected: FAIL — every new route 404s with FastAPI's own generic not-found response
(none of these routes exist yet).

- [ ] **Step 3: Add the new routes to `src/audiobookify/api/app.py`**

Add this module-level helper function right after `_check_writable`, before `def
create_app(...):`:

```python
def _cancel_active_job_for_chapter(db: Database, chapter) -> None:
    latest = db.latest_job_for_chapter(chapter.id)
    if latest is None or latest.status not in (JobStatus.QUEUED, JobStatus.GENERATING):
        return
    if latest.status == JobStatus.QUEUED:
        db.finish_job(latest.id, JobStatus.CANCELLED)
        db.update_chapter_status(chapter.id, ChapterStatus.PENDING)
    else:
        db.request_job_cancel(latest.id)
```

Add these routes inside `create_app`, after the `generate_chapter` route and before
`assign_voice`:

```python
    @app.get("/api/worker/status")
    def worker_status(db: Database = Depends(get_db)):
        return {"paused": db.is_paused()}

    @app.post("/api/worker/pause")
    def worker_pause(db: Database = Depends(get_db)):
        db.set_paused(True)
        return {"paused": True}

    @app.post("/api/worker/resume")
    def worker_resume(db: Database = Depends(get_db)):
        db.set_paused(False)
        return {"paused": False}

    @app.post("/api/books/{book_id}/chapters/{chapter_id}/cancel")
    def cancel_chapter(book_id: int, chapter_id: int, db: Database = Depends(get_db)):
        try:
            db.get_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="book not found")

        try:
            chapter = db.get_chapter(chapter_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="chapter not found")
        if chapter.book_id != book_id:
            raise HTTPException(status_code=404, detail="chapter not found")

        _cancel_active_job_for_chapter(db, chapter)
        return {"ok": True}

    @app.post("/api/books/{book_id}/cancel")
    def cancel_book(book_id: int, db: Database = Depends(get_db)):
        try:
            book = db.get_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="book not found")

        for chapter in db.list_chapters(book.id):
            _cancel_active_job_for_chapter(db, chapter)
        return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: all pass (existing + 8 new)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (257 + 8 = 265), 2 deselected

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/api/app.py tests/test_api.py
git commit -m "feat: pause/resume/status and per-chapter/per-book cancel endpoints"
```

---

### Task 6: Frontend — global pause/resume toggle

**Files:**
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/components/AppShell.jsx`

**Interfaces:**
- Consumes: `GET /api/worker/status`, `POST /api/worker/pause`,
  `POST /api/worker/resume` (Task 5).
- Produces: `api.getWorkerStatus()`, `api.pauseWorker()`, `api.resumeWorker()`
  (`frontend/src/lib/api.js`). A pause/resume `Button` visible in the app header on
  every page.

No automated tests for this task (established precedent — no frontend test
framework); verified via `npm run build` and manual/controller browser verification in
step 4.

- [ ] **Step 1: Add worker-status methods to `frontend/src/lib/api.js`**

Change:

```js
export const api = {
  listBooks: () => request("/api/books"),
  getBook: (id) => request(`/api/books/${id}`),
  createBook: (formData) => request("/api/books", { method: "POST", body: formData }),
  browseDir: (path) => request(`/api/browse-dir${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  validateOutputDir: (path) => request(`/api/validate-output-dir?path=${encodeURIComponent(path)}`),
  generateBook: (id) => request(`/api/books/${id}/generate`, { method: "POST" }),
```

to:

```js
export const api = {
  listBooks: () => request("/api/books"),
  getBook: (id) => request(`/api/books/${id}`),
  createBook: (formData) => request("/api/books", { method: "POST", body: formData }),
  browseDir: (path) => request(`/api/browse-dir${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  validateOutputDir: (path) => request(`/api/validate-output-dir?path=${encodeURIComponent(path)}`),
  getWorkerStatus: () => request("/api/worker/status"),
  pauseWorker: () => request("/api/worker/pause", { method: "POST" }),
  resumeWorker: () => request("/api/worker/resume", { method: "POST" }),
  generateBook: (id) => request(`/api/books/${id}/generate`, { method: "POST" }),
```

- [ ] **Step 2: Wire the toggle into `frontend/src/components/AppShell.jsx`**

Change:

```jsx
import { NavLink, Outlet } from "react-router-dom";
import { BookOpen, Home, Mic, SpellCheck2, Type } from "lucide-react";
import ThemeToggle from "./ThemeToggle.jsx";

const navItems = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/text", label: "Text", icon: Type },
  { to: "/voices", label: "Voices", icon: Mic },
  { to: "/pronunciations", label: "Pronunciations", icon: SpellCheck2 },
];

export default function AppShell() {
  return (
    <div className="min-h-dvh flex">
      <aside className="w-16 sm:w-60 shrink-0 border-r border-border bg-surface flex flex-col">
        <div className="flex items-center justify-center sm:justify-start gap-2 px-2 sm:px-5 h-16 border-b border-border">
          <BookOpen className="size-6 text-primary shrink-0" aria-hidden="true" />
          <span className="hidden sm:inline font-semibold text-lg">Audiobookify</span>
        </div>
        <nav className="flex-1 p-2 sm:p-3 flex flex-col gap-1">
          {navItems.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              title={label}
              aria-label={label}
              className={({ isActive }) =>
                `flex items-center justify-center sm:justify-start gap-3 rounded-lg px-0 sm:px-3 h-10 text-sm font-medium transition-colors duration-150
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`
              }
            >
              <Icon className="size-4.5 shrink-0" aria-hidden="true" />
              <span className="hidden sm:inline">{label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-16 border-b border-border flex items-center justify-end px-6">
          <ThemeToggle />
        </header>
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
```

to:

```jsx
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { BookOpen, Home, Mic, Pause, Play, SpellCheck2, Type } from "lucide-react";
import { api } from "../lib/api.js";
import Button from "./ui/Button.jsx";
import ThemeToggle from "./ThemeToggle.jsx";

const navItems = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/text", label: "Text", icon: Type },
  { to: "/voices", label: "Voices", icon: Mic },
  { to: "/pronunciations", label: "Pronunciations", icon: SpellCheck2 },
];

export default function AppShell() {
  const [paused, setPaused] = useState(false);
  const [toggling, setToggling] = useState(false);

  async function refreshStatus() {
    const status = await api.getWorkerStatus();
    setPaused(status.paused);
  }

  useEffect(() => {
    refreshStatus().catch(() => {});
    const id = setInterval(() => refreshStatus().catch(() => {}), 2000);
    return () => clearInterval(id);
  }, []);

  async function handleToggle() {
    setToggling(true);
    try {
      if (paused) {
        await api.resumeWorker();
      } else {
        await api.pauseWorker();
      }
      await refreshStatus();
    } finally {
      setToggling(false);
    }
  }

  return (
    <div className="min-h-dvh flex">
      <aside className="w-16 sm:w-60 shrink-0 border-r border-border bg-surface flex flex-col">
        <div className="flex items-center justify-center sm:justify-start gap-2 px-2 sm:px-5 h-16 border-b border-border">
          <BookOpen className="size-6 text-primary shrink-0" aria-hidden="true" />
          <span className="hidden sm:inline font-semibold text-lg">Audiobookify</span>
        </div>
        <nav className="flex-1 p-2 sm:p-3 flex flex-col gap-1">
          {navItems.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              title={label}
              aria-label={label}
              className={({ isActive }) =>
                `flex items-center justify-center sm:justify-start gap-3 rounded-lg px-0 sm:px-3 h-10 text-sm font-medium transition-colors duration-150
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`
              }
            >
              <Icon className="size-4.5 shrink-0" aria-hidden="true" />
              <span className="hidden sm:inline">{label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-16 border-b border-border flex items-center justify-end gap-3 px-6">
          <Button variant="secondary" onClick={handleToggle} loading={toggling}>
            {paused ? (
              <Play className="size-4" aria-hidden="true" />
            ) : (
              <Pause className="size-4" aria-hidden="true" />
            )}
            {paused ? "Resume generation" : "Pause generation"}
          </Button>
          <ThemeToggle />
        </header>
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Build verification**

Run: `cd frontend && npm run build`
Expected: success, no errors

- [ ] **Step 4: Manually verify in a running browser**

Run: `make dev`

In the browser: confirm the "Pause generation" button appears in the header on every
page (Home, Text, Voices, Pronunciations), click it, confirm it flips to "Resume
generation", click again, confirm it flips back — and confirm the label survives a
page navigation (proving the poll on mount correctly reflects the real backend state,
not just local click state).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.js frontend/src/components/AppShell.jsx
git commit -m "feat: global pause/resume toggle in the app header"
```

---

### Task 7: Frontend — per-chapter cancel and "Cancel all"

**Files:**
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/pages/BookDetailPage.jsx`

**Interfaces:**
- Consumes: `POST /api/books/{book_id}/chapters/{chapter_id}/cancel`,
  `POST /api/books/{book_id}/cancel` (Task 5).
- Produces: `api.cancelChapter(bookId, chapterId)`, `api.cancelBook(bookId)`
  (`frontend/src/lib/api.js`). A stop-icon button next to each chapter's existing
  generate button (enabled whenever that chapter is queued/generating), and a
  "Cancel all" button next to "Generate all" (enabled whenever any chapter is
  queued/generating).

No confirmation dialog for cancel (unlike this app's delete actions) — cancelling is
easily undone by clicking Generate again, it doesn't destroy data, and "Generate"
itself has no confirmation either.

No automated tests for this task (established precedent); verified via `npm run
build` and manual/controller browser verification in step 4.

- [ ] **Step 1: Add cancel methods to `frontend/src/lib/api.js`**

Change:

```js
  generateChapter: (bookId, chapterId) =>
    request(`/api/books/${bookId}/chapters/${chapterId}/generate`, { method: "POST" }),
```

to:

```js
  generateChapter: (bookId, chapterId) =>
    request(`/api/books/${bookId}/chapters/${chapterId}/generate`, { method: "POST" }),
  cancelChapter: (bookId, chapterId) =>
    request(`/api/books/${bookId}/chapters/${chapterId}/cancel`, { method: "POST" }),
  cancelBook: (bookId) => request(`/api/books/${bookId}/cancel`, { method: "POST" }),
```

- [ ] **Step 2: Add cancel handlers and buttons to `frontend/src/pages/BookDetailPage.jsx`**

Change the icon import from:

```jsx
import { AlertTriangle, ArrowLeft, Loader2, Play } from "lucide-react";
```

to:

```jsx
import { AlertTriangle, ArrowLeft, Loader2, Play, Square } from "lucide-react";
```

Add cancel-tracking state alongside the existing `generatingChapterIds` state. Change:

```jsx
  const [generating, setGenerating] = useState(false);
  const [generatingChapterIds, setGeneratingChapterIds] = useState(new Set());
  const [error, setError] = useState(null);
```

to:

```jsx
  const [generating, setGenerating] = useState(false);
  const [generatingChapterIds, setGeneratingChapterIds] = useState(new Set());
  const [cancelingChapterIds, setCancelingChapterIds] = useState(new Set());
  const [cancelingAll, setCancelingAll] = useState(false);
  const [error, setError] = useState(null);
```

Add the two cancel handlers after the existing `handleGenerateChapter`:

```jsx
  async function handleCancelChapter(chapterId) {
    setCancelingChapterIds((prev) => new Set(prev).add(chapterId));
    try {
      await api.cancelChapter(bookId, chapterId);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setCancelingChapterIds((prev) => {
        const next = new Set(prev);
        next.delete(chapterId);
        return next;
      });
    }
  }

  async function handleCancelAll() {
    setCancelingAll(true);
    try {
      await api.cancelBook(bookId);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setCancelingAll(false);
    }
  }
```

Change the "Generate all" button block from:

```jsx
      <div className="flex items-center gap-4 mb-6">
        <Button onClick={handleGenerate} loading={generating} disabled={anyInProgress || allDone}>
          <Play className="size-4" aria-hidden="true" />
          Generate all
        </Button>

        <label className="flex items-center gap-2 text-sm">
```

to:

```jsx
      <div className="flex items-center gap-4 mb-6">
        <Button onClick={handleGenerate} loading={generating} disabled={anyInProgress || allDone}>
          <Play className="size-4" aria-hidden="true" />
          Generate all
        </Button>
        <Button variant="secondary" onClick={handleCancelAll} loading={cancelingAll} disabled={!anyInProgress}>
          <Square className="size-4" aria-hidden="true" />
          Cancel all
        </Button>

        <label className="flex items-center gap-2 text-sm">
```

Add the per-chapter cancel button right before the existing per-chapter generate
button. Change:

```jsx
              <button
                onClick={() => handleGenerateChapter(chapter.id)}
                disabled={chapter.status === "done" || inProgress || generatingChapterIds.has(chapter.id)}
                aria-label={`Generate ${chapter.title}`}
                title={`Generate ${chapter.title}`}
                className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground
                  transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40
                  focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                {generatingChapterIds.has(chapter.id) ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Play className="size-4" aria-hidden="true" />
                )}
              </button>
```

to:

```jsx
              <button
                onClick={() => handleCancelChapter(chapter.id)}
                disabled={!inProgress || cancelingChapterIds.has(chapter.id)}
                aria-label={`Cancel ${chapter.title}`}
                title={`Cancel ${chapter.title}`}
                className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive
                  transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40
                  focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                {cancelingChapterIds.has(chapter.id) ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Square className="size-4" aria-hidden="true" />
                )}
              </button>
              <button
                onClick={() => handleGenerateChapter(chapter.id)}
                disabled={chapter.status === "done" || inProgress || generatingChapterIds.has(chapter.id)}
                aria-label={`Generate ${chapter.title}`}
                title={`Generate ${chapter.title}`}
                className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground
                  transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40
                  focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                {generatingChapterIds.has(chapter.id) ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Play className="size-4" aria-hidden="true" />
                )}
              </button>
```

- [ ] **Step 3: Build verification**

Run: `cd frontend && npm run build`
Expected: success, no errors

- [ ] **Step 4: Manually verify in a running browser**

Run: `make dev`

Import a book with a few chapters (or use one already imported). Queue several
chapters via "Generate all", then:
1. Click the stop icon on one still-queued chapter; confirm it goes back to
   `pending` within a couple of polling cycles.
2. Click "Cancel all" while chapters are queued/generating; confirm they all return
   to `pending`.
3. Click "Generate all" again and, while a chapter is actively `generating` (not
   merely queued), click that chapter's stop icon; confirm it takes a moment (waiting
   for the next chunk boundary) but the chapter does return to `pending` rather than
   staying stuck on `generating`.
4. Click the global "Pause generation" toggle from Task 6 while a chapter is
   `generating`; confirm that chapter finishes its current chunk, reverts to
   `pending`, and no further chapters start until "Resume generation" is clicked.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.js frontend/src/pages/BookDetailPage.jsx
git commit -m "feat: per-chapter cancel and cancel-all controls on the book detail page"
```

---

## Self-Review Notes

- **Spec coverage:** global pause (`worker_state`, `is_paused`/`set_paused`, the
  `run_worker_once` gate) → Tasks 1 and 4. Per-chapter/per-book cancel (`cancel_requested`,
  `request_job_cancel`, the two cancel endpoints, the two frontend cancel controls) →
  Tasks 1, 5, 7. True mid-chunk resume (the chunk cache module, its integration into
  `synthesize_chapter`, cache-key invalidation on lexicon/voice change, cleanup on
  success) → Tasks 2 and 3. Cancelled/paused chapters reverting to `pending` (not a new
  `ChapterStatus`) → Task 4's exception handlers. The spec's explicit out-of-scope
  items (per-chunk `Job` granularity in the API, `TextClip` pause/cancel) are
  deliberately untouched by every task above.
- **Placeholder scan:** no TBD/TODO markers; every step has complete, runnable code.
- **Type consistency:** `synthesize_chapter`'s public signature is unchanged across
  Tasks 3-4 (still `(chapter, book, engine, profile_handle, voice_params,
  lexicon_entries, db, on_chunk_done=None) -> Chapter`, raising instead of returning
  for the two new control-flow cases). `GenerationPaused`/`GenerationCancelled` are
  defined once (Task 3, `synth/pipeline.py`) and imported by name everywhere else that
  raises or catches them (Task 4's `worker.py`) — no duplicate exception class
  definitions. `chunk_cache`'s five functions keep identical signatures from their
  first definition (Task 2) through every later caller (Task 3's `synthesize_chapter`,
  Task 4's tests).

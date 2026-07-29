# Audiobookify M4 Sub-project 1 — Backend API + Worker-Process Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the M4 GUI architecture end-to-end — a FastAPI backend, a separate
worker process that owns the loaded TTS engine, SQLite as the sole coordination layer
between them, and a bare React frontend — by making "upload a book → generate it → see
a resulting MP3" work through the browser.

**Architecture:** A new `Job` table (`id, chapter_id, status, progress, created_at,
started_at, finished_at, error`) is the queue: the API creates `queued` rows, a
standalone `audiobookify worker` process polls for them, and `Chapter.status` (unchanged
from M0-M3) is written only by the worker at the same two points it writes the matching
`Job`. `synth/pipeline.py`'s per-chapter synthesis logic is extracted into
`synthesize_chapter()` so both `generate_book()` (CLI, unchanged behavior) and the worker
(new, with a chunk-level progress callback) can call it. A new `src/audiobookify/api/`
FastAPI app exposes book upload/list/detail/generate endpoints; a new `frontend/`
Vite+React app is a single bare page that uploads, polls, and triggers generation.

**Tech Stack:** `fastapi`, `uvicorn`, `python-multipart` (new Python deps — first since
M0); `react`, `react-dom`, `vite`, `@vitejs/plugin-react` (new JS deps, first for this
project). `pytest` + FastAPI's `TestClient` for API tests.

**Reference:** `docs/superpowers/specs/2026-07-29-audiobookify-m4-backend-worker-skeleton-design.md`
(approved design spec — read it for full rationale) and
`docs/superpowers/plans/2026-07-29-audiobookify-m3-audio-sample-pronunciation.md` (this
project's established plan/task conventions).

**Post-implementation addendum (final whole-branch review, all 9 tasks complete):**
found no Critical issues, but 3 real Important findings a per-task lens missed — (1)
`POST /api/books/{book_id}/generate` (Task 6) only checked `Job` status, never
`Chapter.status`, so calling it again on a fully-`done` book silently re-queued and
re-synthesized already-finished chapters, overwriting completed MP3s (reproduced
concretely, not hypothetical — this contradicted the CLI's own resumability contract
going back to M0); (2) the worker (Task 4) had no exception boundary around
chapter/book lookup, voice-profile-cache resolution, or lexicon lookup, so any
unexpected exception there killed the entire worker process rather than just failing
one job — not reachable through today's API surface, but would become reachable once
later GUI sub-projects add chapter/profile mutation, and directly undercut the
"separate worker process" architecture decision's own stated rationale
(crash-isolation from the API); (3) `db.py`'s `sqlite3.connect` used the default
`check_same_thread=True`, a residual risk given the API's sync `Depends(get_db)` +
sync-route pattern relies on FastAPI's threadpool reuse landing dependency-resolution
and route-body execution on the same thread — true today but not a documented
guarantee. All three fixed in one wave and re-reviewed clean: `generate` now also
skips `ChapterStatus.DONE` chapters; `run_worker_once` wraps its post-pop body in
try/except (marking the claimed Job `FAILED` with the real error on any exception) and
`run_forever` wraps the `run_worker_once` call as a last-resort safety net; `db.py`
now uses `check_same_thread=False`. Also fixed as part of the same wave: orphaned
staged-upload directories on `ingest_by_path` failure (Task 5) are now cleaned up.
Final count: 184 tests passing, 2 integration tests deselected.

## Global Constraints

- **New Python dependencies limited to `fastapi`, `uvicorn`, `python-multipart`.** No
  ORM, no async database driver, no task queue/broker package (Celery, RQ, Redis, etc.)
  — SQLite is the only coordination mechanism between the API and worker processes.
- **New JS dependencies limited to `react`, `react-dom`, `vite`, `@vitejs/plugin-react`.**
  No router, no state-management library, no icon library, no UI kit for this bare
  skeleton.
- **`Chapter.status` semantics are unchanged.** Still `pending/generating/done/failed`;
  no new value is added to `ChapterStatus`. Only the worker writes it, and only at the
  same two transition points where it writes the corresponding `Job` row.
- **`generate_book()`'s external behavior must not change.** All 5 existing tests in
  `tests/test_pipeline.py` must pass unmodified after the `synthesize_chapter()`
  extraction — this is a pure internal refactor.
- **Testing conventions (established M0-M3, keep following them):** `FakeEngine`
  backs every non-integration test that touches an engine; `requires_ffmpeg =
  pytest.mark.skipif(shutil.which("ffmpeg") is None, ...)` marks any test whose path
  reaches `export_chapter_mp3`; engine/class references stay monkeypatchable as
  `cli.ChatterboxEngine` (and, new in this plan, `cli.worker.run_forever`); `tmp_path`
  for all database/filesystem tests.
- **No emojis anywhere in the frontend** (standing UI rule) — this skeleton doesn't add
  icons at all yet, so this is trivially satisfied, but don't introduce any.
- **No new integration test is needed for this plan.** Nothing here adds a new
  `from_pretrained`-style heavy ML call (the worker reuses the existing
  `ChatterboxEngine`/`FakeEngine` pair); the existing Chatterbox/wav2vec2 integration
  tests remain sufficient coverage for that surface.

---

## File Structure

```
audiobookify/
  src/audiobookify/
    models.py                     # + Job, JobStatus
    db.py                         # + jobs table/CRUD, list_books, get_book KeyError fix, WAL pragmas
    synth/pipeline.py             # + synthesize_chapter(), generate_book() now calls it
    ingest/__init__.py            # + ingest_by_path()
    voice/validation.py           # + params_from_profile()
    worker.py                     # new: run_worker_once(), run_forever()
    cli.py                        # + `worker` subcommand; uses ingest_by_path/params_from_profile
    api/
      __init__.py                 # new
      app.py                      # new: create_app(), book + generate endpoints
  frontend/                       # new: Vite + React app
    package.json
    vite.config.js
    index.html
    src/main.jsx
    src/App.jsx
  Makefile                        # build/up/dev/down filled in
  pyproject.toml                  # + fastapi, uvicorn, python-multipart
  .gitignore                      # + .run/, frontend/node_modules/, frontend/dist/
  tests/
    test_db.py                    # + Job CRUD, list_books, get_book tests
    test_pipeline.py              # + synthesize_chapter progress test
    test_ingest_dispatch.py       # new
    test_voice_params.py          # new
    test_worker.py                # new
    test_cli.py                   # + worker command test
    test_api.py                   # new
  README.md                       # + GUI (M4) usage docs
```

---

### Task 1: `Job` model, schema, CRUD, and small `Database` fixes

**Files:**
- Modify: `src/audiobookify/models.py`
- Modify: `src/audiobookify/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: existing `Database` class, `SCHEMA` string (`src/audiobookify/db.py`),
  existing `Chapter`/`Book` dataclasses.
- Produces: `JobStatus` str enum (`QUEUED`, `GENERATING`, `DONE`, `FAILED`) and `Job`
  dataclass (`id, chapter_id, status, progress, created_at, started_at, finished_at,
  error`) in `audiobookify.models`. `Database.create_job(chapter_id: int) -> Job`,
  `Database.get_job(job_id: int) -> Job` (raises `KeyError`),
  `Database.list_jobs_for_chapter(chapter_id: int) -> List[Job]` (most recent first),
  `Database.latest_job_for_chapter(chapter_id: int) -> Optional[Job]`,
  `Database.pop_next_queued_job() -> Optional[Job]` (atomically claims the oldest
  `queued` job, marking it `generating`), `Database.update_job_progress(job_id: int,
  progress: float) -> None`, `Database.finish_job(job_id: int, status: JobStatus,
  error: Optional[str] = None) -> None`, `Database.reset_stale_generating_jobs() ->
  None`, `Database.list_books() -> List[Book]` (ordered by id). `Database.get_book`
  now raises `KeyError` for an unknown id instead of crashing with `TypeError`. Tasks
  2, 4, 5, 6 consume these directly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py` (add `from audiobookify.models import JobStatus` to the
existing `from audiobookify.models import SourceFormat, ChapterStatus` import line, so
it reads `from audiobookify.models import SourceFormat, ChapterStatus, JobStatus`):

```python
def _make_chapter(tmp_path, db):
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    return book, chapter


def test_create_job_defaults_to_queued_with_zero_progress(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter = _make_chapter(tmp_path, db)

    job = db.create_job(chapter.id)

    assert job.chapter_id == chapter.id
    assert job.status == JobStatus.QUEUED
    assert job.progress == 0.0
    assert job.started_at is None
    assert job.finished_at is None
    assert job.error is None
    db.close()


def test_get_job_raises_keyerror_for_missing_id(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    with pytest.raises(KeyError):
        db.get_job(999)
    db.close()


def test_list_jobs_for_chapter_orders_most_recent_first(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter = _make_chapter(tmp_path, db)
    first = db.create_job(chapter.id)
    second = db.create_job(chapter.id)

    jobs = db.list_jobs_for_chapter(chapter.id)

    assert [j.id for j in jobs] == [second.id, first.id]
    db.close()


def test_latest_job_for_chapter_returns_none_when_no_jobs(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter = _make_chapter(tmp_path, db)

    assert db.latest_job_for_chapter(chapter.id) is None
    db.close()


def test_pop_next_queued_job_claims_oldest_and_marks_generating(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter = _make_chapter(tmp_path, db)
    job = db.create_job(chapter.id)

    popped = db.pop_next_queued_job()

    assert popped.id == job.id
    assert popped.status == JobStatus.GENERATING
    assert popped.started_at is not None
    db.close()


def test_pop_next_queued_job_returns_none_when_queue_empty(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    assert db.pop_next_queued_job() is None
    db.close()


def test_pop_next_queued_job_orders_fifo_by_creation_order(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book, _ = _make_chapter(tmp_path, db)
    chapters = db.add_chapters(book.id, [RawChapter(title="Ch 2", text="Body 2.")])
    first_job = db.create_job(db.list_chapters(book.id)[0].id)
    second_job = db.create_job(chapters[0].id)

    popped = db.pop_next_queued_job()

    assert popped.id == first_job.id
    db.close()


def test_pop_next_queued_job_does_not_reclaim_a_generating_job(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter = _make_chapter(tmp_path, db)
    db.create_job(chapter.id)
    db.pop_next_queued_job()  # claims it -> generating

    assert db.pop_next_queued_job() is None
    db.close()


def test_update_job_progress_updates_value(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter = _make_chapter(tmp_path, db)
    job = db.create_job(chapter.id)

    db.update_job_progress(job.id, 0.5)

    assert db.get_job(job.id).progress == 0.5
    db.close()


def test_finish_job_marks_done_and_sets_progress_to_one(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter = _make_chapter(tmp_path, db)
    job = db.create_job(chapter.id)
    db.update_job_progress(job.id, 0.5)

    db.finish_job(job.id, JobStatus.DONE)

    finished = db.get_job(job.id)
    assert finished.status == JobStatus.DONE
    assert finished.progress == 1.0
    assert finished.finished_at is not None
    db.close()


def test_finish_job_marks_failed_and_preserves_last_progress(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter = _make_chapter(tmp_path, db)
    job = db.create_job(chapter.id)
    db.update_job_progress(job.id, 0.5)

    db.finish_job(job.id, JobStatus.FAILED, error="synthesis blew up")

    finished = db.get_job(job.id)
    assert finished.status == JobStatus.FAILED
    assert finished.progress == 0.5
    assert finished.error == "synthesis blew up"
    db.close()


def test_reset_stale_generating_jobs_requeues_generating_rows(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter = _make_chapter(tmp_path, db)
    job = db.create_job(chapter.id)
    db.pop_next_queued_job()  # -> generating

    db.reset_stale_generating_jobs()

    reset = db.get_job(job.id)
    assert reset.status == JobStatus.QUEUED
    assert reset.started_at is None
    db.close()


def test_list_books_returns_all_books_ordered_by_id(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    first = db.create_book(title="First", author=None, source_path=str(tmp_path / "a.md"),
                            source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"))
    second = db.create_book(title="Second", author=None, source_path=str(tmp_path / "b.md"),
                             source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"))

    books = db.list_books()

    assert [b.id for b in books] == [first.id, second.id]
    db.close()


def test_get_book_raises_keyerror_for_missing_id(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    with pytest.raises(KeyError):
        db.get_book(999)
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v -k "job or list_books or get_book_raises"`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'create_job'` (and
similarly for the other new methods); the `get_book` test fails because a `TypeError`
is raised instead of `KeyError`.

- [ ] **Step 3: Add `JobStatus`/`Job` to `src/audiobookify/models.py`**

Append after the `LexiconEntry` dataclass:

```python
class JobStatus(str, Enum):
    QUEUED = "queued"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    id: int
    chapter_id: int
    status: JobStatus
    progress: Optional[float]
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
```

- [ ] **Step 4: Update `src/audiobookify/db.py`**

Add `Job, JobStatus` to the models import: change
`from .models import Book, Chapter, ChapterStatus, LexiconEntry, SourceFormat, VoiceProfile`
to
`from .models import Book, Chapter, ChapterStatus, Job, JobStatus, LexiconEntry, SourceFormat, VoiceProfile`.

Add the `jobs` table to `SCHEMA`, right after the `lexicon_entries` table's closing
`);` and before the closing `"""`:

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

Change `Database.__init__` from:

```python
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        _migrate_lexicon_entries_columns(self.conn)
        self.conn.commit()
```

to:

```python
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        # Two OS processes (the API and the worker) now write to this file
        # concurrently - WAL + a busy timeout turns lock contention into a
        # brief wait/retry instead of "database is locked".
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        _migrate_lexicon_entries_columns(self.conn)
        self.conn.commit()
```

Add `_row_to_job` after `_row_to_lexicon_entry`:

```python
def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"], chapter_id=row["chapter_id"], status=JobStatus(row["status"]),
        progress=row["progress"], created_at=row["created_at"],
        started_at=row["started_at"], finished_at=row["finished_at"], error=row["error"],
    )
```

Change `get_book` from:

```python
    def get_book(self, book_id: int) -> Book:
        row = self.conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return _row_to_book(row)
```

to:

```python
    def get_book(self, book_id: int) -> Book:
        row = self.conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if row is None:
            raise KeyError(f"no book with id {book_id}")
        return _row_to_book(row)

    def list_books(self) -> List[Book]:
        rows = self.conn.execute("SELECT * FROM books ORDER BY id").fetchall()
        return [_row_to_book(row) for row in rows]
```

Add the `Job` methods at the end of the class, after `update_lexicon_entry_override`:

```python
    def create_job(self, chapter_id: int) -> Job:
        cur = self.conn.execute(
            "INSERT INTO jobs (chapter_id, status, progress, created_at) VALUES (?, ?, ?, ?)",
            (chapter_id, JobStatus.QUEUED.value, 0.0, time.time()),
        )
        self.conn.commit()
        return self.get_job(cur.lastrowid)

    def get_job(self, job_id: int) -> Job:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"no job with id {job_id}")
        return _row_to_job(row)

    def list_jobs_for_chapter(self, chapter_id: int) -> List[Job]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE chapter_id = ? ORDER BY created_at DESC, id DESC",
            (chapter_id,),
        ).fetchall()
        return [_row_to_job(row) for row in rows]

    def latest_job_for_chapter(self, chapter_id: int) -> Optional[Job]:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE chapter_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (chapter_id,),
        ).fetchone()
        return _row_to_job(row) if row else None

    def pop_next_queued_job(self) -> Optional[Job]:
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE status = ? ORDER BY created_at, id LIMIT 1",
            (JobStatus.QUEUED.value,),
        ).fetchone()
        if row is None:
            return None
        job_id = row["id"]
        cur = self.conn.execute(
            "UPDATE jobs SET status = ?, started_at = ? WHERE id = ? AND status = ?",
            (JobStatus.GENERATING.value, time.time(), job_id, JobStatus.QUEUED.value),
        )
        self.conn.commit()
        if cur.rowcount == 0:
            return None  # claimed by another worker between the SELECT and UPDATE
        return self.get_job(job_id)

    def update_job_progress(self, job_id: int, progress: float) -> None:
        self.conn.execute("UPDATE jobs SET progress = ? WHERE id = ?", (progress, job_id))
        self.conn.commit()

    def finish_job(self, job_id: int, status: JobStatus, error: Optional[str] = None) -> None:
        if status == JobStatus.DONE:
            self.conn.execute(
                "UPDATE jobs SET status = ?, progress = 1.0, finished_at = ?, error = ? WHERE id = ?",
                (status.value, time.time(), error, job_id),
            )
        else:
            self.conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status.value, time.time(), error, job_id),
            )
        self.conn.commit()

    def reset_stale_generating_jobs(self) -> None:
        self.conn.execute(
            "UPDATE jobs SET status = ?, started_at = NULL WHERE status = ?",
            (JobStatus.QUEUED.value, JobStatus.GENERATING.value),
        )
        self.conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: all pass (24 existing + 14 new = 38)

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (146 existing + 14 new = 160), 2 deselected

- [ ] **Step 7: Commit**

```bash
git add src/audiobookify/models.py src/audiobookify/db.py tests/test_db.py
git commit -m "feat: Job model, schema, and CRUD for the generation queue"
```

---

### Task 2: Extract `synthesize_chapter()` from `generate_book()`

**Files:**
- Modify: `src/audiobookify/synth/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Chapter`, `Book` (`audiobookify.models`), `Database` (`audiobookify.db`),
  `apply_lexicon` (`audiobookify.lexicon.substitution`), `chunk_text`
  (`audiobookify.normalize.text`), `concatenate_chunks`/`export_chapter_mp3`
  (`audiobookify.postprocess.audio`), the `TTSEngine` protocol shape.
- Produces: `synthesize_chapter(chapter: Chapter, book: Book, engine, profile_handle,
  voice_params: dict, lexicon_entries: list, db: Database, on_chunk_done:
  Optional[Callable[[int, int], None]] = None) -> Chapter` in
  `audiobookify.synth.pipeline`. Task 4 (worker) calls this directly with a callback
  that writes to `Job.progress`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py` (change the import line
`from audiobookify.synth.pipeline import generate_book` to
`from audiobookify.synth.pipeline import generate_book, synthesize_chapter`):

```python
@requires_ffmpeg
def test_synthesize_chapter_reports_progress_per_chunk(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    [chapter] = db.list_chapters(book.id)
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    calls = []
    result = synthesize_chapter(
        chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
        on_chunk_done=lambda done, total: calls.append((done, total)),
    )

    assert result.status == ChapterStatus.DONE
    # "Body of chapter 1." is a single sentence, well under max_chunk_chars -> one chunk
    assert calls == [(1, 1)]
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline.py -v -k synthesize_chapter`
Expected: FAIL with `ImportError: cannot import name 'synthesize_chapter'`

- [ ] **Step 3: Refactor `src/audiobookify/synth/pipeline.py`**

Replace the entire file with:

```python
from typing import Callable, List, Optional

from ..db import Database
from ..lexicon.substitution import apply_lexicon
from ..models import Book, Chapter, ChapterStatus
from ..normalize.text import chunk_text
from ..postprocess.audio import concatenate_chunks, export_chapter_mp3


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
        mp3_path = export_chapter_mp3(
            combined, engine.sample_rate(), book.output_dir, chapter.order_index,
            chapter.title, book.title, voice_params.get("voice_name", engine.name),
        )
        db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=mp3_path)
    except Exception as exc:
        db.update_chapter_status(chapter.id, ChapterStatus.FAILED, error=str(exc))

    return db.get_chapter(chapter.id)


def generate_book(book: Book, engine, db: Database, voice_params: dict, device: str = "auto") -> List[Chapter]:
    results = []
    try:
        engine.load(device)
        profile_handle = engine.clone_profile(
            voice_params.get("reference_audio"), voice_params.get("reference_text"), voice_params
        )
        lexicon_entries = db.list_lexicon_entries(book.id)

        for chapter in db.list_chapters(book.id):
            if chapter.status == ChapterStatus.DONE:
                results.append(chapter)
                continue
            results.append(
                synthesize_chapter(chapter, book, engine, profile_handle, voice_params,
                                    lexicon_entries, db)
            )
    finally:
        engine.teardown()

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: all pass (5 existing + 1 new = 6) — the 5 pre-existing tests must pass
**unmodified**, proving `generate_book()`'s external behavior didn't change.

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (160 + 1 = 161), 2 deselected

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/synth/pipeline.py tests/test_pipeline.py
git commit -m "refactor: extract synthesize_chapter() with a chunk-progress callback"
```

---

### Task 3: Library relocations — `ingest_by_path()` and `params_from_profile()`

**Files:**
- Modify: `src/audiobookify/ingest/__init__.py`
- Modify: `src/audiobookify/voice/validation.py`
- Modify: `src/audiobookify/cli.py`
- Test: `tests/test_ingest_dispatch.py` (new)
- Test: `tests/test_voice_params.py` (new)

**Interfaces:**
- Consumes: `epub.parse`/`markdown.parse` (`audiobookify.ingest`), `SourceFormat`
  (`audiobookify.models`), `VoiceProfile` (`audiobookify.models`).
- Produces: `ingest_by_path(path: str, heading_level: int = 1) -> tuple[ParsedBook,
  SourceFormat]` in `audiobookify.ingest`; `params_from_profile(profile) -> dict` in
  `audiobookify.voice.validation`. Task 5 (API) calls `ingest_by_path` directly; Task 4
  (worker) calls `params_from_profile` directly.

This is a pure relocation — `cli.py`'s two private helpers (`_ingest`, `_voice_params`)
move into the library unchanged in logic, so both the CLI and the new API layer can use
them without duplicating the dispatch/mapping logic. `_ingest`/`_voice_params` have no
existing test coverage of their own (they're exercised indirectly through `_handle_*`
tests in `tests/test_cli.py`), so this task adds direct tests for the relocated
functions and relies on the full `test_cli.py` suite continuing to pass unmodified to
prove the CLI's behavior didn't change.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingest_dispatch.py`:

```python
import pytest

from audiobookify.ingest import ingest_by_path
from audiobookify.models import SourceFormat


def test_ingest_by_path_parses_markdown(tmp_path):
    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody.\n", encoding="utf-8")

    parsed, source_format = ingest_by_path(str(md_path))

    assert source_format == SourceFormat.MARKDOWN
    assert parsed.chapters[0].title == "Chapter One"


def test_ingest_by_path_rejects_unsupported_suffix(tmp_path):
    bad_path = tmp_path / "book.txt"
    bad_path.write_text("hello", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported input format"):
        ingest_by_path(str(bad_path))
```

Create `tests/test_voice_params.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ingest_dispatch.py tests/test_voice_params.py -v`
Expected: FAIL — `ImportError: cannot import name 'ingest_by_path'` /
`ImportError: cannot import name 'params_from_profile'`

- [ ] **Step 3: Write `src/audiobookify/ingest/__init__.py`**

```python
from pathlib import Path
from typing import Tuple

from ..models import SourceFormat
from . import epub, markdown
from .base import ParsedBook


def ingest_by_path(path: str, heading_level: int = 1) -> Tuple[ParsedBook, SourceFormat]:
    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        return epub.parse(path), SourceFormat.EPUB
    if suffix == ".md":
        return markdown.parse(path, heading_level=heading_level), SourceFormat.MARKDOWN
    raise ValueError(f"unsupported input format: {suffix}")
```

- [ ] **Step 4: Add `params_from_profile` to `src/audiobookify/voice/validation.py`**

Append at the end of the file:

```python
def params_from_profile(profile) -> dict:
    return {
        "reference_audio": profile.reference_audio_path,
        "reference_text": profile.reference_transcript,
        "exaggeration": profile.exaggeration,
        "cfg": profile.cfg_weight,
        "temperature": profile.temperature,
    }
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ingest_dispatch.py tests/test_voice_params.py -v`
Expected: all pass (2 + 1 = 3)

- [ ] **Step 6: Update `src/audiobookify/cli.py` to use the relocated functions**

Change the imports at the top from:

```python
from .db import Database
from .engines.chatterbox import ChatterboxEngine
from .ingest import epub, markdown
from .lexicon.substitution import apply_lexicon
from .models import ChapterStatus, SourceFormat
from .phoneme.respelling import phonemes_to_respelling
from .phoneme.wav2vec2 import Wav2Vec2PhonemeExtractor
from .synth.pipeline import generate_book
from .voice.validation import validate_reference_audio
```

to:

```python
from .db import Database
from .engines.chatterbox import ChatterboxEngine
from .ingest import ingest_by_path
from .lexicon.substitution import apply_lexicon
from .models import ChapterStatus
from .phoneme.respelling import phonemes_to_respelling
from .phoneme.wav2vec2 import Wav2Vec2PhonemeExtractor
from .synth.pipeline import generate_book
from .voice.validation import params_from_profile, validate_reference_audio
```

(`SourceFormat` is no longer referenced directly in `cli.py` once `_ingest` is
removed — it was only used inside `_ingest`'s dispatch, which `ingest_by_path` now owns.)

Delete the `_ingest` and `_voice_params` function definitions entirely:

```python
def _ingest(path: str, heading_level: int):
    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        return epub.parse(path), SourceFormat.EPUB
    if suffix == ".md":
        return markdown.parse(path, heading_level=heading_level), SourceFormat.MARKDOWN
    raise ValueError(f"unsupported input format: {suffix}")


def _voice_params(profile) -> dict:
    return {
        "reference_audio": profile.reference_audio_path,
        "reference_text": profile.reference_transcript,
        "exaggeration": profile.exaggeration,
        "cfg": profile.cfg_weight,
        "temperature": profile.temperature,
    }
```

In `_handle_generate`, change `parsed, source_format = _ingest(source_path,
args.heading_level)` to `parsed, source_format = ingest_by_path(source_path,
args.heading_level)`, and change `voice_params.update(_voice_params(profile))` to
`voice_params.update(params_from_profile(profile))`.

In `_handle_voice_preview`, change `audio = engine.synthesize(args.text, handle,
_voice_params(profile))` to `audio = engine.synthesize(args.text, handle,
params_from_profile(profile))`.

In `_handle_lexicon_preview`, change `audio = engine.synthesize(text, handle,
_voice_params(profile) if profile else {})` to `audio = engine.synthesize(text, handle,
params_from_profile(profile) if profile else {})`.

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (161 + 3 = 164), 2 deselected — `tests/test_cli.py`'s existing
tests must pass unmodified, proving the CLI's behavior is unaffected by the relocation.

- [ ] **Step 8: Commit**

```bash
git add src/audiobookify/ingest/__init__.py src/audiobookify/voice/validation.py \
        src/audiobookify/cli.py tests/test_ingest_dispatch.py tests/test_voice_params.py
git commit -m "refactor: relocate ingest dispatch and voice-params mapping into the library"
```

---

### Task 4: Worker process — `run_worker_once()`, `run_forever()`, and the `audiobookify worker` CLI subcommand

**Files:**
- Create: `src/audiobookify/worker.py`
- Modify: `src/audiobookify/cli.py`
- Test: `tests/test_worker.py` (new)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Database.pop_next_queued_job/get_chapter/get_book/get_voice_profile_by_id/
  list_lexicon_entries/update_job_progress/finish_job/reset_stale_generating_jobs`
  (Task 1), `synthesize_chapter` (Task 2), `params_from_profile` (Task 3),
  `ChatterboxEngine`/`FakeEngine` (existing).
- Produces: `run_worker_once(db: Database, engine, profile_cache: dict) -> bool` and
  `run_forever(db: Database, engine, poll_interval: float = 2.0) -> None` in
  `audiobookify.worker`. `audiobookify worker --device --poll-interval --db` CLI
  subcommand.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_worker.py`:

```python
import shutil

import pytest

from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, JobStatus, SourceFormat
from audiobookify.worker import run_worker_once

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_book_with_job(tmp_path, db, text="Body one."):
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text=text)])
    job = db.create_job(chapter.id)
    return book, chapter, job


def test_run_worker_once_returns_false_when_queue_empty(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    engine = FakeEngine()
    engine.load("cpu")

    assert run_worker_once(db, engine, {}) is False
    db.close()


@requires_ffmpeg
def test_run_worker_once_claims_job_and_marks_chapter_done(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter, job = _make_book_with_job(tmp_path, db)
    engine = FakeEngine()
    engine.load("cpu")

    did_work = run_worker_once(db, engine, {})

    assert did_work is True
    assert db.get_chapter(chapter.id).status == ChapterStatus.DONE
    assert db.get_job(job.id).status == JobStatus.DONE
    db.close()


@requires_ffmpeg
def test_run_worker_once_reports_progress_via_job(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter, job = _make_book_with_job(tmp_path, db)
    engine = FakeEngine()
    engine.load("cpu")

    run_worker_once(db, engine, {})

    assert db.get_job(job.id).progress == 1.0
    db.close()


def test_run_worker_once_marks_job_failed_when_synthesis_raises(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter, job = _make_book_with_job(tmp_path, db, text="explodes here.")

    class FailingEngine(FakeEngine):
        def synthesize(self, text_chunk, profile_handle, params):
            raise RuntimeError("synthesis blew up")

    engine = FailingEngine()
    engine.load("cpu")

    run_worker_once(db, engine, {})

    finished = db.get_job(job.id)
    assert finished.status == JobStatus.FAILED
    assert "synthesis blew up" in finished.error
    assert db.get_chapter(chapter.id).status == ChapterStatus.FAILED
    db.close()


@requires_ffmpeg
def test_run_worker_once_reuses_cached_profile_handle_for_same_book(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    chapters = db.add_chapters(book.id, [RawChapter(title=f"Ch {i}", text=f"Body {i}.")
                                          for i in range(2)])
    for chapter in chapters:
        db.create_job(chapter.id)

    clone_calls = []

    class RecordingEngine(FakeEngine):
        def clone_profile(self, reference_audio, reference_text, params):
            clone_calls.append(1)
            return super().clone_profile(reference_audio, reference_text, params)

    engine = RecordingEngine()
    engine.load("cpu")
    cache = {}
    run_worker_once(db, engine, cache)
    run_worker_once(db, engine, cache)

    assert len(clone_calls) == 1
    db.close()


@requires_ffmpeg
def test_run_worker_once_reclones_when_voice_profile_changes(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book_a = db.create_book(
        title="A", author=None, source_path=str(tmp_path / "a.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter_a] = db.add_chapters(book_a.id, [RawChapter(title="Ch 1", text="Body a.")])
    db.create_job(chapter_a.id)

    profile = db.create_voice_profile(
        name="Nova", engine="fake", reference_audio_path=str(tmp_path / "ref.wav"),
        reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8,
    )
    book_b = db.create_book(
        title="B", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.update_book_voice_profile(book_b.id, profile.id)
    [chapter_b] = db.add_chapters(book_b.id, [RawChapter(title="Ch 1", text="Body b.")])
    db.create_job(chapter_b.id)

    clone_calls = []

    class RecordingEngine(FakeEngine):
        def clone_profile(self, reference_audio, reference_text, params):
            clone_calls.append(reference_audio)
            return super().clone_profile(reference_audio, reference_text, params)

    engine = RecordingEngine()
    engine.load("cpu")
    cache = {}
    run_worker_once(db, engine, cache)  # book_a, no profile
    run_worker_once(db, engine, cache)  # book_b, profile "Nova"

    assert len(clone_calls) == 2
    assert clone_calls[1] == str(tmp_path / "ref.wav")
    db.close()
```

Add to `tests/test_cli.py` (import `from audiobookify import cli` and `FakeEngine`
already exist in the file; no new top-level imports needed):

```python
def test_worker_command_loads_engine_once_and_runs_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)
    calls = []

    def fake_run_forever(db, engine, poll_interval):
        calls.append((db, engine, poll_interval))

    monkeypatch.setattr(cli.worker, "run_forever", fake_run_forever)

    db_path = tmp_path / "test.db"
    cli.main(["worker", "--db", str(db_path), "--poll-interval", "5"])

    assert len(calls) == 1
    db, engine, poll_interval = calls[0]
    assert isinstance(engine, FakeEngine)
    assert engine.device == "auto"
    assert poll_interval == 5.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_worker.py tests/test_cli.py -v -k "worker"`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.worker'` (and
`AttributeError: module 'audiobookify.cli' has no attribute 'worker'` /
`SystemExit` from argparse `invalid choice: 'worker'` once the module exists)

- [ ] **Step 3: Write `src/audiobookify/worker.py`**

```python
import time
from typing import Optional

from .db import Database
from .models import ChapterStatus, JobStatus
from .synth.pipeline import synthesize_chapter
from .voice.validation import params_from_profile


def _voice_params_for_book(db: Database, book) -> tuple:
    profile = db.get_voice_profile_by_id(book.voice_profile_id) if book.voice_profile_id else None
    voice_params = {"voice_name": profile.name if profile else "default"}
    if profile:
        voice_params.update(params_from_profile(profile))
    return voice_params, profile


def run_worker_once(db: Database, engine, profile_cache: dict) -> bool:
    job = db.pop_next_queued_job()
    if job is None:
        return False

    chapter = db.get_chapter(job.chapter_id)
    book = db.get_book(chapter.book_id)
    voice_params, profile = _voice_params_for_book(db, book)

    if "voice_profile_id" not in profile_cache or profile_cache["voice_profile_id"] != book.voice_profile_id:
        reference_audio = profile.reference_audio_path if profile else None
        reference_text = profile.reference_transcript if profile else None
        profile_cache["voice_profile_id"] = book.voice_profile_id
        profile_cache["handle"] = engine.clone_profile(reference_audio, reference_text, voice_params)

    lexicon_entries = db.list_lexicon_entries(book.id)

    def on_chunk_done(done: int, total: int) -> None:
        db.update_job_progress(job.id, done / total)

    result = synthesize_chapter(chapter, book, engine, profile_cache["handle"], voice_params,
                                 lexicon_entries, db, on_chunk_done=on_chunk_done)

    status = JobStatus.DONE if result.status == ChapterStatus.DONE else JobStatus.FAILED
    db.finish_job(job.id, status, error=result.error)
    return True


def run_forever(db: Database, engine, poll_interval: float = 2.0) -> None:
    db.reset_stale_generating_jobs()
    profile_cache: dict = {}
    while True:
        did_work = run_worker_once(db, engine, profile_cache)
        if not did_work:
            time.sleep(poll_interval)
```

- [ ] **Step 4: Run `test_worker.py` to verify it passes**

Run: `.venv/bin/pytest tests/test_worker.py -v`
Expected: all pass (6)

- [ ] **Step 5: Add the `worker` subcommand to `src/audiobookify/cli.py`**

Add `from . import worker` to the imports (alphabetized, after `from .synth.pipeline
import generate_book` and before `from .voice.validation import ...`).

Add a handler after `_handle_lexicon_preview`:

```python
def _handle_worker(args) -> None:
    db = Database(args.db)
    engine = ChatterboxEngine()
    engine.load(args.device)
    try:
        worker.run_forever(db, engine, poll_interval=args.poll_interval)
    finally:
        engine.teardown()
        db.close()
```

Add the subparser in `main()`, after the `lexicon_parser`/`lexicon_subparsers` block
and before `args = parser.parse_args(argv)`:

```python
    worker_parser = subparsers.add_parser("worker", help="Run the background generation worker")
    worker_parser.add_argument("--device", default="auto")
    worker_parser.add_argument("--poll-interval", type=float, default=2.0)
    worker_parser.add_argument("--db", default="audiobookify.db")
```

Add the dispatch branch, as a new `elif` alongside the existing `elif args.command ==
"lexicon":` block:

```python
    elif args.command == "worker":
        _handle_worker(args)
```

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (164 + 6 + 1 = 171), 2 deselected

- [ ] **Step 7: Commit**

```bash
git add src/audiobookify/worker.py src/audiobookify/cli.py tests/test_worker.py tests/test_cli.py
git commit -m "feat: worker process consuming the Job queue, and audiobookify worker CLI subcommand"
```

---

### Task 5: FastAPI app — book upload, list, and detail endpoints

**Files:**
- Create: `src/audiobookify/api/__init__.py`
- Create: `src/audiobookify/api/app.py`
- Modify: `pyproject.toml`
- Test: `tests/test_api.py` (new)

**Interfaces:**
- Consumes: `Database` (`audiobookify.db`), `ingest_by_path` (Task 3),
  `JobStatus`/`ChapterStatus` (`audiobookify.models`).
- Produces: `create_app(db_path: str = "audiobookify.db", upload_dir: str = "uploads")
  -> FastAPI` in `audiobookify.api.app`. Routes: `POST /api/books`, `GET /api/books`,
  `GET /api/books/{book_id}`. Task 6 adds `POST /api/books/{book_id}/generate` to the
  same file; Task 8 adds a `frontend_dist` parameter to `create_app`.

- [ ] **Step 1: Add new dependencies to `pyproject.toml`**

Change:

```toml
dependencies = [
    "ebooklib>=0.18",
    "beautifulsoup4>=4.12",
    "num2words>=0.5.13",
    "soundfile>=0.12",
    "numpy>=1.26",
    "mutagen>=1.47",
    "chatterbox-tts>=0.1.1",
    "phonemizer>=3.2",
]
```

to:

```toml
dependencies = [
    "ebooklib>=0.18",
    "beautifulsoup4>=4.12",
    "num2words>=0.5.13",
    "soundfile>=0.12",
    "numpy>=1.26",
    "mutagen>=1.47",
    "chatterbox-tts>=0.1.1",
    "phonemizer>=3.2",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "python-multipart>=0.0.9",
]
```

Run: `.venv/bin/pip install -e ".[dev]"`
Expected: `fastapi`, `uvicorn`, `python-multipart`, and `starlette`/`httpx` (FastAPI's
transitive deps, `httpx` is what `TestClient` uses) install successfully.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_api.py`:

```python
from fastapi.testclient import TestClient

from audiobookify.api.app import create_app


def _client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"), upload_dir=str(tmp_path / "uploads"))
    return TestClient(app)


def test_create_book_ingests_uploaded_markdown_and_returns_chapters(tmp_path):
    client = _client(tmp_path)
    md_bytes = b"# Chapter One\nFirst chapter body.\n\n# Chapter Two\nSecond chapter body.\n"

    response = client.post(
        "/api/books",
        files={"file": ("book.md", md_bytes, "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "book"
    assert len(body["chapters"]) == 2
    assert body["chapters"][0]["title"] == "Chapter One"
    assert body["chapters"][0]["status"] == "pending"
    assert body["chapters"][0]["progress"] is None


def test_create_book_rejects_unsupported_format(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/api/books",
        files={"file": ("book.txt", b"hello", "text/plain")},
        data={"output_dir": str(tmp_path / "out")},
    )

    assert response.status_code == 400
    assert "unsupported input format" in response.json()["detail"]


def test_create_book_creates_a_new_book_row_for_each_upload_even_with_the_same_filename(tmp_path):
    client = _client(tmp_path)
    md_bytes = b"# Ch\nBody.\n"

    first = client.post("/api/books", files={"file": ("book.md", md_bytes, "text/markdown")},
                         data={"output_dir": str(tmp_path / "out")})
    second = client.post("/api/books", files={"file": ("book.md", md_bytes, "text/markdown")},
                          data={"output_dir": str(tmp_path / "out")})

    assert first.json()["id"] != second.json()["id"]


def test_list_books_returns_all_imported_books(tmp_path):
    client = _client(tmp_path)
    client.post("/api/books", files={"file": ("a.md", b"# Ch\nBody.\n", "text/markdown")},
                 data={"output_dir": str(tmp_path / "out")})
    client.post("/api/books", files={"file": ("b.md", b"# Ch\nBody.\n", "text/markdown")},
                 data={"output_dir": str(tmp_path / "out")})

    response = client.get("/api/books")

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_get_book_returns_404_for_unknown_id(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/books/999")

    assert response.status_code == 404
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.api'`

- [ ] **Step 4: Write `src/audiobookify/api/__init__.py`**

```python
```

- [ ] **Step 5: Write `src/audiobookify/api/app.py`**

```python
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ..db import Database
from ..ingest import ingest_by_path


def create_app(db_path: str = "audiobookify.db", upload_dir: str = "uploads") -> FastAPI:
    app = FastAPI()
    Path(upload_dir).mkdir(parents=True, exist_ok=True)

    def get_db():
        db = Database(db_path)
        try:
            yield db
        finally:
            db.close()

    def _book_to_dict(db: Database, book) -> dict:
        chapters = []
        for chapter in db.list_chapters(book.id):
            job = db.latest_job_for_chapter(chapter.id)
            chapters.append({
                "id": chapter.id,
                "order_index": chapter.order_index,
                "title": chapter.title,
                "status": chapter.status.value,
                "output_mp3_path": chapter.output_mp3_path,
                "error": chapter.error,
                "progress": job.progress if job else None,
            })
        return {
            "id": book.id,
            "title": book.title,
            "author": book.author,
            "output_dir": book.output_dir,
            "voice_profile_id": book.voice_profile_id,
            "chapters": chapters,
        }

    @app.post("/api/books")
    def create_book(file: UploadFile = File(...), output_dir: str = Form(...),
                     db: Database = Depends(get_db)):
        # Sync, not async: get_db is a sync generator dependency, so FastAPI builds
        # the Database (and its check_same_thread=True sqlite3 connection) on a
        # threadpool worker thread. An `async def` route body runs on the event
        # loop thread instead - a different thread than the one that opened the
        # connection - so any db.* call inside would raise sqlite3.ProgrammingError.
        # Keeping this route sync keeps dependency resolution and the route body on
        # the same worker thread.
        filename = Path(file.filename).name
        suffix = Path(filename).suffix.lower()
        if suffix not in (".epub", ".md"):
            raise HTTPException(status_code=400, detail=f"unsupported input format: {suffix}")

        staged_dir = Path(upload_dir) / uuid.uuid4().hex
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_path = staged_dir / filename
        staged_path.write_bytes(file.file.read())

        try:
            parsed, source_format = ingest_by_path(str(staged_path.resolve()))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        book = db.create_book(
            title=parsed.title, author=parsed.author, source_path=str(staged_path.resolve()),
            source_format=source_format, output_dir=str(Path(output_dir).resolve()),
        )
        db.add_chapters(book.id, parsed.chapters)

        return JSONResponse(_book_to_dict(db, book))

    @app.get("/api/books")
    def list_books(db: Database = Depends(get_db)):
        return [_book_to_dict(db, b) for b in db.list_books()]

    @app.get("/api/books/{book_id}")
    def get_book(book_id: int, db: Database = Depends(get_db)):
        try:
            book = db.get_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="book not found")
        return _book_to_dict(db, book)

    return app
```

(FastAPI's `yield`-style dependencies still run their `finally` block when the endpoint
raises `HTTPException`, so `db.close()` always runs even on the 400/404 paths above.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: all pass (5)

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (171 + 5 = 176), 2 deselected

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/audiobookify/api/__init__.py src/audiobookify/api/app.py tests/test_api.py
git commit -m "feat: FastAPI app with book upload, list, and detail endpoints"
```

---

### Task 6: FastAPI `POST /api/books/{book_id}/generate` endpoint

**Files:**
- Modify: `src/audiobookify/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Database.list_chapters/latest_job_for_chapter/create_job` (Task 1),
  `JobStatus` (`audiobookify.models`).
- Produces: `POST /api/books/{book_id}/generate` returning `{"queued_job_ids": [...]}`.

- [ ] **Step 1: Write the failing tests**

Change the top of `tests/test_api.py` from:

```python
from fastapi.testclient import TestClient

from audiobookify.api.app import create_app
```

to:

```python
import shutil

import pytest
from fastapi.testclient import TestClient

from audiobookify.api.app import create_app
from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.models import JobStatus
from audiobookify.worker import run_worker_once

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
```

Add to `tests/test_api.py`:

```python
def test_generate_endpoint_creates_queued_jobs_for_all_chapters(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n\n# Two\nB.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()

    response = client.post(f"/api/books/{created['id']}/generate")

    assert response.status_code == 200
    assert len(response.json()["queued_job_ids"]) == 2

    db = Database(str(tmp_path / "test.db"))
    for chapter in db.list_chapters(created["id"]):
        assert db.latest_job_for_chapter(chapter.id).status == JobStatus.QUEUED
    db.close()


def test_generate_endpoint_does_not_duplicate_jobs_for_already_queued_chapters(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()

    client.post(f"/api/books/{created['id']}/generate")
    second = client.post(f"/api/books/{created['id']}/generate")

    assert second.json()["queued_job_ids"] == []


def test_generate_endpoint_returns_404_for_unknown_book(tmp_path):
    client = _client(tmp_path)

    response = client.post("/api/books/999/generate")

    assert response.status_code == 404


@requires_ffmpeg
def test_book_detail_reflects_job_progress_after_worker_processes_it(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()
    client.post(f"/api/books/{created['id']}/generate")

    db = Database(str(tmp_path / "test.db"))
    engine = FakeEngine()
    engine.load("cpu")
    run_worker_once(db, engine, {})
    db.close()

    detail = client.get(f"/api/books/{created['id']}").json()
    assert detail["chapters"][0]["status"] == "done"
    assert detail["chapters"][0]["progress"] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v -k generate`
Expected: FAIL with `404 Not Found` (no route registered for `POST
/api/books/{book_id}/generate`) — FastAPI's `TestClient` doesn't raise, so these fail on
the `assert response.status_code == 200` / `assert len(...) == 2` lines.

- [ ] **Step 3: Add the generate endpoint to `src/audiobookify/api/app.py`**

Add `JobStatus` to the models import: change `from ..db import Database` /
`from ..ingest import ingest_by_path` to also import `JobStatus`:

```python
from ..db import Database
from ..ingest import ingest_by_path
from ..models import JobStatus
```

Add the route inside `create_app`, after `get_book`'s definition and before `return app`:

```python
    @app.post("/api/books/{book_id}/generate")
    def generate(book_id: int, db: Database = Depends(get_db)):
        try:
            book = db.get_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="book not found")

        queued_job_ids = []
        for chapter in db.list_chapters(book.id):
            latest = db.latest_job_for_chapter(chapter.id)
            if latest is not None and latest.status in (JobStatus.QUEUED, JobStatus.GENERATING):
                continue
            job = db.create_job(chapter.id)
            queued_job_ids.append(job.id)

        return {"queued_job_ids": queued_job_ids}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: all pass (5 + 4 = 9)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (176 + 4 = 180), 2 deselected

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/api/app.py tests/test_api.py
git commit -m "feat: FastAPI generate endpoint that queues Jobs for a book's chapters"
```

---

### Task 7: Frontend skeleton (Vite + React)

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.jsx`
- Create: `frontend/src/App.jsx`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `POST /api/books`, `GET /api/books/{id}`, `POST /api/books/{id}/generate`
  (Tasks 5-6).
- Produces: a single-page app served by `vite dev` (proxying `/api` to the FastAPI
  backend) or, once built, by FastAPI itself (Task 8).

No automated tests for this task — per the approved design, this bare skeleton gets no
test framework (YAGNI for a page this small). It's verified by building it and, once
Task 8 wires up `make dev`, running it against the real backend in a browser.

- [ ] **Step 1: Add frontend build artifacts to `.gitignore`**

Change:

```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
*.db
```

to:

```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
*.db
frontend/node_modules/
frontend/dist/
.run/
```

- [ ] **Step 2: Create `frontend/package.json`**

```json
{
  "name": "audiobookify-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.1",
    "vite": "^5.4.0"
  }
}
```

- [ ] **Step 3: Create `frontend/vite.config.js`**

```javascript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
```

- [ ] **Step 4: Create `frontend/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Audiobookify</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `frontend/src/main.jsx`**

```javascript
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 6: Create `frontend/src/App.jsx`**

```javascript
import { useEffect, useState } from "react";

export default function App() {
  const [book, setBook] = useState(null);
  const [outputDir, setOutputDir] = useState("");
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!book) return;
    const interval = setInterval(async () => {
      const res = await fetch(`/api/books/${book.id}`);
      if (res.ok) setBook(await res.json());
    }, 2000);
    return () => clearInterval(interval);
  }, [book?.id]);

  async function handleImport(event) {
    event.preventDefault();
    setError(null);
    const form = new FormData();
    form.append("file", event.target.elements.file.files[0]);
    form.append("output_dir", outputDir);
    const res = await fetch("/api/books", { method: "POST", body: form });
    if (!res.ok) {
      setError((await res.json()).detail);
      return;
    }
    setBook(await res.json());
  }

  async function handleGenerate() {
    await fetch(`/api/books/${book.id}/generate`, { method: "POST" });
  }

  return (
    <div style={{ fontFamily: "sans-serif", maxWidth: 640, margin: "2rem auto" }}>
      <h1>Audiobookify</h1>
      {!book && (
        <form onSubmit={handleImport}>
          <div>
            <input type="file" name="file" accept=".epub,.md" required />
          </div>
          <div>
            <input
              type="text"
              placeholder="Output folder path"
              value={outputDir}
              onChange={(e) => setOutputDir(e.target.value)}
              required
            />
          </div>
          <button type="submit">Import</button>
          {error && <p style={{ color: "red" }}>{error}</p>}
        </form>
      )}
      {book && (
        <div>
          <h2>{book.title}</h2>
          <button onClick={handleGenerate}>Generate all</button>
          <ul>
            {book.chapters.map((chapter) => (
              <li key={chapter.id}>
                {chapter.title} &mdash; {chapter.status}
                {chapter.progress != null && ` (${Math.round(chapter.progress * 100)}%)`}
                {chapter.output_mp3_path && ` -> ${chapter.output_mp3_path}`}
                {chapter.error && ` (error: ${chapter.error})`}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 7: Install dependencies and verify the build compiles**

Run: `cd frontend && npm install && npm run build`
Expected: `vite build` completes with no errors, producing `frontend/dist/index.html`
and bundled assets.

- [ ] **Step 8: Commit**

```bash
git add frontend/ .gitignore
git commit -m "feat: bare Vite + React frontend skeleton"
```

---

### Task 8: Makefile wiring and serving the built frontend from `make up`

**Files:**
- Modify: `Makefile`
- Modify: `src/audiobookify/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `create_app` (Task 5).
- Produces: `create_app(..., frontend_dist: Optional[str] = None)` — when the resolved
  directory exists, `create_app` mounts it at `/` via `StaticFiles(html=True)` so
  `make up` can serve the built frontend from the same FastAPI process. `make
  dev`/`make up`/`make down`/`make build` are real now instead of stub echoes.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api.py`:

```python
def test_create_app_serves_built_frontend_index_when_dist_dir_provided(tmp_path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>Audiobookify</body></html>", encoding="utf-8")
    app = create_app(db_path=str(tmp_path / "test.db"), upload_dir=str(tmp_path / "uploads"),
                      frontend_dist=str(dist_dir))
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Audiobookify" in response.text


def test_create_app_returns_404_at_root_when_no_frontend_dist_configured(tmp_path):
    # Point frontend_dist at a directory that doesn't exist rather than relying on the
    # default (which resolves to the real repo's frontend/dist) - otherwise this test
    # would flake depending on whether `npm run build` has been run on this machine.
    app = create_app(db_path=str(tmp_path / "test.db"), upload_dir=str(tmp_path / "uploads"),
                      frontend_dist=str(tmp_path / "no-such-dist"))
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v -k frontend`
Expected: FAIL — `create_app() got an unexpected keyword argument 'frontend_dist'`

- [ ] **Step 3: Update `src/audiobookify/api/app.py`**

Add the `StaticFiles` import: change

```python
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
```

to:

```python
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
```

Change the `create_app` signature from:

```python
def create_app(db_path: str = "audiobookify.db", upload_dir: str = "uploads") -> FastAPI:
    app = FastAPI()
    Path(upload_dir).mkdir(parents=True, exist_ok=True)
```

to:

```python
def create_app(db_path: str = "audiobookify.db", upload_dir: str = "uploads",
                frontend_dist: Optional[str] = None) -> FastAPI:
    app = FastAPI()
    Path(upload_dir).mkdir(parents=True, exist_ok=True)
```

Add this immediately before `return app` (after all `/api/...` routes are registered,
so they take priority over the catch-all static mount):

```python
    resolved_dist = Path(frontend_dist) if frontend_dist else Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if resolved_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(resolved_dist), html=True), name="frontend")

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: all pass (9 + 2 = 11)

- [ ] **Step 5: Update `Makefile`**

Change:

```makefile
build:
	@echo "no frontend yet - 'build' arrives with the GUI (M4)"

up:
	@echo "no server yet - 'up' arrives with the GUI (M4); use 'audiobookify generate ...' directly for now"

dev:
	@echo "no dev server yet - 'dev' arrives with the GUI (M4)"

down:
	@echo "nothing running - 'down' arrives with the GUI (M4)"
```

to:

```makefile
build:
	cd frontend && npm run build

up: build
	mkdir -p .run
	$(VENV)/bin/uvicorn audiobookify.api.app:create_app --factory --host 127.0.0.1 --port 8000 & echo $$! > .run/api.pid
	$(VENV)/bin/audiobookify worker & echo $$! > .run/worker.pid
	@echo "audiobookify running at http://127.0.0.1:8000 - 'make down' to stop."

dev:
	mkdir -p .run
	$(VENV)/bin/uvicorn audiobookify.api.app:create_app --factory --reload --host 127.0.0.1 --port 8000 & echo $$! > .run/api.pid
	$(VENV)/bin/audiobookify worker & echo $$! > .run/worker.pid
	cd frontend && npm run dev & echo $$! > .run/frontend.pid
	@echo "dev stack running - api http://127.0.0.1:8000, frontend http://127.0.0.1:5173 - 'make down' to stop."

down:
	@for f in .run/*.pid; do [ -f "$$f" ] && kill "$$(cat "$$f")" 2>/dev/null; rm -f "$$f"; done
	@command -v lsof >/dev/null 2>&1 && lsof -ti:5173 | xargs kill 2>/dev/null; true
	@echo "stopped."
```

**Note (added after Task 8 implementation found a real bug via the process-lifecycle
verification below):** `npm run dev &` forks a child `node`/`vite` process rather than
exec'ing into it, so `$!` captures the wrong PID and the plain `kill "$(cat
.run/frontend.pid)"` above leaves an orphaned Vite process holding port 5173. The
`lsof -ti:5173 | xargs kill` line kills whatever process is actually listening on the
frontend dev port, independent of how many process levels `npm`/`vite` fork through.
`command -v lsof` guards against `lsof` being unavailable (e.g. some Windows/Git Bash
setups); the trailing `; true` keeps this best-effort line from aborting `make down` if
`lsof` is missing or nothing is listening.

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (180 + 2 = 182), 2 deselected

- [ ] **Step 7: Manually verify `make dev` end-to-end**

Run: `make dev` (requires `make setup` to have been run at least once with `frontend/`
present, and `ffmpeg` installed). In a browser, go to `http://127.0.0.1:5173`, pick a
small `.epub` or `.md` file, type an output folder path, click Import, then click
"Generate all" and watch the chapter list update via polling until each chapter shows
`done` with an `output_mp3_path`. Confirm the MP3 file actually exists at that path.
Run `make down` afterward and confirm the three background processes stop (`ps` shows
nothing listening on 8000/5173).

- [ ] **Step 8: Commit**

```bash
git add src/audiobookify/api/app.py Makefile tests/test_api.py
git commit -m "feat: serve the built frontend from FastAPI, wire up make dev/up/down"
```

---

### Task 9: README — GUI (M4) usage docs

**Files:**
- Modify: `README.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Update `README.md`**

Find the `## Development` section (the last section in the file) and insert a new
section immediately **before** it:

```markdown

## GUI (M4)

The GUI wraps the same core library behind a local web UI: a FastAPI backend, a
separate worker process that owns the loaded TTS engine, and a React frontend — all
running on `localhost`, still fully local.

```bash
make dev    # backend (autoreload) + worker + frontend dev server, hot reload
```

Open `http://127.0.0.1:5173`, upload an EPUB or Markdown file, type an output folder
path, click Import, then click "Generate all". The chapter list polls the backend every
couple of seconds and shows each chapter's status (`pending` / `generating` / `done` /
`failed`), in-progress percentage, and — once done — the resulting MP3's path.

For a non-hot-reload run (built frontend served by FastAPI itself):

```bash
make up     # builds the frontend, then runs backend + worker
make down   # stops everything make dev/up started
```

The API and worker communicate purely through the same SQLite database `generate`/
`voice`/`lexicon` already use — there's no separate message broker. You can run
`audiobookify worker --db audiobookify.db` and the API server independently if you want
more control than the Makefile targets give you.

This first GUI pass is deliberately bare: one page, upload → generate → watch chapters
finish. Chapter review/editing, voice profile management, the pronunciation lexicon, and
a real generation dashboard are still CLI-only (`voice`, `lexicon` subcommands) until
later GUI sub-projects land.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: M4 GUI (backend/worker skeleton) usage instructions"
```

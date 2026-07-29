# Audiobookify M4 Sub-project 1 — Backend API + Worker-Process Skeleton: Design

**Status:** Approved by user (2026-07-29), ready for implementation planning.

## Goal

M4 (spec §9) wraps the core pipeline in a GUI: local web UI (FastAPI + React), per
spec §7/§10. This is the **first of six M4 sub-projects** (see the decomposition below)
and the highest-risk one: prove the walking skeleton — import a book through the
browser, generate it, see a resulting MP3 — end to end, before building the richer
chapter-review / voice / lexicon / dashboard screens on top of it in later sub-projects.

## M4 sub-project decomposition (context, not all built here)

1. **Backend API + worker-process skeleton** (this spec)
2. Chapter review screen (merge/split/rename/reorder/exclude)
3. Voice profile management + preview UI
4. Lexicon management + preview UI
5. Generation queue dashboard (depends on 1, 2)
6. Export surface

## Background

- M0-M3 built a UI-agnostic core library (`src/audiobookify/`) with `cli.py` as a thin
  argparse layer over it — ingest, normalize, voice cloning, pronunciation lexicon,
  phoneme extraction, synthesis pipeline, SQLite persistence. All 146 tests pass (2
  integration tests deselected, both confirmed against real weights).
- `synth/pipeline.py`'s `generate_book()` is the only synthesis entry point today: loads
  the engine once, clones the voice profile once, then loops every chapter that isn't
  already `DONE`, synthesizing and updating `Chapter.status` per chapter. It runs
  synchronously to completion inside whatever process calls it (currently only the CLI).
- There is no `Job` table today, despite one being sketched in `project-spec.md` §6 —
  M0-M3 quietly replaced it with `Chapter.status` alone, which was sufficient for a
  single-process CLI with no concept of "queued but not yet started" separate from
  "chapter exists."
- `Database` opens one SQLite connection per CLI invocation; nothing before this has
  needed two OS processes writing to the same database file concurrently.

## Decisions (confirmed with the user before this design)

- **UI stack:** local web UI — FastAPI backend + React frontend at `localhost`, not a
  native Tauri/Electron shell.
- **Worker model:** a **separate worker process** (not an in-process background thread)
  owns the loaded `TTSEngine` and consumes generation work, decoupling API
  responsiveness from a crashing/hanging worker.
- **API ↔ worker coordination:** the shared SQLite database, via a new `Job` table — not
  a message broker. `Job` gives real per-chapter-attempt history and a genuine home for
  progress percentage (see below), which a bare `Chapter.status` extension would not.
- **Progress reporting:** the frontend polls a status endpoint (~2s interval), not
  SSE/WebSocket.
- **Book import:** browser file picker + upload (not a typed filesystem path) — the
  backend stages the upload to disk, then ingests it via the existing path-based
  `ingest.epub`/`ingest.markdown` functions unchanged.
- **Packaging:** out of scope. `make dev`/`make up` only, for the two target machines.

## Architecture

```
Browser (React) ──HTTP + polling──▶ FastAPI (audiobookify serve)
                                          │  reads/writes
                                          ▼
                                    SQLite (audiobookify.db)
                                          ▲  reads/writes
                                          │
                              Worker process (audiobookify worker)
                              owns the loaded TTSEngine, consumes Jobs
```

SQLite is the sole coordination mechanism between the API and worker processes — no new
broker dependency. Because two OS processes now write to the same database file
concurrently (new as of this sub-project), `Database.__init__` gains:

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
```

so lock contention degrades to a brief wait/retry instead of an immediate
`sqlite3.OperationalError: database is locked`.

## Data model

### New: `Job`

```
Job
  id, chapter_id, status, progress, created_at, started_at, finished_at, error
```

- `status ∈ {queued, generating, done, failed}` (new `JobStatus` str enum, same shape as
  `ChapterStatus`).
- `progress`: nullable float 0.0-1.0. Populated with real per-chunk progress (see
  "Synthesis refactor" below) — not a placeholder, since chapters are chunked into
  sentence-level pieces before synthesis and the worker can report "N of M chunks done."
- One `Job` row is created per generation **request** for a chapter. Regenerating a
  chapter later creates a new `Job` row rather than overwriting the old one, preserving
  attempt history — this is the concrete value `Job` adds over extending
  `Chapter.status` with a `queued` value, which was the alternative considered and
  rejected (see "Alternatives considered").
- `Database` gains: `create_job(chapter_id) -> Job`, `get_job(job_id) -> Job`,
  `list_jobs_for_chapter(chapter_id) -> List[Job]` (most recent first),
  `pop_next_queued_job() -> Optional[Job]` (or equivalent: fetch the oldest `queued` job
  and atomically mark it `generating` so two worker instances can't double-process it —
  see "Worker" below for why this matters even though v1 assumes a single worker),
  `update_job_progress(job_id, progress: float) -> None`,
  `finish_job(job_id, status: JobStatus, error: Optional[str] = None) -> None`,
  `reset_stale_generating_jobs() -> None` (crash recovery, called at worker startup).

### `Chapter.status`: unchanged

Still `pending/generating/done/failed`, still exactly what `generate_book()` (CLI path)
reads and writes today. The worker writes `Chapter.status` at the same two transition
points where it writes the corresponding `Job.status` (start-of-generation,
end-of-generation), in the same code path — so the two can't drift out of sync from
independent writers. No other code writes `Chapter.status` while a `Job` is active.

## Synthesis refactor: `synthesize_chapter()`

Extract the per-chapter body of `generate_book()`'s loop into a standalone function in
`synth/pipeline.py`:

```python
def synthesize_chapter(chapter, book, engine, profile_handle, voice_params,
                        lexicon_entries, db, on_chunk_done: Optional[Callable] = None) -> Chapter:
    ...
```

Same logic as today (apply lexicon → chunk → synthesize each chunk → concatenate →
export → update `Chapter.status`), with one addition: after each chunk is synthesized,
call `on_chunk_done(completed, total)` if provided. `generate_book()` becomes a thin
wrapper: load engine, clone profile, loop non-`DONE` chapters calling
`synthesize_chapter(..., on_chunk_done=None)`, teardown. This is a pure internal
refactor — `generate_book()`'s external behavior, signature, and all existing tests are
expected to be unaffected; the worker is a second caller of `synthesize_chapter()` that
passes a callback wiring into `Job.progress` and creates/updates `Job` rows around the
call.

## Library relocations (small, in-scope cleanup)

Two helpers currently live as private functions inside `cli.py` but are needed by both
the CLI and the new API layer:

- `_ingest(path, heading_level)` → move to `audiobookify.ingest.ingest_by_path(path,
  heading_level)`.
- `_voice_params(profile)` → move to `audiobookify.voice.params_from_profile(profile)`.

`cli.py` keeps calling them (now as imports from the library instead of local
functions), consistent with spec §7's "keep the core pipeline UI-agnostic" — this was
already a minor deviation from that principle, and the API layer is the forcing
function to fix it now rather than duplicate the logic.

## API surface (`src/audiobookify/api/`, new FastAPI app)

- `POST /api/books` — multipart upload (`file`: the EPUB/MD, `output_dir`: form field,
  a server-local path — the browser still can't supply a filesystem path for the
  *export* destination any more than for import, so this stays a typed field). Saves
  the upload to a staging directory under the app's data dir, then calls
  `ingest_by_path()` unchanged. Reuses `Database.find_book_by_source` /
  `create_book` / `add_chapters` exactly as `cli._handle_generate` does today.
- `GET /api/books` — list books.
- `GET /api/books/{id}` — book detail; each embedded chapter includes `status` and,
  when a `Job` exists for it, that job's `progress`/`error` (most recent `Job` per
  chapter). This is the endpoint the frontend polls.
- `POST /api/books/{id}/generate` — creates `queued` `Job` rows for every chapter that
  doesn't already have an active (`queued` or `generating`) job. No chapter-subset
  selection yet (that's sub-project 5) — this sub-project's one action is "generate
  everything not already in flight or done."

FastAPI dependency-injects a fresh `Database(db_path)` per request (opened and closed
within the request), mirroring the CLI's per-invocation lifecycle — no shared
long-lived connection on the API side, avoiding sqlite3's thread-safety pitfalls under
FastAPI's default threaded execution.

## Worker (`src/audiobookify/worker.py` + new `audiobookify worker` CLI subcommand)

- `run_worker_once(db, engine, cached_profile: dict) -> bool` — the testable unit.
  Pops the oldest `queued` Job (FIFO across all books) via
  `db.pop_next_queued_job()`; if none, returns `False` immediately (no sleep, no loop —
  keeps this synchronously unit-testable). Otherwise: looks up the job's chapter and
  book, reuses `cached_profile` if it already matches this book's `voice_profile_id`,
  else calls `engine.clone_profile(...)` and updates the cache; calls
  `synthesize_chapter(..., on_chunk_done=lambda i, n: db.update_job_progress(job.id,
  i / n))`; calls `db.finish_job(job.id, DONE or FAILED, error=...)`; returns `True`.
- `audiobookify worker --db --device auto --poll-interval 2` — the daemon entrypoint:
  loads the engine once (kept resident for the process lifetime, torn down on exit),
  calls `db.reset_stale_generating_jobs()` once at startup (crash recovery — any `Job`
  left `generating` by a previous worker process that died mid-chapter goes back to
  `queued`, mirroring the CLI's existing resumability contract for `Chapter.status`),
  then loops `while True: if not run_worker_once(db, engine, cache): sleep(poll_interval)`.
- Test-double convention matches every existing module: `ChatterboxEngine` importable
  as `worker.ChatterboxEngine` for monkeypatching, `FakeEngine` used in all
  non-integration tests.
- `pop_next_queued_job()` marks the job `generating` atomically as part of the same
  `SELECT`+`UPDATE` (e.g. `UPDATE jobs SET status='generating' WHERE id = (SELECT id
  FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1) RETURNING *` or the
  SQLite-compatible equivalent). V1 only ever runs one worker process, but this makes
  "only one worker process at a time" a property of correct code rather than an
  unenforced assumption a future second worker instance would silently violate.

## Frontend skeleton (`frontend/`, new Vite + React app)

Deliberately bare — proving the architecture, not the UX. One page: upload form
(`file` + `output_dir` text field) → POST `/api/books`; chapter list with live
`status`/`progress` (polls `GET /api/books/{id}` every ~2s); a "Generate all" button →
POST `/api/books/{id}/generate`; once a chapter is `done`, show its
`output_mp3_path` as plain text (no audio player yet — that's sub-project 5). No
router, no state-management library, no icon library.

## Makefile wiring

- `make setup`: unchanged behavior, already has the `if [ -d frontend ]; then npm
  install; fi` guard from M0 waiting for `frontend/` to exist.
- `make dev`: starts `uvicorn --reload` (API), `audiobookify worker`, and `vite dev`
  (frontend), each backgrounded with a PID file under `.run/`.
- `make up`: `vite build` once, FastAPI serves the built static assets via
  `StaticFiles`, `uvicorn` without `--reload`, plus the worker — same PID-file
  bookkeeping.
- `make down`: kills each process by its PID file.
- No new process-manager dependency (no honcho/foreman/supervisord) — shell + PID files
  is enough for two-to-three local processes.

## New dependencies

- Python (first new pip deps since M0): `fastapi`, `uvicorn`, `python-multipart`
  (multipart upload parsing) — directly required by the confirmed UI-stack decision.
- JS (first npm deps): `react`, `react-dom`, `vite`.

## Testing strategy

- `synthesize_chapter()` refactor: existing `test_pipeline.py` must continue to pass
  unmodified — it's asserting on `generate_book()`'s external behavior, which doesn't
  change. Add direct tests for `synthesize_chapter()`'s `on_chunk_done` callback.
- `Job`/`Database` additions: unit tests following the exact pattern used for
  `LexiconEntry`/`VoiceProfile` in `test_db.py` (create, fetch, status transitions,
  `reset_stale_generating_jobs`, the atomic pop-and-mark-generating behavior under
  concurrent-looking sequential calls).
- `worker.py`: `run_worker_once()` tested directly and synchronously with `FakeEngine`
  and a `tmp_path` database — no threads, no real sleep, no infinite loop in tests.
- API layer: FastAPI's `TestClient` (sync, no running server needed) against a
  `tmp_path` database, `FakeEngine` where relevant. Upload endpoint tested with a real
  small EPUB/MD fixture (existing test fixtures from `test_ingest_epub.py`/
  `test_ingest_markdown.py` can likely be reused or mirrored).
- Frontend: no test framework added for this bare skeleton (YAGNI for a page this
  small); manually verified against the running `make dev` stack per this project's
  standing rule to verify UI changes in a real browser before calling the task done.
- Per this project's standing lesson from M3 (mocked `from_pretrained`-style calls can
  hide a real-dependency surprise): none of this sub-project's new code touches a heavy
  ML `from_pretrained`-style call, so no new integration test is needed here — the
  existing Chatterbox/wav2vec2 integration tests remain the coverage for that surface.

## Alternatives considered

**`Chapter.status` gains a `queued` value instead of a `Job` table.** Simpler (no new
table, no dual-write-consistency concern), but loses per-attempt history and has no
natural home for chunk-level progress — the dashboard (sub-project 5) would have no way
to show "3 of 12 chunks done" for the currently-generating chapter. Rejected in favor of
`Job`, per user decision.

**Reuse `generate_book()` as-is from the worker (call it once per book, let it run to
completion).** Simpler, but doesn't allow per-chapter `Job` bookkeeping, chunk-level
progress, or interleaving chapters from different books fairly — a large book queued
first would block all other books' chapters until it finished entirely. Rejected in
favor of the `synthesize_chapter()` extraction + per-`Job` worker loop.

## Out of scope for this sub-project

Chapter-subset selection in `generate`, chapter review/editing (merge/split/rename/
reorder/exclude), voice profile picking/creation UI, lexicon UI, regenerate-a-single-
chapter, audio playback/preview, packaging/installers. All deferred to sub-projects 2-6
or M5, built against this same API/worker foundation.

## Open questions

None outstanding — all decisions in this document were confirmed with the user during
brainstorming on 2026-07-29.

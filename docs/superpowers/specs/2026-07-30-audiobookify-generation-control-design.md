# Audiobookify — Generation Control (Pause / Cancel / Resume): Design

**Status:** Approved by user (2026-07-30), ready for implementation planning.

## Goal

Generation can take a long time, and users can't always leave it running uninterrupted.
Today there is no way to pause, cancel, or meaningfully resume in-flight generation:
`synthesize_chapter` holds all chunk audio in memory and writes to disk once at the end,
so any interruption (worker restart, crash) loses all synthesis progress for whatever
chapter was in flight, and there is no user-facing control to stop work intentionally at
all. This sub-project adds a global pause, per-chapter/per-book cancel, and true
mid-chunk resume so interrupted or intentionally-paused generation doesn't waste
already-completed chunk work.

This is one of 3 sub-projects from this brainstorming session (see also: output path
safety, voice reference audio safety — each gets its own spec/plan/build cycle).

## Background

- `worker.py`'s `_process_next_chapter_job` calls `synthesize_chapter`, which loops
  every text chunk, synthesizes it, appends to an in-memory list, and only writes
  anything to disk after every chunk for the chapter is done (`concatenate_chunks` →
  `export_chapter_mp3`).
- `Job.status ∈ {queued, generating, done, failed}` — no `cancelled` state, no pause
  concept anywhere in the schema.
- `run_forever` already calls `db.reset_stale_generating_jobs()` at startup, resetting
  any `Job` left `generating` by a crashed worker back to `queued` — this is the existing
  crash-recovery contract this sub-project extends (via chunk caching) rather than
  replaces.
- The per-chunk `on_chunk_done(done, total)` callback (added in the M4 backend/worker
  skeleton sub-project for progress reporting) already fires after every chunk — the
  natural checkpoint for both writing a chunk to a durable cache and checking
  pause/cancel signals.

## Decisions (confirmed with the user during brainstorming)

- **Pause is global**, not per-book — one worker process serves the whole queue today,
  so a single pause/resume switch matches the current architecture with far less to
  build than per-book pause state.
- **Cancel is available both per-chapter and per-book** — a stop control next to each
  chapter's existing generate button, plus a book-level "cancel all."
- **True mid-chunk resume** — each chunk is persisted to disk as it's produced (not held
  only in memory), so pausing, cancelling, or crashing mid-chapter never discards more
  than the single chunk in flight, and resuming continues from the next chunk rather
  than re-synthesizing the whole chapter from scratch.
- **Cancelled/paused chapters revert to `pending`** (not a new `ChapterStatus` value) —
  from the chapter's point of view, a cancelled or paused generation attempt looks
  exactly like "never queued," which is already a state every part of the UI and API
  understands.

## Architecture

```
run_forever loop:
  if db.is_paused(): sleep; continue          (don't start new jobs while paused)
  job = db.pop_next_queued_job()
  ...
  synthesize_chapter(..., on_chunk_done=cb)
    cb(done, total):
      write chunk to .cache/synth/chapter-<id>/chunk-NNNN.wav
      db.update_job_progress(...)
      if db.is_paused():              raise GenerationPaused
      if db.is_cancel_requested(job): raise GenerationCancelled
  except GenerationPaused:    job → QUEUED,    chapter → PENDING, cache kept
  except GenerationCancelled: job → CANCELLED, chapter → PENDING, cache deleted
```

## Data model changes

- New table `worker_state`:
  ```sql
  CREATE TABLE IF NOT EXISTS worker_state (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      paused INTEGER NOT NULL DEFAULT 0
  );
  ```
  Single-row-by-construction (the `CHECK (id = 1)` constraint), avoiding any ambiguity
  about which row holds the real flag. `Database` gains `is_paused() -> bool`,
  `set_paused(paused: bool) -> None` (upserts the one row).
- `jobs` table: new column `cancel_requested INTEGER NOT NULL DEFAULT 0`, added via the
  existing additive-migration pattern (`_migrate_lexicon_entries_columns`-style).
  `Database` gains `request_job_cancel(job_id) -> None` and
  `is_job_cancel_requested(job_id) -> bool`.
- `JobStatus` gains `CANCELLED = "cancelled"`.

## Chunk cache

- Location: `<repo-root>/.cache/synth/chapter-<chapter_id>/` — deliberately **not**
  under the user-supplied `output_dir`, so cache durability is never affected by the
  same permission issues the output-path-safety sub-project addresses; this is an
  app-owned scratch area, cleaned up automatically on success.
- Contents: `chunk-0000.wav`, `chunk-0001.wav`, … (one file per chunk, written via
  `soundfile` — already a dependency) plus `manifest.json`:
  ```json
  {"cache_key": "<sha256 of post-lexicon text + engine.max_chunk_chars() + voice_params>"}
  ```
- `synthesize_chapter` computes the same `cache_key` at the start of each attempt. If
  `manifest.json` exists and its `cache_key` matches: load however many
  `chunk-NNNN.wav` files are present (in order, via `soundfile.read`) as the starting
  `audio_chunks` list, and resume the synthesis loop from the next chunk index. If the
  key doesn't match (lexicon entry or voice params changed since the last attempt) or
  the manifest is missing: delete any stale cache dir contents and start fresh, writing
  a new manifest with the current key.
- Every newly-synthesized chunk (not loaded from cache) is written to
  `chunk-NNNN.wav` immediately, before `on_chunk_done` returns — this is what makes
  pause/cancel/crash-mid-chapter lossless up to the last completed chunk.
- On successful export (`ChapterStatus.DONE`), the chapter's entire cache directory is
  deleted — it has no further purpose once the final MP3 exists.

## Control-flow exceptions

- Two small exception classes in `synth/pipeline.py` (or a shared `worker` module —
  implementer's call): `GenerationPaused`, `GenerationCancelled`. Raised from inside
  `on_chunk_done` (called from within `synthesize_chapter`'s loop), caught in
  `worker.py`'s `_process_next_chapter_job`, which is the one place that knows how to
  translate each into the right `Job`/`Chapter` status transition — `synthesize_chapter`
  itself doesn't need to know about jobs at all, consistent with how it already doesn't
  touch `Job` rows today (the worker owns that boundary).

## API surface

- `POST /api/worker/pause`, `POST /api/worker/resume` — flip the global flag.
- `GET /api/worker/status` — `{"paused": bool}`, polled by the frontend alongside the
  existing book/chapter polling.
- `POST /api/books/{book_id}/chapters/{chapter_id}/cancel` — if the chapter's latest
  `Job` is `QUEUED`, mark it `CANCELLED` directly (nothing running to interrupt) and
  reset the chapter to `PENDING`. If `GENERATING`, call
  `db.request_job_cancel(job.id)` and return immediately — the worker observes the flag
  at the next chunk boundary and performs the actual transition asynchronously (the
  response doesn't wait for that to happen; the frontend's existing polling picks up the
  resulting status change).
- `POST /api/books/{book_id}/cancel` — same logic applied to every chapter of the book
  with an active (`queued`/`generating`) job.

## Frontend changes

- App shell header: a global pause/resume toggle, visible on every page (matches the
  global scope decision), reflecting `GET /api/worker/status`.
- `BookDetailPage`: a stop-icon button next to each chapter's existing generate button,
  enabled whenever that chapter is `queued` or `generating`; a "Cancel all" button next
  to "Generate all," enabled whenever any chapter has an active job.
- No new polling mechanism — reuses the existing 2-second interval already driving
  chapter status updates.

## Testing strategy

- `worker_state`/`is_paused`/`set_paused`: straightforward `Database` unit tests,
  mirroring existing patterns in `test_db.py`.
- Chunk cache: unit tests for `synthesize_chapter` covering (a) fresh chapter, no cache,
  writes chunks as it goes; (b) matching cache present, resumes from the correct index
  without re-synthesizing cached chunks (assert the fake engine's `synthesize` call
  count reflects only the remaining chunks); (c) stale cache (mismatched key) is
  discarded and synthesis restarts from chunk 0; (d) cache directory is gone after a
  successful export.
- Pause: `run_worker_once`/`run_forever`-level test asserting no queued job is picked up
  while paused, and that a job interrupted mid-chapter (fake engine raises after N
  calls, or the test flips `paused` between chunks) ends as `QUEUED`/chapter `PENDING`
  with the cache intact for the next call to pick up from.
- Cancel: `QUEUED`-job cancel (direct transition, no worker involved) and
  `GENERATING`-job cancel (flag observed at the next chunk boundary, job ends
  `CANCELLED`, chapter `PENDING`, cache directory removed) as two separate test cases.
- API layer: `TestClient` tests for all five new/changed endpoints against a `tmp_path`
  database and `FakeEngine`.
- Frontend: no test framework added (matches established precedent); manually verified
  in a running `make dev` session — pause mid-generation, cancel a queued chapter,
  cancel a generating chapter, resume and confirm chunk-level resume actually skips
  already-completed chunks (observable via reduced wall-clock time on resume) — before
  this sub-project is called done.

## Alternatives considered

**Chapter-status-only cancel (add a `cancelled` `ChapterStatus`) instead of reverting to
`pending`.** Rejected per user decision — reverting to `pending` reuses every existing
code path (retry logic, "generate" button enablement, dashboard filtering) that already
understands "not yet generated," rather than teaching all of them about a new terminal
state that behaves identically to `pending` except for the label.

**Restart-chapter-from-scratch resume (no chunk cache) instead of true mid-chunk
resume.** Simpler — no cache directory, no manifest, no cache-invalidation logic — and
was the initially-recommended option. Rejected per user decision in favor of true
mid-chunk resume, since large chapters interrupted near the end would otherwise lose
nearly all their synthesis time on every pause/cancel/crash.

**Per-book pause instead of global.** Rejected per user decision — the current
single-worker-process architecture has no natural per-book granularity without adding a
"which books are paused" set the worker must consult on every job pop; global pause is
the smaller change that matches how the worker already operates (one queue, FIFO across
all books).

## Out of scope for this sub-project

Per-chunk (rather than per-chapter) `Job` granularity in the UI/API (chunk-level detail
stays internal to the cache, not exposed as its own resource); pausing/cancelling
`TextClip` generation (text clips are typically short enough that this wasn't raised as
a pain point — can be added later by applying the same `cancel_requested`
pattern to `text_clips` if it becomes one); output path safety and voice reference audio
safety — separate specs from this session.

## Open questions

None outstanding — all decisions in this document were confirmed with the user during
brainstorming on 2026-07-30.

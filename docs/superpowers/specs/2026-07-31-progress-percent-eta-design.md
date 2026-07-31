# Progress percent + ETA indicator

## Problem

The chapter progress bars (book detail page) and text-clip progress bars (text page) show a filled bar but no numbers. Users can't tell how far along generation is or how much longer it'll take.

## Scope

- Per-chapter progress bars on the book detail page (`BookDetailPage.jsx`).
- Per-clip progress bars on the text page (`TextPage.jsx`).
- Explicitly out of scope: a new aggregate whole-book progress bar, and the library/home page badge (which already shows a percent, just no bar/ETA) — these can be revisited later if wanted.

## Design

### 1. Backend: expose `started_at`

`Job` and `TextClip` already have a `started_at` field (dataclass + DB column) but it isn't in the API response. Add it:

- `src/audiobookify/api/app.py::_book_to_dict` — each chapter dict gets `"started_at": active_job.started_at if active_job else None`.
- `src/audiobookify/api/app.py::_text_clip_to_dict` — add `"started_at": clip.started_at`.

Test: extend `tests/test_api.py` to assert `started_at` is present and correct once a chapter/clip is generating.

### 2. ETA calculation (frontend, pure function)

New file `frontend/src/lib/eta.js`:

```js
export function formatDuration(seconds) { ... } // "45s", "5m 30s", "1h 20m"

export function formatEta(progress, startedAt, now = Date.now() / 1000) {
  if (!startedAt || !progress || progress <= 0) return null;
  const elapsed = now - startedAt;
  const remaining = elapsed * (1 - progress) / progress;
  return formatDuration(remaining);
}
```

Linear extrapolation from elapsed time and progress fraction — no new backend fields beyond `started_at`, no per-chunk timing needed.

No frontend test runner exists in this repo yet (no vitest/jest in `frontend/package.json`). Rather than introduce one for a single pure function, add a minimal self-check using Node's built-in `node:test`/`node:assert` (stdlib, zero new deps), runnable via `node --test frontend/src/lib/eta.test.js`.

### 3. `ProgressBar.jsx`

Add an optional `startedAt` prop:
- Always renders `{pct}%` next to the bar (fixes the "no number at all" gap even when no ETA is computable yet).
- When `formatEta(value, startedAt)` returns a string, also renders `· ~{eta} left`.
- No `startedAt` passed → behaves exactly as today plus the percent text (safe default, no other call sites broken).

### 4. Call sites

- `BookDetailPage.jsx`: pass `startedAt={chapter.started_at}` into each chapter's `<ProgressBar>`.
- `TextPage.jsx`: pass `startedAt={clip.started_at}` into each clip's `<ProgressBar>`.

Both pages already poll (2s and 1.5s respectively) while items are in progress, so the ETA recomputes for free on each poll — no new timers.

## Edge cases

- `progress` is `0`/`null` (queued, no chunks done yet): show `0%`, no ETA text.
- `started_at` missing (shouldn't happen once generating, but defensively handled): percent only, no ETA.

## Testing

- Backend: extend `tests/test_api.py` (TDD — write the assertion first, then wire `started_at` through).
- Frontend: `node:test` self-check for `formatEta`/`formatDuration` covering the zero-progress, missing-`startedAt`, sub-minute, and hour-plus cases.

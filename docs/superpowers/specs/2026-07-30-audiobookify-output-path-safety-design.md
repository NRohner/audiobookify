# Audiobookify — Output Path Safety: Design

**Status:** Approved by user (2026-07-30), ready for implementation planning.

## Goal

A user lost ~30 minutes of chapter-generation work because the `output_dir` typed into
the import form was wrong, and the permission error only surfaced in
`export_chapter_mp3` — after synthesis for that chapter had already run to completion.
This sub-project makes that failure mode structurally impossible to hit blind: pick the
folder instead of typing it, validate writability before any generation starts, and fall
back to a guaranteed-writable location if a path somehow goes bad mid-flight anyway.

This is sub-project 1 of 3 from this brainstorming session (see also: voice reference
audio safety, generation control/pause-cancel-resume — each gets its own spec/plan/build
cycle, this one built first).

## Background

- `POST /api/books` (`src/audiobookify/api/app.py`) takes `output_dir` as an untrusted
  form string, resolves it (`Path(output_dir).resolve()`), and stores it on the `Book`
  row with no existence or writability check.
- The frontend's `ImportBookModal` (`frontend/src/pages/HomePage.jsx`) is a plain
  required text `<input>` — free-form typing, the direct cause of the reported typo.
- The actual filesystem write happens once, at the very end, in
  `export_chapter_mp3`/`export_mp3` (`src/audiobookify/postprocess/audio.py`) — after
  chunked synthesis for the whole chapter has completed in memory. A bad path is
  discovered only here, discarding that chapter's synthesis work.

## Decisions (confirmed with the user during brainstorming)

- **Folder picker over validated text field:** a real clickable server-side directory
  browser, not just a validated text input with a recent-paths dropdown — directly
  addresses "better browser to an output path," not only the permission-error surprise.
- **Per-listed-folder writability:** the browse endpoint reports writability for every
  child folder it lists (not just the currently-browsed one), so the picker can visibly
  gray out folders the user can't select, rather than letting them pick one and find out
  after the fact.
- **Backup fallback is a safety net, not the primary defense:** import-time validation
  is expected to catch the vast majority of bad paths before any generation is queued.
  The fallback location only exists for the rarer case of a path going bad *after*
  import (permissions changed, drive unmounted, disk full).

## Architecture

```
ImportBookModal (React)
  │ GET /api/browse-dir?path=...   ──▶  lists subdirs + per-dir writable flag
  │ POST /api/books (output_dir=...) ──▶ server re-validates writability, 400s if bad
  ▼
Book.output_dir (stored, already validated at import time)
  │
  ▼ (later, at generation time)
export_chapter_mp3 / export_mp3
  │ try: write to Book.output_dir
  └ except OSError/PermissionError: retry write to BACKUP_OUTPUT_DIR/<book_id>/,
                                     return that path + used_fallback=True
```

## Backend: directory browsing & validation

New module `src/audiobookify/api/browse.py` (or a function group in `app.py` if small
enough after writing — implementer's call):

- `_check_writable(path: Path) -> bool` — attempts to create the directory (`mkdir(parents=True,
  exist_ok=True)`) and write+delete a hidden probe file (e.g. `.audiobookify_write_test`)
  inside it, catching `OSError`/`PermissionError`. This is the single source of truth
  used by both the browse endpoint and the import-time guard, so they can never
  disagree about whether a path is usable.
- `GET /api/browse-dir?path=<path>` — defaults to the user's home directory when `path`
  is omitted. Returns:
  ```json
  {
    "path": "/resolved/current/path",
    "parent": "/resolved/parent/or/null/at/root",
    "writable": true,
    "entries": [
      {"name": "audiobooks", "writable": true},
      {"name": "System", "writable": false}
    ]
  }
  ```
  Filtered to directories only (via `Path.is_dir()`), no file listing — this endpoint
  exists purely to support picking a folder. Non-existent or unreadable `path` returns
  `404`.
- `POST /api/books` gains a stricter check before `db.create_book(...)`: re-runs
  `_check_writable` on the resolved `output_dir` and returns `400` with a clear message
  (e.g. `"cannot write to '/foo/bar': Permission denied"`) if it fails. This applies
  regardless of whether the path came from the new picker or was hand-typed — it's the
  actual fix for "wrong path discovered after synthesis," independent of the frontend
  change.

## Frontend: `DirectoryBrowser` component

Replaces the free-text `output_dir` field in `ImportBookModal`:

- Breadcrumb-style current path + an "up" button (disabled at `parent === null`).
- A list of clickable subfolders from `GET /api/browse-dir`. Folders where
  `writable: false` render muted/disabled with a tooltip explaining why they can't be
  selected.
- A "Use this folder" button confirms the currently browsed directory.
- An editable text field beneath the browser, pre-filled with the browsed path, so the
  user can append a new subfolder name (e.g. browse to `/Users/you/audiobooks`, type
  `this-book` on the end) without the picker itself needing a "create folder" affordance
  — keeps the browser component read-only/simple, per YAGNI.
- Inline status next to the field: green check ("writable") or a red error with the
  actual reason, re-checked live via `_check_writable` semantics as the path is browsed
  or edited (debounced on text-field edits). The Import button stays disabled until this
  check passes.

## Backup fallback

- Constant `BACKUP_OUTPUT_DIR = <repo-root>/output/` (created lazily, matches the
  existing `.gitignore` entry for `output/` already present in this repo).
- `export_chapter_mp3` and `export_mp3` wrap their write in try/except. On
  `OSError`/`PermissionError`, retry the identical export into
  `BACKUP_OUTPUT_DIR/<book_id>/` (chapters) or `BACKUP_OUTPUT_DIR/text-clips/` (clips),
  and return `(path, used_fallback: bool)` instead of a bare path.
- Callers (`synthesize_chapter`, `synthesize_text_clip`) store `used_fallback` alongside
  the resulting path — `Chapter` and `TextClip` each gain a `used_fallback: bool = False`
  field, persisted the same way `output_mp3_path`/`output_path` already are.
- API responses (`_book_to_dict`'s per-chapter dict, `_text_clip_to_dict`) surface
  `used_fallback` so the UI can show a small warning badge ("saved to backup location —
  original folder wasn't writable") instead of silently hiding the discrepancy.

## Data model changes

- `chapters` table: new column `used_fallback INTEGER NOT NULL DEFAULT 0`.
- `text_clips` table: new column `used_fallback INTEGER NOT NULL DEFAULT 0`.
- Both via the existing `_migrate_*_columns`-style additive migration pattern already
  used for `lexicon_entries` (`_migrate_lexicon_entries_columns` in `db.py`) — no
  destructive schema changes, safe against existing local databases.

## Testing strategy

- `_check_writable`: unit tests against `tmp_path` for the writable case, a
  `chmod`-restricted subdirectory for the permission-denied case, and a path whose
  parent doesn't exist yet (should succeed, since `mkdir(parents=True)` creates it).
- `GET /api/browse-dir`: `TestClient` tests — default (home dir) response shape, a path
  with a mix of writable/unwritable children, `parent` correctness at a few levels,
  `404` on a non-existent path.
- `POST /api/books` validation: existing book-creation tests extended with an
  unwritable-`output_dir` case asserting `400` and that no `Book`/`Chapter` rows were
  created (import-time rejection must be all-or-nothing, no partial book).
- Backup fallback: unit tests for `export_chapter_mp3`/`export_mp3` that force a
  `PermissionError` on the primary path (e.g. monkeypatching or a real chmod'd
  directory) and assert the file lands under `BACKUP_OUTPUT_DIR` with `used_fallback`
  reported correctly, and that a normal writable path never touches the fallback dir.
- Frontend: no test framework added (matches the M4 skeleton's established precedent of
  manual verification for UI-only work); manually verified in a running `make dev`
  browser session before this sub-project is called done, per this project's standing
  rule for UI changes.

## Alternatives considered

**Validated text field + recent-paths dropdown instead of a full browser.** Smaller
diff, and alone would already fix the permission-error surprise (which is the literal
incident that prompted this). Rejected in favor of the full picker per user decision,
since a freshly-typed path can still be a typo even with live validation — the picker
removes that class of mistake entirely rather than just catching it faster.

**Client-side `showDirectoryPicker()` (File System Access API).** Rejected: it returns
a sandboxed `FileSystemDirectoryHandle`, not an OS path string usable by the
server-side/worker process that actually performs the writes — doesn't fit this app's
architecture where synthesis output happens server-side, not in the browser.

## Out of scope for this sub-project

Creating new folders from within the picker (users can type an appended subfolder name
in the text field, or create the folder via their OS beforehand); voice reference audio
safety and generation control (pause/cancel/resume) — separate specs, sub-projects 2 and
3 from this session.

## Open questions

None outstanding — all decisions in this document were confirmed with the user during
brainstorming on 2026-07-30.

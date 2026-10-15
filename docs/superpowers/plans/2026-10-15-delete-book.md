# Delete a Book: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Delete book" action in the app, so a book can be removed and imported again (for example Augustus, to pick up the TOC-based chapters).

**Branch:** `feat/delete-book`, off `main`.

## Decisions

- Delete only what the app owns. Rendered MP3s in the book's output folder are kept: they're the user's audiobook (user, 2026-10-15).
- No delete while the book is rendering: return 409 with the same wording style as the chapter-breaks edit ("This book is rendering. Pause or cancel it before deleting it.").
- The staged copy of the source file (`uploads/<hex>/<file>`) is removed only when `source_path` lies inside the API's `upload_dir`. Books imported through the CLI point at the user's own file, which must never be deleted.
- The button lives inside the book page's existing "Advanced" disclosure, so it's out of the way of everyday clicks, and it always confirms first.

## What a book owns

- the `books` row;
- its `chapters`;
- the `jobs` of those chapters;
- book-scoped `lexicon_entries` (`book_id = <id>`);
- the chunk cache per chapter (`chunk_cache.clear_cache(chapter_id)`);
- the staged upload directory.

Nothing else references a book: voices are shared, and text clips aren't tied to books.

## Tasks (TDD, one reviewed commit each)

### Task 1: `Database.delete_book` (Python + Rust DB parity)
- Python, `src/audiobookify/db.py`: `delete_book(book_id) -> List[Chapter]`. In one transaction, delete the jobs of its chapters, its chapters, its book-scoped lexicon entries, and the book. Return the deleted chapters, so callers can clean up files. Raise `KeyError("book not found")` (same style as `get_book`) when it doesn't exist.
- Tests in `tests/test_db.py`:
  - everything listed above is gone;
  - another book's chapters, jobs and lexicon entries are untouched;
  - global lexicon entries (`book_id = 0`) survive;
  - a missing id raises `KeyError`.
- Rust, `desktop/core/src/db/mod.rs`: `Database::delete_book(&self, book_id) -> Result<Vec<Chapter>>` with the same SQL, `DbError::NotFound` when missing, and the same tests ported to `desktop/core/tests/books.rs`. The Rust DB is a one-for-one port of `db.py`, and phase 4 needs this for its delete command.

### Task 2: `DELETE /api/books/{book_id}`
In `src/audiobookify/api/app.py`:
- 404 when missing.
- 409 when any chapter's latest job is queued or generating.
- Otherwise call `db.delete_book`, then `clear_cache(chapter.id)` for each returned chapter, then `shutil.rmtree` of the staged directory, but only if `Path(source_path).resolve()` is inside `Path(upload_dir).resolve()`.
- Return `{"ok": true}`.

Tests in `tests/test_api.py`:
- the book is gone from `GET /api/books`;
- its chunk cache directory and staged upload directory are removed;
- an MP3 recorded on one of its chapters still exists;
- a book whose `source_path` is outside `upload_dir` keeps its source file;
- 404 for an unknown book;
- 409 while a job is queued.

### Task 3: The button
- `frontend/src/lib/api.js`: `deleteBook: (id) => request(\`/api/books/${id}\`, { method: "DELETE" })`.
- `frontend/src/pages/BookDetailPage.jsx`: inside the "Advanced" panel, a quiet destructive button "Delete book" with a lucide `Trash2` icon (no emoji).
  - It opens the existing `ConfirmDialog` with the title "Delete book" and the message "Delete “{title}”? Its chapters, render progress and book pronunciations are removed. MP3s already in {output_dir} stay where they are."
  - On confirm it calls `api.deleteBook` and navigates to the home page.
  - A 409 or other error shows inline, the same way the page shows other errors.
  - Disable the button while `state.rendering`.
- Follow `docs/design/DESIGN.md`, use only `--rr-*` tokens, and match how `VoicesPage`/`TextPage` handle `pendingDelete` and `ConfirmDialog`.
- Verify in the browser preview against `make dev`: open a test book, delete it, land on the home page, confirm the book is gone, and check the console for errors. Screenshot the dialog in light and dark mode.

## Not in this change
- Deleting rendered MP3s (the user chose to keep them).
- A delete action on the home page's book cards.

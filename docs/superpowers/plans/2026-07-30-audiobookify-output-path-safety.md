# Audiobookify — Output Path Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the "wrong/unwritable output folder discovered only after synthesis" failure
mode structurally impossible to hit blind — a clickable folder browser replaces the
free-text field, the server validates writability before any generation is queued, and a
guaranteed-writable backup location catches the rarer case of a path going bad mid-flight
anyway.

**Architecture:** Two new read endpoints (`GET /api/browse-dir`, `GET
/api/validate-output-dir`) share one `_check_writable(path) -> bool` helper in
`api/app.py`; `POST /api/books` calls the same helper before creating a `Book` row.
`postprocess/audio.py`'s two export functions gain a `fallback_dir` parameter and now
return `(path, used_fallback)` instead of a bare path; `synth/pipeline.py`'s
`synthesize_chapter`/`synthesize_text_clip` pass a project-root `output/` fallback and
persist the resulting flag on `Chapter`/`TextClip`. The frontend's `ImportBookModal` gets
a new `DirectoryBrowser` component; chapter/clip rows in the UI show a small warning
badge when a file landed in the backup location.

**Tech Stack:** No new dependencies — reuses `fastapi`/`pathlib` (backend),
`react`/`lucide-react` (frontend, `AlertTriangle`/`ArrowUp`/`Folder`/`Check`/`AlertCircle`
icons — no emojis, standing UI rule), `soundfile`/`sqlite3` (already present).

**Reference:** `docs/superpowers/specs/2026-07-30-audiobookify-output-path-safety-design.md`
(approved design spec — read it for full rationale) and
`docs/superpowers/plans/2026-07-29-audiobookify-m4-backend-worker-skeleton.md` (this
project's established plan/task conventions).

## Global Constraints

- **No new dependencies, Python or JS.** Everything here is buildable with what's already
  installed.
- **`_check_writable` is the single source of truth for "can I write here."** Both new
  endpoints and the `POST /api/books` guard call the exact same function — they must
  never be able to disagree about whether a path is usable.
- **Backup fallback only ever triggers on a write failure, never as a first choice.**
  Import-time validation is expected to catch the vast majority of bad paths; the
  fallback is a safety net for a path going bad *after* import (permissions changed,
  drive unmounted, disk full mid-run).
- **`export_chapter_mp3`/`export_mp3` now return `(path: str, used_fallback: bool)`
  tuples, not bare strings.** Every existing caller (production code and tests) must be
  updated in the same task that changes the signature — no caller may be left unpacking
  a plain string.
- **Testing conventions (established M0-M4, keep following them):** `FakeEngine` backs
  every non-integration test that touches an engine; `requires_ffmpeg =
  pytest.mark.skipif(shutil.which("ffmpeg") is None, ...)` marks any test whose path
  reaches `export_chapter_mp3`/`export_mp3`; `tmp_path` for all database/filesystem
  tests; `os.chmod`/`os.chmod(..., 0o700)` cleanup in a `try`/`finally` for every test
  that locks down a directory's permissions, so `tmp_path`'s own cleanup doesn't fail
  afterward.
- **No emojis anywhere in the frontend** (standing UI rule) — use `lucide-react` icons
  for the new folder/checkmark/warning indicators, exactly as the rest of this project
  already does.
- **No new integration test is needed for this plan.** Nothing here adds a new
  `from_pretrained`-style heavy ML call — the existing Chatterbox/wav2vec2 integration
  tests remain sufficient coverage for that surface.

---

## File Structure

```
audiobookify/
  src/audiobookify/
    api/app.py                    # + _check_writable(), GET /browse-dir, GET /validate-output-dir,
                                   #   POST /books writability guard, used_fallback in response dicts
    models.py                     # Chapter, TextClip + used_fallback: bool = False
    db.py                         # + used_fallback column/migration, update_chapter_status/
                                   #   finish_text_clip gain used_fallback param
    postprocess/audio.py          # + BACKUP_OUTPUT_DIR, fallback_dir param, (path, used_fallback) returns
    synth/pipeline.py             # synthesize_chapter/synthesize_text_clip pass fallback_dir, store flag
  frontend/
    src/lib/api.js                # + browseDir(), validateOutputDir()
    src/components/DirectoryBrowser.jsx   # new
    src/pages/HomePage.jsx        # ImportBookModal uses DirectoryBrowser instead of a text input
    src/pages/BookDetailPage.jsx  # + "backup location" badge per chapter
    src/pages/TextPage.jsx        # + "backup location" badge per clip
  README.md                       # output-folder usage note updated for the picker
  tests/
    test_api.py                   # + browse-dir, validate-output-dir, POST /books rejection,
                                   #   used_fallback surfaced in book/clip responses
    test_db.py                    # + used_fallback CRUD + migration tests
    test_postprocess_audio.py     # existing test updated for tuple return + new fallback tests
    test_pipeline.py              # + synthesize_chapter/synthesize_text_clip fallback wiring tests
```

---

### Task 1: Backend directory browsing & validation — `_check_writable`, `GET /api/browse-dir`, `GET /api/validate-output-dir`

**Files:**
- Modify: `src/audiobookify/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: nothing new — `Path`, `HTTPException`, `Optional` are already imported in
  `app.py`.
- Produces: `_check_writable(path: Path) -> bool` (module-level function in
  `audiobookify.api.app`). Routes `GET /api/browse-dir?path=<path>` (defaults to the
  home directory) → `{"path": str, "parent": str|None, "writable": bool, "entries":
  [{"name": str, "writable": bool}, ...]}`, 404 if `path` isn't a directory. `GET
  /api/validate-output-dir?path=<path>` → `{"path": str, "writable": bool, "error":
  str|None}` — unlike `browse-dir`, this works for a path that doesn't exist yet
  (`_check_writable` creates it via `mkdir(parents=True)` as part of the probe). Task 2
  (`POST /api/books`) calls `_check_writable` directly (no HTTP round-trip needed,
  it's a plain function in the same module). Task 6 (frontend) calls both new routes.

- [ ] **Step 1: Write the failing tests**

Add `import os` near the top of `tests/test_api.py` (alongside the existing `import
shutil`).

Append to `tests/test_api.py`:

```python
def test_browse_dir_lists_subdirectories_with_writability(tmp_path):
    client = _client(tmp_path)
    (tmp_path / "books").mkdir()
    (tmp_path / "locked").mkdir()
    os.chmod(tmp_path / "locked", 0o500)
    (tmp_path / "afile.txt").write_text("not a directory")

    try:
        response = client.get("/api/browse-dir", params={"path": str(tmp_path)})

        assert response.status_code == 200
        body = response.json()
        assert body["path"] == str(tmp_path.resolve())
        assert body["parent"] == str(tmp_path.resolve().parent)
        entries = {e["name"]: e["writable"] for e in body["entries"]}
        assert entries == {"books": True, "locked": False}
    finally:
        os.chmod(tmp_path / "locked", 0o700)


def test_browse_dir_returns_404_for_a_nonexistent_path(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/browse-dir", params={"path": str(tmp_path / "nope")})

    assert response.status_code == 404


def test_browse_dir_parent_is_none_at_filesystem_root(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/browse-dir", params={"path": "/"})

    assert response.status_code == 200
    assert response.json()["parent"] is None


def test_validate_output_dir_reports_writable_for_a_creatable_path(tmp_path):
    client = _client(tmp_path)
    new_dir = tmp_path / "brand-new-book-folder"

    response = client.get("/api/validate-output-dir", params={"path": str(new_dir)})

    assert response.status_code == 200
    body = response.json()
    assert body["writable"] is True
    assert body["error"] is None
    assert new_dir.is_dir()


def test_validate_output_dir_reports_error_for_a_permission_denied_path(tmp_path):
    client = _client(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)

    try:
        response = client.get("/api/validate-output-dir", params={"path": str(locked)})

        assert response.status_code == 200
        body = response.json()
        assert body["writable"] is False
        assert body["error"] is not None
    finally:
        os.chmod(locked, 0o700)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v -k "browse_dir or validate_output_dir"`
Expected: FAIL — `404 Not Found` for the browse-dir routes (they don't exist yet, FastAPI
returns its own generic 404 for unmatched routes, not the app's `HTTPException` 404), and
similarly for `validate-output-dir`.

- [ ] **Step 3: Add `_check_writable` and the two routes to `src/audiobookify/api/app.py`**

Add this module-level function right after the imports, before `def create_app(...):`:

```python
def _check_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".audiobookify_write_test"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False
```

Add these two routes inside `create_app`, after the `get_book` route and before the
`generate` route:

```python
    @app.get("/api/browse-dir")
    def browse_dir(path: Optional[str] = None):
        current = Path(path).expanduser().resolve() if path else Path.home().resolve()
        if not current.is_dir():
            raise HTTPException(status_code=404, detail=f"not a directory: {current}")

        try:
            children = sorted(current.iterdir())
        except PermissionError:
            children = []

        entries = [
            {"name": child.name, "writable": _check_writable(child)}
            for child in children if child.is_dir()
        ]
        parent = str(current.parent) if current.parent != current else None

        return {
            "path": str(current),
            "parent": parent,
            "writable": _check_writable(current),
            "entries": entries,
        }

    @app.get("/api/validate-output-dir")
    def validate_output_dir(path: str):
        resolved = Path(path).expanduser().resolve()
        if _check_writable(resolved):
            return {"path": str(resolved), "writable": True, "error": None}
        return {
            "path": str(resolved), "writable": False,
            "error": f"cannot write to '{resolved}' — check the folder exists and you have permission",
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v -k "browse_dir or validate_output_dir"`
Expected: all pass (5)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (215 + 5 = 220), 2 deselected

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/api/app.py tests/test_api.py
git commit -m "feat: directory browsing and writability-validation endpoints"
```

---

### Task 2: `POST /api/books` rejects an unwritable `output_dir` before creating a book

**Files:**
- Modify: `src/audiobookify/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `_check_writable` (Task 1).
- Produces: `POST /api/books` now returns `400` (with a `detail` message containing
  `"cannot write to"`) for an unwritable `output_dir`, before touching the upload
  staging directory or the database, regardless of whether the path came from the new
  picker (Task 6) or was sent as a raw string.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py`:

```python
def test_create_book_rejects_an_unwritable_output_dir(tmp_path):
    client = _client(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)

    try:
        response = client.post(
            "/api/books",
            files={"file": ("book.md", b"# Ch\nBody.\n", "text/markdown")},
            data={"output_dir": str(locked)},
        )

        assert response.status_code == 400
        assert "cannot write to" in response.json()["detail"]
    finally:
        os.chmod(locked, 0o700)

    db = Database(str(tmp_path / "test.db"))
    assert db.list_books() == []
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api.py -v -k rejects_an_unwritable_output_dir`
Expected: FAIL — response status is `200`, not `400` (today's `create_book` never
checks writability).

- [ ] **Step 3: Add the writability guard to `create_book` in `src/audiobookify/api/app.py`**

Change:

```python
    @app.post("/api/books")
    def create_book(file: UploadFile = File(...), output_dir: str = Form(...),
                     db: Database = Depends(get_db)):
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
            shutil.rmtree(staged_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc))

        book = db.create_book(
            title=parsed.title, author=parsed.author, source_path=str(staged_path.resolve()),
            source_format=source_format, output_dir=str(Path(output_dir).resolve()),
        )
        db.add_chapters(book.id, parsed.chapters)

        return JSONResponse(_book_to_dict(db, book))
```

to:

```python
    @app.post("/api/books")
    def create_book(file: UploadFile = File(...), output_dir: str = Form(...),
                     db: Database = Depends(get_db)):
        filename = Path(file.filename).name
        suffix = Path(filename).suffix.lower()
        if suffix not in (".epub", ".md"):
            raise HTTPException(status_code=400, detail=f"unsupported input format: {suffix}")

        resolved_output_dir = Path(output_dir).resolve()
        if not _check_writable(resolved_output_dir):
            raise HTTPException(
                status_code=400,
                detail=f"cannot write to '{resolved_output_dir}' — check the folder exists "
                        "and you have permission to write to it",
            )

        staged_dir = Path(upload_dir) / uuid.uuid4().hex
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_path = staged_dir / filename
        staged_path.write_bytes(file.file.read())

        try:
            parsed, source_format = ingest_by_path(str(staged_path.resolve()))
        except ValueError as exc:
            shutil.rmtree(staged_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc))

        book = db.create_book(
            title=parsed.title, author=parsed.author, source_path=str(staged_path.resolve()),
            source_format=source_format, output_dir=str(resolved_output_dir),
        )
        db.add_chapters(book.id, parsed.chapters)

        return JSONResponse(_book_to_dict(db, book))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: all pass (220 + 1 = 221) — including the pre-existing
`test_create_book_ingests_uploaded_markdown_and_returns_chapters`, whose `output_dir`
(`tmp_path / "out"`) doesn't exist yet at request time; `_check_writable`'s
`mkdir(parents=True, exist_ok=True)` creates it as part of the check, so that test's
behavior is unaffected.

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (220 + 1 = 221), 2 deselected

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/api/app.py tests/test_api.py
git commit -m "feat: reject unwritable output_dir at import time, before any generation"
```

---

### Task 3: `used_fallback` schema, model fields, and migration

**Files:**
- Modify: `src/audiobookify/models.py`
- Modify: `src/audiobookify/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: existing `Chapter`/`TextClip` dataclasses, `SCHEMA` string, existing
  `_migrate_lexicon_entries_columns` pattern.
- Produces: `Chapter.used_fallback: bool = False`, `TextClip.used_fallback: bool =
  False` (`audiobookify.models`). `Database.update_chapter_status(..., used_fallback:
  bool = False)`, `Database.finish_text_clip(..., used_fallback: bool = False)`. Task 4
  (postprocess/pipeline) passes real values for these; Task 5 (API) reads
  `chapter.used_fallback`/`clip.used_fallback` off the returned dataclasses.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_update_chapter_status_sets_used_fallback(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])

    db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path="/out/01 - Ch 1.mp3",
                              used_fallback=True)

    [reloaded] = db.list_chapters(book.id)
    assert reloaded.used_fallback is True
    db.close()


def test_update_chapter_status_defaults_used_fallback_to_false(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])

    db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path="/out/01 - Ch 1.mp3")

    [reloaded] = db.list_chapters(book.id)
    assert reloaded.used_fallback is False
    db.close()


def test_finish_text_clip_sets_used_fallback(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    clip = db.create_text_clip(text="Hello.", voice_profile_id=None)

    db.finish_text_clip(clip.id, JobStatus.DONE, output_path="/out/clip.mp3", used_fallback=True)

    assert db.get_text_clip(clip.id).used_fallback is True
    db.close()


def test_existing_pre_fallback_database_gains_used_fallback_columns_via_migration(tmp_path):
    # Simulates opening a database file created before this change (no used_fallback
    # column on chapters/text_clips) - the migration in Database.__init__ must add it
    # without raising.
    import sqlite3

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            order_index INTEGER NOT NULL,
            title TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            output_mp3_path TEXT,
            duration REAL,
            error TEXT,
            UNIQUE(book_id, order_index)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE text_clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            voice_profile_id INTEGER,
            status TEXT NOT NULL DEFAULT 'queued',
            progress REAL,
            output_path TEXT,
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
        title="T", author=None, source_path="/books/t.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    assert chapter.used_fallback is False
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v -k "used_fallback"`
Expected: FAIL — `TypeError: update_chapter_status() got an unexpected keyword argument
'used_fallback'` (and similarly for `finish_text_clip`); the migration test fails with
`AttributeError: 'Chapter' object has no attribute 'used_fallback'`.

- [ ] **Step 3: Add `used_fallback` to `Chapter` and `TextClip` in `src/audiobookify/models.py`**

Change:

```python
@dataclass
class Chapter:
    id: int
    book_id: int
    order_index: int
    title: str
    text: str
    status: ChapterStatus = ChapterStatus.PENDING
    output_mp3_path: Optional[str] = None
    duration: Optional[float] = None
    error: Optional[str] = None
```

to:

```python
@dataclass
class Chapter:
    id: int
    book_id: int
    order_index: int
    title: str
    text: str
    status: ChapterStatus = ChapterStatus.PENDING
    output_mp3_path: Optional[str] = None
    duration: Optional[float] = None
    error: Optional[str] = None
    used_fallback: bool = False
```

Change:

```python
@dataclass
class TextClip:
    id: int
    text: str
    voice_profile_id: Optional[int]
    status: JobStatus
    progress: Optional[float]
    output_path: Optional[str]
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
```

to:

```python
@dataclass
class TextClip:
    id: int
    text: str
    voice_profile_id: Optional[int]
    status: JobStatus
    progress: Optional[float]
    output_path: Optional[str]
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    used_fallback: bool = False
```

- [ ] **Step 4: Update `src/audiobookify/db.py`**

Add the migration function right after `_migrate_lexicon_entries_columns`:

```python
def _migrate_fallback_columns(conn: sqlite3.Connection) -> None:
    chapter_cols = {row["name"] for row in conn.execute("PRAGMA table_info(chapters)")}
    if "used_fallback" not in chapter_cols:
        conn.execute("ALTER TABLE chapters ADD COLUMN used_fallback INTEGER NOT NULL DEFAULT 0")
    clip_cols = {row["name"] for row in conn.execute("PRAGMA table_info(text_clips)")}
    if "used_fallback" not in clip_cols:
        conn.execute("ALTER TABLE text_clips ADD COLUMN used_fallback INTEGER NOT NULL DEFAULT 0")
```

Change `Database.__init__` from:

```python
        self.conn.executescript(SCHEMA)
        _migrate_lexicon_entries_columns(self.conn)
        self.conn.commit()
```

to:

```python
        self.conn.executescript(SCHEMA)
        _migrate_lexicon_entries_columns(self.conn)
        _migrate_fallback_columns(self.conn)
        self.conn.commit()
```

Add `used_fallback INTEGER NOT NULL DEFAULT 0` to `SCHEMA`'s `chapters` table (right
before its closing `);`) and to `text_clips` (same), so a brand-new database gets the
column directly rather than only via the migration:

Change the `chapters` table definition from:

```sql
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id),
    order_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    output_mp3_path TEXT,
    duration REAL,
    error TEXT,
    UNIQUE(book_id, order_index)
);
```

to:

```sql
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id),
    order_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    output_mp3_path TEXT,
    duration REAL,
    error TEXT,
    used_fallback INTEGER NOT NULL DEFAULT 0,
    UNIQUE(book_id, order_index)
);
```

Change the `text_clips` table definition from:

```sql
CREATE TABLE IF NOT EXISTS text_clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    voice_profile_id INTEGER REFERENCES voice_profiles(id),
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL,
    output_path TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    error TEXT
);
```

to:

```sql
CREATE TABLE IF NOT EXISTS text_clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    voice_profile_id INTEGER REFERENCES voice_profiles(id),
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL,
    output_path TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    error TEXT,
    used_fallback INTEGER NOT NULL DEFAULT 0
);
```

Change `_row_to_chapter` from:

```python
def _row_to_chapter(row: sqlite3.Row) -> Chapter:
    return Chapter(
        id=row["id"], book_id=row["book_id"], order_index=row["order_index"],
        title=row["title"], text=row["text"], status=ChapterStatus(row["status"]),
        output_mp3_path=row["output_mp3_path"], duration=row["duration"], error=row["error"],
    )
```

to:

```python
def _row_to_chapter(row: sqlite3.Row) -> Chapter:
    return Chapter(
        id=row["id"], book_id=row["book_id"], order_index=row["order_index"],
        title=row["title"], text=row["text"], status=ChapterStatus(row["status"]),
        output_mp3_path=row["output_mp3_path"], duration=row["duration"], error=row["error"],
        used_fallback=bool(row["used_fallback"]),
    )
```

Change `_row_to_text_clip` from:

```python
def _row_to_text_clip(row: sqlite3.Row) -> TextClip:
    return TextClip(
        id=row["id"], text=row["text"], voice_profile_id=row["voice_profile_id"],
        status=JobStatus(row["status"]), progress=row["progress"], output_path=row["output_path"],
        created_at=row["created_at"], started_at=row["started_at"], finished_at=row["finished_at"],
        error=row["error"],
    )
```

to:

```python
def _row_to_text_clip(row: sqlite3.Row) -> TextClip:
    return TextClip(
        id=row["id"], text=row["text"], voice_profile_id=row["voice_profile_id"],
        status=JobStatus(row["status"]), progress=row["progress"], output_path=row["output_path"],
        created_at=row["created_at"], started_at=row["started_at"], finished_at=row["finished_at"],
        error=row["error"], used_fallback=bool(row["used_fallback"]),
    )
```

Change `update_chapter_status` from:

```python
    def update_chapter_status(self, chapter_id: int, status: ChapterStatus,
                               output_mp3_path: Optional[str] = None,
                               duration: Optional[float] = None,
                               error: Optional[str] = None) -> None:
        cur = self.conn.execute(
            "UPDATE chapters SET status = ?, output_mp3_path = COALESCE(?, output_mp3_path), "
            "duration = COALESCE(?, duration), error = ? WHERE id = ?",
            (status.value, output_mp3_path, duration, error, chapter_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"no chapter with id {chapter_id}")
        self.conn.commit()
```

to:

```python
    def update_chapter_status(self, chapter_id: int, status: ChapterStatus,
                               output_mp3_path: Optional[str] = None,
                               duration: Optional[float] = None,
                               error: Optional[str] = None,
                               used_fallback: bool = False) -> None:
        cur = self.conn.execute(
            "UPDATE chapters SET status = ?, output_mp3_path = COALESCE(?, output_mp3_path), "
            "duration = COALESCE(?, duration), error = ?, used_fallback = ? WHERE id = ?",
            (status.value, output_mp3_path, duration, error, int(used_fallback), chapter_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"no chapter with id {chapter_id}")
        self.conn.commit()
```

Change `finish_text_clip` from:

```python
    def finish_text_clip(self, clip_id: int, status: JobStatus, output_path: Optional[str] = None,
                          error: Optional[str] = None) -> None:
        if status == JobStatus.DONE:
            self.conn.execute(
                "UPDATE text_clips SET status = ?, progress = 1.0, output_path = ?, "
                "finished_at = ?, error = ? WHERE id = ?",
                (status.value, output_path, time.time(), error, clip_id),
            )
        else:
            self.conn.execute(
                "UPDATE text_clips SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status.value, time.time(), error, clip_id),
            )
        self.conn.commit()
```

to:

```python
    def finish_text_clip(self, clip_id: int, status: JobStatus, output_path: Optional[str] = None,
                          error: Optional[str] = None, used_fallback: bool = False) -> None:
        if status == JobStatus.DONE:
            self.conn.execute(
                "UPDATE text_clips SET status = ?, progress = 1.0, output_path = ?, "
                "finished_at = ?, error = ?, used_fallback = ? WHERE id = ?",
                (status.value, output_path, time.time(), error, int(used_fallback), clip_id),
            )
        else:
            self.conn.execute(
                "UPDATE text_clips SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status.value, time.time(), error, clip_id),
            )
        self.conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: all pass (existing + 4 new)

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (221 + 4 = 225), 2 deselected

- [ ] **Step 7: Commit**

```bash
git add src/audiobookify/models.py src/audiobookify/db.py tests/test_db.py
git commit -m "feat: used_fallback column, model field, and migration for chapters/text clips"
```

---

### Task 4: Backup fallback in `export_chapter_mp3`/`export_mp3`, wired through `synthesize_chapter`/`synthesize_text_clip`

**Files:**
- Modify: `src/audiobookify/postprocess/audio.py`
- Modify: `src/audiobookify/synth/pipeline.py`
- Test: `tests/test_postprocess_audio.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Database.update_chapter_status(..., used_fallback)`,
  `Database.finish_text_clip(..., used_fallback)` (Task 3).
- Produces: `BACKUP_OUTPUT_DIR: str` (module constant, `audiobookify.postprocess.audio`,
  `<repo-root>/output`). `export_chapter_mp3(..., fallback_dir: Optional[str] = None) ->
  Tuple[str, bool]` and `export_mp3(..., fallback_dir: Optional[str] = None) ->
  Tuple[str, bool]` — both raise their original exception unchanged if the primary
  write fails and no `fallback_dir` was given. `synthesize_chapter`/
  `synthesize_text_clip` (`audiobookify.synth.pipeline`) now pass
  `fallback_dir=str(Path(BACKUP_OUTPUT_DIR) / str(book.id))` /
  `fallback_dir=str(Path(BACKUP_OUTPUT_DIR) / "text-clips")` respectively, and persist
  the returned `used_fallback` flag via `Database`. Task 5 (API) reads the persisted
  flag; it does not touch this task's functions directly.

- [ ] **Step 1: Write the failing tests**

Change the import block at the top of `tests/test_postprocess_audio.py` from:

```python
import shutil

import numpy as np
import pytest
from mutagen.mp3 import MP3

from audiobookify.postprocess.audio import (
    chapter_filename,
    concatenate_chunks,
    export_chapter_mp3,
    sanitize_filename,
)

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
```

to:

```python
import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from mutagen.mp3 import MP3

from audiobookify.postprocess.audio import (
    chapter_filename,
    concatenate_chunks,
    export_chapter_mp3,
    export_mp3,
    sanitize_filename,
)

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
```

Change the existing `test_export_chapter_mp3_writes_tagged_file` from:

```python
@requires_ffmpeg
def test_export_chapter_mp3_writes_tagged_file(tmp_path):
    sample_rate = 22050
    audio = np.sin(np.linspace(0, 440 * 2 * np.pi, sample_rate)).astype(np.float32)

    mp3_path = export_chapter_mp3(
        audio, sample_rate, str(tmp_path), order_index=0,
        chapter_title="Chapter One", book_title="My Book", narrator="Chatterbox Default",
    )

    assert mp3_path.endswith("01 - Chapter One.mp3")

    tags = MP3(mp3_path)
    assert str(tags.get("TIT2")) == "Chapter One"
    assert str(tags.get("TALB")) == "My Book"
    assert str(tags.get("TRCK")) == "1"
    assert str(tags.get("TPE1")) == "Chatterbox Default"
```

to:

```python
@requires_ffmpeg
def test_export_chapter_mp3_writes_tagged_file(tmp_path):
    sample_rate = 22050
    audio = np.sin(np.linspace(0, 440 * 2 * np.pi, sample_rate)).astype(np.float32)

    mp3_path, used_fallback = export_chapter_mp3(
        audio, sample_rate, str(tmp_path), order_index=0,
        chapter_title="Chapter One", book_title="My Book", narrator="Chatterbox Default",
    )

    assert used_fallback is False
    assert mp3_path.endswith("01 - Chapter One.mp3")

    tags = MP3(mp3_path)
    assert str(tags.get("TIT2")) == "Chapter One"
    assert str(tags.get("TALB")) == "My Book"
    assert str(tags.get("TRCK")) == "1"
    assert str(tags.get("TPE1")) == "Chatterbox Default"
```

Append to `tests/test_postprocess_audio.py`:

```python
@requires_ffmpeg
def test_export_chapter_mp3_falls_back_when_primary_dir_is_unwritable(tmp_path):
    sample_rate = 22050
    audio = np.sin(np.linspace(0, 440 * 2 * np.pi, sample_rate)).astype(np.float32)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    fallback = tmp_path / "fallback"

    try:
        mp3_path, used_fallback = export_chapter_mp3(
            audio, sample_rate, str(locked), order_index=0,
            chapter_title="Chapter One", book_title="My Book", narrator="Narrator",
            fallback_dir=str(fallback),
        )
    finally:
        os.chmod(locked, 0o700)

    assert used_fallback is True
    assert mp3_path.startswith(str(fallback))
    assert Path(mp3_path).is_file()


@requires_ffmpeg
def test_export_chapter_mp3_raises_when_unwritable_and_no_fallback_given(tmp_path):
    sample_rate = 22050
    audio = np.sin(np.linspace(0, 440 * 2 * np.pi, sample_rate)).astype(np.float32)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)

    try:
        with pytest.raises((OSError, sf.LibsndfileError)):
            export_chapter_mp3(
                audio, sample_rate, str(locked), order_index=0,
                chapter_title="Chapter One", book_title="My Book", narrator="Narrator",
            )
    finally:
        os.chmod(locked, 0o700)


@requires_ffmpeg
def test_export_mp3_falls_back_when_primary_dir_is_unwritable(tmp_path):
    sample_rate = 22050
    audio = np.sin(np.linspace(0, 440 * 2 * np.pi, sample_rate)).astype(np.float32)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    fallback = tmp_path / "fallback"

    try:
        mp3_path, used_fallback = export_mp3(
            audio, sample_rate, str(locked / "clip.mp3"), fallback_dir=str(fallback),
        )
    finally:
        os.chmod(locked, 0o700)

    assert used_fallback is True
    assert mp3_path == str(fallback / "clip.mp3")
    assert Path(mp3_path).is_file()
```

Change the top of `tests/test_pipeline.py` from:

```python
import shutil

import pytest

from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, SourceFormat
from audiobookify.synth.pipeline import generate_book, synthesize_chapter

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
```

to:

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

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
```

Append to `tests/test_pipeline.py`:

```python
@requires_ffmpeg
def test_synthesize_chapter_records_used_fallback_when_output_dir_is_unwritable(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "test.db"))
    locked = tmp_path / "locked"
    locked.mkdir()
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(locked),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    os.chmod(locked, 0o500)
    monkeypatch.setattr(pipeline, "BACKUP_OUTPUT_DIR", str(tmp_path / "backup"))
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    try:
        result = synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"},
                                     [], db)
    finally:
        os.chmod(locked, 0o700)

    assert result.status == ChapterStatus.DONE
    assert result.used_fallback is True
    assert result.output_mp3_path.startswith(str(tmp_path / "backup"))
    db.close()


@requires_ffmpeg
def test_synthesize_text_clip_records_used_fallback_when_output_dir_is_unwritable(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "test.db"))
    clip = db.create_text_clip(text="Hello there.", voice_profile_id=None)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    monkeypatch.setattr(pipeline, "BACKUP_OUTPUT_DIR", str(tmp_path / "backup"))
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    try:
        result = synthesize_text_clip(clip, engine, profile_handle, {"voice_name": "fake"}, [], db,
                                       str(locked))
    finally:
        os.chmod(locked, 0o700)

    assert result.status == JobStatus.DONE
    assert result.used_fallback is True
    assert result.output_path.startswith(str(tmp_path / "backup"))
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_postprocess_audio.py tests/test_pipeline.py -v`
Expected: FAIL — the modified `test_export_chapter_mp3_writes_tagged_file` fails with
`ValueError: not enough values to unpack` (current `export_chapter_mp3` returns a bare
string, not a tuple); the two new `export_chapter_mp3`/`export_mp3` fallback tests fail
with `TypeError: ...() got an unexpected keyword argument 'fallback_dir'`; the two new
`synthesize_*` tests fail with `AttributeError: module 'audiobookify.synth.pipeline' has
no attribute 'BACKUP_OUTPUT_DIR'`.

- [ ] **Step 3: Refactor `src/audiobookify/postprocess/audio.py`**

Change the imports from:

```python
import re
import subprocess
from pathlib import Path
from typing import List, Optional

import numpy as np
import soundfile as sf
from mutagen.id3 import TALB, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3
```

to:

```python
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf
from mutagen.id3 import TALB, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3

BACKUP_OUTPUT_DIR = str(Path(__file__).resolve().parents[3] / "output")
```

Change `export_chapter_mp3` from:

```python
def export_chapter_mp3(audio: np.ndarray, sample_rate: int, output_dir: str, order_index: int,
                        chapter_title: str, book_title: str, narrator: str) -> str:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    wav_path = output_path / f".tmp_{order_index}.wav"
    sf.write(str(wav_path), audio, sample_rate)

    mp3_path = output_path / chapter_filename(order_index, chapter_title)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-qscale:a", "2", str(mp3_path)],
        check=True, capture_output=True,
    )
    wav_path.unlink()

    tags = MP3(str(mp3_path))
    if tags.tags is None:
        tags.add_tags()
    tags.tags.add(TIT2(encoding=3, text=chapter_title))
    tags.tags.add(TALB(encoding=3, text=book_title))
    tags.tags.add(TRCK(encoding=3, text=str(order_index + 1)))
    tags.tags.add(TPE1(encoding=3, text=narrator))
    tags.save()

    return str(mp3_path)
```

to:

```python
def _export_chapter_mp3_to(output_dir: str, audio: np.ndarray, sample_rate: int, order_index: int,
                            chapter_title: str, book_title: str, narrator: str) -> str:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    wav_path = output_path / f".tmp_{order_index}.wav"
    sf.write(str(wav_path), audio, sample_rate)

    mp3_path = output_path / chapter_filename(order_index, chapter_title)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-qscale:a", "2", str(mp3_path)],
        check=True, capture_output=True,
    )
    wav_path.unlink()

    tags = MP3(str(mp3_path))
    if tags.tags is None:
        tags.add_tags()
    tags.tags.add(TIT2(encoding=3, text=chapter_title))
    tags.tags.add(TALB(encoding=3, text=book_title))
    tags.tags.add(TRCK(encoding=3, text=str(order_index + 1)))
    tags.tags.add(TPE1(encoding=3, text=narrator))
    tags.save()

    return str(mp3_path)


def export_chapter_mp3(audio: np.ndarray, sample_rate: int, output_dir: str, order_index: int,
                        chapter_title: str, book_title: str, narrator: str,
                        fallback_dir: Optional[str] = None) -> Tuple[str, bool]:
    try:
        return _export_chapter_mp3_to(output_dir, audio, sample_rate, order_index,
                                       chapter_title, book_title, narrator), False
    except (OSError, sf.LibsndfileError):
        if fallback_dir is None:
            raise
        return _export_chapter_mp3_to(fallback_dir, audio, sample_rate, order_index,
                                       chapter_title, book_title, narrator), True
```

(`sf.LibsndfileError` — a `RuntimeError` subclass, not `OSError` — is what `soundfile`
actually raises when it can't open a file for writing in an unwritable-but-existing
directory; `mkdir(parents=True, exist_ok=True)` silently no-ops on a directory that
already exists without needing write permission to it, so the permission failure
surfaces at the `sf.write` call, not at `mkdir`. Both exception types must be caught for
the fallback to actually trigger on a real permission-denied directory.)

Change `export_mp3` from:

```python
def export_mp3(audio: np.ndarray, sample_rate: int, output_path: str,
                title: Optional[str] = None, artist: Optional[str] = None) -> str:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wav_path = output_path.with_suffix(".tmp.wav")
    sf.write(str(wav_path), audio, sample_rate)

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-qscale:a", "2", str(output_path)],
        check=True, capture_output=True,
    )
    wav_path.unlink()

    if title or artist:
        tags = MP3(str(output_path))
        if tags.tags is None:
            tags.add_tags()
        if title:
            tags.tags.add(TIT2(encoding=3, text=title))
        if artist:
            tags.tags.add(TPE1(encoding=3, text=artist))
        tags.save()

    return str(output_path)
```

to:

```python
def _export_mp3_to(output_path: Path, audio: np.ndarray, sample_rate: int,
                    title: Optional[str], artist: Optional[str]) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wav_path = output_path.with_suffix(".tmp.wav")
    sf.write(str(wav_path), audio, sample_rate)

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-qscale:a", "2", str(output_path)],
        check=True, capture_output=True,
    )
    wav_path.unlink()

    if title or artist:
        tags = MP3(str(output_path))
        if tags.tags is None:
            tags.add_tags()
        if title:
            tags.tags.add(TIT2(encoding=3, text=title))
        if artist:
            tags.tags.add(TPE1(encoding=3, text=artist))
        tags.save()

    return str(output_path)


def export_mp3(audio: np.ndarray, sample_rate: int, output_path: str,
                title: Optional[str] = None, artist: Optional[str] = None,
                fallback_dir: Optional[str] = None) -> Tuple[str, bool]:
    primary_path = Path(output_path)
    try:
        return _export_mp3_to(primary_path, audio, sample_rate, title, artist), False
    except (OSError, sf.LibsndfileError):
        if fallback_dir is None:
            raise
        fallback_path = Path(fallback_dir) / primary_path.name
        return _export_mp3_to(fallback_path, audio, sample_rate, title, artist), True
```

- [ ] **Step 4: Run `test_postprocess_audio.py` to verify it passes**

Run: `.venv/bin/pytest tests/test_postprocess_audio.py -v`
Expected: all pass (4 existing, 1 modified in place + 3 new = 7)

- [ ] **Step 5: Update `src/audiobookify/synth/pipeline.py`**

Change the import from:

```python
from ..postprocess.audio import concatenate_chunks, export_chapter_mp3, export_mp3
```

to:

```python
from ..postprocess.audio import BACKUP_OUTPUT_DIR, concatenate_chunks, export_chapter_mp3, export_mp3
```

Change `synthesize_chapter` from:

```python
        combined = concatenate_chunks(audio_chunks, engine.sample_rate())
        mp3_path = export_chapter_mp3(
            combined, engine.sample_rate(), book.output_dir, chapter.order_index,
            chapter.title, book.title, voice_params.get("voice_name", engine.name),
        )
        db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=mp3_path)
```

to:

```python
        combined = concatenate_chunks(audio_chunks, engine.sample_rate())
        mp3_path, used_fallback = export_chapter_mp3(
            combined, engine.sample_rate(), book.output_dir, chapter.order_index,
            chapter.title, book.title, voice_params.get("voice_name", engine.name),
            fallback_dir=str(Path(BACKUP_OUTPUT_DIR) / str(book.id)),
        )
        db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=mp3_path,
                                  used_fallback=used_fallback)
```

Change `synthesize_text_clip` from:

```python
        combined = concatenate_chunks(audio_chunks, engine.sample_rate())
        output_path = str(Path(output_dir) / f"text-clip-{clip.id}.mp3")
        mp3_path = export_mp3(
            combined, engine.sample_rate(), output_path,
            title=f"Text clip #{clip.id}", artist=voice_params.get("voice_name", engine.name),
        )
        db.finish_text_clip(clip.id, JobStatus.DONE, output_path=mp3_path)
```

to:

```python
        combined = concatenate_chunks(audio_chunks, engine.sample_rate())
        output_path = str(Path(output_dir) / f"text-clip-{clip.id}.mp3")
        mp3_path, used_fallback = export_mp3(
            combined, engine.sample_rate(), output_path,
            title=f"Text clip #{clip.id}", artist=voice_params.get("voice_name", engine.name),
            fallback_dir=str(Path(BACKUP_OUTPUT_DIR) / "text-clips"),
        )
        db.finish_text_clip(clip.id, JobStatus.DONE, output_path=mp3_path, used_fallback=used_fallback)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: all pass (existing + 2 new)

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (225 + 3 + 2 = 230), 2 deselected

- [ ] **Step 8: Commit**

```bash
git add src/audiobookify/postprocess/audio.py src/audiobookify/synth/pipeline.py \
        tests/test_postprocess_audio.py tests/test_pipeline.py
git commit -m "feat: fall back to output/ when the chosen output folder isn't writable"
```

---

### Task 5: Surface `used_fallback` in API responses

**Files:**
- Modify: `src/audiobookify/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Chapter.used_fallback`, `TextClip.used_fallback` (Task 3).
- Produces: `GET /api/books/{id}`'s per-chapter dict and `GET /api/text-clips/{id}`'s dict
  (and every other place `_text_clip_to_dict` is used: `list_text_clips`,
  `create_text_clip`) now include `"used_fallback": bool`. Task 7 (frontend) reads this
  field.

- [ ] **Step 1: Write the failing tests**

Change the top of `tests/test_api.py` from:

```python
from audiobookify.models import JobStatus
```

to:

```python
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, JobStatus, SourceFormat
```

Append to `tests/test_api.py`:

```python
def test_get_book_surfaces_used_fallback_per_chapter(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path="/backup/ch1.mp3",
                              used_fallback=True)
    db.close()

    response = client.get(f"/api/books/{book.id}")

    assert response.json()["chapters"][0]["used_fallback"] is True


def test_get_text_clip_surfaces_used_fallback(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    clip = db.create_text_clip(text="Hi.", voice_profile_id=None)
    db.finish_text_clip(clip.id, JobStatus.DONE, output_path="/backup/clip.mp3", used_fallback=True)
    db.close()

    response = client.get(f"/api/text-clips/{clip.id}")

    assert response.json()["used_fallback"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v -k "surfaces_used_fallback"`
Expected: FAIL — `KeyError: 'used_fallback'` (neither response dict includes the field
yet).

- [ ] **Step 3: Update `_book_to_dict` and `_text_clip_to_dict` in `src/audiobookify/api/app.py`**

Change:

```python
            chapters.append({
                "id": chapter.id,
                "order_index": chapter.order_index,
                "title": chapter.title,
                "status": chapter.status.value,
                "output_mp3_path": chapter.output_mp3_path,
                "error": chapter.error,
                "progress": job.progress if job else None,
            })
```

to:

```python
            chapters.append({
                "id": chapter.id,
                "order_index": chapter.order_index,
                "title": chapter.title,
                "status": chapter.status.value,
                "output_mp3_path": chapter.output_mp3_path,
                "error": chapter.error,
                "progress": job.progress if job else None,
                "used_fallback": chapter.used_fallback,
            })
```

Change:

```python
    def _text_clip_to_dict(clip) -> dict:
        return {
            "id": clip.id,
            "text": clip.text,
            "voice_profile_id": clip.voice_profile_id,
            "status": clip.status.value,
            "progress": clip.progress,
            "error": clip.error,
            "created_at": clip.created_at,
        }
```

to:

```python
    def _text_clip_to_dict(clip) -> dict:
        return {
            "id": clip.id,
            "text": clip.text,
            "voice_profile_id": clip.voice_profile_id,
            "status": clip.status.value,
            "progress": clip.progress,
            "error": clip.error,
            "created_at": clip.created_at,
            "used_fallback": clip.used_fallback,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: all pass (221 + 2 = 223)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (230 + 2 = 232), 2 deselected

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/api/app.py tests/test_api.py
git commit -m "feat: surface used_fallback in book/text-clip API responses"
```

---

### Task 6: Frontend `DirectoryBrowser` component, wired into `ImportBookModal`

**Files:**
- Modify: `frontend/src/lib/api.js`
- Create: `frontend/src/components/DirectoryBrowser.jsx`
- Modify: `frontend/src/pages/HomePage.jsx`
- Modify: `README.md`

**Interfaces:**
- Consumes: `GET /api/browse-dir`, `GET /api/validate-output-dir` (Task 1).
- Produces: `api.browseDir(path?: string)`, `api.validateOutputDir(path: string)`
  (`frontend/src/lib/api.js`). `<DirectoryBrowser value onChange onValidityChange />`
  component, replacing the free-text `output_dir` field in `ImportBookModal`.

No automated tests for this task — this project has no frontend test framework
(established precedent, see the M4 backend/worker skeleton plan); verified manually in
step 5 below.

- [ ] **Step 1: Add `browseDir`/`validateOutputDir` to `frontend/src/lib/api.js`**

Change:

```js
export const api = {
  listBooks: () => request("/api/books"),
  getBook: (id) => request(`/api/books/${id}`),
  createBook: (formData) => request("/api/books", { method: "POST", body: formData }),
```

to:

```js
export const api = {
  listBooks: () => request("/api/books"),
  getBook: (id) => request(`/api/books/${id}`),
  createBook: (formData) => request("/api/books", { method: "POST", body: formData }),
  browseDir: (path) => request(`/api/browse-dir${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  validateOutputDir: (path) => request(`/api/validate-output-dir?path=${encodeURIComponent(path)}`),
```

- [ ] **Step 2: Create `frontend/src/components/DirectoryBrowser.jsx`**

```jsx
import { useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowUp, Check, Folder, Loader2 } from "lucide-react";
import { api } from "../lib/api.js";

export default function DirectoryBrowser({ value, onChange, onValidityChange }) {
  const [browsed, setBrowsed] = useState(null);
  const [browseError, setBrowseError] = useState(null);
  const [status, setStatus] = useState(null);
  const [checking, setChecking] = useState(false);
  const debounceRef = useRef(null);

  async function browse(path) {
    setBrowseError(null);
    try {
      const result = await api.browseDir(path);
      setBrowsed(result);
    } catch (e) {
      setBrowseError(e.message);
    }
  }

  useEffect(() => {
    browse(null); // start at the home directory
  }, []);

  useEffect(() => {
    clearTimeout(debounceRef.current);
    if (!value.trim()) {
      setStatus(null);
      onValidityChange?.(false);
      return;
    }
    setChecking(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const result = await api.validateOutputDir(value.trim());
        setStatus(result);
        onValidityChange?.(result.writable);
      } catch (e) {
        setStatus({ writable: false, error: e.message });
        onValidityChange?.(false);
      } finally {
        setChecking(false);
      }
    }, 400);
    return () => clearTimeout(debounceRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  function selectFolder(path) {
    onChange(path);
    browse(path);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="rounded-lg border border-border overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-muted/40 text-sm">
          <button
            type="button"
            onClick={() => browsed?.parent && browse(browsed.parent)}
            disabled={!browsed?.parent}
            aria-label="Go up one folder"
            className="cursor-pointer rounded p-1 text-muted-foreground hover:bg-muted
              disabled:cursor-not-allowed disabled:opacity-40
              focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <ArrowUp className="size-4" aria-hidden="true" />
          </button>
          <span className="truncate font-mono text-xs" title={browsed?.path}>
            {browsed?.path || "Loading…"}
          </span>
        </div>

        {browseError && <p className="text-sm text-destructive px-3 py-2">{browseError}</p>}

        <div className="max-h-48 overflow-y-auto divide-y divide-border">
          {browsed && browsed.entries.length === 0 && (
            <p className="text-sm text-muted-foreground px-3 py-2">No subfolders here.</p>
          )}
          {browsed?.entries.map((entry) => (
            <button
              key={entry.name}
              type="button"
              disabled={!entry.writable}
              onClick={() => browse(`${browsed.path}/${entry.name}`)}
              title={entry.writable ? undefined : "You don't have permission to write here"}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left cursor-pointer
                hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              <Folder className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="truncate">{entry.name}</span>
            </button>
          ))}
        </div>

        <div className="px-3 py-2 border-t border-border">
          <button
            type="button"
            onClick={() => browsed && selectFolder(browsed.path)}
            disabled={!browsed?.writable}
            className="text-sm font-medium text-primary hover:underline cursor-pointer
              disabled:cursor-not-allowed disabled:opacity-40 disabled:no-underline"
          >
            Use this folder
          </button>
        </div>
      </div>

      <div>
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="/Users/you/audiobooks/this-book"
          className="w-full h-10 rounded-lg border border-border bg-background px-3 text-sm font-mono
            focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        />
        <div className="mt-1 min-h-5 flex items-center gap-1.5 text-xs">
          {checking && (
            <>
              <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-hidden="true" />
              <span className="text-muted-foreground">Checking…</span>
            </>
          )}
          {!checking && status?.writable === true && (
            <>
              <Check className="size-3.5 text-success" aria-hidden="true" />
              <span className="text-success">This folder is writable</span>
            </>
          )}
          {!checking && status?.writable === false && (
            <>
              <AlertCircle className="size-3.5 text-destructive" aria-hidden="true" />
              <span className="text-destructive">{status.error || "Can't write here"}</span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire `DirectoryBrowser` into `ImportBookModal` in `frontend/src/pages/HomePage.jsx`**

Add the import at the top, alongside the other component imports:

```jsx
import DirectoryBrowser from "../components/DirectoryBrowser.jsx";
```

Change the `ImportBookModal` component's state and form from:

```jsx
function ImportBookModal({ onClose, onImported }) {
  const [outputDir, setOutputDir] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
```

to:

```jsx
function ImportBookModal({ onClose, onImported }) {
  const [outputDir, setOutputDir] = useState("");
  const [outputDirWritable, setOutputDirWritable] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
```

Change:

```jsx
        <div>
          <label htmlFor="output_dir" className="block text-sm font-medium mb-1">
            Output folder path
          </label>
          <input
            id="output_dir"
            type="text"
            value={outputDir}
            onChange={(e) => setOutputDir(e.target.value)}
            required
            placeholder="/Users/you/audiobooks/this-book"
            className="w-full h-10 rounded-lg border border-border bg-background px-3 text-sm
              focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          />
        </div>
```

to:

```jsx
        <div>
          <label className="block text-sm font-medium mb-1">Output folder</label>
          <DirectoryBrowser
            value={outputDir}
            onChange={setOutputDir}
            onValidityChange={setOutputDirWritable}
          />
        </div>
```

Change:

```jsx
          <Button type="submit" loading={submitting}>
            <Upload className="size-4" aria-hidden="true" />
            Import
          </Button>
```

to:

```jsx
          <Button type="submit" loading={submitting} disabled={!outputDirWritable}>
            <Upload className="size-4" aria-hidden="true" />
            Import
          </Button>
```

- [ ] **Step 4: Update `README.md`'s GUI usage note**

Change:

```
Open `http://127.0.0.1:5173`, upload an EPUB or Markdown file, type an output folder
path, click Import, then click "Generate all". The chapter list polls the backend every
```

to:

```
Open `http://127.0.0.1:5173`, upload an EPUB or Markdown file, browse to (or type) an
output folder, click Import, then click "Generate all". The folder picker checks
writability live and won't let you import into a folder it can't write to; if a folder
somehow goes bad *after* import (permissions changed, drive unmounted), that chapter's
audio is saved under this repo's `output/` folder instead, and the chapter list flags it.
The chapter list polls the backend every
```

- [ ] **Step 5: Manually verify in a running browser**

Run: `make dev`

In the browser at `http://127.0.0.1:5173`:
1. Click "Import book", confirm the folder browser loads starting at your home
   directory, and that navigating into subfolders (and back up via the up-arrow) works.
2. Confirm a folder you don't have write access to (or create one with `chmod 500`)
   shows disabled/greyed-out in the listing and can't be selected.
3. Type a brand-new subfolder name into the text field below the browser and confirm
   the green "writable" check appears after the debounce.
4. Confirm the Import button stays disabled until the path shows as writable, then
   succeeds once it does.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api.js frontend/src/components/DirectoryBrowser.jsx \
        frontend/src/pages/HomePage.jsx README.md
git commit -m "feat: folder browser replaces the free-text output path field"
```

---

### Task 7: Frontend "backup location" warning badges

**Files:**
- Modify: `frontend/src/pages/BookDetailPage.jsx`
- Modify: `frontend/src/pages/TextPage.jsx`

**Interfaces:**
- Consumes: `chapter.used_fallback`, `clip.used_fallback` (Task 5's API responses).
- Produces: a small warning indicator next to any chapter or clip whose audio ended up
  under the `output/` backup location instead of the requested folder.

No automated tests for this task (same rationale as Task 6 — frontend-only, no test
framework in this project); verified manually in step 3 below.

- [ ] **Step 1: Add the badge to `frontend/src/pages/BookDetailPage.jsx`**

Change the icon import from:

```jsx
import { ArrowLeft, Loader2, Play } from "lucide-react";
```

to:

```jsx
import { AlertTriangle, ArrowLeft, Loader2, Play } from "lucide-react";
```

Change:

```jsx
              <Badge status={badgeStatus} label={badgeStatus} />
              {chapter.output_mp3_path && (
                <span
                  className="text-xs text-muted-foreground font-mono truncate max-w-40"
                  title={chapter.output_mp3_path}
                >
                  {chapter.output_mp3_path.split("/").pop()}
                </span>
              )}
```

to:

```jsx
              <Badge status={badgeStatus} label={badgeStatus} />
              {chapter.used_fallback && (
                <span
                  title="The output folder wasn't writable when this chapter finished — saved to the backup location instead."
                  className="inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400"
                >
                  <AlertTriangle className="size-3.5" aria-hidden="true" />
                  Backup location
                </span>
              )}
              {chapter.output_mp3_path && (
                <span
                  className="text-xs text-muted-foreground font-mono truncate max-w-40"
                  title={chapter.output_mp3_path}
                >
                  {chapter.output_mp3_path.split("/").pop()}
                </span>
              )}
```

- [ ] **Step 2: Add the badge to `frontend/src/pages/TextPage.jsx`**

Change the icon import from:

```jsx
import { Download, Send, Trash2, Type } from "lucide-react";
```

to:

```jsx
import { AlertTriangle, Download, Send, Trash2, Type } from "lucide-react";
```

Change:

```jsx
                  <div className="flex items-center gap-2 shrink-0">
                    <Badge status={clip.status} label={clip.status} />
                    <button
```

to:

```jsx
                  <div className="flex items-center gap-2 shrink-0">
                    <Badge status={clip.status} label={clip.status} />
                    {clip.used_fallback && (
                      <span
                        title="The output folder wasn't writable when this clip finished — saved to the backup location instead."
                        className="inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400"
                      >
                        <AlertTriangle className="size-3.5" aria-hidden="true" />
                        Backup
                      </span>
                    )}
                    <button
```

- [ ] **Step 3: Manually verify in a running browser**

Run: `make dev`

Since the fallback path only triggers on a real write failure, force it: create a book
with a writable `output_dir`, then before clicking "Generate all", `chmod 500` that
directory from a terminal. Generate a chapter and confirm:
1. The chapter still ends up `done` (not `failed`) with the "Backup location" badge
   shown, and its filename (in the small mono text next to the badge) reflects the
   backup path.
2. The same for a text clip on the Text page, using the same `chmod 500` trick on its
   output directory (`text_clips/` by default, per `worker.DEFAULT_TEXT_CLIP_OUTPUT_DIR`).

Restore permissions afterward (`chmod 700`) so the directory can be cleaned up normally.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/BookDetailPage.jsx frontend/src/pages/TextPage.jsx
git commit -m "feat: show a backup-location warning badge on chapters/clips that used it"
```

---

## Self-Review Notes

- **Spec coverage:** `_check_writable` + `browse-dir` (spec section "Backend: directory
  browsing & validation") → Task 1. `POST /api/books` import-time rejection → Task 2.
  `used_fallback` data model → Task 3. Backup fallback in the export functions → Task 4.
  API surfacing → Task 5. `DirectoryBrowser` UI → Task 6. Warning badges → Task 7. The
  spec's `GET /api/validate-output-dir`-vs-`browse-dir` split wasn't fully spelled out in
  the design doc (which sketched validation as folded into `browse-dir` or a separate
  endpoint, leaving it open) — this plan resolves that ambiguity explicitly: `browse-dir`
  lists an *existing* directory's children (for navigation), `validate-output-dir` checks
  an arbitrary (possibly not-yet-existing) typed path (for the live text-field check and
  internally by `POST /api/books`). Both call the same `_check_writable`, preserving the
  spec's "single source of truth" requirement.
- **Placeholder scan:** no TBD/TODO markers; every step has complete, runnable code.
- **Type consistency:** `export_chapter_mp3`/`export_mp3` consistently return `Tuple[str,
  bool]` from Task 4 onward, and every caller (production in `pipeline.py`, tests in
  `test_postprocess_audio.py`) unpacks accordingly. `used_fallback` is `bool` everywhere
  it crosses a layer boundary (dataclass field, SQL `INTEGER` via explicit `int()`/`bool()`
  conversion at the `db.py` boundary, JSON `bool` in API responses) — no layer leaks a raw
  `0`/`1` integer where a `bool` is expected.

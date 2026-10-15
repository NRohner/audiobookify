# Tauri Desktop Phase 1 (Rust DB) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Rust `db` module that opens the existing `audiobookify.db` for reading and writing, runs the same schema and ALTER-TABLE migrations as `src/audiobookify/db.py` (and running them twice changes nothing), and offers the same operations, test-first, ported from `tests/test_db.py`.

**Architecture:** `desktop/` becomes a Cargo workspace. The existing Tauri package stays the root package, unmoved, and a new library crate `desktop/core` (`audiobookify-core`) is added as a member. Phases 2 and 3 (text pipeline, jobs) put their modules in the same crate, and the app crate depends on it once commands move to Rust (phase 4). The `db` module wraps one `rusqlite::Connection` and copies the Python SQL statements one for one. The schema text is copied verbatim into `schema.sql`. The app crate does not change in this phase, and nothing at runtime uses the Rust DB yet. Python keeps owning the live database until phase 4.

**Tech Stack:** Rust stable (cargo 1.98), `rusqlite` 0.40 with `bundled` SQLite, `thiserror` 2, `serde_json` 1, and `tempfile` 3 (dev only).

**Spec:** `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md`, phase 1.

**Branch:** `feat/tauri-phase1-db`. Its base depends on PR NRohner/audiobookify#22 (phase 0, still open on 2026-10-14). If #22 has merged, branch off `main`. Otherwise branch off `feat/tauri-desktop` and retarget the PR to `main` after #22 merges.

## Global Constraints

- macOS on Apple Silicon only.
- The schema in `desktop/core/src/db/schema.sql` must be byte-identical to the `SCHEMA` string in `src/audiobookify/db.py`. The Python app keeps reading and writing the same file during phases 1-3, so the schema itself must not change.
- Map rows by column **name**, never by index. Real databases got columns through `ALTER TABLE ... ADD COLUMN`, so their column order differs from a fresh database.
- Error messages are copied word for word from `db.py`. Python `KeyError` becomes `DbError::NotFound`, and `ValueError` becomes `DbError::Invalid`.
- Nothing in `desktop/src/` changes. Only `desktop/Cargo.toml` gains a `[workspace]` section.
- TDD: each task writes its tests first and watches them fail before writing the implementation.
- Run every cargo command from `desktop/`.

## Decisions

| Fork | Choice | Why |
|---|---|---|
| Crate layout | Root package `desktop/` plus member `desktop/core`, no move | A root-package workspace is standard Cargo. `tauri.conf.json`, `build.rs`, the icons and the Makefile targets keep working untouched, and `desktop/target` stays the shared target dir. |
| Migration atomicity | Schema + migrations in one `BEGIN IMMEDIATE` transaction | Python and Rust can open the file at the same moment. Without the write lock, both see a column missing and the second `ALTER` fails with "duplicate column". |
| Voice reference-audio migration | Ported. `Database::open(path, voices_dir)` takes the voices dir explicitly | `db.py` runs it on every open, and its 4 tests are part of the 64. Python uses a module global (`VOICES_DIR`); an explicit parameter suits the app data dir that phase 4 introduces. |
| "Already migrated" check | `Path::starts_with` (compares whole path components) | Python's string-prefix check would treat `voices2/...` as inside `voices`. |
| Warn-once dedupe (`_WARNED_VOICE_MIGRATION_IDS`) | Dropped | Python opens a new `Database` per API request, so it needed the dedupe. The Rust app will hold one connection. |
| `Book.chapters` field | Dropped | `db.py` never fills it. |

---

## File structure

| File | Responsibility |
|---|---|
| `desktop/Cargo.toml` | Gains `[workspace] members = ["core"]`. |
| `desktop/core/Cargo.toml` | Crate manifest. |
| `desktop/core/src/lib.rs` | Module list. |
| `desktop/core/src/models.rs` | Row structs, the status/format enums (with SQLite text conversion), and input structs. |
| `desktop/core/src/db/mod.rs` | `Database`, `open`, `DbError`, query helpers, books and chapters, chapter breaks. |
| `desktop/core/src/db/schema.sql` | Verbatim copy of `db.py`'s `SCHEMA`. |
| `desktop/core/src/db/voices.rs` | Voice profile methods. |
| `desktop/core/src/db/lexicon.rs` | Lexicon methods. |
| `desktop/core/src/db/jobs.rs` | Jobs, text clips, worker state. |
| `desktop/core/src/db/migrations.rs` | Column migrations and the voice reference-audio migration. |
| `desktop/core/src/voice_storage.rs` | `sanitize_filename`, `permanent_voice_dir`, `store_reference_audio` (phase 4 voice import reuses them). |
| `desktop/core/tests/common/mod.rs` | Shared test helpers. |
| `desktop/core/tests/{books,voices,lexicon,jobs,migrations}.rs` | The ported tests, grouped by table. |
| `desktop/core/examples/check_db.rs` | Exit check: opens a real database twice, decodes every row, writes, and checks that the second open is a no-op. |

### Test map (`tests/test_db.py` → Rust)

All 64 Python tests are ported. Rust test names are the Python names without the `test_` prefix, sometimes shortened.
- `books.rs` (9 + 5 from `test_chapter_breaks.py` + 1 new): create/add, persist across reopen, find by source, get/update chapter missing, list books, get book missing, used_fallback set/default, book_paragraphs, replace breaks (keep, merge, invalid, no words), and new: `set_chapter_audio_path_and_duration`.
- `voices.rs` (14): create/find, list order, duplicate name, book voice profile, get by id, delete, delete missing, update reference path, update path missing, transcription queue, whisper transcript, edit keeps timings, failed transcription and retry, tempo.
- `lexicon.rs` (13): every lexicon test except the M2 migration.
- `jobs.rs` (20 + 3 new): job lifecycle, paused flag, cancel flag, requeue, text clip fallback/engine/tempo, and new: text clip queue lifecycle, list clips newest first, delete clip.
- `migrations.rs` (8 + 2 new): the four column-migration tests, the four voice reference-audio tests, and new: `reopening_a_fresh_db_changes_nothing`, `reopening_a_migrated_old_db_changes_nothing`.

### Execution order

**1, 5, 2, 3, 4, 6, 7, 8.** Task 2's tests use `create_job` from Task 5. The numbering is unchanged so that cross-references stay valid.

---

### Task 1: Workspace, core crate, models, open, books and chapters

**Files:**
- Modify: `desktop/Cargo.toml`
- Create: `desktop/core/Cargo.toml`, `desktop/core/src/lib.rs`, `desktop/core/src/models.rs`, `desktop/core/src/db/mod.rs`, `desktop/core/src/db/schema.sql`
- Test: `desktop/core/tests/common/mod.rs`, `desktop/core/tests/books.rs`

**Interfaces:**
- Produces:
  - `audiobookify_core::db::{Database, DbError, Result}`
  - `Database::open(path: impl AsRef<Path>) -> Result<Database>` (Task 7 adds a `voices_dir` parameter)
  - `create_book(&self, title: &str, author: Option<&str>, source_path: &str, source_format: SourceFormat, output_dir: &str) -> Result<Book>`
  - `get_book(&self, id: i64) -> Result<Book>`, `list_books(&self) -> Result<Vec<Book>>`, `find_book_by_source(&self, source_path: &str) -> Result<Option<Book>>`
  - `update_book_engine(&self, book_id: i64, engine: &str) -> Result<()>`, `update_book_voice_profile(&self, book_id: i64, voice_profile_id: Option<i64>) -> Result<()>`
  - `add_chapters(&self, book_id: i64, raw: &[RawChapter]) -> Result<Vec<Chapter>>`, `list_chapters(&self, book_id: i64) -> Result<Vec<Chapter>>`, `get_chapter(&self, id: i64) -> Result<Chapter>`
  - `update_chapter_status(&self, id: i64, status: ChapterStatus, outcome: ChapterOutcome) -> Result<()>`
  - `set_chapter_audio_path(&self, id: i64, path: &str) -> Result<()>`, `set_chapter_duration(&self, id: i64, duration: f64) -> Result<()>`
  - crate-internal helpers: `now() -> f64`, `is_constraint(&rusqlite::Error) -> bool`, `Database::query_all`, `Database::query_opt`
  - `audiobookify_core::models::*`: all structs and enums below

- [ ] **Step 1: Make `desktop/` a workspace**

Append to `desktop/Cargo.toml`:
```toml

[workspace]
members = ["core"]
```

- [ ] **Step 2: Write `desktop/core/Cargo.toml`**

```toml
[package]
name = "audiobookify-core"
version = "0.1.0"
edition = "2021"

[dependencies]
rusqlite = { version = "0.40", features = ["bundled"] }
serde_json = "1"
thiserror = "2"

[dev-dependencies]
tempfile = "3"
```

- [ ] **Step 3: Write `desktop/core/src/lib.rs`**

```rust
pub mod db;
pub mod models;
```

- [ ] **Step 4: Write `desktop/core/src/models.rs`**

```rust
//! Rows of the audiobookify database, mirroring `src/audiobookify/models.py`.

use rusqlite::types::{FromSql, FromSqlError, FromSqlResult, ToSql, ToSqlOutput, ValueRef};

/// The engine a new book or clip gets, matching the column defaults in schema.sql.
pub const DEFAULT_ENGINE: &str = "chatterbox-turbo";

/// A status/format enum that is stored as its lowercase text.
macro_rules! text_enum {
    ($name:ident { $($variant:ident => $text:literal),+ $(,)? }) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum $name { $($variant),+ }

        impl $name {
            pub fn as_str(self) -> &'static str {
                match self { $(Self::$variant => $text),+ }
            }
        }

        impl ToSql for $name {
            fn to_sql(&self) -> rusqlite::Result<ToSqlOutput<'_>> {
                Ok(self.as_str().into())
            }
        }

        impl FromSql for $name {
            fn column_result(value: ValueRef<'_>) -> FromSqlResult<Self> {
                match value.as_str()? {
                    $($text => Ok(Self::$variant),)+
                    other => Err(FromSqlError::Other(
                        format!("unknown {} {other:?}", stringify!($name)).into(),
                    )),
                }
            }
        }
    };
}

text_enum!(SourceFormat { Epub => "epub", Markdown => "md" });
text_enum!(ChapterStatus { Pending => "pending", Generating => "generating", Done => "done", Failed => "failed" });
text_enum!(JobStatus {
    Queued => "queued", Generating => "generating", Done => "done", Failed => "failed", Cancelled => "cancelled",
});

#[derive(Debug, Clone, PartialEq)]
pub struct RawChapter {
    pub title: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Book {
    pub id: i64,
    pub title: String,
    pub author: Option<String>,
    pub source_path: String,
    pub source_format: SourceFormat,
    pub output_dir: String,
    pub created_at: f64,
    pub voice_profile_id: Option<i64>,
    /// Used for every (re)generated chapter.
    pub engine: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Chapter {
    pub id: i64,
    pub book_id: i64,
    pub order_index: i64,
    pub title: String,
    pub text: String,
    pub status: ChapterStatus,
    pub output_mp3_path: Option<String>,
    pub duration: Option<f64>,
    pub error: Option<String>,
    pub used_fallback: bool,
    /// What produced the audio; None until generated.
    pub engine: Option<String>,
}

/// The optional fields of `Database::update_chapter_status`.
#[derive(Debug, Clone, Copy, Default)]
pub struct ChapterOutcome<'a> {
    pub output_mp3_path: Option<&'a str>,
    pub duration: Option<f64>,
    pub error: Option<&'a str>,
    pub used_fallback: bool,
    pub engine: Option<&'a str>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct VoiceProfile {
    pub id: i64,
    pub name: String,
    /// Legacy: voices work with any engine; the book or clip picks one.
    pub engine: String,
    pub reference_audio_path: String,
    pub reference_transcript: Option<String>,
    pub exaggeration: f64,
    pub cfg_weight: f64,
    pub temperature: f64,
    pub created_at: f64,
    /// None (never asked), "pending", "done", "failed".
    pub transcript_status: Option<String>,
    pub transcript_error: Option<String>,
    /// Whisper word timings of the whole clip.
    pub reference_words: Option<serde_json::Value>,
    /// Engines clone from the first N seconds; None = whole clip.
    pub reference_trim_end: Option<f64>,
    /// The reference is played at this speed (atempo) before cloning.
    pub tempo: f64,
}

#[derive(Debug, Clone, Copy)]
pub struct NewVoiceProfile<'a> {
    pub name: &'a str,
    pub engine: &'a str,
    pub reference_audio_path: &'a str,
    pub reference_transcript: Option<&'a str>,
    pub exaggeration: f64,
    pub cfg_weight: f64,
    pub temperature: f64,
    pub transcript_status: Option<&'a str>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LexiconEntry {
    pub id: i64,
    pub term: String,
    pub override_value: String,
    pub source: String,
    pub scope: String,
    /// 0 = global.
    pub book_id: i64,
    pub notes: Option<String>,
    pub created_at: f64,
    pub phonemes: Option<String>,
    pub sample_audio_path: Option<String>,
}

#[derive(Debug, Clone, Copy)]
pub struct NewLexiconEntry<'a> {
    pub term: &'a str,
    pub override_value: &'a str,
    pub scope: &'a str,
    pub book_id: i64,
    pub notes: Option<&'a str>,
    pub source: &'a str,
    pub phonemes: Option<&'a str>,
    pub sample_audio_path: Option<&'a str>,
}

impl<'a> NewLexiconEntry<'a> {
    /// A global, manual entry; set the other fields directly.
    pub fn new(term: &'a str, override_value: &'a str, scope: &'a str) -> Self {
        Self {
            term, override_value, scope, book_id: 0, notes: None, source: "manual",
            phonemes: None, sample_audio_path: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Job {
    pub id: i64,
    pub chapter_id: i64,
    pub status: JobStatus,
    pub progress: Option<f64>,
    pub created_at: f64,
    pub started_at: Option<f64>,
    pub finished_at: Option<f64>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TextClip {
    pub id: i64,
    pub text: String,
    pub voice_profile_id: Option<i64>,
    pub status: JobStatus,
    pub progress: Option<f64>,
    pub output_path: Option<String>,
    pub created_at: f64,
    pub started_at: Option<f64>,
    pub finished_at: Option<f64>,
    pub error: Option<String>,
    pub used_fallback: bool,
    pub engine: String,
    /// A sample at an unsaved tempo; None = the voice's tempo.
    pub tempo: Option<f64>,
}
```

- [ ] **Step 5: Copy the schema**

Create `desktop/core/src/db/schema.sql` with the exact text between the triple quotes of `SCHEMA` in `src/audiobookify/db.py` (lines 15-104). Run this to extract it and check that it is identical:
```bash
mkdir -p core/src/db && ../.venv/bin/python -c "import sys; sys.path.insert(0, '../src'); from audiobookify.db import SCHEMA; open('core/src/db/schema.sql', 'w').write(SCHEMA)" && head -3 core/src/db/schema.sql
```
Expected: an empty first line, then `CREATE TABLE IF NOT EXISTS books (` and `    id INTEGER PRIMARY KEY AUTOINCREMENT,`.

- [ ] **Step 6: Write the test helpers `desktop/core/tests/common/mod.rs`**

```rust
#![allow(dead_code)] // each test binary uses a different subset

use std::fmt::Debug;
use std::path::Path;

use audiobookify_core::db::{Database, DbError};
use audiobookify_core::models::*;

pub fn open(dir: &Path) -> Database {
    Database::open(dir.join("test.db")).unwrap()
}

pub fn path(dir: &Path, name: &str) -> String {
    dir.join(name).to_string_lossy().into_owned()
}

pub fn raw(title: &str, text: &str) -> RawChapter {
    RawChapter { title: title.into(), text: text.into() }
}

pub fn md_book(db: &Database, dir: &Path, source_path: &str) -> Book {
    db.create_book("Test Book", None, source_path, SourceFormat::Markdown, &path(dir, "out")).unwrap()
}

pub fn book_with_chapter(db: &Database, dir: &Path) -> (Book, Chapter) {
    let book = md_book(db, dir, &path(dir, "b.md"));
    let mut chapters = db.add_chapters(book.id, &[raw("Ch 1", "Body.")]).unwrap();
    (book, chapters.remove(0))
}

/// A "chatterbox" voice with default params and no transcript, like most test_db.py voices.
pub fn voice_at(db: &Database, name: &str, reference_audio_path: &str) -> VoiceProfile {
    db.create_voice_profile(&NewVoiceProfile {
        name, engine: "chatterbox", reference_audio_path, reference_transcript: None,
        exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8, transcript_status: None,
    })
    .unwrap()
}

/// test_db.py's `_voice` helper.
pub fn voice(db: &Database, dir: &Path, name: &str, transcript: Option<&str>,
             transcript_status: Option<&str>) -> VoiceProfile {
    db.create_voice_profile(&NewVoiceProfile {
        name, engine: "chatterbox-turbo", reference_audio_path: &path(dir, &format!("{name}.wav")),
        reference_transcript: transcript, exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8,
        transcript_status,
    })
    .unwrap()
}

pub fn not_found<T: Debug>(result: Result<T, DbError>) -> String {
    match result {
        Err(DbError::NotFound(message)) => message,
        other => panic!("expected NotFound, got {other:?}"),
    }
}

pub fn invalid<T: Debug>(result: Result<T, DbError>) -> String {
    match result {
        Err(DbError::Invalid(message)) => message,
        other => panic!("expected Invalid, got {other:?}"),
    }
}
```

- [ ] **Step 7: Write the failing tests `desktop/core/tests/books.rs`**

```rust
mod common;

use audiobookify_core::models::{ChapterOutcome, ChapterStatus, SourceFormat};
use common::*;
use tempfile::TempDir;

#[test]
fn create_book_and_add_chapters() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = db
        .create_book("Test Book", Some("Jane Author"), "/books/test.epub", SourceFormat::Epub,
                     &path(dir.path(), "out"))
        .unwrap();
    db.add_chapters(book.id, &[raw("Chapter One", "Once upon a time.")]).unwrap();

    let chapters = db.list_chapters(book.id).unwrap();
    assert_eq!(chapters.len(), 1);
    assert_eq!(chapters[0].order_index, 0);
    assert_eq!(chapters[0].title, "Chapter One");
    assert_eq!(chapters[0].status, ChapterStatus::Pending);
    assert_eq!(db.get_book(book.id).unwrap().author.as_deref(), Some("Jane Author"));
}

#[test]
fn update_chapter_status_persists_across_reopen() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    let chapter = db.add_chapters(book.id, &[raw("Ch 1", "Body.")]).unwrap().remove(0);
    db.update_chapter_status(chapter.id, ChapterStatus::Done,
        ChapterOutcome { output_mp3_path: Some("/out/01 - Ch 1.mp3"), ..Default::default() }).unwrap();
    drop(db);

    let reopened = open(dir.path());
    let reloaded = reopened.list_chapters(book.id).unwrap().remove(0);
    assert_eq!(reloaded.status, ChapterStatus::Done);
    assert_eq!(reloaded.output_mp3_path.as_deref(), Some("/out/01 - Ch 1.mp3"));
}

#[test]
fn find_book_by_source() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    db.create_book("Test Book", None, "/books/test.epub", SourceFormat::Epub, &path(dir.path(), "out"))
        .unwrap();
    let found = db.find_book_by_source("/books/test.epub").unwrap().unwrap();
    assert_eq!(found.title, "Test Book");
    assert_eq!(found.source_format, SourceFormat::Epub);
    assert!(db.find_book_by_source("/nope.epub").unwrap().is_none());
}

#[test]
fn get_chapter_raises_not_found_for_missing_id() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert_eq!(not_found(db.get_chapter(999)), "no chapter with id 999");
}

#[test]
fn update_chapter_status_raises_not_found_for_missing_id() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let result = db.update_chapter_status(999, ChapterStatus::Done, ChapterOutcome::default());
    assert_eq!(not_found(result), "no chapter with id 999");
}

#[test]
fn list_books_returns_all_books_ordered_by_id() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let first = md_book(&db, dir.path(), &path(dir.path(), "a.md"));
    let second = md_book(&db, dir.path(), &path(dir.path(), "b.md"));
    let ids: Vec<i64> = db.list_books().unwrap().iter().map(|b| b.id).collect();
    assert_eq!(ids, vec![first.id, second.id]);
}

#[test]
fn get_book_raises_not_found_for_missing_id() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert_eq!(not_found(db.get_book(999)), "no book with id 999");
}

#[test]
fn update_chapter_status_sets_used_fallback() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (book, chapter) = book_with_chapter(&db, dir.path());
    db.update_chapter_status(chapter.id, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some("/out/01 - Ch 1.mp3"), used_fallback: true, ..Default::default()
    }).unwrap();
    assert!(db.list_chapters(book.id).unwrap()[0].used_fallback);
}

#[test]
fn update_chapter_status_defaults_used_fallback_to_false() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (book, chapter) = book_with_chapter(&db, dir.path());
    db.update_chapter_status(chapter.id, ChapterStatus::Done,
        ChapterOutcome { output_mp3_path: Some("/out/01 - Ch 1.mp3"), ..Default::default() }).unwrap();
    assert!(!db.list_chapters(book.id).unwrap()[0].used_fallback);
}

#[test]
fn set_chapter_audio_path_and_duration() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    db.set_chapter_audio_path(chapter.id, "/out/02 - Moved.mp3").unwrap();
    db.set_chapter_duration(chapter.id, 12.5).unwrap();
    let reloaded = db.get_chapter(chapter.id).unwrap();
    assert_eq!(reloaded.output_mp3_path.as_deref(), Some("/out/02 - Moved.mp3"));
    assert_eq!(reloaded.duration, Some(12.5));
}
```

- [ ] **Step 8: Run the tests to check that they fail**

Run: `cargo test -p audiobookify-core --test books`
Expected: compile error, `file not found for module 'db'`.

- [ ] **Step 9: Write `desktop/core/src/db/mod.rs`**

```rust
//! The audiobookify SQLite database, ported from `src/audiobookify/db.py`.

use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension, Params, Row, TransactionBehavior};

use crate::models::{Book, Chapter, ChapterOutcome, ChapterStatus, RawChapter, SourceFormat};

const SCHEMA: &str = include_str!("schema.sql");

#[derive(Debug, thiserror::Error)]
pub enum DbError {
    /// A row that should exist doesn't (Python: KeyError).
    #[error("{0}")]
    NotFound(String),
    /// The caller asked for something the data rules forbid (Python: ValueError).
    #[error("{0}")]
    Invalid(String),
    #[error(transparent)]
    Sqlite(#[from] rusqlite::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

pub type Result<T> = std::result::Result<T, DbError>;

/// Seconds since the epoch, like Python's `time.time()`.
pub(crate) fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).expect("clock before 1970").as_secs_f64()
}

/// A UNIQUE / NOT NULL violation (Python: sqlite3.IntegrityError).
pub(crate) fn is_constraint(err: &rusqlite::Error) -> bool {
    matches!(err, rusqlite::Error::SqliteFailure(e, _) if e.code == rusqlite::ErrorCode::ConstraintViolation)
}

pub struct Database {
    conn: Connection,
}

impl Database {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let mut conn = Connection::open(path)?;
        // The API and the worker write this file from two processes at once.
        // WAL + a busy timeout turn lock contention into a brief wait instead of
        // "database is locked".
        conn.pragma_update_and_check(None, "journal_mode", "WAL", |row| row.get::<_, String>(0))?;
        conn.busy_timeout(Duration::from_secs(5))?;
        // IMMEDIATE takes the write lock up front, so two processes opening at once
        // can't both decide a column is missing and both try to add it.
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        tx.execute_batch(SCHEMA)?;
        tx.commit()?;
        Ok(Self { conn })
    }

    pub(crate) fn query_all<T>(&self, sql: &str, params: impl Params,
                               map: fn(&Row<'_>) -> rusqlite::Result<T>) -> Result<Vec<T>> {
        let mut stmt = self.conn.prepare(sql)?;
        let rows = stmt.query_map(params, map)?.collect::<rusqlite::Result<Vec<T>>>()?;
        Ok(rows)
    }

    pub(crate) fn query_opt<T>(&self, sql: &str, params: impl Params,
                               map: fn(&Row<'_>) -> rusqlite::Result<T>) -> Result<Option<T>> {
        Ok(self.conn.query_row(sql, params, map).optional()?)
    }

    pub fn create_book(&self, title: &str, author: Option<&str>, source_path: &str,
                       source_format: SourceFormat, output_dir: &str) -> Result<Book> {
        self.conn.execute(
            "INSERT INTO books (title, author, source_path, source_format, output_dir, created_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![title, author, source_path, source_format, output_dir, now()],
        )?;
        self.get_book(self.conn.last_insert_rowid())
    }

    pub fn get_book(&self, book_id: i64) -> Result<Book> {
        self.query_opt("SELECT * FROM books WHERE id = ?1", [book_id], book_from_row)?
            .ok_or_else(|| DbError::NotFound(format!("no book with id {book_id}")))
    }

    pub fn list_books(&self) -> Result<Vec<Book>> {
        self.query_all("SELECT * FROM books ORDER BY id", [], book_from_row)
    }

    pub fn find_book_by_source(&self, source_path: &str) -> Result<Option<Book>> {
        self.query_opt("SELECT * FROM books WHERE source_path = ?1", [source_path], book_from_row)
    }

    pub fn update_book_engine(&self, book_id: i64, engine: &str) -> Result<()> {
        self.conn.execute("UPDATE books SET engine = ?1 WHERE id = ?2", params![engine, book_id])?;
        Ok(())
    }

    pub fn update_book_voice_profile(&self, book_id: i64, voice_profile_id: Option<i64>) -> Result<()> {
        self.conn.execute("UPDATE books SET voice_profile_id = ?1 WHERE id = ?2",
                          params![voice_profile_id, book_id])?;
        Ok(())
    }

    pub fn add_chapters(&self, book_id: i64, raw_chapters: &[RawChapter]) -> Result<Vec<Chapter>> {
        // Find the next available order_index for this book
        let max_index: Option<i64> = self.conn.query_row(
            "SELECT MAX(order_index) FROM chapters WHERE book_id = ?1", [book_id], |row| row.get(0),
        )?;
        let start_index = max_index.map_or(0, |max| max + 1);
        for (offset, raw) in raw_chapters.iter().enumerate() {
            self.conn.execute(
                "INSERT INTO chapters (book_id, order_index, title, text) VALUES (?1, ?2, ?3, ?4)",
                params![book_id, start_index + offset as i64, raw.title, raw.text],
            )?;
        }
        self.list_chapters(book_id)
    }

    pub fn list_chapters(&self, book_id: i64) -> Result<Vec<Chapter>> {
        self.query_all("SELECT * FROM chapters WHERE book_id = ?1 ORDER BY order_index", [book_id],
                       chapter_from_row)
    }

    pub fn get_chapter(&self, chapter_id: i64) -> Result<Chapter> {
        self.query_opt("SELECT * FROM chapters WHERE id = ?1", [chapter_id], chapter_from_row)?
            .ok_or_else(|| DbError::NotFound(format!("no chapter with id {chapter_id}")))
    }

    pub fn update_chapter_status(&self, chapter_id: i64, status: ChapterStatus,
                                 outcome: ChapterOutcome<'_>) -> Result<()> {
        let changed = self.conn.execute(
            "UPDATE chapters SET status = ?1, output_mp3_path = COALESCE(?2, output_mp3_path), \
             duration = COALESCE(?3, duration), error = ?4, used_fallback = ?5, \
             engine = COALESCE(?6, engine) WHERE id = ?7",
            params![status, outcome.output_mp3_path, outcome.duration, outcome.error,
                    outcome.used_fallback, outcome.engine, chapter_id],
        )?;
        if changed == 0 {
            return Err(DbError::NotFound(format!("no chapter with id {chapter_id}")));
        }
        Ok(())
    }

    pub fn set_chapter_audio_path(&self, chapter_id: i64, path: &str) -> Result<()> {
        self.conn.execute("UPDATE chapters SET output_mp3_path = ?1 WHERE id = ?2", params![path, chapter_id])?;
        Ok(())
    }

    pub fn set_chapter_duration(&self, chapter_id: i64, duration: f64) -> Result<()> {
        self.conn.execute("UPDATE chapters SET duration = ?1 WHERE id = ?2", params![duration, chapter_id])?;
        Ok(())
    }
}

fn book_from_row(r: &Row<'_>) -> rusqlite::Result<Book> {
    Ok(Book {
        id: r.get("id")?, title: r.get("title")?, author: r.get("author")?,
        source_path: r.get("source_path")?, source_format: r.get("source_format")?,
        output_dir: r.get("output_dir")?, created_at: r.get("created_at")?,
        voice_profile_id: r.get("voice_profile_id")?, engine: r.get("engine")?,
    })
}

fn chapter_from_row(r: &Row<'_>) -> rusqlite::Result<Chapter> {
    Ok(Chapter {
        id: r.get("id")?, book_id: r.get("book_id")?, order_index: r.get("order_index")?,
        title: r.get("title")?, text: r.get("text")?, status: r.get("status")?,
        output_mp3_path: r.get("output_mp3_path")?, duration: r.get("duration")?, error: r.get("error")?,
        used_fallback: r.get("used_fallback")?, engine: r.get("engine")?,
    })
}
```

- [ ] **Step 10: Run the tests to check that they pass**

Run: `cargo test -p audiobookify-core --test books`
Expected: `test result: ok. 10 passed`. The first build compiles bundled SQLite and takes about a minute.

- [ ] **Step 11: Check that the app crate still builds**

Run: `cargo build -p audiobookify-desktop`
Expected: `Finished` with no errors. The workspace root is still `desktop/`, so `desktop/target` stays where it was.

- [ ] **Step 12: Commit**

```bash
git add desktop/Cargo.toml desktop/Cargo.lock desktop/core
git commit -m "Add audiobookify-core crate with the books and chapters DB port"
```

---

### Task 2: Chapter breaks

**Files:**
- Modify: `desktop/core/src/db/mod.rs`
- Test: `desktop/core/tests/books.rs`

**Interfaces:**
- Consumes: `Database::list_chapters`, `DbError::Invalid`
- Produces:
  - `pub fn book_paragraphs(chapters: &[Chapter]) -> (Vec<&str>, Vec<usize>)` in `audiobookify_core::db`
  - `Database::replace_chapter_breaks(&mut self, book_id: i64, breaks: &[(usize, &str)]) -> Result<Vec<Chapter>>`, which returns the removed chapters

- [ ] **Step 1: Append the failing tests to `desktop/core/tests/books.rs`**

These are the DB-level tests from `tests/test_chapter_breaks.py`.
```rust
use audiobookify_core::db::{book_paragraphs, Database};

const THREE: [(&str, &str); 3] = [("One", "a\n\nb"), ("Two", "c\n\nd\n\ne"), ("Three", "f")];

fn three_chapter_book(dir: &std::path::Path, chapters: &[(&str, &str)]) -> (Database, i64) {
    let db = open(dir);
    let book = md_book(&db, dir, &path(dir, "b.md"));
    let raws: Vec<_> = chapters.iter().map(|(t, x)| raw(t, x)).collect();
    db.add_chapters(book.id, &raws).unwrap();
    (db, book.id)
}

#[test]
fn book_paragraphs_flattens_chapters_and_reports_starts() {
    let dir = TempDir::new().unwrap();
    let (db, book_id) = three_chapter_book(dir.path(), &THREE);
    let chapters = db.list_chapters(book_id).unwrap();
    let (paragraphs, starts) = book_paragraphs(&chapters);
    assert_eq!(paragraphs, vec!["a", "b", "c", "d", "e", "f"]);
    assert_eq!(starts, vec![0, 2, 5]);
}

#[test]
fn replace_breaks_keeps_untouched_chapters_and_rebuilds_changed_ones() {
    let dir = TempDir::new().unwrap();
    let (mut db, book_id) = three_chapter_book(dir.path(), &THREE);
    let old = db.list_chapters(book_id).unwrap();
    let (one, two, three) = (&old[0], &old[1], &old[2]);
    db.update_chapter_status(one.id, ChapterStatus::Done,
        ChapterOutcome { output_mp3_path: Some("/x/1.mp3"), ..Default::default() }).unwrap();
    db.update_chapter_status(two.id, ChapterStatus::Done,
        ChapterOutcome { output_mp3_path: Some("/x/2.mp3"), ..Default::default() }).unwrap();
    db.create_job(two.id).unwrap();

    // Split "Two" after "c"; rename "One" (same range, so its audio survives).
    let removed = db
        .replace_chapter_breaks(book_id, &[(0, "Prologue"), (2, "Two"), (3, "Two b"), (5, "Three")])
        .unwrap();

    let chapters = db.list_chapters(book_id).unwrap();
    let summary: Vec<(i64, &str, &str)> =
        chapters.iter().map(|c| (c.order_index, c.title.as_str(), c.text.as_str())).collect();
    assert_eq!(summary, vec![(0, "Prologue", "a\n\nb"), (1, "Two", "c"), (2, "Two b", "d\n\ne"), (3, "Three", "f")]);
    assert_eq!(chapters[0].id, one.id);
    assert_eq!(chapters[0].status, ChapterStatus::Done);
    assert_eq!(chapters[3].id, three.id);
    assert_ne!(chapters[1].id, two.id);
    assert_eq!(chapters[1].status, ChapterStatus::Pending);
    assert_eq!(chapters[1].output_mp3_path, None);
    assert_eq!(removed.iter().map(|c| c.id).collect::<Vec<_>>(), vec![two.id]);
    assert!(db.list_jobs_for_chapter(two.id).unwrap().is_empty());
}

#[test]
fn replace_breaks_can_merge_chapters() {
    let dir = TempDir::new().unwrap();
    let (mut db, book_id) = three_chapter_book(dir.path(), &THREE);
    db.replace_chapter_breaks(book_id, &[(0, "All of it")]).unwrap();
    let chapters = db.list_chapters(book_id).unwrap();
    assert_eq!(chapters.len(), 1);
    assert_eq!(chapters[0].text, "a\n\nb\n\nc\n\nd\n\ne\n\nf");
}

#[test]
fn replace_breaks_rejects_invalid_breaks_without_changing_anything() {
    let cases: [&[(usize, &str)]; 5] = [
        &[],                              // no chapters
        &[(1, "X")],                      // must start at the first paragraph
        &[(0, "X"), (3, "Y"), (3, "Z")],  // not strictly increasing
        &[(0, "X"), (6, "Y")],            // past the end
        &[(0, "X"), (2, "  ")],           // blank title
    ];
    for breaks in cases {
        let dir = TempDir::new().unwrap();
        let (mut db, book_id) = three_chapter_book(dir.path(), &THREE);
        let before = db.list_chapters(book_id).unwrap();
        invalid(db.replace_chapter_breaks(book_id, breaks));
        assert_eq!(db.list_chapters(book_id).unwrap(), before, "breaks {breaks:?}");
    }
}

#[test]
fn replace_breaks_rejects_a_chapter_with_no_words() {
    let dir = TempDir::new().unwrap();
    let (mut db, book_id) = three_chapter_book(dir.path(), &[("One", "a\n\n\n\nb")]); // paragraphs: a, "", b
    invalid(db.replace_chapter_breaks(book_id, &[(0, "X"), (1, "Empty"), (2, "B")]));
}
```

- [ ] **Step 2: Run the tests to check that they fail**

Run: `cargo test -p audiobookify-core --test books`
Expected: compile errors: `unresolved import audiobookify_core::db::book_paragraphs` and `no method named replace_chapter_breaks`. `create_job` already exists because Task 5 runs first (see Execution order).

- [ ] **Step 3: Implement in `desktop/core/src/db/mod.rs`**

Add `use std::collections::HashMap;` to the imports and this free function below the `Database` impl:
```rust
/// The book as one paragraph list, plus the index each chapter starts at.
///
/// Chapter text is paragraphs joined by a blank line, and split/join on it are exact
/// inverses, so chapter breaks can move between paragraphs without losing a character.
pub fn book_paragraphs(chapters: &[Chapter]) -> (Vec<&str>, Vec<usize>) {
    let mut paragraphs = Vec::new();
    let mut starts = Vec::new();
    for chapter in chapters {
        starts.push(paragraphs.len());
        paragraphs.extend(chapter.text.split("\n\n"));
    }
    (paragraphs, starts)
}
```
Add this method inside `impl Database`:
```rust
    /// Re-split the book's paragraphs at `breaks` [(start paragraph, title), ...].
    ///
    /// A chapter covering exactly the same paragraphs as before keeps its row (and so its
    /// audio and status); any other range becomes a new pending chapter. Returns the old
    /// chapters that no longer exist, so callers can clean up their files.
    pub fn replace_chapter_breaks(&mut self, book_id: i64, breaks: &[(usize, &str)]) -> Result<Vec<Chapter>> {
        let old = self.list_chapters(book_id)?;
        let (paragraphs, old_starts) = book_paragraphs(&old);
        let starts: Vec<usize> = breaks.iter().map(|&(start, _)| start).collect();
        if starts.first() != Some(&0)
            || starts.last().is_some_and(|&last| last >= paragraphs.len())
            || starts.windows(2).any(|pair| pair[0] >= pair[1])
        {
            return Err(DbError::Invalid("chapter breaks must start at the first paragraph and go forward".into()));
        }
        let ends: Vec<usize> = starts[1..].iter().copied().chain([paragraphs.len()]).collect();
        let texts: Vec<String> = starts.iter().zip(&ends).map(|(&s, &e)| paragraphs[s..e].join("\n\n")).collect();
        let titles: Vec<&str> = breaks.iter().map(|&(_, title)| title.trim()).collect();
        if titles.iter().any(|title| title.is_empty()) {
            return Err(DbError::Invalid("every chapter needs a title".into()));
        }
        if texts.iter().any(|text| text.trim().is_empty()) {
            return Err(DbError::Invalid("every chapter needs some words in it".into()));
        }

        let old_ends: Vec<usize> = old_starts[1..].iter().copied().chain([paragraphs.len()]).collect();
        let by_range: HashMap<(usize, usize), i64> =
            old_starts.iter().zip(&old_ends).zip(&old).map(|((&s, &e), c)| ((s, e), c.id)).collect();
        let kept: Vec<Option<i64>> =
            starts.iter().zip(&ends).map(|(&s, &e)| by_range.get(&(s, e)).copied()).collect();
        let removed: Vec<Chapter> = old.iter().filter(|c| !kept.contains(&Some(c.id))).cloned().collect();

        let tx = self.conn.transaction()?;
        // Park every index out of the way so the UNIQUE(book_id, order_index) holds mid-update.
        tx.execute("UPDATE chapters SET order_index = -1 - order_index WHERE book_id = ?1", [book_id])?;
        for chapter in &removed {
            tx.execute("DELETE FROM jobs WHERE chapter_id = ?1", [chapter.id])?;
            tx.execute("DELETE FROM chapters WHERE id = ?1", [chapter.id])?;
        }
        for (i, (title, text)) in titles.iter().zip(&texts).enumerate() {
            match kept[i] {
                Some(id) => tx.execute("UPDATE chapters SET order_index = ?1, title = ?2 WHERE id = ?3",
                                       params![i as i64, title, id])?,
                None => tx.execute(
                    "INSERT INTO chapters (book_id, order_index, title, text) VALUES (?1, ?2, ?3, ?4)",
                    params![book_id, i as i64, title, text],
                )?,
            };
        }
        tx.commit()?;
        Ok(removed)
    }
```

- [ ] **Step 4: Run the tests to check that they pass**

Run: `cargo test -p audiobookify-core --test books`
Expected: `test result: ok. 15 passed`.

- [ ] **Step 5: Commit**

```bash
git add desktop/core
git commit -m "Port chapter break replacement to Rust"
```

---

### Task 3: Voice profiles

**Files:**
- Create: `desktop/core/src/db/voices.rs`
- Modify: `desktop/core/src/db/mod.rs` (add `mod voices;`)
- Test: `desktop/core/tests/voices.rs`

**Interfaces:**
- Consumes: `now`, `is_constraint`, `query_opt`, `query_all`, `DbError`
- Produces (all `&self`, all `Result`):
  - `create_voice_profile(&NewVoiceProfile) -> VoiceProfile` (`Invalid("a voice profile named '{name}' already exists")`)
  - `find_voice_profile_by_name(&str) -> Option<VoiceProfile>`, `get_voice_profile_by_id(i64) -> Option<VoiceProfile>`, `list_voice_profiles() -> Vec<VoiceProfile>`
  - `update_voice_profile_reference_audio_path(i64, &str) -> ()`, `delete_voice_profile(i64) -> ()`
  - `next_pending_transcription() -> Option<VoiceProfile>`, `request_voice_transcription(i64)`, `fail_voice_transcription(i64, &str)`
  - `save_voice_transcript(id: i64, text: &str, words: Option<&serde_json::Value>, trim_end: Option<f64>)`
  - `set_voice_tempo(i64, f64)`
  - Each id-taking update returns `NotFound("no voice profile with id {id}")` when nothing matched.

- [ ] **Step 1: Write the failing tests `desktop/core/tests/voices.rs`**

```rust
mod common;

use audiobookify_core::models::NewVoiceProfile;
use common::*;
use serde_json::json;
use tempfile::TempDir;

#[test]
fn create_and_find_voice_profile_by_name() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let profile = db.create_voice_profile(&NewVoiceProfile {
        name: "narrator-1", engine: "chatterbox", reference_audio_path: "/refs/clip.wav",
        reference_transcript: Some("Hello there."), exaggeration: 0.6, cfg_weight: 0.4,
        temperature: 0.75, transcript_status: None,
    }).unwrap();
    assert_eq!(profile.name, "narrator-1");

    let found = db.find_voice_profile_by_name("narrator-1").unwrap().unwrap();
    assert_eq!(found.reference_audio_path, "/refs/clip.wav");
    assert_eq!(found.reference_transcript.as_deref(), Some("Hello there."));
    assert_eq!((found.exaggeration, found.cfg_weight, found.temperature), (0.6, 0.4, 0.75));
    assert!(db.find_voice_profile_by_name("nope").unwrap().is_none());
}

#[test]
fn list_voice_profiles_returns_all_in_creation_order() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    voice_at(&db, "a", "/a.wav");
    voice_at(&db, "b", "/b.wav");
    let names: Vec<String> = db.list_voice_profiles().unwrap().into_iter().map(|p| p.name).collect();
    assert_eq!(names, vec!["a", "b"]);
}

#[test]
fn create_voice_profile_rejects_duplicate_name() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    voice_at(&db, "dup", "/a.wav");
    let result = db.create_voice_profile(&NewVoiceProfile {
        name: "dup", engine: "chatterbox", reference_audio_path: "/b.wav", reference_transcript: None,
        exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8, transcript_status: None,
    });
    assert_eq!(invalid(result), "a voice profile named 'dup' already exists");
}

#[test]
fn update_book_voice_profile_persists() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    let profile = voice_at(&db, "narrator-1", "/refs/clip.wav");
    db.update_book_voice_profile(book.id, Some(profile.id)).unwrap();
    assert_eq!(db.get_book(book.id).unwrap().voice_profile_id, Some(profile.id));
}

#[test]
fn get_voice_profile_by_id() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let profile = voice_at(&db, "narrator-1", "/refs/clip.wav");
    assert_eq!(db.get_voice_profile_by_id(profile.id).unwrap().unwrap().name, "narrator-1");
    assert!(db.get_voice_profile_by_id(999999).unwrap().is_none());
}

#[test]
fn delete_voice_profile_removes_it() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let profile = voice_at(&db, "narrator-1", "/refs/clip.wav");
    db.delete_voice_profile(profile.id).unwrap();
    assert!(db.get_voice_profile_by_id(profile.id).unwrap().is_none());
}

#[test]
fn delete_voice_profile_raises_not_found_for_missing_id() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert_eq!(not_found(db.delete_voice_profile(999999)), "no voice profile with id 999999");
}

#[test]
fn update_voice_profile_reference_audio_path_updates_the_path() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let profile = voice_at(&db, "Nova", &path(dir.path(), "old.wav"));
    db.update_voice_profile_reference_audio_path(profile.id, &path(dir.path(), "new.wav")).unwrap();
    assert_eq!(db.get_voice_profile_by_id(profile.id).unwrap().unwrap().reference_audio_path,
               path(dir.path(), "new.wav"));
}

#[test]
fn update_voice_profile_reference_audio_path_raises_not_found_for_missing_id() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    not_found(db.update_voice_profile_reference_audio_path(999, "/some/path.wav"));
}

#[test]
fn voice_transcription_queue_hands_out_pending_voices_oldest_first() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let typed = voice(&db, dir.path(), "Typed", Some("I typed this."), Some("done"));
    let first = voice(&db, dir.path(), "First", None, Some("pending"));
    voice(&db, dir.path(), "Second", None, Some("pending"));
    assert_eq!(typed.transcript_status.as_deref(), Some("done"));
    assert_eq!(first.transcript_status.as_deref(), Some("pending"));
    assert_eq!(db.next_pending_transcription().unwrap().unwrap().name, "First");
}

#[test]
fn saving_a_whisper_transcript_stores_words_and_trim_and_finishes_the_voice() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let v = voice(&db, dir.path(), "Nova", None, Some("pending"));
    let words = json!([{"word": " Hi", "start": 0.0, "end": 0.4}]);

    db.save_voice_transcript(v.id, "Hi", Some(&words), Some(14.95)).unwrap();

    let saved = db.get_voice_profile_by_id(v.id).unwrap().unwrap();
    assert_eq!(saved.reference_transcript.as_deref(), Some("Hi"));
    assert_eq!(saved.transcript_status.as_deref(), Some("done"));
    assert_eq!(saved.reference_words, Some(words));
    assert_eq!(saved.reference_trim_end, Some(14.95));
    assert!(db.next_pending_transcription().unwrap().is_none());
}

#[test]
fn editing_a_transcript_keeps_the_word_timings_and_trim() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let v = voice(&db, dir.path(), "Nova", None, Some("pending"));
    let words = json!([{"word": " a", "start": 0, "end": 1}]);
    db.save_voice_transcript(v.id, "a small son cold", Some(&words), Some(12.0)).unwrap();

    db.save_voice_transcript(v.id, "a small son called", None, None).unwrap();

    let saved = db.get_voice_profile_by_id(v.id).unwrap().unwrap();
    assert_eq!(saved.reference_transcript.as_deref(), Some("a small son called"));
    assert_eq!(saved.reference_words, Some(words));
    assert_eq!(saved.reference_trim_end, Some(12.0));
}

#[test]
fn a_failed_transcription_is_recorded_and_can_be_retried() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let v = voice(&db, dir.path(), "Nova", None, Some("pending"));

    db.fail_voice_transcription(v.id, "unreadable audio").unwrap();
    let failed = db.get_voice_profile_by_id(v.id).unwrap().unwrap();
    assert_eq!(failed.transcript_status.as_deref(), Some("failed"));
    assert_eq!(failed.transcript_error.as_deref(), Some("unreadable audio"));
    assert!(db.next_pending_transcription().unwrap().is_none());

    db.request_voice_transcription(v.id).unwrap();
    let retry = db.get_voice_profile_by_id(v.id).unwrap().unwrap();
    assert_eq!(retry.transcript_status.as_deref(), Some("pending"));
    assert_eq!(retry.transcript_error, None);
    not_found(db.request_voice_transcription(999));
}

#[test]
fn voice_tempo_defaults_to_one_and_can_be_set() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let v = voice(&db, dir.path(), "Nova", None, None);
    assert_eq!(v.tempo, 1.0);
    db.set_voice_tempo(v.id, 0.88).unwrap();
    assert_eq!(db.get_voice_profile_by_id(v.id).unwrap().unwrap().tempo, 0.88);
    not_found(db.set_voice_tempo(999, 0.9));
}
```
Add `serde_json = "1"` to `[dev-dependencies]` in `desktop/core/Cargo.toml` (needed for `json!` in tests).

- [ ] **Step 2: Run the tests to check that they fail**

Run: `cargo test -p audiobookify-core --test voices`
Expected: compile errors: `no method named create_voice_profile`.

- [ ] **Step 3: Write `desktop/core/src/db/voices.rs`**

```rust
use rusqlite::types::Type;
use rusqlite::{params, Row, ToSql};

use super::{is_constraint, now, Database, DbError, Result};
use crate::models::{NewVoiceProfile, VoiceProfile};

impl Database {
    pub fn create_voice_profile(&self, v: &NewVoiceProfile<'_>) -> Result<VoiceProfile> {
        let inserted = self.conn.execute(
            "INSERT INTO voice_profiles (name, engine, reference_audio_path, reference_transcript, \
             exaggeration, cfg_weight, temperature, created_at, transcript_status) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![v.name, v.engine, v.reference_audio_path, v.reference_transcript, v.exaggeration,
                    v.cfg_weight, v.temperature, now(), v.transcript_status],
        );
        match inserted {
            Err(e) if is_constraint(&e) => {
                Err(DbError::Invalid(format!("a voice profile named '{}' already exists", v.name)))
            }
            Err(e) => Err(e.into()),
            Ok(_) => Ok(self.conn.query_row("SELECT * FROM voice_profiles WHERE id = ?1",
                                            [self.conn.last_insert_rowid()], voice_from_row)?),
        }
    }

    pub fn find_voice_profile_by_name(&self, name: &str) -> Result<Option<VoiceProfile>> {
        self.query_opt("SELECT * FROM voice_profiles WHERE name = ?1", [name], voice_from_row)
    }

    pub fn list_voice_profiles(&self) -> Result<Vec<VoiceProfile>> {
        self.query_all("SELECT * FROM voice_profiles ORDER BY id", [], voice_from_row)
    }

    pub fn get_voice_profile_by_id(&self, id: i64) -> Result<Option<VoiceProfile>> {
        self.query_opt("SELECT * FROM voice_profiles WHERE id = ?1", [id], voice_from_row)
    }

    pub fn update_voice_profile_reference_audio_path(&self, id: i64, reference_audio_path: &str) -> Result<()> {
        self.update_voice(id, "reference_audio_path = ?", &[&reference_audio_path])
    }

    pub fn next_pending_transcription(&self) -> Result<Option<VoiceProfile>> {
        self.query_opt(
            "SELECT * FROM voice_profiles WHERE transcript_status = 'pending' ORDER BY id LIMIT 1",
            [], voice_from_row,
        )
    }

    pub fn request_voice_transcription(&self, id: i64) -> Result<()> {
        self.update_voice(id, "transcript_status = 'pending', transcript_error = NULL", &[])
    }

    pub fn fail_voice_transcription(&self, id: i64, error: &str) -> Result<()> {
        self.update_voice(id, "transcript_status = 'failed', transcript_error = ?", &[&error])
    }

    /// A Whisper result (words given: replaces timings and trim) or a user edit
    /// of the text alone (keeps them).
    pub fn save_voice_transcript(&self, id: i64, text: &str, words: Option<&serde_json::Value>,
                                 trim_end: Option<f64>) -> Result<()> {
        match words {
            None => self.update_voice(
                id, "reference_transcript = ?, transcript_status = 'done', transcript_error = NULL", &[&text],
            ),
            Some(words) => {
                let words = serde_json::to_string(words)?;
                self.update_voice(
                    id,
                    "reference_transcript = ?, transcript_status = 'done', transcript_error = NULL, \
                     reference_words = ?, reference_trim_end = ?",
                    &[&text, &words, &trim_end],
                )
            }
        }
    }

    pub fn set_voice_tempo(&self, id: i64, tempo: f64) -> Result<()> {
        self.update_voice(id, "tempo = ?", &[&tempo])
    }

    pub fn delete_voice_profile(&self, id: i64) -> Result<()> {
        if self.conn.execute("DELETE FROM voice_profiles WHERE id = ?1", [id])? == 0 {
            return Err(DbError::NotFound(format!("no voice profile with id {id}")));
        }
        Ok(())
    }

    /// `assignments` uses plain `?` placeholders; the id is bound last.
    fn update_voice(&self, id: i64, assignments: &str, values: &[&dyn ToSql]) -> Result<()> {
        let mut bound = values.to_vec();
        bound.push(&id);
        let sql = format!("UPDATE voice_profiles SET {assignments} WHERE id = ?");
        if self.conn.execute(&sql, bound.as_slice())? == 0 {
            return Err(DbError::NotFound(format!("no voice profile with id {id}")));
        }
        Ok(())
    }
}

fn voice_from_row(r: &Row<'_>) -> rusqlite::Result<VoiceProfile> {
    Ok(VoiceProfile {
        id: r.get("id")?, name: r.get("name")?, engine: r.get("engine")?,
        reference_audio_path: r.get("reference_audio_path")?,
        reference_transcript: r.get("reference_transcript")?,
        exaggeration: r.get("exaggeration")?, cfg_weight: r.get("cfg_weight")?,
        temperature: r.get("temperature")?, created_at: r.get("created_at")?,
        transcript_status: r.get("transcript_status")?, transcript_error: r.get("transcript_error")?,
        reference_words: json_column(r, "reference_words")?,
        reference_trim_end: r.get("reference_trim_end")?, tempo: r.get("tempo")?,
    })
}

/// A JSON text column; NULL or empty is None, as in db.py.
fn json_column(r: &Row<'_>, name: &str) -> rusqlite::Result<Option<serde_json::Value>> {
    let text: Option<String> = r.get(name)?;
    match text.as_deref() {
        None | Some("") => Ok(None),
        Some(text) => serde_json::from_str(text).map(Some).map_err(|e| {
            let index = r.as_ref().column_index(name).unwrap_or(0);
            rusqlite::Error::FromSqlConversionFailure(index, Type::Text, Box::new(e))
        }),
    }
}
```
In `desktop/core/src/db/mod.rs`, add `mod voices;` below the `use` lines.

- [ ] **Step 4: Run the tests to check that they pass**

Run: `cargo test -p audiobookify-core --test voices`
Expected: `test result: ok. 14 passed`.

- [ ] **Step 5: Commit**

```bash
git add desktop/core
git commit -m "Port voice profile DB operations to Rust"
```

---

### Task 4: Lexicon entries

**Files:**
- Create: `desktop/core/src/db/lexicon.rs`
- Modify: `desktop/core/src/db/mod.rs` (add `mod lexicon;`)
- Test: `desktop/core/tests/lexicon.rs`

**Interfaces:**
- Consumes: `now`, `is_constraint`, `query_opt`, `query_all`
- Produces (all `&self`, all `Result`):
  - `create_lexicon_entry(&NewLexiconEntry) -> LexiconEntry` (`Invalid("a lexicon entry for '{term}' already exists in this scope")`)
  - `find_lexicon_entry(term: &str, book_id: i64) -> Option<LexiconEntry>`. Pass `0` for global.
  - `list_lexicon_entries(book_id: Option<i64>) -> Vec<LexiconEntry>`
  - `delete_lexicon_entry(term: &str, book_id: i64) -> ()`, `update_lexicon_entry_override(term: &str, override_value: &str, book_id: i64) -> LexiconEntry`
  - Both return `NotFound("no lexicon entry for '{term}' in this scope")` when nothing matched.

- [ ] **Step 1: Write the failing tests `desktop/core/tests/lexicon.rs`**

```rust
mod common;

use audiobookify_core::models::NewLexiconEntry;
use common::*;
use tempfile::TempDir;

fn global<'a>(term: &'a str, value: &'a str) -> NewLexiconEntry<'a> {
    NewLexiconEntry::new(term, value, "global")
}

fn for_book<'a>(term: &'a str, value: &'a str, book_id: i64) -> NewLexiconEntry<'a> {
    NewLexiconEntry { book_id, ..NewLexiconEntry::new(term, value, "book") }
}

fn from_audio<'a>() -> NewLexiconEntry<'a> {
    NewLexiconEntry {
        source: "from_audio", phonemes: Some("k ə d eɪ ɹ n"), sample_audio_path: Some("/clips/cadeyrn.wav"),
        ..global("Cadeyrn", "kuhdayrn")
    }
}

#[test]
fn create_and_find_global_lexicon_entry() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let entry = db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    assert_eq!(entry.book_id, 0);
    let found = db.find_lexicon_entry("Cadeyrn", 0).unwrap().unwrap();
    assert_eq!(found.override_value, "KAH-dern");
    assert_eq!(found.scope, "global");
    assert_eq!(found.source, "manual");
}

#[test]
fn create_and_find_book_scoped_lexicon_entry() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    let entry = db.create_lexicon_entry(&NewLexiconEntry {
        notes: Some("book-specific take"), ..for_book("Cadeyrn", "kah-DAIRN", book.id)
    }).unwrap();
    assert_eq!(entry.book_id, book.id);

    let found = db.find_lexicon_entry("Cadeyrn", book.id).unwrap().unwrap();
    assert_eq!(found.override_value, "kah-DAIRN");
    assert_eq!(found.notes.as_deref(), Some("book-specific take"));
    // the global lookup must not see the book-scoped entry
    assert!(db.find_lexicon_entry("Cadeyrn", 0).unwrap().is_none());
}

#[test]
fn create_lexicon_entry_rejects_duplicate_term_in_same_scope() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    assert_eq!(invalid(db.create_lexicon_entry(&global("Cadeyrn", "something-else"))),
               "a lexicon entry for 'Cadeyrn' already exists in this scope");
}

#[test]
fn create_lexicon_entry_allows_same_term_at_different_scopes() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    // a book-scoped override of a globally-defined term is legitimate
    db.create_lexicon_entry(&for_book("Cadeyrn", "kah-DAIRN", book.id)).unwrap();
}

#[test]
fn list_lexicon_entries_without_book_id_returns_global_only() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    db.create_lexicon_entry(&global("Global One", "g-one")).unwrap();
    db.create_lexicon_entry(&for_book("Book One", "b-one", book.id)).unwrap();
    let terms: Vec<String> = db.list_lexicon_entries(None).unwrap().into_iter().map(|e| e.term).collect();
    assert_eq!(terms, vec!["Global One"]);
}

#[test]
fn list_lexicon_entries_with_book_id_returns_book_scoped_first_then_global() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    db.create_lexicon_entry(&global("Global One", "g-one")).unwrap();
    db.create_lexicon_entry(&for_book("Book One", "b-one", book.id)).unwrap();
    let terms: Vec<String> =
        db.list_lexicon_entries(Some(book.id)).unwrap().into_iter().map(|e| e.term).collect();
    assert_eq!(terms, vec!["Book One", "Global One"]);
}

#[test]
fn delete_lexicon_entry_removes_it() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    db.delete_lexicon_entry("Cadeyrn", 0).unwrap();
    assert!(db.find_lexicon_entry("Cadeyrn", 0).unwrap().is_none());
}

#[test]
fn delete_lexicon_entry_raises_not_found_for_missing_entry() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert_eq!(not_found(db.delete_lexicon_entry("Cadeyrn", 0)), "no lexicon entry for 'Cadeyrn' in this scope");
}

#[test]
fn create_lexicon_entry_stores_phonemes_and_sample_audio_path() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let entry = db.create_lexicon_entry(&from_audio()).unwrap();
    assert_eq!(entry.source, "from_audio");
    assert_eq!(entry.phonemes.as_deref(), Some("k ə d eɪ ɹ n"));
    assert_eq!(entry.sample_audio_path.as_deref(), Some("/clips/cadeyrn.wav"));
    let found = db.find_lexicon_entry("Cadeyrn", 0).unwrap().unwrap();
    assert_eq!(found.phonemes.as_deref(), Some("k ə d eɪ ɹ n"));
    assert_eq!(found.sample_audio_path.as_deref(), Some("/clips/cadeyrn.wav"));
}

#[test]
fn create_lexicon_entry_defaults_phonemes_and_sample_audio_path_to_none() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let entry = db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    assert_eq!(entry.phonemes, None);
    assert_eq!(entry.sample_audio_path, None);
}

#[test]
fn update_lexicon_entry_override_changes_value_and_keeps_metadata() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    db.create_lexicon_entry(&NewLexiconEntry { notes: Some("derived from audio"), ..from_audio() }).unwrap();

    let updated = db.update_lexicon_entry_override("Cadeyrn", "kah-DAIRN", 0).unwrap();

    assert_eq!(updated.override_value, "kah-DAIRN");
    assert_eq!(updated.source, "from_audio");
    assert_eq!(updated.phonemes.as_deref(), Some("k ə d eɪ ɹ n"));
    assert_eq!(updated.sample_audio_path.as_deref(), Some("/clips/cadeyrn.wav"));
    assert_eq!(updated.notes.as_deref(), Some("derived from audio"));
}

#[test]
fn update_lexicon_entry_override_raises_not_found_for_missing_entry() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert!(not_found(db.update_lexicon_entry_override("Cadeyrn", "kah-DAIRN", 0)).contains("Cadeyrn"));
}

#[test]
fn update_lexicon_entry_override_respects_book_scope() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    db.create_lexicon_entry(&for_book("Cadeyrn", "kah-DAIRN", book.id)).unwrap();

    db.update_lexicon_entry_override("Cadeyrn", "NEW-book-version", book.id).unwrap();

    assert_eq!(db.find_lexicon_entry("Cadeyrn", book.id).unwrap().unwrap().override_value, "NEW-book-version");
    assert_eq!(db.find_lexicon_entry("Cadeyrn", 0).unwrap().unwrap().override_value, "KAH-dern"); // global untouched
}
```

- [ ] **Step 2: Run the tests to check that they fail**

Run: `cargo test -p audiobookify-core --test lexicon`
Expected: compile errors: `no method named create_lexicon_entry`.

- [ ] **Step 3: Write `desktop/core/src/db/lexicon.rs`**

```rust
use rusqlite::{params, Row};

use super::{is_constraint, now, Database, DbError, Result};
use crate::models::{LexiconEntry, NewLexiconEntry};

impl Database {
    pub fn create_lexicon_entry(&self, e: &NewLexiconEntry<'_>) -> Result<LexiconEntry> {
        let inserted = self.conn.execute(
            "INSERT INTO lexicon_entries (term, override_value, source, scope, book_id, notes, \
             created_at, phonemes, sample_audio_path) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![e.term, e.override_value, e.source, e.scope, e.book_id, e.notes, now(),
                    e.phonemes, e.sample_audio_path],
        );
        match inserted {
            Err(err) if is_constraint(&err) => Err(DbError::Invalid(format!(
                "a lexicon entry for '{}' already exists in this scope", e.term
            ))),
            Err(err) => Err(err.into()),
            Ok(_) => Ok(self.conn.query_row("SELECT * FROM lexicon_entries WHERE id = ?1",
                                            [self.conn.last_insert_rowid()], lexicon_from_row)?),
        }
    }

    pub fn find_lexicon_entry(&self, term: &str, book_id: i64) -> Result<Option<LexiconEntry>> {
        self.query_opt("SELECT * FROM lexicon_entries WHERE term = ?1 AND book_id = ?2",
                       params![term, book_id], lexicon_from_row)
    }

    /// Global entries only, or a book's entries first and then the global ones.
    pub fn list_lexicon_entries(&self, book_id: Option<i64>) -> Result<Vec<LexiconEntry>> {
        match book_id {
            None => self.query_all("SELECT * FROM lexicon_entries WHERE book_id = 0 ORDER BY id", [],
                                   lexicon_from_row),
            Some(book_id) => self.query_all(
                "SELECT * FROM lexicon_entries WHERE book_id = ?1 OR book_id = 0 ORDER BY (book_id = 0), id",
                [book_id], lexicon_from_row,
            ),
        }
    }

    pub fn delete_lexicon_entry(&self, term: &str, book_id: i64) -> Result<()> {
        let changed = self.conn.execute("DELETE FROM lexicon_entries WHERE term = ?1 AND book_id = ?2",
                                        params![term, book_id])?;
        if changed == 0 {
            return Err(missing_entry(term));
        }
        Ok(())
    }

    pub fn update_lexicon_entry_override(&self, term: &str, override_value: &str,
                                         book_id: i64) -> Result<LexiconEntry> {
        let changed = self.conn.execute(
            "UPDATE lexicon_entries SET override_value = ?1 WHERE term = ?2 AND book_id = ?3",
            params![override_value, term, book_id],
        )?;
        if changed == 0 {
            return Err(missing_entry(term));
        }
        self.find_lexicon_entry(term, book_id)?.ok_or_else(|| missing_entry(term))
    }
}

fn missing_entry(term: &str) -> DbError {
    DbError::NotFound(format!("no lexicon entry for '{term}' in this scope"))
}

fn lexicon_from_row(r: &Row<'_>) -> rusqlite::Result<LexiconEntry> {
    Ok(LexiconEntry {
        id: r.get("id")?, term: r.get("term")?, override_value: r.get("override_value")?,
        source: r.get("source")?, scope: r.get("scope")?, book_id: r.get("book_id")?,
        notes: r.get("notes")?, created_at: r.get("created_at")?,
        phonemes: r.get("phonemes")?, sample_audio_path: r.get("sample_audio_path")?,
    })
}
```
In `desktop/core/src/db/mod.rs`, add `mod lexicon;` next to `mod voices;`.

- [ ] **Step 4: Run the tests to check that they pass**

Run: `cargo test -p audiobookify-core --test lexicon`
Expected: `test result: ok. 13 passed`.

- [ ] **Step 5: Commit**

```bash
git add desktop/core
git commit -m "Port lexicon DB operations to Rust"
```

---

### Task 5: Jobs, text clips, worker state

Execute this task **before Task 2** (see Task 2 Step 2).

**Files:**
- Create: `desktop/core/src/db/jobs.rs`
- Modify: `desktop/core/src/db/mod.rs` (add `mod jobs;`)
- Test: `desktop/core/tests/jobs.rs`

**Interfaces:**
- Consumes: `now`, `query_opt`, `query_all`, `update_chapter_status`, `add_chapters`, `create_book`
- Produces (all `&self`, all `Result`):
  - Jobs: `create_job(chapter_id: i64) -> Job`, `get_job(i64) -> Job` (`NotFound("no job with id {id}")`), `list_jobs_for_chapter(i64) -> Vec<Job>`, `latest_job_for_chapter(i64) -> Option<Job>`, `pop_next_queued_job() -> Option<Job>`, `update_job_progress(i64, f64)`, `finish_job(i64, JobStatus, error: Option<&str>)`, `reset_stale_generating_jobs()`, `request_job_cancel(i64)`, `is_job_cancel_requested(i64) -> bool`, `requeue_job(i64)`
  - Clips: `create_text_clip(text: &str, voice_profile_id: Option<i64>, engine: &str, tempo: Option<f64>) -> TextClip`, `get_text_clip(i64) -> TextClip` (`NotFound("no text clip with id {id}")`), `list_text_clips() -> Vec<TextClip>`, `delete_text_clip(i64)`, `pop_next_queued_text_clip() -> Option<TextClip>`, `update_text_clip_progress(i64, f64)`, `finish_text_clip(i64, JobStatus, output_path: Option<&str>, error: Option<&str>, used_fallback: bool)`, `reset_stale_generating_text_clips()`
  - Worker: `is_paused() -> bool`, `set_paused(bool)`

- [ ] **Step 1: Write the failing tests `desktop/core/tests/jobs.rs`**

```rust
mod common;

use audiobookify_core::models::{ChapterOutcome, ChapterStatus, JobStatus, DEFAULT_ENGINE};
use common::*;
use tempfile::TempDir;

#[test]
fn create_job_defaults_to_queued_with_zero_progress() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    let job = db.create_job(chapter.id).unwrap();
    assert_eq!(job.chapter_id, chapter.id);
    assert_eq!(job.status, JobStatus::Queued);
    assert_eq!(job.progress, Some(0.0));
    assert_eq!((job.started_at, job.finished_at, job.error), (None, None, None));
}

#[test]
fn get_job_raises_not_found_for_missing_id() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert_eq!(not_found(db.get_job(999)), "no job with id 999");
}

#[test]
fn list_jobs_for_chapter_orders_most_recent_first() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    let first = db.create_job(chapter.id).unwrap();
    let second = db.create_job(chapter.id).unwrap();
    let ids: Vec<i64> = db.list_jobs_for_chapter(chapter.id).unwrap().iter().map(|j| j.id).collect();
    assert_eq!(ids, vec![second.id, first.id]);
    assert_eq!(db.latest_job_for_chapter(chapter.id).unwrap().unwrap().id, second.id);
}

#[test]
fn latest_job_for_chapter_returns_none_when_no_jobs() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    assert!(db.latest_job_for_chapter(chapter.id).unwrap().is_none());
}

#[test]
fn pop_next_queued_job_claims_oldest_and_marks_generating() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    let job = db.create_job(chapter.id).unwrap();
    let popped = db.pop_next_queued_job().unwrap().unwrap();
    assert_eq!(popped.id, job.id);
    assert_eq!(popped.status, JobStatus::Generating);
    assert!(popped.started_at.is_some());
}

#[test]
fn pop_next_queued_job_returns_none_when_queue_empty() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert!(db.pop_next_queued_job().unwrap().is_none());
}

#[test]
fn pop_next_queued_job_orders_fifo_by_creation_order() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (book, first_chapter) = book_with_chapter(&db, dir.path());
    let chapters = db.add_chapters(book.id, &[raw("Ch 2", "Body 2.")]).unwrap();
    let first_job = db.create_job(first_chapter.id).unwrap();
    db.create_job(chapters[1].id).unwrap();
    assert_eq!(db.pop_next_queued_job().unwrap().unwrap().id, first_job.id);
}

#[test]
fn pop_next_queued_job_does_not_reclaim_a_generating_job() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    db.create_job(chapter.id).unwrap();
    db.pop_next_queued_job().unwrap(); // claims it -> generating
    assert!(db.pop_next_queued_job().unwrap().is_none());
}

#[test]
fn update_job_progress_updates_value() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    let job = db.create_job(chapter.id).unwrap();
    db.update_job_progress(job.id, 0.5).unwrap();
    assert_eq!(db.get_job(job.id).unwrap().progress, Some(0.5));
}

#[test]
fn finish_job_marks_done_and_sets_progress_to_one() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    let job = db.create_job(chapter.id).unwrap();
    db.update_job_progress(job.id, 0.5).unwrap();
    db.finish_job(job.id, JobStatus::Done, None).unwrap();
    let finished = db.get_job(job.id).unwrap();
    assert_eq!(finished.status, JobStatus::Done);
    assert_eq!(finished.progress, Some(1.0));
    assert!(finished.finished_at.is_some());
}

#[test]
fn finish_job_marks_failed_and_preserves_last_progress() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    let job = db.create_job(chapter.id).unwrap();
    db.update_job_progress(job.id, 0.5).unwrap();
    db.finish_job(job.id, JobStatus::Failed, Some("synthesis blew up")).unwrap();
    let finished = db.get_job(job.id).unwrap();
    assert_eq!(finished.status, JobStatus::Failed);
    assert_eq!(finished.progress, Some(0.5));
    assert_eq!(finished.error.as_deref(), Some("synthesis blew up"));
}

#[test]
fn reset_stale_generating_jobs_requeues_generating_rows() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    let job = db.create_job(chapter.id).unwrap();
    db.pop_next_queued_job().unwrap(); // -> generating
    db.reset_stale_generating_jobs().unwrap();
    let reset = db.get_job(job.id).unwrap();
    assert_eq!(reset.status, JobStatus::Queued);
    assert_eq!(reset.started_at, None);
}

#[test]
fn is_paused_defaults_to_false() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert!(!db.is_paused().unwrap());
}

#[test]
fn set_paused_toggles_the_flag() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    db.set_paused(true).unwrap();
    assert!(db.is_paused().unwrap());
    db.set_paused(false).unwrap();
    assert!(!db.is_paused().unwrap());
}

#[test]
fn is_job_cancel_requested_defaults_to_false() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    let job = db.create_job(chapter.id).unwrap();
    assert!(!db.is_job_cancel_requested(job.id).unwrap());
    assert_eq!(not_found(db.is_job_cancel_requested(999)), "no job with id 999");
}

#[test]
fn request_job_cancel_sets_the_flag() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    let job = db.create_job(chapter.id).unwrap();
    db.request_job_cancel(job.id).unwrap();
    assert!(db.is_job_cancel_requested(job.id).unwrap());
}

#[test]
fn requeue_job_resets_status_and_clears_started_at() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let (_, chapter) = book_with_chapter(&db, dir.path());
    let job = db.create_job(chapter.id).unwrap();
    db.pop_next_queued_job().unwrap(); // -> generating, started_at set
    db.requeue_job(job.id).unwrap();
    let requeued = db.get_job(job.id).unwrap();
    assert_eq!(requeued.status, JobStatus::Queued);
    assert_eq!(requeued.started_at, None);
}

#[test]
fn finish_text_clip_sets_used_fallback() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let clip = db.create_text_clip("Hello.", None, DEFAULT_ENGINE, None).unwrap();
    db.finish_text_clip(clip.id, JobStatus::Done, Some("/out/clip.mp3"), None, true).unwrap();
    let finished = db.get_text_clip(clip.id).unwrap();
    assert!(finished.used_fallback);
    assert_eq!(finished.output_path.as_deref(), Some("/out/clip.mp3"));
    assert_eq!(finished.progress, Some(1.0));
}

#[test]
fn books_and_clips_remember_their_engine_and_chapters_record_what_made_them() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), &path(dir.path(), "b.md"));
    assert_eq!(book.engine, "chatterbox-turbo");
    db.update_book_engine(book.id, "qwen3").unwrap();
    assert_eq!(db.get_book(book.id).unwrap().engine, "qwen3");

    let chapter = db.add_chapters(book.id, &[raw("Ch 1", "Body.")]).unwrap().remove(0);
    assert_eq!(chapter.engine, None);
    db.update_chapter_status(chapter.id, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some("/x.mp3"), duration: Some(1.0), engine: Some("qwen3"), ..Default::default()
    }).unwrap();
    db.update_chapter_status(chapter.id, ChapterStatus::Generating, ChapterOutcome::default()).unwrap(); // requeue keeps the record
    assert_eq!(db.get_chapter(chapter.id).unwrap().engine.as_deref(), Some("qwen3"));

    assert_eq!(db.create_text_clip("Hello.", None, "qwen3", None).unwrap().engine, "qwen3");
    assert_eq!(db.create_text_clip("Hello.", None, DEFAULT_ENGINE, None).unwrap().engine, "chatterbox-turbo");
}

#[test]
fn text_clip_tempo_is_stored_and_defaults_to_the_voices() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert_eq!(db.create_text_clip("Hi.", None, DEFAULT_ENGINE, None).unwrap().tempo, None);
    assert_eq!(db.create_text_clip("Hi.", None, DEFAULT_ENGINE, Some(0.9)).unwrap().tempo, Some(0.9));
}

#[test]
fn text_clip_queue_claims_oldest_tracks_progress_and_resets_stale() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let first = db.create_text_clip("One.", None, DEFAULT_ENGINE, None).unwrap();
    db.create_text_clip("Two.", None, DEFAULT_ENGINE, None).unwrap();
    assert_eq!(first.status, JobStatus::Queued);
    assert_eq!(first.progress, Some(0.0));

    let popped = db.pop_next_queued_text_clip().unwrap().unwrap();
    assert_eq!(popped.id, first.id);
    assert_eq!(popped.status, JobStatus::Generating);
    assert!(popped.started_at.is_some());

    db.update_text_clip_progress(first.id, 0.25).unwrap();
    assert_eq!(db.get_text_clip(first.id).unwrap().progress, Some(0.25));

    db.reset_stale_generating_text_clips().unwrap();
    let reset = db.get_text_clip(first.id).unwrap();
    assert_eq!((reset.status, reset.started_at), (JobStatus::Queued, None));

    db.finish_text_clip(first.id, JobStatus::Failed, None, Some("boom"), false).unwrap();
    let failed = db.get_text_clip(first.id).unwrap();
    assert_eq!((failed.status, failed.progress, failed.error.as_deref()), (JobStatus::Failed, Some(0.25), Some("boom")));
}

#[test]
fn list_text_clips_returns_newest_first() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let first = db.create_text_clip("One.", None, DEFAULT_ENGINE, None).unwrap();
    let second = db.create_text_clip("Two.", None, DEFAULT_ENGINE, None).unwrap();
    let ids: Vec<i64> = db.list_text_clips().unwrap().iter().map(|c| c.id).collect();
    assert_eq!(ids, vec![second.id, first.id]);
}

#[test]
fn delete_text_clip_removes_it_and_rejects_missing_ids() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let clip = db.create_text_clip("One.", None, DEFAULT_ENGINE, None).unwrap();
    db.delete_text_clip(clip.id).unwrap();
    assert_eq!(not_found(db.get_text_clip(clip.id)), format!("no text clip with id {}", clip.id));
    not_found(db.delete_text_clip(clip.id));
}
```

- [ ] **Step 2: Run the tests to check that they fail**

Run: `cargo test -p audiobookify-core --test jobs`
Expected: compile errors: `no method named create_job`.

- [ ] **Step 3: Write `desktop/core/src/db/jobs.rs`**

```rust
use rusqlite::{params, Row};

use super::{now, Database, DbError, Result};
use crate::models::{Job, JobStatus, TextClip};

impl Database {
    pub fn create_job(&self, chapter_id: i64) -> Result<Job> {
        self.conn.execute(
            "INSERT INTO jobs (chapter_id, status, progress, created_at) VALUES (?1, ?2, ?3, ?4)",
            params![chapter_id, JobStatus::Queued, 0.0, now()],
        )?;
        self.get_job(self.conn.last_insert_rowid())
    }

    pub fn get_job(&self, job_id: i64) -> Result<Job> {
        self.query_opt("SELECT * FROM jobs WHERE id = ?1", [job_id], job_from_row)?
            .ok_or_else(|| missing_job(job_id))
    }

    pub fn list_jobs_for_chapter(&self, chapter_id: i64) -> Result<Vec<Job>> {
        self.query_all("SELECT * FROM jobs WHERE chapter_id = ?1 ORDER BY created_at DESC, id DESC",
                       [chapter_id], job_from_row)
    }

    pub fn latest_job_for_chapter(&self, chapter_id: i64) -> Result<Option<Job>> {
        self.query_opt("SELECT * FROM jobs WHERE chapter_id = ?1 ORDER BY created_at DESC, id DESC LIMIT 1",
                       [chapter_id], job_from_row)
    }

    pub fn pop_next_queued_job(&self) -> Result<Option<Job>> {
        let Some(job_id) = self.query_opt(
            "SELECT id FROM jobs WHERE status = ?1 ORDER BY created_at, id LIMIT 1",
            [JobStatus::Queued], |r| r.get::<_, i64>(0),
        )? else {
            return Ok(None);
        };
        let claimed = self.conn.execute(
            "UPDATE jobs SET status = ?1, started_at = ?2 WHERE id = ?3 AND status = ?4",
            params![JobStatus::Generating, now(), job_id, JobStatus::Queued],
        )?;
        if claimed == 0 {
            return Ok(None); // claimed by another worker between the SELECT and UPDATE
        }
        self.get_job(job_id).map(Some)
    }

    pub fn update_job_progress(&self, job_id: i64, progress: f64) -> Result<()> {
        self.conn.execute("UPDATE jobs SET progress = ?1 WHERE id = ?2", params![progress, job_id])?;
        Ok(())
    }

    pub fn finish_job(&self, job_id: i64, status: JobStatus, error: Option<&str>) -> Result<()> {
        let sql = if status == JobStatus::Done {
            "UPDATE jobs SET status = ?1, progress = 1.0, finished_at = ?2, error = ?3 WHERE id = ?4"
        } else {
            "UPDATE jobs SET status = ?1, finished_at = ?2, error = ?3 WHERE id = ?4"
        };
        self.conn.execute(sql, params![status, now(), error, job_id])?;
        Ok(())
    }

    pub fn reset_stale_generating_jobs(&self) -> Result<()> {
        self.conn.execute("UPDATE jobs SET status = ?1, started_at = NULL WHERE status = ?2",
                          params![JobStatus::Queued, JobStatus::Generating])?;
        Ok(())
    }

    pub fn request_job_cancel(&self, job_id: i64) -> Result<()> {
        self.conn.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?1", [job_id])?;
        Ok(())
    }

    pub fn is_job_cancel_requested(&self, job_id: i64) -> Result<bool> {
        self.query_opt("SELECT cancel_requested FROM jobs WHERE id = ?1", [job_id], |r| r.get::<_, bool>(0))?
            .ok_or_else(|| missing_job(job_id))
    }

    pub fn requeue_job(&self, job_id: i64) -> Result<()> {
        self.conn.execute("UPDATE jobs SET status = ?1, started_at = NULL WHERE id = ?2",
                          params![JobStatus::Queued, job_id])?;
        Ok(())
    }

    pub fn create_text_clip(&self, text: &str, voice_profile_id: Option<i64>, engine: &str,
                            tempo: Option<f64>) -> Result<TextClip> {
        self.conn.execute(
            "INSERT INTO text_clips (text, voice_profile_id, status, progress, created_at, engine, tempo) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![text, voice_profile_id, JobStatus::Queued, 0.0, now(), engine, tempo],
        )?;
        self.get_text_clip(self.conn.last_insert_rowid())
    }

    pub fn get_text_clip(&self, clip_id: i64) -> Result<TextClip> {
        self.query_opt("SELECT * FROM text_clips WHERE id = ?1", [clip_id], clip_from_row)?
            .ok_or_else(|| missing_clip(clip_id))
    }

    pub fn list_text_clips(&self) -> Result<Vec<TextClip>> {
        self.query_all("SELECT * FROM text_clips ORDER BY id DESC", [], clip_from_row)
    }

    pub fn delete_text_clip(&self, clip_id: i64) -> Result<()> {
        if self.conn.execute("DELETE FROM text_clips WHERE id = ?1", [clip_id])? == 0 {
            return Err(missing_clip(clip_id));
        }
        Ok(())
    }

    pub fn pop_next_queued_text_clip(&self) -> Result<Option<TextClip>> {
        let Some(clip_id) = self.query_opt(
            "SELECT id FROM text_clips WHERE status = ?1 ORDER BY created_at, id LIMIT 1",
            [JobStatus::Queued], |r| r.get::<_, i64>(0),
        )? else {
            return Ok(None);
        };
        let claimed = self.conn.execute(
            "UPDATE text_clips SET status = ?1, started_at = ?2 WHERE id = ?3 AND status = ?4",
            params![JobStatus::Generating, now(), clip_id, JobStatus::Queued],
        )?;
        if claimed == 0 {
            return Ok(None); // claimed by another worker between the SELECT and UPDATE
        }
        self.get_text_clip(clip_id).map(Some)
    }

    pub fn update_text_clip_progress(&self, clip_id: i64, progress: f64) -> Result<()> {
        self.conn.execute("UPDATE text_clips SET progress = ?1 WHERE id = ?2", params![progress, clip_id])?;
        Ok(())
    }

    pub fn finish_text_clip(&self, clip_id: i64, status: JobStatus, output_path: Option<&str>,
                            error: Option<&str>, used_fallback: bool) -> Result<()> {
        if status == JobStatus::Done {
            self.conn.execute(
                "UPDATE text_clips SET status = ?1, progress = 1.0, output_path = ?2, \
                 finished_at = ?3, error = ?4, used_fallback = ?5 WHERE id = ?6",
                params![status, output_path, now(), error, used_fallback, clip_id],
            )?;
        } else {
            self.conn.execute(
                "UPDATE text_clips SET status = ?1, finished_at = ?2, error = ?3 WHERE id = ?4",
                params![status, now(), error, clip_id],
            )?;
        }
        Ok(())
    }

    pub fn reset_stale_generating_text_clips(&self) -> Result<()> {
        self.conn.execute("UPDATE text_clips SET status = ?1, started_at = NULL WHERE status = ?2",
                          params![JobStatus::Queued, JobStatus::Generating])?;
        Ok(())
    }

    pub fn is_paused(&self) -> Result<bool> {
        Ok(self.query_opt("SELECT paused FROM worker_state WHERE id = 1", [], |r| r.get::<_, bool>(0))?
            .unwrap_or(false))
    }

    pub fn set_paused(&self, paused: bool) -> Result<()> {
        self.conn.execute(
            "INSERT INTO worker_state (id, paused) VALUES (1, ?1) \
             ON CONFLICT(id) DO UPDATE SET paused = excluded.paused",
            [paused],
        )?;
        Ok(())
    }
}

fn missing_job(job_id: i64) -> DbError {
    DbError::NotFound(format!("no job with id {job_id}"))
}

fn missing_clip(clip_id: i64) -> DbError {
    DbError::NotFound(format!("no text clip with id {clip_id}"))
}

fn job_from_row(r: &Row<'_>) -> rusqlite::Result<Job> {
    Ok(Job {
        id: r.get("id")?, chapter_id: r.get("chapter_id")?, status: r.get("status")?,
        progress: r.get("progress")?, created_at: r.get("created_at")?,
        started_at: r.get("started_at")?, finished_at: r.get("finished_at")?, error: r.get("error")?,
    })
}

fn clip_from_row(r: &Row<'_>) -> rusqlite::Result<TextClip> {
    Ok(TextClip {
        id: r.get("id")?, text: r.get("text")?, voice_profile_id: r.get("voice_profile_id")?,
        status: r.get("status")?, progress: r.get("progress")?, output_path: r.get("output_path")?,
        created_at: r.get("created_at")?, started_at: r.get("started_at")?,
        finished_at: r.get("finished_at")?, error: r.get("error")?,
        used_fallback: r.get("used_fallback")?, engine: r.get("engine")?, tempo: r.get("tempo")?,
    })
}
```
In `desktop/core/src/db/mod.rs`, add `mod jobs;` next to the other `mod` lines.

`query_opt` takes a `fn` pointer. Closures that capture nothing, like `|r| r.get::<_, i64>(0)`, coerce to one.

- [ ] **Step 4: Run the tests to check that they pass**

Run: `cargo test -p audiobookify-core --test jobs`
Expected: `test result: ok. 23 passed`.

- [ ] **Step 5: Commit**

```bash
git add desktop/core
git commit -m "Port job, text clip and worker state DB operations to Rust"
```

---

### Task 6: Column migrations and idempotency

**Files:**
- Create: `desktop/core/src/db/migrations.rs`
- Modify: `desktop/core/src/db/mod.rs` (`pub mod migrations;`, call it from `open`)
- Test: `desktop/core/tests/migrations.rs`

**Interfaces:**
- Consumes: `Database::open`
- Produces: `audiobookify_core::db::migrations::migrate_columns(conn: &rusqlite::Connection) -> rusqlite::Result<()>`, which runs inside `open`'s transaction.

- [ ] **Step 1: Write the failing tests `desktop/core/tests/migrations.rs`**

```rust
mod common;

use std::path::Path;

use audiobookify_core::models::{ChapterOutcome, ChapterStatus, JobStatus, NewLexiconEntry, DEFAULT_ENGINE};
use common::*;
use rusqlite::Connection;
use tempfile::TempDir;

fn raw_conn(dir: &Path) -> Connection {
    Connection::open(dir.join("test.db")).unwrap()
}

/// Every table's SQL as SQLite stores it, which ALTER TABLE rewrites.
fn schema(dir: &Path) -> Vec<(String, String)> {
    let conn = raw_conn(dir);
    let mut stmt = conn
        .prepare("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")
        .unwrap();
    let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
    rows.map(Result::unwrap).collect()
}

#[test]
fn existing_m2_database_gains_new_lexicon_columns_via_migration() {
    // A database file created before M3 (no phonemes/sample_audio_path columns).
    let dir = TempDir::new().unwrap();
    raw_conn(dir.path()).execute_batch(
        "CREATE TABLE lexicon_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL COLLATE NOCASE,
            override_value TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            scope TEXT NOT NULL,
            book_id INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at REAL NOT NULL,
            UNIQUE(book_id, term)
        )",
    ).unwrap();

    let db = open(dir.path()); // must not fail
    let entry = db.create_lexicon_entry(&NewLexiconEntry::new("Cadeyrn", "KAH-dern", "global")).unwrap();
    assert_eq!(entry.phonemes, None);
}

#[test]
fn existing_pre_fallback_database_gains_used_fallback_columns_via_migration() {
    let dir = TempDir::new().unwrap();
    raw_conn(dir.path()).execute_batch(
        "CREATE TABLE chapters (
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
        );
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
        );",
    ).unwrap();

    let db = open(dir.path()); // must not fail
    let book = md_book(&db, dir.path(), "/books/t.md");
    let chapter = db.add_chapters(book.id, &[raw("Ch 1", "Body.")]).unwrap().remove(0);
    assert!(!chapter.used_fallback);

    let clip = db.create_text_clip("Hi.", None, DEFAULT_ENGINE, None).unwrap();
    db.finish_text_clip(clip.id, JobStatus::Done, Some("/o.mp3"), None, true).unwrap();
    assert!(db.get_text_clip(clip.id).unwrap().used_fallback);
}

#[test]
fn existing_pre_cancel_database_gains_cancel_requested_column_via_migration() {
    let dir = TempDir::new().unwrap();
    raw_conn(dir.path()).execute_batch(
        "CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            progress REAL,
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL,
            error TEXT
        )",
    ).unwrap();

    let db = open(dir.path()); // must not fail
    let (_, chapter) = book_with_chapter(&db, dir.path());
    let job = db.create_job(chapter.id).unwrap();
    assert!(!db.is_job_cancel_requested(job.id).unwrap());
}

#[test]
fn existing_database_gains_transcript_and_engine_columns_via_migration() {
    // A pre-Phase-2 database: voices with no transcript state, finished chapters
    // made by the old PyTorch Chatterbox engine.
    let dir = TempDir::new().unwrap();
    let old = open(dir.path());
    let book = md_book(&old, dir.path(), &path(dir.path(), "b.md"));
    let chapters = old.add_chapters(book.id, &[raw("1", "A."), raw("2", "B.")]).unwrap();
    let (done, pending) = (&chapters[0], &chapters[1]);
    old.update_chapter_status(done.id, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some("/x.mp3"), duration: Some(1.0), ..Default::default()
    }).unwrap();
    drop(old);
    let conn = raw_conn(dir.path());
    for (table, col) in [("books", "engine"), ("text_clips", "engine"), ("chapters", "engine"),
                         ("voice_profiles", "transcript_status"), ("voice_profiles", "transcript_error"),
                         ("voice_profiles", "reference_words"), ("voice_profiles", "reference_trim_end"),
                         ("voice_profiles", "tempo"), ("text_clips", "tempo")] {
        conn.execute(&format!("ALTER TABLE {table} DROP COLUMN {col}"), []).unwrap();
    }
    conn.execute(
        "INSERT INTO voice_profiles (name, engine, reference_audio_path, exaggeration, cfg_weight, \
         temperature, created_at) VALUES ('Old', 'chatterbox', ?1, 0.5, 0.5, 0.8, 0)",
        [path(dir.path(), "missing.wav")],
    ).unwrap();
    drop(conn);

    let db = open(dir.path());
    assert_eq!(db.get_book(book.id).unwrap().engine, "chatterbox-turbo");
    assert_eq!(db.get_chapter(done.id).unwrap().engine.as_deref(), Some("chatterbox")); // honest record of what made it
    assert_eq!(db.get_chapter(pending.id).unwrap().engine, None);
    let voice = db.find_voice_profile_by_name("Old").unwrap().unwrap();
    assert_eq!(voice.transcript_status, None); // transcribed lazily
    assert_eq!(voice.reference_words, None);
    assert_eq!(voice.tempo, 1.0);
}

#[test]
fn reopening_a_fresh_db_changes_nothing() {
    let dir = TempDir::new().unwrap();
    drop(open(dir.path()));
    let first = schema(dir.path());
    drop(open(dir.path()));
    assert_eq!(schema(dir.path()), first);
}

#[test]
fn reopening_a_migrated_old_db_changes_nothing() {
    // An old chapters table without used_fallback/engine, so the first open adds
    // chapters.engine and runs its backfill.
    let dir = TempDir::new().unwrap();
    raw_conn(dir.path()).execute_batch(
        "CREATE TABLE chapters (
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
        )",
    ).unwrap();
    let db = open(dir.path());
    let (_, done) = book_with_chapter(&db, dir.path());
    db.update_chapter_status(done.id, ChapterStatus::Done, ChapterOutcome::default()).unwrap();
    drop(db);
    let first = schema(dir.path());

    let db = open(dir.path());
    assert_eq!(schema(dir.path()), first);
    // The chapters.engine backfill only runs when the column is added, so a second
    // open must not relabel audio made after the migration.
    assert_eq!(db.get_chapter(done.id).unwrap().engine, None);
}
```

- [ ] **Step 2: Run the tests to check that they fail**

Run: `cargo test -p audiobookify-core --test migrations`
Expected: these 4 FAIL: `existing_m2_...`, `existing_pre_fallback_...`, `existing_pre_cancel_...` and `existing_database_gains_transcript_...`, with `no such column` / `has no column named` errors. The two `reopening_...` tests pass already.

- [ ] **Step 3: Write `desktop/core/src/db/migrations.rs`**

```rust
//! Schema changes for database files created by older versions, ported from db.py.
//! Each one checks first, so running them on an up-to-date file does nothing.

use std::collections::HashSet;

use rusqlite::Connection;

/// (table, column, declaration), in the order db.py adds them.
const ADDED_COLUMNS: &[(&str, &str, &str)] = &[
    ("lexicon_entries", "phonemes", "TEXT"),
    ("lexicon_entries", "sample_audio_path", "TEXT"),
    ("chapters", "used_fallback", "INTEGER NOT NULL DEFAULT 0"),
    ("text_clips", "used_fallback", "INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
    ("books", "engine", "TEXT NOT NULL DEFAULT 'chatterbox-turbo'"),
    ("text_clips", "engine", "TEXT NOT NULL DEFAULT 'chatterbox-turbo'"),
    ("chapters", "engine", "TEXT"),
    ("voice_profiles", "transcript_status", "TEXT"),
    ("voice_profiles", "transcript_error", "TEXT"),
    ("voice_profiles", "reference_words", "TEXT"),
    ("voice_profiles", "reference_trim_end", "REAL"),
    ("voice_profiles", "tempo", "REAL NOT NULL DEFAULT 1.0"),
    ("text_clips", "tempo", "REAL"),
];

pub fn migrate_columns(conn: &Connection) -> rusqlite::Result<()> {
    for &(table, column, declaration) in ADDED_COLUMNS {
        if columns(conn, table)?.contains(column) {
            continue;
        }
        conn.execute(&format!("ALTER TABLE {table} ADD COLUMN {column} {declaration}"), [])?;
        if (table, column) == ("chapters", "engine") {
            // Audio finished before engines were recorded came from PyTorch Chatterbox.
            conn.execute("UPDATE chapters SET engine = 'chatterbox' WHERE status = 'done'", [])?;
        }
    }
    Ok(())
}

fn columns(conn: &Connection, table: &str) -> rusqlite::Result<HashSet<String>> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let names = stmt.query_map([], |row| row.get::<_, String>("name"))?.collect();
    names
}
```
In `desktop/core/src/db/mod.rs`, add `pub mod migrations;` with the other `mod` lines. In `open`, change the transaction block to:
```rust
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        tx.execute_batch(SCHEMA)?;
        migrations::migrate_columns(&tx)?;
        tx.commit()?;
```

- [ ] **Step 4: Run all tests to check that they pass**

Run: `cargo test -p audiobookify-core`
Expected: every test binary reports `ok`. `migrations` runs 6 tests.

- [ ] **Step 5: Commit**

```bash
git add desktop/core
git commit -m "Port the ALTER TABLE migrations to Rust"
```

---

### Task 7: Voice reference-audio migration

**Files:**
- Create: `desktop/core/src/voice_storage.rs`
- Modify: `desktop/core/src/lib.rs`, `desktop/core/src/db/migrations.rs`, `desktop/core/src/db/mod.rs` (`open` gains `voices_dir`), `desktop/core/tests/common/mod.rs` (`open` helper)
- Test: `desktop/core/tests/migrations.rs`, unit test in `voice_storage.rs`

**Interfaces:**
- Consumes: `migrations` module, `DbError::Io`
- Produces:
  - `Database::open(path: impl AsRef<Path>, voices_dir: impl AsRef<Path>) -> Result<Database>`
  - `audiobookify_core::voice_storage::{sanitize_filename(&str) -> String, permanent_voice_dir(voices_dir: &Path, profile_id: i64, name: &str) -> PathBuf, store_reference_audio(voices_dir: &Path, staged: &Path, profile_id: i64, name: &str) -> io::Result<PathBuf>}`
  - `migrations::migrate_voice_reference_audio(conn: &Connection, voices_dir: &Path) -> crate::db::Result<Vec<String>>`, which returns warnings

- [ ] **Step 1: Point the test helper at a voices dir**

In `desktop/core/tests/common/mod.rs`, replace `open` with:
```rust
pub fn open(dir: &Path) -> Database {
    Database::open(dir.join("test.db"), dir.join("voices")).unwrap()
}
```

- [ ] **Step 2: Append the failing tests to `desktop/core/tests/migrations.rs`**

```rust
use std::fs;

use audiobookify_core::db::migrations::migrate_voice_reference_audio;

fn write_audio(dir: &Path, name: &str) -> String {
    let file = dir.join("original-upload").join(name);
    fs::create_dir_all(file.parent().unwrap()).unwrap();
    fs::write(&file, b"fake audio bytes").unwrap();
    file.to_string_lossy().into_owned()
}

fn is_stored_under_voices(dir: &Path, reference_audio_path: &str) -> bool {
    let stored = Path::new(reference_audio_path);
    stored.starts_with(dir.join("voices")) && stored.is_file()
}

#[test]
fn existing_voice_profile_gets_migrated_to_permanent_voices_dir() {
    let dir = TempDir::new().unwrap();
    let source = write_audio(dir.path(), "clip.wav");
    let db = open(dir.path());
    voice_at(&db, "Nova", &source);
    drop(db);

    let reopened = open(dir.path()); // a fresh open triggers the migration

    let profile = reopened.find_voice_profile_by_name("Nova").unwrap().unwrap();
    assert!(is_stored_under_voices(dir.path(), &profile.reference_audio_path));
    assert!(profile.reference_audio_path.ends_with("_Nova/reference.wav"));
}

#[test]
fn migration_skips_a_voice_profile_whose_source_file_no_longer_exists() {
    let dir = TempDir::new().unwrap();
    let missing = path(dir.path(), "does-not-exist.wav");
    let db = open(dir.path());
    voice_at(&db, "Ghost", &missing);
    drop(db);

    let reopened = open(dir.path()); // must not fail even though the source file is gone

    assert_eq!(reopened.find_voice_profile_by_name("Ghost").unwrap().unwrap().reference_audio_path, missing);
}

#[test]
fn migration_of_one_profile_does_not_block_migration_of_others() {
    let dir = TempDir::new().unwrap();
    let source = write_audio(dir.path(), "clip.wav");
    let missing = path(dir.path(), "does-not-exist.wav");
    let db = open(dir.path());
    voice_at(&db, "Ghost", &missing);
    voice_at(&db, "Nova", &source);
    drop(db);

    let reopened = open(dir.path());

    assert_eq!(reopened.find_voice_profile_by_name("Ghost").unwrap().unwrap().reference_audio_path, missing);
    let nova = reopened.find_voice_profile_by_name("Nova").unwrap().unwrap();
    assert!(is_stored_under_voices(dir.path(), &nova.reference_audio_path));
}

#[test]
fn migration_does_not_crash_when_storing_a_profile_raises_an_io_error() {
    let dir = TempDir::new().unwrap();
    let broken_audio = write_audio(dir.path(), "broken.wav");
    let fine_audio = write_audio(dir.path(), "fine.wav");
    drop(open(dir.path())); // creates the schema
    // Insert through a raw connection, so no open migrates either profile before the trap is set.
    let conn = raw_conn(dir.path());
    let mut ids = Vec::new();
    for (name, file) in [("Broken", &broken_audio), ("Fine", &fine_audio)] {
        conn.execute(
            "INSERT INTO voice_profiles (name, engine, reference_audio_path, exaggeration, cfg_weight, \
             temperature, created_at) VALUES (?1, 'chatterbox', ?2, 0.5, 0.5, 0.8, 0)",
            [name, file.as_str()],
        ).unwrap();
        ids.push(conn.last_insert_rowid());
    }
    let broken_id = ids[0];
    // A plain file where Broken's voice directory belongs makes create_dir_all fail.
    fs::create_dir_all(dir.path().join("voices")).unwrap();
    fs::write(dir.path().join("voices").join(format!("{broken_id}_Broken")), b"").unwrap();

    let warnings = migrate_voice_reference_audio(&conn, &dir.path().join("voices")).unwrap();

    assert_eq!(warnings.len(), 1);
    assert!(warnings[0].contains("could not be migrated"), "{warnings:?}");
    drop(conn);
    let reopened = open(dir.path());
    assert_eq!(reopened.find_voice_profile_by_name("Broken").unwrap().unwrap().reference_audio_path, broken_audio);
    let fine = reopened.find_voice_profile_by_name("Fine").unwrap().unwrap();
    assert!(is_stored_under_voices(dir.path(), &fine.reference_audio_path));
}
```
- [ ] **Step 3: Run the tests to check that they fail**

Run: `cargo test -p audiobookify-core`
Expected: compile errors: `Database::open` takes 1 argument but 2 were supplied, and `migrate_voice_reference_audio` not found.

- [ ] **Step 4: Write `desktop/core/src/voice_storage.rs`**

```rust
//! Where voice reference clips live permanently, ported from voice/storage.py.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

/// Drops the characters macOS/Windows file names can't hold, like postprocess/audio.py.
pub fn sanitize_filename(name: &str) -> String {
    name.chars().filter(|c| !r#"\/:*?"<>|"#.contains(*c)).collect::<String>().trim().to_string()
}

pub fn permanent_voice_dir(voices_dir: &Path, profile_id: i64, name: &str) -> PathBuf {
    voices_dir.join(format!("{profile_id}_{}", sanitize_filename(name)))
}

/// Copies a staged clip to `<voices_dir>/<id>_<name>/reference<ext>` and returns that path.
pub fn store_reference_audio(voices_dir: &Path, staged: &Path, profile_id: i64, name: &str) -> io::Result<PathBuf> {
    let dest_dir = permanent_voice_dir(voices_dir, profile_id, name);
    fs::create_dir_all(&dest_dir)?;
    let suffix = staged.extension().map(|ext| format!(".{}", ext.to_string_lossy())).unwrap_or_default();
    let dest = dest_dir.join(format!("reference{suffix}"));
    fs::copy(staged, &dest)?;
    Ok(dest)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitize_filename_drops_reserved_characters_and_trims() {
        assert_eq!(sanitize_filename(r#" a/b\c:d*e?f"g<h>i|j "#), "abcdefghij");
        assert_eq!(sanitize_filename("Jim Dale"), "Jim Dale");
    }
}
```
In `desktop/core/src/lib.rs`, add `pub mod voice_storage;`.

- [ ] **Step 5: Add the migration to `desktop/core/src/db/migrations.rs`**

Add these imports at the top:
```rust
use std::path::Path;

use crate::db::Result;
use crate::voice_storage;
```
Append:
```rust
/// Copies reference clips that live outside `voices_dir` (old uploads) into it and
/// points the profile at the copy. A profile that can't be migrated is left as it
/// is and reported in the returned warnings, so one bad file can't block startup.
pub fn migrate_voice_reference_audio(conn: &Connection, voices_dir: &Path) -> Result<Vec<String>> {
    let voices_root = std::path::absolute(voices_dir)?;
    let profiles: Vec<(i64, String, String)> = {
        let mut stmt = conn.prepare("SELECT id, name, reference_audio_path FROM voice_profiles")?;
        let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
        rows.collect::<rusqlite::Result<_>>()?
    };
    let mut warnings = Vec::new();
    for (id, name, current) in profiles {
        let current_path = Path::new(&current);
        if current_path.starts_with(&voices_root) {
            continue;
        }
        if !current_path.is_file() {
            warnings.push(format!(
                "voice profile {id} ({name})'s reference audio '{current}' no longer exists - \
                 skipping migration for this profile"
            ));
            continue;
        }
        match voice_storage::store_reference_audio(&voices_root, current_path, id, &name) {
            Ok(new_path) => {
                conn.execute("UPDATE voice_profiles SET reference_audio_path = ?1 WHERE id = ?2",
                             rusqlite::params![new_path.to_string_lossy(), id])?;
            }
            Err(e) => warnings.push(format!(
                "voice profile {id} ({name})'s reference audio could not be migrated to permanent \
                 storage ({e}) - skipping this profile"
            )),
        }
    }
    Ok(warnings)
}
```

- [ ] **Step 6: Run the migration from `open`**

In `desktop/core/src/db/mod.rs`, replace `open` with:
```rust
    /// `voices_dir` is where voice reference clips live permanently (repo `voices/` today).
    pub fn open(path: impl AsRef<Path>, voices_dir: impl AsRef<Path>) -> Result<Self> {
        let mut conn = Connection::open(path)?;
        // The API and the worker write this file from two processes at once.
        // WAL + a busy timeout turn lock contention into a brief wait instead of
        // "database is locked".
        conn.pragma_update_and_check(None, "journal_mode", "WAL", |row| row.get::<_, String>(0))?;
        conn.busy_timeout(Duration::from_secs(5))?;
        // IMMEDIATE takes the write lock up front, so two processes opening at once
        // can't both decide a column is missing and both try to add it.
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        tx.execute_batch(SCHEMA)?;
        migrations::migrate_columns(&tx)?;
        let warnings = migrations::migrate_voice_reference_audio(&tx, voices_dir.as_ref())?;
        tx.commit()?;
        // ponytail: no warn-once dedupe like db.py; the app opens the DB once, add it if it ever opens per request.
        for warning in warnings {
            eprintln!("Warning: {warning}");
        }
        Ok(Self { conn })
    }
```

- [ ] **Step 7: Run all tests to check that they pass**

Run: `cargo test -p audiobookify-core`
Expected: all binaries `ok`. Totals: books 15, voices 14, lexicon 13, jobs 23, migrations 10, plus 1 unit test.

- [ ] **Step 8: Commit**

```bash
git add desktop/core
git commit -m "Port the voice reference-audio migration to Rust"
```

---

### Task 8: Exit check against the real database

**Files:**
- Create: `desktop/core/examples/check_db.rs`

**Interfaces:**
- Consumes: the whole `Database` API.
- Produces: `cargo run -p audiobookify-core --example check_db -- <db> <voices_dir>`, which prints row counts, exits 0 on success, and exits nonzero with the error otherwise.

- [ ] **Step 1: Write `desktop/core/examples/check_db.rs`**

```rust
//! Phase 1 exit check: open a real audiobookify.db, decode every row, write, and
//! confirm a second open changes nothing. Run it on a copy, never the live file.

use std::error::Error;
use std::path::Path;

use audiobookify_core::db::Database;
use rusqlite::Connection;

fn schema(db_path: &Path) -> rusqlite::Result<Vec<(String, String)>> {
    let conn = Connection::open(db_path)?;
    let mut stmt = conn.prepare("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")?;
    let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?;
    rows.collect()
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [db_path, voices_dir] = args.as_slice() else {
        eprintln!("usage: check_db <path/to/audiobookify.db> <path/to/voices>");
        std::process::exit(2);
    };
    let db_path = Path::new(db_path);

    let db = Database::open(db_path, voices_dir)?;
    let books = db.list_books()?;
    let (mut chapters, mut jobs, mut lexicon) = (0, 0, db.list_lexicon_entries(None)?.len());
    for book in &books {
        let book_chapters = db.list_chapters(book.id)?;
        for chapter in &book_chapters {
            jobs += db.list_jobs_for_chapter(chapter.id)?.len();
        }
        chapters += book_chapters.len();
        lexicon += db.list_lexicon_entries(Some(book.id))?.iter().filter(|e| e.book_id == book.id).count();
    }
    let voices = db.list_voice_profiles()?.len();
    let clips = db.list_text_clips()?.len();

    let paused = db.is_paused()?;
    db.set_paused(!paused)?;
    assert_eq!(db.is_paused()?, !paused, "write did not stick");
    db.set_paused(paused)?;
    drop(db);

    let before = schema(db_path)?;
    drop(Database::open(db_path, voices_dir)?);
    assert_eq!(schema(db_path)?, before, "second open changed the schema");

    println!("ok: {} books, {chapters} chapters, {jobs} jobs, {voices} voices, {lexicon} lexicon entries, \
              {clips} text clips; write ok; reopen is a no-op", books.len());
    Ok(())
}
```

- [ ] **Step 2: Run it on a copy of the real database**

`.backup` takes a consistent snapshot even while the Python app is running and has WAL data pending. Run Steps 2-4 from `desktop/` in the same shell, because they share `$SCRATCH`:
```bash
SCRATCH=$(mktemp -d) && sqlite3 ../audiobookify.db ".backup '$SCRATCH/audiobookify.db'" && cargo run -p audiobookify-core --example check_db -- "$SCRATCH/audiobookify.db" "$(cd .. && pwd)/voices" && echo "$SCRATCH"
```
Expected: `ok: 7 books, ...` (counts as of 2026-10-14: 7 books, 1 voice), followed by the scratch path. Keep that path for Step 3.

If it fails with a `FromSqlConversionFailure` or an `unknown ChapterStatus`-style error, the real data holds a value the Python models accept but the Rust ones don't. Fix the model type, add a test for that value, and rerun.

- [ ] **Step 3: Check that Python still reads the database after Rust has written it**

```bash
../.venv/bin/python -c "import sys; sys.path.insert(0, '../src'); from audiobookify.db import Database; d = Database(sys.argv[1]); print(len(d.list_books()), 'books via Python')" "$SCRATCH/audiobookify.db"
```
Expected: `7 books via Python`, the same count as Step 2.

- [ ] **Step 4: Check that a fresh Rust database has Python's exact schema**

```bash
../.venv/bin/python -c "import sys; sys.path.insert(0, '../src'); from audiobookify.db import Database; Database(sys.argv[1])" "$SCRATCH/py.db" && cargo run -q -p audiobookify-core --example check_db -- "$SCRATCH/rs.db" "$SCRATCH/voices" && diff <(sqlite3 "$SCRATCH/py.db" .schema) <(sqlite3 "$SCRATCH/rs.db" .schema) && echo "schemas identical"
```
Expected: `ok: 0 books, ...` then `schemas identical`.

- [ ] **Step 5: Run the whole suite once more, plus the Python DB tests**

Run: `cargo test -p audiobookify-core && (cd .. && .venv/bin/pytest tests/test_db.py tests/test_chapter_breaks.py -q)`
Expected: every Rust binary `ok`, and pytest passes (Python is untouched, so this only confirms nothing leaked).

- [ ] **Step 6: Commit**

```bash
git add desktop/core/examples
git commit -m "Add check_db example for the phase 1 exit check"
```

---

## Not in this phase

- The app crate does not depend on `audiobookify-core` yet. That starts in phase 3 (job runner) or phase 4 (commands), whichever needs it first.
- No `serde` derives on the models. Phase 4 adds them for Tauri command results.
- No connection pooling or `Mutex<Database>`. Phase 4 decides that when commands share the DB.
- No `delete_voice_storage`. Phase 4 ports it with the voice delete command.

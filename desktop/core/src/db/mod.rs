//! The audiobookify SQLite database, ported from `src/audiobookify/db.py`.

use std::collections::HashMap;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension, Params, Row, TransactionBehavior};

use crate::models::{Book, Chapter, ChapterOutcome, ChapterStatus, RawChapter, SourceFormat};

pub mod migrations;
mod jobs;
mod lexicon;
mod voices;

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
    /// `voices_dir` is where voice reference clips live permanently. While the Python app shares
    /// this file it must be the Python app's `VOICES_DIR` (the repo's `voices/` dir); any other dir
    /// makes every open rewrite `reference_audio_path` for all profiles (and Python rewrites them back).
    pub fn open(path: impl AsRef<Path>, voices_dir: impl AsRef<Path>) -> Result<Self> {
        let mut conn = Connection::open(path)?;
        // The API and the worker write this file from two processes at once.
        // WAL + a busy timeout turn lock contention into a brief wait instead of
        // "database is locked".
        conn.pragma_update_and_check(None, "journal_mode", "WAL", |row| row.get::<_, String>(0))?;
        conn.busy_timeout(Duration::from_secs(5))?;
        // Match Python, which never enforced REFERENCES (the bundled SQLite defaults them on);
        // existing DBs may hold dangling refs. Must precede the transaction: it's a no-op inside one.
        conn.pragma_update(None, "foreign_keys", false)?;
        // IMMEDIATE serializes Rust openers and takes the write lock before the column checks.
        // Python's own migrations don't lock, so this is not a cross-language guarantee
        // (real DBs are already migrated).
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
}

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

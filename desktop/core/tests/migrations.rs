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

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
    stored.is_file() && fs::canonicalize(stored).unwrap().starts_with(fs::canonicalize(dir).unwrap().join("voices"))
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

#[test]
fn migration_leaves_an_already_migrated_clip_alone_under_a_symlinked_voices_dir() {
    let dir = TempDir::new().unwrap();
    let resolved = fs::canonicalize(dir.path()).unwrap();
    let real = resolved.join("real");
    fs::create_dir_all(&real).unwrap();
    std::os::unix::fs::symlink(&real, resolved.join("link")).unwrap();
    // The profile already holds the resolved path, as Python's resolve() stored it.
    let clip_dir = real.join("voices").join("1_Nova");
    fs::create_dir_all(&clip_dir).unwrap();
    let clip = clip_dir.join("reference.wav");
    fs::write(&clip, b"precious audio").unwrap();
    drop(audiobookify_core::db::Database::open(real.join("test.db"), real.join("voices")).unwrap()); // creates the schema
    let conn = Connection::open(real.join("test.db")).unwrap();
    conn.execute(
        "INSERT INTO voice_profiles (id, name, engine, reference_audio_path, exaggeration, cfg_weight, \
         temperature, created_at) VALUES (1, 'Nova', 'chatterbox', ?1, 0.5, 0.5, 0.8, 0)",
        [clip.to_string_lossy().as_ref()],
    ).unwrap();

    let warnings = migrate_voice_reference_audio(&conn, &resolved.join("link").join("voices")).unwrap();

    assert!(warnings.is_empty(), "{warnings:?}");
    assert_eq!(fs::read(&clip).unwrap(), b"precious audio");
    let stored: String = conn
        .query_row("SELECT reference_audio_path FROM voice_profiles WHERE id = 1", [], |r| r.get(0))
        .unwrap();
    assert_eq!(stored, clip.to_string_lossy());
}

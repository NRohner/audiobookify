mod common;

use audiobookify_core::app::stats::{self, Named};
use audiobookify_core::models::{ChapterOutcome, ChapterStatus, JobStatus};
use common::*;
use rusqlite::Connection;
use tempfile::TempDir;

#[test]
fn empty_library_is_all_zero() {
    let dir = TempDir::new().unwrap();
    let (_p, db) = app(dir.path());
    let s = stats::stats(&db).unwrap();
    assert_eq!((s.audio_seconds, s.words, s.books_finished, s.chapters_done, s.disk_bytes), (0.0, 0, 0, 0, 0));
    assert!(s.longest_book.is_none() && s.top_voice.is_none() && s.busiest_day.is_none());
}

#[test]
fn totals_come_from_done_chapters_and_clips() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = md_book(&db, dir.path(), &path(dir.path(), "b.md"));
    let chapters = db.add_chapters(book.id, &[raw("One", "three little words"), raw("Two", "not done yet")]).unwrap();
    let mp3 = dir.path().join("one.mp3");
    std::fs::write(&mp3, vec![0u8; 5000]).unwrap();
    let job = db.create_job(chapters[0].id).unwrap();
    db.pop_next_queued_job().unwrap();
    db.finish_job(job.id, JobStatus::Done, None).unwrap();
    timing(&paths, job.id, 100.0, 112.0);
    db.update_chapter_status(chapters[0].id, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some(&mp3.to_string_lossy()), duration: Some(60.0), engine: Some("qwen3"),
        ..Default::default() }).unwrap();
    let clip = db.create_text_clip("Hi.", None, "chatterbox-turbo", None).unwrap();
    db.finish_text_clip(clip.id, JobStatus::Done, Some("/nope.mp3"), None, false).unwrap();
    db.set_text_clip_duration(clip.id, 4.0).unwrap();

    let s = stats::stats(&db).unwrap();
    assert_eq!(s.audio_seconds, 64.0);
    assert_eq!(s.words, 3);
    assert_eq!(s.chapters_done, 1);
    assert_eq!(s.books_finished, 0); // chapter two isn't done
    assert_eq!(s.disk_bytes, 5000);
    assert_eq!(s.longest_book, Some(Named { name: "Test Book".into(), seconds: 60.0 }));
    assert_eq!(s.busiest_day.as_ref().unwrap().chapters, 1);
    let qwen = s.engines.iter().find(|e| e.engine == "qwen3").unwrap();
    assert_eq!(qwen.audio_seconds, 60.0);
    assert_eq!(qwen.generating_seconds, 12.0);
}

#[test]
fn a_finished_book_counts_and_its_voice_is_top() {
    let dir = TempDir::new().unwrap();
    let (_p, db) = app(dir.path());
    let (book, chapter) = book_with_chapter(&db, dir.path());
    let v = voice(&db, dir.path(), "Harriet", Some("hi"), Some("done"));
    db.update_book_voice_profile(book.id, Some(v.id)).unwrap();
    db.update_chapter_status(chapter.id, ChapterStatus::Done,
        ChapterOutcome { duration: Some(30.0), ..Default::default() }).unwrap();
    let s = stats::stats(&db).unwrap();
    assert_eq!(s.books_finished, 1);
    assert_eq!(s.top_voice, Some(Named { name: "Harriet".into(), seconds: 30.0 }));
}

#[test]
fn missing_durations_are_backfilled_from_the_mp3_once() {
    let dir = TempDir::new().unwrap();
    let (_p, db) = app(dir.path());
    let (_book, chapter) = book_with_chapter(&db, dir.path());
    let wav_path = dir.path().join("ch.wav"); // symphonia reads any format; a WAV keeps the test small
    wav(&wav_path, 24000, 1, 48000, |_| 0.0);
    db.update_chapter_status(chapter.id, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some(&wav_path.to_string_lossy()), duration: None, ..Default::default() }).unwrap();

    assert_eq!(stats::stats(&db).unwrap().audio_seconds, 2.0);
    assert_eq!(db.get_chapter(chapter.id).unwrap().duration, Some(2.0));
}

fn conn(paths: &audiobookify_core::app::AppPaths) -> Connection {
    Connection::open(paths.db()).unwrap()
}

fn timing(paths: &audiobookify_core::app::AppPaths, job_id: i64, started: f64, finished: f64) {
    conn(paths).execute("UPDATE jobs SET started_at = ?1, finished_at = ?2 WHERE id = ?3",
                       rusqlite::params![started, finished, job_id]).unwrap();
}

fn done_job(db: &audiobookify_core::db::Database, chapter_id: i64) -> i64 {
    let job = db.create_job(chapter_id).unwrap();
    db.pop_next_queued_job().unwrap();
    db.finish_job(job.id, JobStatus::Done, None).unwrap();
    job.id
}

#[test]
fn the_migration_adds_text_clip_duration() {
    let dir = TempDir::new().unwrap();
    let db_path = dir.path().join("old.db");
    Connection::open(&db_path).unwrap().execute_batch(
        "CREATE TABLE text_clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
            voice_profile_id INTEGER, status TEXT NOT NULL DEFAULT 'queued', progress REAL,
            output_path TEXT, created_at REAL NOT NULL, started_at REAL, finished_at REAL, error TEXT)").unwrap();
    for _ in 0..2 {
        let db = audiobookify_core::db::Database::open(&db_path, dir.path().join("voices")).unwrap();
        let clip = db.create_text_clip("Hi.", None, "chatterbox-turbo", None).unwrap();
        db.set_text_clip_duration(clip.id, 1.5).unwrap();
    }
    let seconds: f64 = Connection::open(&db_path).unwrap()
        .query_row("SELECT duration FROM text_clips WHERE id = 1", [], |r| r.get(0)).unwrap();
    assert_eq!(seconds, 1.5);
}

#[test]
fn a_text_clip_without_a_duration_is_backfilled() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let wav_path = dir.path().join("clip.wav");
    wav(&wav_path, 24000, 1, 24000, |_| 0.0);
    let clip = db.create_text_clip("Hi.", None, "chatterbox-turbo", None).unwrap();
    db.finish_text_clip(clip.id, JobStatus::Done, Some(&wav_path.to_string_lossy()), None, false).unwrap();
    assert_eq!(stats::stats(&db).unwrap().audio_seconds, 1.0);
    let stored: f64 = conn(&paths).query_row("SELECT duration FROM text_clips WHERE id = ?1", [clip.id], |r| r.get(0)).unwrap();
    assert_eq!(stored, 1.0);
}

#[test]
fn a_missing_file_leaves_the_duration_unset() {
    let dir = TempDir::new().unwrap();
    let (_p, db) = app(dir.path());
    let (_book, chapter) = book_with_chapter(&db, dir.path());
    db.update_chapter_status(chapter.id, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some("/nope/gone.mp3"), ..Default::default() }).unwrap();
    assert_eq!(stats::stats(&db).unwrap().audio_seconds, 0.0);
    assert_eq!(db.get_chapter(chapter.id).unwrap().duration, None);
}

#[test]
fn a_rerendered_chapter_counts_once() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let (_book, chapter) = book_with_chapter(&db, dir.path());
    db.update_chapter_status(chapter.id, ChapterStatus::Done, ChapterOutcome {
        duration: Some(30.0), engine: Some("qwen3"), ..Default::default() }).unwrap();
    let first = done_job(&db, chapter.id);
    let second = done_job(&db, chapter.id);
    timing(&paths, first, 10.0, 15.0);
    timing(&paths, second, 20.0, 27.0);
    let s = stats::stats(&db).unwrap();
    assert_eq!(s.engines.len(), 1);
    assert_eq!((s.engines[0].audio_seconds, s.engines[0].generating_seconds), (30.0, 7.0));
    assert_eq!(s.busiest_day.unwrap().chapters, 1);
}

#[test]
fn a_done_job_without_a_finish_time_does_not_break_stats() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let (_book, chapter) = book_with_chapter(&db, dir.path());
    db.update_chapter_status(chapter.id, ChapterStatus::Done, ChapterOutcome {
        duration: Some(30.0), engine: Some("qwen3"), ..Default::default() }).unwrap();
    let job = done_job(&db, chapter.id);
    conn(&paths).execute("UPDATE jobs SET started_at = 5.0, finished_at = NULL WHERE id = ?1", [job]).unwrap();
    assert_eq!(stats::stats(&db).unwrap().audio_seconds, 30.0);
}

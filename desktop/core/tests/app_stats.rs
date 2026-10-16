mod common;

use audiobookify_core::app::stats::{self, Named};
use audiobookify_core::models::{ChapterOutcome, ChapterStatus, JobStatus};
use common::*;
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
    let (_p, db) = app(dir.path());
    let book = md_book(&db, dir.path(), &path(dir.path(), "b.md"));
    let chapters = db.add_chapters(book.id, &[raw("One", "three little words"), raw("Two", "not done yet")]).unwrap();
    let mp3 = dir.path().join("one.mp3");
    std::fs::write(&mp3, vec![0u8; 5000]).unwrap();
    let job = db.create_job(chapters[0].id).unwrap();
    db.pop_next_queued_job().unwrap();
    db.finish_job(job.id, JobStatus::Done, None).unwrap();
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
    assert!(qwen.generating_seconds >= 0.0);
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

#[test]
fn the_migration_adds_text_clip_duration() {
    let dir = TempDir::new().unwrap();
    let (_p, db) = app(dir.path());
    let clip = db.create_text_clip("Hi.", None, "chatterbox-turbo", None).unwrap();
    db.set_text_clip_duration(clip.id, 1.5).unwrap();
}

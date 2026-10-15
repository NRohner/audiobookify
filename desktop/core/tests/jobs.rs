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

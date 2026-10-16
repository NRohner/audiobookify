mod common;

use std::fs;

use audiobookify_core::app::books::{self, BookDto};
use audiobookify_core::app::{jobs, AppPaths};
use audiobookify_core::db::Database;
use audiobookify_core::jobs::ChunkCache;
use audiobookify_core::models::{ChapterOutcome, ChapterStatus, JobStatus};
use common::*;
use tempfile::TempDir;

fn setup(dir: &std::path::Path) -> (AppPaths, Database, BookDto) {
    let (paths, db) = app(dir);
    let src = write_file(dir, "book.md", TWO_CHAPTERS_MD);
    let book = books::create_book(&db, &paths, &src, &path(dir, "out")).unwrap();
    (paths, db, book)
}

fn finish(db: &Database, chapter_id: i64, mp3: Option<&str>) {
    db.update_chapter_status(chapter_id, ChapterStatus::Done,
        ChapterOutcome { output_mp3_path: mp3, duration: Some(1.0), ..Default::default() }).unwrap();
}

// test_generate_endpoint_creates_queued_jobs_for_all_chapters,
// ..._does_not_duplicate_jobs_for_already_queued_chapters, ..._does_not_requeue_already_done_chapters
#[test]
fn generate_book_queues_each_unfinished_chapter_once() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (_p, db, book) = setup(dir.path());
    finish(&db, book.chapters[0].id, Some("/a.mp3"));

    let first = jobs::generate_book(&db, &models, book.id, None).unwrap();
    let again = jobs::generate_book(&db, &models, book.id, None).unwrap();

    assert_eq!(first.queued_job_ids.len(), 1);
    assert_eq!(db.get_job(first.queued_job_ids[0]).unwrap().chapter_id, book.chapters[1].id);
    assert!(again.queued_job_ids.is_empty());
}

// test_generate_endpoint_returns_404_for_unknown_book,
// test_generate_rejects_unknown_engines_and_quality_without_a_voice
#[test]
fn generate_book_rejects_unknown_books_and_bad_engines() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (_p, db, book) = setup(dir.path());
    assert_eq!(api_err(jobs::generate_book(&db, &models, 999, None)), "book not found");
    assert_eq!(api_err(jobs::generate_book(&db, &models, book.id, Some("nope"))), "unknown engine 'nope'");
    assert_eq!(api_err(jobs::generate_book(&db, &models, book.id, Some("qwen3"))),
               "Quality (Qwen3) needs a voice: pick one first");
}

// test_generating_a_book_stores_the_chosen_engine_for_every_later_job
#[test]
fn generate_book_stores_the_engine() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (_p, db, book) = setup(dir.path());
    let voice = voice(&db, dir.path(), "Nova", Some("hi"), Some("done"));
    books::assign_voice(&db, book.id, Some(voice.id)).unwrap();
    jobs::generate_book(&db, &models, book.id, Some("qwen3")).unwrap();
    assert_eq!(db.get_book(book.id).unwrap().engine, "qwen3");
}

// test_generate_chapter_endpoint_queues_only_that_chapter, ..._does_not_requeue_an_already_queued_chapter,
// ..._does_not_requeue_an_already_done_chapter
#[test]
fn generate_chapter_queues_only_that_chapter_once() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (_p, db, book) = setup(dir.path());
    let (a, b) = (book.chapters[0].id, book.chapters[1].id);

    let queued = jobs::generate_chapter(&db, &models, book.id, b, None).unwrap();
    assert_eq!(db.get_job(queued.job_id.unwrap()).unwrap().chapter_id, b);
    assert!(db.latest_job_for_chapter(a).unwrap().is_none());
    assert_eq!(jobs::generate_chapter(&db, &models, book.id, b, None).unwrap().job_id, None);

    finish(&db, a, Some("/a.mp3"));
    assert_eq!(jobs::generate_chapter(&db, &models, book.id, a, None).unwrap().job_id, None);
}

// test_generate_chapter_endpoint_returns_404_for_unknown_book, ..._for_unknown_chapter,
// ..._when_chapter_belongs_to_a_different_book
#[test]
fn generate_chapter_rejects_unknown_and_foreign_chapters() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (paths, db, book) = setup(dir.path());
    let src = write_file(dir.path(), "other.md", TWO_CHAPTERS_MD);
    let other = books::create_book(&db, &paths, &src, &path(dir.path(), "out")).unwrap();
    assert_eq!(api_err(jobs::generate_chapter(&db, &models, 999, book.chapters[0].id, None)), "book not found");
    assert_eq!(api_err(jobs::generate_chapter(&db, &models, book.id, 999, None)), "chapter not found");
    assert_eq!(api_err(jobs::generate_chapter(&db, &models, book.id, other.chapters[0].id, None)), "chapter not found");
}

// test_regenerating_one_chapter_with_another_engine_switches_the_book
#[test]
fn generating_a_chapter_with_another_engine_switches_the_book() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (_p, db, book) = setup(dir.path());
    let voice = voice(&db, dir.path(), "Nova", Some("hi"), Some("done"));
    books::assign_voice(&db, book.id, Some(voice.id)).unwrap();
    jobs::generate_chapter(&db, &models, book.id, book.chapters[0].id, Some("qwen3")).unwrap();
    assert_eq!(db.get_book(book.id).unwrap().engine, "qwen3");
}

// test_worker_status_reports_not_paused_by_default, test_worker_pause_then_status_reports_paused,
// test_worker_resume_after_pause_reports_not_paused
#[test]
fn worker_pause_and_resume() {
    let dir = TempDir::new().unwrap();
    let (_p, db, _b) = setup(dir.path());
    assert!(!jobs::worker_status(&db).unwrap().paused);
    assert!(jobs::set_paused(&db, true).unwrap().paused);
    assert!(jobs::worker_status(&db).unwrap().paused);
    assert!(!jobs::set_paused(&db, false).unwrap().paused);
    assert!(!jobs::worker_status(&db).unwrap().paused);
}

// test_cancel_chapter_with_a_queued_job_marks_it_cancelled_and_resets_the_chapter,
// test_cancel_chapter_clears_the_cache_for_a_paused_then_cancelled_job,
// test_get_book_does_not_report_stale_progress_after_cancelling_a_queued_job
#[test]
fn cancelling_a_queued_chapter_cancels_the_job_resets_the_chapter_and_clears_its_cache() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    let ch = book.chapters[0].id;
    let job = db.create_job(ch).unwrap();
    db.update_job_progress(job.id, 0.4).unwrap();
    let cache = ChunkCache::new(paths.cache());
    fs::create_dir_all(cache.chapter_dir(ch)).unwrap();

    jobs::cancel_chapter(&db, &paths, book.id, ch).unwrap();

    assert_eq!(db.get_job(job.id).unwrap().status, JobStatus::Cancelled);
    assert_eq!(db.get_chapter(ch).unwrap().status, ChapterStatus::Pending);
    assert!(!cache.chapter_dir(ch).exists());
    assert_eq!(books::get_book(&db, book.id).unwrap().chapters[0].progress, None);
}

// test_cancel_chapter_with_a_generating_job_sets_cancel_requested_without_changing_status_yet
#[test]
fn cancelling_a_generating_chapter_asks_the_runner_to_stop() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    db.create_job(book.chapters[0].id).unwrap();
    let job = db.pop_next_queued_job().unwrap().unwrap();

    jobs::cancel_chapter(&db, &paths, book.id, book.chapters[0].id).unwrap();

    assert!(db.is_job_cancel_requested(job.id).unwrap());
    assert_eq!(db.get_job(job.id).unwrap().status, JobStatus::Generating);
}

// test_cancel_chapter_with_no_active_job_is_a_noop, test_cancel_chapter_is_a_noop_for_an_already_cancelled_job
#[test]
fn cancelling_a_chapter_without_an_active_job_changes_nothing() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    let ch = book.chapters[0].id;
    jobs::cancel_chapter(&db, &paths, book.id, ch).unwrap();
    let job = db.create_job(ch).unwrap();
    jobs::cancel_chapter(&db, &paths, book.id, ch).unwrap();
    jobs::cancel_chapter(&db, &paths, book.id, ch).unwrap();
    assert_eq!(db.get_job(job.id).unwrap().status, JobStatus::Cancelled);
}

// test_cancel_book_returns_404_for_an_unknown_book, test_cancel_chapter_returns_404_for_a_chapter_in_a_different_book
#[test]
fn cancel_rejects_unknown_books_and_foreign_chapters() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    let src = write_file(dir.path(), "other.md", TWO_CHAPTERS_MD);
    let other = books::create_book(&db, &paths, &src, &path(dir.path(), "out")).unwrap();
    assert_eq!(api_err(jobs::cancel_book(&db, &paths, 999)), "book not found");
    assert_eq!(api_err(jobs::cancel_chapter(&db, &paths, 999, book.chapters[0].id)), "book not found");
    assert_eq!(api_err(jobs::cancel_chapter(&db, &paths, book.id, other.chapters[0].id)), "chapter not found");
}

// test_cancel_book_cancels_every_chapter_with_an_active_job
#[test]
fn cancel_book_cancels_every_active_chapter() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (paths, db, book) = setup(dir.path());
    let ids = jobs::generate_book(&db, &models, book.id, None).unwrap().queued_job_ids;
    jobs::cancel_book(&db, &paths, book.id).unwrap();
    for id in ids {
        assert_eq!(db.get_job(id).unwrap().status, JobStatus::Cancelled);
    }
}

// test_chapter_audio_serves_the_finished_mp3, test_chapter_audio_404s_until_the_chapter_is_done,
// test_chapter_audio_404s_when_the_file_is_missing, test_chapter_audio_404s_for_unknown_book_or_a_chapter_of_another_book
#[test]
fn chapter_audio_path_is_only_given_for_a_finished_file() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    let (a, b) = (book.chapters[0].id, book.chapters[1].id);
    assert_eq!(api_err(jobs::chapter_audio_path(&db, book.id, a)), "audio not ready");

    let mp3 = write_file(dir.path(), "a.mp3", "ID3");
    finish(&db, a, Some(&mp3.to_string_lossy()));
    assert_eq!(jobs::chapter_audio_path(&db, book.id, a).unwrap(), mp3);

    finish(&db, b, Some(&path(dir.path(), "gone.mp3")));
    assert_eq!(api_err(jobs::chapter_audio_path(&db, book.id, b)), "audio not ready");

    let src = write_file(dir.path(), "other.md", TWO_CHAPTERS_MD);
    let other = books::create_book(&db, &paths, &src, &path(dir.path(), "out")).unwrap();
    assert_eq!(api_err(jobs::chapter_audio_path(&db, other.id, a)), "chapter not found");
    assert_eq!(api_err(jobs::chapter_audio_path(&db, book.id, 999)), "chapter not found");
}

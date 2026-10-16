mod common;

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use audiobookify_core::db::Database;
use audiobookify_core::jobs::{Paths, Runner};
use audiobookify_core::models::*;
use common::*;
use tempfile::TempDir;

const LONG: &str = "She looked out over the water for a long time before she finally spoke again.";

/// A runner on a temp DB and temp paths, driving the real engine worker in fake mode.
/// Every synthesized chunk's text is logged to <dir>/log.jsonl.
fn runner(dir: &Path, env: &[(&str, &str)]) -> Runner {
    let log = dir.join("log.jsonl");
    let mut env = env.to_vec();
    let log_str = log.to_string_lossy().into_owned();
    env.push(("AUDIOBOOKIFY_FAKE_LOG", &log_str));
    Runner::new(open(dir), fake_engine(&env), Paths {
        cache_root: dir.join("cache"), text_clip_dir: dir.join("clips"), backup_dir: dir.join("backup"),
    })
}

fn logged(dir: &Path) -> Vec<String> {
    std::fs::read_to_string(dir.join("log.jsonl")).unwrap_or_default().lines()
        .map(|l| serde_json::from_str(l).unwrap()).collect()
}

/// A "fake"-engine book with one chapter per text and a queued job for each.
fn book_with_jobs(db: &Database, dir: &Path, texts: &[&str]) -> (Book, Vec<Chapter>, Vec<Job>) {
    let book = md_book(db, dir, &path(dir, "b.md"));
    db.update_book_engine(book.id, "fake").unwrap();
    let raws: Vec<RawChapter> = texts.iter().enumerate().map(|(i, t)| raw(&format!("Ch {}", i + 1), t)).collect();
    let chapters = db.add_chapters(book.id, &raws).unwrap();
    let jobs = chapters.iter().map(|c| db.create_job(c.id).unwrap()).collect();
    (db.get_book(book.id).unwrap(), chapters, jobs)
}

/// Three chunks: each paragraph is far over the fake engine's 20-char minimum.
fn three_paragraphs() -> String {
    format!("{LONG}\n\n{LONG} Again.\n\n{LONG} Once more.")
}

/// Run `act` on a second DB connection as soon as the first chunk has started.
fn when_first_chunk_starts(dir: &Path, act: impl FnOnce(&Database) + Send + 'static) -> std::thread::JoinHandle<()> {
    let dir: PathBuf = dir.to_path_buf();
    std::thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(60);
        while logged(&dir).is_empty() {
            assert!(Instant::now() < deadline, "first chunk never started");
            std::thread::sleep(Duration::from_millis(20));
        }
        act(&open(&dir));
    })
}

#[test]
fn run_once_is_false_with_nothing_queued() {
    let dir = TempDir::new().unwrap();
    assert!(!runner(dir.path(), &[]).run_once().unwrap());
}

#[test]
fn a_chapter_job_produces_a_tagged_mp3_and_marks_everything_done() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    let (_, chapters, jobs) = book_with_jobs(&r.db, dir.path(), &["Body one is long enough to speak."]);

    assert!(r.run_once().unwrap());

    let job = r.db.get_job(jobs[0].id).unwrap();
    let chapter = r.db.get_chapter(chapters[0].id).unwrap();
    assert_eq!((job.status, job.progress), (JobStatus::Done, Some(1.0)));
    assert_eq!((chapter.status, chapter.engine.as_deref(), chapter.used_fallback), (ChapterStatus::Done, Some("fake"), false));
    assert!(chapter.duration.unwrap() > 0.0);
    assert_eq!(chapter.output_mp3_path.as_deref(), Some(path(dir.path(), "out/01 - Ch 1.mp3").as_str()));
    assert!(Path::new(chapter.output_mp3_path.as_deref().unwrap()).is_file());
    assert!(!dir.path().join(format!("cache/chapter-{}", chapter.id)).exists(), "cache not cleared");
}

#[test]
fn chunks_by_paragraph_and_never_sends_a_tiny_chunk() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    book_with_jobs(&r.db, dir.path(), &[&format!("{LONG}\n\nNo.\n\n* * *\n\n{LONG}")]);
    r.run_once().unwrap();
    assert_eq!(logged(dir.path()), vec![format!("{LONG} No."), LONG.to_string()]);
}

#[test]
fn applies_the_lexicon_with_book_entries_winning_over_global_ones() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    let (book, _, _) = book_with_jobs(&r.db, dir.path(), &["Cadeyrn rode out at dawn with the others."]);
    r.db.create_lexicon_entry(&NewLexiconEntry::new("Cadeyrn", "global-take", "global")).unwrap();
    r.db.create_lexicon_entry(&NewLexiconEntry { book_id: book.id, ..NewLexiconEntry::new("Cadeyrn", "kah-DAIRN", "book") }).unwrap();
    r.run_once().unwrap();
    assert_eq!(logged(dir.path()), vec!["kah-DAIRN rode out at dawn with the others.".to_string()]);
}

#[test]
fn a_synthesis_error_fails_the_job_and_the_chapter() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_FAIL_ON", "body one")]);
    let (_, chapters, jobs) = book_with_jobs(&r.db, dir.path(), &["Body one is long enough to speak."]);
    assert!(r.run_once().unwrap());
    let job = r.db.get_job(jobs[0].id).unwrap();
    assert_eq!(job.status, JobStatus::Failed);
    assert!(job.error.unwrap().contains("synthesis blew up"));
    assert_eq!(r.db.get_chapter(chapters[0].id).unwrap().status, ChapterStatus::Failed);
}

#[test]
fn an_unknown_engine_fails_the_job() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    let (book, _, jobs) = book_with_jobs(&r.db, dir.path(), &["Body one is long enough to speak."]);
    r.db.update_book_engine(book.id, "no-such-engine").unwrap();
    assert!(r.run_once().unwrap());
    let job = r.db.get_job(jobs[0].id).unwrap();
    assert_eq!(job.status, JobStatus::Failed);
    assert!(job.error.unwrap().contains("no-such-engine"));
}

#[test]
fn a_crashed_engine_fails_its_job_and_the_next_job_respawns_it() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_CRASH_ON", "crash")]);
    let (_, _, jobs) = book_with_jobs(&r.db, dir.path(), &["Crash here, long enough to speak.", "Body two is long enough to speak."]);
    r.run_once().unwrap();
    let first = r.db.get_job(jobs[0].id).unwrap();
    assert_eq!(first.status, JobStatus::Failed);
    assert!(first.error.unwrap().starts_with("engine worker failed"));
    r.run_once().unwrap();
    assert_eq!(r.db.get_job(jobs[1].id).unwrap().status, JobStatus::Done);
}

#[test]
fn records_used_fallback_when_the_output_dir_is_unwritable() {
    use std::os::unix::fs::PermissionsExt;
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    let (book, chapters, _) = book_with_jobs(&r.db, dir.path(), &["Body one is long enough to speak."]);
    let out = dir.path().join("out");
    std::fs::create_dir(&out).unwrap();
    std::fs::set_permissions(&out, std::fs::Permissions::from_mode(0o500)).unwrap();
    r.run_once().unwrap();
    std::fs::set_permissions(&out, std::fs::Permissions::from_mode(0o700)).unwrap();
    let chapter = r.db.get_chapter(chapters[0].id).unwrap();
    assert!(chapter.used_fallback);
    assert!(chapter.output_mp3_path.unwrap().starts_with(&path(dir.path(), &format!("backup/{}", book.id))));
}

#[test]
fn a_pause_mid_chapter_requeues_the_job_keeps_the_cache_and_resumes_without_redoing_chunks() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_DELAY", "0.5")]);
    let (_, chapters, jobs) = book_with_jobs(&r.db, dir.path(), &[&three_paragraphs()]);
    let pauser = when_first_chunk_starts(dir.path(), |db| db.set_paused(true).unwrap());

    assert!(r.run_once().unwrap());
    pauser.join().unwrap();

    assert_eq!(r.db.get_job(jobs[0].id).unwrap().status, JobStatus::Queued);
    assert_eq!(r.db.get_chapter(chapters[0].id).unwrap().status, ChapterStatus::Pending);
    assert!(dir.path().join(format!("cache/chapter-{}/chunk-0000.wav", chapters[0].id)).is_file());
    assert!(!r.run_once().unwrap(), "paused: no work");

    r.db.set_paused(false).unwrap();
    r.run_once().unwrap();
    assert_eq!(r.db.get_job(jobs[0].id).unwrap().status, JobStatus::Done);
    assert_eq!(logged(dir.path()).len(), 3, "a cached chunk was synthesized again");
}

#[test]
fn a_cancel_mid_chapter_cancels_the_job_and_clears_the_cache() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_DELAY", "0.5")]);
    let (_, chapters, jobs) = book_with_jobs(&r.db, dir.path(), &[&three_paragraphs()]);
    let job_id = jobs[0].id;
    let canceller = when_first_chunk_starts(dir.path(), move |db| db.request_job_cancel(job_id).unwrap());

    assert!(r.run_once().unwrap());
    canceller.join().unwrap();

    assert_eq!(r.db.get_job(job_id).unwrap().status, JobStatus::Cancelled);
    assert_eq!(r.db.get_chapter(chapters[0].id).unwrap().status, ChapterStatus::Pending);
    assert!(!dir.path().join(format!("cache/chapter-{}", chapters[0].id)).exists());
}

#[test]
fn another_engines_cached_audio_is_never_reused() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_DELAY", "0.5")]);
    let (book, chapters, _) = book_with_jobs(&r.db, dir.path(), &[&three_paragraphs()]);
    let pauser = when_first_chunk_starts(dir.path(), |db| db.set_paused(true).unwrap());
    r.run_once().unwrap();
    pauser.join().unwrap();

    r.db.update_book_engine(book.id, "fake-b").unwrap();
    r.db.set_paused(false).unwrap();
    r.run_once().unwrap();

    let log = logged(dir.path());
    assert_eq!(log.len(), 4, "chunk 0 is regenerated by fake-b: {log:?}");
    assert_eq!(log[0], log[1]);
    assert_eq!(r.db.get_chapter(chapters[0].id).unwrap().engine.as_deref(), Some("fake-b"));
}

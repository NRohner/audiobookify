mod common;

use std::fs;
use std::os::unix::fs::PermissionsExt;

use audiobookify_core::app::books::{self, ChapterBreak};
use audiobookify_core::app::list_engines;
use audiobookify_core::audio::{chapter_filename, export_chapter_mp3};
use audiobookify_core::jobs::ChunkCache;
use audiobookify_core::models::{ChapterOutcome, ChapterStatus, JobStatus};
use common::*;
use tempfile::TempDir;

fn import(dir: &std::path::Path, paths: &audiobookify_core::app::AppPaths, db: &audiobookify_core::db::Database)
          -> books::BookDto {
    let src = write_file(dir, "book.md", TWO_CHAPTERS_MD);
    books::create_book(db, paths, &src, &path(dir, "out")).unwrap()
}

/// test_chapter_breaks.py's `_book`: a Markdown book with THREE, created straight in the DB.
fn three(db: &audiobookify_core::db::Database, dir: &std::path::Path) -> i64 {
    let book = md_book(db, dir, &path(dir, "b.md"));
    db.add_chapters(book.id, &THREE.map(|(t, x)| raw(t, x))).unwrap();
    book.id
}

// test_create_book_ingests_uploaded_markdown_and_returns_chapters
#[test]
fn create_book_ingests_markdown_and_returns_chapters() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    assert_eq!(book.title, "book");
    assert_eq!(book.chapters.len(), 2);
    assert_eq!(book.chapters[0].title, "Chapter One");
    assert_eq!(book.chapters[0].status, "pending");
    assert_eq!(book.chapters[0].progress, None);
}

#[test]
fn create_book_stages_a_copy_and_leaves_the_original() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    let source = db.get_book(book.id).unwrap().source_path;
    assert!(std::path::Path::new(&source).starts_with(fs::canonicalize(paths.uploads()).unwrap()), "{source}");
    assert_eq!(fs::read_to_string(&source).unwrap(), TWO_CHAPTERS_MD);
    assert!(dir.path().join("book.md").is_file());
}

// test_create_book_strips_whitespace_from_output_dir
#[test]
fn create_book_strips_whitespace_from_output_dir() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let src = write_file(dir.path(), "book.md", TWO_CHAPTERS_MD);
    let book = books::create_book(&db, &paths, &src, &format!("  {}  ", path(dir.path(), "out"))).unwrap();
    assert_eq!(book.output_dir, fs::canonicalize(dir.path().join("out")).unwrap().to_string_lossy());
}

// test_create_book_rejects_unsupported_format
#[test]
fn create_book_rejects_unsupported_format() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let src = write_file(dir.path(), "book.txt", "hi");
    assert_eq!(api_err(books::create_book(&db, &paths, &src, &path(dir.path(), "out"))),
               "unsupported input format: .txt");
}

// test_create_book_creates_a_new_book_row_for_each_upload_even_with_the_same_filename
#[test]
fn create_book_adds_a_row_for_each_import_of_the_same_file() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let a = import(dir.path(), &paths, &db);
    let b = import(dir.path(), &paths, &db);
    assert_ne!(a.id, b.id);
    assert_eq!(books::list_books(&db).unwrap().len(), 2);
}

// test_create_book_rejects_an_unwritable_output_dir
#[test]
fn create_book_rejects_an_unwritable_output_dir() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let locked = dir.path().join("locked");
    fs::create_dir(&locked).unwrap();
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o500)).unwrap();
    let src = write_file(dir.path(), "book.md", TWO_CHAPTERS_MD);

    let err = api_err(books::create_book(&db, &paths, &src, &path(&locked, "out")));

    assert!(err.starts_with("cannot write to '"), "{err}");
    assert!(err.ends_with("' — check the folder exists and you have permission to write to it"), "{err}");
    assert!(books::list_books(&db).unwrap().is_empty());
}

// test_list_books_returns_all_imported_books, test_get_book_returns_404_for_unknown_id
#[test]
fn list_and_get_books() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    assert_eq!(books::list_books(&db).unwrap().iter().map(|b| b.id).collect::<Vec<_>>(), vec![book.id]);
    assert_eq!(books::get_book(&db, book.id).unwrap().id, book.id);
    assert_eq!(api_err(books::get_book(&db, 999)), "book not found");
}

// test_get_book_surfaces_started_at_for_a_generating_chapter, test_get_book_still_reports_progress_for_a_genuinely_queued_job
#[test]
fn get_book_reports_progress_and_start_of_the_active_job() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    db.create_job(book.chapters[0].id).unwrap();
    let running = db.pop_next_queued_job().unwrap().unwrap();
    db.update_job_progress(running.id, 0.5).unwrap();
    let queued = db.create_job(book.chapters[1].id).unwrap();
    db.update_job_progress(queued.id, 0.0).unwrap();

    let chapters = books::get_book(&db, book.id).unwrap().chapters;

    let first = chapters.iter().find(|c| c.id == running.chapter_id).unwrap();
    assert_eq!(first.progress, Some(0.5));
    assert!(first.started_at.is_some());
    let second = chapters.iter().find(|c| c.id == queued.chapter_id).unwrap();
    assert_eq!((second.progress, second.started_at), (Some(0.0), None));
}

// test_get_book_surfaces_used_fallback_per_chapter,
// test_book_reports_which_engine_made_each_chapter_and_whether_they_are_mixed
#[test]
fn get_book_reports_fallback_engine_and_mixed_engines() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    let (a, b) = (book.chapters[0].id, book.chapters[1].id);
    db.update_chapter_status(a, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some("/a.mp3"), used_fallback: true, engine: Some("qwen3"), duration: Some(1.0),
        ..Default::default()
    }).unwrap();

    let got = books::get_book(&db, book.id).unwrap();
    assert_eq!(got.chapters.iter().map(|c| c.engine.as_deref()).collect::<Vec<_>>(), vec![Some("qwen3"), None]);
    assert_eq!(got.chapters.iter().map(|c| c.used_fallback).collect::<Vec<_>>(), vec![true, false]);
    assert!(!got.mixed_engines);

    db.update_chapter_status(b, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some("/b.mp3"), engine: Some("chatterbox-turbo"), duration: Some(1.0), ..Default::default()
    }).unwrap();
    assert!(books::get_book(&db, book.id).unwrap().mixed_engines);
}

// test_book_reports_duration_only_for_done_chapters,
// test_book_measures_and_stores_duration_of_chapters_finished_before_durations_were_kept
#[test]
fn chapter_duration_is_reported_for_done_chapters_and_measured_once() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    assert_eq!(books::get_book(&db, book.id).unwrap().chapters[0].duration, None);

    let mp3 = dir.path().join("ch1.mp3");
    silent_mp3(&mp3, 2.0);
    let id = book.chapters[0].id;
    db.update_chapter_status(id, ChapterStatus::Done,
        ChapterOutcome { output_mp3_path: Some(&mp3.to_string_lossy()), ..Default::default() }).unwrap();

    let duration = books::get_book(&db, book.id).unwrap().chapters[0].duration.unwrap();
    assert!((duration - 2.0).abs() < 0.1);
    assert_eq!(db.get_chapter(id).unwrap().duration, Some(duration));
}

// test_assign_voice_to_book_persists, test_assign_voice_to_book_returns_404_for_unknown_book
#[test]
fn assign_voice_persists() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    let voice = voice(&db, dir.path(), "Nova", None, None);
    books::assign_voice(&db, book.id, Some(voice.id)).unwrap();
    assert_eq!(db.get_book(book.id).unwrap().voice_profile_id, Some(voice.id));
    books::assign_voice(&db, book.id, None).unwrap();
    assert_eq!(db.get_book(book.id).unwrap().voice_profile_id, None);
    assert_eq!(api_err(books::assign_voice(&db, 999, None)), "book not found");
}

// test_delete_book_removes_book_cache_and_staged_upload_but_keeps_mp3s
#[test]
fn delete_book_removes_rows_cache_and_staged_copy_but_keeps_mp3s() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    let staged = std::path::PathBuf::from(db.get_book(book.id).unwrap().source_path);
    let cache = ChunkCache::new(paths.cache());
    fs::create_dir_all(cache.chapter_dir(book.chapters[0].id)).unwrap();
    fs::create_dir_all(dir.path().join("out")).unwrap();
    let mp3 = write_file(&dir.path().join("out"), "01 - Chapter One.mp3", "ID3");

    books::delete_book(&db, &paths, book.id).unwrap();

    assert_eq!(api_err(books::get_book(&db, book.id)), "book not found");
    assert!(!cache.chapter_dir(book.chapters[0].id).exists());
    assert!(!staged.parent().unwrap().exists());
    assert!(paths.uploads().is_dir());
    assert!(mp3.is_file());
}

// test_delete_book_keeps_a_source_file_outside_the_upload_dir,
// test_delete_book_never_removes_upload_dir_for_a_file_directly_inside_it
#[test]
fn delete_book_only_removes_a_staged_folder_inside_uploads() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let outside = write_file(dir.path(), "mine.md", "# A\nB.\n");
    let a = md_book(&db, dir.path(), &outside.to_string_lossy());
    fs::create_dir_all(paths.uploads()).unwrap();
    let direct = write_file(&paths.uploads(), "direct.md", "# A\nB.\n");
    let b = md_book(&db, dir.path(), &fs::canonicalize(&direct).unwrap().to_string_lossy());

    books::delete_book(&db, &paths, a.id).unwrap();
    books::delete_book(&db, &paths, b.id).unwrap();

    assert!(outside.is_file());
    assert!(direct.is_file());
}

// test_delete_book_returns_404_for_unknown_book, test_delete_book_returns_409_while_a_job_is_queued
#[test]
fn delete_book_refuses_unknown_and_rendering_books() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    db.create_job(book.chapters[1].id).unwrap();
    assert_eq!(api_err(books::delete_book(&db, &paths, 999)), "book not found");
    assert_eq!(api_err(books::delete_book(&db, &paths, book.id)),
               "This book is rendering. Stop rendering it before deleting it.");
}

// test_text_endpoint_returns_paragraphs_and_chapter_starts
#[test]
fn book_text_returns_paragraphs_and_chapter_starts() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let id = three(&db, dir.path());
    let text = books::book_text(&db, id).unwrap();
    assert_eq!(text.paragraphs, vec!["a", "b", "c", "d", "e", "f"]);
    assert_eq!(text.chapters.iter().map(|c| (c.title.as_str(), c.start, c.status)).collect::<Vec<_>>(),
               vec![("One", 0, "pending"), ("Two", 2, "pending"), ("Three", 5, "pending")]);
    assert_eq!(api_err(books::book_text(&db, 999)), "book not found");
}

fn breaks(list: &[(usize, &str)]) -> Vec<ChapterBreak> {
    list.iter().map(|&(start, title)| ChapterBreak { start, title: title.into() }).collect()
}

// test_put_chapters_renumbers_kept_audio_and_deletes_stale_audio
#[test]
fn chapter_breaks_renumber_kept_audio() {
    let dir = TempDir::new().unwrap();
    let (paths, mut db) = app(dir.path());
    let id = three(&db, dir.path());
    let out = dir.path().join("out");
    for ch in db.list_chapters(id).unwrap().into_iter().skip(1) {
        let (mp3, _) = export_chapter_mp3(&vec![0.0; 2205], 22050, &out, ch.order_index, &ch.title, "B", "N", None).unwrap();
        db.update_chapter_status(ch.id, ChapterStatus::Done,
            ChapterOutcome { output_mp3_path: Some(&mp3.to_string_lossy()), ..Default::default() }).unwrap();
    }

    // Split "One" in two: "Two" stays intact but moves to position 2; "Three" too.
    let book = books::save_chapter_breaks(&mut db, &paths, id,
        &breaks(&[(0, "One"), (1, "One b"), (2, "Two"), (5, "Three")])).unwrap();

    let ch = &book.chapters;
    assert_eq!(ch.iter().map(|c| c.title.as_str()).collect::<Vec<_>>(), vec!["One", "One b", "Two", "Three"]);
    assert_eq!(ch[2].status, "done");
    assert_eq!(ch[2].output_mp3_path.as_deref(), Some(&*out.join(chapter_filename(2, "Two")).to_string_lossy()));
    assert_eq!(ch[3].output_mp3_path.as_deref(), Some(&*out.join(chapter_filename(3, "Three")).to_string_lossy()));
    let mut names: Vec<String> = fs::read_dir(&out).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into()).collect();
    names.sort();
    assert_eq!(names, vec![chapter_filename(2, "Two"), chapter_filename(3, "Three")]);
}

// test_put_chapters_deletes_audio_of_removed_chapters
#[test]
fn chapter_breaks_delete_audio_and_cache_of_removed_chapters() {
    let dir = TempDir::new().unwrap();
    let (paths, mut db) = app(dir.path());
    let id = three(&db, dir.path());
    let two = db.list_chapters(id).unwrap().remove(1);
    let stale = write_file(dir.path(), "stale.mp3", "ID3fake");
    db.update_chapter_status(two.id, ChapterStatus::Done,
        ChapterOutcome { output_mp3_path: Some(&stale.to_string_lossy()), ..Default::default() }).unwrap();
    let cache = ChunkCache::new(paths.cache());
    fs::create_dir_all(cache.chapter_dir(two.id)).unwrap();

    books::save_chapter_breaks(&mut db, &paths, id, &breaks(&[(0, "One"), (3, "Two")])).unwrap();

    assert!(!stale.exists());
    assert!(!cache.chapter_dir(two.id).exists());
}

// test_put_chapters_rejects_invalid_breaks_and_unknown_books,
// test_put_chapters_refuses_while_the_book_is_rendering, test_put_chapters_allows_edits_once_jobs_have_finished
#[test]
fn chapter_breaks_refuse_bad_breaks_unknown_books_and_rendering_books() {
    let dir = TempDir::new().unwrap();
    let (paths, mut db) = app(dir.path());
    let id = three(&db, dir.path());
    assert!(!api_err(books::save_chapter_breaks(&mut db, &paths, id, &breaks(&[(2, "X")]))).is_empty());
    assert_eq!(api_err(books::save_chapter_breaks(&mut db, &paths, 999, &breaks(&[(0, "X")]))), "book not found");

    let job = db.create_job(db.list_chapters(id).unwrap()[2].id).unwrap();
    assert_eq!(api_err(books::save_chapter_breaks(&mut db, &paths, id, &breaks(&[(0, "X")]))),
               "This book is rendering. Pause or cancel it before changing chapter breaks.");
    db.finish_job(job.id, JobStatus::Done, None).unwrap();
    assert_eq!(books::save_chapter_breaks(&mut db, &paths, id, &breaks(&[(0, "X")])).unwrap().chapters.len(), 1);
}

// test_validate_output_dir_reports_writable_for_a_creatable_path,
// test_validate_output_dir_does_not_create_intermediate_directories,
// test_validate_output_dir_reports_error_for_a_permission_denied_path
#[test]
fn validate_output_dir_checks_the_nearest_existing_folder_without_creating_anything() {
    let dir = TempDir::new().unwrap();
    let wanted = dir.path().join("a/b/c");
    let ok = books::validate_output_dir(&wanted.to_string_lossy());
    assert!(ok.writable && ok.error.is_none());
    assert!(!dir.path().join("a").exists());

    let locked = dir.path().join("locked");
    fs::create_dir(&locked).unwrap();
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o500)).unwrap();
    let denied = books::validate_output_dir(&locked.join("x").to_string_lossy());
    assert!(!denied.writable);
    assert_eq!(denied.error.unwrap(), format!(
        "cannot write to '{}' — check the folder exists and you have permission", locked.join("x").display()));
}

// test_engines_endpoint_lists_both_engines_with_labels
#[test]
fn engines_lists_both_with_labels() {
    let engines = list_engines();
    assert_eq!(engines.default, "chatterbox-turbo");
    let ids: Vec<_> = engines.engines.iter().map(|e| (e.id, e.label, e.needs_voice)).collect();
    assert_eq!(ids, vec![("qwen3", "Quality (Qwen3)", true), ("chatterbox-turbo", "Speed (Turbo)", false)]);
}

#[test]
fn api_errors_serialize_as_their_message() {
    let e = audiobookify_core::app::ApiError("book not found".into());
    assert_eq!(serde_json::to_string(&e).unwrap(), "\"book not found\"");
}

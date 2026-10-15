mod common;

use audiobookify_core::db::{book_paragraphs, Database};
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

#![allow(dead_code)] // each test binary uses a different subset

use std::fmt::Debug;
use std::path::Path;

use audiobookify_core::db::{Database, DbError};
use audiobookify_core::models::*;

pub fn open(dir: &Path) -> Database {
    Database::open(dir.join("test.db")).unwrap()
}

pub fn path(dir: &Path, name: &str) -> String {
    dir.join(name).to_string_lossy().into_owned()
}

pub fn raw(title: &str, text: &str) -> RawChapter {
    RawChapter { title: title.into(), text: text.into() }
}

pub fn md_book(db: &Database, dir: &Path, source_path: &str) -> Book {
    db.create_book("Test Book", None, source_path, SourceFormat::Markdown, &path(dir, "out")).unwrap()
}

pub fn book_with_chapter(db: &Database, dir: &Path) -> (Book, Chapter) {
    let book = md_book(db, dir, &path(dir, "b.md"));
    let mut chapters = db.add_chapters(book.id, &[raw("Ch 1", "Body.")]).unwrap();
    (book, chapters.remove(0))
}

pub fn not_found<T: Debug>(result: Result<T, DbError>) -> String {
    match result {
        Err(DbError::NotFound(message)) => message,
        other => panic!("expected NotFound, got {other:?}"),
    }
}

pub fn invalid<T: Debug>(result: Result<T, DbError>) -> String {
    match result {
        Err(DbError::Invalid(message)) => message,
        other => panic!("expected Invalid, got {other:?}"),
    }
}

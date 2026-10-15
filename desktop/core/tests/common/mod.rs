#![allow(dead_code)] // each test binary uses a different subset

use std::fmt::Debug;
use std::path::Path;

use audiobookify_core::db::{Database, DbError};
use audiobookify_core::models::*;

pub fn open(dir: &Path) -> Database {
    Database::open(dir.join("test.db"), dir.join("voices")).unwrap()
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

/// A "chatterbox" voice with default params and no transcript, like most test_db.py voices.
pub fn voice_at(db: &Database, name: &str, reference_audio_path: &str) -> VoiceProfile {
    db.create_voice_profile(&NewVoiceProfile {
        name, engine: "chatterbox", reference_audio_path, reference_transcript: None,
        exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8, transcript_status: None,
    })
    .unwrap()
}

/// test_db.py's `_voice` helper.
pub fn voice(db: &Database, dir: &Path, name: &str, transcript: Option<&str>,
             transcript_status: Option<&str>) -> VoiceProfile {
    db.create_voice_profile(&NewVoiceProfile {
        name, engine: "chatterbox-turbo", reference_audio_path: &path(dir, &format!("{name}.wav")),
        reference_transcript: transcript, exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8,
        transcript_status,
    })
    .unwrap()
}

//! Book endpoints: import, list, delete, chapter text and breaks, voice, output folder.

use std::collections::HashSet;
use std::ffi::{CString, OsStr};
use std::fs;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::{err, found, ApiResult, AppPaths};
use crate::audio::{chapter_filename, relabel_chapter_mp3};
use crate::db::{book_paragraphs, Database};
use crate::ingest::ingest_by_path;
use crate::jobs::ChunkCache;
use crate::media;
use crate::models::{Book, Chapter, ChapterStatus, Job, JobStatus};

#[derive(Debug, Serialize)]
pub struct ChapterDto {
    pub id: i64,
    pub order_index: i64,
    pub title: String,
    pub status: &'static str,
    pub output_mp3_path: Option<String>,
    pub error: Option<String>,
    pub progress: Option<f64>,
    pub started_at: Option<f64>,
    pub used_fallback: bool,
    pub engine: Option<String>,
    pub duration: Option<f64>,
}

#[derive(Debug, Serialize)]
pub struct BookDto {
    pub id: i64,
    pub title: String,
    pub author: Option<String>,
    pub output_dir: String,
    pub voice_profile_id: Option<i64>,
    pub engine: String,
    pub mixed_engines: bool,
    pub chapters: Vec<ChapterDto>,
}

/// The chapter's latest job, if it is queued or generating.
pub(crate) fn active_job(db: &Database, chapter_id: i64) -> ApiResult<Option<Job>> {
    Ok(db.latest_job_for_chapter(chapter_id)?
        .filter(|j| matches!(j.status, JobStatus::Queued | JobStatus::Generating)))
}

pub(crate) fn rendering(db: &Database, book_id: i64) -> ApiResult<bool> {
    for chapter in db.list_chapters(book_id)? {
        if active_job(db, chapter.id)?.is_some() {
            return Ok(true);
        }
    }
    Ok(false)
}

fn chapter_duration(db: &Database, chapter: &Chapter) -> Option<f64> {
    if chapter.status != ChapterStatus::Done {
        return None;
    }
    if chapter.duration.is_none() {
        // Chapters finished before durations were stored: measure once, keep it.
        let duration = media::duration(Path::new(chapter.output_mp3_path.as_deref()?)).ok()?;
        db.set_chapter_duration(chapter.id, duration).ok()?;
        return Some(duration);
    }
    chapter.duration
}

pub fn book_dto(db: &Database, book: &Book) -> ApiResult<BookDto> {
    let mut chapters = Vec::new();
    for c in db.list_chapters(book.id)? {
        let job = active_job(db, c.id)?;
        let duration = chapter_duration(db, &c);
        chapters.push(ChapterDto {
            id: c.id,
            order_index: c.order_index,
            status: c.status.as_str(),
            progress: job.as_ref().and_then(|j| j.progress),
            started_at: job.as_ref().and_then(|j| j.started_at),
            used_fallback: c.used_fallback,
            duration,
            title: c.title,
            output_mp3_path: c.output_mp3_path,
            error: c.error,
            engine: c.engine,
        });
    }
    let done_engines: HashSet<&Option<String>> =
        chapters.iter().filter(|c| c.status == ChapterStatus::Done.as_str()).map(|c| &c.engine).collect();
    Ok(BookDto {
        id: book.id,
        title: book.title.clone(),
        author: book.author.clone(),
        output_dir: book.output_dir.clone(),
        voice_profile_id: book.voice_profile_id,
        engine: book.engine.clone(),
        mixed_engines: done_engines.len() > 1,
        chapters,
    })
}

pub fn list_books(db: &Database) -> ApiResult<Vec<BookDto>> {
    db.list_books()?.iter().map(|b| book_dto(db, b)).collect()
}

pub fn get_book(db: &Database, book_id: i64) -> ApiResult<BookDto> {
    book_dto(db, &found(db.get_book(book_id), "book")?)
}

fn check_writable(path: &Path) -> bool {
    let probe = path.join(".audiobookify_write_test");
    fs::create_dir_all(path).is_ok() && fs::write(&probe, b"").is_ok() && fs::remove_file(&probe).is_ok()
}

/// Imports an EPUB or Markdown file. The file is copied to `uploads/<uuid>/` first, so
/// moving or deleting the original can't break the book.
pub fn create_book(db: &Database, paths: &AppPaths, source: &Path, output_dir: &str) -> ApiResult<BookDto> {
    let Some(file_name) = source.file_name() else { return err("no file chosen") };
    let suffix = source.extension().map(|e| format!(".{}", e.to_string_lossy().to_lowercase())).unwrap_or_default();
    if suffix != ".epub" && suffix != ".md" {
        return err(format!("unsupported input format: {suffix}"));
    }
    let output = std::path::absolute(output_dir.trim())?;
    if !check_writable(&output) {
        return err(format!(
            "cannot write to '{}' — check the folder exists and you have permission to write to it",
            output.display()
        ));
    }
    let output = fs::canonicalize(&output)?;

    let staged_dir = paths.uploads().join(uuid::Uuid::new_v4().simple().to_string());
    fs::create_dir_all(&staged_dir)?;
    let staged = staged_dir.join(file_name);
    fs::copy(source, &staged)?;
    let staged = fs::canonicalize(&staged)?;
    let (parsed, format) = match ingest_by_path(&staged, 1) {
        Ok(parsed) => parsed,
        Err(e) => {
            let _ = fs::remove_dir_all(&staged_dir);
            return err(e.to_string());
        }
    };
    let book = db.create_book(&parsed.title, parsed.author.as_deref(), &staged.to_string_lossy(), format,
                              &output.to_string_lossy())?;
    db.add_chapters(book.id, &parsed.chapters)?;
    book_dto(db, &book)
}

pub fn delete_book(db: &Database, paths: &AppPaths, book_id: i64) -> ApiResult<()> {
    let book = found(db.get_book(book_id), "book")?;
    if rendering(db, book_id)? {
        return err("This book is rendering. Stop rendering it before deleting it.");
    }
    let cache = ChunkCache::new(paths.cache());
    for chapter in db.delete_book(book_id)? {
        cache.clear(chapter.id);
    }
    // Rendered MP3s stay. Only remove the copy we staged, never anything outside uploads.
    let root = fs::canonicalize(paths.uploads());
    let staged = Path::new(&book.source_path).parent().map(fs::canonicalize);
    if let (Ok(root), Some(Ok(staged))) = (root, staged) {
        if staged != root && staged.starts_with(&root) {
            let _ = fs::remove_dir_all(staged);
        }
    }
    Ok(())
}

#[derive(Debug, Serialize)]
pub struct ChapterStart {
    pub id: i64,
    pub title: String,
    pub start: usize,
    pub status: &'static str,
}

#[derive(Debug, Serialize)]
pub struct BookText {
    pub paragraphs: Vec<String>,
    pub chapters: Vec<ChapterStart>,
}

pub fn book_text(db: &Database, book_id: i64) -> ApiResult<BookText> {
    found(db.get_book(book_id), "book")?;
    let chapters = db.list_chapters(book_id)?;
    let (paragraphs, starts) = book_paragraphs(&chapters);
    Ok(BookText {
        paragraphs: paragraphs.into_iter().map(String::from).collect(),
        chapters: chapters.iter().zip(starts)
            .map(|(c, start)| ChapterStart { id: c.id, title: c.title.clone(), start, status: c.status.as_str() })
            .collect(),
    })
}

#[derive(Debug, Clone, Deserialize)]
pub struct ChapterBreak {
    pub start: usize,
    pub title: String,
}

pub fn save_chapter_breaks(db: &mut Database, paths: &AppPaths, book_id: i64, breaks: &[ChapterBreak])
                           -> ApiResult<BookDto> {
    let book = found(db.get_book(book_id), "book")?;
    if rendering(db, book_id)? {
        return err("This book is rendering. Pause or cancel it before changing chapter breaks.");
    }
    let pairs: Vec<(usize, &str)> = breaks.iter().map(|b| (b.start, b.title.as_str())).collect();
    let removed = db.replace_chapter_breaks(book_id, &pairs)?;

    let cache = ChunkCache::new(paths.cache());
    for chapter in &removed {
        // their audio no longer matches any chapter's text
        if let Some(mp3) = &chapter.output_mp3_path {
            let _ = fs::remove_file(mp3);
        }
        cache.clear(chapter.id);
    }

    // Kept chapters may have moved or been renamed. Park the files first so a chapter
    // taking a neighbour's old filename can't overwrite it, then give them their new names.
    let stale: Vec<(Chapter, PathBuf)> = db.list_chapters(book_id)?.into_iter()
        .filter_map(|c| {
            let mp3 = PathBuf::from(c.output_mp3_path.as_deref()?);
            let wanted = chapter_filename(c.order_index, &c.title);
            (mp3.is_file() && mp3.file_name()? != OsStr::new(&wanted)).then_some((c, mp3))
        })
        .collect();
    let mut parked = Vec::new();
    for (chapter, mp3) in stale {
        let tmp = mp3.with_extension(format!("relabel-{}.mp3", chapter.id));
        fs::rename(&mp3, &tmp)?;
        parked.push((chapter, tmp));
    }
    for (chapter, tmp) in parked {
        let path = relabel_chapter_mp3(&tmp, chapter.order_index, &chapter.title).unwrap_or_else(|_| {
            // Keep the audio playable wherever it ended up rather than lose track of it.
            if tmp.is_file() { tmp.clone() } else { tmp.with_file_name(chapter_filename(chapter.order_index, &chapter.title)) }
        });
        db.set_chapter_audio_path(chapter.id, &path.to_string_lossy())?;
    }
    book_dto(db, &book)
}

pub fn assign_voice(db: &Database, book_id: i64, voice_profile_id: Option<i64>) -> ApiResult<()> {
    found(db.get_book(book_id), "book")?;
    db.update_book_voice_profile(book_id, voice_profile_id)?;
    Ok(())
}

#[derive(Debug, Serialize)]
pub struct DirCheck {
    pub path: String,
    pub writable: bool,
    pub error: Option<String>,
}

fn can_write_into(dir: &Path) -> bool {
    let Ok(c) = CString::new(dir.as_os_str().as_bytes()) else { return false };
    // SAFETY: `c` is a valid NUL-terminated path for the duration of the call.
    unsafe { libc::access(c.as_ptr(), libc::W_OK | libc::X_OK) == 0 }
}

/// Whether the app could create and write `path`, judged from its nearest existing folder.
/// Creates nothing.
pub fn validate_output_dir(path: &str) -> DirCheck {
    let resolved = std::path::absolute(path.trim()).unwrap_or_else(|_| PathBuf::from(path));
    let existing = resolved.ancestors().find(|p| p.exists()).unwrap_or(Path::new("/"));
    let writable = existing.is_dir() && can_write_into(existing);
    let shown = resolved.display().to_string();
    DirCheck {
        error: (!writable).then(|| format!("cannot write to '{shown}' — check the folder exists and you have permission")),
        path: shown,
        writable,
    }
}

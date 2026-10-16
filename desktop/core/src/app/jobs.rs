//! Generation endpoints: queue, cancel, pause, and the finished chapter's file.

use std::path::{Path, PathBuf};

use serde::Serialize;

use super::books::active_job;
use super::{chosen_engine, err, found, ApiResult, AppPaths};
use crate::db::Database;
use crate::jobs::ChunkCache;
use crate::models::{Chapter, ChapterOutcome, ChapterStatus, JobStatus};

#[derive(Debug, Serialize)]
pub struct Queued {
    pub queued_job_ids: Vec<i64>,
}

#[derive(Debug, Serialize)]
pub struct QueuedOne {
    pub job_id: Option<i64>,
}

#[derive(Debug, Serialize)]
pub struct WorkerStatus {
    pub paused: bool,
}

/// The chapter, if it exists and belongs to `book_id`.
fn chapter_of(db: &Database, book_id: i64, chapter_id: i64) -> ApiResult<Chapter> {
    let chapter = found(db.get_chapter(chapter_id), "chapter")?;
    if chapter.book_id != book_id {
        return err("chapter not found");
    }
    Ok(chapter)
}

pub fn generate_book(db: &Database, book_id: i64, engine: Option<&str>) -> ApiResult<Queued> {
    let book = found(db.get_book(book_id), "book")?;
    db.update_book_engine(book.id, &chosen_engine(engine, &book.engine, book.voice_profile_id)?)?;
    let mut queued_job_ids = Vec::new();
    for chapter in db.list_chapters(book.id)? {
        if chapter.status == ChapterStatus::Done || active_job(db, chapter.id)?.is_some() {
            continue;
        }
        queued_job_ids.push(db.create_job(chapter.id)?.id);
    }
    Ok(Queued { queued_job_ids })
}

pub fn generate_chapter(db: &Database, book_id: i64, chapter_id: i64, engine: Option<&str>) -> ApiResult<QueuedOne> {
    let book = found(db.get_book(book_id), "book")?;
    let chapter = chapter_of(db, book_id, chapter_id)?;
    let engine = chosen_engine(engine, &book.engine, book.voice_profile_id)?;
    if chapter.status == ChapterStatus::Done || active_job(db, chapter.id)?.is_some() {
        return Ok(QueuedOne { job_id: None });
    }
    db.update_book_engine(book.id, &engine)?; // the book's chapters all use one engine
    Ok(QueuedOne { job_id: Some(db.create_job(chapter.id)?.id) })
}

/// A queued job is cancelled here; a generating one is asked to stop, and the runner
/// cancels it after its current chunk.
fn cancel_active(db: &Database, cache: &ChunkCache, chapter: &Chapter) -> ApiResult<()> {
    match active_job(db, chapter.id)? {
        Some(job) if job.status == JobStatus::Queued => {
            // The runner may have claimed it since we read it; then ask it to stop instead.
            if db.cancel_queued_job(job.id)? {
                db.update_chapter_status(chapter.id, ChapterStatus::Pending, ChapterOutcome::default())?;
                cache.clear(chapter.id);
            } else {
                db.request_job_cancel(job.id)?;
            }
        }
        Some(job) => db.request_job_cancel(job.id)?,
        None => {}
    }
    Ok(())
}

pub fn cancel_chapter(db: &Database, paths: &AppPaths, book_id: i64, chapter_id: i64) -> ApiResult<()> {
    found(db.get_book(book_id), "book")?;
    let chapter = chapter_of(db, book_id, chapter_id)?;
    cancel_active(db, &ChunkCache::new(paths.cache()), &chapter)
}

pub fn cancel_book(db: &Database, paths: &AppPaths, book_id: i64) -> ApiResult<()> {
    found(db.get_book(book_id), "book")?;
    let cache = ChunkCache::new(paths.cache());
    for chapter in db.list_chapters(book_id)? {
        cancel_active(db, &cache, &chapter)?;
    }
    Ok(())
}

pub fn worker_status(db: &Database) -> ApiResult<WorkerStatus> {
    Ok(WorkerStatus { paused: db.is_paused()? })
}

pub fn set_paused(db: &Database, paused: bool) -> ApiResult<WorkerStatus> {
    db.set_paused(paused)?;
    Ok(WorkerStatus { paused })
}

pub fn chapter_audio_path(db: &Database, book_id: i64, chapter_id: i64) -> ApiResult<PathBuf> {
    let chapter = chapter_of(db, book_id, chapter_id)?;
    match chapter.output_mp3_path {
        Some(mp3) if chapter.status == ChapterStatus::Done && Path::new(&mp3).is_file() => Ok(mp3.into()),
        _ => err("audio not ready"),
    }
}

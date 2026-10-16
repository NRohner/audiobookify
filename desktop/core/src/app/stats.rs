//! The Settings page's listening stats, from the existing tables.

use std::fs;
use std::path::Path;

use serde::Serialize;

use super::ApiResult;
use crate::db::Database;
use crate::media;

#[derive(Debug, Serialize, PartialEq)]
pub struct EngineStats {
    pub engine: String,
    pub audio_seconds: f64,
    pub generating_seconds: f64,
}

#[derive(Debug, Serialize, PartialEq)]
pub struct Named {
    pub name: String,
    pub seconds: f64,
}

#[derive(Debug, Serialize, PartialEq)]
pub struct BusyDay {
    pub day: String,
    pub chapters: i64,
}

#[derive(Debug, Serialize, PartialEq)]
pub struct Stats {
    pub audio_seconds: f64,
    pub words: u64,
    pub books_finished: i64,
    pub chapters_done: i64,
    pub engines: Vec<EngineStats>,
    pub longest_book: Option<Named>,
    pub top_voice: Option<Named>,
    pub busiest_day: Option<BusyDay>,
    pub disk_bytes: u64,
}

/// Older chapters (and every text clip before this phase) have no stored duration: read it
/// from the file once and keep it. A file that's gone stays NULL and is skipped.
fn backfill(db: &Database) -> ApiResult<()> {
    for (id, file) in db.chapters_missing_duration()? {
        if let Ok(seconds) = media::duration(Path::new(&file)) {
            db.set_chapter_duration(id, seconds)?;
        }
    }
    for (id, file) in db.text_clips_missing_duration()? {
        if let Ok(seconds) = media::duration(Path::new(&file)) {
            db.set_text_clip_duration(id, seconds)?;
        }
    }
    Ok(())
}

pub fn stats(db: &Database) -> ApiResult<Stats> {
    backfill(db)?;
    let (audio_seconds, chapters_done, books_finished) = db.stat_numbers()?;
    let mut words = 0u64;
    let mut disk_bytes = 0u64;
    for (text, file) in db.done_chapter_texts_and_files()? {
        words += text.split_whitespace().count() as u64;
        disk_bytes += file.and_then(|f| fs::metadata(f).ok()).map_or(0, |m| m.len());
    }
    Ok(Stats {
        audio_seconds,
        words,
        books_finished,
        chapters_done,
        engines: db.engine_stats()?.into_iter()
            .map(|(engine, audio_seconds, generating_seconds)| EngineStats { engine, audio_seconds, generating_seconds })
            .collect(),
        longest_book: db.longest_book()?.map(|(name, seconds)| Named { name, seconds }),
        top_voice: db.top_voice()?.map(|(name, seconds)| Named { name, seconds }),
        busiest_day: db.busiest_day()?.map(|(day, chapters)| BusyDay { day, chapters }),
        disk_bytes,
    })
}

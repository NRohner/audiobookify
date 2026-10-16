//! Read-only queries for the Settings page.

use std::collections::BTreeSet;

use rusqlite::params;

use super::{Database, Result};
use crate::models::JobStatus;

/// Work that is queued or running, and so needs its models on disk.
#[derive(Debug, Default)]
pub struct ActiveWork {
    pub engines: BTreeSet<String>,
    pub transcriptions: bool,
}

impl ActiveWork {
    pub fn any(&self) -> bool {
        !self.engines.is_empty() || self.transcriptions
    }
}

impl Database {
    pub fn active_work(&self) -> Result<ActiveWork> {
        let (q, g) = (JobStatus::Queued, JobStatus::Generating);
        let engines = self.query_all(
            "SELECT b.engine FROM jobs j JOIN chapters c ON c.id = j.chapter_id JOIN books b ON b.id = c.book_id \
             WHERE j.status IN (?1, ?2) \
             UNION SELECT engine FROM text_clips WHERE status IN (?1, ?2)",
            params![q, g], |r| r.get::<_, String>(0))?;
        let transcriptions = self.query_opt(
            "SELECT 1 FROM voice_profiles WHERE transcript_status = 'pending' LIMIT 1", [], |r| r.get::<_, i64>(0))?
            .is_some();
        Ok(ActiveWork { engines: engines.into_iter().collect(), transcriptions })
    }
}

/// Rows whose duration is missing but whose file may still be there: (id, path).
pub type MissingDuration = (i64, String);

impl Database {
    pub fn chapters_missing_duration(&self) -> Result<Vec<MissingDuration>> {
        self.query_all("SELECT id, output_mp3_path FROM chapters \
                        WHERE status = 'done' AND duration IS NULL AND output_mp3_path IS NOT NULL",
                       [], |r| Ok((r.get(0)?, r.get(1)?)))
    }

    pub fn text_clips_missing_duration(&self) -> Result<Vec<MissingDuration>> {
        self.query_all("SELECT id, output_path FROM text_clips \
                        WHERE status = 'done' AND duration IS NULL AND output_path IS NOT NULL",
                       [], |r| Ok((r.get(0)?, r.get(1)?)))
    }

    pub fn done_chapter_texts_and_files(&self) -> Result<Vec<(String, Option<String>)>> {
        self.query_all("SELECT text, output_mp3_path FROM chapters WHERE status = 'done'", [],
                       |r| Ok((r.get(0)?, r.get(1)?)))
    }

    /// One query per figure; the tables are small (one row per chapter).
    pub fn stat_numbers(&self) -> Result<(f64, i64, i64)> {
        let audio: f64 = self.query_opt(
            "SELECT COALESCE((SELECT SUM(duration) FROM chapters WHERE status = 'done'), 0) \
                  + COALESCE((SELECT SUM(duration) FROM text_clips WHERE status = 'done'), 0)",
            [], |r| r.get(0))?.unwrap_or(0.0);
        let chapters: i64 = self.query_opt("SELECT COUNT(*) FROM chapters WHERE status = 'done'", [], |r| r.get(0))?
            .unwrap_or(0);
        let books: i64 = self.query_opt(
            "SELECT COUNT(*) FROM books b WHERE EXISTS (SELECT 1 FROM chapters c WHERE c.book_id = b.id) \
             AND NOT EXISTS (SELECT 1 FROM chapters c WHERE c.book_id = b.id AND c.status != 'done')",
            [], |r| r.get(0))?.unwrap_or(0);
        Ok((audio, chapters, books))
    }

    /// (engine, audio seconds, generating seconds). A chapter counts its latest done job, so
    /// re-renders aren't double-counted.
    // ponytail: a paused-and-resumed job's started_at is its last start, so its time is undercounted.
    pub fn engine_stats(&self) -> Result<Vec<(String, f64, f64)>> {
        self.query_all(
            "SELECT engine, SUM(audio), SUM(spent) FROM ( \
               SELECT c.engine AS engine, c.duration AS audio, j.finished_at - j.started_at AS spent \
               FROM chapters c JOIN jobs j ON j.id = (SELECT MAX(id) FROM jobs WHERE chapter_id = c.id AND status = 'done') \
               WHERE c.status = 'done' AND c.engine IS NOT NULL AND c.duration IS NOT NULL AND j.started_at IS NOT NULL \
               UNION ALL \
               SELECT engine, duration, finished_at - started_at FROM text_clips \
               WHERE status = 'done' AND duration IS NOT NULL AND started_at IS NOT NULL \
             ) GROUP BY engine ORDER BY engine",
            [], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
    }

    pub fn longest_book(&self) -> Result<Option<(String, f64)>> {
        self.query_opt(
            "SELECT b.title, SUM(c.duration) AS s FROM chapters c JOIN books b ON b.id = c.book_id \
             WHERE c.status = 'done' AND c.duration IS NOT NULL GROUP BY b.id ORDER BY s DESC LIMIT 1",
            [], |r| Ok((r.get(0)?, r.get(1)?)))
    }

    /// The voice with the most narrated time, by each book's current voice.
    pub fn top_voice(&self) -> Result<Option<(String, f64)>> {
        self.query_opt(
            "SELECT v.name, SUM(c.duration) AS s FROM chapters c JOIN books b ON b.id = c.book_id \
             JOIN voice_profiles v ON v.id = b.voice_profile_id \
             WHERE c.status = 'done' AND c.duration IS NOT NULL GROUP BY v.id ORDER BY s DESC LIMIT 1",
            [], |r| Ok((r.get(0)?, r.get(1)?)))
    }

    pub fn busiest_day(&self) -> Result<Option<(String, i64)>> {
        self.query_opt(
            "SELECT date(finished_at, 'unixepoch', 'localtime') AS d, COUNT(*) AS n FROM jobs \
             WHERE status = 'done' AND finished_at IS NOT NULL GROUP BY d ORDER BY n DESC, d DESC LIMIT 1",
            [], |r| Ok((r.get(0)?, r.get(1)?)))
    }
}

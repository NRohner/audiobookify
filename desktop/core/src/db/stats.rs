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

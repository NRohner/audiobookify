use rusqlite::{params, Row};

use super::{now, Database, DbError, Result};
use crate::models::{Job, JobStatus, TextClip};

impl Database {
    pub fn create_job(&self, chapter_id: i64) -> Result<Job> {
        self.conn.execute(
            "INSERT INTO jobs (chapter_id, status, progress, created_at) VALUES (?1, ?2, ?3, ?4)",
            params![chapter_id, JobStatus::Queued, 0.0, now()],
        )?;
        self.get_job(self.conn.last_insert_rowid())
    }

    pub fn get_job(&self, job_id: i64) -> Result<Job> {
        self.query_opt("SELECT * FROM jobs WHERE id = ?1", [job_id], job_from_row)?
            .ok_or_else(|| missing_job(job_id))
    }

    pub fn list_jobs_for_chapter(&self, chapter_id: i64) -> Result<Vec<Job>> {
        self.query_all("SELECT * FROM jobs WHERE chapter_id = ?1 ORDER BY created_at DESC, id DESC",
                       [chapter_id], job_from_row)
    }

    pub fn latest_job_for_chapter(&self, chapter_id: i64) -> Result<Option<Job>> {
        self.query_opt("SELECT * FROM jobs WHERE chapter_id = ?1 ORDER BY created_at DESC, id DESC LIMIT 1",
                       [chapter_id], job_from_row)
    }

    pub fn pop_next_queued_job(&self) -> Result<Option<Job>> {
        let Some(job_id) = self.query_opt(
            "SELECT id FROM jobs WHERE status = ?1 ORDER BY created_at, id LIMIT 1",
            [JobStatus::Queued], |r| r.get::<_, i64>(0),
        )? else {
            return Ok(None);
        };
        let claimed = self.conn.execute(
            "UPDATE jobs SET status = ?1, started_at = ?2 WHERE id = ?3 AND status = ?4",
            params![JobStatus::Generating, now(), job_id, JobStatus::Queued],
        )?;
        if claimed == 0 {
            return Ok(None); // claimed by another worker between the SELECT and UPDATE
        }
        self.get_job(job_id).map(Some)
    }

    pub fn update_job_progress(&self, job_id: i64, progress: f64) -> Result<()> {
        self.conn.execute("UPDATE jobs SET progress = ?1 WHERE id = ?2", params![progress, job_id])?;
        Ok(())
    }

    pub fn finish_job(&self, job_id: i64, status: JobStatus, error: Option<&str>) -> Result<()> {
        let sql = if status == JobStatus::Done {
            "UPDATE jobs SET status = ?1, progress = 1.0, finished_at = ?2, error = ?3 WHERE id = ?4"
        } else {
            "UPDATE jobs SET status = ?1, finished_at = ?2, error = ?3 WHERE id = ?4"
        };
        self.conn.execute(sql, params![status, now(), error, job_id])?;
        Ok(())
    }

    pub fn reset_stale_generating_jobs(&self) -> Result<()> {
        self.conn.execute("UPDATE jobs SET status = ?1, started_at = NULL WHERE status = ?2",
                          params![JobStatus::Queued, JobStatus::Generating])?;
        Ok(())
    }

    pub fn request_job_cancel(&self, job_id: i64) -> Result<()> {
        self.conn.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?1", [job_id])?;
        Ok(())
    }

    pub fn is_job_cancel_requested(&self, job_id: i64) -> Result<bool> {
        self.query_opt("SELECT cancel_requested FROM jobs WHERE id = ?1", [job_id], |r| r.get::<_, bool>(0))?
            .ok_or_else(|| missing_job(job_id))
    }

    pub fn requeue_job(&self, job_id: i64) -> Result<()> {
        self.conn.execute("UPDATE jobs SET status = ?1, started_at = NULL WHERE id = ?2",
                          params![JobStatus::Queued, job_id])?;
        Ok(())
    }

    pub fn create_text_clip(&self, text: &str, voice_profile_id: Option<i64>, engine: &str,
                            tempo: Option<f64>) -> Result<TextClip> {
        self.conn.execute(
            "INSERT INTO text_clips (text, voice_profile_id, status, progress, created_at, engine, tempo) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![text, voice_profile_id, JobStatus::Queued, 0.0, now(), engine, tempo],
        )?;
        self.get_text_clip(self.conn.last_insert_rowid())
    }

    pub fn get_text_clip(&self, clip_id: i64) -> Result<TextClip> {
        self.query_opt("SELECT * FROM text_clips WHERE id = ?1", [clip_id], clip_from_row)?
            .ok_or_else(|| missing_clip(clip_id))
    }

    pub fn list_text_clips(&self) -> Result<Vec<TextClip>> {
        self.query_all("SELECT * FROM text_clips ORDER BY id DESC", [], clip_from_row)
    }

    pub fn delete_text_clip(&self, clip_id: i64) -> Result<()> {
        if self.conn.execute("DELETE FROM text_clips WHERE id = ?1", [clip_id])? == 0 {
            return Err(missing_clip(clip_id));
        }
        Ok(())
    }

    pub fn pop_next_queued_text_clip(&self) -> Result<Option<TextClip>> {
        let Some(clip_id) = self.query_opt(
            "SELECT id FROM text_clips WHERE status = ?1 ORDER BY created_at, id LIMIT 1",
            [JobStatus::Queued], |r| r.get::<_, i64>(0),
        )? else {
            return Ok(None);
        };
        let claimed = self.conn.execute(
            "UPDATE text_clips SET status = ?1, started_at = ?2 WHERE id = ?3 AND status = ?4",
            params![JobStatus::Generating, now(), clip_id, JobStatus::Queued],
        )?;
        if claimed == 0 {
            return Ok(None); // claimed by another worker between the SELECT and UPDATE
        }
        self.get_text_clip(clip_id).map(Some)
    }

    pub fn update_text_clip_progress(&self, clip_id: i64, progress: f64) -> Result<()> {
        self.conn.execute("UPDATE text_clips SET progress = ?1 WHERE id = ?2", params![progress, clip_id])?;
        Ok(())
    }

    pub fn finish_text_clip(&self, clip_id: i64, status: JobStatus, output_path: Option<&str>,
                            error: Option<&str>, used_fallback: bool) -> Result<()> {
        if status == JobStatus::Done {
            self.conn.execute(
                "UPDATE text_clips SET status = ?1, progress = 1.0, output_path = ?2, \
                 finished_at = ?3, error = ?4, used_fallback = ?5 WHERE id = ?6",
                params![status, output_path, now(), error, used_fallback, clip_id],
            )?;
        } else {
            self.conn.execute(
                "UPDATE text_clips SET status = ?1, finished_at = ?2, error = ?3 WHERE id = ?4",
                params![status, now(), error, clip_id],
            )?;
        }
        Ok(())
    }

    pub fn reset_stale_generating_text_clips(&self) -> Result<()> {
        self.conn.execute("UPDATE text_clips SET status = ?1, started_at = NULL WHERE status = ?2",
                          params![JobStatus::Queued, JobStatus::Generating])?;
        Ok(())
    }

    pub fn is_paused(&self) -> Result<bool> {
        Ok(self.query_opt("SELECT paused FROM worker_state WHERE id = 1", [], |r| r.get::<_, bool>(0))?
            .unwrap_or(false))
    }

    pub fn set_paused(&self, paused: bool) -> Result<()> {
        self.conn.execute(
            "INSERT INTO worker_state (id, paused) VALUES (1, ?1) \
             ON CONFLICT(id) DO UPDATE SET paused = excluded.paused",
            [paused],
        )?;
        Ok(())
    }
}

fn missing_job(job_id: i64) -> DbError {
    DbError::NotFound(format!("no job with id {job_id}"))
}

fn missing_clip(clip_id: i64) -> DbError {
    DbError::NotFound(format!("no text clip with id {clip_id}"))
}

fn job_from_row(r: &Row<'_>) -> rusqlite::Result<Job> {
    Ok(Job {
        id: r.get("id")?, chapter_id: r.get("chapter_id")?, status: r.get("status")?,
        progress: r.get("progress")?, created_at: r.get("created_at")?,
        started_at: r.get("started_at")?, finished_at: r.get("finished_at")?, error: r.get("error")?,
    })
}

fn clip_from_row(r: &Row<'_>) -> rusqlite::Result<TextClip> {
    Ok(TextClip {
        id: r.get("id")?, text: r.get("text")?, voice_profile_id: r.get("voice_profile_id")?,
        status: r.get("status")?, progress: r.get("progress")?, output_path: r.get("output_path")?,
        created_at: r.get("created_at")?, started_at: r.get("started_at")?,
        finished_at: r.get("finished_at")?, error: r.get("error")?,
        used_fallback: r.get("used_fallback")?, engine: r.get("engine")?, tempo: r.get("tempo")?,
    })
}

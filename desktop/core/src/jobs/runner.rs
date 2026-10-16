//! The generation loop, ported from worker.py and synth/pipeline.py. Pause and
//! cancel are checked between chunks, as before.

use std::io;
use std::path::{Path, PathBuf};

use serde_json::json;

use super::cache::{compute_cache_key, ChunkCache};
use super::voice::voice_for;
use crate::audio::{concatenate_chunks, export_chapter_mp3, read_wav, AudioError};
use crate::db::{Database, DbError};
use crate::engine::{EngineError, EngineInfo, PythonEngine};
use crate::models::{ChapterOutcome, ChapterStatus, Job, JobStatus, VoiceProfile};
use crate::text::{apply_lexicon, chunk_text, EndsWith};

pub struct Paths {
    /// Chunks of unfinished chapters (`.cache/synth`).
    pub cache_root: PathBuf,
    /// Finished text clips (`text_clips`).
    pub text_clip_dir: PathBuf,
    /// Where MP3s go when the chosen output dir can't be written (`output`).
    pub backup_dir: PathBuf,
}

#[derive(Debug, thiserror::Error)]
pub enum JobError {
    #[error(transparent)]
    Db(#[from] DbError),
    #[error(transparent)]
    Engine(#[from] EngineError),
    #[error(transparent)]
    Audio(#[from] AudioError),
    #[error(transparent)]
    Io(#[from] io::Error),
}

enum Stopped {
    Paused,
    Cancelled,
}

pub struct Runner {
    pub db: Database,
    engine: PythonEngine,
    cache: ChunkCache,
    paths: Paths,
}

impl Runner {
    pub fn new(db: Database, engine: PythonEngine, paths: Paths) -> Self {
        Self { db, engine, cache: ChunkCache::new(&paths.cache_root), paths }
    }

    /// One unit of work, if any; false when paused or idle.
    pub fn run_once(&mut self) -> Result<bool, JobError> {
        if self.db.is_paused()? {
            return Ok(false);
        }
        self.process_next_chapter_job()
    }

    fn process_next_chapter_job(&mut self) -> Result<bool, JobError> {
        let Some(job) = self.db.pop_next_queued_job()? else { return Ok(false) };
        match self.run_chapter_job(&job) {
            Ok(None) => self.db.finish_job(job.id, JobStatus::Done, None)?,
            Ok(Some(Stopped::Paused)) => {
                self.db.update_chapter_status(job.chapter_id, ChapterStatus::Pending, ChapterOutcome::default())?;
                self.db.requeue_job(job.id)?;
            }
            Ok(Some(Stopped::Cancelled)) => {
                self.db.update_chapter_status(job.chapter_id, ChapterStatus::Pending, ChapterOutcome::default())?;
                self.db.finish_job(job.id, JobStatus::Cancelled, None)?;
                self.cache.clear(job.chapter_id);
            }
            Err(e) => {
                let message = e.to_string();
                // Ignored: the chapter may be the thing that's missing.
                let _ = self.db.update_chapter_status(
                    job.chapter_id, ChapterStatus::Failed, ChapterOutcome { error: Some(&message), ..Default::default() });
                self.db.finish_job(job.id, JobStatus::Failed, Some(&message))?;
            }
        }
        Ok(true)
    }

    fn run_chapter_job(&mut self, job: &Job) -> Result<Option<Stopped>, JobError> {
        let chapter = self.db.get_chapter(job.chapter_id)?;
        let book = self.db.get_book(chapter.book_id)?;
        let (info, profile) = self.engine_and_voice(&book.engine, book.voice_profile_id)?;
        let voice = voice_for(profile.as_ref(), None);
        let narrator = profile.as_ref().map_or("default", |p| p.name.as_str()).to_string();
        let entries = self.db.list_lexicon_entries(Some(book.id))?;
        self.db.update_chapter_status(chapter.id, ChapterStatus::Generating, ChapterOutcome::default())?;

        let text = apply_lexicon(&chapter.text, entries.iter().map(|e| (e.term.as_str(), e.override_value.as_str())));
        let chunks = chunk_text(&text, info.max_chunk_chars, info.min_chunk_chars);
        let texts: Vec<&str> = chunks.iter().map(|c| c.text.as_str()).collect();
        // The engine is part of the key: same text and voice from another engine is different audio.
        let key = compute_cache_key(&texts, &json!({"voice": voice, "engine": info.name}));
        let mut audio = self.cache.load(chapter.id, &key);
        if audio.is_empty() {
            self.cache.clear(chapter.id);
            self.cache.write_manifest(chapter.id, &key)?;
        }

        let total = chunks.len();
        for i in audio.len()..total {
            let chunk_path = self.cache.chunk_path(chapter.id, i);
            self.engine.synthesize(&chunks[i].text, &voice, &chunk_path)?;
            audio.push(read_wav(&chunk_path)?.0);
            self.db.update_job_progress(job.id, (i + 1) as f64 / total as f64)?;
            if self.db.is_paused()? {
                return Ok(Some(Stopped::Paused));
            }
            if self.db.is_job_cancel_requested(job.id)? {
                return Ok(Some(Stopped::Cancelled));
            }
        }

        let ends: Vec<EndsWith> = chunks.iter().map(|c| c.ends_with).collect();
        let combined = concatenate_chunks(audio, &ends, info.sample_rate);
        let fallback = self.paths.backup_dir.join(book.id.to_string());
        let (mp3, used_fallback) = export_chapter_mp3(
            &combined, info.sample_rate, Path::new(&book.output_dir), chapter.order_index, &chapter.title,
            &book.title, &narrator, Some(&fallback))?;
        let mp3 = mp3.to_string_lossy();
        self.db.update_chapter_status(chapter.id, ChapterStatus::Done, ChapterOutcome {
            output_mp3_path: Some(&mp3),
            duration: Some(combined.len() as f64 / info.sample_rate as f64),
            error: None,
            used_fallback,
            engine: Some(&info.name),
        })?;
        self.cache.clear(chapter.id);
        Ok(None)
    }

    /// The loaded engine and the job's voice profile.
    fn engine_and_voice(&mut self, engine: &str, voice_profile_id: Option<i64>)
                        -> Result<(EngineInfo, Option<VoiceProfile>), JobError> {
        let info = self.engine.load(engine)?;
        let profile = match voice_profile_id {
            Some(id) => self.db.get_voice_profile_by_id(id)?,
            None => None,
        };
        Ok((info, profile))
    }
}

//! The generation loop, ported from worker.py and synth/pipeline.py. Pause and
//! cancel are checked between chunks, as before.

use std::fs;
use std::io;
use std::panic::AssertUnwindSafe;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use serde_json::json;

use super::cache::{compute_cache_key, ChunkCache};
use super::voice::{trim_point, voice_for, MAX_REFERENCE_SECONDS};
use crate::audio::{concatenate_chunks, export_chapter_mp3, export_mp3, read_wav, AudioError, Tags};
use crate::db::{Database, DbError};
use crate::engine::{EngineError, EngineInfo, PythonEngine};
use crate::models::{ChapterOutcome, ChapterStatus, Job, JobStatus, TextClip, VoiceProfile};
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

    /// One unit of work, if any; false when paused or idle. Transcriptions go
    /// first: they take seconds and the Voices page is waiting on them.
    pub fn run_once(&mut self) -> Result<bool, JobError> {
        if self.db.is_paused()? {
            return Ok(false);
        }
        if self.process_next_transcription()? {
            return Ok(true);
        }
        if self.process_next_chapter_job()? {
            return Ok(true);
        }
        self.process_next_text_clip()
    }

    /// The runner thread's body: work the queue until `stop` is set.
    pub fn run_forever(&mut self, stop: &AtomicBool) {
        // Jobs a dead runner left mid-generation go back in the queue.
        if let Err(e) = self.db.reset_stale_generating_jobs().and_then(|_| self.db.reset_stale_generating_text_clips()) {
            eprintln!("Error: could not reset stale jobs: {e}");
        }
        while !stop.load(Ordering::Relaxed) {
            let did_work = match std::panic::catch_unwind(AssertUnwindSafe(|| self.run_once())) {
                Ok(Ok(did_work)) => did_work,
                Ok(Err(e)) => {
                    eprintln!("Error: worker loop iteration failed: {e}");
                    false
                }
                Err(_) => {
                    eprintln!("Error: worker loop iteration panicked");
                    false
                }
            };
            if !did_work {
                for _ in 0..20 { // 2 s idle poll, in steps so `stop` is seen promptly
                    if stop.load(Ordering::Relaxed) {
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
            }
        }
    }

    fn process_next_transcription(&mut self) -> Result<bool, JobError> {
        let Some(profile) = self.db.next_pending_transcription()? else { return Ok(false) };
        self.transcribe_voice(&profile)?;
        Ok(true)
    }

    /// One Whisper pass: transcript, word timings, and the reference trim. A failure
    /// is recorded on the voice, so it isn't retried forever.
    fn transcribe_voice(&mut self, profile: &VoiceProfile) -> Result<(), JobError> {
        match self.engine.transcribe(Path::new(&profile.reference_audio_path)) {
            Ok(t) => {
                let (trim_end, text) = match trim_point(&t.words, MAX_REFERENCE_SECONDS) {
                    Some((end, text)) => (Some(end), text),
                    None => (None, t.text),
                };
                let words = serde_json::to_value(&t.words).expect("words serialize");
                if let Err(e) = self.db.save_voice_transcript(profile.id, &text, Some(&words), trim_end) {
                    let _ = self.db.fail_voice_transcription(profile.id, &e.to_string());
                    return Err(e.into());
                }
            }
            Err(e) => self.db.fail_voice_transcription(profile.id, &e.to_string())?,
        }
        Ok(())
    }

    fn process_next_text_clip(&mut self) -> Result<bool, JobError> {
        let Some(clip) = self.db.pop_next_queued_text_clip()? else { return Ok(false) };
        if let Err(e) = self.run_text_clip(&clip) {
            self.db.finish_text_clip(clip.id, JobStatus::Failed, None, Some(&e.to_string()), false)?;
        }
        Ok(true)
    }

    fn run_text_clip(&mut self, clip: &TextClip) -> Result<(), JobError> {
        let (info, profile) = self.engine_and_voice(&clip.engine, clip.voice_profile_id)?;
        let voice = voice_for(profile.as_ref(), clip.tempo);
        let narrator = profile.as_ref().map_or("default", |p| p.name.as_str()).to_string();
        let entries = self.db.list_lexicon_entries(None)?;
        let text = apply_lexicon(&clip.text, entries.iter().map(|e| (e.term.as_str(), e.override_value.as_str())));
        let chunks = chunk_text(&text, info.max_chunk_chars, info.min_chunk_chars);

        fs::create_dir_all(&self.paths.text_clip_dir)?;
        let part = self.paths.text_clip_dir.join(format!(".text-clip-{}.part.wav", clip.id));
        let audio = self.synthesize_all(&chunks, &voice, &part, clip.id);
        let _ = fs::remove_file(&part);
        let audio = audio?;

        let ends: Vec<EndsWith> = chunks.iter().map(|c| c.ends_with).collect();
        let combined = concatenate_chunks(audio, &ends, info.sample_rate);
        let title = format!("Text clip #{}", clip.id);
        let tags = Tags { title: Some(&title), artist: Some(&narrator), ..Default::default() };
        let (path, used_fallback) = export_mp3(
            &combined, info.sample_rate, &self.paths.text_clip_dir.join(format!("text-clip-{}.mp3", clip.id)), &tags,
            Some(&self.paths.backup_dir.join("text-clips")))?;
        self.db.finish_text_clip(clip.id, JobStatus::Done, Some(&path.to_string_lossy()), None, used_fallback)?;
        Ok(())
    }

    /// Every chunk through one scratch WAV; text clips aren't cached.
    fn synthesize_all(&mut self, chunks: &[crate::text::Chunk], voice: &crate::engine::Voice, part: &Path,
                      clip_id: i64) -> Result<Vec<Vec<f32>>, JobError> {
        let mut audio = Vec::with_capacity(chunks.len());
        for (i, chunk) in chunks.iter().enumerate() {
            self.engine.synthesize(&chunk.text, voice, part)?;
            audio.push(read_wav(part)?.0);
            self.db.update_text_clip_progress(clip_id, (i + 1) as f64 / chunks.len() as f64)?;
        }
        Ok(audio)
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
        for (i, chunk) in chunks.iter().enumerate().skip(audio.len()) {
            let chunk_path = self.cache.chunk_path(chapter.id, i);
            self.engine.synthesize(&chunk.text, &voice, &chunk_path)?;
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

    /// The loaded engine and the job's voice profile, transcribed first if this
    /// engine needs a transcript and the voice has none yet (voices made before
    /// transcripts existed).
    fn engine_and_voice(&mut self, engine: &str, voice_profile_id: Option<i64>)
                        -> Result<(EngineInfo, Option<VoiceProfile>), JobError> {
        let info = self.engine.load(engine)?;
        let Some(id) = voice_profile_id else { return Ok((info, None)) };
        let Some(profile) = self.db.get_voice_profile_by_id(id)? else { return Ok((info, None)) };
        if !info.needs_transcript || !profile.reference_transcript.as_deref().unwrap_or("").trim().is_empty() {
            return Ok((info, Some(profile)));
        }
        self.transcribe_voice(&profile)?;
        // A worker crash during transcription drops the loaded engine; load is a no-op otherwise.
        let info = self.engine.load(engine)?;
        Ok((info, self.db.get_voice_profile_by_id(id)?))
    }
}

//! Text-clip endpoints: queue a passage, list, play, save a copy, delete.

use std::fs;
use std::path::{Path, PathBuf};

use serde::Serialize;

use super::{check_tempo, chosen_engine, err, found, ApiResult};
use crate::db::Database;
use crate::models::{JobStatus, TextClip, DEFAULT_ENGINE};

#[derive(Debug, Serialize)]
pub struct TextClipDto {
    pub id: i64,
    pub text: String,
    pub voice_profile_id: Option<i64>,
    pub status: &'static str,
    pub progress: Option<f64>,
    pub started_at: Option<f64>,
    pub error: Option<String>,
    pub created_at: f64,
    pub used_fallback: bool,
    pub engine: String,
    pub tempo: Option<f64>,
}

fn dto(c: TextClip) -> TextClipDto {
    TextClipDto {
        id: c.id,
        text: c.text,
        voice_profile_id: c.voice_profile_id,
        status: c.status.as_str(),
        progress: c.progress,
        started_at: c.started_at,
        error: c.error,
        created_at: c.created_at,
        used_fallback: c.used_fallback,
        engine: c.engine,
        tempo: c.tempo,
    }
}

pub fn list_text_clips(db: &Database) -> ApiResult<Vec<TextClipDto>> {
    Ok(db.list_text_clips()?.into_iter().map(dto).collect())
}

pub fn create_text_clip(db: &Database, text: &str, voice_profile_id: Option<i64>, engine: Option<&str>,
                        tempo: Option<f64>) -> ApiResult<TextClipDto> {
    let text = text.trim();
    if text.is_empty() {
        return err("text must not be empty");
    }
    if let Some(id) = voice_profile_id {
        if db.get_voice_profile_by_id(id)?.is_none() {
            return err("voice profile not found");
        }
    }
    let engine = chosen_engine(engine, DEFAULT_ENGINE, voice_profile_id)?;
    let tempo = tempo.map(check_tempo).transpose()?;
    if tempo.is_some() && voice_profile_id.is_none() {
        return err("a tempo needs a voice");
    }
    Ok(dto(db.create_text_clip(text, voice_profile_id, &engine, tempo)?))
}

pub fn get_text_clip(db: &Database, clip_id: i64) -> ApiResult<TextClipDto> {
    Ok(dto(found(db.get_text_clip(clip_id), "text clip")?))
}

pub fn delete_text_clip(db: &Database, clip_id: i64) -> ApiResult<()> {
    let clip = found(db.get_text_clip(clip_id), "text clip")?;
    db.delete_text_clip(clip_id)?;
    if let Some(mp3) = clip.output_path.filter(|p| Path::new(p).is_file()) {
        fs::remove_file(mp3)?;
    }
    Ok(())
}

pub fn text_clip_audio_path(db: &Database, clip_id: i64) -> ApiResult<PathBuf> {
    let clip = found(db.get_text_clip(clip_id), "text clip")?;
    match clip.output_path {
        Some(mp3) if clip.status == JobStatus::Done && Path::new(&mp3).is_file() => Ok(mp3.into()),
        _ => err("audio not ready"),
    }
}

/// Copies a finished clip to where the user chose in the save dialog.
pub fn save_text_clip(db: &Database, clip_id: i64, dest: &Path) -> ApiResult<()> {
    fs::copy(text_clip_audio_path(db, clip_id)?, dest)?;
    Ok(())
}

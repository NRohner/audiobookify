//! Voice endpoints: clone, list, edit, delete, and the clip engines clone from.

use std::path::{Path, PathBuf};

use serde::Serialize;

use super::{check_tempo, err, ApiError, ApiResult, AppPaths};
use crate::db::Database;
use crate::media;
use crate::models::{NewVoiceProfile, VoiceProfile};
use crate::voice_audio::{export_mono_clip, reference_clip, reference_pace, validate_reference_audio, Pace};
use crate::voice_storage::{delete_voice_storage, store_reference_audio};

#[derive(Debug, Serialize)]
pub struct VoiceDto {
    pub id: i64,
    pub name: String,
    pub engine: String,
    pub reference_transcript: Option<String>,
    pub transcript_status: Option<String>,
    pub transcript_error: Option<String>,
    pub reference_seconds: Option<f64>,
    pub reference_trim_end: Option<f64>,
    pub tempo: f64,
    pub pace: Option<Pace>,
    pub exaggeration: f64,
    pub cfg_weight: f64,
    pub temperature: f64,
    pub warnings: Vec<String>,
}

fn voice_dto(p: VoiceProfile, warnings: Vec<String>) -> VoiceDto {
    // A missing or unreadable file is None here; generation reports it properly.
    let reference_seconds = media::duration(Path::new(&p.reference_audio_path)).ok();
    let pace = reference_pace(p.reference_words.as_ref(), p.reference_trim_end);
    VoiceDto {
        id: p.id,
        name: p.name,
        engine: p.engine,
        reference_transcript: p.reference_transcript,
        transcript_status: p.transcript_status,
        transcript_error: p.transcript_error,
        reference_seconds,
        reference_trim_end: p.reference_trim_end,
        tempo: p.tempo,
        pace,
        exaggeration: p.exaggeration,
        cfg_weight: p.cfg_weight,
        temperature: p.temperature,
        warnings,
    }
}

fn existing(db: &Database, voice_id: i64) -> ApiResult<VoiceProfile> {
    db.get_voice_profile_by_id(voice_id)?.ok_or_else(|| ApiError("voice profile not found".into()))
}

pub fn list_voices(db: &Database) -> ApiResult<Vec<VoiceDto>> {
    Ok(db.list_voice_profiles()?.into_iter().map(|p| voice_dto(p, Vec::new())).collect())
}

/// Clones a voice from a recording the user picked. The recording is copied to
/// `voices/<id>_<name>/reference.<ext>`; the original stays where it is.
pub fn create_voice(db: &Database, paths: &AppPaths, source: &Path, name: &str, reference_transcript: Option<&str>)
                    -> ApiResult<VoiceDto> {
    let info = validate_reference_audio(source).map_err(ApiError)?;
    // A typed transcript is used as-is; otherwise the runner runs Whisper.
    let transcript = reference_transcript.map(str::trim).filter(|t| !t.is_empty());
    let profile = db.create_voice_profile(&NewVoiceProfile {
        name,
        engine: "chatterbox-turbo",
        reference_audio_path: &source.to_string_lossy(),
        reference_transcript: transcript,
        exaggeration: 0.5,
        cfg_weight: 0.5,
        temperature: 0.8,
        transcript_status: Some(if transcript.is_some() { "done" } else { "pending" }),
    })?;
    match store_reference_audio(&paths.voices(), source, profile.id, &profile.name) {
        Ok(stored) => db.update_voice_profile_reference_audio_path(profile.id, &stored.to_string_lossy())?,
        Err(e) => {
            db.delete_voice_profile(profile.id)?;
            return err(format!("failed to store reference audio: {e}"));
        }
    }
    Ok(voice_dto(existing(db, profile.id)?, info.warnings))
}

/// Clones a voice from the `keep` ranges of `source` (the clip editor's save): exports them
/// as a mono WAV under `staging`, clones from that, and removes the staged file either way.
pub fn create_voice_from_clip(db: &Database, paths: &AppPaths, staging: &Path, source: &Path, keep: &[(f64, f64)],
                              name: &str, reference_transcript: Option<&str>) -> ApiResult<VoiceDto> {
    std::fs::create_dir_all(staging).map_err(|e| ApiError(format!("failed to stage the clip: {e}")))?;
    let staged = staging.join(format!("clip-{}.wav", uuid::Uuid::new_v4()));
    let result = export_mono_clip(source, &staged, keep)
        .map_err(ApiError)
        .and_then(|_| create_voice(db, paths, &staged, name, reference_transcript));
    let _ = std::fs::remove_file(&staged);
    result
}

pub fn set_voice_transcript(db: &Database, voice_id: i64, transcript: &str) -> ApiResult<VoiceDto> {
    existing(db, voice_id)?;
    let text = transcript.trim();
    if text.is_empty() {
        return err("transcript must not be empty");
    }
    db.save_voice_transcript(voice_id, text, None, None)?;
    Ok(voice_dto(existing(db, voice_id)?, Vec::new()))
}

pub fn set_voice_tempo(db: &Database, voice_id: i64, tempo: f64) -> ApiResult<VoiceDto> {
    existing(db, voice_id)?;
    db.set_voice_tempo(voice_id, check_tempo(tempo)?)?;
    Ok(voice_dto(existing(db, voice_id)?, Vec::new()))
}

/// Queues Whisper again; the runner picks it up within a couple of seconds.
pub fn transcribe_voice(db: &Database, cache: &crate::hub::HubCache, voice_id: i64) -> ApiResult<VoiceDto> {
    super::models::require_transcription(cache)?;
    existing(db, voice_id)?;
    db.request_voice_transcription(voice_id)?;
    Ok(voice_dto(existing(db, voice_id)?, Vec::new()))
}

pub fn delete_voice(db: &Database, paths: &AppPaths, voice_id: i64) -> ApiResult<()> {
    let profile = existing(db, voice_id)?;
    db.delete_voice_profile(voice_id)?;
    delete_voice_storage(&paths.voices(), voice_id, &profile.name);
    Ok(())
}

/// The clip engines clone from (trimmed, at the voice's tempo), or a preview at another
/// tempo without saving it.
pub fn voice_audio_path(db: &Database, voice_id: i64, tempo: Option<f64>) -> ApiResult<PathBuf> {
    let profile = existing(db, voice_id)?;
    let tempo = match tempo {
        Some(t) => check_tempo(t)?,
        None => profile.tempo,
    };
    match reference_clip(Path::new(&profile.reference_audio_path), profile.reference_trim_end, tempo) {
        Ok(path) if path.is_file() => Ok(path),
        _ => err("reference audio missing"),
    }
}

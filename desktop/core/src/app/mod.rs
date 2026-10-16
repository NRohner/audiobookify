//! What each Tauri command does, ported from api/app.py: one function per endpoint, over a
//! `Database` and the app's folders. desktop/src/commands.rs only adds locking and the
//! asset-protocol scope, so everything testable lives here.

pub mod books;
pub mod clips;
pub mod jobs;
pub mod lexicon;
pub mod voices;

use std::fmt;
use std::fs;
use std::path::PathBuf;

use serde::{Serialize, Serializer};

use crate::db::{self, Database, DbError};
use crate::voice_audio::TEMPO_RANGE;

/// Everything the app writes, under one root: the app data dir in the app, a temp dir in tests.
#[derive(Debug, Clone)]
pub struct AppPaths {
    pub root: PathBuf,
}

impl AppPaths {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }
    pub fn db(&self) -> PathBuf { self.root.join("audiobookify.db") }
    pub fn uploads(&self) -> PathBuf { self.root.join("uploads") }
    pub fn voices(&self) -> PathBuf { self.root.join("voices") }
    pub fn cache(&self) -> PathBuf { self.root.join("cache/synth") }
    pub fn text_clips(&self) -> PathBuf { self.root.join("text_clips") }
    pub fn output(&self) -> PathBuf { self.root.join("output") }
    pub fn log(&self) -> PathBuf { self.root.join("logs/audiobookify.log") }

    pub fn open_db(&self) -> db::Result<Database> {
        fs::create_dir_all(&self.root)?;
        Database::open(self.db(), self.voices())
    }

    pub fn runner_paths(&self) -> crate::jobs::Paths {
        crate::jobs::Paths { cache_root: self.cache(), text_clip_dir: self.text_clips(), backup_dir: self.output() }
    }
}

/// A command's error: the message the UI shows, FastAPI's `detail`. It reaches JS as a
/// plain string.
#[derive(Debug, Clone, PartialEq)]
pub struct ApiError(pub String);

pub type ApiResult<T> = Result<T, ApiError>;

impl Serialize for ApiError {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str(&self.0)
    }
}

impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl From<DbError> for ApiError {
    fn from(e: DbError) -> Self {
        match e {
            DbError::NotFound(m) | DbError::Invalid(m) => ApiError(m),
            other => ApiError(other.to_string()),
        }
    }
}

impl From<std::io::Error> for ApiError {
    fn from(e: std::io::Error) -> Self {
        ApiError(e.to_string())
    }
}

pub(crate) fn err<T>(message: impl Into<String>) -> ApiResult<T> {
    Err(ApiError(message.into()))
}

/// `result`, with a missing row reported as app.py's 404 detail, `"{what} not found"`.
pub(crate) fn found<T>(result: db::Result<T>, what: &str) -> ApiResult<T> {
    match result {
        Err(DbError::NotFound(_)) => err(format!("{what} not found")),
        other => Ok(other?),
    }
}

#[derive(Debug, Serialize)]
pub struct EngineSpec {
    pub id: &'static str,
    pub label: &'static str,
    pub description: &'static str,
    pub needs_voice: bool,
}

// ponytail: copied from engines/*.py so commands can validate without loading a model;
// phase 6 deletes the Python registry and this becomes the only copy.
pub const ENGINES: [EngineSpec; 2] = [
    EngineSpec {
        id: "qwen3",
        label: "Quality (Qwen3)",
        description: "Most natural; needs a voice with a transcript. About 0.85 s per second of audio.",
        needs_voice: true,
    },
    EngineSpec {
        id: "chatterbox-turbo",
        label: "Speed (Turbo)",
        description: "About twice as fast, slightly less natural.",
        needs_voice: false,
    },
];

#[derive(Debug, Serialize)]
pub struct Engines {
    pub default: &'static str,
    pub engines: &'static [EngineSpec],
}

pub fn list_engines() -> Engines {
    Engines { default: crate::models::DEFAULT_ENGINE, engines: &ENGINES }
}

/// The engine a request asks for (or the stored one), validated.
pub(crate) fn chosen_engine(requested: Option<&str>, current: &str, voice_profile_id: Option<i64>) -> ApiResult<String> {
    let id = requested.filter(|e| !e.is_empty()).unwrap_or(current);
    let Some(spec) = ENGINES.iter().find(|e| e.id == id) else {
        return err(format!("unknown engine '{id}'"));
    };
    if spec.needs_voice && voice_profile_id.is_none() {
        return err(format!("{} needs a voice: pick one first", spec.label));
    }
    Ok(id.to_string())
}

#[allow(dead_code)] // used by the voice endpoints (task 4)
pub(crate) fn check_tempo(tempo: f64) -> ApiResult<f64> {
    let (lo, hi) = TEMPO_RANGE;
    if !(lo..=hi).contains(&tempo) {
        return err(format!("tempo must be a number from {lo} to {hi}"));
    }
    Ok((tempo * 100.0).round() / 100.0)
}

//! The Settings page's Models section: the registry's models, their downloads, and deletes
//! that never pull files out from under queued or running work.

use std::time::Duration;

use serde::Serialize;

use super::{err, ApiError, ApiResult};
use crate::db::Database;
use crate::hub::store::{DownloadState, Hub};
use crate::hub::{self, HubCache, ModelSpec, REGISTRY};
use crate::jobs::RunnerControl;

#[derive(Debug, Serialize)]
pub struct ModelRow {
    pub id: &'static str,
    pub label: &'static str,
    pub purpose: &'static str,
    pub repos: Vec<&'static str>,
    pub engine: Option<&'static str>,
    pub downloaded: bool,
    /// Bytes on disk now (partial downloads included).
    pub size: u64,
    /// Roughly what a download fetches.
    pub download_bytes: u64,
    pub download: Option<DownloadState>,
}

#[derive(Debug, Serialize)]
pub struct ModelProgress {
    pub id: &'static str,
    pub downloaded: bool,
    pub download: Option<DownloadState>,
}

#[derive(Debug, Serialize)]
pub struct Deleted {
    pub freed: u64,
}

fn known(id: &str) -> ApiResult<&'static ModelSpec> {
    hub::model(id).ok_or_else(|| ApiError(format!("unknown model '{id}'")))
}

pub fn list_models(h: &Hub) -> ApiResult<Vec<ModelRow>> {
    let sizes = h.sizes().map_err(ApiError)?;
    Ok(REGISTRY.iter().map(|m| ModelRow {
        id: m.id,
        label: m.label,
        purpose: m.purpose,
        repos: m.repos.iter().map(|r| r.repo).collect(),
        engine: m.engine,
        downloaded: h.cache().is_present(m),
        size: m.repos.iter().map(|r| sizes.get(r.repo).copied().unwrap_or(0)).sum(),
        download_bytes: m.download_bytes,
        download: h.download_state(m.id),
    }).collect())
}

/// The cheap poll while a download runs: no Python, just the cache layout and the progress.
pub fn model_progress(h: &Hub) -> Vec<ModelProgress> {
    REGISTRY.iter().map(|m| ModelProgress {
        id: m.id, downloaded: h.cache().is_present(m), download: h.download_state(m.id),
    }).collect()
}

pub fn download_model(h: &Hub, id: &str) -> ApiResult<()> {
    h.start_download(known(id)?).map_err(ApiError)
}

pub fn cancel_model_download(h: &Hub, id: &str) {
    h.cancel_download(id);
}

pub fn delete_model(db: &Database, h: &Hub, runner: &RunnerControl, id: &str) -> ApiResult<Deleted> {
    let m = known(id)?;
    if h.download_state(id).is_some_and(|s| matches!(s, DownloadState::Running { .. })) {
        return err("It's still downloading. Cancel the download first.");
    }
    let work = db.active_work()?;
    match m.engine {
        Some(engine) if work.engines.contains(engine) => {
            return err(format!("The {} is in use by queued or rendering work. Cancel it or let it finish, then delete.",
                               m.label.to_lowercase()));
        }
        None if work.any() => {
            return err("Voice transcription is needed by queued work. Cancel it or let it finish, then delete.");
        }
        _ => {}
    }
    // The worker keeps the last engine it used loaded, busy or not, so unload when this model's
    // engine is the loaded one. Whisper is only deletable when no work exists, so the runner
    // is idle and can answer; it may have held Whisper's weights too.
    // ponytail: work queued between this check and the delete fails with "isn't downloaded yet".
    let loaded = match m.engine {
        Some(e) => runner.loaded_engine().as_deref() == Some(e),
        None => true,
    };
    if loaded && !runner.unload_and_wait(Duration::from_secs(10)) {
        return err("The studio is still holding the model. Try again in a moment.");
    }
    Ok(Deleted { freed: h.delete(m).map_err(ApiError)? })
}

pub(crate) fn require_engine(cache: &HubCache, engine: &str) -> ApiResult<()> {
    match hub::model_for_engine(engine) {
        Some(m) if !cache.is_present(m) => err(hub::not_downloaded(m.label)),
        _ => Ok(()),
    }
}

pub(crate) fn require_transcription(cache: &HubCache) -> ApiResult<()> {
    let m = hub::model(hub::TRANSCRIPTION).expect("registry has transcription");
    if cache.is_present(m) { Ok(()) } else { err(hub::not_downloaded(m.label)) }
}

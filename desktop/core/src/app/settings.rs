//! App-wide settings, in `<data dir>/settings.json`: a file, not the DB, because opening the
//! DB already needs the voices library path.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::books::validate_output_dir;
use super::{ApiError, ApiResult, AppPaths};
use crate::db::Database;

#[derive(Debug, Default, Serialize, Deserialize)]
pub(crate) struct Stored {
    #[serde(default)]
    pub voices_dir: Option<PathBuf>,
}

fn file(root: &Path) -> PathBuf {
    root.join("settings.json")
}

/// Missing or unreadable settings are the defaults, so a bad file can't stop the app opening.
pub(crate) fn load(root: &Path) -> Stored {
    fs::read(file(root)).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default()
}

#[derive(Debug, Serialize)]
pub struct SettingsDto {
    pub voices_dir: String,
    pub default_voices_dir: String,
    pub is_default: bool,
}

pub fn get_settings(paths: &AppPaths) -> SettingsDto {
    SettingsDto {
        voices_dir: paths.voices().display().to_string(),
        default_voices_dir: paths.default_voices().display().to_string(),
        is_default: paths.voices() == paths.default_voices(),
    }
}

#[derive(Debug, Serialize)]
pub struct VoicesDirChange {
    pub settings: SettingsDto,
    /// Voices whose clips couldn't be copied; they keep playing from where they were.
    pub warnings: Vec<String>,
}

/// Points the voices library at `dir` (`None`: the default), copying every existing voice
/// clip into it. The old folder is left as it was.
pub fn set_voices_dir(db: &mut Database, paths: &mut AppPaths, dir: Option<&str>) -> ApiResult<VoicesDirChange> {
    let new = match dir.map(str::trim).filter(|d| !d.is_empty()) {
        None => paths.default_voices(),
        Some(d) => {
            let check = validate_output_dir(d);
            if let Some(error) = check.error {
                return Err(ApiError(error));
            }
            PathBuf::from(check.path)
        }
    };
    fs::create_dir_all(&new)?;
    // The same resolved form the migration writes into the rows.
    let new = fs::canonicalize(&new)?;
    let warnings = db.adopt_voices_dir(&new)?;
    let default = fs::canonicalize(paths.default_voices()).unwrap_or_else(|_| paths.default_voices());
    let stored = Stored { voices_dir: (new != default).then(|| new.clone()) };
    fs::create_dir_all(&paths.root)?;
    fs::write(file(&paths.root), serde_json::to_vec_pretty(&stored).expect("settings serialize"))?;
    paths.voices_dir = stored.voices_dir;
    Ok(VoicesDirChange { settings: get_settings(paths), warnings })
}

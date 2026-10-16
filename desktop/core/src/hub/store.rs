//! Sizes, deletion and downloads, through one-shot `python -m audiobookify.model_store`
//! processes (huggingface_hub's own cache API; snapshots are symlinks into blob stores, so
//! hand-rolled du/rm get them wrong). Downloads run online; the engine worker never does.

use std::collections::HashMap;
use std::ffi::OsString;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex, MutexGuard};

use serde::Serialize;
use serde_json::{json, Value};

use super::{HubCache, ModelSpec, REGISTRY};

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum DownloadState {
    Running { downloaded: u64, total: u64 },
    Failed { error: String },
}

struct Entry {
    state: DownloadState,
    pid: u32,
    /// Held so the child sees EOF, and exits, when the entry is dropped (cancel) or the app exits.
    _stdin: Option<ChildStdin>,
}

type Downloads = Arc<Mutex<HashMap<String, Entry>>>;

pub struct Hub {
    cache: HubCache,
    python: PathBuf,
    cwd: PathBuf,
    env: Vec<(OsString, OsString)>,
    downloads: Downloads,
}

fn lock(d: &Downloads) -> MutexGuard<'_, HashMap<String, Entry>> {
    d.lock().unwrap_or_else(|p| p.into_inner())
}

impl Hub {
    pub fn new(cache: HubCache, python: impl Into<PathBuf>, cwd: impl Into<PathBuf>) -> Self {
        Self { cache, python: python.into(), cwd: cwd.into(), env: Vec::new(), downloads: Downloads::default() }
    }

    pub fn env(mut self, key: impl Into<OsString>, value: impl Into<OsString>) -> Self {
        self.env.push((key.into(), value.into()));
        self
    }

    pub fn cache(&self) -> &HubCache {
        &self.cache
    }

    fn command(&self, args: &[&str]) -> Command {
        let mut cmd = Command::new(&self.python);
        cmd.args(["-m", "audiobookify.model_store"]).args(args).current_dir(&self.cwd)
            .env_remove("HF_HUB_OFFLINE")
            .env("HF_HUB_CACHE", &self.cache.root)
            .envs(self.env.iter().map(|(k, v)| (k, v)));
        cmd
    }

    fn one_shot(&self, args: &[&str]) -> Result<Value, String> {
        let out = self.command(args).stdin(Stdio::null()).output()
            .map_err(|e| format!("could not start {}: {e}", self.python.display()))?;
        let line = String::from_utf8_lossy(&out.stdout);
        serde_json::from_str(line.trim()).map_err(|e| {
            let err = String::from_utf8_lossy(&out.stderr);
            let last = err.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("");
            format!("model_store {} failed ({}): {e}: {last}", args[0], out.status)
        })
    }

    pub fn sizes(&self) -> Result<HashMap<String, u64>, String> {
        let repos: Vec<&str> = REGISTRY.iter().flat_map(|m| m.repos.iter().map(|r| r.repo)).collect();
        let reply = self.one_shot(&[&["sizes"], repos.as_slice()].concat())?;
        serde_json::from_value(reply["sizes"].clone()).map_err(|e| e.to_string())
    }

    pub fn delete(&self, m: &ModelSpec) -> Result<u64, String> {
        let repos: Vec<&str> = m.repos.iter().map(|r| r.repo).collect();
        let reply = self.one_shot(&[&["delete"], repos.as_slice()].concat())?;
        reply["freed"].as_u64().ok_or_else(|| format!("unexpected reply {reply}"))
    }

    pub fn downloading(&self) -> bool {
        lock(&self.downloads).values().any(|e| matches!(e.state, DownloadState::Running { .. }))
    }

    // ponytail: one download at a time; parallel downloads only split the same bandwidth.
    pub fn start_download(&self, m: &'static ModelSpec) -> Result<(), String> {
        let mut downloads = lock(&self.downloads);
        if downloads.values().any(|e| matches!(e.state, DownloadState::Running { .. })) {
            return Err("Another download is still running. Wait for it to finish, or cancel it.".into());
        }
        let items: Vec<Value> = m.repos.iter()
            .map(|r| json!({"repo": r.repo, "allow_patterns": r.allow_patterns, "required": r.required}))
            .collect();
        let mut child = self.command(&["download", &Value::from(items).to_string()])
            .stdin(Stdio::piped()).stdout(Stdio::piped()).spawn()
            .map_err(|e| format!("could not start {}: {e}", self.python.display()))?;
        downloads.insert(m.id.to_string(), Entry {
            state: DownloadState::Running { downloaded: 0, total: m.download_bytes },
            pid: child.id(),
            _stdin: child.stdin.take(),
        });
        let (map, id) = (self.downloads.clone(), m.id.to_string());
        std::thread::spawn(move || watch(map, id, child));
        Ok(())
    }

    pub fn cancel_download(&self, id: &str) {
        // Dropping the entry closes the child's stdin; model_store exits on that EOF.
        lock(&self.downloads).remove(id);
    }

    pub fn download_state(&self, id: &str) -> Option<DownloadState> {
        lock(&self.downloads).get(id).map(|e| e.state.clone())
    }
}

/// Reads one download's progress lines until the child exits, then records how it ended.
fn watch(downloads: Downloads, id: String, mut child: Child) {
    let pid = child.id();
    let stdout = BufReader::new(child.stdout.take().expect("piped"));
    let mut failure = Some("The download stopped before it finished.".to_string());
    for line in stdout.lines().map_while(Result::ok) {
        let Ok(v) = serde_json::from_str::<Value>(&line) else { continue };
        if v.get("done").is_some() {
            failure = None;
        } else if let Some(e) = v.get("error").and_then(Value::as_str) {
            failure = Some(e.to_string());
        } else if let (Some(downloaded), Some(total)) = (v["downloaded"].as_u64(), v["total"].as_u64()) {
            if let Some(entry) = lock(&downloads).get_mut(&id).filter(|e| e.pid == pid) {
                entry.state = DownloadState::Running { downloaded, total };
            }
        }
    }
    let _ = child.wait();
    let mut map = lock(&downloads);
    // A cancelled entry was already removed; a later download of the same id may own it now.
    let Some(entry) = map.get_mut(&id).filter(|e| e.pid == pid) else { return };
    match failure {
        None => {
            map.remove(&id);
        }
        Some(error) => entry.state = DownloadState::Failed { error },
    }
}

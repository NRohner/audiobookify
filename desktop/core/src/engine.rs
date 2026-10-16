//! The client for the Python engine worker (`python -m audiobookify.engine_worker`):
//! JSON lines over the child's stdin/stdout, one request in flight at a time.
//! Audio moves through WAV files, never through the pipe.

use std::ffi::OsString;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Debug, thiserror::Error)]
pub enum EngineError {
    /// The engine answered with an error (bad input, a model exception); the child is fine.
    #[error("{0}")]
    Engine(String),
    /// The child couldn't start, died, or broke the protocol; it has been dropped
    /// and the next request starts a new one.
    #[error("engine worker failed: {0}")]
    Process(String),
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct EngineInfo {
    pub name: String,
    pub max_chunk_chars: usize,
    pub min_chunk_chars: usize,
    pub sample_rate: u32,
    pub needs_transcript: bool,
}

/// What the engines clone from. All None = the engine's built-in voice.
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Voice {
    pub reference_path: Option<String>,
    pub reference_text: Option<String>,
    pub trim_end: Option<f64>,
    /// None = 1.0.
    pub tempo: Option<f64>,
    pub exaggeration: Option<f64>,
    pub cfg: Option<f64>,
    pub temperature: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Word {
    pub word: String,
    pub start: f64,
    pub end: f64,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Transcript {
    pub text: String,
    pub words: Vec<Word>,
}

#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
pub struct SynthOut {
    pub samples: usize,
    pub sample_rate: u32,
}

struct Proc {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

pub struct PythonEngine {
    python: PathBuf,
    cwd: PathBuf,
    env: Vec<(OsString, OsString)>,
    proc: Option<Proc>,
    loaded: Option<EngineInfo>,
}

impl PythonEngine {
    /// The child starts lazily, on the first request.
    pub fn new(python: impl Into<PathBuf>, cwd: impl Into<PathBuf>) -> Self {
        Self { python: python.into(), cwd: cwd.into(), env: Vec::new(), proc: None, loaded: None }
    }

    pub fn env(mut self, key: impl Into<OsString>, value: impl Into<OsString>) -> Self {
        self.env.push((key.into(), value.into()));
        self
    }

    pub fn child_pid(&self) -> Option<u32> {
        self.proc.as_ref().map(|p| p.child.id())
    }

    /// Make `name` the loaded engine (a no-op if it already is) and return its info.
    pub fn load(&mut self, name: &str) -> Result<EngineInfo, EngineError> {
        self.drop_if_dead(); // before the short-circuit, which trusts `loaded`
        if let Some(info) = self.loaded.as_ref().filter(|i| i.name == name) {
            return Ok(info.clone());
        }
        self.loaded = None; // the worker tears the old engine down before loading
        self.request(json!({"op": "load", "engine": name}))?;
        let ok = self.request(json!({"op": "engine_info"}))?;
        let info: EngineInfo = self.parse(ok)?;
        self.loaded = Some(info.clone());
        Ok(info)
    }

    pub fn synthesize(&mut self, text: &str, voice: &Voice, out_wav: &Path) -> Result<SynthOut, EngineError> {
        let ok = self.request(json!({"op": "synthesize", "text": text, "voice": voice, "out_wav": out_wav}))?;
        self.parse(ok)
    }

    pub fn transcribe(&mut self, path: &Path) -> Result<Transcript, EngineError> {
        let ok = self.request(json!({"op": "transcribe", "path": path}))?;
        self.parse(ok)
    }

    fn parse<T: serde::de::DeserializeOwned>(&mut self, ok: Value) -> Result<T, EngineError> {
        serde_json::from_value(ok).map_err(|e| self.fail(format!("unexpected response: {e}")))
    }

    /// A child that died while idle: drop it (and its loaded engine) so the request respawns it.
    fn drop_if_dead(&mut self) {
        if self.proc.as_mut().is_some_and(|p| p.child.try_wait().is_ok_and(|s| s.is_some())) {
            self.proc = None;
            self.loaded = None;
        }
    }

    fn request(&mut self, req: Value) -> Result<Value, EngineError> {
        self.drop_if_dead();
        let response = self.round_trip(&req).map_err(|e| self.fail(e))?;
        if let Some(ok) = response.get("ok") {
            return Ok(ok.clone());
        }
        match response.get("error").and_then(Value::as_str) {
            Some(message) => Err(EngineError::Engine(message.to_string())),
            None => Err(self.fail(format!("malformed response {response}"))),
        }
    }

    fn round_trip(&mut self, req: &Value) -> Result<Value, String> {
        if self.proc.is_none() {
            self.proc = Some(self.spawn()?);
        }
        let p = self.proc.as_mut().expect("just spawned");
        writeln!(p.stdin, "{req}").and_then(|_| p.stdin.flush()).map_err(|e| format!("could not send request: {e}"))?;
        let mut line = String::new();
        let n = p.stdout.read_line(&mut line).map_err(|e| format!("could not read response: {e}"))?;
        if n == 0 {
            return Err("the engine worker exited".into());
        }
        serde_json::from_str(&line).map_err(|e| format!("bad response line {line:?}: {e}"))
    }

    fn spawn(&self) -> Result<Proc, String> {
        let mut child = Command::new(&self.python)
            .args(["-m", "audiobookify.engine_worker"])
            .current_dir(&self.cwd)
            .envs(self.env.iter().map(|(k, v)| (k, v)))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| format!("could not start {}: {e}", self.python.display()))?;
        let stdin = child.stdin.take().expect("piped");
        let stdout = BufReader::new(child.stdout.take().expect("piped"));
        Ok(Proc { child, stdin, stdout })
    }

    /// Drop a broken child; the next request starts a fresh one.
    fn fail(&mut self, message: String) -> EngineError {
        self.loaded = None;
        if let Some(mut p) = self.proc.take() {
            let _ = p.child.kill();
            let _ = p.child.wait();
        }
        EngineError::Process(message)
    }

    /// Close stdin (the worker exits on EOF), give it a moment, then kill. The next request
    /// starts a fresh child.
    pub fn shutdown(&mut self) {
        self.loaded = None;
        let Some(Proc { mut child, stdin, stdout }) = self.proc.take() else { return };
        drop(stdin);
        drop(stdout);
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

impl Drop for PythonEngine {
    fn drop(&mut self) {
        self.shutdown();
    }
}

//! The Hugging Face models the app uses, and whether they're in the cache. The cache is
//! shared with other projects, so nothing outside `REGISTRY` is ever listed or touched.
//! Presence is read straight from the cache layout (fast enough to gate every generate);
//! sizes, deletion and downloads go through `audiobookify.model_store` (see `store`).

pub mod store;

use std::path::PathBuf;

pub struct RepoSpec {
    pub repo: &'static str,
    /// What `snapshot_download` fetches; the same patterns mlx-audio's loader uses.
    pub allow_patterns: &'static [&'static str],
    /// Snapshot paths that must resolve for the model to load.
    pub required: &'static [&'static str],
}

pub struct ModelSpec {
    /// The engine id for engines, `TRANSCRIPTION` for Whisper.
    pub id: &'static str,
    pub label: &'static str,
    pub purpose: &'static str,
    pub engine: Option<&'static str>,
    /// Shown before download.
    // ponytail: measured from the cache on 2026-10-16; query the Hub if repos change often.
    pub download_bytes: u64,
    pub repos: &'static [RepoSpec],
}

pub const TRANSCRIPTION: &str = "whisper";

/// mlx_audio.utils.DEFAULT_ALLOW_PATTERNS in mlx-audio 0.5.9 (pinned in pyproject.toml).
const MLX_PATTERNS: &[&str] = &["*.json", "*.safetensors", "*.py", "*.model", "*.tiktoken", "*.txt", "*.jinja",
                                "*.jsonl", "*.yaml", "*.npz", "*.pth"];

pub static REGISTRY: [ModelSpec; 3] = [
    ModelSpec {
        id: "qwen3",
        label: "Quality engine",
        purpose: "The most natural narration. Needs a narrator voice with a transcript.",
        engine: Some("qwen3"),
        download_bytes: 4_500_000_000,
        repos: &[RepoSpec {
            repo: "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16",
            allow_patterns: MLX_PATTERNS,
            required: &["config.json", "model.safetensors", "speech_tokenizer/config.json",
                        "speech_tokenizer/model.safetensors"],
        }],
    },
    ModelSpec {
        id: "chatterbox-turbo",
        label: "Speed engine",
        purpose: "About twice as fast, slightly less natural. Works without a narrator voice.",
        engine: Some("chatterbox-turbo"),
        download_bytes: 3_500_000_000,
        repos: &[
            RepoSpec {
                repo: "mlx-community/chatterbox-turbo-fp16",
                allow_patterns: MLX_PATTERNS,
                required: &["config.json", "model.safetensors", "conds.safetensors"],
            },
            // Fetched by mlx-audio at load time; offline, a miss is only a logged warning,
            // so it must be downloaded with the engine.
            RepoSpec {
                repo: "mlx-community/S3TokenizerV2",
                allow_patterns: &["*.json", "*.safetensors"],
                required: &["model.safetensors"],
            },
        ],
    },
    ModelSpec {
        id: TRANSCRIPTION,
        label: "Voice transcription",
        purpose: "Writes the transcript for a narrator voice you add, so the Quality engine can use it.",
        engine: None,
        download_bytes: 1_600_000_000,
        repos: &[RepoSpec {
            repo: "mlx-community/whisper-large-v3-turbo-asr-fp16",
            allow_patterns: MLX_PATTERNS,
            required: &["config.json", "model.safetensors"],
        }],
    },
];

pub fn model(id: &str) -> Option<&'static ModelSpec> {
    REGISTRY.iter().find(|m| m.id == id)
}

pub fn model_for_engine(engine: &str) -> Option<&'static ModelSpec> {
    REGISTRY.iter().find(|m| m.engine == Some(engine))
}

/// huggingface_hub's rule: HF_HUB_CACHE, else HUGGINGFACE_HUB_CACHE (legacy), else HF_HOME/hub,
/// else XDG_CACHE_HOME/huggingface/hub, else ~/.cache/huggingface/hub. Empty values count as
/// unset and a leading `~` expands to HOME, as in HF's `expanduser`.
pub fn default_cache_dir() -> PathBuf {
    cache_dir_from(|k| std::env::var_os(k))
}

pub fn cache_dir_from(var: impl Fn(&str) -> Option<std::ffi::OsString>) -> PathBuf {
    let home = || var("HOME").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_default();
    let get = |k: &str| {
        var(k).filter(|v| !v.is_empty()).map(PathBuf::from).map(|p| match p.strip_prefix("~") {
            Ok(rest) => home().join(rest),
            Err(_) => p,
        })
    };
    if let Some(p) = get("HF_HUB_CACHE").or_else(|| get("HUGGINGFACE_HUB_CACHE")) {
        return p;
    }
    let hf_home = get("HF_HOME").unwrap_or_else(|| {
        get("XDG_CACHE_HOME").unwrap_or_else(|| home().join(".cache")).join("huggingface")
    });
    hf_home.join("hub")
}

#[derive(Debug, Clone)]
pub struct HubCache {
    pub root: PathBuf,
}

impl HubCache {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    fn repo_dir(&self, repo: &str) -> PathBuf {
        self.root.join(format!("models--{}", repo.replace('/', "--")))
    }

    /// Every required file of every repo resolves through `refs/main`. HF only links a
    /// snapshot file once its blob is complete, so a cancelled download reads as missing.
    pub fn is_present(&self, m: &ModelSpec) -> bool {
        m.repos.iter().all(|r| {
            let dir = self.repo_dir(r.repo);
            let Ok(commit) = std::fs::read_to_string(dir.join("refs/main")) else { return false };
            let snapshot = dir.join("snapshots").join(commit.trim());
            r.required.iter().all(|f| snapshot.join(f).exists())
        })
    }

    /// False only for a registry engine whose files are missing; the test engines ("fake")
    /// have no files.
    pub fn engine_ready(&self, engine: &str) -> bool {
        model_for_engine(engine).is_none_or(|m| self.is_present(m))
    }
}

use std::fs;
use std::io;
use std::path::PathBuf;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::audio::read_wav;

/// Keyed on the chunks themselves, not the source text, so any chunking change
/// (chunker logic, engine min/max) invalidates audio cached under old boundaries.
pub fn compute_cache_key(chunk_texts: &[&str], voice: &Value) -> String {
    let payload = json!({"chunks": chunk_texts, "voice_params": voice}).to_string();
    Sha256::digest(payload.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

/// Synthesized chunks of an unfinished chapter, so a pause or crash resumes where it stopped:
/// `<root>/chapter-N/manifest.json` + `chunk-NNNN.wav`.
pub struct ChunkCache {
    root: PathBuf,
}

impl ChunkCache {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn chapter_dir(&self, chapter_id: i64) -> PathBuf {
        self.root.join(format!("chapter-{chapter_id}"))
    }

    pub fn chunk_path(&self, chapter_id: i64, index: usize) -> PathBuf {
        self.chapter_dir(chapter_id).join(format!("chunk-{index:04}.wav"))
    }

    /// The cached chunks from 0 up to the first missing or unreadable one; empty
    /// when the manifest is missing, unreadable or for another key.
    pub fn load(&self, chapter_id: i64, key: &str) -> Vec<Vec<f32>> {
        let manifest = fs::read_to_string(self.chapter_dir(chapter_id).join("manifest.json")).ok()
            .and_then(|s| serde_json::from_str::<Value>(&s).ok());
        if manifest.as_ref().and_then(|m| m["cache_key"].as_str()) != Some(key) {
            return Vec::new();
        }
        // An unreadable or empty chunk was cut off mid-write (a WAV header alone reads as
        // Ok with 0 samples); it gets synthesized again.
        (0..).map_while(|i| read_wav(&self.chunk_path(chapter_id, i)).ok().map(|(audio, _)| audio).filter(|a| !a.is_empty())).collect()
    }

    pub fn write_manifest(&self, chapter_id: i64, key: &str) -> io::Result<()> {
        let dir = self.chapter_dir(chapter_id);
        fs::create_dir_all(&dir)?;
        fs::write(dir.join("manifest.json"), json!({"cache_key": key}).to_string())
    }

    pub fn clear(&self, chapter_id: i64) {
        let _ = fs::remove_dir_all(self.chapter_dir(chapter_id));
    }
}

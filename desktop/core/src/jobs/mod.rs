//! The job runner and its pieces, ported from worker.py, synth/pipeline.py and synth/chunk_cache.py.

mod cache;
mod voice;

pub use cache::{compute_cache_key, ChunkCache};
pub use voice::{trim_point, voice_for, MAX_REFERENCE_SECONDS};

use audiobookify_core::audio::write_wav;
use audiobookify_core::jobs::{compute_cache_key, ChunkCache};
use serde_json::json;
use tempfile::TempDir;

fn key(chunks: &[&str], voice: &str) -> String {
    compute_cache_key(chunks, &json!({"voice": voice}))
}

#[test]
fn the_key_is_deterministic_and_changes_with_text_voice_and_boundaries() {
    assert_eq!(key(&["Hello."], "fake"), key(&["Hello."], "fake"));
    assert_ne!(key(&["Hello."], "fake"), key(&["Goodbye."], "fake"));
    assert_ne!(key(&["Hello."], "fake"), key(&["Hello."], "other"));
    assert_ne!(key(&["Hello. World."], "fake"), key(&["Hello.", "World."], "fake"));
    assert_eq!(key(&["x"], "v").len(), 64);
}

#[test]
fn chunks_round_trip_in_order_until_the_first_missing_one() {
    let dir = TempDir::new().unwrap();
    let cache = ChunkCache::new(dir.path());
    let k = key(&["a", "b"], "v");
    cache.write_manifest(1, &k).unwrap();
    write_wav(&cache.chunk_path(1, 0), &[0.1, 0.2], 24000).unwrap();
    write_wav(&cache.chunk_path(1, 1), &[0.3], 24000).unwrap();
    write_wav(&cache.chunk_path(1, 3), &[0.9], 24000).unwrap(); // after a gap: ignored
    assert_eq!(cache.load(1, &k), vec![vec![0.1, 0.2], vec![0.3]]);
    assert_eq!(cache.chunk_path(1, 1), dir.path().join("chapter-1/chunk-0001.wav"));
}

#[test]
fn load_is_empty_without_a_cache_or_with_another_key() {
    let dir = TempDir::new().unwrap();
    let cache = ChunkCache::new(dir.path());
    assert!(cache.load(999, &key(&["x"], "v")).is_empty());
    cache.write_manifest(2, &key(&["old"], "v")).unwrap();
    write_wav(&cache.chunk_path(2, 0), &[0.0; 10], 24000).unwrap();
    assert!(cache.load(2, &key(&["new"], "v")).is_empty());
}

#[test]
fn a_half_written_chunk_ends_the_cached_run() {
    let dir = TempDir::new().unwrap();
    let cache = ChunkCache::new(dir.path());
    let k = key(&["a", "b"], "v");
    cache.write_manifest(4, &k).unwrap();
    write_wav(&cache.chunk_path(4, 0), &[0.5], 24000).unwrap();
    std::fs::write(cache.chunk_path(4, 1), b"RIFF").unwrap(); // killed mid-write
    assert_eq!(cache.load(4, &k), vec![vec![0.5]]);
}

#[test]
fn clear_removes_the_chapter_dir_and_is_a_noop_without_one() {
    let dir = TempDir::new().unwrap();
    let cache = ChunkCache::new(dir.path());
    cache.write_manifest(3, "k").unwrap();
    assert!(cache.chapter_dir(3).is_dir());
    cache.clear(3);
    assert!(!cache.chapter_dir(3).exists());
    cache.clear(999);
}

#[test]
fn a_header_only_chunk_from_a_killed_write_ends_the_load() {
    let dir = TempDir::new().unwrap();
    let cache = ChunkCache::new(dir.path());
    let k = key(&["a", "b", "c"], "v");
    cache.write_manifest(1, &k).unwrap();
    write_wav(&cache.chunk_path(1, 0), &[0.1, 0.2], 24000).unwrap();
    write_wav(&cache.chunk_path(1, 1), &[], 24000).unwrap(); // a valid header, data length 0
    write_wav(&cache.chunk_path(1, 2), &[0.5], 24000).unwrap();
    assert_eq!(cache.load(1, &k), vec![vec![0.1, 0.2]]);
}

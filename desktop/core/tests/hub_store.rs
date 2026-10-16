mod common;

use std::time::{Duration, Instant};

use audiobookify_core::hub::store::{DownloadState, Hub};
use audiobookify_core::hub::{self, HubCache};
use common::*;
use tempfile::TempDir;

fn hub(dir: &std::path::Path, env: &[(&str, &str)]) -> Hub {
    let repo = repo_root();
    let cache = dir.join("hub");
    env.iter().fold(
        Hub::new(HubCache::new(&cache), repo.join(".venv/bin/python"), &repo).env("HF_HUB_CACHE", &cache),
        |h, (k, v)| h.env(*k, *v),
    )
}

fn wait_until(mut done: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(30);
    while !done() {
        assert!(Instant::now() < deadline, "timed out");
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[test]
fn sizes_cover_every_registry_repo() {
    let dir = TempDir::new().unwrap();
    cache_model(&dir.path().join("hub"), "whisper");
    let sizes = hub(dir.path(), &[]).sizes().unwrap();
    assert_eq!(sizes["mlx-community/whisper-large-v3-turbo-asr-fp16"], 14); // 2 required files x b"weights"
    assert_eq!(sizes["mlx-community/S3TokenizerV2"], 0);
    assert_eq!(sizes.len(), 4);
}

#[test]
fn fake_download_completes_and_clears_its_entry() {
    let dir = TempDir::new().unwrap();
    let h = hub(dir.path(), &[("AUDIOBOOKIFY_FAKE_DOWNLOAD", "1")]);
    let turbo = hub::model("chatterbox-turbo").unwrap();
    h.start_download(turbo).unwrap();
    wait_until(|| h.download_state("chatterbox-turbo").is_none());
    assert!(h.cache().is_present(turbo));
}

#[test]
fn delete_frees_the_model_and_reports_bytes() {
    let dir = TempDir::new().unwrap();
    let h = hub(dir.path(), &[]);
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    cache_model(&dir.path().join("hub"), "whisper");
    let turbo = hub::model("chatterbox-turbo").unwrap();
    assert_eq!(h.delete(turbo).unwrap(), 4 * 7); // 3 + 1 required files
    assert!(!h.cache().is_present(turbo));
    assert!(h.cache().is_present(hub::model("whisper").unwrap()));
}

#[test]
fn failed_download_is_reported() {
    let dir = TempDir::new().unwrap();
    let h = hub(dir.path(), &[("AUDIOBOOKIFY_FAKE_DOWNLOAD", "error")]);
    h.start_download(hub::model("whisper").unwrap()).unwrap();
    wait_until(|| matches!(h.download_state("whisper"), Some(DownloadState::Failed { .. })));
    assert_eq!(h.download_state("whisper"), Some(DownloadState::Failed { error: "fake download failed".into() }));
}

#[test]
fn one_download_at_a_time_and_cancel_stops_it() {
    let dir = TempDir::new().unwrap();
    let h = hub(dir.path(), &[("AUDIOBOOKIFY_FAKE_DOWNLOAD", "1"), ("AUDIOBOOKIFY_FAKE_DOWNLOAD_DELAY", "0.3")]);
    h.start_download(hub::model("qwen3").unwrap()).unwrap();
    wait_until(|| matches!(h.download_state("qwen3"), Some(DownloadState::Running { downloaded, .. }) if downloaded > 0));
    assert!(h.downloading());
    assert!(h.start_download(hub::model("whisper").unwrap()).is_err());

    h.cancel_download("qwen3");
    assert_eq!(h.download_state("qwen3"), None);
    std::thread::sleep(Duration::from_secs(2)); // 4 files x 0.3s: an unstopped child would have finished by now
    assert!(!h.cache().is_present(hub::model("qwen3").unwrap()));
    assert!(!h.downloading());
}

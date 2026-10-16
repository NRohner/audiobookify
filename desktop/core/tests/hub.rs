mod common;

use std::fs;

use audiobookify_core::hub::{self, HubCache, REGISTRY};
use common::*;
use tempfile::TempDir;

#[test]
fn registry_engines_match_the_app_engines() {
    for spec in audiobookify_core::app::ENGINES.iter() {
        assert_eq!(hub::model_for_engine(spec.id).unwrap().id, spec.id);
    }
    assert!(hub::model(hub::TRANSCRIPTION).unwrap().engine.is_none());
    assert!(hub::model("facebook/wav2vec2-lv-60-espeak-cv-ft").is_none());
}

#[test]
fn turbo_includes_the_s3_tokenizer_it_fetches_at_load() {
    let repos: Vec<_> = hub::model("chatterbox-turbo").unwrap().repos.iter().map(|r| r.repo).collect();
    assert_eq!(repos, ["mlx-community/chatterbox-turbo-fp16", "mlx-community/S3TokenizerV2"]);
}

#[test]
fn present_only_when_every_required_file_resolves() {
    let dir = TempDir::new().unwrap();
    let cache = HubCache::new(dir.path());
    let qwen = hub::model("qwen3").unwrap();
    assert!(!cache.is_present(qwen));

    cache_model(dir.path(), "qwen3");
    assert!(cache.is_present(qwen));

    // A cancelled download: the weights' symlink never got made.
    let repo = dir.path().join("models--mlx-community--Qwen3-TTS-12Hz-1.7B-Base-bf16");
    let commit = fs::read_to_string(repo.join("refs/main")).unwrap();
    fs::remove_file(repo.join("snapshots").join(commit.trim()).join("speech_tokenizer/model.safetensors")).unwrap();
    assert!(!cache.is_present(qwen));
}

#[test]
fn a_dangling_symlink_is_not_present() {
    let dir = TempDir::new().unwrap();
    cache_model(dir.path(), "whisper");
    let repo = dir.path().join("models--mlx-community--whisper-large-v3-turbo-asr-fp16");
    for blob in fs::read_dir(repo.join("blobs")).unwrap() {
        fs::remove_file(blob.unwrap().path()).unwrap();
    }
    assert!(!HubCache::new(dir.path()).is_present(hub::model("whisper").unwrap()));
}

#[test]
fn engine_ready_follows_presence_and_ignores_unknown_engines() {
    let dir = TempDir::new().unwrap();
    let cache = HubCache::new(dir.path());
    assert!(!cache.engine_ready("chatterbox-turbo"));
    assert!(cache.engine_ready("fake"));
    cache_model(dir.path(), "chatterbox-turbo");
    assert!(cache.engine_ready("chatterbox-turbo"));
}

#[test]
fn ready_cache_has_everything() {
    let dir = TempDir::new().unwrap();
    let cache = ready_cache(dir.path());
    assert!(REGISTRY.iter().all(|m| cache.is_present(m)));
}

fn lookup(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<std::ffi::OsString> {
    let map: std::collections::HashMap<String, String> =
        pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
    move |k| map.get(k).map(Into::into)
}

#[test]
fn cache_dir_follows_huggingface_hub_precedence() {
    use std::path::PathBuf;
    let all = [("HF_HUB_CACHE", "/a"), ("HUGGINGFACE_HUB_CACHE", "/b"), ("HF_HOME", "/c"),
               ("XDG_CACHE_HOME", "/d"), ("HOME", "/h")];
    for (skip, want) in [(0, "/a"), (1, "/b"), (2, "/c/hub"), (3, "/d/huggingface/hub"), (4, "/h/.cache/huggingface/hub")] {
        assert_eq!(hub::cache_dir_from(lookup(&all[skip..])), PathBuf::from(want));
    }
}

#[test]
fn cache_dir_treats_empty_as_unset() {
    let env = lookup(&[("HF_HUB_CACHE", ""), ("HF_HOME", "/c"), ("HOME", "/h")]);
    assert_eq!(hub::cache_dir_from(env), std::path::PathBuf::from("/c/hub"));
}

#[test]
fn cache_dir_expands_a_leading_tilde() {
    let env = lookup(&[("HF_HOME", "~/x"), ("HOME", "/h")]);
    assert_eq!(hub::cache_dir_from(env), std::path::PathBuf::from("/h/x/hub"));
}

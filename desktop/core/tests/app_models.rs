mod common;

use std::time::Duration;

use audiobookify_core::app::{self, clips, jobs, models};
use audiobookify_core::hub::store::Hub;
use audiobookify_core::hub::HubCache;
use audiobookify_core::jobs::RunnerControl;
use audiobookify_core::models::JobStatus;
use common::*;
use tempfile::TempDir;

fn hub_at(dir: &std::path::Path) -> Hub {
    let repo = repo_root();
    let cache = dir.join("hub");
    Hub::new(HubCache::new(&cache), repo.join(".venv/bin/python"), &repo).env("HF_HUB_CACHE", &cache)
}

/// A runner thread stand-in that answers unload requests.
fn answering_runner() -> RunnerControl {
    // Built from a real Runner would need an engine; the control is just a flag, so a
    // helper thread clears it the way Runner::handle_unload does.
    let control = RunnerControl::default();
    let c = control.clone();
    std::thread::spawn(move || loop {
        c.answer_unload_for_tests();
        std::thread::sleep(Duration::from_millis(10));
    });
    control
}

#[test]
fn list_models_shows_only_the_registry_with_sizes() {
    let dir = TempDir::new().unwrap();
    cache_model(&dir.path().join("hub"), "whisper");
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    std::fs::create_dir_all(dir.path().join("hub/models--someone--else/blobs")).unwrap();
    let rows = models::list_models(&hub_at(dir.path())).unwrap();
    let ids: Vec<_> = rows.iter().map(|r| r.id).collect();
    assert_eq!(ids, ["qwen3", "chatterbox-turbo", "whisper"]);
    let turbo = &rows[1];
    assert!(turbo.downloaded);
    assert_eq!(turbo.size, 4 * 7);
    assert!(!rows[0].downloaded);
    assert_eq!(rows[0].size, 0);
}

#[test]
fn delete_is_refused_while_queued_work_needs_the_engine() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    clips::create_text_clip(&db, hub.cache(), "Hi.", None, Some("chatterbox-turbo"), None).unwrap();
    let _ = paths;

    let e = models::delete_model(&db, &hub, &answering_runner(), "chatterbox-turbo").unwrap_err();
    assert!(e.0.contains("in use"), "{e}");
    assert!(hub.cache().is_present(audiobookify_core::hub::model("chatterbox-turbo").unwrap()));
}

#[test]
fn delete_of_another_engine_while_busy_needs_no_unload() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    cache_model(&dir.path().join("hub"), "qwen3");
    clips::create_text_clip(&db, hub.cache(), "Hi.", None, Some("chatterbox-turbo"), None).unwrap();
    // No one answers unload requests: this must not wait for one.
    let freed = models::delete_model(&db, &hub, &RunnerControl::default(), "qwen3").unwrap().freed;
    assert_eq!(freed, 4 * 7);
}

#[test]
fn transcription_delete_is_refused_while_anything_is_working() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "whisper");
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    clips::create_text_clip(&db, hub.cache(), "Hi.", None, Some("chatterbox-turbo"), None).unwrap();
    assert!(models::delete_model(&db, &hub, &answering_runner(), "whisper").is_err());
}

#[test]
fn idle_delete_unloads_first_and_frees_the_space() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    assert_eq!(models::delete_model(&db, &hub, &answering_runner(), "chatterbox-turbo").unwrap().freed, 28);
    // Idle, but nobody answers: refused rather than deleting under a loaded engine.
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    let e = models::delete_model(&db, &hub, &RunnerControl::default(), "chatterbox-turbo").unwrap_err();
    assert!(e.0.contains("Try again"), "{e}");
}

#[test]
fn unknown_model_ids_are_refused() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    assert!(models::download_model(&hub, "facebook/wav2vec2-lv-60-espeak-cv-ft").is_err());
    assert!(models::delete_model(&db, &hub, &answering_runner(), "someone/else").is_err());
}

#[test]
fn generating_with_a_missing_engine_is_refused() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let src = write_file(dir.path(), "book.md", TWO_CHAPTERS_MD);
    let book = audiobookify_core::app::books::create_book(&db, &paths, &src, &path(dir.path(), "out")).unwrap();
    let empty = HubCache::new(dir.path().join("empty"));
    let e = jobs::generate_book(&db, &empty, book.id, Some("chatterbox-turbo")).unwrap_err();
    assert_eq!(e.0, "Speed engine isn't downloaded yet. Download it in Settings.");
    assert!(clips::create_text_clip(&db, &empty, "Hi.", None, Some("chatterbox-turbo"), None).is_err());
    assert!(db.list_text_clips().unwrap().iter().all(|c| c.status != JobStatus::Queued));
}

#[test]
fn list_engines_reports_readiness() {
    let dir = TempDir::new().unwrap();
    cache_model(dir.path(), "chatterbox-turbo");
    let engines = app::list_engines(&HubCache::new(dir.path()));
    let ready: Vec<_> = engines.engines.iter().map(|e| (e.spec.id, e.ready)).collect();
    assert_eq!(ready, [("qwen3", false), ("chatterbox-turbo", true)]);
}

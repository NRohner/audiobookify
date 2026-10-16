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
    let (_paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    clips::create_text_clip(&db, hub.cache(), "Hi.", None, Some("chatterbox-turbo"), None).unwrap();

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
    let e = models::delete_model(&db, &hub, &answering_runner(), "whisper").unwrap_err();
    assert_eq!(e.0, "Voice transcription is needed by queued work. Cancel it or let it finish, then delete.");
    assert!(hub.cache().is_present(audiobookify_core::hub::model("whisper").unwrap()));
}

#[test]
fn idle_delete_unloads_first_and_frees_the_space() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    let answering = answering_runner();
    answering.set_loaded_for_tests(Some("chatterbox-turbo"));
    assert_eq!(models::delete_model(&db, &hub, &answering, "chatterbox-turbo").unwrap().freed, 28);
    // Idle, but nobody answers: refused rather than deleting under a loaded engine.
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    let silent = RunnerControl::default();
    silent.set_loaded_for_tests(Some("chatterbox-turbo"));
    let e = models::delete_model(&db, &hub, &silent, "chatterbox-turbo").unwrap_err();
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
    let msg = "Speed engine isn't downloaded yet. Download it in Settings.";
    let e = jobs::generate_book(&db, &empty, book.id, Some("qwen3")).unwrap_err();
    assert!(e.0.contains("needs a voice"), "{e}");
    let e = jobs::generate_book(&db, &empty, book.id, Some("chatterbox-turbo")).unwrap_err();
    assert_eq!(e.0, msg);
    let chapter = book.chapters[0].id;
    assert_eq!(jobs::generate_chapter(&db, &empty, book.id, chapter, Some("chatterbox-turbo")).unwrap_err().0, msg);
    for c in &book.chapters {
        assert!(db.list_jobs_for_chapter(c.id).unwrap().is_empty());
    }
    assert_eq!(db.get_book(book.id).unwrap().engine, book.engine);
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

fn book_in(dir: &std::path::Path) -> (audiobookify_core::app::AppPaths, audiobookify_core::db::Database,
                                      audiobookify_core::app::books::BookDto) {
    let (paths, db) = app(dir);
    let src = write_file(dir, "book.md", TWO_CHAPTERS_MD);
    let book = audiobookify_core::app::books::create_book(&db, &paths, &src, &path(dir, "out")).unwrap();
    (paths, db, book)
}

#[test]
fn delete_is_refused_for_a_queued_then_a_generating_book_job() {
    let dir = TempDir::new().unwrap();
    let hub = hub_at(dir.path());
    let models_cache = ready_cache(dir.path());
    let (_p, db, book) = book_in(dir.path());
    jobs::generate_book(&db, &models_cache, book.id, Some("chatterbox-turbo")).unwrap();
    let e = models::delete_model(&db, &hub, &answering_runner(), "chatterbox-turbo").unwrap_err();
    assert!(e.0.contains("in use"), "{e}");
    db.pop_next_queued_job().unwrap().unwrap(); // now Generating
    let e = models::delete_model(&db, &hub, &answering_runner(), "chatterbox-turbo").unwrap_err();
    assert!(e.0.contains("in use"), "{e}");
    assert!(hub.cache().is_present(audiobookify_core::hub::model("chatterbox-turbo").unwrap()));
}

#[test]
fn a_pending_transcription_blocks_deleting_whisper() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "whisper");
    let clip = dir.path().join("nova.wav");
    clean_tone_wav(&clip);
    audiobookify_core::app::voices::create_voice(&db, &paths, &clip, "Nova", None).unwrap();
    let e = models::delete_model(&db, &hub, &answering_runner(), "whisper").unwrap_err();
    assert!(e.0.contains("needed by queued work"), "{e}");
}

#[test]
fn transcribing_without_whisper_is_refused() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let clip = dir.path().join("nova.wav");
    clean_tone_wav(&clip);
    let voice = audiobookify_core::app::voices::create_voice(&db, &paths, &clip, "Nova", Some("Hi.")).unwrap();
    let empty = HubCache::new(dir.path().join("empty"));
    let e = audiobookify_core::app::voices::transcribe_voice(&db, &empty, voice.id).unwrap_err();
    assert_eq!(e.0, "Voice transcription isn't downloaded yet. Download it in Settings.");
}

#[test]
fn deleting_the_loaded_engine_while_paused_and_busy_needs_an_unload() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    cache_model(&dir.path().join("hub"), "qwen3");
    clips::create_text_clip(&db, hub.cache(), "Hi.", None, Some("chatterbox-turbo"), None).unwrap();
    let control = RunnerControl::default();
    control.set_loaded_for_tests(Some("qwen3"));
    let e = models::delete_model(&db, &hub, &control, "qwen3").unwrap_err();
    assert!(e.0.contains("Try again"), "{e}");
    assert!(hub.cache().is_present(audiobookify_core::hub::model("qwen3").unwrap()));

    let answering = answering_runner();
    answering.set_loaded_for_tests(Some("qwen3"));
    assert_eq!(models::delete_model(&db, &hub, &answering, "qwen3").unwrap().freed, 4 * 7);
}

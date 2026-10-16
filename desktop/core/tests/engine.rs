mod common;

use audiobookify_core::audio::read_wav;
use audiobookify_core::engine::{EngineError, Voice};
use common::*;
use tempfile::TempDir;

#[test]
fn load_returns_engine_info_and_synthesize_writes_the_wav() {
    let dir = TempDir::new().unwrap();
    let mut engine = fake_engine(&[]);
    let info = engine.load("fake").unwrap();
    assert_eq!((info.name.as_str(), info.max_chunk_chars, info.min_chunk_chars, info.sample_rate, info.needs_transcript),
               ("fake", 300, 20, 24000, false));
    let wav = dir.path().join("a.wav");
    let out = engine.synthesize("Hello there.", &Voice::default(), &wav).unwrap();
    let (audio, sr) = read_wav(&wav).unwrap();
    assert_eq!((out.samples, out.sample_rate), (audio.len(), sr));
}

#[test]
fn an_engine_error_keeps_the_child_and_its_loaded_engine() {
    let dir = TempDir::new().unwrap();
    let mut engine = fake_engine(&[("AUDIOBOOKIFY_FAKE_FAIL_ON", "boom")]);
    engine.load("fake").unwrap();
    let err = engine.synthesize("Boom.", &Voice::default(), &dir.path().join("a.wav")).unwrap_err();
    assert!(matches!(&err, EngineError::Engine(m) if m == "synthesis blew up"), "{err:?}");
    engine.synthesize("Fine.", &Voice::default(), &dir.path().join("b.wav")).unwrap();
}

#[test]
fn a_dead_child_is_a_process_error_and_the_next_load_respawns_it() {
    let dir = TempDir::new().unwrap();
    let mut engine = fake_engine(&[("AUDIOBOOKIFY_FAKE_CRASH_ON", "crash")]);
    engine.load("fake").unwrap();
    let err = engine.synthesize("Crash now.", &Voice::default(), &dir.path().join("a.wav")).unwrap_err();
    assert!(matches!(err, EngineError::Process(_)), "{err:?}");
    assert!(err.to_string().starts_with("engine worker failed: "));
    // The loaded engine is forgotten with the child, so load really reloads.
    engine.load("fake").unwrap();
    engine.synthesize("Fine.", &Voice::default(), &dir.path().join("b.wav")).unwrap();
}

#[test]
fn synthesize_before_load_is_an_engine_error() {
    let dir = TempDir::new().unwrap();
    let err = fake_engine(&[]).synthesize("Hi.", &Voice::default(), &dir.path().join("a.wav")).unwrap_err();
    assert!(matches!(&err, EngineError::Engine(m) if m == "no engine loaded"), "{err:?}");
}

#[test]
fn transcribe_returns_text_and_word_timings() {
    let mut engine = fake_engine(&[("AUDIOBOOKIFY_FAKE_STT", "1")]);
    let t = engine.transcribe(std::path::Path::new("/nowhere.wav")).unwrap();
    assert_eq!(t.text, "One two. Three");
    assert_eq!((t.words.len(), t.words[1].word.as_str(), t.words[1].end), (3, " two.", 14.0));
}

#[test]
fn switching_engines_reports_the_new_engine() {
    let mut engine = fake_engine(&[]);
    assert_eq!(engine.load("fake").unwrap().name, "fake");
    assert_eq!(engine.load("fake-b").unwrap().name, "fake-b");
}

#[test]
fn dropping_the_engine_ends_the_child() {
    let mut engine = fake_engine(&[]);
    engine.load("fake").unwrap();
    let pid = engine.child_pid().unwrap();
    drop(engine);
    let alive = std::process::Command::new("kill").args(["-0", &pid.to_string()])
        .stderr(std::process::Stdio::null()).status().unwrap().success();
    assert!(!alive, "engine worker {pid} outlived its PythonEngine");
}

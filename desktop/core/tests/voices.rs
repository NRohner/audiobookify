mod common;

use audiobookify_core::models::NewVoiceProfile;
use common::*;
use serde_json::json;
use tempfile::TempDir;

#[test]
fn create_and_find_voice_profile_by_name() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let profile = db.create_voice_profile(&NewVoiceProfile {
        name: "narrator-1", engine: "chatterbox", reference_audio_path: "/refs/clip.wav",
        reference_transcript: Some("Hello there."), exaggeration: 0.6, cfg_weight: 0.4,
        temperature: 0.75, transcript_status: None,
    }).unwrap();
    assert_eq!(profile.name, "narrator-1");

    let found = db.find_voice_profile_by_name("narrator-1").unwrap().unwrap();
    assert_eq!(found.reference_audio_path, "/refs/clip.wav");
    assert_eq!(found.reference_transcript.as_deref(), Some("Hello there."));
    assert_eq!((found.exaggeration, found.cfg_weight, found.temperature), (0.6, 0.4, 0.75));
    assert!(db.find_voice_profile_by_name("nope").unwrap().is_none());
}

#[test]
fn list_voice_profiles_returns_all_in_creation_order() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    voice_at(&db, "a", "/a.wav");
    voice_at(&db, "b", "/b.wav");
    let names: Vec<String> = db.list_voice_profiles().unwrap().into_iter().map(|p| p.name).collect();
    assert_eq!(names, vec!["a", "b"]);
}

#[test]
fn create_voice_profile_rejects_duplicate_name() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    voice_at(&db, "dup", "/a.wav");
    let result = db.create_voice_profile(&NewVoiceProfile {
        name: "dup", engine: "chatterbox", reference_audio_path: "/b.wav", reference_transcript: None,
        exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8, transcript_status: None,
    });
    assert_eq!(invalid(result), "a voice profile named 'dup' already exists");
}

#[test]
fn update_book_voice_profile_persists() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    let profile = voice_at(&db, "narrator-1", "/refs/clip.wav");
    db.update_book_voice_profile(book.id, Some(profile.id)).unwrap();
    assert_eq!(db.get_book(book.id).unwrap().voice_profile_id, Some(profile.id));
}

#[test]
fn get_voice_profile_by_id() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let profile = voice_at(&db, "narrator-1", "/refs/clip.wav");
    assert_eq!(db.get_voice_profile_by_id(profile.id).unwrap().unwrap().name, "narrator-1");
    assert!(db.get_voice_profile_by_id(999999).unwrap().is_none());
}

#[test]
fn delete_voice_profile_removes_it() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let profile = voice_at(&db, "narrator-1", "/refs/clip.wav");
    db.delete_voice_profile(profile.id).unwrap();
    assert!(db.get_voice_profile_by_id(profile.id).unwrap().is_none());
}

#[test]
fn delete_voice_profile_raises_not_found_for_missing_id() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert_eq!(not_found(db.delete_voice_profile(999999)), "no voice profile with id 999999");
}

#[test]
fn update_voice_profile_reference_audio_path_updates_the_path() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let profile = voice_at(&db, "Nova", &path(dir.path(), "old.wav"));
    db.update_voice_profile_reference_audio_path(profile.id, &path(dir.path(), "new.wav")).unwrap();
    assert_eq!(db.get_voice_profile_by_id(profile.id).unwrap().unwrap().reference_audio_path,
               path(dir.path(), "new.wav"));
}

#[test]
fn update_voice_profile_reference_audio_path_raises_not_found_for_missing_id() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    not_found(db.update_voice_profile_reference_audio_path(999, "/some/path.wav"));
}

#[test]
fn voice_transcription_queue_hands_out_pending_voices_oldest_first() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let typed = voice(&db, dir.path(), "Typed", Some("I typed this."), Some("done"));
    let first = voice(&db, dir.path(), "First", None, Some("pending"));
    voice(&db, dir.path(), "Second", None, Some("pending"));
    assert_eq!(typed.transcript_status.as_deref(), Some("done"));
    assert_eq!(first.transcript_status.as_deref(), Some("pending"));
    assert_eq!(db.next_pending_transcription().unwrap().unwrap().name, "First");
}

#[test]
fn saving_a_whisper_transcript_stores_words_and_trim_and_finishes_the_voice() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let v = voice(&db, dir.path(), "Nova", None, Some("pending"));
    let words = json!([{"word": " Hi", "start": 0.0, "end": 0.4}]);

    db.save_voice_transcript(v.id, "Hi", Some(&words), Some(14.95)).unwrap();

    let saved = db.get_voice_profile_by_id(v.id).unwrap().unwrap();
    assert_eq!(saved.reference_transcript.as_deref(), Some("Hi"));
    assert_eq!(saved.transcript_status.as_deref(), Some("done"));
    assert_eq!(saved.reference_words, Some(words));
    assert_eq!(saved.reference_trim_end, Some(14.95));
    assert!(db.next_pending_transcription().unwrap().is_none());
}

#[test]
fn editing_a_transcript_keeps_the_word_timings_and_trim() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let v = voice(&db, dir.path(), "Nova", None, Some("pending"));
    let words = json!([{"word": " a", "start": 0, "end": 1}]);
    db.save_voice_transcript(v.id, "a small son cold", Some(&words), Some(12.0)).unwrap();

    db.save_voice_transcript(v.id, "a small son called", None, None).unwrap();

    let saved = db.get_voice_profile_by_id(v.id).unwrap().unwrap();
    assert_eq!(saved.reference_transcript.as_deref(), Some("a small son called"));
    assert_eq!(saved.reference_words, Some(words));
    assert_eq!(saved.reference_trim_end, Some(12.0));
}

#[test]
fn a_failed_transcription_is_recorded_and_can_be_retried() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let v = voice(&db, dir.path(), "Nova", None, Some("pending"));

    db.fail_voice_transcription(v.id, "unreadable audio").unwrap();
    let failed = db.get_voice_profile_by_id(v.id).unwrap().unwrap();
    assert_eq!(failed.transcript_status.as_deref(), Some("failed"));
    assert_eq!(failed.transcript_error.as_deref(), Some("unreadable audio"));
    assert!(db.next_pending_transcription().unwrap().is_none());

    db.request_voice_transcription(v.id).unwrap();
    let retry = db.get_voice_profile_by_id(v.id).unwrap().unwrap();
    assert_eq!(retry.transcript_status.as_deref(), Some("pending"));
    assert_eq!(retry.transcript_error, None);
    not_found(db.request_voice_transcription(999));
}

#[test]
fn voice_tempo_defaults_to_one_and_can_be_set() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let v = voice(&db, dir.path(), "Nova", None, None);
    assert_eq!(v.tempo, 1.0);
    db.set_voice_tempo(v.id, 0.88).unwrap();
    assert_eq!(db.get_voice_profile_by_id(v.id).unwrap().unwrap().tempo, 0.88);
    not_found(db.set_voice_tempo(999, 0.9));
}

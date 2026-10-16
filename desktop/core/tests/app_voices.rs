mod common;

use std::fs;
use std::path::Path;

use audiobookify_core::app::{clips, lexicon, voices, AppPaths};
use audiobookify_core::db::Database;
use audiobookify_core::models::JobStatus;
use audiobookify_core::voice_storage::permanent_voice_dir;
use common::*;
use serde_json::json;
use tempfile::TempDir;

fn clone_nova(dir: &Path, paths: &AppPaths, db: &Database, transcript: Option<&str>) -> voices::VoiceDto {
    let clip = dir.join("nova.wav");
    clean_tone_wav(&clip);
    voices::create_voice(db, paths, &clip, "Nova", transcript).unwrap()
}

// --- voices ---

// test_create_voice_uploads_reference_audio_and_returns_profile,
// test_create_voice_stores_reference_audio_under_voices_dir_not_uploads,
// test_a_new_voice_is_queued_for_transcription_and_reports_its_length
#[test]
fn create_voice_stores_a_copy_and_queues_transcription() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, None);

    assert_eq!(voice.name, "Nova");
    assert_eq!(voice.warnings, Vec::<String>::new());
    assert_eq!(voice.transcript_status.as_deref(), Some("pending"));
    assert!((voice.reference_seconds.unwrap() - 15.0).abs() < 1e-6);
    let stored = db.get_voice_profile_by_id(voice.id).unwrap().unwrap().reference_audio_path;
    assert_eq!(Path::new(&stored), permanent_voice_dir(&paths.voices(), voice.id, "Nova").join("reference.wav"));
    assert!(dir.path().join("nova.wav").is_file());
}

// test_a_voice_uploaded_with_a_typed_transcript_skips_whisper
#[test]
fn a_typed_transcript_skips_whisper() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, Some("  Hello there.  "));
    assert_eq!(voice.reference_transcript.as_deref(), Some("Hello there."));
    assert_eq!(voice.transcript_status.as_deref(), Some("done"));
}

// test_create_voice_rejects_audio_too_short, test_create_voice_rejects_duplicate_name
#[test]
fn create_voice_rejects_short_clips_and_duplicate_names() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let short = dir.path().join("short.wav");
    wav(&short, 16000, 1, 8000, |_| 0.1);
    assert_eq!(api_err(voices::create_voice(&db, &paths, &short, "Short", None)),
               "reference audio is too short to clone from (0.50s); need at least 1s");

    clone_nova(dir.path(), &paths, &db, None);
    let again = dir.path().join("again.wav");
    clean_tone_wav(&again);
    assert!(!api_err(voices::create_voice(&db, &paths, &again, "Nova", None)).is_empty());
    assert_eq!(voices::list_voices(&db).unwrap().len(), 1);
}

// test_create_voice_rolls_back_the_profile_row_when_permanent_storage_fails
#[test]
fn create_voice_rolls_back_when_the_copy_fails() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    fs::write(paths.voices(), b"a file where the voices folder should be").unwrap();
    let clip = dir.path().join("nova.wav");
    clean_tone_wav(&clip);

    let err = api_err(voices::create_voice(&db, &paths, &clip, "Nova", None));

    assert!(err.starts_with("failed to store reference audio: "), "{err}");
    assert!(voices::list_voices(&db).unwrap().is_empty());
}

#[test]
fn a_voice_from_an_edited_clip_stores_the_kept_ranges_in_mono() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let take = dir.path().join("take.wav");
    wav(&take, 16000, 2, 16000 * 20, |_| 0.3);
    let staging = dir.path().join("staging");

    // Trim to 2..14 s and cut 6..9 s out of the middle: 9 s kept.
    let voice = voices::create_voice_from_clip(&db, &paths, &staging, &take, &[(2.0, 6.0), (9.0, 14.0)],
                                               "Rec", Some("Hello."))
        .unwrap();

    assert!((voice.reference_seconds.unwrap() - 9.0).abs() < 0.01);
    assert_eq!(voice.reference_transcript.as_deref(), Some("Hello."));
    let stored = db.get_voice_profile_by_id(voice.id).unwrap().unwrap().reference_audio_path;
    assert_eq!(hound::WavReader::open(&stored).unwrap().spec().channels, 1);
    assert_eq!(fs::read_dir(&staging).unwrap().count(), 0, "the staged clip is cleaned up");
}

#[test]
fn a_failed_clip_voice_leaves_no_voice_or_staged_file() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let take = dir.path().join("take.wav");
    clean_tone_wav(&take);
    let staging = dir.path().join("staging");

    clone_nova(dir.path(), &paths, &db, None);
    assert!(voices::create_voice_from_clip(&db, &paths, &staging, &take, &[(0.0, 10.0)], "Nova", None).is_err());

    assert_eq!(voices::list_voices(&db).unwrap().len(), 1);
    assert_eq!(fs::read_dir(&staging).unwrap().count(), 0);
}

// test_list_voices_returns_all_profiles, test_delete_voice_removes_profile,
// test_delete_voice_returns_404_for_unknown_id, test_delete_voice_removes_the_permanent_voice_storage_folder
#[test]
fn list_and_delete_voices() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, None);
    assert_eq!(voices::list_voices(&db).unwrap().iter().map(|v| v.id).collect::<Vec<_>>(), vec![voice.id]);

    voices::delete_voice(&db, &paths, voice.id).unwrap();

    assert!(voices::list_voices(&db).unwrap().is_empty());
    assert!(!permanent_voice_dir(&paths.voices(), voice.id, "Nova").exists());
    assert_eq!(api_err(voices::delete_voice(&db, &paths, voice.id)), "voice profile not found");
}

// test_editing_a_voice_transcript_saves_it, test_requesting_a_transcription_requeues_it
#[test]
fn transcripts_can_be_edited_or_requested_again() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, None);

    let edited = voices::set_voice_transcript(&db, voice.id, "  Fixed words.  ").unwrap();
    assert_eq!(edited.reference_transcript.as_deref(), Some("Fixed words."));
    assert_eq!(api_err(voices::set_voice_transcript(&db, voice.id, "   ")), "transcript must not be empty");

    db.fail_voice_transcription(voice.id, "Whisper broke").unwrap();
    let requeued = voices::transcribe_voice(&db, &models, voice.id).unwrap();
    assert_eq!((requeued.transcript_status.as_deref(), requeued.transcript_error), (Some("pending"), None));
    assert_eq!(api_err(voices::transcribe_voice(&db, &models, 999)), "voice profile not found");
}

// test_a_voice_reports_its_tempo_and_the_pace_of_the_clip_engines_hear
#[test]
fn a_voice_reports_its_tempo_and_pace() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, None);
    let words = json!([
        {"word": "Hello.", "start": 0.0, "end": 0.5},
        {"word": "there", "start": 1.0, "end": 1.5},
        {"word": "friend", "start": 1.5, "end": 2.0},
    ]);
    db.save_voice_transcript(voice.id, "Hello. there friend", Some(&words), Some(1.5)).unwrap();

    let got = voices::list_voices(&db).unwrap().remove(0);

    assert_eq!(got.tempo, 1.0);
    assert_eq!(got.reference_trim_end, Some(1.5));
    let pace = got.pace.unwrap();
    assert_eq!((pace.speaking_wpm, pace.overall_wpm), (120.0, 80.0));
}

// test_setting_a_voice_tempo_validates_the_range_and_keeps_the_transcript
#[test]
fn setting_a_tempo_validates_and_rounds_it() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, Some("Hi."));
    assert_eq!(api_err(voices::set_voice_tempo(&db, voice.id, 1.3)), "tempo must be a number from 0.75 to 1.25");
    let set = voices::set_voice_tempo(&db, voice.id, 0.904).unwrap();
    assert_eq!((set.tempo, set.reference_transcript.as_deref()), (0.9, Some("Hi.")));
}

// test_voice_audio_serves_the_clip_the_engines_clone_from, test_voice_audio_plays_the_saved_tempo_or_a_preview_tempo
#[test]
fn voice_audio_is_the_clip_engines_clone_from_at_the_saved_or_preview_tempo() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, Some("Hi."));
    let reference = permanent_voice_dir(&paths.voices(), voice.id, "Nova").join("reference.wav");

    assert_eq!(voices::voice_audio_path(&db, voice.id, None).unwrap(), reference);
    voices::set_voice_tempo(&db, voice.id, 0.9).unwrap();
    assert_eq!(voices::voice_audio_path(&db, voice.id, None).unwrap(), reference.with_file_name("reference.tempo-0.90.wav"));
    assert_eq!(voices::voice_audio_path(&db, voice.id, Some(1.1)).unwrap(), reference.with_file_name("reference.tempo-1.10.wav"));
    assert_eq!(api_err(voices::voice_audio_path(&db, voice.id, Some(2.0))), "tempo must be a number from 0.75 to 1.25");
    assert_eq!(api_err(voices::voice_audio_path(&db, 999, None)), "voice profile not found");

    fs::remove_dir_all(reference.parent().unwrap()).unwrap();
    assert_eq!(api_err(voices::voice_audio_path(&db, voice.id, None)), "reference audio missing");
}

// --- pronunciations ---

// test_list_pronunciations_returns_global_entries, test_create_pronunciation_rejects_duplicate_term,
// test_update_pronunciation_override_value, test_update_pronunciation_returns_404_for_missing_term,
// test_delete_pronunciation_entry, test_delete_pronunciation_returns_404_for_missing_term
#[test]
fn pronunciations_round_trip() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let made = lexicon::create_pronunciation(&db, "Hermione", "her-MY-oh-nee", Some("HP")).unwrap();
    assert_eq!((made.term.as_str(), made.notes.as_deref()), ("Hermione", Some("HP")));
    assert!(!api_err(lexicon::create_pronunciation(&db, "Hermione", "x", None)).is_empty());

    assert_eq!(lexicon::update_pronunciation(&db, "Hermione", "her-MY-uh-nee").unwrap().override_value, "her-MY-uh-nee");
    assert_eq!(api_err(lexicon::update_pronunciation(&db, "Nope", "x")), "pronunciation entry not found");
    assert_eq!(lexicon::list_pronunciations(&db).unwrap().len(), 1);

    lexicon::delete_pronunciation(&db, "Hermione").unwrap();
    assert!(lexicon::list_pronunciations(&db).unwrap().is_empty());
    assert_eq!(api_err(lexicon::delete_pronunciation(&db, "Hermione")), "pronunciation entry not found");
}

// --- text clips ---

// test_create_text_clip_queues_it_with_default_voice, test_create_text_clip_rejects_empty_text,
// test_create_text_clip_returns_404_for_unknown_voice_profile
#[test]
fn create_text_clip_queues_it_and_validates() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (_paths, db) = app(dir.path());
    let clip = clips::create_text_clip(&db, &models, "  Hello.  ", None, None, None).unwrap();
    assert_eq!((clip.text.as_str(), clip.status, clip.engine.as_str(), clip.voice_profile_id),
               ("Hello.", "queued", "chatterbox-turbo", None));
    assert_eq!(api_err(clips::create_text_clip(&db, &models, "   ", None, None, None)), "text must not be empty");
    assert_eq!(api_err(clips::create_text_clip(&db, &models, "Hi", Some(999), None, None)), "voice profile not found");
}

// test_text_clips_store_their_engine, test_a_text_clip_can_carry_a_sample_tempo
#[test]
fn text_clips_store_engine_and_tempo() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, Some("Hi."));
    let clip = clips::create_text_clip(&db, &models, "Hi", Some(voice.id), Some("qwen3"), Some(0.9)).unwrap();
    assert_eq!((clip.engine.as_str(), clip.tempo), ("qwen3", Some(0.9)));
    assert_eq!(api_err(clips::create_text_clip(&db, &models, "Hi", None, Some("qwen3"), None)),
               "Quality engine needs a voice: pick one first");
    assert_eq!(api_err(clips::create_text_clip(&db, &models, "Hi", None, None, Some(0.9))), "a tempo needs a voice");
    assert_eq!(api_err(clips::create_text_clip(&db, &models, "Hi", Some(voice.id), None, Some(3.0))),
               "tempo must be a number from 0.75 to 1.25");
}

// test_list_text_clips_returns_most_recent_first, test_get_text_clip_returns_404_for_unknown_id,
// test_get_text_clip_surfaces_used_fallback, test_get_text_clip_surfaces_started_at_while_generating
#[test]
fn list_and_get_text_clips() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (_paths, db) = app(dir.path());
    let a = clips::create_text_clip(&db, &models, "A", None, None, None).unwrap();
    let b = clips::create_text_clip(&db, &models, "B", None, None, None).unwrap();
    assert_eq!(clips::list_text_clips(&db).unwrap().iter().map(|c| c.id).collect::<Vec<_>>(), vec![b.id, a.id]);
    assert_eq!(api_err(clips::get_text_clip(&db, 999)), "text clip not found");

    let running = db.pop_next_queued_text_clip().unwrap().unwrap();
    assert!(clips::get_text_clip(&db, running.id).unwrap().started_at.is_some());
    db.finish_text_clip(running.id, JobStatus::Done, Some("/x.mp3"), None, true).unwrap();
    assert!(clips::get_text_clip(&db, running.id).unwrap().used_fallback);
}

// test_get_text_clip_audio_returns_404_before_generation_completes, test_delete_text_clip_removes_it,
// test_delete_text_clip_returns_404_for_unknown_id
#[test]
fn text_clip_audio_save_and_delete() {
    let dir = TempDir::new().unwrap();
    let models = ready_cache(dir.path());
    let (_paths, db) = app(dir.path());
    let clip = clips::create_text_clip(&db, &models, "Hi", None, None, None).unwrap();
    assert_eq!(api_err(clips::text_clip_audio_path(&db, clip.id)), "audio not ready");

    let mp3 = write_file(dir.path(), "text-clip-1.mp3", "ID3");
    db.finish_text_clip(clip.id, JobStatus::Done, Some(&mp3.to_string_lossy()), None, false).unwrap();
    assert_eq!(clips::text_clip_audio_path(&db, clip.id).unwrap(), mp3);

    let copy = dir.path().join("saved.mp3");
    clips::save_text_clip(&db, clip.id, &copy).unwrap();
    assert_eq!(fs::read_to_string(&copy).unwrap(), "ID3");

    clips::delete_text_clip(&db, clip.id).unwrap();
    assert!(!mp3.exists());
    assert_eq!(api_err(clips::get_text_clip(&db, clip.id)), "text clip not found");
    assert_eq!(api_err(clips::delete_text_clip(&db, clip.id)), "text clip not found");
}

mod common;

use std::fs;

use audiobookify_core::audio::{chapter_filename, export_chapter_mp3, relabel_chapter_mp3};
use audiobookify_core::media;
use audiobookify_core::voice_audio::{reference_clip, reference_pace, validate_reference_audio, Pace};
use audiobookify_core::voice_storage::{delete_voice_storage, permanent_voice_dir};
use common::*;
use serde_json::json;
use tempfile::TempDir;

#[test]
fn a_clean_clip_has_no_warnings() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("clean.wav");
    clean_tone_wav(&path);

    let info = validate_reference_audio(&path).unwrap();

    assert_eq!((info.sample_rate, info.channels), (22050, 1));
    assert!((info.duration_seconds - 15.0).abs() < 1e-9);
    assert_eq!(info.warnings, Vec::<String>::new());
}

#[test]
fn a_poor_clip_gets_every_warning_python_gives() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("loud.wav");
    wav(&path, 8000, 2, 5 * 8000, |_| 1.0);

    let info = validate_reference_audio(&path).unwrap();

    assert_eq!(info.warnings, vec![
        "reference audio sample rate is 8000 Hz, below the recommended 16000 Hz minimum",
        "reference audio is 5.0s, shorter than the recommended 10-30s window",
        "reference audio has 2 channels; mono is recommended",
        "possible clipping detected (100.00% of samples at peak amplitude)",
        "no quiet passages detected; recording may have constant background noise",
    ]);
}

#[test]
fn a_long_clip_is_warned_about() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("long.wav");
    wav(&path, 16000, 1, 31 * 16000, |i| if i % 2000 < 1000 { 0.3 } else { 0.0 });
    assert_eq!(validate_reference_audio(&path).unwrap().warnings,
               vec!["reference audio is 31.0s, longer than the recommended 10-30s window"]);
}

#[test]
fn too_short_and_unreadable_clips_are_errors() {
    let dir = TempDir::new().unwrap();
    let short = dir.path().join("short.wav");
    wav(&short, 16000, 1, 8000, |_| 0.1);
    assert_eq!(validate_reference_audio(&short).unwrap_err(),
               "reference audio is too short to clone from (0.50s); need at least 1s");

    let junk = dir.path().join("junk.wav");
    fs::write(&junk, b"nope").unwrap();
    let err = validate_reference_audio(&junk).unwrap_err();
    assert!(err.starts_with(&format!("could not read reference audio '{}': ", junk.display())), "{err}");
}

#[test]
fn pace_uses_the_words_engines_hear() {
    let words = json!([
        {"word": "Hello.", "start": 0.0, "end": 0.5},
        {"word": "there", "start": 1.0, "end": 1.5},
        {"word": "friend", "start": 1.5, "end": 2.0},
    ]);
    assert_eq!(reference_pace(Some(&words), None), Some(Pace { speaking_wpm: 120.0, overall_wpm: 90.0 }));
    assert_eq!(reference_pace(Some(&words), Some(1.5)), Some(Pace { speaking_wpm: 120.0, overall_wpm: 80.0 }));
    assert_eq!(reference_pace(Some(&words), Some(0.5)), None);
    assert_eq!(reference_pace(None, None), None);
}

#[test]
fn reference_clip_trims_then_changes_tempo_and_caches_both() {
    let dir = TempDir::new().unwrap();
    let reference = dir.path().join("reference.wav");
    wav(&reference, 16000, 2, 3 * 16000, |i| ((i % 100) as f32 / 100.0) - 0.5);

    assert_eq!(reference_clip(&reference, None, 1.0).unwrap(), reference);

    let trimmed = reference_clip(&reference, Some(1.0), 1.0).unwrap();
    assert_eq!(trimmed, dir.path().join("reference.trim-1.00s.wav"));
    let t = media::decode(&trimmed).unwrap();
    assert_eq!((t.sample_rate, t.channels, t.frames()), (16000, 2, 16000));

    let slow = reference_clip(&reference, Some(1.0), 0.8).unwrap();
    assert_eq!(slow, dir.path().join("reference.trim-1.00s.tempo-0.80.wav"));
    assert!((media::duration(&slow).unwrap() - 1.25).abs() < 0.05);

    let written = fs::metadata(&slow).unwrap().modified().unwrap();
    reference_clip(&reference, Some(1.0), 0.8).unwrap();
    assert_eq!(fs::metadata(&slow).unwrap().modified().unwrap(), written);
}

#[test]
fn reference_clip_of_a_missing_file_is_an_error() {
    let dir = TempDir::new().unwrap();
    assert!(reference_clip(&dir.path().join("gone.wav"), Some(1.0), 1.0).is_err());
}

#[test]
fn relabel_chapter_mp3_renames_and_retags() {
    let dir = TempDir::new().unwrap();
    let (path, _) = export_chapter_mp3(&vec![0.0; 2205], 22050, dir.path(), 0, "Old", "Book", "N", None).unwrap();

    let new_path = relabel_chapter_mp3(&path, 4, "New").unwrap();

    assert_eq!(new_path, dir.path().join(chapter_filename(4, "New")));
    assert!(!dir.path().join(chapter_filename(0, "Old")).exists());
    use id3::TagLike;
    let tag = id3::Tag::read_from_path(&new_path).unwrap();
    assert_eq!((tag.title(), tag.track(), tag.album()), (Some("New"), Some(5), Some("Book")));
}

#[test]
fn delete_voice_storage_removes_the_folder_and_ignores_a_missing_one() {
    let dir = TempDir::new().unwrap();
    let voice_dir = permanent_voice_dir(dir.path(), 3, "Nova");
    fs::create_dir_all(&voice_dir).unwrap();
    fs::write(voice_dir.join("reference.wav"), b"x").unwrap();

    delete_voice_storage(dir.path(), 3, "Nova");
    delete_voice_storage(dir.path(), 3, "Nova");

    assert!(!voice_dir.exists());
}

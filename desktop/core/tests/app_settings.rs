mod common;

use std::fs;
use std::path::Path;

use audiobookify_core::app::{settings, voices, AppPaths};
use common::*;
use tempfile::TempDir;

#[test]
fn the_voices_library_defaults_to_the_data_dir() {
    let dir = TempDir::new().unwrap();
    let paths = AppPaths::new(dir.path().join("data"));
    assert_eq!(paths.voices(), dir.path().join("data/voices"));

    let shown = settings::get_settings(&paths);
    assert_eq!(Path::new(&shown.voices_dir), paths.voices());
    assert!(shown.is_default);
}

#[test]
fn a_corrupt_settings_file_falls_back_to_the_default() {
    let dir = TempDir::new().unwrap();
    let root = dir.path().join("data");
    fs::create_dir_all(&root).unwrap();
    fs::write(root.join("settings.json"), "{not json").unwrap();
    assert_eq!(AppPaths::new(&root).voices(), root.join("voices"));
}

#[test]
fn a_new_voices_library_copies_existing_voices_and_persists() {
    let dir = TempDir::new().unwrap();
    let (mut paths, mut db) = app(dir.path());
    let clip = dir.path().join("nova.wav");
    clean_tone_wav(&clip);
    let voice = voices::create_voice(&db, &paths, &clip, "Nova", Some("Hi.")).unwrap();
    let old = db.get_voice_profile_by_id(voice.id).unwrap().unwrap().reference_audio_path;
    let library = dir.path().canonicalize().unwrap().join("Library");

    let change = settings::set_voices_dir(&mut db, &mut paths, Some(library.to_str().unwrap())).unwrap();

    assert_eq!(change.warnings, Vec::<String>::new());
    assert!(!change.settings.is_default);
    assert_eq!(paths.voices(), library);
    let moved = db.get_voice_profile_by_id(voice.id).unwrap().unwrap().reference_audio_path;
    assert!(Path::new(&moved).starts_with(&library), "{moved}");
    assert!(Path::new(&moved).is_file());
    assert!(Path::new(&old).is_file(), "the old copy is left in place");
    // Survives a restart, and reopening the DB doesn't move anything back.
    let reopened = AppPaths::new(&paths.root);
    assert_eq!(reopened.voices(), library);
    let db2 = reopened.open_db().unwrap();
    assert_eq!(db2.get_voice_profile_by_id(voice.id).unwrap().unwrap().reference_audio_path, moved);
}

#[test]
fn resetting_returns_to_the_default_folder() {
    let dir = TempDir::new().unwrap();
    let (mut paths, mut db) = app(dir.path());
    let library = dir.path().join("Library");
    settings::set_voices_dir(&mut db, &mut paths, Some(library.to_str().unwrap())).unwrap();

    let change = settings::set_voices_dir(&mut db, &mut paths, None).unwrap();

    assert!(change.settings.is_default);
    assert_eq!(paths.voices(), paths.root.join("voices"));
    assert_eq!(AppPaths::new(&paths.root).voices(), paths.root.join("voices"));
}

#[test]
fn an_unwritable_folder_is_rejected_and_nothing_changes() {
    let dir = TempDir::new().unwrap();
    let (mut paths, mut db) = app(dir.path());
    let blocker = dir.path().join("a-file");
    fs::write(&blocker, b"x").unwrap();

    let err = api_err(settings::set_voices_dir(&mut db, &mut paths, Some(blocker.join("voices").to_str().unwrap())));

    assert!(err.contains("cannot write"), "{err}");
    assert_eq!(paths.voices(), paths.root.join("voices"));
    assert!(!paths.root.join("settings.json").exists());
}

#![allow(dead_code)] // each test binary uses a different subset

use std::fmt::Debug;
use std::path::{Path, PathBuf};

use audiobookify_core::db::{Database, DbError};
use audiobookify_core::engine::PythonEngine;
use audiobookify_core::models::*;

pub fn open(dir: &Path) -> Database {
    Database::open(dir.join("test.db"), dir.join("voices")).unwrap()
}

pub fn path(dir: &Path, name: &str) -> String {
    dir.join(name).to_string_lossy().into_owned()
}

pub fn raw(title: &str, text: &str) -> RawChapter {
    RawChapter { title: title.into(), text: text.into() }
}

pub fn md_book(db: &Database, dir: &Path, source_path: &str) -> Book {
    db.create_book("Test Book", None, source_path, SourceFormat::Markdown, &path(dir, "out")).unwrap()
}

pub fn book_with_chapter(db: &Database, dir: &Path) -> (Book, Chapter) {
    let book = md_book(db, dir, &path(dir, "b.md"));
    let mut chapters = db.add_chapters(book.id, &[raw("Ch 1", "Body.")]).unwrap();
    (book, chapters.remove(0))
}

pub fn not_found<T: Debug>(result: Result<T, DbError>) -> String {
    match result {
        Err(DbError::NotFound(message)) => message,
        other => panic!("expected NotFound, got {other:?}"),
    }
}

pub fn invalid<T: Debug>(result: Result<T, DbError>) -> String {
    match result {
        Err(DbError::Invalid(message)) => message,
        other => panic!("expected Invalid, got {other:?}"),
    }
}

/// A "chatterbox" voice with default params and no transcript, like most test_db.py voices.
pub fn voice_at(db: &Database, name: &str, reference_audio_path: &str) -> VoiceProfile {
    db.create_voice_profile(&NewVoiceProfile {
        name, engine: "chatterbox", reference_audio_path, reference_transcript: None,
        exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8, transcript_status: None,
    })
    .unwrap()
}

/// test_db.py's `_voice` helper.
pub fn voice(db: &Database, dir: &Path, name: &str, transcript: Option<&str>,
             transcript_status: Option<&str>) -> VoiceProfile {
    db.create_voice_profile(&NewVoiceProfile {
        name, engine: "chatterbox-turbo", reference_audio_path: &path(dir, &format!("{name}.wav")),
        reference_transcript: transcript, exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8,
        transcript_status,
    })
    .unwrap()
}

pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().unwrap()
}

/// The real engine_worker child (repo .venv), for the fake engine, with test knobs set.
pub fn fake_engine(env: &[(&str, &str)]) -> PythonEngine {
    let repo = repo_root();
    env.iter().fold(PythonEngine::new(repo.join(".venv/bin/python"), &repo), |e, (k, v)| e.env(*k, *v))
}

/// A float WAV where every channel of frame `i` is `sample(i)`.
pub fn wav(path: &Path, sample_rate: u32, channels: u16, frames: usize, sample: impl Fn(usize) -> f32) {
    let spec = hound::WavSpec { channels, sample_rate, bits_per_sample: 32, sample_format: hound::SampleFormat::Float };
    let mut w = hound::WavWriter::create(path, spec).unwrap();
    for i in 0..frames {
        for _ in 0..channels {
            w.write_sample(sample(i)).unwrap();
        }
    }
    w.finalize().unwrap();
}

/// test_api.py's `_clean_tone_wav_bytes`: 15 s of a 220 Hz tone with a silent last tenth.
/// Passes `validate_reference_audio` with no warnings.
pub fn clean_tone_wav(path: &Path) {
    let rate = 22050;
    let n = 15 * rate as usize;
    wav(path, rate, 1, n, |i| {
        if i < n * 9 / 10 { 0.6 * (2.0 * std::f32::consts::PI * 220.0 * i as f32 / rate as f32).sin() } else { 0.0 }
    });
}

/// `seconds` of mono silence as an MP3, written by ffmpeg.
pub fn silent_mp3(path: &Path, seconds: f64) {
    let status = std::process::Command::new("ffmpeg")
        .env("PATH", audiobookify_core::audio::child_path(std::env::var_os("PATH")))
        .args(["-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", &seconds.to_string()])
        .arg(path)
        .stdin(std::process::Stdio::null())
        .status()
        .unwrap();
    assert!(status.success());
}

mod common;

use audiobookify_core::media;
use common::*;
use tempfile::TempDir;

#[test]
fn decodes_a_stereo_wav_interleaved() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("a.wav");
    wav(&path, 16000, 2, 24000, |i| if i == 0 { 0.5 } else { 0.0 });

    let d = media::decode(&path).unwrap();

    assert_eq!((d.sample_rate, d.channels, d.frames()), (16000, 2, 24000));
    assert_eq!(&d.samples[..3], &[0.5, 0.5, 0.0]);
    assert!((d.duration() - 1.5).abs() < 1e-9);
}

#[test]
fn mono_averages_the_channels() {
    let d = media::Decoded { samples: vec![1.0, 0.0, 0.5, 0.5], sample_rate: 8000, channels: 2 };
    assert_eq!(d.mono(), vec![0.5, 0.5]);
}

#[test]
fn reads_an_mp3_and_its_duration() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("ch.mp3");
    silent_mp3(&path, 2.0);

    assert!((media::duration(&path).unwrap() - 2.0).abs() < 0.1);
    let d = media::decode(&path).unwrap();
    assert_eq!(d.channels, 1);
    assert!((d.duration() - 2.0).abs() < 0.1);
}

#[test]
fn wav_duration_comes_from_the_header() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("a.wav");
    wav(&path, 22050, 1, 22050 * 3, |_| 0.0);
    assert!((media::duration(&path).unwrap() - 3.0).abs() < 1e-9);
}

#[test]
fn unreadable_files_are_errors() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("x.wav");
    std::fs::write(&path, b"not audio").unwrap();
    assert!(media::decode(&path).is_err());
    assert!(media::duration(&dir.path().join("missing.mp3")).is_err());
}

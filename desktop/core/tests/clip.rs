mod common;

use audiobookify_core::media;
use audiobookify_core::voice_audio::{cut, export_mono_clip, trim};
use common::*;
use tempfile::TempDir;

/// 10 samples at 10 Hz: one second, sample i == i.
fn ramp() -> Vec<f32> {
    (0..10).map(|i| i as f32).collect()
}

#[test]
fn trim_keeps_the_range() {
    assert_eq!(trim(&ramp(), 10, 0.2, 0.5), vec![2.0, 3.0, 4.0]);
}

#[test]
fn trim_clamps_to_the_clip() {
    assert_eq!(trim(&ramp(), 10, -1.0, 5.0), ramp());
    assert_eq!(trim(&ramp(), 10, 0.7, 0.3), Vec::<f32>::new());
    assert_eq!(trim(&[], 10, 0.0, 1.0), Vec::<f32>::new());
}

#[test]
fn cut_removes_the_middle() {
    assert_eq!(cut(&ramp(), 10, 0.2, 0.8), vec![0.0, 1.0, 8.0, 9.0]);
    assert_eq!(cut(&ramp(), 10, 0.5, 9.0), vec![0.0, 1.0, 2.0, 3.0, 4.0]);
    assert_eq!(cut(&ramp(), 10, 0.7, 0.3), ramp());
}

#[test]
fn decoded_mono_averages_channels() {
    let dir = TempDir::new().unwrap();
    let src = dir.path().join("stereo.wav");
    let spec = hound::WavSpec { channels: 2, sample_rate: 8000, bits_per_sample: 32, sample_format: hound::SampleFormat::Float };
    let mut w = hound::WavWriter::create(&src, spec).unwrap();
    for _ in 0..100 {
        w.write_sample(0.6f32).unwrap();
        w.write_sample(0.2f32).unwrap();
    }
    w.finalize().unwrap();

    let mono = media::decode(&src).unwrap().mono();

    assert_eq!(mono.len(), 100);
    assert!(mono.iter().all(|&s| (s - 0.4).abs() < 1e-6));
}

#[test]
fn export_wav_joins_kept_ranges_as_mono_pcm16() {
    let dir = TempDir::new().unwrap();
    let src = dir.path().join("src.wav");
    wav(&src, 8000, 2, 8000 * 4, |i| if i < 8000 { 0.5 } else { 0.0 });
    let dest = dir.path().join("out.wav");

    let written = export_mono_clip(&src, &dest, &[(0.0, 1.0), (2.0, 2.5)]).unwrap();

    assert_eq!(written, dest);
    let spec = hound::WavReader::open(&dest).unwrap().spec();
    assert_eq!((spec.channels, spec.sample_rate, spec.bits_per_sample), (1, 8000, 16));
    let d = media::decode(&dest).unwrap();
    assert_eq!(d.frames(), 12000);
    assert!((d.samples[0] - 0.5).abs() < 1e-3);
    assert!(d.samples[11999].abs() < 1e-3);
}

#[test]
fn export_mp3_is_mono() {
    let dir = TempDir::new().unwrap();
    let src = dir.path().join("src.wav");
    wav(&src, 22050, 2, 22050 * 3, |i| 0.3 * (i as f32 * 0.05).sin());
    let dest = dir.path().join("out.mp3");

    export_mono_clip(&src, &dest, &[(0.5, 2.5)]).unwrap();

    let d = media::decode(&dest).unwrap();
    assert_eq!(d.channels, 1);
    assert!((d.duration() - 2.0).abs() < 0.1, "duration {}", d.duration());
}

#[test]
fn export_rejects_unknown_extension() {
    let dir = TempDir::new().unwrap();
    let src = dir.path().join("src.wav");
    clean_tone_wav(&src);

    let err = export_mono_clip(&src, &dir.path().join("out.ogg"), &[(0.0, 1.0)]).unwrap_err();

    assert!(err.contains("ogg"), "{err}");
}

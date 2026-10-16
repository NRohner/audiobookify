use std::path::Path;

use audiobookify_core::audio::*;
use audiobookify_core::text::EndsWith;
use serde_json::Value;
use tempfile::TempDir;

const SR: u32 = 1000;

fn speech(n: usize, amp: f32) -> Vec<f32> {
    (0..n).map(|i| amp * (i as f32 * 0.3).sin()).collect()
}

fn padded(n_speech: usize, n_pad: usize, amp: f32) -> Vec<f32> {
    let mut v = vec![0.0; n_pad];
    v.extend(speech(n_speech, amp));
    v.extend(vec![0.0; n_pad]);
    v
}

fn peak(a: &[f32]) -> f32 {
    a.iter().fold(0.0, |m, x| m.max(x.abs()))
}

fn ends(names: &[Value]) -> Vec<EndsWith> {
    names.iter().map(|n| match n.as_str().unwrap() {
        "word" => EndsWith::Word, "clause" => EndsWith::Clause, "sentence" => EndsWith::Sentence,
        "paragraph" => EndsWith::Paragraph, "scene" => EndsWith::Scene, other => panic!("{other}"),
    }).collect()
}

fn floats(v: &Value) -> Vec<f32> {
    v.as_array().unwrap().iter().map(|x| x.as_f64().unwrap() as f32).collect()
}

fn chunks(v: &Value) -> Vec<Vec<f32>> {
    v.as_array().unwrap().iter().map(floats).collect()
}

fn assert_close(got: &[f32], want: &[f32], what: &str) {
    assert_eq!(got.len(), want.len(), "{what}: length");
    for (i, (g, w)) in got.iter().zip(want).enumerate() {
        assert!((g - w).abs() <= 1e-6, "{what}: sample {i}: {g} vs {w}");
    }
}

fn golden() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/audio/golden.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn trim_silence_matches_python() {
    for (i, case) in golden()["trim"].as_array().unwrap().iter().enumerate() {
        assert_close(&trim_silence(&floats(&case["input"]), SR), &floats(&case["output"]), &format!("trim {i}"));
    }
}

#[test]
fn level_chunks_matches_python() {
    for (i, case) in golden()["level"].as_array().unwrap().iter().enumerate() {
        let got = level_chunks(chunks(&case["input"]));
        for (j, (g, w)) in got.iter().zip(chunks(&case["output"])).enumerate() {
            assert_close(g, &w, &format!("level {i}.{j}"));
        }
    }
}

#[test]
fn concatenate_chunks_matches_python() {
    for (i, case) in golden()["concat"].as_array().unwrap().iter().enumerate() {
        let got = concatenate_chunks(chunks(&case["input"]), &ends(case["ends_with"].as_array().unwrap()), SR);
        assert_close(&got, &floats(&case["output"]), &format!("concat {i}"));
    }
}

#[test]
fn trim_silence_removes_padding_but_keeps_speech_and_a_small_margin() {
    let trimmed = trim_silence(&padded(500, 400, 0.5), SR);
    let margin = (SR as f64 * 0.03) as usize;
    assert!((500..=500 + 2 * margin + 2 * 10).contains(&trimmed.len()), "{}", trimmed.len());
}

#[test]
fn trim_silence_fades_edges_to_zero() {
    let trimmed = trim_silence(&speech(500, 0.5), SR);
    assert_eq!((trimmed[0], *trimmed.last().unwrap()), (0.0, 0.0));
}

#[test]
fn trim_silence_leaves_an_all_silent_chunk_alone() {
    assert_eq!(trim_silence(&[0.0; 300], SR), vec![0.0; 300]);
}

#[test]
fn level_chunks_nudges_an_outlier_toward_the_median_capped_at_6_db() {
    let quiet = speech(500, 0.05);
    let out = level_chunks(vec![speech(500, 0.5), speech(500, 0.5), quiet.clone(), speech(500, 0.5)]);
    let gain_db = 20.0 * (peak(&out[2]) as f64 / peak(&quiet) as f64).log10();
    assert!((gain_db - 6.0).abs() < 0.01, "{gain_db}");
    assert_eq!(out[0], speech(500, 0.5));
}

#[test]
fn level_chunks_never_clips() {
    let mut spiky = speech(500, 0.05);
    spiky[250] = 0.95;
    let out = level_chunks(vec![speech(500, 0.5), speech(500, 0.5), spiky]);
    assert!(peak(&out[2]) <= 0.99);
}

#[test]
fn concatenate_chunks_pauses_by_boundary_type() {
    let out = concatenate_chunks(vec![speech(200, 0.5); 3],
                                 &[EndsWith::Sentence, EndsWith::Paragraph, EndsWith::Paragraph], SR);
    assert_eq!(out.len(), 600 + 350 + 750);
}

#[test]
fn concatenate_chunks_handles_empty_input() {
    assert!(concatenate_chunks(vec![], &[], SR).is_empty());
}

#[test]
fn pause_table_matches_python() {
    let got: Vec<f64> = [EndsWith::Word, EndsWith::Clause, EndsWith::Sentence, EndsWith::Paragraph, EndsWith::Scene]
        .into_iter().map(pause_seconds).collect();
    assert_eq!(got, vec![0.1, 0.2, 0.35, 0.75, 1.5]);
}

#[test]
fn wav_round_trips_float32_exactly() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("a.wav");
    let audio = speech(1000, 0.7);
    write_wav(&path, &audio, 24000).unwrap();
    assert_eq!(read_wav(&path).unwrap(), (audio, 24000));
}

#[test]
fn read_wav_reads_16_bit_pcm_as_floats() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("a.wav");
    let spec = hound::WavSpec { channels: 1, sample_rate: 16000, bits_per_sample: 16,
                                sample_format: hound::SampleFormat::Int };
    let mut w = hound::WavWriter::create(&path, spec).unwrap();
    for s in [0i16, 16384, -32768] {
        w.write_sample(s).unwrap();
    }
    w.finalize().unwrap();
    assert_eq!(read_wav(&path).unwrap(), (vec![0.0, 0.5, -1.0], 16000));
}

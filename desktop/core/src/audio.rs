//! Audio post-processing, ported from src/audiobookify/postprocess/audio.py.

use std::io;
use std::path::Path;

use crate::text::EndsWith;

/// Silence inserted after a chunk, by why the chunk ended (see text::Chunk).
pub fn pause_seconds(ends_with: EndsWith) -> f64 {
    match ends_with {
        EndsWith::Word => 0.1,
        EndsWith::Clause => 0.2,
        EndsWith::Sentence => 0.35,
        EndsWith::Paragraph => 0.75,
        EndsWith::Scene => 1.5,
    }
}

const FRAME_SECONDS: f64 = 0.01;
const SILENCE_BELOW_PEAK_DB: f64 = 40.0;
const KEEP_MARGIN_SECONDS: f64 = 0.03; // keeps breaths and soft consonant onsets
const FADE_SECONDS: f64 = 0.01;
const LEVEL_TOLERANCE_DB: f64 = 3.0;
const MAX_LEVEL_GAIN_DB: f64 = 6.0;
const PEAK_CEILING: f64 = 0.99;

fn peak(audio: &[f32]) -> f32 {
    audio.iter().fold(0.0, |m, x| m.max(x.abs()))
}

/// RMS in f64, as numpy computes it for these float32 arrays.
fn rms(audio: &[f32]) -> f64 {
    (audio.iter().map(|&x| (x as f64) * (x as f64)).sum::<f64>() / audio.len() as f64).sqrt()
}

/// Cut the model's own leading/trailing silence (so pauses come only from
/// pause_seconds) and fade the edges so joins don't click.
pub fn trim_silence(audio: &[f32], sample_rate: u32) -> Vec<f32> {
    let peak = peak(audio) as f64;
    let frame = ((sample_rate as f64 * FRAME_SECONDS) as usize).max(1);
    let n_frames = audio.len() / frame;
    if peak == 0.0 || n_frames == 0 {
        return audio.to_vec();
    }
    let threshold = peak * 10f64.powf(-SILENCE_BELOW_PEAK_DB / 20.0);
    let mut loud = (0..n_frames).filter(|&i| rms(&audio[i * frame..(i + 1) * frame]) > threshold);
    let Some(first) = loud.next() else {
        return audio.to_vec();
    };
    let last = loud.last().unwrap_or(first);
    let margin = (sample_rate as f64 * KEEP_MARGIN_SECONDS) as usize;
    let start = (first * frame).saturating_sub(margin);
    let end = ((last + 1) * frame + margin).min(audio.len());
    let mut out = audio[start..end].to_vec();
    let fade = ((sample_rate as f64 * FADE_SECONDS) as usize).min(out.len() / 2);
    let n = out.len();
    for i in 0..fade {
        // np.linspace(0.0, 1.0, fade, dtype=float32): i * step in f64, last value exactly 1.
        let r = if i + 1 == fade { 1.0 } else { (i as f64 * (1.0 / (fade - 1) as f64)) as f32 };
        out[i] *= r;
        out[n - 1 - i] *= r;
    }
    out
}

fn level_db(audio: &[f32]) -> Option<f64> {
    if audio.is_empty() {
        return None;
    }
    let r = rms(audio);
    (r > 0.0).then(|| 20.0 * r.log10())
}

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let mid = values.len() / 2;
    if values.len() % 2 == 1 { values[mid] } else { (values[mid - 1] + values[mid]) / 2.0 }
}

/// Nudge chunks more than LEVEL_TOLERANCE_DB off the median level toward it
/// (capped, never clipping). The overall level is left as the model made it.
pub fn level_chunks(chunks: Vec<Vec<f32>>) -> Vec<Vec<f32>> {
    let levels: Vec<Option<f64>> = chunks.iter().map(|c| level_db(c)).collect();
    let mut known: Vec<f64> = levels.iter().flatten().copied().collect();
    if known.is_empty() {
        return chunks;
    }
    let median = median(&mut known);
    chunks.into_iter().zip(levels).map(|(chunk, level)| match level {
        Some(level) if (median - level).abs() > LEVEL_TOLERANCE_DB => {
            let gain = 10f64.powf((median - level).clamp(-MAX_LEVEL_GAIN_DB, MAX_LEVEL_GAIN_DB) / 20.0)
                .min(PEAK_CEILING / peak(&chunk) as f64);
            // numpy 2 multiplies a float32 array by a Python float in float32.
            let gain = gain as f32;
            chunk.iter().map(|&x| x * gain).collect()
        }
        _ => chunk,
    }).collect()
}

pub fn concatenate_chunks(chunks: Vec<Vec<f32>>, ends_with: &[EndsWith], sample_rate: u32) -> Vec<f32> {
    let chunks = level_chunks(chunks.iter().map(|c| trim_silence(c, sample_rate)).collect());
    let last = chunks.len().saturating_sub(1);
    let mut out = Vec::new();
    for (i, chunk) in chunks.into_iter().enumerate() {
        out.extend(chunk);
        if i < last {
            let pause = (sample_rate as f64 * pause_seconds(ends_with[i])) as usize;
            out.resize(out.len() + pause, 0.0);
        }
    }
    out
}

/// Mono float32 WAV.
pub fn write_wav(path: &Path, audio: &[f32], sample_rate: u32) -> io::Result<()> {
    let spec = hound::WavSpec { channels: 1, sample_rate, bits_per_sample: 32,
                                sample_format: hound::SampleFormat::Float };
    let mut w = hound::WavWriter::create(path, spec).map_err(io::Error::other)?;
    for &s in audio {
        w.write_sample(s).map_err(io::Error::other)?;
    }
    w.finalize().map_err(io::Error::other)
}

/// A mono WAV (float32 or 16-bit int) as floats in [-1, 1].
pub fn read_wav(path: &Path) -> io::Result<(Vec<f32>, u32)> {
    let mut r = hound::WavReader::open(path).map_err(io::Error::other)?;
    let spec = r.spec();
    if spec.channels != 1 {
        return Err(io::Error::other(format!("{}: expected mono, got {} channels", path.display(), spec.channels)));
    }
    let samples = match (spec.sample_format, spec.bits_per_sample) {
        (hound::SampleFormat::Float, 32) => r.samples::<f32>().collect::<Result<Vec<_>, _>>(),
        (hound::SampleFormat::Int, 16) => r.samples::<i16>().map(|s| s.map(|s| s as f32 / 32768.0)).collect(),
        (format, bits) => return Err(io::Error::other(format!("{}: unsupported WAV {format:?} {bits}-bit", path.display()))),
    };
    Ok((samples.map_err(io::Error::other)?, spec.sample_rate))
}

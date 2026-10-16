//! Audio post-processing, ported from src/audiobookify/postprocess/audio.py.

use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use crate::text::EndsWith;
use crate::voice_storage::sanitize_filename;

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

/// Homebrew dirs first, then the existing PATH (only when present and non-empty, so no trailing `:`).
/// ponytail: hardcoded for this personal-Macs-only build; a Finder-launched .app only has
/// /usr/bin:/bin:/usr/sbin:/sbin. A shareable build would bundle ffmpeg.
pub fn child_path(existing: Option<OsString>) -> OsString {
    let mut p = OsString::from("/opt/homebrew/bin:/usr/local/bin");
    if let Some(e) = existing.filter(|e| !e.is_empty()) {
        p.push(":");
        p.push(e);
    }
    p
}

pub fn chapter_filename(order_index: i64, title: &str) -> String {
    format!("{:02} - {}.mp3", order_index + 1, sanitize_filename(title))
}

#[derive(Debug, Clone, Copy, Default)]
pub struct Tags<'a> {
    pub title: Option<&'a str>,
    pub album: Option<&'a str>,
    pub track: Option<u32>,
    pub artist: Option<&'a str>,
}

#[derive(Debug, thiserror::Error)]
pub enum AudioError {
    /// Writing failed (e.g. an unwritable output dir); the only error that tries the fallback dir.
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error("ffmpeg failed: {0}")]
    Ffmpeg(String),
    #[error("could not write MP3 tags: {0}")]
    Tag(String),
}

fn encode_mp3(audio: &[f32], sample_rate: u32, mp3_path: &Path, tags: &Tags) -> Result<(), AudioError> {
    if let Some(dir) = mp3_path.parent() {
        fs::create_dir_all(dir)?;
    }
    let wav = mp3_path.with_extension("tmp.wav");
    write_wav(&wav, audio, sample_rate)?;
    let out = Command::new("ffmpeg")
        .env("PATH", child_path(std::env::var_os("PATH")))
        .args(["-y", "-loglevel", "error", "-i"]).arg(&wav)
        // Mono out: ffmpeg reads hound's float WAV header as stereo, which put each channel 3 dB down.
        .args(["-ac", "1", "-codec:a", "libmp3lame", "-qscale:a", "2"]).arg(mp3_path)
        .stdin(Stdio::null()) // never a closed fd 0: ffmpeg would open its output there
        .output();
    let _ = fs::remove_file(&wav);
    let out = out.map_err(|e| AudioError::Ffmpeg(format!("could not run ffmpeg: {e}")))?;
    if !out.status.success() {
        return Err(AudioError::Ffmpeg(String::from_utf8_lossy(&out.stderr).trim().to_string()));
    }
    write_tags(mp3_path, tags)
}

fn write_tags(path: &Path, tags: &Tags) -> Result<(), AudioError> {
    use id3::{Tag, TagLike, Version};
    let mut tag = Tag::read_from_path(path).unwrap_or_else(|_| Tag::new());
    if let Some(t) = tags.title { tag.set_title(t); }
    if let Some(a) = tags.album { tag.set_album(a); }
    if let Some(n) = tags.track { tag.set_track(n); }
    if let Some(a) = tags.artist { tag.set_artist(a); }
    tag.write_to_path(path, Version::Id3v24).map_err(|e| AudioError::Tag(e.to_string()))
}

/// Encode to `output_path`; on a write error retry once as `<fallback_dir>/<same name>`.
/// Returns the path written and whether the fallback was used.
pub fn export_mp3(audio: &[f32], sample_rate: u32, output_path: &Path, tags: &Tags,
                  fallback_dir: Option<&Path>) -> Result<(PathBuf, bool), AudioError> {
    match encode_mp3(audio, sample_rate, output_path, tags) {
        Ok(()) => Ok((output_path.to_path_buf(), false)),
        Err(AudioError::Io(e)) => {
            let Some(dir) = fallback_dir else { return Err(AudioError::Io(e)) };
            let path = dir.join(output_path.file_name().expect("output path has a file name"));
            encode_mp3(audio, sample_rate, &path, tags)?;
            Ok((path, true))
        }
        Err(e) => Err(e),
    }
}

#[allow(clippy::too_many_arguments)]
pub fn export_chapter_mp3(audio: &[f32], sample_rate: u32, output_dir: &Path, order_index: i64, chapter_title: &str,
                          book_title: &str, narrator: &str, fallback_dir: Option<&Path>)
                          -> Result<(PathBuf, bool), AudioError> {
    let tags = Tags { title: Some(chapter_title), album: Some(book_title), track: Some((order_index + 1) as u32),
                      artist: Some(narrator) };
    export_mp3(audio, sample_rate, &output_dir.join(chapter_filename(order_index, chapter_title)), &tags, fallback_dir)
}

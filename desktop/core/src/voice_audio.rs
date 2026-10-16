//! Checks and previews of a voice's reference clip, ported from voice/validation.py and
//! the API-facing parts of voice/analysis.py.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde::Serialize;

use crate::audio::child_path;
use crate::engine::Word;
use crate::media;

pub const TEMPO_RANGE: (f64, f64) = (0.75, 1.25);

const ABSOLUTE_MIN_SECONDS: f64 = 1.0;
const RECOMMENDED_MIN_SECONDS: f64 = 10.0;
const RECOMMENDED_MAX_SECONDS: f64 = 30.0;
const RECOMMENDED_MIN_SAMPLE_RATE: u32 = 16000;
const CLIP_AMPLITUDE: f32 = 0.999;
const CLIP_FRACTION_WARN: f64 = 0.001;
const FRAME_SECONDS: f64 = 0.02;
const SILENCE_RMS_THRESHOLD: f64 = 0.02;
const SILENCE_FRACTION_MIN: f64 = 0.02;
const PAUSE_MIN_SECONDS: f64 = 0.15;

#[derive(Debug)]
pub struct ReferenceInfo {
    pub sample_rate: u32,
    pub channels: usize,
    pub duration_seconds: f64,
    pub warnings: Vec<String>,
}

fn quiet_frame_fraction(mono: &[f32], sample_rate: u32) -> f64 {
    let frame_len = ((FRAME_SECONDS * sample_rate as f64) as usize).max(1);
    let n_frames = mono.len() / frame_len;
    if n_frames == 0 {
        return 1.0; // too short to frame - don't false-alarm on noise
    }
    let quiet = mono.chunks_exact(frame_len).filter(|frame| {
        let mean_square = frame.iter().map(|&x| f64::from(x) * f64::from(x)).sum::<f64>() / frame_len as f64;
        mean_square.sqrt() < SILENCE_RMS_THRESHOLD
    });
    quiet.count() as f64 / n_frames as f64
}

/// Too short or unreadable is an error; everything else that could hurt cloning is a warning.
pub fn validate_reference_audio(path: &Path) -> Result<ReferenceInfo, String> {
    let d = media::decode(path).map_err(|e| format!("could not read reference audio '{}': {e}", path.display()))?;
    let duration = d.duration();
    if duration < ABSOLUTE_MIN_SECONDS {
        return Err(format!(
            "reference audio is too short to clone from ({duration:.2}s); need at least {ABSOLUTE_MIN_SECONDS:.0}s"
        ));
    }
    let mut warnings = Vec::new();
    if d.sample_rate < RECOMMENDED_MIN_SAMPLE_RATE {
        warnings.push(format!(
            "reference audio sample rate is {} Hz, below the recommended {RECOMMENDED_MIN_SAMPLE_RATE} Hz minimum",
            d.sample_rate
        ));
    }
    let window = format!("{RECOMMENDED_MIN_SECONDS:.0}-{RECOMMENDED_MAX_SECONDS:.0}s window");
    if duration < RECOMMENDED_MIN_SECONDS {
        warnings.push(format!("reference audio is {duration:.1}s, shorter than the recommended {window}"));
    } else if duration > RECOMMENDED_MAX_SECONDS {
        warnings.push(format!("reference audio is {duration:.1}s, longer than the recommended {window}"));
    }
    if d.channels > 1 {
        warnings.push(format!("reference audio has {} channels; mono is recommended", d.channels));
    }
    let mono = d.mono();
    let clipped = mono.iter().filter(|x| x.abs() >= CLIP_AMPLITUDE).count() as f64 / mono.len() as f64;
    if clipped > CLIP_FRACTION_WARN {
        warnings.push(format!("possible clipping detected ({:.2}% of samples at peak amplitude)", clipped * 100.0));
    }
    if quiet_frame_fraction(&mono, d.sample_rate) < SILENCE_FRACTION_MIN {
        warnings.push("no quiet passages detected; recording may have constant background noise".into());
    }
    Ok(ReferenceInfo { sample_rate: d.sample_rate, channels: d.channels, duration_seconds: duration, warnings })
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Pace {
    pub speaking_wpm: f64,
    pub overall_wpm: f64,
}

/// Words per minute in the part of the clip engines hear (words ending by `trim_end`).
/// `speaking_wpm` leaves out pauses of 0.15 s or more. None with fewer than two words.
pub fn reference_pace(words: Option<&serde_json::Value>, trim_end: Option<f64>) -> Option<Pace> {
    let words: Vec<Word> = serde_json::from_value(words?.clone()).ok()?;
    let kept: Vec<&Word> = words.iter().filter(|w| trim_end.is_none_or(|t| w.end <= t)).collect();
    if kept.len() < 2 {
        return None;
    }
    let pauses: f64 = kept
        .windows(2)
        .map(|pair| ((pair[1].start - pair[0].end) * 1000.0).round() / 1000.0)
        .filter(|&gap| gap >= PAUSE_MIN_SECONDS)
        .sum();
    let span = kept[kept.len() - 1].end - kept[0].start;
    let n = kept.len() as f64;
    Some(Pace { speaking_wpm: n / (span - pauses) * 60.0, overall_wpm: n / span * 60.0 })
}

fn stem(path: &Path) -> String {
    path.file_stem().unwrap_or_default().to_string_lossy().into_owned()
}

fn hidden_twin(path: &Path) -> PathBuf {
    path.with_file_name(format!(".{}", path.file_name().unwrap_or_default().to_string_lossy()))
}

/// soundfile's default WAV subtype is PCM_16. Matching it means the Python engine worker
/// (which makes the same trim, same name) and this write the same cached file.
fn write_pcm16(path: &Path, samples: &[f32], sample_rate: u32, channels: usize) -> Result<(), String> {
    let spec = hound::WavSpec {
        channels: channels as u16, sample_rate, bits_per_sample: 16, sample_format: hound::SampleFormat::Int,
    };
    let tmp = hidden_twin(path); // never leave a half-written clip under the cached name
    let mut w = hound::WavWriter::create(&tmp, spec).map_err(|e| e.to_string())?;
    for &s in samples {
        w.write_sample((s.clamp(-1.0, 1.0) * 32767.0).round() as i16).map_err(|e| e.to_string())?;
    }
    w.finalize().map_err(|e| e.to_string())?;
    fs::rename(&tmp, path).map_err(|e| e.to_string())
}

/// The clip engines clone from: the reference cut at `trim_end`, at `tempo`. Both steps
/// are cached next to the reference under the names voice/analysis.py uses.
pub fn reference_clip(reference: &Path, trim_end: Option<f64>, tempo: f64) -> Result<PathBuf, String> {
    let mut clip = reference.to_path_buf();
    if let Some(end) = trim_end {
        clip = reference.with_file_name(format!("{}.trim-{end:.2}s.wav", stem(reference)));
        if !clip.is_file() {
            let d = media::decode(reference).map_err(|e| e.to_string())?;
            let keep = ((end * d.sample_rate as f64).round() as usize * d.channels).min(d.samples.len());
            write_pcm16(&clip, &d.samples[..keep], d.sample_rate, d.channels)?;
        }
    }
    if (tempo * 100.0).round() == 100.0 {
        return Ok(clip);
    }
    let out = clip.with_file_name(format!("{}.tempo-{tempo:.2}.wav", stem(&clip)));
    if !out.is_file() {
        let tmp = hidden_twin(&out);
        let result = Command::new("ffmpeg")
            .env("PATH", child_path(std::env::var_os("PATH")))
            .args(["-y", "-loglevel", "error", "-i"])
            .arg(&clip)
            .args(["-filter:a", &format!("atempo={tempo:.2}")])
            .arg(&tmp)
            .stdin(Stdio::null())
            .output()
            .map_err(|e| format!("could not run ffmpeg: {e}"))?;
        if !result.status.success() {
            return Err(format!("ffmpeg failed: {}", String::from_utf8_lossy(&result.stderr).trim()));
        }
        fs::rename(&tmp, &out).map_err(|e| e.to_string())?;
    }
    Ok(out)
}

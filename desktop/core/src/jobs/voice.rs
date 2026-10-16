use std::sync::LazyLock;

use regex::Regex;

use crate::engine::{Voice, Word};
use crate::models::VoiceProfile;
use crate::pystr;

/// Qwen3 encodes the whole reference per chunk; 36 s ran ~15% slower than 15 s.
pub const MAX_REFERENCE_SECONDS: f64 = 15.0;
const SENTENCE_WINDOW_SECONDS: f64 = 5.0; // cut at a sentence end if one falls this close to the limit
const TRAIL_SECONDS: f64 = 0.15; // air kept after the last word
static SENTENCE_END_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"[.!?…]["'”’)\]]*$"#).unwrap());

/// (end_seconds, transcript_of_kept_words) for a clip longer than max_seconds, cut at a
/// word boundary (a sentence end if one is near the limit); None if the clip already fits.
/// Ported from the former Python trim_point.
pub fn trim_point(words: &[Word], max_seconds: f64) -> Option<(f64, String)> {
    if words.last()?.end <= max_seconds {
        return None;
    }
    let fits: Vec<usize> = (0..words.len()).filter(|&i| words[i].end <= max_seconds).collect();
    let mut last = *fits.last()?; // ponytail: one word longer than the limit; use the clip whole
    if let Some(&s) = fits.iter().rev().find(|&&i| {
        SENTENCE_END_RE.is_match(pystr::strip(&words[i].word)) && words[i].end >= max_seconds - SENTENCE_WINDOW_SECONDS
    }) {
        last = s;
    }
    // `last` < len - 1: the final word ends past max_seconds, so it isn't in `fits`.
    let end = (words[last].end + TRAIL_SECONDS).min(words[last + 1].start);
    let text: String = words[..=last].iter().map(|w| w.word.as_str()).collect();
    Some((end, pystr::strip(&text).to_string()))
}

/// What a job's engine clones from; `tempo_override` is a text clip's unsaved sample tempo.
pub fn voice_for(profile: Option<&VoiceProfile>, tempo_override: Option<f64>) -> Voice {
    let Some(p) = profile else { return Voice::default() };
    Voice {
        reference_path: Some(p.reference_audio_path.clone()),
        reference_text: p.reference_transcript.clone(),
        trim_end: p.reference_trim_end,
        tempo: Some(tempo_override.unwrap_or(p.tempo)),
        exaggeration: Some(p.exaggeration),
        cfg: Some(p.cfg_weight),
        temperature: Some(p.temperature),
    }
}

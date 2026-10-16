use audiobookify_core::engine::{Voice, Word};
use audiobookify_core::jobs::{trim_point, voice_for};
use audiobookify_core::models::VoiceProfile;

fn w(word: &str, start: f64, end: f64) -> Word {
    Word { word: word.into(), start, end }
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

#[test]
fn keeps_a_short_clip_whole() {
    assert_eq!(trim_point(&[w(" One", 0.0, 5.0), w(" two.", 5.2, 14.9)], 15.0), None);
}

#[test]
fn cuts_after_the_last_whole_word_before_the_limit() {
    let (end, text) = trim_point(&[w(" The", 0.0, 0.3), w(" quick", 0.4, 7.0), w(" fox", 7.1, 14.8),
                                   w(" ran.", 15.0, 15.6)], 15.0).unwrap();
    assert!(close(end, 14.8 + 0.15), "{end}");
    assert_eq!(text, "The quick fox");
}

#[test]
fn never_cuts_into_the_next_word() {
    let (end, _) = trim_point(&[w(" a", 0.0, 14.0), w(" b", 14.05, 16.0)], 15.0).unwrap();
    assert!(close(end, 14.05), "{end}");
}

#[test]
fn prefers_a_sentence_end_close_to_the_limit() {
    let (end, text) = trim_point(&[w(" One", 0.0, 1.0), w(" two.", 1.1, 11.5), w(" Three", 11.7, 13.0),
                                   w(" four", 13.1, 14.9), w(" five", 15.0, 16.0)], 15.0).unwrap();
    assert_eq!(text, "One two.");
    assert!(close(end, 11.65), "{end}");
}

#[test]
fn a_single_word_longer_than_the_limit_keeps_the_clip_whole() {
    assert_eq!(trim_point(&[w(" Hmm", 0.0, 16.0), w(" yes", 16.1, 17.0)], 15.0), None);
}

fn profile() -> VoiceProfile {
    VoiceProfile {
        id: 1, name: "Nova".into(), engine: "chatterbox-turbo".into(), reference_audio_path: "/v/ref.wav".into(),
        reference_transcript: Some("One two.".into()), exaggeration: 0.5, cfg_weight: 0.4, temperature: 0.8,
        created_at: 0.0, transcript_status: Some("done".into()), transcript_error: None, reference_words: None,
        reference_trim_end: Some(14.15), tempo: 1.1,
    }
}

#[test]
fn voice_for_maps_the_profile_and_lets_a_clip_override_the_tempo() {
    let v = voice_for(Some(&profile()), None);
    assert_eq!(v, Voice {
        reference_path: Some("/v/ref.wav".into()), reference_text: Some("One two.".into()), trim_end: Some(14.15),
        tempo: Some(1.1), exaggeration: Some(0.5), cfg: Some(0.4), temperature: Some(0.8),
    });
    assert_eq!(voice_for(Some(&profile()), Some(0.9)).tempo, Some(0.9));
    assert_eq!(voice_for(None, Some(0.9)), Voice::default());
}

// Reference-pace display logic for the Voices page, kept out of the components so it is testable.

export const TEMPO_MIN = 0.75; // matches TEMPO_RANGE in desktop/core/src/voice_audio.rs
export const TEMPO_MAX = 1.25;
// The user's favourite narration came from a reference at about this pace.
export const SWEET_SPOT = { overall: 174, speaking: 188 };

const clamp = (t) => Math.min(TEMPO_MAX, Math.max(TEMPO_MIN, t));

// The reference clip's pace once played at this tempo (exact for the reference;
// the narration follows only part of a change).
export function pacedWpm(pace, tempo) {
  if (!pace) return null;
  return { speaking: Math.round(pace.speaking_wpm * tempo), overall: Math.round(pace.overall_wpm * tempo) };
}

export function sweetSpotTempo(pace) {
  if (!pace) return null;
  return clamp(Math.round((SWEET_SPOT.overall / pace.overall_wpm) * 100) / 100);
}

export function tempoPosition(tempo) {
  return Math.round(((tempo - TEMPO_MIN) / (TEMPO_MAX - TEMPO_MIN)) * 100);
}

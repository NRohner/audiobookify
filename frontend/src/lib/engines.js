// Engine choice and voice-transcript display logic, kept out of the components so it is testable.

const LEGACY_LABELS = { chatterbox: "Older Chatterbox" };

export function engineLabel(id, engines) {
  return engines.find((e) => e.id === id)?.label || LEGACY_LABELS[id] || id;
}

// The engine a request will actually use: one that needs a voice can't run without one,
// and one that isn't downloaded can't run at all.
export function effectiveEngine(chosen, engines, hasVoice) {
  const usable = (e) => e.ready !== false && (!e.needs_voice || hasVoice);
  const spec = engines.find((e) => e.id === chosen);
  if (spec && usable(spec)) return chosen;
  return engines.find(usable)?.id ?? chosen;
}

// New books (nothing generated yet) start on Quality; otherwise keep the book's engine.
export function initialBookEngine(book) {
  return book.chapters.some((c) => c.engine) ? book.engine : "qwen3";
}

// A warning when chapters of one book would sound different, or null.
export function engineMismatchWarning(book, chosen, engines) {
  const used = [...new Set(book.chapters.filter((c) => c.status === "done" && c.engine).map((c) => c.engine))];
  if (book.mixed_engines) {
    return `Finished chapters were made with different engines (${used.map((e) => engineLabel(e, engines)).join(", ")}), so they may sound different. Regenerate them to match.`;
  }
  if (used.length === 0 || (used.length === 1 && used[0] === chosen)) return null;
  const pending = book.chapters.some((c) => c.status !== "done");
  if (!pending) return null;
  return `Finished chapters used ${engineLabel(used[0], engines)}. Generating the rest with ${engineLabel(chosen, engines)} will make them sound different.`;
}

export function transcriptState(voice) {
  const status = voice.transcript_status;
  if (status === "pending") return { key: "pending", label: "Transcribing…", note: null };
  if (status === "failed") {
    return { key: "failed", label: `Transcription failed: ${voice.transcript_error || "unknown error"}`, note: null };
  }
  if (!voice.reference_transcript) {
    return { key: "missing", label: "Not transcribed yet. The Quality engine transcribes it on first use.", note: null };
  }
  const note =
    voice.reference_trim_end != null && voice.reference_seconds != null
      ? `Uses the first ${voice.reference_trim_end.toFixed(1)} s of the ${voice.reference_seconds.toFixed(1)} s clip. The transcript covers that part.`
      : null;
  return { key: "done", label: null, note };
}

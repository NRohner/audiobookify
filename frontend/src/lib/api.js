import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { numericIds } from "./ids.js";

// Commands reject with the Rust error's message as a plain string; callers show e.message.
// A few (recording) reject with {kind, message}; e.kind lets the caller branch.
async function call(command, args) {
  try {
    return await invoke(command, numericIds(args));
  } catch (e) {
    throw Object.assign(new Error(typeof e === "string" ? e : e?.message ?? String(e)), e?.kind ? { kind: e.kind } : {});
  }
}

// A command that returns a file path, as a URL an <audio> can play.
const audioSrc = (command, args) => call(command, args).then((path) => convertFileSrc(path));

export const api = {
  listBooks: () => call("list_books"),
  getBook: (id) => call("get_book", { bookId: id }),
  createBook: (path, outputDir) => call("create_book", { path, outputDir }),
  validateOutputDir: (path) => call("validate_output_dir", { path }),
  getWorkerStatus: () => call("worker_status"),
  pauseWorker: () => call("pause_worker"),
  resumeWorker: () => call("resume_worker"),
  listEngines: () => call("list_engines"),
  generateBook: (id, engine) => call("generate_book", { bookId: id, engine }),
  generateChapter: (bookId, chapterId, engine) => call("generate_chapter", { bookId, chapterId, engine }),
  cancelChapter: (bookId, chapterId) => call("cancel_chapter", { bookId, chapterId }),
  cancelBook: (bookId) => call("cancel_book", { bookId }),
  assignVoice: (bookId, voiceProfileId) => call("assign_voice", { bookId, voiceProfileId }),
  chapterAudioSrc: (bookId, chapterId) => audioSrc("chapter_audio_path", { bookId, chapterId }),

  getBookText: (id) => call("get_book_text", { bookId: id }),
  saveChapterBreaks: (id, breaks) =>
    call("save_chapter_breaks", { bookId: id, chapters: breaks.map(({ start, title }) => ({ start, title })) }),

  listVoices: () => call("list_voices"),
  createVoice: (path, name, transcript) =>
    call("create_voice", { path, name, referenceTranscript: transcript || null }),
  deleteBook: (id) => call("delete_book", { bookId: id }),
  deleteVoice: (id) => call("delete_voice", { voiceId: id }),
  updateVoiceTranscript: (id, transcript) => call("set_voice_transcript", { voiceId: id, transcript }),
  setVoiceTempo: (id, tempo) => call("set_voice_tempo", { voiceId: id, tempo }),
  transcribeVoice: (id) => call("transcribe_voice", { voiceId: id }),
  voiceAudioSrc: (id, tempo) => audioSrc("voice_audio_path", { voiceId: id, tempo }),
  // keep: [[startSec, endSec], ...] of the source, in order; dest ends in .wav or .mp3.
  exportMonoClip: (source, dest, keep) => call("export_mono_clip", { source, dest, keep }),
  // The editor's save: clones a voice from the keep ranges of source, as mono.
  createVoiceFromClip: (source, keep, name, transcript) =>
    call("create_voice_from_clip", { source, keep, name, referenceTranscript: transcript || null }),

  listPronunciations: () => call("list_pronunciations"),
  createPronunciation: ({ term, override_value, notes }) =>
    call("create_pronunciation", { term, overrideValue: override_value, notes: notes ?? null }),
  updatePronunciation: (term, overrideValue) => call("update_pronunciation", { term, overrideValue }),
  deletePronunciation: (term) => call("delete_pronunciation", { term }),

  listTextClips: () => call("list_text_clips"),
  createTextClip: (text, voiceProfileId, engine, tempo = null) =>
    call("create_text_clip", { text, voiceProfileId, engine, tempo }),
  getTextClip: (id) => call("get_text_clip", { clipId: id }),
  deleteTextClip: (id) => call("delete_text_clip", { clipId: id }),
  textClipAudioSrc: (id) => audioSrc("text_clip_audio_path", { clipId: id }),
  saveTextClip: (id, dest) => call("save_text_clip", { clipId: id, dest }),

  listModels: () => call("list_models"),
  modelProgress: () => call("model_progress"),
  downloadModel: (model) => call("download_model", { model }),
  cancelModelDownload: (model) => call("cancel_model_download", { model }),
  deleteModel: (model) => call("delete_model", { model }),
  getStats: () => call("get_stats"),
  getSettings: () => call("get_settings"),
  // null moves the library back to the default folder.
  setVoicesDir: (path) => call("set_voices_dir", { path }),

  // System audio to a temp WAV. Rejects with e.kind "permission_denied" when macOS
  // Screen Recording access is off. stopRecording resolves to the WAV's path.
  startRecording: () => call("start_recording"),
  stopRecording: () => call("stop_recording"),
};

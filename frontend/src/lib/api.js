import { convertFileSrc, invoke } from "@tauri-apps/api/core";

// Commands reject with the Rust error's message as a plain string; callers show e.message.
async function call(command, args) {
  try {
    return await invoke(command, args);
  } catch (e) {
    throw new Error(typeof e === "string" ? e : e?.message ?? String(e));
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
};

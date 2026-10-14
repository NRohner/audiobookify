async function request(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(body?.detail || `request failed: ${res.status}`);
  }
  return body;
}

export const api = {
  listBooks: () => request("/api/books"),
  getBook: (id) => request(`/api/books/${id}`),
  createBook: (formData) => request("/api/books", { method: "POST", body: formData }),
  browseDir: (path) => request(`/api/browse-dir${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  validateOutputDir: (path) => request(`/api/validate-output-dir?path=${encodeURIComponent(path)}`),
  getWorkerStatus: () => request("/api/worker/status"),
  pauseWorker: () => request("/api/worker/pause", { method: "POST" }),
  resumeWorker: () => request("/api/worker/resume", { method: "POST" }),
  listEngines: () => request("/api/engines"),
  generateBook: (id, engine) =>
    request(`/api/books/${id}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engine }),
    }),
  generateChapter: (bookId, chapterId, engine) =>
    request(`/api/books/${bookId}/chapters/${chapterId}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engine }),
    }),
  cancelChapter: (bookId, chapterId) =>
    request(`/api/books/${bookId}/chapters/${chapterId}/cancel`, { method: "POST" }),
  cancelBook: (bookId) => request(`/api/books/${bookId}/cancel`, { method: "POST" }),
  assignVoice: (bookId, voiceProfileId) =>
    request(`/api/books/${bookId}/voice`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice_profile_id: voiceProfileId }),
    }),

  listVoices: () => request("/api/voices"),
  createVoice: (formData) => request("/api/voices", { method: "POST", body: formData }),
  deleteVoice: (id) => request(`/api/voices/${id}`, { method: "DELETE" }),
  updateVoiceTranscript: (id, transcript) =>
    request(`/api/voices/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference_transcript: transcript }),
    }),
  setVoiceTempo: (id, tempo) =>
    request(`/api/voices/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tempo }),
    }),
  transcribeVoice: (id) => request(`/api/voices/${id}/transcribe`, { method: "POST" }),

  listPronunciations: () => request("/api/pronunciations"),
  createPronunciation: (payload) =>
    request("/api/pronunciations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  updatePronunciation: (term, overrideValue) =>
    request(`/api/pronunciations/${encodeURIComponent(term)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ override_value: overrideValue }),
    }),
  deletePronunciation: (term) =>
    request(`/api/pronunciations/${encodeURIComponent(term)}`, { method: "DELETE" }),

  listTextClips: () => request("/api/text-clips"),
  createTextClip: (text, voiceProfileId, engine, tempo = null) =>
    request("/api/text-clips", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice_profile_id: voiceProfileId, engine, tempo }),
    }),
  getTextClip: (id) => request(`/api/text-clips/${id}`),
  deleteTextClip: (id) => request(`/api/text-clips/${id}`, { method: "DELETE" }),
};

export function textClipInProgress(clip) {
  return clip.status === "queued" || clip.status === "generating";
}

// A chapter with a queued job still reports status "pending" (chapters only flip to
// "generating" once the worker actually picks the job up, which can take a while - e.g.
// loading/cloning a voice profile for the first time). Treat "pending" + a non-null
// progress (set the moment a job is created) as still in-flight, not idle.
export function chapterInProgress(chapter) {
  return chapter.status === "generating" || (chapter.status === "pending" && chapter.progress != null);
}

export function bookStatus(book) {
  const chapters = book.chapters || [];
  if (chapters.length === 0) return { key: "empty", label: "No chapters" };
  if (chapters.some(chapterInProgress)) {
    const progress =
      chapters.reduce((sum, c) => sum + (c.progress ?? (c.status === "done" ? 1 : 0)), 0) /
      chapters.length;
    return { key: "converting", label: `Converting ${Math.round(progress * 100)}%` };
  }
  if (chapters.some((c) => c.status === "failed")) return { key: "failed", label: "Failed" };
  if (chapters.every((c) => c.status === "done")) return { key: "done", label: "Done" };
  return { key: "pending", label: "Not started" };
}

export function isBookInProgress(book) {
  return bookStatus(book).key === "converting";
}

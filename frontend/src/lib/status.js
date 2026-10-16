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

// Time formatting and the whole-book timeline the Player scrubs across.

export function formatClock(seconds) {
  const s = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function formatRuntime(seconds) {
  const minutes = Math.max(1, Math.round(seconds / 60));
  const h = Math.floor(minutes / 60);
  return h ? `${h}h ${minutes % 60}m` : `${minutes}m`;
}

export function formatMinutes(seconds) {
  return `${Math.max(1, Math.round(seconds / 60))} min`;
}

// Finished chapters laid end to end; chapters without audio are skipped.
export function bookTimeline(book) {
  const chapters = [];
  let total = 0;
  (book?.chapters || []).forEach((c, index) => {
    if (c.status !== "done" || !c.duration) return;
    chapters.push({ ...c, index, start: total });
    total += c.duration;
  });
  return { chapters, total };
}

// Which chapter a book time falls in, and how far into it.
export function locate(timeline, time) {
  const t = Math.min(Math.max(0, time), timeline.total);
  const chapter = timeline.chapters.findLast((c) => c.start <= t) ?? timeline.chapters[0];
  return { chapter, offset: Math.min(t - chapter.start, chapter.duration) };
}

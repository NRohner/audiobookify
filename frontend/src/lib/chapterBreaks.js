// Chapter breaks are [{ start, title }] sorted by start, where start is the paragraph a
// chapter opens with. The first break is always at paragraph 0: the book's text never
// changes, only where it is split.

export function addBreak(breaks, at, title) {
  if (at <= 0 || breaks.some((b) => b.start === at)) return breaks;
  return [...breaks, { start: at, title }].sort((a, b) => a.start - b.start);
}

export function removeBreak(breaks, index) {
  if (index <= 0) return breaks;
  return breaks.filter((_, i) => i !== index);
}

// Moves break `index` to paragraph `to`, kept strictly between its neighbours so chapters
// never swap or come out empty. `total` is the book's paragraph count.
export function moveBreak(breaks, index, to, total = Infinity) {
  if (index <= 0) return breaks;
  const low = breaks[index - 1].start + 1;
  const high = (breaks[index + 1]?.start ?? total) - 1;
  const start = Math.min(Math.max(to, low), high);
  return breaks.map((b, i) => (i === index ? { ...b, start } : b));
}

export function guessTitle(paragraph) {
  const text = (paragraph ?? "").trim();
  return text && text.length <= 60 ? text : "Untitled chapter";
}

const ranges = (breaks, total) => breaks.map((b, i) => `${b.start}-${breaks[i + 1]?.start ?? total}`);

export function summarize(original, draft, total) {
  const before = ranges(original, total);
  const after = ranges(draft, total);
  const oldStarts = new Set(original.map((b) => b.start));
  const newStarts = new Set(draft.map((b) => b.start));
  const added = draft.filter((b) => !oldStarts.has(b.start)).length;
  const removed = original.filter((b) => !newStarts.has(b.start)).length;
  const renamed = draft.filter((b, i) => {
    const j = before.indexOf(after[i]);
    return j !== -1 && original[j].title !== b.title.trim();
  }).length;
  const loseAudio = original.filter((b, i) => b.status === "done" && !after.includes(before[i])).length;
  return { dirty: added + removed + renamed > 0 || before.join() !== after.join(), added, removed, renamed, loseAudio };
}

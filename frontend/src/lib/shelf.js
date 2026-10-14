// How books look and sit on the shelf, kept out of the components so it is testable.
import { chapterInProgress } from "./api.js";

// [spine token, text tone] — the tone pairs are fixed by tokens.css.
export const SPINE_COLORS = [
  ["mustard", "dark"],
  ["forest", "light"],
  ["oxblood", "light"],
  ["navy", "light"],
  ["cream", "dark"],
  ["rose", "dark"],
  ["charcoal", "light"],
  ["ochre", "dark"],
];

// murmur3 fmix32: neighbouring ids get unrelated colours and heights.
function hash(id) {
  let x = Number(id) | 0;
  x = Math.imul(x ^ (x >>> 16), 0x85ebca6b);
  x = Math.imul(x ^ (x >>> 13), 0xc2b2ae35);
  return (x ^ (x >>> 16)) >>> 0;
}

// Colour, height and lean come from the id so the shelf looks the same every time.
// ponytail: width maps chapter count (5..60) since the API has no word count.
export function spineLook(book) {
  const h = hash(book.id);
  const [color, tone] = SPINE_COLORS[h >>> 29];
  const chapters = book.chapters?.length ?? 0;
  const thickness = Math.min(1, Math.max(0, (chapters - 5) / 55));
  return {
    color,
    tone,
    height: 128 + ((h >>> 20) % 39),
    width: Math.round(34 + 32 * thickness),
    lean: (h >>> 12) % 7 === 0,
  };
}

export function bookState(book) {
  const chapters = book.chapters || [];
  const total = chapters.length;
  const done = chapters.filter((c) => c.status === "done").length;
  const failed = chapters.filter((c) => c.status === "failed" && !chapterInProgress(c)).length;
  const activeIndex = chapters.findIndex(chapterInProgress);
  const rendering = activeIndex !== -1;
  const progress = total
    ? chapters.reduce((sum, c) => sum + (c.status === "done" ? 1 : chapterInProgress(c) ? c.progress ?? 0 : 0), 0) /
      total
    : 0;
  let shelf = "unstarted";
  if (total > 0 && done === total) shelf = "finished";
  else if (rendering || failed > 0 || done > 0) shelf = "studio";
  return { total, done, failed, rendering, activeIndex, progress, shelf };
}

const SHELVES = [
  ["studio", "In the studio"],
  ["finished", "Finished"],
  ["unstarted", "Not started"],
];

export function groupShelves(books) {
  return SHELVES.map(([key, label]) => ({
    key,
    label,
    books: books.filter((b) => bookState(b).shelf === key),
  })).filter((s) => s.books.length > 0);
}

// The one primary button on the book view: Render → Resume → Retry, or Stop while rendering.
export function primaryAction(state) {
  if (state.rendering) return { kind: "stop", label: "Stop rendering" };
  if (state.total === 0 || state.done === state.total) return null;
  if (state.failed > 0) {
    return { kind: "render", label: state.failed === 1 ? "Retry chapter" : `Retry ${state.failed} chapters` };
  }
  if (state.done > 0) return { kind: "render", label: "Resume rendering" };
  return { kind: "render", label: "Render audiobook" };
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// Text for the tag hanging under a spine, or null for none.
export function spineNote(state) {
  if (state.rendering) return null;
  if (state.failed > 0) return `${plural(state.failed, "chapter")} failed`;
  if (state.shelf === "finished") return plural(state.total, "chapter");
  return null;
}

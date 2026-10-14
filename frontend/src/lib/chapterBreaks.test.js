import { test } from "node:test";
import assert from "node:assert/strict";
import { addBreak, guessTitle, moveBreak, removeBreak, summarize } from "./chapterBreaks.js";

const brk = (start, title = `C${start}`, extra = {}) => ({ start, title, ...extra });
const starts = (breaks) => breaks.map((b) => b.start);

test("addBreak inserts in order and ignores a paragraph that already starts a chapter", () => {
  const breaks = [brk(0), brk(10)];
  assert.deepEqual(starts(addBreak(breaks, 5, "New")), [0, 5, 10]);
  assert.deepEqual(starts(addBreak(breaks, 20, "End")), [0, 10, 20]);
  assert.equal(addBreak(breaks, 10, "Dup"), breaks);
  assert.equal(addBreak(breaks, 5, "New")[1].title, "New");
});

test("removeBreak merges a chapter into the one before, but never removes the first", () => {
  const breaks = [brk(0), brk(5), brk(10)];
  assert.deepEqual(starts(removeBreak(breaks, 1)), [0, 10]);
  assert.equal(removeBreak(breaks, 0), breaks);
});

test("moveBreak stays between its neighbours and leaves the first break at the top", () => {
  const breaks = [brk(0), brk(5), brk(10)];
  assert.deepEqual(starts(moveBreak(breaks, 1, 7)), [0, 7, 10]);
  assert.deepEqual(starts(moveBreak(breaks, 1, 50)), [0, 9, 10]);
  assert.deepEqual(starts(moveBreak(breaks, 1, -3)), [0, 1, 10]);
  assert.deepEqual(starts(moveBreak(breaks, 2, 99, 12)), [0, 5, 11]); // last break: before the final paragraph
  assert.equal(moveBreak(breaks, 0, 3), breaks);
});

test("guessTitle uses a short heading-like paragraph, otherwise a placeholder", () => {
  assert.equal(guessTitle("  CHAPTER XII  "), "CHAPTER XII");
  assert.equal(guessTitle("It was a dark and stormy night; the rain fell in torrents, except at occasional intervals."), "Untitled chapter");
  assert.equal(guessTitle(""), "Untitled chapter");
});

test("summarize counts what changed and which finished chapters lose their audio", () => {
  const original = [brk(0, "A", { status: "done" }), brk(5, "B", { status: "done" }), brk(10, "C", { status: "pending" })];
  assert.deepEqual(summarize(original, original, 20), { dirty: false, added: 0, removed: 0, renamed: 0, loseAudio: 0 });

  // Split B (5..10) at 7 and rename A: B's audio no longer matches, A keeps its.
  const draft = [brk(0, "Prologue"), brk(5, "B"), brk(7, "B2"), brk(10, "C")];
  assert.deepEqual(summarize(original, draft, 20), { dirty: true, added: 1, removed: 0, renamed: 1, loseAudio: 1 });

  // Removing C's break merges it into B: both B and C change.
  assert.deepEqual(summarize(original, [original[0], original[1]], 20),
    { dirty: true, added: 0, removed: 1, renamed: 0, loseAudio: 1 });
});

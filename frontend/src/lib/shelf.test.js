import { test } from "node:test";
import assert from "node:assert/strict";
import { SPINE_COLORS, bookState, groupShelves, primaryAction, spineLook, spineNote } from "./shelf.js";

const ch = (status, extra = {}) => ({ status, progress: null, ...extra });
const book = (id, chapters) => ({ id, title: `Book ${id}`, chapters });

test("spineLook is deterministic and stays in the spec's ranges", () => {
  for (let id = 1; id <= 50; id++) {
    const a = spineLook(book(id, [ch("pending")]));
    assert.deepEqual(a, spineLook(book(id, [ch("pending")])));
    assert.ok(a.height >= 128 && a.height <= 166, `height ${a.height}`);
    assert.ok(a.width >= 34 && a.width <= 66, `width ${a.width}`);
    assert.ok(SPINE_COLORS.some(([c]) => c === a.color));
  }
});

test("spineLook spreads neighbouring ids across the cloth colours", () => {
  const colors = new Set(Array.from({ length: 8 }, (_, i) => spineLook(book(i + 1, [])).color));
  assert.ok(colors.size >= 5, `only ${colors.size} colours`);
});

test("spineLook gets thicker for books with more chapters", () => {
  const thin = spineLook(book(1, Array.from({ length: 3 }, () => ch("pending"))));
  const thick = spineLook(book(1, Array.from({ length: 80 }, () => ch("pending"))));
  assert.equal(thin.width, 34);
  assert.equal(thick.width, 66);
});

test("bookState puts books on the right shelf", () => {
  assert.equal(bookState(book(1, [ch("pending"), ch("pending")])).shelf, "unstarted");
  assert.equal(bookState(book(1, [])).shelf, "unstarted");
  assert.equal(bookState(book(1, [ch("done"), ch("done")])).shelf, "finished");
  assert.equal(bookState(book(1, [ch("done"), ch("pending")])).shelf, "studio");
  assert.equal(bookState(book(1, [ch("failed"), ch("pending")])).shelf, "studio");
  assert.equal(bookState(book(1, [ch("pending", { progress: 0 }), ch("pending")])).shelf, "studio");
});

test("bookState counts progress, failures and the active chapter", () => {
  const s = bookState(book(1, [ch("done"), ch("generating", { progress: 0.5 }), ch("failed"), ch("pending")]));
  assert.equal(s.done, 1);
  assert.equal(s.failed, 1);
  assert.equal(s.rendering, true);
  assert.equal(s.activeIndex, 1);
  assert.equal(s.progress, 1.5 / 4);
});

test("groupShelves orders shelves and drops empty ones", () => {
  const shelves = groupShelves([book(1, [ch("done")]), book(2, [ch("pending")]), book(3, [ch("done")])]);
  assert.deepEqual(
    shelves.map((s) => [s.key, s.books.map((b) => b.id)]),
    [
      ["finished", [1, 3]],
      ["unstarted", [2]],
    ],
  );
});

test("primaryAction follows the spec's priority order", () => {
  const act = (chapters) => primaryAction(bookState(book(1, chapters)));
  assert.equal(act([ch("pending"), ch("pending")]).label, "Render audiobook");
  assert.equal(act([ch("done"), ch("pending")]).label, "Resume rendering");
  assert.equal(act([ch("done"), ch("failed")]).label, "Retry chapter");
  assert.equal(act([ch("failed"), ch("failed")]).label, "Retry 2 chapters");
  assert.equal(act([ch("generating", { progress: 0.1 }), ch("pending")]).kind, "stop");
  assert.equal(act([ch("done")]), null);
  assert.equal(act([]), null);
});

test("spineNote says what needs attention in plain words", () => {
  const note = (chapters) => spineNote(bookState(book(1, chapters)));
  assert.equal(note([ch("failed"), ch("done")]), "1 chapter failed");
  assert.equal(note([ch("failed"), ch("failed")]), "2 chapters failed");
  assert.equal(note([ch("done"), ch("pending")]), null);
  assert.equal(note([ch("done"), ch("done")]), "2 chapters"); // no durations known yet
  assert.equal(note([ch("done", { duration: 30000 }), ch("done", { duration: 12120 })]), "11h 42m");
  assert.equal(note([ch("pending")]), null);
  assert.equal(note([ch("generating", { progress: 0.2 })]), null);
});

test("bookState adds up the runtime of finished chapters", () => {
  const s = bookState(book(1, [ch("done", { duration: 60 }), ch("done", { duration: 30 }), ch("pending")]));
  assert.equal(s.runtime, 90);
});

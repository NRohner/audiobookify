import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MIN_KEEP,
  clipTo,
  commit,
  removedSpans,
  formatClipTime,
  fullKeep,
  history,
  keptDuration,
  peaks,
  redo,
  removeRange,
  setEnd,
  setStart,
  toEdited,
  toSource,
  undo,
} from "./clipEdit.js";

const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test("fullKeep keeps the whole source", () => {
  assert.deepEqual(fullKeep(12.5), [[0, 12.5]]);
});

test("keptDuration sums the kept ranges", () => {
  assert.equal(keptDuration([[0, 2], [5, 8]]), 5);
  assert.equal(keptDuration([]), 0);
});

test("setStart trims the head, dropping ranges it passes", () => {
  assert.deepEqual(setStart([[0, 10]], 2), [[2, 10]]);
  assert.deepEqual(setStart([[0, 2], [5, 10]], 3), [[5, 10]]);
  assert.deepEqual(setStart([[0, 2], [5, 10]], 6), [[6, 10]]);
  // pulling the handle back out restores trimmed audio up to the source start
  assert.deepEqual(setStart([[2, 10]], -1), [[0, 10]]);
  assert.deepEqual(setStart([[4, 6], [8, 10]], 1), [[1, 6], [8, 10]]);
});

test("setStart never crosses the end handle", () => {
  assert.deepEqual(setStart([[0, 10]], 10), [[10 - MIN_KEEP, 10]]);
  assert.deepEqual(setStart([[0, 2], [5, 10]], 12), [[10 - MIN_KEEP, 10]]);
});

test("setEnd trims the tail, dropping ranges it passes", () => {
  assert.deepEqual(setEnd([[0, 10]], 7), [[0, 7]]);
  assert.deepEqual(setEnd([[0, 2], [5, 10]], 4), [[0, 2]]);
  assert.deepEqual(setEnd([[0, 2], [5, 10]], 1), [[0, 1]]);
  assert.deepEqual(setEnd([[0, 2], [5, 6]], 9, 10), [[0, 2], [5, 9]]);
  // clamped to the source duration when given
  assert.deepEqual(setEnd([[0, 6]], 99, 10), [[0, 10]]);
  assert.deepEqual(setEnd([[0, 10]], 0), [[0, MIN_KEEP]]);
});

test("removeRange deletes a middle selection by splitting a range", () => {
  assert.deepEqual(removeRange([[0, 10]], 3, 5), [[0, 3], [5, 10]]);
  assert.deepEqual(removeRange([[0, 10]], 5, 3), [[0, 3], [5, 10]]);
});

test("removeRange spans gaps and several ranges", () => {
  assert.deepEqual(removeRange([[0, 3], [5, 10]], 2, 6), [[0, 2], [6, 10]]);
  assert.deepEqual(removeRange([[0, 1], [2, 3], [4, 5]], 0.5, 4.5), [[0, 0.5], [4.5, 5]]);
  assert.deepEqual(removeRange([[0, 3], [5, 10]], 3, 5), [[0, 3], [5, 10]]);
});

test("removeRange drops slivers shorter than a millisecond", () => {
  assert.deepEqual(removeRange([[0, 10]], 0.0004, 5), [[5, 10]]);
});

test("removeRange refuses to delete everything", () => {
  const keep = [[2, 4]];
  assert.equal(removeRange(keep, 0, 10), keep);
  assert.equal(removeRange(keep, 2, 4), keep);
});

test("clipTo keeps only the kept parts inside a source range", () => {
  assert.deepEqual(clipTo([[0, 3], [5, 10]], 2, 7), [[2, 3], [5, 7]]);
  assert.deepEqual(clipTo([[0, 3], [5, 10]], 3.5, 4.5), []);
  assert.deepEqual(clipTo([[0, 3], [5, 10]], 0, Infinity), [[0, 3], [5, 10]]);
});

test("toSource maps edited time onto the source", () => {
  const keep = [[1, 3], [5, 10]];
  assert.equal(toSource(keep, 0), 1);
  assert.equal(toSource(keep, 1.5), 2.5);
  assert.equal(toSource(keep, 2), 3);
  assert.equal(toSource(keep, 2.5), 5.5);
  assert.equal(toSource(keep, 7), 10);
  assert.equal(toSource(keep, 99), 10);
  assert.equal(toSource(keep, -1), 1);
});

test("toEdited maps source time back, snapping gaps to the next kept audio", () => {
  const keep = [[1, 3], [5, 10]];
  assert.equal(toEdited(keep, 2.5), 1.5);
  assert.equal(toEdited(keep, 5.5), 2.5);
  assert.equal(toEdited(keep, 0), 0);
  assert.equal(toEdited(keep, 4), 2);
  assert.equal(toEdited(keep, 12), 7);
  for (const t of [0, 0.3, 2, 4.4, 7]) close(toEdited(keep, toSource(keep, t)), t);
});

test("history commits, undoes and redoes", () => {
  let h = history([[0, 10]]);
  h = commit(h, [[0, 8]]);
  h = commit(h, [[2, 8]]);
  assert.deepEqual(h.present, [[2, 8]]);
  h = undo(h);
  assert.deepEqual(h.present, [[0, 8]]);
  h = undo(h);
  assert.deepEqual(h.present, [[0, 10]]);
  assert.equal(undo(h), h);
  h = redo(h);
  assert.deepEqual(h.present, [[0, 8]]);
  h = commit(h, [[1, 8]]);
  assert.equal(redo(h), h, "a new edit clears redo");
  assert.deepEqual(undo(h).present, [[0, 8]]);
});

test("history ignores a commit that changes nothing", () => {
  const h = history([[0, 10]]);
  assert.equal(commit(h, [[0, 10]]), h);
});

test("peaks takes the loudest sample per bucket across channels", () => {
  const left = Float32Array.from([0, 0.5, -1, 0.25, 0, 0, 0.1, -0.2]);
  const right = Float32Array.from([0.75, 0, 0, 0, 0, 0, 0, 0]);
  assert.deepEqual(Array.from(peaks([left, right], 4)), [0.75, 1, 0, 0.2].map(Math.fround));
});

test("peaks covers every sample when buckets don't divide evenly", () => {
  const data = Float32Array.from([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]);
  assert.deepEqual(Array.from(peaks([data], 3)), [0.2, 0.4, 0.7].map(Math.fround));
});

test("peaks handles silence and more buckets than samples", () => {
  assert.deepEqual(Array.from(peaks([new Float32Array(4)], 2)), [0, 0]);
  assert.equal(peaks([Float32Array.from([0.5, -0.5])], 5).length, 5);
});

test("formatClipTime shows tenths of a second", () => {
  assert.equal(formatClipTime(0), "0:00.0");
  assert.equal(formatClipTime(12.34), "0:12.3");
  assert.equal(formatClipTime(65.99), "1:05.9");
  assert.equal(formatClipTime(-1), "0:00.0");
});

test("removedSpans lists what trims and cuts took out", () => {
  assert.deepEqual(removedSpans([[1, 3], [5, 8]], 10), [[0, 1], [3, 5], [8, 10]]);
  assert.deepEqual(removedSpans([[0, 10]], 10), []);
});

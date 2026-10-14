import { test } from "node:test";
import assert from "node:assert/strict";
import { bookTimeline, formatClock, formatMinutes, formatRuntime, locate } from "./player.js";

test("formatClock shows m:ss, and h:mm:ss past an hour", () => {
  assert.equal(formatClock(0), "0:00");
  assert.equal(formatClock(724.6), "12:04");
  assert.equal(formatClock(11560), "3:12:40");
  assert.equal(formatClock(-5), "0:00");
  assert.equal(formatClock(NaN), "0:00");
});

test("formatRuntime reads like a runtime tag", () => {
  assert.equal(formatRuntime(42120), "11h 42m");
  assert.equal(formatRuntime(10800), "3h 0m");
  assert.equal(formatRuntime(1500), "25m");
  assert.equal(formatRuntime(20), "1m");
});

test("formatMinutes rounds chapter lengths to whole minutes", () => {
  assert.equal(formatMinutes(780), "13 min");
  assert.equal(formatMinutes(20), "1 min");
});

const book = {
  chapters: [
    { id: 1, status: "done", duration: 100 },
    { id: 2, status: "pending", duration: null },
    { id: 3, status: "done", duration: 50 },
  ],
};

test("bookTimeline lays the playable chapters end to end", () => {
  const t = bookTimeline(book);
  assert.equal(t.total, 150);
  assert.deepEqual(t.chapters.map((c) => [c.id, c.start, c.index]), [[1, 0, 0], [3, 100, 2]]);
});

test("locate finds the chapter and offset for a book time", () => {
  const t = bookTimeline(book);
  assert.deepEqual([locate(t, 40).chapter.id, locate(t, 40).offset], [1, 40]);
  assert.deepEqual([locate(t, 120).chapter.id, locate(t, 120).offset], [3, 20]);
  assert.deepEqual([locate(t, 999).chapter.id, locate(t, 999).offset], [3, 50]);
  assert.deepEqual([locate(t, -3).chapter.id, locate(t, -3).offset], [1, 0]);
});

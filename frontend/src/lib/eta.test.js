import { test } from "node:test";
import assert from "node:assert/strict";
import { formatDuration, formatEta } from "./eta.js";

test("formatDuration renders seconds under a minute", () => {
  assert.equal(formatDuration(45), "45s");
});

test("formatDuration renders minutes and seconds under an hour", () => {
  assert.equal(formatDuration(330), "5m 30s");
});

test("formatDuration renders hours and minutes at or above an hour", () => {
  assert.equal(formatDuration(4800), "1h 20m");
});

test("formatEta returns null when progress is zero", () => {
  assert.equal(formatEta(0, 1000, 1010), null);
});

test("formatEta returns null when progress is missing", () => {
  assert.equal(formatEta(null, 1000, 1010), null);
});

test("formatEta returns null when startedAt is missing", () => {
  assert.equal(formatEta(0.5, null, 1010), null);
});

test("formatEta extrapolates remaining time linearly from elapsed time and progress", () => {
  // 10s elapsed at 50% done -> 10s remaining
  assert.equal(formatEta(0.5, 1000, 1010), "10s");
});

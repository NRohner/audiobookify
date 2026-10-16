import { test } from "node:test";
import assert from "node:assert/strict";
import {
  downloadFraction, downloadText, formatBytes, formatDuration, framings, needsSetup, renderPace,
} from "./settings.js";

test("formatBytes uses decimal units like Finder", () => {
  assert.equal(formatBytes(3_500_000_000), "3.5 GB");
  assert.equal(formatBytes(512_000_000), "512 MB");
  assert.equal(formatBytes(0), "0 MB");
  assert.equal(formatBytes(14_000_000_000), "14 GB");
});

test("formatDuration speaks in hours or minutes", () => {
  assert.equal(formatDuration(41 * 3600 + 100), "41 hours");
  assert.equal(formatDuration(3600), "1 hour");
  assert.equal(formatDuration(35 * 60), "35 minutes");
  assert.equal(formatDuration(1 * 60), "1 minute");
  assert.equal(formatDuration(20), "under a minute");
});

test("framings appear only once there is enough to say", () => {
  assert.deepEqual(framings({ audio_seconds: 600, words: 5000 }), []);
  assert.deepEqual(framings({ audio_seconds: 41 * 3600, words: 225_000 }), [
    "That's 1.7 days of nonstop listening.",
    "About 2.5 average novels, at 90,000 words each.",
  ]);
});

test("renderPace says how long an hour of audio takes", () => {
  assert.equal(renderPace({ audio_seconds: 3600, generating_seconds: 1680 }), "about 28 minutes for every hour of audio");
  assert.equal(renderPace({ audio_seconds: 3600, generating_seconds: 7200 }), "about 2 hours for every hour of audio");
  assert.equal(renderPace({ audio_seconds: 0, generating_seconds: 10 }), null);
});

test("download progress", () => {
  const d = { state: "running", downloaded: 1_200_000_000, total: 3_500_000_000 };
  assert.equal(downloadText(d), "1.2 of 3.5 GB");
  assert.ok(Math.abs(downloadFraction(d) - 0.343) < 0.001);
  assert.equal(downloadFraction({ state: "running", downloaded: 5, total: 0 }), 0);
});

test("needsSetup when no engine is ready", () => {
  assert.equal(needsSetup([{ id: "qwen3", ready: false }, { id: "chatterbox-turbo", ready: false }]), true);
  assert.equal(needsSetup([{ id: "qwen3", ready: false }, { id: "chatterbox-turbo", ready: true }]), false);
  assert.equal(needsSetup([]), false); // not loaded yet
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { effectiveEngine, engineMismatchWarning, initialBookEngine, transcriptState } from "./engines.js";

const ENGINES = [
  { id: "qwen3", label: "Quality engine", needs_voice: true },
  { id: "chatterbox-turbo", label: "Speed engine", needs_voice: false },
];

test("effectiveEngine keeps the choice when it can run", () => {
  assert.equal(effectiveEngine("qwen3", ENGINES, true), "qwen3");
  assert.equal(effectiveEngine("chatterbox-turbo", ENGINES, false), "chatterbox-turbo");
});

test("effectiveEngine falls back to an engine that needs no voice when none is picked", () => {
  assert.equal(effectiveEngine("qwen3", ENGINES, false), "chatterbox-turbo");
});

test("initialBookEngine starts new books on Quality and keeps a used book's engine", () => {
  assert.equal(initialBookEngine({ engine: "chatterbox-turbo", chapters: [{ engine: null }] }), "qwen3");
  assert.equal(
    initialBookEngine({ engine: "chatterbox-turbo", chapters: [{ engine: "chatterbox-turbo" }, { engine: null }] }),
    "chatterbox-turbo",
  );
});

test("engineMismatchWarning is silent when every finished chapter used the chosen engine", () => {
  const book = { mixed_engines: false, chapters: [{ status: "done", engine: "qwen3" }, { status: "pending", engine: null }] };
  assert.equal(engineMismatchWarning(book, "qwen3", ENGINES), null);
  assert.equal(engineMismatchWarning({ mixed_engines: false, chapters: [] }, "qwen3", ENGINES), null);
});

test("engineMismatchWarning names the engines when chapters would not match", () => {
  const book = { mixed_engines: false, chapters: [{ status: "done", engine: "qwen3" }, { status: "pending" }] };
  assert.match(engineMismatchWarning(book, "chatterbox-turbo", ENGINES), /Quality engine/);
  assert.match(engineMismatchWarning(book, "chatterbox-turbo", ENGINES), /Speed engine/);
});

test("engineMismatchWarning reports a book that already mixes engines, including legacy audio", () => {
  const book = {
    mixed_engines: true,
    chapters: [{ status: "done", engine: "chatterbox" }, { status: "done", engine: "qwen3" }],
  };
  const warning = engineMismatchWarning(book, "qwen3", ENGINES);
  assert.match(warning, /Older Chatterbox/);
});

test("transcriptState describes each stage of a voice's transcript", () => {
  assert.deepEqual(transcriptState({ transcript_status: "pending" }), { key: "pending", label: "Transcribing…", note: null });
  assert.equal(transcriptState({ transcript_status: "failed", transcript_error: "no speech" }).label,
    "Transcription failed: no speech");
  assert.equal(transcriptState({ transcript_status: null, reference_transcript: null }).key, "missing");
  assert.equal(transcriptState({ transcript_status: "done", reference_transcript: "Hi" }).key, "done");
});

test("transcriptState explains when only part of the clip is used", () => {
  const voice = { transcript_status: "done", reference_transcript: "Hi", reference_trim_end: 15.06, reference_seconds: 36.1 };
  assert.equal(transcriptState(voice).note, "Uses the first 15.1 s of the 36.1 s clip. The transcript covers that part.");
  assert.equal(transcriptState({ ...voice, reference_trim_end: null }).note, null);
});

test("effectiveEngine skips an engine that isn't downloaded", () => {
  const engines = [
    { id: "qwen3", needs_voice: true, ready: true },
    { id: "chatterbox-turbo", needs_voice: false, ready: false },
  ];
  assert.equal(effectiveEngine("chatterbox-turbo", engines, true), "qwen3");
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { START, clipFlow, exportName, recordErrorText } from "./clipFlow.js";

const run = (...events) => events.reduce(clipFlow, START);
const denied = Object.assign(new Error("no access"), { kind: "permission_denied" });

test("a recording goes starting, recording, stopping, then into the editor", () => {
  assert.deepEqual(run({ type: "record" }), { stage: "starting" });
  assert.deepEqual(run({ type: "record" }, { type: "started", at: 1000 }), { stage: "recording", startedAt: 1000 });
  assert.deepEqual(run({ type: "record" }, { type: "started", at: 1000 }, { type: "stop" }),
    { stage: "stopping", startedAt: 1000 });
  assert.deepEqual(
    run({ type: "record" }, { type: "started", at: 1000 }, { type: "stop" }, { type: "stopped", path: "/t/a.wav" }),
    { stage: "editing", path: "/t/a.wav", recorded: true },
  );
});

test("a picked file opens the editor", () => {
  assert.deepEqual(run({ type: "picked", path: "/m/a.mp3" }), { stage: "editing", path: "/m/a.mp3", recorded: false });
});

test("permission denied gets its own state; other failures are generic", () => {
  assert.deepEqual(run({ type: "record" }, { type: "error", error: denied }), { stage: "denied" });
  assert.deepEqual(run({ type: "record" }, { type: "error", error: new Error("boom") }),
    { stage: "failed", message: "boom" });
  // A failure while stopping (the take couldn't be finished) is a failure too.
  const stopping = run({ type: "record" }, { type: "started", at: 0 }, { type: "stop" });
  assert.equal(clipFlow(stopping, { type: "error", error: new Error("x") }).stage, "failed");
});

test("you can try again after a failure, or start over from the editor", () => {
  for (const state of [run({ type: "record" }, { type: "error", error: denied }),
    run({ type: "record" }, { type: "error", error: new Error("x") })]) {
    assert.deepEqual(clipFlow(state, { type: "record" }), { stage: "starting" });
    assert.deepEqual(clipFlow(state, { type: "reset" }), START);
  }
  assert.deepEqual(clipFlow(run({ type: "picked", path: "/a" }), { type: "reset" }), START);
});

test("events that don't apply are ignored, so double clicks are harmless", () => {
  const recording = run({ type: "record" }, { type: "started", at: 5 });
  assert.equal(clipFlow(recording, { type: "record" }), recording);
  assert.equal(clipFlow(recording, { type: "picked", path: "/a" }), recording);
  const stopping = clipFlow(recording, { type: "stop" });
  assert.equal(clipFlow(stopping, { type: "stop" }), stopping);
  assert.equal(clipFlow(START, { type: "stop" }), START);
  assert.equal(clipFlow(START, { type: "stopped", path: "/a" }), START);
  // While the editor is open, a stray recorder event changes nothing.
  const editing = run({ type: "picked", path: "/a" });
  assert.equal(clipFlow(editing, { type: "error", error: denied }), editing);
});

test("the permission message says where to turn access on", () => {
  const text = recordErrorText(denied);
  assert.match(text.title, /permission/i);
  assert.match(text.body, /System Settings/);
  assert.match(text.body, /Screen & System Audio Recording/);
  assert.match(text.body, /Audiobookify/);
  assert.deepEqual(recordErrorText(new Error("device busy")), {
    title: "The recording didn't work",
    body: "device busy",
  });
});

test("the export file is named after the voice", () => {
  assert.equal(exportName("Harriet"), "Harriet.wav");
  assert.equal(exportName("  "), "voice clip.wav");
  assert.equal(exportName("A/B: C"), "A-B- C.wav");
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { TEMPO_MAX, TEMPO_MIN, pacedWpm, sweetSpotTempo, tempoPosition } from "./pace.js";

const PACE = { speaking_wpm: 210.4, overall_wpm: 197.7 };

test("pacedWpm scales the measured reference pace by the tempo", () => {
  assert.deepEqual(pacedWpm(PACE, 1), { speaking: 210, overall: 198 });
  assert.deepEqual(pacedWpm(PACE, 0.88), { speaking: 185, overall: 174 });
  assert.equal(pacedWpm(null, 0.9), null);
});

test("sweetSpotTempo brings the reference to about 174 wpm overall", () => {
  assert.equal(sweetSpotTempo(PACE), 0.88);
  assert.equal(sweetSpotTempo({ speaking_wpm: 130, overall_wpm: 120 }), TEMPO_MAX); // kept in range
  assert.equal(sweetSpotTempo({ speaking_wpm: 300, overall_wpm: 290 }), TEMPO_MIN);
  assert.equal(sweetSpotTempo(null), null);
});

test("tempoPosition places a tempo along the slider as a percentage", () => {
  assert.equal(tempoPosition(TEMPO_MIN), 0);
  assert.equal(tempoPosition(1), 50);
  assert.equal(tempoPosition(TEMPO_MAX), 100);
});

// Edit math for the clip editor. An edit is a `keep` list: [[startSec, endSec], ...] of the
// source, in order and non-overlapping — exactly what api.exportMonoClip takes. "Edited time"
// is the kept audio laid end to end; "source time" is a position in the original file.
import { formatClock } from "./player.js";

export const MIN_KEEP = 0.1; // trim handles always leave this much audio between them
const SLIVER = 1e-3; // cut leftovers shorter than this are dropped

export const fullKeep = (duration) => [[0, duration]];

export const keptDuration = (keep) => keep.reduce((sum, [s, e]) => sum + e - s, 0);

// Move the start handle to source time `t`. Moving it in drops whatever it passes; moving it
// out extends the first range, restoring trimmed audio.
export function setStart(keep, t) {
  const end = keep.at(-1)[1];
  t = Math.min(Math.max(t, 0), end - MIN_KEEP);
  if (t <= keep[0][0]) return [[t, keep[0][1]], ...keep.slice(1)];
  const rest = keep.filter(([, e]) => e > t);
  return [[Math.max(rest[0][0], t), rest[0][1]], ...rest.slice(1)];
}

// Mirror of setStart for the end handle; `duration` caps how far out it can go.
export function setEnd(keep, t, duration = Infinity) {
  const start = keep[0][0];
  t = Math.max(Math.min(t, duration), start + MIN_KEEP);
  const last = keep.at(-1);
  if (t >= last[1]) return [...keep.slice(0, -1), [last[0], t]];
  const rest = keep.filter(([s]) => s < t);
  const tail = rest.at(-1);
  return [...rest.slice(0, -1), [tail[0], Math.min(tail[1], t)]];
}

// Delete source range a..b (either order). Returns `keep` unchanged rather than leave nothing.
export function removeRange(keep, a, b) {
  if (a > b) [a, b] = [b, a];
  const out = [];
  for (const [s, e] of keep) {
    if (Math.min(e, a) - s >= SLIVER) out.push([s, Math.min(e, a)]);
    if (e - Math.max(s, b) >= SLIVER) out.push([Math.max(s, b), e]);
  }
  return out.length ? out : keep;
}

// The kept parts of source range a..b — the segments to play for a selection.
export const clipTo = (keep, a, b) =>
  keep.map(([s, e]) => [Math.max(s, a), Math.min(e, b)]).filter(([s, e]) => e > s);

export function toSource(keep, t) {
  t = Math.max(0, t);
  for (const [s, e] of keep) {
    if (t <= e - s) return s + t;
    t -= e - s;
  }
  return keep.at(-1)?.[1] ?? 0;
}

// Source time inside a removed gap maps to where the next kept audio starts.
export function toEdited(keep, x) {
  let acc = 0;
  for (const [s, e] of keep) {
    if (x < s) return acc;
    if (x <= e) return acc + x - s;
    acc += e - s;
  }
  return acc;
}

// The parts of 0..duration that trims and cuts removed.
export function removedSpans(keep, duration) {
  const out = [];
  let at = 0;
  for (const [s, e] of keep) {
    if (s > at) out.push([at, s]);
    at = e;
  }
  if (at < duration) out.push([at, duration]);
  return out;
}

// Undo history: past and future hold whole keep lists (they're tiny).
export const history = (present) => ({ past: [], present, future: [] });

export function commit(h, next) {
  if (JSON.stringify(next) === JSON.stringify(h.present)) return h;
  return { past: [...h.past, h.present], present: next, future: [] };
}

export function undo(h) {
  if (!h.past.length) return h;
  return { past: h.past.slice(0, -1), present: h.past.at(-1), future: [h.present, ...h.future] };
}

export function redo(h) {
  if (!h.future.length) return h;
  return { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) };
}

// Loudest absolute sample per bucket across all channels (Float32Arrays of equal length).
export function peaks(channels, buckets) {
  const out = new Float32Array(buckets);
  const n = channels[0].length;
  for (let i = 0; i < buckets; i++) {
    const from = Math.floor((i * n) / buckets);
    const to = Math.floor(((i + 1) * n) / buckets);
    let max = 0;
    for (const data of channels) {
      for (let j = from; j < to; j++) {
        const v = Math.abs(data[j]);
        if (v > max) max = v;
      }
    }
    out[i] = max;
  }
  return out;
}

export function formatClipTime(seconds) {
  const tenths = Math.floor(Math.max(0, seconds) * 10 + 1e-6);
  return `${formatClock(Math.floor(tenths / 10))}.${tenths % 10}`;
}

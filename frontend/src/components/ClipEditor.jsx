import { useEffect, useMemo, useRef, useState } from "react";
import { Redo2, Scissors, SquarePlay, Undo2 } from "lucide-react";
import {
  clipTo, commit, formatClipTime, fullKeep, history, keptDuration, peaks, redo, removeRange, removedSpans,
  setEnd, setStart, toEdited, toSource, undo,
} from "../lib/clipEdit.js";
import PlayGlyph from "./player/PlayGlyph.jsx";
import Button, { IconButton } from "./ui/Button.jsx";

const BAR = 2; // css px per bar, plus a 1px gap
const PITCH = 3;
const COLORS = ["--rr-ink-muted", "--rr-line", "--rr-accent"];

// Trim and cut a recording. The waveform always shows the whole source; trimmed and deleted
// parts stay visible but greyed, and playback skips them. Reports the `keep` list (source
// seconds, for api.exportMonoClip) through onChange after every edit.
export default function ClipEditor({ src, onChange }) {
  const wrap = useRef(null);
  const canvas = useRef(null);
  const ctxRef = useRef(null);
  const playRef = useRef(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  const [buffer, setBuffer] = useState(null);
  const [error, setError] = useState(null);
  const [h, setH] = useState(null);
  const [draft, setDraft] = useState(null); // keep list while a trim handle is dragged
  const [sel, setSel] = useState(null); // [a, b] source seconds, unordered while dragging
  const [cursor, setCursor] = useState(0); // source time whole-clip playback starts from
  const [playhead, setPlayhead] = useState(null); // source time while playing
  const [width, setWidth] = useState(0);
  const [theme, setTheme] = useState(0);

  const keep = draft ?? h?.present;
  const duration = buffer?.duration ?? 0;
  const range = sel && sel[0] !== sel[1] ? [Math.min(...sel), Math.max(...sel)] : null;

  useEffect(() => {
    const ctx = (ctxRef.current = new AudioContext());
    return () => ctx.close();
  }, []);

  useEffect(() => {
    let live = true;
    setBuffer(null);
    setError(null);
    stop();
    if (!src) return;
    fetch(src)
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(r.statusText))))
      .then((data) => ctxRef.current.decodeAudioData(data))
      .then((buf) => {
        if (!live) return;
        setBuffer(buf);
        setH(history(fullKeep(buf.duration)));
        setSel(null);
        setCursor(0);
      })
      .catch(() => live && setError("This recording couldn't be opened. Try recording it again, or choose another file."));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);

  useEffect(() => {
    if (buffer && h) onChangeRef.current?.(h.present);
  }, [buffer, h?.present]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(wrap.current);
    // Repaint when the theme flips; canvas colours are read from the --rr-* tokens.
    const bump = () => setTheme((n) => n + 1);
    const mo = new MutationObserver(bump);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", bump);
    return () => {
      ro.disconnect();
      mo.disconnect();
      mq.removeEventListener("change", bump);
    };
  }, []);

  useEffect(() => () => stop(), []); // eslint-disable-line react-hooks/exhaustive-deps

  const bars = useMemo(() => {
    if (!buffer || !width) return null;
    const channels = Array.from({ length: buffer.numberOfChannels }, (_, i) => buffer.getChannelData(i));
    const p = peaks(channels, Math.floor(width / PITCH));
    const max = p.reduce((m, v) => Math.max(m, v), 0) || 1;
    return p.map((v) => v / max);
  }, [buffer, width]);

  useEffect(() => {
    const el = canvas.current;
    if (!el || !bars || !keep) return;
    const dpr = devicePixelRatio || 1;
    const { clientWidth: w, clientHeight: ht } = el;
    el.width = w * dpr;
    el.height = ht * dpr;
    const g = el.getContext("2d");
    g.scale(dpr, dpr);
    const style = getComputedStyle(el);
    const [kept, removed, played] = COLORS.map((c) => style.getPropertyValue(c).trim());
    bars.forEach((v, i) => {
      const t = ((i + 0.5) / bars.length) * duration;
      const isKept = keep.some(([s, e]) => t >= s && t <= e);
      g.fillStyle = !isKept ? removed : playhead != null && t <= playhead ? played : kept;
      const barH = Math.max(2, v * ht * 0.86);
      g.beginPath();
      g.roundRect(i * PITCH, (ht - barH) / 2, BAR, barH, 1);
      g.fill();
    });
  }, [bars, keep, playhead, duration, theme]);

  function stop() {
    const p = playRef.current;
    playRef.current = null;
    if (!p) return null;
    cancelAnimationFrame(p.raf);
    p.nodes.forEach((n) => {
      n.onended = null;
      n.stop();
    });
    setPlayhead(null);
    return p;
  }

  // Schedule the segments back to back so cuts play gaplessly; the playhead follows the clock.
  function play(segments, whole) {
    stop();
    if (!segments.length) return;
    const ctx = ctxRef.current;
    ctx.resume();
    const t0 = ctx.currentTime + 0.05;
    let when = t0;
    const nodes = segments.map(([s, e]) => {
      const n = ctx.createBufferSource();
      n.buffer = buffer;
      n.connect(ctx.destination);
      n.start(when, s, e - s);
      when += e - s;
      return n;
    });
    const p = { nodes, segments, whole, t0, raf: 0 };
    const tick = () => {
      setPlayhead(toSource(segments, Math.max(0, ctx.currentTime - t0)));
      p.raf = requestAnimationFrame(tick);
    };
    nodes.at(-1).onended = () => {
      stop();
      if (whole) setCursor(keep[0][0]);
    };
    playRef.current = p;
    tick();
  }

  function togglePlay() {
    const p = playRef.current;
    if (p?.whole) {
      stop();
      setCursor(toSource(p.segments, Math.max(0, ctxRef.current.currentTime - p.t0)));
      return;
    }
    const from = cursor >= keep.at(-1)[1] ? keep[0][0] : cursor;
    play(clipTo(keep, from, Infinity), true);
  }

  function edit(next) {
    stop();
    setH((cur) => commit(cur, next));
  }

  function deleteSelection() {
    if (!range) return;
    edit(removeRange(keep, ...range));
    setSel(null);
    setCursor(range[0]);
  }

  const timeAt = (clientX) => {
    const r = wrap.current.getBoundingClientRect();
    return Math.min(duration, Math.max(0, ((clientX - r.left) / r.width) * duration));
  };
  const pct = (t) => `${(t / duration) * 100}%`;

  function onKeyDown(e) {
    if (!keep) return;
    const mod = e.metaKey || e.ctrlKey;
    if (e.key === " " && e.target.tagName !== "BUTTON") togglePlay();
    else if ((e.key === "Delete" || e.key === "Backspace") && range) deleteSelection();
    else if (mod && e.key.toLowerCase() === "z") {
      stop();
      setH(e.shiftKey ? redo(h) : undo(h));
    }
    else if (e.key === "Escape" && sel) setSel(null);
    else return;
    e.preventDefault();
  }

  const playing = playRef.current;
  const selSegments = range && keep ? clipTo(keep, ...range) : [];
  const canDelete = range && keep && removeRange(keep, ...range) !== keep;
  const position = keep ? toEdited(keep, playhead ?? cursor) : 0;
  const gaps = keep ? removedSpans(keep, duration) : [];

  return (
    <div role="group" aria-label="Clip editor" onKeyDown={onKeyDown} className="rounded-tile border border-line bg-card p-4">
      <div
        ref={wrap}
        tabIndex={buffer ? 0 : -1}
        aria-label="Waveform. Drag to select a part, then delete it. Click to set where playback starts."
        onPointerDown={(e) => {
          if (!buffer) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          const t = timeAt(e.clientX);
          setSel([t, t]);
        }}
        onPointerMove={(e) => sel && e.buttons && setSel([sel[0], timeAt(e.clientX)])}
        onPointerUp={() => {
          if (!sel) return;
          if (Math.abs(sel[1] - sel[0]) < duration / 200) {
            setSel(null);
            setCursor(sel[0]);
          }
        }}
        className="relative h-32 cursor-text touch-none select-none rounded-input"
      >
        {gaps.map(([s, e]) => (
          <div key={s} aria-hidden="true" className="absolute inset-y-0 bg-oat" style={{ left: pct(s), width: pct(e - s) }} />
        ))}
        {range && (
          <div aria-hidden="true" className="absolute inset-y-0 bg-accent-soft" style={{ left: pct(range[0]), width: pct(range[1] - range[0]) }} />
        )}
        <canvas ref={canvas} aria-hidden="true" className="absolute inset-0 size-full" />
        {!buffer && (
          <p className="absolute inset-0 flex items-center justify-center px-6 text-center text-meta text-ink-muted">
            {error ?? (src ? "Opening the recording…" : "No recording yet.")}
          </p>
        )}
        {keep && (
          <>
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-0 w-[2px] -translate-x-1/2 bg-accent"
              style={{ left: pct(playhead ?? cursor) }}
            />
            <TrimHandle
              label="Start of clip"
              value={keep[0][0]}
              duration={duration}
              onMove={(t) => setStart(keep, t)}
              setDraft={setDraft}
              onCommit={edit}
              timeAt={timeAt}
              stop={stop}
            />
            <TrimHandle
              label="End of clip"
              value={keep.at(-1)[1]}
              duration={duration}
              onMove={(t) => setEnd(keep, t, duration)}
              setDraft={setDraft}
              onCommit={edit}
              timeAt={timeAt}
              stop={stop}
            />
          </>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={togglePlay}
          disabled={!keep}
          aria-label={playing?.whole ? "Pause" : "Play clip"}
          className="inline-flex size-9 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent hover:brightness-95 disabled:opacity-45"
        >
          <PlayGlyph playing={!!playing?.whole} />
        </button>
        <span className="text-meta tabular-nums text-ink-muted">
          <span className="text-ink">{formatClipTime(position)}</span> / {formatClipTime(keep ? keptDuration(keep) : 0)}
        </span>
        <span className="flex-1" />
        <Button
          variant="secondary"
          className="h-9"
          disabled={!selSegments.length}
          onClick={() => (playing && !playing.whole ? stop() : play(selSegments, false))}
        >
          <SquarePlay className="size-4" strokeWidth={1.7} aria-hidden="true" />
          {playing && !playing.whole ? "Stop" : "Play selection"}
        </Button>
        <Button variant="secondary" className="h-9" disabled={!canDelete} onClick={deleteSelection}>
          <Scissors className="size-4" strokeWidth={1.7} aria-hidden="true" />
          Delete selection
        </Button>
        <IconButton aria-label="Undo" disabled={!h?.past.length} onClick={() => (stop(), setH(undo(h)))}>
          <Undo2 className="size-4" strokeWidth={1.7} aria-hidden="true" />
        </IconButton>
        <IconButton aria-label="Redo" disabled={!h?.future.length} onClick={() => (stop(), setH(redo(h)))}>
          <Redo2 className="size-4" strokeWidth={1.7} aria-hidden="true" />
        </IconButton>
      </div>
    </div>
  );
}

// A draggable, keyboard-operable edge: arrows move 0.1s (Shift: 1s), Home/End go to the limits.
function TrimHandle({ label, value, duration, onMove, setDraft, onCommit, timeAt, stop }) {
  const dragging = useRef(null);

  function onKeyDown(e) {
    const step = e.shiftKey ? 1 : 0.1;
    const moves = { ArrowRight: step, ArrowUp: step, ArrowLeft: -step, ArrowDown: -step };
    let next;
    if (e.key in moves) next = value + moves[e.key];
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = duration;
    else return;
    e.preventDefault();
    e.stopPropagation();
    onCommit(onMove(next));
  }

  return (
    <div
      role="slider"
      tabIndex={0}
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={Math.round(duration * 10) / 10}
      aria-valuenow={Math.round(value * 10) / 10}
      aria-valuetext={formatClipTime(value)}
      onKeyDown={onKeyDown}
      onPointerDown={(e) => {
        e.stopPropagation();
        e.currentTarget.setPointerCapture(e.pointerId);
        stop();
        dragging.current = onMove(timeAt(e.clientX));
        setDraft(dragging.current);
      }}
      onPointerMove={(e) => {
        if (!dragging.current) return;
        dragging.current = onMove(timeAt(e.clientX));
        setDraft(dragging.current);
      }}
      onPointerUp={() => {
        if (dragging.current) onCommit(dragging.current);
        dragging.current = null;
        setDraft(null);
      }}
      onPointerCancel={() => {
        dragging.current = null;
        setDraft(null);
      }}
      className="group absolute inset-y-0 flex w-4 -translate-x-1/2 cursor-ew-resize touch-none justify-center rounded-input"
      style={{ left: `${(value / duration) * 100}%` }}
    >
      <span aria-hidden="true" className="h-full w-[2px] bg-ink" />
      <span
        aria-hidden="true"
        className="absolute top-1/2 h-7 w-3 -translate-y-1/2 rounded-full border-2 border-ink bg-card group-hover:bg-oat"
      />
    </div>
  );
}

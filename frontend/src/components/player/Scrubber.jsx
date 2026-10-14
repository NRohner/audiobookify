import { useRef, useState } from "react";

// A seek bar: ink fill on a line track, optional chapter ticks, arrow keys move 5s.
// Seeks on release so dragging across chapters doesn't reload audio on every move.
export default function Scrubber({ value, max, onSeek, ticks = [], label, valueText, slim = false, className = "" }) {
  const track = useRef(null);
  const [drag, setDrag] = useState(null);
  const shown = drag ?? value;
  const pct = max ? Math.min(100, Math.max(0, (shown / max) * 100)) : 0;
  const clamp = (t) => Math.min(max, Math.max(0, t));

  function at(clientX) {
    const r = track.current.getBoundingClientRect();
    return clamp(((clientX - r.left) / r.width) * max);
  }

  function onKeyDown(e) {
    const moves = { ArrowRight: 5, ArrowUp: 5, ArrowLeft: -5, ArrowDown: -5, PageUp: 60, PageDown: -60 };
    let next;
    if (e.key in moves) next = value + moves[e.key];
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = max;
    else return;
    e.preventDefault();
    onSeek(clamp(next));
  }

  return (
    <div
      role="slider"
      tabIndex={max ? 0 : -1}
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={Math.round(max)}
      aria-valuenow={Math.round(shown)}
      aria-valuetext={valueText}
      aria-disabled={!max}
      onKeyDown={onKeyDown}
      onPointerDown={(e) => {
        if (!max) return;
        e.currentTarget.setPointerCapture(e.pointerId);
        setDrag(at(e.clientX));
      }}
      onPointerMove={(e) => drag != null && setDrag(at(e.clientX))}
      onPointerUp={() => {
        if (drag != null) onSeek(drag);
        setDrag(null);
      }}
      onPointerCancel={() => setDrag(null)}
      className={`group relative flex h-5 cursor-pointer touch-none items-center rounded-full ${className}`}
    >
      <div ref={track} className={`relative w-full rounded-full bg-line ${slim ? "h-[3px]" : "h-1"}`}>
        <div className="absolute inset-y-0 left-0 rounded-full bg-ink" style={{ width: `${pct}%` }} />
        {ticks.map((t) => (
          <span
            key={t}
            aria-hidden="true"
            className={`absolute top-1/2 h-2.5 w-[2px] -translate-x-1/2 -translate-y-1/2 ${
              t <= shown ? "bg-ink" : "bg-line-strong"
            }`}
            style={{ left: `${(t / max) * 100}%` }}
          />
        ))}
        {!slim && (
          <span
            aria-hidden="true"
            className="absolute top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-ink bg-card"
            style={{ left: `${pct}%` }}
          />
        )}
      </div>
    </div>
  );
}

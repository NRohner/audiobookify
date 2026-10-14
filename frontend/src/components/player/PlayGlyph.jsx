import { Pause, Play } from "lucide-react";

// Filled triangle / two filled rounded bars, per the icon spec.
export default function PlayGlyph({ playing, className = "size-4" }) {
  const Icon = playing ? Pause : Play;
  return <Icon className={`${className} fill-current`} strokeWidth={1.7} aria-hidden="true" />;
}

export function SpeedPill({ rate, onChange, rates, className = "" }) {
  const next = rates[(rates.indexOf(rate) + 1) % rates.length];
  return (
    <button
      type="button"
      onClick={() => onChange(next)}
      aria-label={`Playback speed ${rate}×, change to ${next}×`}
      className={`inline-flex h-8 shrink-0 items-center rounded-full border border-line bg-card px-3.5 text-meta font-bold tabular-nums hover:bg-oat ${className}`}
    >
      Speed {rate}×
    </button>
  );
}

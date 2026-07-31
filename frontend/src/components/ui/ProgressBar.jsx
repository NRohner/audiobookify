import { formatEta } from "../../lib/eta.js";

export default function ProgressBar({ value, startedAt }) {
  const pct = Math.round((value ?? 0) * 100);
  const eta = formatEta(value, startedAt);
  return (
    <div>
      <div
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-2 w-full rounded-full bg-muted overflow-hidden"
      >
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-300 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {pct}%{eta ? ` · ~${eta} left` : ""}
      </p>
    </div>
  );
}

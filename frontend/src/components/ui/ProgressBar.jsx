export default function ProgressBar({ value, label, className = "" }) {
  const pct = Math.round((value ?? 0) * 100);
  return (
    <div
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className={`h-1 w-full rounded-full bg-line overflow-hidden ${className}`}
    >
      <div
        className="h-full rounded-full bg-accent transition-[width] duration-500 ease-settle"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

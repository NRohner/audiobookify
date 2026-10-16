import Button from "./ui/Button.jsx";

// A "Choose…" button that opens a native dialog, and the path it picked.
export default function PathPick({ label, path, onPick, icon: Icon }) {
  return (
    <div className="flex items-center gap-3 min-w-0">
      <Button type="button" variant="secondary" onClick={onPick}>
        {Icon && <Icon className="size-4" aria-hidden="true" />}
        {label}
      </Button>
      <span
        className={`truncate font-mono text-[12px] ${path ? "text-ink" : "text-ink-muted"}`}
        title={path ?? undefined}
      >
        {path ?? "Nothing chosen yet"}
      </span>
    </div>
  );
}

import { CheckCircle2, CircleDashed, Clock, Loader2, XCircle } from "lucide-react";

const statusConfig = {
  done: { icon: CheckCircle2, className: "bg-success/10 text-success" },
  converting: { icon: Loader2, className: "bg-primary/10 text-primary", spin: true },
  generating: { icon: Loader2, className: "bg-primary/10 text-primary", spin: true },
  queued: { icon: Clock, className: "bg-accent/10 text-accent" },
  failed: { icon: XCircle, className: "bg-destructive/10 text-destructive" },
  pending: { icon: CircleDashed, className: "bg-muted text-muted-foreground" },
  empty: { icon: CircleDashed, className: "bg-muted text-muted-foreground" },
};

export default function Badge({ status, label }) {
  const config = statusConfig[status] || statusConfig.pending;
  const Icon = config.icon;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${config.className}`}
    >
      <Icon className={`size-3.5 ${config.spin ? "animate-spin" : ""}`} aria-hidden="true" />
      {label}
    </span>
  );
}

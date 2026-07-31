export function formatDuration(seconds) {
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function formatEta(progress, startedAt, now = Date.now() / 1000) {
  if (!startedAt || !progress || progress <= 0) return null;
  const elapsed = now - startedAt;
  const remaining = (elapsed * (1 - progress)) / progress;
  return formatDuration(remaining);
}

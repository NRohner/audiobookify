// Wording for the Settings page, kept out of the component so it is testable.

// Whole megabytes below a gigabyte; a value that would round to 1000 MB reads as 1 GB.
export function formatBytes(n) {
  if (n >= 999.5e6) {
    const gb = n / 1e9;
    return `${gb >= 10 ? Math.round(gb) : gb.toFixed(1).replace(/\.0$/, "")} GB`;
  }
  return `${Math.round(n / 1e6)} MB`;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// Round first, then pick the unit, so 3576 s reads as "1 hour", not "60 minutes".
export function formatDuration(seconds) {
  if (seconds < 60) return "under a minute";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return plural(minutes, "minute");
  return plural(Math.round(minutes / 60), "hour");
}

const NOVEL_WORDS = 90_000;
const trimmed = (x) => x.toFixed(1).replace(/\.0$/, "");

export function framings({ audio_seconds, words }) {
  const out = [];
  const days = trimmed(audio_seconds / 86400);
  if (audio_seconds / 86400 >= 1) out.push(`That's ${days} ${days === "1" ? "day" : "days"} of nonstop listening.`);
  const novels = trimmed(words / NOVEL_WORDS);
  if (words / NOVEL_WORDS >= 1) {
    out.push(`About ${novels} average ${novels === "1" ? "novel" : "novels"}, at 90,000 words each.`);
  }
  return out;
}

export function renderPace({ audio_seconds, generating_seconds }) {
  if (!audio_seconds) return null;
  const perHour = (generating_seconds / audio_seconds) * 3600;
  if (perHour < 60) return "under a minute for every hour of audio";
  return `about ${formatDuration(perHour)} for every hour of audio`;
}

// The downloaded amount is counted in the same unit as the total.
export function downloadText({ downloaded, total }) {
  const totalText = formatBytes(total);
  const done = totalText.endsWith(" GB") ? (downloaded / 1e9).toFixed(1) : String(Math.round(downloaded / 1e6));
  return `${done} of ${totalText}`;
}

export function downloadFraction({ downloaded, total }) {
  return total > 0 ? Math.min(downloaded / total, 1) : 0;
}

// Only an engine marked not ready counts against setup, matching effectiveEngine.
export function needsSetup(engines) {
  return engines.length > 0 && engines.every((e) => e.ready === false);
}

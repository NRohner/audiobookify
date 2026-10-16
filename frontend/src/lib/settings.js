// Wording for the Settings page, kept out of the component so it is testable.

export function formatBytes(n) {
  if (n >= 1e9) {
    const gb = n / 1e9;
    return `${gb >= 10 ? Math.round(gb) : gb.toFixed(1).replace(/\.0$/, "")} GB`;
  }
  return `${Math.round(n / 1e6)} MB`;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function formatDuration(seconds) {
  if (seconds >= 3600) return plural(Math.round(seconds / 3600), "hour");
  if (seconds >= 60) return plural(Math.round(seconds / 60), "minute");
  return "under a minute";
}

const NOVEL_WORDS = 90_000;

export function framings({ audio_seconds, words }) {
  const out = [];
  const days = audio_seconds / 86400;
  if (days >= 1) out.push(`That's ${days.toFixed(1).replace(/\.0$/, "")} days of nonstop listening.`);
  const novels = words / NOVEL_WORDS;
  if (novels >= 1) out.push(`About ${novels.toFixed(1).replace(/\.0$/, "")} average novels, at 90,000 words each.`);
  return out;
}

export function renderPace({ audio_seconds, generating_seconds }) {
  if (!audio_seconds) return null;
  return `about ${formatDuration((generating_seconds / audio_seconds) * 3600)} for every hour of audio`;
}

export function downloadText({ downloaded, total }) {
  const unit = (n) => (n / 1e9).toFixed(1);
  return `${unit(downloaded)} of ${formatBytes(total)}`;
}

export function downloadFraction({ downloaded, total }) {
  return total > 0 ? Math.min(downloaded / total, 1) : 0;
}

export function needsSetup(engines) {
  return engines.length > 0 && !engines.some((e) => e.ready);
}

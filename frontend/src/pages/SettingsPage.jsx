import { useEffect, useRef, useState } from "react";
import { Download, HardDrive, Trash2, X } from "lucide-react";
import { api } from "../lib/api.js";
import {
  downloadFraction, downloadText, formatBytes, formatDuration, framings, needsSetup, renderPace,
} from "../lib/settings.js";
import { engineLabel } from "../lib/engines.js";
import Button, { ErrorNote, PageHeader } from "../components/ui/Button.jsx";
import ProgressBar from "../components/ui/ProgressBar.jsx";
import ConfirmDialog from "../components/ui/ConfirmDialog.jsx";

const RECOMMENDED = "chatterbox-turbo"; // needs no narrator voice, so it works straight away

// The stats as a reader's card (DESIGN.md "Library card"): a <dl>, not stat tiles.
function ListeningCard({ stats, engines }) {
  if (stats.chapters_done === 0 && stats.audio_seconds === 0) {
    return (
      <p className="text-ui text-ink-muted">Nothing rendered yet. Your totals show up here after your first chapter.</p>
    );
  }
  const rows = [
    ["Words read aloud", stats.words.toLocaleString()],
    ["Chapters", stats.chapters_done.toLocaleString()],
    ["Books finished", stats.books_finished.toLocaleString()],
    stats.longest_book && ["Longest book", `${stats.longest_book.name} · ${formatDuration(stats.longest_book.seconds)}`],
    stats.top_voice && ["Favourite narrator", `${stats.top_voice.name} · ${formatDuration(stats.top_voice.seconds)}`],
    stats.busiest_day && [
      "Busiest day",
      `${new Date(`${stats.busiest_day.day}T12:00:00`).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })} · ${stats.busiest_day.chapters} ${stats.busiest_day.chapters === 1 ? "chapter" : "chapters"}`,
    ],
    ...stats.engines.map((e) => {
      const pace = renderPace(e);
      return pace && [engineLabel(e.engine, engines), pace];
    }),
    ["On your disk", formatBytes(stats.disk_bytes)],
  ].filter(Boolean);
  return (
    <div className="max-w-[560px]">
      <p className="font-serif text-[30px] leading-tight">You've rendered {formatDuration(stats.audio_seconds)} of audio.</p>
      {framings(stats).map((line) => (
        <p key={line} className="text-ui text-ink-muted mt-1">{line}</p>
      ))}
      {/* The eyebrow sits outside the <dl>: a <dl> may only hold dt/dd groups. */}
      <div className="mt-5 rounded-[4px_4px_14px_14px] border border-line border-t-[3px] border-t-accent-deco bg-card px-5 pt-3 pb-2">
        <p className="rr-eyebrow mb-1">Your listening so far</p>
        <dl>
          {rows.map(([label, value]) => (
            <div key={label} className="grid grid-cols-[150px_1fr] items-center min-h-8 border-t border-line text-meta">
              <dt className="text-ink-muted">{label}</dt>
              <dd className="text-ink tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

function ModelRow({ model, busy, onDownload, onCancel, onDelete }) {
  const running = model.download?.state === "running";
  const failed = model.download?.state === "failed";
  return (
    <li className="flex flex-wrap items-start gap-x-6 gap-y-2 py-4 border-t border-line">
      <div className="flex-1 min-w-[260px]">
        <p className="font-serif text-[18px]">{model.label}</p>
        <p className="text-meta text-ink-muted mt-0.5">{model.purpose}</p>
        <p className="text-[12px] text-ink-muted mt-1">{model.repos.join(" · ")}</p>
        {running && (
          <div className="mt-3 max-w-[360px]">
            <ProgressBar value={downloadFraction(model.download)} label={`${model.label} download`} />
            <p className="text-[12px] text-ink-muted mt-1 tabular-nums">Downloading · {downloadText(model.download)}</p>
          </div>
        )}
        {failed && <p className="text-meta text-accent-text mt-2">{model.download.error}</p>}
      </div>
      <div className="flex items-center gap-3">
        <span className="text-meta text-ink-muted tabular-nums">
          {model.downloaded ? `Downloaded · ${formatBytes(model.size)}` : `Not downloaded · about ${formatBytes(model.download_bytes)}`}
        </span>
        {running ? (
          <Button variant="secondary" onClick={onCancel} aria-label={`Cancel the ${model.label} download`}>
            <X className="size-4" aria-hidden="true" /> Cancel
          </Button>
        ) : model.downloaded ? (
          <Button variant="secondary" onClick={onDelete} disabled={busy} aria-label={`Delete ${model.label}`}>
            <Trash2 className="size-4" aria-hidden="true" /> Delete
          </Button>
        ) : (
          <Button
            variant="secondary"
            onClick={onDownload}
            disabled={busy}
            aria-label={`Download ${model.label}${failed ? " again" : ""}`}
          >
            <Download className="size-4" aria-hidden="true" /> {failed ? "Try again" : "Download"}
          </Button>
        )}
      </div>
    </li>
  );
}

export default function SettingsPage() {
  const [stats, setStats] = useState(null);
  const [models, setModels] = useState([]);
  const [engines, setEngines] = useState([]);
  const [error, setError] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);
  const wasRunning = useRef(false);
  const pollFailed = useRef(false);

  async function refreshModels() {
    const [rows, eng] = await Promise.all([api.listModels(), api.listEngines()]);
    setModels(rows);
    setEngines(eng.engines);
  }

  useEffect(() => {
    api.getStats().then(setStats).catch((e) => setError(e.message));
    refreshModels().catch((e) => setError(e.message));
  }, []);

  const running = models.some((m) => m.download?.state === "running");

  // While a download runs, poll the cheap progress command; re-list (sizes) once it ends.
  useEffect(() => {
    if (!running) {
      if (wasRunning.current) refreshModels().catch((e) => setError(e.message));
      wasRunning.current = false;
      return undefined;
    }
    wasRunning.current = true;
    const id = setInterval(async () => {
      try {
        const progress = await api.modelProgress();
        setModels((rows) => rows.map((r) => ({ ...r, ...progress.find((p) => p.id === r.id) })));
        if (pollFailed.current) {
          pollFailed.current = false;
          setError(null);
        }
      } catch (e) {
        pollFailed.current = true;
        setError(e.message);
      }
    }, 1000);
    return () => clearInterval(id);
  }, [running]);

  async function act(fn) {
    setBusy(true);
    pollFailed.current = false;
    setError(null);
    try {
      await fn();
      await refreshModels();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const recommended = models.find((m) => m.id === RECOMMENDED);

  return (
    <div className="max-w-[880px]">
      <PageHeader title="Settings" />
      <ErrorNote>{error}</ErrorNote>

      {needsSetup(engines) && recommended && !running && (
        <section className="mb-10 rounded-tile bg-oat px-6 py-5 flex flex-wrap items-center gap-x-6 gap-y-3">
          <HardDrive className="size-5 text-accent-text shrink-0" strokeWidth={1.7} aria-hidden="true" />
          <div className="flex-1 min-w-[260px]">
            <p className="font-serif text-[22px]">Download a voice engine to get started.</p>
            <p className="text-meta text-ink-muted mt-1">
              We suggest the {recommended.label.toLowerCase()}: it reads without a narrator voice, so you can start
              right away. It's about {formatBytes(recommended.download_bytes)} and stays on this Mac.
            </p>
          </div>
          <Button onClick={() => act(() => api.downloadModel(RECOMMENDED))} disabled={busy}>
            <Download className="size-4" aria-hidden="true" /> Download the {recommended.label.toLowerCase()}
          </Button>
        </section>
      )}

      <section className="mb-12">
        <h2 className="rr-eyebrow mb-4">Listening</h2>
        {stats ? <ListeningCard stats={stats} engines={engines} /> : <p className="text-meta text-ink-muted">Counting…</p>}
      </section>

      <section>
        <h2 className="rr-eyebrow mb-1">Downloads</h2>
        <p className="text-meta text-ink-muted mb-3">
          The voice engines and voice transcription are downloaded once from Hugging Face and kept on this Mac. Other apps' models in the same folder are never touched.
        </p>
        <ul>
          {models.map((m) => (
            <ModelRow
              key={m.id}
              model={m}
              busy={busy || (running && m.download?.state !== "running")}
              onDownload={() => act(() => api.downloadModel(m.id))}
              onCancel={() => act(() => api.cancelModelDownload(m.id))}
              onDelete={() => setConfirm(m)}
            />
          ))}
        </ul>
      </section>

      {confirm && (
        <ConfirmDialog
          title={`Delete the ${confirm.label.toLowerCase()}?`}
          message={`This frees ${formatBytes(confirm.size)}. You can download it again any time.`}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            const m = confirm;
            setConfirm(null);
            act(() => api.deleteModel(m.id));
          }}
        />
      )}
    </div>
  );
}

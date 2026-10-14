import { useEffect, useState } from "react";
import { ChevronRight, Download, Trash2 } from "lucide-react";
import { api, textClipInProgress } from "../lib/api.js";
import Button, { ErrorNote, PageHeader } from "../components/ui/Button.jsx";
import ProgressBar from "../components/ui/ProgressBar.jsx";
import ConfirmDialog from "../components/ui/ConfirmDialog.jsx";
import EnginePicker from "../components/EnginePicker.jsx";
import { effectiveEngine, engineLabel } from "../lib/engines.js";
import { formatEta } from "../lib/eta.js";

function clipStatus(clip) {
  if (clip.status === "generating") {
    const eta = formatEta(clip.progress, clip.started_at);
    return { text: `Reading · ${Math.round((clip.progress ?? 0) * 100)}%${eta ? ` · about ${eta} left` : ""}`, tone: "text-accent-text font-bold" };
  }
  if (clip.status === "queued") return { text: "Queued", tone: "text-ink-muted" };
  if (clip.status === "failed") return { text: "Didn't finish", tone: "text-accent-text font-bold" };
  return null;
}

export default function TextPage() {
  const [text, setText] = useState("");
  const [voiceProfileId, setVoiceProfileId] = useState("");
  const [voices, setVoices] = useState([]);
  const [engines, setEngines] = useState([]);
  const [chosenEngine, setChosenEngine] = useState("qwen3");
  const [clips, setClips] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);

  async function refreshClips() {
    setClips(await api.listTextClips());
  }

  useEffect(() => {
    Promise.all([api.listVoices().then(setVoices), api.listEngines().then((r) => setEngines(r.engines)), refreshClips()])
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const anyInProgress = clips.some(textClipInProgress);
  const engine = effectiveEngine(chosenEngine, engines, Boolean(voiceProfileId));

  useEffect(() => {
    if (!anyInProgress) return;
    const id = setInterval(() => refreshClips().catch((e) => setError(e.message)), 1500);
    return () => clearInterval(id);
  }, [anyInProgress]);

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.createTextClip(text.trim(), voiceProfileId ? Number(voiceProfileId) : null, engine);
      setText("");
      await refreshClips();
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    try {
      await api.deleteTextClip(pendingDelete.id);
      setPendingDelete(null);
      await refreshClips();
    } catch (e) {
      setError(e.message);
    }
  }

  if (loading) return <p className="text-ink-muted">Loading…</p>;

  return (
    <div className="max-w-3xl">
      <PageHeader title="Try a line" />
      <p className="-mt-4 mb-6 text-ui text-ink-muted">
        Hear any passage in any voice. Handy for checking a narrator before you render a whole book.
      </p>

      <ErrorNote>{error}</ErrorNote>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4 mb-8">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="It was the best of times, it was the worst of times…"
          aria-label="Text to read aloud"
          required
          rows={6}
          className="rr-input font-serif text-list! leading-relaxed!"
        />

        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-wrap items-start gap-4">
          <label className="flex items-center gap-2 text-meta">
            <span className="text-ink-muted">Narrator</span>
            <select
              value={voiceProfileId}
              onChange={(e) => setVoiceProfileId(e.target.value)}
              className="rr-input w-auto!"
            >
              <option value="">Default voice</option>
              {voices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </label>
          <details className="group">
            <summary className="inline-flex h-10 items-center gap-1 text-meta font-semibold text-ink-muted hover:text-ink list-none [&::-webkit-details-marker]:hidden">
              Advanced
              <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />
            </summary>
            <div className="mt-1">
              <EnginePicker engines={engines} value={engine} onChange={setChosenEngine} hasVoice={Boolean(voiceProfileId)} />
            </div>
          </details>
          </div>

          <Button type="submit" loading={submitting} disabled={!text.trim()}>
            Read it aloud
          </Button>
        </div>
      </form>

      {clips.length === 0 ? (
        <p className="pt-8 text-center font-serif text-[19px] text-ink-muted">Lines you try will appear here.</p>
      ) : (
        <div className="border-t border-line divide-y divide-line">
          {clips.map((clip) => {
            const voiceName = voices.find((v) => v.id === clip.voice_profile_id)?.name || "Default";
            const engineName = engineLabel(clip.engine, engines);
            return (
              <div key={clip.id} className="py-4 flex flex-col gap-3">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <p className="font-serif text-[17px] leading-snug line-clamp-2">{clip.text}</p>
                    <p className="text-[12px] text-ink-muted mt-1">
                      {voiceName} · {engineName}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {clipStatus(clip) && <span className={`text-meta ${clipStatus(clip).tone}`}>{clipStatus(clip).text}</span>}
                    {clip.used_fallback && (
                      <span
                        title="The output folder wasn't writable when this clip finished — saved to the backup location instead."
                        className="text-[12px] text-accent-text"
                      >
                        saved to backup folder
                      </span>
                    )}
                    <button
                      onClick={() => setPendingDelete(clip)}
                      aria-label="Delete this line"
                      className="rounded-full p-1.5 text-ink-muted hover:bg-accent-soft hover:text-accent-text"
                    >
                      <Trash2 className="size-4" strokeWidth={1.7} aria-hidden="true" />
                    </button>
                  </div>
                </div>

                {clip.status === "generating" && (
                  <div className="max-w-xs">
                    <ProgressBar value={clip.progress} label="Progress" />
                  </div>
                )}

                {clip.error && (
                  <details className="group text-meta">
                    <summary className="inline-flex items-center gap-1 font-semibold text-ink-muted hover:text-ink list-none [&::-webkit-details-marker]:hidden">
                      Advanced
                      <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />
                    </summary>
                    <pre className="mt-2 rounded-input bg-oat p-3 font-mono text-[12px] whitespace-pre-wrap text-ink-muted">{clip.error}</pre>
                  </details>
                )}

                {clip.status === "done" && (
                  <div className="flex items-center gap-3">
                    <audio controls src={`/api/text-clips/${clip.id}/audio`} className="h-10 max-w-xs" />
                    <a
                      href={`/api/text-clips/${clip.id}/audio`}
                      download={`text-clip-${clip.id}.mp3`}
                      className="inline-flex items-center gap-1.5 text-meta text-ink-muted hover:text-ink rounded"
                    >
                      <Download className="size-4" strokeWidth={1.7} aria-hidden="true" />
                      Download
                    </a>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this line"
          message="Delete this line and its audio?"
          onConfirm={handleDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}

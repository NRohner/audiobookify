import { useEffect, useState } from "react";
import { Download, Send, Trash2, Type } from "lucide-react";
import { api, textClipInProgress } from "../lib/api.js";
import Badge from "../components/ui/Badge.jsx";
import Button from "../components/ui/Button.jsx";
import ProgressBar from "../components/ui/ProgressBar.jsx";
import ConfirmDialog from "../components/ui/ConfirmDialog.jsx";

export default function TextPage() {
  const [text, setText] = useState("");
  const [voiceProfileId, setVoiceProfileId] = useState("");
  const [voices, setVoices] = useState([]);
  const [clips, setClips] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);

  async function refreshClips() {
    setClips(await api.listTextClips());
  }

  useEffect(() => {
    Promise.all([api.listVoices().then(setVoices), refreshClips()])
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const anyInProgress = clips.some(textClipInProgress);

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
      await api.createTextClip(text.trim(), voiceProfileId ? Number(voiceProfileId) : null);
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

  if (loading) return <p className="text-muted-foreground">Loading…</p>;

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-semibold mb-6">Text to speech</h1>

      {error && <p className="text-sm text-destructive mb-4">{error}</p>}

      <form onSubmit={handleSubmit} className="flex flex-col gap-4 mb-8">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Type or paste some text to convert to speech…"
          required
          rows={6}
          className="w-full rounded-lg border border-border bg-background p-3 text-sm resize-y
            focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        />

        <div className="flex items-center justify-between gap-4">
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Voice</span>
            <select
              value={voiceProfileId}
              onChange={(e) => setVoiceProfileId(e.target.value)}
              className="h-10 rounded-lg border border-border bg-background px-3 text-sm cursor-pointer
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              <option value="">Default</option>
              {voices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </label>

          <Button type="submit" loading={submitting} disabled={!text.trim()}>
            <Send className="size-4" aria-hidden="true" />
            Convert to audio
          </Button>
        </div>
      </form>

      {clips.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border py-24 text-center">
          <Type className="size-10 text-muted-foreground mb-3" aria-hidden="true" />
          <p className="text-muted-foreground">No text conversions yet.</p>
        </div>
      ) : (
        <div className="rounded-xl border border-border divide-y divide-border overflow-hidden">
          {clips.map((clip) => {
            const voiceName = voices.find((v) => v.id === clip.voice_profile_id)?.name || "Default";
            return (
              <div key={clip.id} className="p-4 flex flex-col gap-3">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm line-clamp-2">{clip.text}</p>
                    <p className="text-xs text-muted-foreground mt-1">{voiceName}</p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Badge status={clip.status} label={clip.status} />
                    <button
                      onClick={() => setPendingDelete(clip)}
                      aria-label="Delete text clip"
                      className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive
                        focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                    >
                      <Trash2 className="size-4" aria-hidden="true" />
                    </button>
                  </div>
                </div>

                {clip.status === "generating" && (
                  <div className="max-w-xs">
                    <ProgressBar value={clip.progress} />
                  </div>
                )}

                {clip.error && <p className="text-sm text-destructive">{clip.error}</p>}

                {clip.status === "done" && (
                  <div className="flex items-center gap-3">
                    <audio controls src={`/api/text-clips/${clip.id}/audio`} className="h-10 max-w-xs" />
                    <a
                      href={`/api/text-clips/${clip.id}/audio`}
                      download={`text-clip-${clip.id}.mp3`}
                      className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground
                        focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring rounded"
                    >
                      <Download className="size-4" aria-hidden="true" />
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
          title="Delete text clip"
          message="Delete this text-to-speech conversion?"
          onConfirm={handleDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}

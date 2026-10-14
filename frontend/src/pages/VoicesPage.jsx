import { useEffect, useState } from "react";
import { AlertTriangle, AudioLines, Loader2, Mic, Play, Plus, RotateCcw, Save, Target, Trash2 } from "lucide-react";
import { api, textClipInProgress } from "../lib/api.js";
import { transcriptState } from "../lib/engines.js";
import { SWEET_SPOT, TEMPO_MAX, TEMPO_MIN, pacedWpm, sweetSpotTempo, tempoPosition } from "../lib/pace.js";
import Button from "../components/ui/Button.jsx";
import Modal from "../components/ui/Modal.jsx";
import ConfirmDialog from "../components/ui/ConfirmDialog.jsx";

function CloneVoiceModal({ onClose, onCloned }) {
  const [name, setName] = useState("");
  const [transcript, setTranscript] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", event.target.elements.file.files[0]);
      form.append("name", name);
      if (transcript.trim()) form.append("reference_transcript", transcript.trim());
      const voice = await api.createVoice(form);
      onCloned(voice);
    } catch (e) {
      setError(e.message);
      setSubmitting(false);
    }
  }

  return (
    <Modal title="Clone a voice" onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div>
          <label htmlFor="voice-name" className="block text-sm font-medium mb-1">
            Name
          </label>
          <input
            id="voice-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            className="w-full h-10 rounded-lg border border-border bg-background px-3 text-sm
              focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          />
        </div>

        <div>
          <label htmlFor="file" className="block text-sm font-medium mb-1">
            Reference audio (10-30s, clean recording)
          </label>
          <input
            id="file"
            type="file"
            name="file"
            accept="audio/*"
            required
            className="block w-full text-sm cursor-pointer file:mr-3 file:cursor-pointer file:rounded-lg
              file:border-0 file:bg-muted file:px-3 file:py-2 file:text-sm file:font-medium"
          />
        </div>

        <div>
          <label htmlFor="transcript" className="block text-sm font-medium mb-1">
            Reference transcript{" "}
            <span className="text-muted-foreground font-normal">(optional: leave empty to transcribe automatically)</span>
          </label>
          <textarea
            id="transcript"
            rows={3}
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            className="w-full rounded-lg border border-border bg-background p-3 text-sm resize-y
              focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          />
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="flex justify-end gap-3 mt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting}>
            Clone voice
          </Button>
        </div>
      </form>
    </Modal>
  );
}

const SAMPLE_TEXT =
  "The house stood at the end of a quiet lane, half hidden by an old hedge. " +
  "Nobody had lived there for years, or so the neighbours said, but on certain evenings a light still burned in the upper window.";

function wpmText(wpm) {
  return `${wpm.overall} wpm overall, ${wpm.speaking} speaking`;
}

function PaceSection({ voice, onChanged, onError }) {
  const [draft, setDraft] = useState(voice.tempo);
  const [lastSeen, setLastSeen] = useState(voice.tempo);
  if (voice.tempo !== lastSeen) {
    setLastSeen(voice.tempo);
    setDraft(voice.tempo);
  }
  const [saving, setSaving] = useState(false);
  const [previewTempo, setPreviewTempo] = useState(null);
  const [sample, setSample] = useState(null);
  const sweet = sweetSpotTempo(voice.pace);
  const measured = pacedWpm(voice.pace, 1);
  const atDraft = pacedWpm(voice.pace, draft);
  const dirty = draft !== voice.tempo;

  useEffect(() => {
    if (!sample || !textClipInProgress(sample)) return;
    const id = setInterval(
      () => api.getTextClip(sample.id).then(setSample).catch((e) => onError(e.message)),
      1500,
    );
    return () => clearInterval(id);
  }, [sample, onError]);

  async function save() {
    setSaving(true);
    try {
      await api.setVoiceTempo(voice.id, draft);
      await onChanged();
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function generateSample() {
    try {
      setSample(await api.createTextClip(SAMPLE_TEXT, voice.id, "qwen3", draft));
    } catch (e) {
      onError(e.message);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-border pt-3">
      <div className="flex items-baseline justify-between">
        <label htmlFor={`tempo-${voice.id}`} className="text-xs font-medium text-muted-foreground">
          Reference pace
        </label>
        <span className="text-sm font-medium tabular-nums">{draft.toFixed(2)}x</span>
      </div>
      {measured ? (
        <p className="text-xs text-muted-foreground">Measured: {wpmText(measured)}</p>
      ) : (
        <p className="text-xs text-muted-foreground">Pace is measured once the clip is transcribed.</p>
      )}
      <div className="relative pb-5">
        <input
          id={`tempo-${voice.id}`}
          type="range"
          min={TEMPO_MIN}
          max={TEMPO_MAX}
          step={0.01}
          value={draft}
          onChange={(e) => setDraft(Number(e.target.value))}
          className="w-full cursor-pointer accent-primary"
        />
        {sweet != null && (
          <button
            type="button"
            onClick={() => setDraft(sweet)}
            title="Move the slider to the sweet spot (not saved)"
            className="absolute bottom-0 -translate-x-1/2 cursor-pointer rounded text-[11px] text-primary whitespace-nowrap
              flex items-center gap-0.5 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
            style={{ left: `${tempoPosition(sweet)}%` }}
          >
            <Target className="size-3" aria-hidden="true" />
            sweet spot {sweet.toFixed(2)}x
          </button>
        )}
      </div>
      {atDraft && <p className="text-sm tabular-nums">At {draft.toFixed(2)}x: {wpmText(atDraft)}</p>}
      <p className="text-xs text-muted-foreground">
        Sweet spot: about {SWEET_SPOT.overall} wpm overall ({SWEET_SPOT.speaking} speaking), the reference pace behind
        the best narration so far. These figures describe the reference clip; the narration follows only part of a
        tempo change, so its pace is approximate. Listen to a sample before saving.
      </p>
      <div className="flex flex-wrap items-center gap-2 [&>button]:whitespace-nowrap">
        <Button variant="ghost" className="h-8 px-3" onClick={() => setPreviewTempo(draft)}>
          <Play className="size-4" aria-hidden="true" />
          Preview reference
        </Button>
        <Button
          variant="ghost"
          className="h-8 px-3"
          onClick={generateSample}
          disabled={!voice.reference_transcript || (sample && textClipInProgress(sample))}
        >
          <AudioLines className="size-4" aria-hidden="true" />
          Generate sample
        </Button>
        <Button onClick={save} loading={saving} disabled={!dirty} className="h-8 px-3">
          <Save className="size-4" aria-hidden="true" />
          Save tempo
        </Button>
        {dirty && (
          <Button variant="ghost" className="h-8 px-3" onClick={() => setDraft(voice.tempo)}>
            <RotateCcw className="size-4" aria-hidden="true" />
            Revert
          </Button>
        )}
      </div>
      {previewTempo != null && (
        <div className="flex flex-col gap-1">
          <p className="text-xs text-muted-foreground">Reference at {previewTempo.toFixed(2)}x (not saved)</p>
          <audio controls autoPlay src={`/api/voices/${voice.id}/audio?tempo=${previewTempo}`} className="h-9 w-full" />
        </div>
      )}
      {sample && (
        <div className="flex flex-col gap-1">
          <p className="text-xs text-muted-foreground">
            Sample (Quality engine) at {sample.tempo.toFixed(2)}x
            {textClipInProgress(sample) && `: generating ${Math.round((sample.progress || 0) * 100)}%`}
          </p>
          {sample.status === "done" && (
            <audio controls src={`/api/text-clips/${sample.id}/audio`} className="h-9 w-full" />
          )}
          {sample.status === "failed" && <p className="text-xs text-destructive">{sample.error}</p>}
        </div>
      )}
    </div>
  );
}

function VoiceCard({ voice, onDelete, onChanged, onError }) {
  const [draft, setDraft] = useState(voice.reference_transcript || "");
  const [saving, setSaving] = useState(false);
  const [confirmRetranscribe, setConfirmRetranscribe] = useState(false);
  const state = transcriptState(voice);

  // Pick up a new server transcript (Whisper finished) unless the user is mid-edit.
  const [lastSeen, setLastSeen] = useState(voice.reference_transcript);
  if (voice.reference_transcript !== lastSeen) {
    setLastSeen(voice.reference_transcript);
    setDraft(voice.reference_transcript || "");
  }
  const dirty = draft.trim() !== (voice.reference_transcript || "") && draft.trim() !== "";

  async function save() {
    setSaving(true);
    try {
      await api.updateVoiceTranscript(voice.id, draft.trim());
      await onChanged();
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function transcribe() {
    setConfirmRetranscribe(false);
    try {
      await api.transcribeVoice(voice.id);
      await onChanged();
    } catch (e) {
      onError(e.message);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <Mic className="size-5 text-primary" aria-hidden="true" />
          <h2 className="font-semibold">{voice.name}</h2>
        </div>
        <button
          onClick={() => onDelete(voice)}
          aria-label={`Delete voice ${voice.name}`}
          className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive
            focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <Trash2 className="size-4" aria-hidden="true" />
        </button>
      </div>

      <audio controls src={`/api/voices/${voice.id}/audio?tempo=${voice.tempo}`} className="h-9 w-full" />

      <div className="flex flex-col gap-1.5">
        <label htmlFor={`transcript-${voice.id}`} className="text-xs font-medium text-muted-foreground">
          Transcript
        </label>
        {state.key === "pending" ? (
          <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            {state.label}
          </p>
        ) : (
          <>
            {state.key !== "done" && (
              <p className={`text-sm ${state.key === "failed" ? "text-destructive" : "text-muted-foreground"}`}>
                {state.label}
              </p>
            )}
            <textarea
              id={`transcript-${voice.id}`}
              rows={4}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="What the reference clip says, word for word"
              className="w-full rounded-lg border border-border bg-background p-2 text-sm resize-y
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            />
            {state.note && <p className="text-xs text-muted-foreground">{state.note}</p>}
            <div className="flex flex-wrap items-center gap-2 [&>button]:whitespace-nowrap">
              <Button onClick={save} loading={saving} disabled={!dirty} className="h-8 px-3">
                <Save className="size-4" aria-hidden="true" />
                Save
              </Button>
              <Button
                variant="ghost"
                className="h-8 px-3"
                onClick={() => (voice.reference_transcript ? setConfirmRetranscribe(true) : transcribe())}
              >
                <RotateCcw className="size-4" aria-hidden="true" />
                {voice.reference_transcript ? "Transcribe again" : "Transcribe"}
              </Button>
            </div>
          </>
        )}
      </div>

      <PaceSection voice={voice} onChanged={onChanged} onError={onError} />

      {confirmRetranscribe && (
        <ConfirmDialog
          title="Transcribe again"
          message="Replace this transcript (including your edits) with a fresh automatic one?"
          onConfirm={transcribe}
          onCancel={() => setConfirmRetranscribe(false)}
        />
      )}
    </div>
  );
}

export default function VoicesPage() {
  const [voices, setVoices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [warnings, setWarnings] = useState([]);
  const [showClone, setShowClone] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);

  async function refresh() {
    setVoices(await api.listVoices());
  }

  useEffect(() => {
    refresh()
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const anyTranscribing = voices.some((v) => v.transcript_status === "pending");

  useEffect(() => {
    if (!anyTranscribing) return;
    const id = setInterval(() => refresh().catch((e) => setError(e.message)), 2000);
    return () => clearInterval(id);
  }, [anyTranscribing]);

  async function handleCloned(voice) {
    setShowClone(false);
    setWarnings(voice.warnings || []);
    await refresh();
  }

  async function handleDelete() {
    try {
      await api.deleteVoice(pendingDelete.id);
      setPendingDelete(null);
      await refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  if (loading) return <p className="text-muted-foreground">Loading…</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold">Voice library</h1>
        <Button onClick={() => setShowClone(true)}>
          <Plus className="size-4" aria-hidden="true" />
          Clone a voice
        </Button>
      </div>

      {error && <p className="text-sm text-destructive mb-4">{error}</p>}

      {warnings.length > 0 && (
        <div className="mb-4 rounded-lg border border-accent/30 bg-accent/10 p-3 text-sm text-accent flex gap-2">
          <AlertTriangle className="size-4 shrink-0 mt-0.5" aria-hidden="true" />
          <ul className="list-disc list-inside">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {voices.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border py-24 text-center">
          <Mic className="size-10 text-muted-foreground mb-3" aria-hidden="true" />
          <p className="text-muted-foreground mb-4">No cloned voices yet.</p>
          <Button onClick={() => setShowClone(true)}>
            <Plus className="size-4" aria-hidden="true" />
            Clone a voice
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
          {voices.map((voice) => (
            <VoiceCard key={voice.id} voice={voice} onDelete={setPendingDelete} onChanged={refresh} onError={setError} />
          ))}
        </div>
      )}

      {showClone && <CloneVoiceModal onClose={() => setShowClone(false)} onCloned={handleCloned} />}

      {pendingDelete && (
        <ConfirmDialog
          title="Delete voice"
          message={`Delete "${pendingDelete.name}"? Books using this voice will fall back to the default.`}
          onConfirm={handleDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}

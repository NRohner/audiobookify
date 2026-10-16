import { useEffect, useReducer, useRef, useState } from "react";
import { AudioLines, ChevronRight, Download, FolderOpen, Loader2, Mic, Play, Plus, RotateCcw, Save, Square, Target, Trash2 } from "lucide-react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";
import { api } from "../lib/api.js";
import { textClipInProgress } from "../lib/status.js";
import FileAudio from "../components/FileAudio.jsx";
import { transcriptState } from "../lib/engines.js";
import { SWEET_SPOT, TEMPO_MAX, TEMPO_MIN, pacedWpm, sweetSpotTempo, tempoPosition } from "../lib/pace.js";
import Button, { ErrorNote, PageHeader } from "../components/ui/Button.jsx";
import ClipEditor from "../components/ClipEditor.jsx";
import { START, clipFlow, exportName, recordErrorText } from "../lib/clipFlow.js";
import { formatClock } from "../lib/player.js";
import Modal from "../components/ui/Modal.jsx";
import ConfirmDialog from "../components/ui/ConfirmDialog.jsx";

const AUDIO_FILTER = { name: "Audio", extensions: ["wav", "mp3", "flac", "ogg", "m4a", "aif", "aiff"] };

function SourceTile({ icon: Icon, title, note, onClick, disabled, busy = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex items-center gap-3 rounded-tile border border-line bg-card p-4 text-left transition-colors
        duration-150 hover:bg-oat disabled:cursor-not-allowed disabled:opacity-45"
    >
      <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-text">
        {busy ? (
          <Loader2 className="size-[18px] animate-spin" strokeWidth={1.7} aria-hidden="true" />
        ) : (
          <Icon className="size-[18px]" strokeWidth={1.7} aria-hidden="true" />
        )}
      </span>
      <span className="min-w-0">
        <span className="block text-ui font-semibold">{title}</span>
        <span className="block text-meta text-ink-muted">{note}</span>
      </span>
    </button>
  );
}

function Recording({ startedAt, stopping, onStop }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="flex flex-col items-center gap-4 rounded-tile border border-line bg-oat px-4 py-6 text-center">
      <p role="status" className="flex items-center gap-2 text-ui font-semibold">
        <span aria-hidden="true" className="size-2.5 rounded-full bg-accent animate-pulse" />
        Recording
        <span className="font-normal tabular-nums text-ink-muted">{formatClock((now - startedAt) / 1000)}</span>
      </p>
      <p className="text-meta text-ink-muted">
        Play the voice you want on this Mac, then stop. 10 to 30 seconds of clear speech is plenty.
      </p>
      <Button type="button" onClick={onStop} loading={stopping}>
        {!stopping && <Square className="size-3.5 fill-current" aria-hidden="true" />}
        Stop
      </Button>
    </div>
  );
}

function CloneVoiceModal({ onClose, onCloned }) {
  const [flow, dispatch] = useReducer(clipFlow, START);
  const [name, setName] = useState("");
  const [transcript, setTranscript] = useState("");
  const [keep, setKeep] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [exported, setExported] = useState(null);
  const [error, setError] = useState(null);

  // Closing mid-take stops the recorder; a start still in flight is stopped when it lands.
  const live = useRef(true);
  const stage = useRef(flow.stage);
  stage.current = flow.stage;
  useEffect(() => {
    live.current = true; // StrictMode remounts after a cleanup in dev
    return () => {
      live.current = false;
      if (stage.current === "recording") api.stopRecording().catch(() => {});
    };
  }, []);

  async function record() {
    setError(null);
    dispatch({ type: "record" });
    try {
      await api.startRecording();
      if (!live.current) return void api.stopRecording().catch(() => {});
      dispatch({ type: "started", at: Date.now() });
    } catch (e) {
      dispatch({ type: "error", error: e });
    }
  }

  async function stop() {
    dispatch({ type: "stop" });
    try {
      dispatch({ type: "stopped", path: await api.stopRecording() });
    } catch (e) {
      dispatch({ type: "error", error: e });
    }
  }

  async function chooseFile() {
    setError(null);
    try {
      const picked = await open({ multiple: false, filters: [AUDIO_FILTER] });
      if (picked) dispatch({ type: "picked", path: picked });
    } catch (e) {
      setError(e.message);
    }
  }

  function startOver() {
    setKeep(null);
    setExported(null);
    setError(null);
    dispatch({ type: "reset" });
  }

  async function exportToFile() {
    setError(null);
    try {
      const dest = await save({ defaultPath: exportName(name), filters: [{ name: "Audio", extensions: ["wav", "mp3"] }] });
      if (dest) setExported(await api.exportMonoClip(flow.path, dest, keep));
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      // ponytail: a file the editor couldn't open (no keep list) is cloned as-is, like before step 5.
      const voice = keep
        ? await api.createVoiceFromClip(flow.path, keep, name, transcript.trim())
        : await api.createVoice(flow.path, name, transcript.trim());
      onCloned(voice);
    } catch (e) {
      setError(e.message);
      setSubmitting(false);
    }
  }

  const editing = flow.stage === "editing";
  const problem = flow.stage === "denied" ? recordErrorText({ kind: "permission_denied" })
    : flow.stage === "failed" ? recordErrorText(new Error(flow.message)) : null;

  return (
    <Modal title="Clone a new voice" onClose={onClose} wide>
      {(flow.stage === "recording" || flow.stage === "stopping") && (
        <Recording startedAt={flow.startedAt} stopping={flow.stage === "stopping"} onStop={stop} />
      )}

      {!editing && flow.stage !== "recording" && flow.stage !== "stopping" && (
        <div className="flex flex-col gap-4">
          <p className="text-meta text-ink-muted">
            You need 10 to 30 seconds of one person speaking clearly, with no music behind them.
          </p>
          {problem && (
            <div role="alert" className="rounded-tile bg-accent-soft px-4 py-3 text-accent-text">
              <p className="text-ui font-semibold">{problem.title}</p>
              <p className="mt-1 text-meta">{problem.body}</p>
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <SourceTile
              icon={Mic}
              busy={flow.stage === "starting"}
              title={problem ? "Try recording again" : "Record"}
              note="Capture a video or clip playing on this Mac"
              onClick={record}
              disabled={flow.stage === "starting"}
            />
            <SourceTile
              icon={FolderOpen}
              title="Edit an existing file"
              note="Trim a recording you already have"
              onClick={chooseFile}
              disabled={flow.stage === "starting"}
            />
          </div>
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}

      {editing && (
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <ClipEditor src={convertFileSrc(flow.path)} onChange={setKeep} />

          <div>
            <label htmlFor="voice-name" className="block text-meta font-semibold mb-1.5">
              Name
            </label>
            <input
              id="voice-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="rr-input"
            />
          </div>

          <div>
            <label htmlFor="transcript" className="block text-meta font-semibold mb-1.5">
              What it says{" "}
              <span className="text-ink-muted font-normal">(optional, we'll listen and fill this in)</span>
            </label>
            <textarea
              id="transcript"
              rows={3}
              value={transcript}
              onChange={(e) => setTranscript(e.target.value)}
              className="rr-input"
            />
          </div>

          <ErrorNote>{error}</ErrorNote>
          {exported && (
            <p role="status" className="truncate text-meta text-sage" title={exported}>
              Saved a copy to {exported}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" variant="quiet" onClick={startOver} className="-ml-2">
              <RotateCcw className="size-4" aria-hidden="true" />
              {flow.recorded ? "Record again" : "Choose another"}
            </Button>
            <span className="flex-1" />
            <Button type="button" variant="secondary" onClick={exportToFile} disabled={!keep}>
              <Download className="size-4" aria-hidden="true" />
              Export to file…
            </Button>
            <Button type="submit" loading={submitting} disabled={flow.recorded && !keep}>
              Save voice
            </Button>
          </div>
        </form>
      )}
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
    <div className="flex flex-col gap-2 border-t border-line pt-4">
      <div className="flex items-baseline justify-between">
        <label htmlFor={`tempo-${voice.id}`} className="text-meta font-semibold">
          Pace
        </label>
        <span className="text-meta font-medium tabular-nums">{draft.toFixed(2)}x</span>
      </div>
      {measured ? (
        <p className="text-[12px] text-ink-muted">Measured: {wpmText(measured)}</p>
      ) : (
        <p className="text-[12px] text-ink-muted">Pace is measured once the clip is transcribed.</p>
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
          className="w-full cursor-pointer accent-(--rr-accent)"
        />
        {sweet != null && (
          <button
            type="button"
            onClick={() => setDraft(sweet)}
            title="Move the slider to the sweet spot (not saved)"
            className="absolute bottom-0 -translate-x-1/2 rounded text-[11px] font-semibold text-accent-text whitespace-nowrap
              flex items-center gap-0.5 hover:underline"
            style={{ left: `${tempoPosition(sweet)}%` }}
          >
            <Target className="size-3" aria-hidden="true" />
            sweet spot {sweet.toFixed(2)}x
          </button>
        )}
      </div>
      {atDraft && <p className="text-meta tabular-nums">At {draft.toFixed(2)}x: {wpmText(atDraft)}</p>}
      <p className="text-[12px] text-ink-muted">
        Sweet spot: about {SWEET_SPOT.overall} wpm overall ({SWEET_SPOT.speaking} speaking), the reference pace behind
        the best narration so far. These figures describe the reference clip; the narration follows only part of a
        tempo change, so its pace is approximate. Listen to a sample before saving.
      </p>
      <div className="flex flex-wrap items-center gap-2 [&>button]:whitespace-nowrap -ml-2">
        <Button variant="quiet" onClick={() => setPreviewTempo(draft)}>
          <Play className="size-4" aria-hidden="true" />
          Preview reference
        </Button>
        <Button
          variant="quiet"
         
          onClick={generateSample}
          disabled={!voice.reference_transcript || (sample && textClipInProgress(sample))}
        >
          <AudioLines className="size-4" aria-hidden="true" />
          Hear a sample
        </Button>
        <Button variant="secondary" onClick={save} loading={saving} disabled={!dirty} className="h-8! px-3.5!">
          <Save className="size-4" aria-hidden="true" />
          Save tempo
        </Button>
        {dirty && (
          <Button variant="quiet" onClick={() => setDraft(voice.tempo)}>
            <RotateCcw className="size-4" aria-hidden="true" />
            Revert
          </Button>
        )}
      </div>
      {previewTempo != null && (
        <div className="flex flex-col gap-1">
          <p className="text-[12px] text-ink-muted">Reference at {previewTempo.toFixed(2)}x (not saved)</p>
          <FileAudio
            load={() => api.voiceAudioSrc(voice.id, previewTempo)}
            cacheKey={`${voice.id}@${previewTempo}@${voice.reference_trim_end}`}
            autoPlay
            className="h-9 w-full"
          />
        </div>
      )}
      {sample && (
        <div className="flex flex-col gap-1">
          <p className="text-[12px] text-ink-muted">
            Sample (Quality engine) at {sample.tempo.toFixed(2)}x
            {textClipInProgress(sample) && `: generating ${Math.round((sample.progress || 0) * 100)}%`}
          </p>
          {sample.status === "done" && (
            <FileAudio load={() => api.textClipAudioSrc(sample.id)} cacheKey={sample.id} className="h-9 w-full" />
          )}
          {sample.status === "failed" && <p className="text-[12px] text-accent-text">{sample.error}</p>}
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
    <li className="rounded-tile border border-line bg-card p-4 flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <span
          aria-hidden="true"
          className="flex size-8 shrink-0 items-center justify-center rounded-full font-serif text-[16px] text-ink"
          style={{ background: `var(--rr-voice-${(voice.id % 8) + 1}-chip)` }}
        >
          {voice.name.charAt(0).toUpperCase()}
        </span>
        <div className="min-w-0 mr-auto">
          <h2 className="text-[17px] truncate">{voice.name}</h2>
          {state.key === "pending" && <p className="text-[12px] text-ink-muted">Listening to the recording…</p>}
          {state.key === "failed" && <p className="text-[12px] text-accent-text">Couldn't make out the words. See Advanced.</p>}
        </div>
        <button
          type="button"
          onClick={() => onDelete(voice)}
          aria-label={`Delete voice ${voice.name}`}
          className="rounded-full p-1.5 text-ink-muted hover:bg-accent-soft hover:text-accent-text"
        >
          <Trash2 className="size-4" strokeWidth={1.7} aria-hidden="true" />
        </button>
      </div>

      <FileAudio
        load={() => api.voiceAudioSrc(voice.id, voice.tempo)}
        cacheKey={`${voice.id}@${voice.tempo}@${voice.reference_trim_end}`}
        className="h-9 w-full"
      />

      <details className="group">
        <summary className="inline-flex h-8 items-center gap-1 text-meta font-semibold text-ink-muted hover:text-ink list-none [&::-webkit-details-marker]:hidden">
          Advanced
          <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />
        </summary>
      <div className="mt-2 flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label htmlFor={`transcript-${voice.id}`} className="text-meta font-semibold">
          What the recording says
        </label>
        {state.key === "pending" ? (
          <p className="flex items-center gap-1.5 text-meta text-ink-muted">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            {state.label}
          </p>
        ) : (
          <>
            {state.key !== "done" && (
              <p className={`text-meta ${state.key === "failed" ? "text-accent-text" : "text-ink-muted"}`}>
                {state.label}
              </p>
            )}
            <textarea
              id={`transcript-${voice.id}`}
              rows={4}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="What the reference clip says, word for word"
              className="rr-input"
            />
            {state.note && <p className="text-[12px] text-ink-muted">{state.note}</p>}
            <div className="flex flex-wrap items-center gap-2 [&>button]:whitespace-nowrap -ml-2">
              <Button variant="secondary" onClick={save} loading={saving} disabled={!dirty} className="h-8! px-3.5!">
                <Save className="size-4" aria-hidden="true" />
                Save
              </Button>
              <Button
                variant="quiet"
               
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
      </div>
      </details>

      {confirmRetranscribe && (
        <ConfirmDialog
          title="Transcribe again"
          message="Replace this transcript (including your edits) with a fresh automatic one?"
          onConfirm={transcribe}
          onCancel={() => setConfirmRetranscribe(false)}
        />
      )}
    </li>
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

  if (loading) return <p className="text-ink-muted">Loading…</p>;

  return (
    <div className="max-w-[1100px]">
      <PageHeader title="Voices">
        <Button onClick={() => setShowClone(true)}>
          <Plus className="size-4" strokeWidth={2} aria-hidden="true" />
          Clone a voice
        </Button>
      </PageHeader>

      <ErrorNote>{error}</ErrorNote>

      {warnings.length > 0 && (
        <div role="status" className="mb-5 rounded-tile bg-accent-soft px-4 py-3 text-meta text-accent-text">
          <ul className="list-disc list-inside">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {voices.length === 0 ? (
        <div className="pt-12 text-center">
          <p className="font-serif text-section">No voices yet.</p>
          <p className="mt-2 text-ui text-ink-muted">
            Record half a minute of someone reading, and they can narrate your books.
          </p>
        </div>
      ) : (
        <ul className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-[18px] items-start">
          {voices.map((voice) => (
            <VoiceCard key={voice.id} voice={voice} onDelete={setPendingDelete} onChanged={refresh} onError={setError} />
          ))}
        </ul>
      )}

      {showClone && <CloneVoiceModal onClose={() => setShowClone(false)} onCloned={handleCloned} />}

      {pendingDelete && (
        <ConfirmDialog
          title="Delete voice"
          message={`Delete “${pendingDelete.name}”? Books narrated by this voice will use the default voice instead.`}
          onConfirm={handleDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}

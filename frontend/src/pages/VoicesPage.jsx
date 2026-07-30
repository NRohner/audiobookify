import { useEffect, useState } from "react";
import { AlertTriangle, ChevronDown, Mic, Plus, Trash2 } from "lucide-react";
import { api } from "../lib/api.js";
import Button from "../components/ui/Button.jsx";
import Modal from "../components/ui/Modal.jsx";
import ConfirmDialog from "../components/ui/ConfirmDialog.jsx";

function CloneVoiceModal({ onClose, onCloned }) {
  const [name, setName] = useState("");
  const [transcript, setTranscript] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [exaggeration, setExaggeration] = useState(0.5);
  const [cfgWeight, setCfgWeight] = useState(0.5);
  const [temperature, setTemperature] = useState(0.8);
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
      if (transcript) form.append("reference_transcript", transcript);
      form.append("exaggeration", String(exaggeration));
      form.append("cfg_weight", String(cfgWeight));
      form.append("temperature", String(temperature));
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
            Reference transcript <span className="text-muted-foreground font-normal">(optional)</span>
          </label>
          <input
            id="transcript"
            type="text"
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            className="w-full h-10 rounded-lg border border-border bg-background px-3 text-sm
              focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          />
        </div>

        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          aria-expanded={showAdvanced}
          className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground
            cursor-pointer self-start focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring rounded"
        >
          <ChevronDown className={`size-4 transition-transform duration-150 ${showAdvanced ? "rotate-180" : ""}`} aria-hidden="true" />
          Advanced settings
        </button>

        {showAdvanced && (
          <div className="flex flex-col gap-3 rounded-lg bg-muted p-3">
            <SliderField label="Exaggeration" value={exaggeration} onChange={setExaggeration} />
            <SliderField label="CFG weight" value={cfgWeight} onChange={setCfgWeight} />
            <SliderField label="Temperature" value={temperature} onChange={setTemperature} max={2} />
          </div>
        )}

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

function SliderField({ label, value, onChange, max = 1 }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="flex justify-between">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-mono">{value.toFixed(2)}</span>
      </span>
      <input
        type="range"
        min={0}
        max={max}
        step={0.05}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="cursor-pointer accent-primary"
      />
    </label>
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
            <div key={voice.id} className="rounded-xl border border-border bg-surface p-4">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2">
                  <Mic className="size-5 text-primary" aria-hidden="true" />
                  <h2 className="font-semibold">{voice.name}</h2>
                </div>
                <button
                  onClick={() => setPendingDelete(voice)}
                  aria-label={`Delete voice ${voice.name}`}
                  className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive
                    focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <Trash2 className="size-4" aria-hidden="true" />
                </button>
              </div>
              <p className="text-xs text-muted-foreground mt-2 font-mono">
                exaggeration {voice.exaggeration} · cfg {voice.cfg_weight} · temp {voice.temperature}
              </p>
            </div>
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

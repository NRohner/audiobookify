import { useEffect, useState } from "react";
import { Pencil, Plus, SpellCheck2, Trash2 } from "lucide-react";
import { api } from "../lib/api.js";
import Button from "../components/ui/Button.jsx";
import Modal from "../components/ui/Modal.jsx";
import ConfirmDialog from "../components/ui/ConfirmDialog.jsx";

function AddPronunciationModal({ onClose, onAdded }) {
  const [term, setTerm] = useState("");
  const [overrideValue, setOverrideValue] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const entry = await api.createPronunciation({
        term,
        override_value: overrideValue,
        notes: notes || null,
      });
      onAdded(entry);
    } catch (e) {
      setError(e.message);
      setSubmitting(false);
    }
  }

  return (
    <Modal title="Add pronunciation" onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Field label="Term" value={term} onChange={setTerm} placeholder="Cadeyrn" required />
        <Field
          label="Pronounced as"
          value={overrideValue}
          onChange={setOverrideValue}
          placeholder="KAH-dern"
          required
        />
        <Field label="Notes (optional)" value={notes} onChange={setNotes} />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-3 mt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting}>
            Add
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function EditPronunciationModal({ entry, onClose, onSaved }) {
  const [overrideValue, setOverrideValue] = useState(entry.override_value);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const updated = await api.updatePronunciation(entry.term, overrideValue);
      onSaved(updated);
    } catch (e) {
      setError(e.message);
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Edit "${entry.term}"`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Field label="Pronounced as" value={overrideValue} onChange={setOverrideValue} required />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-3 mt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting}>
            Save
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function Field({ label, value, onChange, placeholder, required }) {
  return (
    <label className="block">
      <span className="block text-sm font-medium mb-1">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        className="w-full h-10 rounded-lg border border-border bg-background px-3 text-sm
          focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      />
    </label>
  );
}

export default function PronunciationsPage() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);

  async function refresh() {
    setEntries(await api.listPronunciations());
  }

  useEffect(() => {
    refresh()
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  async function handleDelete() {
    try {
      await api.deletePronunciation(pendingDelete.term);
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
        <h1 className="text-2xl font-semibold">Pronunciations</h1>
        <Button onClick={() => setShowAdd(true)}>
          <Plus className="size-4" aria-hidden="true" />
          Add pronunciation
        </Button>
      </div>

      {error && <p className="text-sm text-destructive mb-4">{error}</p>}

      {entries.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border py-24 text-center">
          <SpellCheck2 className="size-10 text-muted-foreground mb-3" aria-hidden="true" />
          <p className="text-muted-foreground mb-4">No custom pronunciations yet.</p>
          <Button onClick={() => setShowAdd(true)}>
            <Plus className="size-4" aria-hidden="true" />
            Add pronunciation
          </Button>
        </div>
      ) : (
        <div className="rounded-xl border border-border divide-y divide-border overflow-hidden">
          {entries.map((entry) => (
            <div key={entry.term} className="p-4 flex items-center gap-4">
              <div className="flex-1 min-w-0">
                <p className="font-medium">{entry.term}</p>
                <p className="text-sm text-muted-foreground">
                  &rarr; {entry.override_value}
                  {entry.notes && <span className="ml-2">({entry.notes})</span>}
                </p>
              </div>
              <button
                onClick={() => setEditing(entry)}
                aria-label={`Edit pronunciation for ${entry.term}`}
                className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground
                  focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                <Pencil className="size-4" aria-hidden="true" />
              </button>
              <button
                onClick={() => setPendingDelete(entry)}
                aria-label={`Delete pronunciation for ${entry.term}`}
                className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive
                  focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                <Trash2 className="size-4" aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}

      {showAdd && (
        <AddPronunciationModal
          onClose={() => setShowAdd(false)}
          onAdded={async () => {
            setShowAdd(false);
            await refresh();
          }}
        />
      )}

      {editing && (
        <EditPronunciationModal
          entry={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await refresh();
          }}
        />
      )}

      {pendingDelete && (
        <ConfirmDialog
          title="Delete pronunciation"
          message={`Delete the pronunciation override for "${pendingDelete.term}"?`}
          onConfirm={handleDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}

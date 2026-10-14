import { useEffect, useState } from "react";
import { Pencil, Plus, Search, Trash2 } from "lucide-react";
import { api } from "../lib/api.js";
import Button, { ErrorNote, PageHeader } from "../components/ui/Button.jsx";
import Modal from "../components/ui/Modal.jsx";
import ConfirmDialog from "../components/ui/ConfirmDialog.jsx";

function Field({ label, hint, value, onChange, placeholder, required, large = false }) {
  return (
    <label className="block">
      <span className="block text-meta font-semibold mb-1.5">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        className={
          large
            ? "w-full border-0 border-b border-line-strong bg-transparent pb-1 font-serif text-[30px] placeholder:text-ink-muted/50"
            : "rr-input"
        }
      />
      {hint && <span className="block mt-1 text-[12px] text-ink-muted">{hint}</span>}
    </label>
  );
}

function WordModal({ entry, onClose, onSaved }) {
  const [term, setTerm] = useState(entry?.term ?? "");
  const [overrideValue, setOverrideValue] = useState(entry?.override_value ?? "");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (entry) await api.updatePronunciation(entry.term, overrideValue);
      else await api.createPronunciation({ term, override_value: overrideValue, notes: notes || null });
      onSaved();
    } catch (e) {
      setError(e.message);
      setSubmitting(false);
    }
  }

  return (
    <Modal title={entry ? `“${entry.term}”` : "Add a tricky word"} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        {!entry && <Field label="Word" value={term} onChange={setTerm} placeholder="Queequeg" required large />}
        <Field
          label="Sounds like"
          hint="Spell it the way it's said, with the stressed part in capitals."
          value={overrideValue}
          onChange={setOverrideValue}
          placeholder="KWEE-kweg"
          required
        />
        {!entry && <Field label="Note (optional)" value={notes} onChange={setNotes} placeholder="Harpooner, Moby-Dick" />}
        <ErrorNote>{error}</ErrorNote>
        <div className="flex justify-end gap-3">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting}>
            Save word
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function WordCard({ entry, onEdit, onDelete }) {
  return (
    <li
      className="group relative h-40 rounded-[6px] border border-line bg-card shadow-tag overflow-hidden"
      style={{
        backgroundImage:
          "repeating-linear-gradient(to bottom, transparent 0 25px, var(--rr-periwinkle-soft) 25px 26px)",
        backgroundPosition: "0 36px",
        backgroundRepeat: "no-repeat",
        backgroundSize: "100% calc(100% - 36px)",
      }}
    >
      <div className="flex h-9 items-end justify-between gap-3 border-b border-accent-deco/60 px-4 pb-1 bg-card">
        <span className="font-serif text-[25px] leading-none truncate">{entry.term}</span>
        <span className="font-serif italic text-meta text-ink-muted truncate">{entry.override_value}</span>
      </div>
      <div className="px-4 pt-3 text-[12px] leading-[26px] text-ink-muted">{entry.notes}</div>
      <div className="absolute bottom-2.5 right-2.5 flex gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
        <button
          type="button"
          onClick={onEdit}
          aria-label={`Change how ${entry.term} sounds`}
          className="rounded-full bg-card p-1.5 text-ink-muted hover:bg-oat hover:text-ink"
        >
          <Pencil className="size-4" strokeWidth={1.7} aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={onDelete}
          aria-label={`Remove ${entry.term}`}
          className="rounded-full bg-card p-1.5 text-ink-muted hover:bg-accent-soft hover:text-accent-text"
        >
          <Trash2 className="size-4" strokeWidth={1.7} aria-hidden="true" />
        </button>
      </div>
    </li>
  );
}

export default function PronunciationsPage() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null); // null | "new" | entry
  const [pendingDelete, setPendingDelete] = useState(null);
  const [query, setQuery] = useState("");

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

  if (loading) return <p className="text-ink-muted">Loading…</p>;

  const q = query.trim().toLowerCase();
  const visible = entries.filter((e) => !q || e.term.toLowerCase().includes(q));

  return (
    <div className="max-w-[1100px]">
      <PageHeader title="Tricky words">
        {entries.length > 0 && (
          <label className="relative">
            <span className="sr-only">Search tricky words</span>
            <Search
              className="absolute left-3.5 top-1/2 -translate-y-1/2 size-4 text-ink-muted"
              strokeWidth={1.7}
              aria-hidden="true"
            />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search"
              className="h-9 w-56 rounded-full border border-line bg-card pl-9 pr-4 text-meta placeholder:text-ink-muted"
            />
          </label>
        )}
        <Button onClick={() => setEditing("new")}>
          <Plus className="size-4" strokeWidth={2} aria-hidden="true" />
          Add a word
        </Button>
      </PageHeader>

      <p className="-mt-4 mb-8 max-w-xl text-ui text-ink-muted">
        Names and words the narrator gets wrong. Tell it how they sound once, and every book will say them your way.
      </p>

      <ErrorNote>{error}</ErrorNote>

      {entries.length === 0 ? (
        <div className="pt-12 text-center">
          <p className="font-serif text-section">No tricky words yet.</p>
          <p className="mt-2 text-ui text-ink-muted">When a name comes out wrong, add it here.</p>
        </div>
      ) : (
        <ul className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-[18px]">
          {visible.map((entry) => (
            <WordCard
              key={entry.term}
              entry={entry}
              onEdit={() => setEditing(entry)}
              onDelete={() => setPendingDelete(entry)}
            />
          ))}
        </ul>
      )}

      {editing && (
        <WordModal
          entry={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await refresh();
          }}
        />
      )}

      {pendingDelete && (
        <ConfirmDialog
          title="Remove word"
          message={`Remove “${pendingDelete.term}”? The narrator will go back to its own guess.`}
          confirmLabel="Remove"
          onConfirm={handleDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}

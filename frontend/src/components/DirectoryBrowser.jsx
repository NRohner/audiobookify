import { useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowUp, Check, Folder, Loader2 } from "lucide-react";
import { api } from "../lib/api.js";

export default function DirectoryBrowser({ value, onChange, onValidityChange }) {
  const [browsed, setBrowsed] = useState(null);
  const [browseError, setBrowseError] = useState(null);
  const [status, setStatus] = useState(null);
  const [checking, setChecking] = useState(false);
  const debounceRef = useRef(null);

  async function browse(path) {
    setBrowseError(null);
    try {
      const result = await api.browseDir(path);
      setBrowsed(result);
    } catch (e) {
      setBrowseError(path ? `Couldn't open ${path}: ${e.message}` : e.message);
    }
  }

  useEffect(() => {
    browse(null); // start at the home directory
  }, []);

  useEffect(() => {
    clearTimeout(debounceRef.current);
    if (!value.trim()) {
      setStatus(null);
      onValidityChange?.(false);
      return;
    }
    setChecking(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const result = await api.validateOutputDir(value.trim());
        setStatus(result);
        onValidityChange?.(result.writable);
      } catch (e) {
        setStatus({ writable: false, error: e.message });
        onValidityChange?.(false);
      } finally {
        setChecking(false);
      }
    }, 400);
    return () => clearTimeout(debounceRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  function selectFolder(path) {
    onChange(path);
    browse(path);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="rounded-input border border-line overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-line bg-oat text-meta">
          <button
            type="button"
            onClick={() => browsed?.parent && browse(browsed.parent)}
            disabled={!browsed?.parent}
            aria-label="Go up one folder"
            className="cursor-pointer rounded p-1 text-ink-muted hover:bg-oat
              disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ArrowUp className="size-4" aria-hidden="true" />
          </button>
          <span className="truncate font-mono text-[12px]" title={browsed?.path}>
            {browsed?.path || "Loading…"}
          </span>
        </div>

        {browseError && <p className="text-meta text-accent-text px-3 py-2">{browseError}</p>}

        <div className="max-h-48 overflow-y-auto divide-y divide-line">
          {browsed && browsed.entries.length === 0 && (
            <p className="text-meta text-ink-muted px-3 py-2">No subfolders here.</p>
          )}
          {browsed?.entries.map((entry) => (
            <button
              key={entry.name}
              type="button"
              disabled={!entry.writable}
              onClick={() => browse(`${browsed.path}/${entry.name}`)}
              title={entry.writable ? undefined : "You don't have permission to write here"}
              className="w-full flex items-center gap-2 px-3 py-2 text-meta text-left cursor-pointer
                hover:bg-oat disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
            >
              <Folder className="size-4 shrink-0 text-ink-muted" aria-hidden="true" />
              <span className="truncate">{entry.name}</span>
            </button>
          ))}
        </div>

        <div className="px-3 py-2 border-t border-line">
          <button
            type="button"
            onClick={() => browsed && selectFolder(browsed.path)}
            disabled={!browsed?.writable}
            className="text-meta font-medium text-accent-text hover:underline cursor-pointer
              disabled:cursor-not-allowed disabled:opacity-40 disabled:no-underline"
          >
            Use this folder
          </button>
        </div>
      </div>

      <div>
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="e.g. ~/audiobooks/this-book"
          className="rr-input"
        />
        <div className="mt-1 min-h-5 flex items-center gap-1.5 text-[12px]">
          {checking && (
            <>
              <Loader2 className="size-3.5 animate-spin text-ink-muted" aria-hidden="true" />
              <span className="text-ink-muted">Checking…</span>
            </>
          )}
          {!checking && status?.writable === true && (
            <>
              <Check className="size-3.5 text-sage" aria-hidden="true" />
              <span className="text-sage">This folder is writable</span>
            </>
          )}
          {!checking && status?.writable === false && (
            <>
              <AlertCircle className="size-3.5 text-accent-text" aria-hidden="true" />
              <span className="text-accent-text">{status.error || "Can't write here"}</span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { AlertCircle, Check, Folder, Loader2 } from "lucide-react";
import { api } from "../lib/api.js";
import PathPick from "./PathPick.jsx";

// Where the MP3s go: the macOS folder picker, then the same writable check as before.
export default function DirectoryBrowser({ value, onChange, onValidityChange }) {
  const [status, setStatus] = useState(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    if (!value) {
      setStatus(null);
      onValidityChange?.(false);
      return;
    }
    let live = true;
    setChecking(true);
    onValidityChange?.(false);
    api
      .validateOutputDir(value)
      .then(
        (result) => {
          if (!live) return;
          setStatus(result);
          onValidityChange?.(result.writable);
        },
        (e) => {
          if (!live) return;
          setStatus({ writable: false, error: e.message });
          onValidityChange?.(false);
        },
      )
      .finally(() => live && setChecking(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  async function choose() {
    try {
      const picked = await open({ directory: true, multiple: false, defaultPath: value || undefined });
      if (picked) onChange(picked);
    } catch (e) {
      setStatus({ writable: false, error: e.message });
      onValidityChange?.(false);
    }
  }

  return (
    <div>
      <PathPick label="Choose folder…" icon={Folder} path={value || null} onPick={choose} />
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
  );
}

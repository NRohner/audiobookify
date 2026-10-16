import { useEffect, useState } from "react";
import ClipEditor from "../components/ClipEditor.jsx";
import { PageHeader } from "../components/ui/Button.jsx";

// Dev-only harness: load any local audio file into the editor and watch the keep list.
export default function ClipEditorDevPage() {
  const [src, setSrc] = useState(null);
  const [keep, setKeep] = useState(null);

  useEffect(() => () => src && URL.revokeObjectURL(src), [src]);

  return (
    <div className="max-w-4xl">
      <PageHeader title="Clip editor" />
      <label className="mb-5 block text-meta text-ink-muted">
        Audio file
        <input
          type="file"
          accept="audio/*"
          className="mt-2 block text-ui text-ink"
          onChange={(e) => e.target.files[0] && setSrc(URL.createObjectURL(e.target.files[0]))}
        />
      </label>
      <ClipEditor src={src} onChange={setKeep} />
      <p className="rr-eyebrow mt-6">Keep list</p>
      <pre className="mt-2 font-mono text-meta tabular-nums text-ink">
        {keep ? JSON.stringify(keep.map(([s, e]) => [+s.toFixed(3), +e.toFixed(3)])) : "—"}
      </pre>
    </div>
  );
}

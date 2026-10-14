import { useEffect } from "react";
import { X } from "lucide-react";

export default function Modal({ title, onClose, children, wide = false }) {
  useEffect(() => {
    function handleKey(e) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-100 flex items-center justify-center bg-ink/35 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className={`w-full ${wide ? "max-w-xl" : "max-w-md"} max-h-[90dvh] overflow-y-auto rounded-panel bg-card
          border border-line shadow-popover p-6`}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-section">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-full p-1.5 text-ink-muted hover:bg-oat hover:text-ink"
          >
            <X className="size-[18px]" strokeWidth={1.7} aria-hidden="true" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

import { Link } from "react-router-dom";
import { Sparkles, Zap } from "lucide-react";

const ICONS = { qwen3: Sparkles, "chatterbox-turbo": Zap };

// Segmented choice between narration engines. An engine that needs a voice is disabled
// (with the reason shown) until one is picked; one that isn't downloaded is disabled with a
// link to Settings.
export default function EnginePicker({ engines, value, onChange, hasVoice, disabled = false }) {
  const missing = engines.filter((e) => e.ready === false);
  // Missing engines get their own note, so the voice note doesn't list them twice.
  const blocked = engines.filter((e) => e.ready !== false && e.needs_voice && !hasVoice);
  return (
    <div className="flex flex-col gap-1.5">
      <div role="radiogroup" aria-label="Engine" className="inline-flex self-start rounded-full border border-line p-[3px]">
        {engines.map((engine) => {
          const Icon = ICONS[engine.id];
          const unavailable = disabled || engine.ready === false || (engine.needs_voice && !hasVoice);
          const selected = value === engine.id;
          return (
            <button
              key={engine.id}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={unavailable}
              onClick={() => onChange(engine.id)}
              title={engine.description}
              className={`inline-flex items-center gap-1.5 h-7 px-3 rounded-full text-meta transition-colors duration-150
                disabled:cursor-not-allowed disabled:opacity-40
                ${selected ? "bg-card font-bold text-ink shadow-tag" : "font-semibold text-ink-muted hover:text-ink"}`}
            >
              {Icon && <Icon className="size-3.5" strokeWidth={1.7} aria-hidden="true" />}
              {engine.label}
            </button>
          );
        })}
      </div>
      {blocked.length > 0 && !disabled && (
        <p className="text-meta text-ink-muted">
          {blocked.map((e) => e.label).join(", ")} needs a narrator voice. Pick one to use it.
        </p>
      )}
      {missing.length > 0 && !disabled && (
        <p className="text-meta text-ink-muted">
          {missing.map((e) => e.label).join(", ")} {missing.length === 1 ? "isn't" : "aren't"} downloaded yet.{" "}
          <Link to="/settings" className="font-bold text-accent-text hover:underline">
            {missing.length === 1 ? "Get it in Settings" : "Get them in Settings"}
          </Link>
        </p>
      )}
    </div>
  );
}

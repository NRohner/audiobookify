import { Sparkles, Zap } from "lucide-react";

const ICONS = { qwen3: Sparkles, "chatterbox-turbo": Zap };

// Segmented choice between TTS engines. An engine that needs a voice is disabled
// (with the reason shown) until one is picked.
export default function EnginePicker({ engines, value, onChange, hasVoice, disabled = false }) {
  const blocked = engines.filter((e) => e.needs_voice && !hasVoice);
  return (
    <div className="flex flex-col gap-1">
      <div role="radiogroup" aria-label="Engine" className="inline-flex rounded-lg border border-border p-0.5 bg-background">
        {engines.map((engine) => {
          const Icon = ICONS[engine.id];
          const unavailable = disabled || (engine.needs_voice && !hasVoice);
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
              className={`inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-sm font-medium cursor-pointer
                transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring
                ${selected ? "bg-primary text-on-primary" : "text-muted-foreground hover:text-foreground"}`}
            >
              {Icon && <Icon className="size-4" aria-hidden="true" />}
              {engine.label}
            </button>
          );
        })}
      </div>
      {blocked.length > 0 && !disabled && (
        <p className="text-xs text-muted-foreground">
          {blocked.map((e) => e.label).join(", ")} needs a voice. Pick one to use it.
        </p>
      )}
    </div>
  );
}

import { useState } from "react";
import { Moon, Sun } from "lucide-react";

function currentlyDark() {
  const explicit = document.documentElement.dataset.theme;
  if (explicit) return explicit === "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export default function ThemeToggle() {
  const [isDark, setIsDark] = useState(currentlyDark);

  function toggle() {
    const next = isDark ? "light" : "dark";
    setIsDark(!isDark);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("theme", next);
    } catch {
      // Private mode: the choice just won't persist.
    }
  }

  const Icon = isDark ? Sun : Moon;
  return (
    <button
      type="button"
      onClick={toggle}
      className="flex h-10 w-full items-center gap-3 rounded-input px-3 text-ui text-ink-muted hover:bg-card/60 hover:text-ink"
    >
      <Icon className="size-[18px]" strokeWidth={1.7} aria-hidden="true" />
      {isDark ? "Light mode" : "Dark mode"}
    </button>
  );
}

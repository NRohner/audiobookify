import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { BookOpen, Home, Mic, Pause, Play, SpellCheck2, Type } from "lucide-react";
import { api } from "../lib/api.js";
import Button from "./ui/Button.jsx";
import ThemeToggle from "./ThemeToggle.jsx";

const navItems = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/text", label: "Text", icon: Type },
  { to: "/voices", label: "Voices", icon: Mic },
  { to: "/pronunciations", label: "Pronunciations", icon: SpellCheck2 },
];

export default function AppShell() {
  const [paused, setPaused] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState(null);

  async function refreshStatus() {
    const status = await api.getWorkerStatus();
    setPaused(status.paused);
  }

  useEffect(() => {
    refreshStatus().catch(() => {});
    const id = setInterval(() => refreshStatus().catch(() => {}), 2000);
    return () => clearInterval(id);
  }, []);

  async function handleToggle() {
    setToggling(true);
    setError(null);
    try {
      if (paused) {
        await api.resumeWorker();
      } else {
        await api.pauseWorker();
      }
      await refreshStatus();
    } catch (e) {
      setError(e.message);
    } finally {
      setToggling(false);
    }
  }

  return (
    <div className="min-h-dvh flex">
      <aside className="w-16 sm:w-60 shrink-0 border-r border-border bg-surface flex flex-col">
        <div className="flex items-center justify-center sm:justify-start gap-2 px-2 sm:px-5 h-16 border-b border-border">
          <BookOpen className="size-6 text-primary shrink-0" aria-hidden="true" />
          <span className="hidden sm:inline font-semibold text-lg">Audiobookify</span>
        </div>
        <nav className="flex-1 p-2 sm:p-3 flex flex-col gap-1">
          {navItems.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              title={label}
              aria-label={label}
              className={({ isActive }) =>
                `flex items-center justify-center sm:justify-start gap-3 rounded-lg px-0 sm:px-3 h-10 text-sm font-medium transition-colors duration-150
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`
              }
            >
              <Icon className="size-4.5 shrink-0" aria-hidden="true" />
              <span className="hidden sm:inline">{label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-16 border-b border-border flex items-center justify-end gap-3 px-6">
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button variant="secondary" onClick={handleToggle} loading={toggling}>
            {paused ? (
              <Play className="size-4" aria-hidden="true" />
            ) : (
              <Pause className="size-4" aria-hidden="true" />
            )}
            {paused ? "Resume generation" : "Pause generation"}
          </Button>
          <ThemeToggle />
        </header>
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

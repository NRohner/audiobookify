import { NavLink, Outlet } from "react-router-dom";
import { BookOpen, Home, Mic, SpellCheck2 } from "lucide-react";
import ThemeToggle from "./ThemeToggle.jsx";

const navItems = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/voices", label: "Voices", icon: Mic },
  { to: "/pronunciations", label: "Pronunciations", icon: SpellCheck2 },
];

export default function AppShell() {
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
        <header className="h-16 border-b border-border flex items-center justify-end px-6">
          <ThemeToggle />
        </header>
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

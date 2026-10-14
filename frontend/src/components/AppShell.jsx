import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { FileText, Headphones, LibraryBig, Mic, Pause, Play, Quote } from "lucide-react";
import { api } from "../lib/api.js";
import { bookState } from "../lib/shelf.js";
import ProgressBar from "./ui/ProgressBar.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import MiniPlayer from "./player/MiniPlayer.jsx";
import { usePlayer } from "./player/PlayerContext.jsx";

const navItems = [
  { to: "/", label: "Library", icon: LibraryBig, end: true },
  { to: "/player", label: "Now playing", icon: Headphones },
  { to: "/voices", label: "Voices", icon: Mic },
  { to: "/pronunciations", label: "Tricky words", icon: FileText },
  { to: "/text", label: "Try a line", icon: Quote },
];

// Footer card shown only while a book is in the studio. Also holds the global pause.
function StudioStatus({ book, paused, onTogglePause, toggling }) {
  const state = bookState(book);
  const chapter = state.activeIndex + 1;
  return (
    <div className="border-t border-line pt-4 mt-4">
      <div className="flex items-center justify-between">
        <p className="rr-eyebrow">{paused ? "Studio paused" : "In the studio"}</p>
        <button
          type="button"
          onClick={onTogglePause}
          disabled={toggling}
          aria-label={paused ? "Resume the studio" : "Pause the studio"}
          title={paused ? "Resume" : "Pause"}
          className="inline-flex size-7 items-center justify-center rounded-full text-ink-muted hover:bg-card hover:text-ink"
        >
          {paused ? (
            <Play className="size-3.5 fill-current" aria-hidden="true" />
          ) : (
            <Pause className="size-3.5 fill-current" aria-hidden="true" />
          )}
        </button>
      </div>
      <Link to={`/books/${book.id}`} className="block rounded-input -mx-1 px-1 py-1 hover:bg-card/50">
        <p className="font-serif text-[16px] leading-tight text-ink line-clamp-2">{book.title}</p>
        <ProgressBar value={state.progress} label={`${book.title} progress`} className="my-2" />
        <p className="text-[12px] text-ink-muted tabular-nums">
          Chapter {chapter} of {state.total} · {Math.round(state.progress * 100)}%
        </p>
      </Link>
    </div>
  );
}

export default function AppShell() {
  const [paused, setPaused] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [studioBook, setStudioBook] = useState(null);
  const { chapter } = usePlayer();
  const onPlayer = useLocation().pathname === "/player";

  async function refresh() {
    const [status, books] = await Promise.all([api.getWorkerStatus(), api.listBooks()]);
    setPaused(status.paused);
    setStudioBook(books.find((b) => bookState(b).rendering) ?? null);
  }

  useEffect(() => {
    refresh().catch(() => {});
    const id = setInterval(() => refresh().catch(() => {}), 2000);
    return () => clearInterval(id);
  }, []);

  async function handleToggle() {
    setToggling(true);
    try {
      await (paused ? api.resumeWorker() : api.pauseWorker());
      await refresh();
    } catch {
      // The next poll shows the real state.
    } finally {
      setToggling(false);
    }
  }

  return (
    <div className="h-dvh flex bg-paper text-ink">
      <aside className="w-(--rr-sidebar-w) shrink-0 bg-oat flex flex-col px-3.5 pt-6 pb-4.5">
        <p className="font-serif text-[25px] font-medium tracking-[-0.01em] px-2.5">Audiobookify</p>
        <nav className="mt-6.5 flex flex-col gap-0.5">
          {navItems.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex h-10 items-center gap-3 rounded-input px-3 text-ui transition-colors duration-150 ${
                  isActive ? "bg-accent-soft text-accent-text font-semibold" : "text-ink hover:bg-card/60"
                }`
              }
            >
              <Icon className="size-[18px] shrink-0" strokeWidth={1.7} aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto">
          {studioBook && (
            <StudioStatus book={studioBook} paused={paused} onTogglePause={handleToggle} toggling={toggling} />
          )}
          {!studioBook && paused && (
            <div className="border-t border-line pt-4 mt-4 flex items-center justify-between">
              <p className="rr-eyebrow">Studio paused</p>
              <button
                type="button"
                onClick={handleToggle}
                disabled={toggling}
                className="text-meta font-bold text-accent-text hover:underline"
              >
                Resume
              </button>
            </div>
          )}
          <div className="mt-3">
            <ThemeToggle />
          </div>
        </div>
      </aside>
      <div className="flex-1 min-w-0 flex flex-col">
        <main className="flex-1 min-h-0 overflow-y-auto pt-6.5 pl-10 pr-11 pb-10">
          <Outlet />
        </main>
        {/* Nothing is loaded on an empty library, so the mini-player stays hidden there too. */}
        {chapter && !onPlayer && (
          <div className="pl-10 pr-11 pb-4">
            <MiniPlayer />
          </div>
        )}
      </div>
    </div>
  );
}

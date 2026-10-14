import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check, Play, RotateCcw, RotateCw, SkipBack, SkipForward } from "lucide-react";
import { api } from "../lib/api.js";
import { formatClock, formatMinutes, formatRuntime } from "../lib/player.js";
import BookCover from "../components/BookCover.jsx";
import PlayGlyph, { SpeedPill } from "../components/player/PlayGlyph.jsx";
import Scrubber from "../components/player/Scrubber.jsx";
import { SPEEDS, usePlayer } from "../components/player/PlayerContext.jsx";

function Transport({ onClick, label, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="relative inline-flex size-11 items-center justify-center rounded-full text-ink hover:bg-oat"
    >
      {children}
    </button>
  );
}

function Skip30({ back }) {
  const Icon = back ? RotateCcw : RotateCw;
  return (
    <>
      <Icon className="size-[22px]" strokeWidth={1.7} aria-hidden="true" />
      <span className="absolute text-[8px] font-bold tabular-nums" aria-hidden="true">
        30
      </span>
    </>
  );
}

function ChapterList({ book, current, onPick }) {
  const done = book.chapters.filter((c) => c.status === "done");
  const runtime = done.reduce((sum, c) => sum + (c.duration ?? 0), 0);
  return (
    <aside className="w-[380px] shrink-0 border-l border-line bg-card px-3.5 pt-6.5 pb-6 lg:sticky lg:top-0 lg:h-dvh lg:overflow-y-auto">
      <div className="flex items-baseline justify-between px-3.5 pb-3">
        <h2 className="text-section">Chapters</h2>
        <p className="text-meta text-ink-muted tabular-nums">
          {book.chapters.length}
          {runtime > 0 && ` · ${formatRuntime(runtime)}`}
        </p>
      </div>
      <ol>
        {book.chapters.map((c) => {
          const isCurrent = c.id === current?.id;
          const playable = c.status === "done" && c.duration;
          return (
            <li key={c.id}>
              <button
                type="button"
                disabled={!playable}
                onClick={() => onPick(c.id)}
                aria-current={isCurrent ? "true" : undefined}
                className={`grid h-[42px] w-full grid-cols-[32px_1fr_auto] items-center rounded-[12px] px-3.5 text-left ${
                  isCurrent ? "bg-accent-soft text-accent-text" : playable ? "hover:bg-oat" : "text-ink-muted"
                } disabled:cursor-default`}
              >
                <span aria-hidden="true">
                  {isCurrent ? (
                    <Play className="size-3 fill-current" />
                  ) : playable ? (
                    <Check className="size-3.5 text-ink-muted" strokeWidth={1.7} />
                  ) : null}
                </span>
                <span className={`truncate font-serif text-[17px] ${isCurrent ? "font-medium" : ""}`}>{c.title}</span>
                <span className="text-[12px] text-ink-muted tabular-nums">
                  {playable ? formatMinutes(c.duration) : c.status === "failed" ? "Didn't finish" : "Not rendered"}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

export default function PlayerPage() {
  const p = usePlayer();
  const { book, chapter, timeline } = p;
  const [narrator, setNarrator] = useState(null);

  useEffect(() => {
    if (!book) return;
    api.getBook(book.id).then(p.refreshBook).catch(() => {});
    api
      .listVoices()
      .then((voices) => setNarrator(voices.find((v) => v.id === book.voice_profile_id)?.name ?? null))
      .catch(() => {});
  }, [book?.id]);

  if (!book || !chapter) {
    return (
      <div className="mx-auto mt-24 max-w-md text-center">
        <h1 className="text-title">Nothing playing yet.</h1>
        <p className="mt-2 text-ui text-ink-muted">
          Open a book from your{" "}
          <Link to="/" className="font-semibold text-accent-text hover:underline">
            Library
          </Link>{" "}
          and press play on any finished chapter.
        </p>
      </div>
    );
  }

  const ticks = timeline.chapters.slice(1).map((c) => c.start);
  const meta = [book.title, book.author, narrator && `narrated by ${narrator}`].filter(Boolean).join(" · ");

  return (
    <div className="-mt-6.5 -mr-11 -mb-10 flex min-h-dvh">
      <div className="flex min-w-0 flex-1 flex-col items-center px-6 pt-10 pb-10">
        <div className="relative">
          <BookCover book={book} className="rr-cover-hero w-[300px]! h-[436px]!" />
          <span aria-hidden="true" className="rr-ribbon absolute -top-1 right-10 h-24 w-4 bg-accent-deco" />
        </div>

        <h1 className="mt-8 text-center text-[30px] leading-tight">{chapter.title}</h1>
        <p className="mt-1 text-center text-[14px] text-ink-muted">{meta}</p>

        <div className="mt-6 w-full max-w-[620px]">
          <Scrubber
            value={p.bookTime}
            max={timeline.total}
            ticks={ticks}
            onSeek={p.seekBook}
            label="Position in book"
            valueText={`Chapter ${chapter.index + 1}, ${formatClock(p.time)} of ${formatClock(chapter.duration)}`}
          />
          <div className="mt-1.5 flex justify-between text-meta text-ink-muted tabular-nums">
            <span>{formatClock(p.bookTime)}</span>
            <span>
              Chapter {chapter.index + 1} · {formatClock(p.time)} of {formatClock(chapter.duration)}
            </span>
            <span>-{formatClock(timeline.total - p.bookTime)}</span>
          </div>
        </div>

        <div className="mt-6 flex items-center gap-5">
          <Transport onClick={() => p.step(-1)} label="Previous chapter">
            <SkipBack className="size-5 fill-current" strokeWidth={1.7} aria-hidden="true" />
          </Transport>
          <Transport onClick={() => p.skip(-30)} label="Back 30 seconds">
            <Skip30 back />
          </Transport>
          <button
            type="button"
            onClick={p.toggle}
            aria-label={p.playing ? "Pause" : "Play"}
            className="inline-flex size-[76px] items-center justify-center rounded-full bg-accent text-on-accent shadow-[0_12px_22px_-10px_var(--rr-accent)] hover:brightness-95"
          >
            <PlayGlyph playing={p.playing} className="size-6" />
          </button>
          <Transport onClick={() => p.skip(30)} label="Forward 30 seconds">
            <Skip30 />
          </Transport>
          <Transport onClick={() => p.step(1)} label="Next chapter">
            <SkipForward className="size-5 fill-current" strokeWidth={1.7} aria-hidden="true" />
          </Transport>
        </div>

        <SpeedPill rate={p.rate} rates={SPEEDS} onChange={p.setRate} className="mt-6 h-[34px]" />
      </div>

      <ChapterList book={book} current={chapter} onPick={(id) => p.play(book, id)} />
    </div>
  );
}

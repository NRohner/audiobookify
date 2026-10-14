import { Link } from "react-router-dom";
import BookCover from "../BookCover.jsx";
import { formatClock } from "../../lib/player.js";
import PlayGlyph, { SpeedPill } from "./PlayGlyph.jsx";
import Scrubber from "./Scrubber.jsx";
import { SPEEDS, usePlayer } from "./PlayerContext.jsx";

// Docked at the bottom of the content area; the title opens the Player.
export default function MiniPlayer() {
  const p = usePlayer();
  const { book, chapter } = p;
  const length = chapter.duration;
  return (
    <section
      aria-label="Now playing"
      className="flex h-[60px] items-center gap-4 rounded-[16px] border border-line bg-card px-3"
    >
      <Link to="/player" className="flex min-w-0 w-64 shrink-0 items-center gap-3 rounded-input">
        <BookCover book={book} mini className="w-[30px]! h-[42px]! text-[14px]!" />
        <span className="min-w-0">
          <span className="block truncate font-serif text-[16px] leading-tight">{book.title}</span>
          <span className="block truncate text-[12px] text-ink-muted">
            Chapter {chapter.index + 1} · {chapter.title}
          </span>
        </span>
      </Link>
      <button
        type="button"
        onClick={p.toggle}
        aria-label={p.playing ? "Pause" : "Play"}
        className="inline-flex size-9 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent hover:brightness-95"
      >
        <PlayGlyph playing={p.playing} className="size-3.5" />
      </button>
      <span className="w-14 shrink-0 text-right text-[12px] text-ink-muted tabular-nums">{formatClock(p.time)}</span>
      <Scrubber
        slim
        value={p.time}
        max={length}
        onSeek={(t) => p.seekBook(chapter.start + t)}
        label="Chapter position"
        valueText={`${formatClock(p.time)} of ${formatClock(length)}`}
        className="flex-1"
      />
      <span className="w-14 shrink-0 text-[12px] text-ink-muted tabular-nums">{formatClock(length)}</span>
      <SpeedPill rate={p.rate} rates={SPEEDS} onChange={p.setRate} />
    </section>
  );
}

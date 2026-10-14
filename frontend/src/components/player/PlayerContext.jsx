import { createContext, useContext, useEffect, useRef, useState } from "react";
import { bookTimeline, locate } from "../../lib/player.js";

export const SPEEDS = [0.8, 1, 1.1, 1.25, 1.5, 2];

const PlayerCtx = createContext(null);

export const usePlayer = () => useContext(PlayerCtx);

const audioUrl = (bookId, chapterId) => `/api/books/${bookId}/chapters/${chapterId}/audio`;

// Space plays/pauses unless the key belongs to a control that has focus.
function ownsSpace(el) {
  return el?.closest?.("input, textarea, select, button, a, summary, [contenteditable='true']");
}

// One <audio> element for the whole app; the mini-player and Player both drive it.
export function PlayerProvider({ children }) {
  const audio = useRef(null);
  const startAt = useRef(0);
  const [book, setBook] = useState(null);
  const [chapterId, setChapterId] = useState(null);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRateState] = useState(1);

  const timeline = bookTimeline(book);
  const chapter = timeline.chapters.find((c) => c.id === chapterId) ?? null;
  const bookTime = chapter ? chapter.start + time : 0;

  function play(nextBook, nextChapterId, offset = 0, autoplay = true) {
    const a = audio.current;
    setBook(nextBook);
    if (nextChapterId === chapterId && book?.id === nextBook.id) {
      a.currentTime = offset;
    } else {
      setChapterId(nextChapterId);
      setTime(offset);
      startAt.current = offset;
      a.src = audioUrl(nextBook.id, nextChapterId);
    }
    if (autoplay) a.play().catch(() => {});
  }

  function toggle() {
    const a = audio.current;
    if (!chapter) return;
    if (a.paused) a.play().catch(() => {});
    else a.pause();
  }

  function seekBook(t) {
    if (!chapter) return;
    const { chapter: target, offset } = locate(timeline, t);
    play(book, target.id, offset, playing);
  }

  function step(dir) {
    const i = timeline.chapters.findIndex((c) => c.id === chapterId);
    if (dir < 0 && time > 3) return play(book, chapterId, 0, playing); // first press restarts the chapter
    const target = timeline.chapters[i + dir];
    if (target) play(book, target.id, 0, playing);
  }

  function setRate(next) {
    setRateState(next);
    audio.current.playbackRate = next;
    audio.current.defaultPlaybackRate = next;
  }

  // Keep the stored book fresh (more chapters finish while you listen).
  function refreshBook(fresh) {
    setBook((current) => (current && fresh?.id === current.id ? fresh : current)); // callers may hold a stale closure
  }

  function onEnded() {
    const i = timeline.chapters.findIndex((c) => c.id === chapterId);
    const next = timeline.chapters[i + 1];
    if (next) play(book, next.id, 0, true);
  }

  useEffect(() => {
    function onKey(e) {
      if (e.code !== "Space" || e.repeat || ownsSpace(e.target) || !chapterId) return;
      e.preventDefault();
      toggle();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const value = {
    book, chapter, timeline, time, bookTime, playing, rate,
    play, toggle, seekBook, step, setRate, refreshBook,
    skip: (seconds) => seekBook(bookTime + seconds),
  };

  return (
    <PlayerCtx.Provider value={value}>
      {children}
      <audio
        ref={audio}
        preload="metadata"
        onLoadedMetadata={(e) => {
          e.currentTarget.currentTime = startAt.current;
          e.currentTarget.playbackRate = rate;
        }}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={onEnded}
      />
    </PlayerCtx.Provider>
  );
}

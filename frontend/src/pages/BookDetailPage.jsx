import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ChevronLeft, ChevronRight, Laptop, Play, Square, Trash2 } from "lucide-react";
import { api, chapterInProgress } from "../lib/api.js";
import { formatEta } from "../lib/eta.js";
import { bookState, primaryAction } from "../lib/shelf.js";
import Button, { ErrorNote, IconButton } from "../components/ui/Button.jsx";
import ConfirmDialog from "../components/ui/ConfirmDialog.jsx";
import ProgressBar from "../components/ui/ProgressBar.jsx";
import EnginePicker from "../components/EnginePicker.jsx";
import BookCover from "../components/BookCover.jsx";
import PlayGlyph from "../components/player/PlayGlyph.jsx";
import { usePlayer } from "../components/player/PlayerContext.jsx";
import { formatClock } from "../lib/player.js";
import { effectiveEngine, engineLabel, engineMismatchWarning, initialBookEngine } from "../lib/engines.js";

const WAVE_BARS = 72;
// Fixed, natural-looking bar heights (percent) so the waveform doesn't jump between polls.
const WAVE = Array.from({ length: WAVE_BARS }, (_, i) =>
  Math.round(30 + 70 * Math.abs(Math.sin(i * 1.7) * Math.cos(i * 0.31))),
);

function Waveform({ progress }) {
  const read = Math.round(progress * WAVE_BARS);
  return (
    <div className="flex h-9 items-center gap-[3px]" aria-hidden="true">
      {WAVE.map((h, i) => (
        <span
          key={i}
          className={`w-[3px] rounded-[2px] ${i < read ? "bg-accent" : "bg-line"}`}
          style={{ height: `${h}%`, animation: `rr-drift 3.2s ease-in-out ${(i % 9) * 0.35}s infinite` }}
        />
      ))}
    </div>
  );
}

function NowReadingCard({ chapter, number }) {
  const eta = formatEta(chapter.progress, chapter.started_at);
  return (
    <section aria-label="Reading aloud now" className="rounded-hero border border-line bg-card px-[22px] py-[18px] shadow-panel mb-8">
      <div className="flex items-center justify-between gap-4">
        <p className="rr-eyebrow text-accent-text! flex items-center gap-2">
          <span className="relative size-2 rounded-full bg-accent shadow-[0_0_0_3px_var(--rr-accent-soft)] animate-[rr-pulse_2.4s_ease-in-out_infinite]" />
          Reading aloud now
        </p>
        <p className="text-meta text-ink-muted tabular-nums">
          {Math.round((chapter.progress ?? 0) * 100)}%{eta && ` · about ${eta} left`}
        </p>
      </div>
      <h2 className="text-section mt-1.5 mb-3">
        {number} · {chapter.title}
      </h2>
      <Waveform progress={chapter.progress ?? 0} />
    </section>
  );
}

function ChapterError({ number, chapter, onRetry, retrying }) {
  const [showLog, setShowLog] = useState(false);
  return (
    <li className="my-2.5 rounded-[16px] border border-line bg-card px-4 py-3.5 shadow-panel">
      <div className="grid grid-cols-[34px_1fr_auto] items-center">
        <span className="text-meta text-ink-muted tabular-nums">{number}</span>
        <span className="font-serif text-[17px]">{chapter.title}</span>
        <span className="rounded-full bg-accent-soft px-2.5 py-0.5 text-[12px] font-bold text-accent-text">
          Didn't finish
        </span>
      </div>
      <p className="mt-2 pl-[34px] text-[14px] leading-normal">
        The narrator stopped before the end of this chapter. Trying again usually fixes it. If it keeps happening,
        closing a few apps to free up memory can help.
      </p>
      <div className="mt-3 pl-[34px] flex items-center gap-3">
        <Button variant="dark" onClick={onRetry} loading={retrying}>
          Retry chapter
        </Button>
        {chapter.error && (
          <Button variant="quiet" aria-expanded={showLog} onClick={() => setShowLog(!showLog)}>
            Advanced
            <ChevronRight className={`size-3.5 transition-transform ${showLog ? "rotate-90" : ""}`} aria-hidden="true" />
          </Button>
        )}
      </div>
      {showLog && (
        <pre className="mt-3 ml-[34px] max-h-48 overflow-auto rounded-input bg-oat p-3 font-mono text-[12px] whitespace-pre-wrap text-ink-muted">
          {chapter.error}
        </pre>
      )}
    </li>
  );
}

function ChapterRow({ number, chapter, busy, onRender, onCancel, playing, onPlay }) {
  const inProgress = chapterInProgress(chapter);
  const queued = chapter.status === "pending" && inProgress;
  const muted = queued || (chapter.status === "pending" && !inProgress);
  const playable = chapter.status === "done" && chapter.duration;
  let status;
  if (chapter.status === "generating") {
    status = (
      <span className="text-meta font-bold text-accent-text tabular-nums">
        Rendering · {Math.round((chapter.progress ?? 0) * 100)}%
      </span>
    );
  } else if (queued) {
    status = <span className="text-meta text-ink-muted">Queued</span>;
  }

  let action = null;
  if (playable) {
    action = (
      <IconButton onClick={onPlay} aria-label={`${playing ? "Pause" : "Play"} ${chapter.title}`}>
        <PlayGlyph playing={playing} className="size-2.5" />
      </IconButton>
    );
  } else if (inProgress) {
    action = (
      <IconButton onClick={onCancel} disabled={busy} aria-label={`Stop rendering ${chapter.title}`} title="Stop">
        <Square className="size-2.5 fill-current" aria-hidden="true" />
      </IconButton>
    );
  } else if (chapter.status === "pending") {
    status = (
      <Button variant="quiet" onClick={onRender} loading={busy} aria-label={`Render ${chapter.title}`}>
        Render
      </Button>
    );
  }

  return (
    <li className="grid grid-cols-[34px_1fr_auto_64px_32px] items-center gap-x-3 min-h-(--rr-row-h) border-b border-line py-1">
      <span className="text-meta text-ink-muted tabular-nums">{number}</span>
      <span className={`font-serif text-[17px] truncate ${muted ? "text-ink-muted" : ""}`}>
        {chapter.title}
        {chapter.used_fallback && (
          <span
            className="ml-2 font-sans text-[12px] text-accent-text"
            title="The output folder wasn't writable when this chapter finished, so it went to the backup folder."
          >
            saved to backup folder
          </span>
        )}
      </span>
      <span>{status}</span>
      <span className="text-right text-meta text-ink-muted tabular-nums">{playable && formatClock(chapter.duration)}</span>
      <span className="flex justify-end">{action}</span>
    </li>
  );
}

export default function BookDetailPage() {
  const { bookId } = useParams();
  const [book, setBook] = useState(null);
  const [voices, setVoices] = useState([]);
  const [engines, setEngines] = useState([]);
  const [chosenEngine, setChosenEngine] = useState(null);
  const [paused, setPaused] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyAll, setBusyAll] = useState(false);
  const [busyChapters, setBusyChapters] = useState(new Set());
  const [error, setError] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const navigate = useNavigate();
  const player = usePlayer();

  async function refresh() {
    const [next, worker] = await Promise.all([api.getBook(bookId), api.getWorkerStatus()]);
    setBook(next);
    player.refreshBook(next);
    setPaused(worker.paused);
    setChosenEngine((current) => current ?? initialBookEngine(next));
  }

  useEffect(() => {
    Promise.all([refresh(), api.listVoices().then(setVoices), api.listEngines().then((r) => setEngines(r.engines))])
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [bookId]);

  const state = book ? bookState(book) : null;
  const engine = book ? effectiveEngine(chosenEngine, engines, book.voice_profile_id != null) : null;

  useEffect(() => {
    if (!state?.rendering) return;
    const id = setInterval(() => refresh().catch((e) => setError(e.message)), 2000);
    return () => clearInterval(id);
  }, [state?.rendering]);

  async function run(fn) {
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleDelete() {
    setConfirmDelete(false);
    setError(null);
    try {
      await api.deleteBook(bookId);
      if (player.book?.id === Number(bookId)) player.clear();
      navigate("/");
    } catch (e) {
      setError(e.message);
    }
  }

  async function handlePrimary(action) {
    setBusyAll(true);
    await run(() => (action.kind === "stop" ? api.cancelBook(bookId) : api.generateBook(bookId, engine)));
    setBusyAll(false);
  }

  async function forChapter(chapterId, fn) {
    setBusyChapters((prev) => new Set(prev).add(chapterId));
    await run(fn);
    setBusyChapters((prev) => {
      const next = new Set(prev);
      next.delete(chapterId);
      return next;
    });
  }

  if (loading) return <p className="text-ink-muted">Taking the book down from the shelf…</p>;
  if (!book) return <ErrorNote>{error || "We couldn't find that book."}</ErrorNote>;

  const action = primaryAction(state);
  const mismatch = engineMismatchWarning(book, engine, engines);
  const narrator = voices.find((v) => v.id === book.voice_profile_id)?.name ?? "Default voice";
  const activeIndex = book.chapters.findIndex((c) => c.status === "generating");
  const folder = book.output_dir?.split("/").filter(Boolean).pop();

  let contextNote = null;
  if (paused && state.rendering) contextNote = "The studio is paused. Resume it from the sidebar to keep going.";
  else if (mismatch) contextNote = mismatch;
  else if (state.done === 0 && !state.rendering && state.total > 0)
    contextNote = `${state.total} chapters, ready when you are. Each one becomes its own MP3.`;

  return (
    <div className="max-w-[1180px]">
      <Link
        to="/"
        className="inline-flex items-center gap-1 text-meta font-semibold text-accent-text hover:underline rounded"
      >
        <ChevronLeft className="size-4" strokeWidth={1.7} aria-hidden="true" />
        Library
      </Link>

      <div className="mt-4 grid gap-x-16 gap-y-10 lg:grid-cols-[minmax(320px,2fr)_3fr]">
        {/* Left: the book as an object */}
        <div className="flex flex-col items-center gap-10">
          <BookCover book={book} className="-rotate-2 mt-4" />

          {state.rendering && (
            <div className="w-full max-w-[380px]">
              <div className="flex items-baseline justify-between">
                <p className="font-serif text-[22px]">
                  Chapter {state.activeIndex + 1} of {state.total}
                </p>
                <p className="text-meta text-ink-muted tabular-nums">{Math.round(state.progress * 100)}%</p>
              </div>
              <ProgressBar value={state.progress} label="Book progress" className="mt-2 h-1.5" />
              <p className="mt-2.5 text-[14px] text-ink-muted">
                {state.done} of {state.total} chapters ready.
              </p>
              <div className="mt-5 flex gap-2.5 rounded-tile bg-oat px-4 py-3 text-meta leading-normal text-ink-muted">
                <Laptop className="size-4 shrink-0 mt-0.5" strokeWidth={1.7} aria-hidden="true" />
                <p>Running privately on this Mac. If it goes to sleep, we'll pause and pick up where we left off.</p>
              </div>
            </div>
          )}

          <dl className="rr-library-card w-full max-w-[380px]">
            <div className="flex items-baseline justify-between pb-2">
              <dt className="rr-eyebrow">Library card</dt>
              <dd className="font-serif text-meta text-ink-muted">No. {String(book.id).padStart(4, "0")}</dd>
            </div>
            <div className="grid grid-cols-[96px_1fr] items-center min-h-8 border-t border-line text-[14px]">
              <dt className="text-ink-muted">
                <label htmlFor="narrator">Narrator</label>
              </dt>
              <dd>
                <select
                  id="narrator"
                  value={book.voice_profile_id ?? ""}
                  disabled={state.rendering}
                  onChange={(e) =>
                    run(() => api.assignVoice(bookId, e.target.value ? Number(e.target.value) : null))
                  }
                  className="-ml-1 w-full bg-transparent py-1 text-ink rounded disabled:opacity-60"
                >
                  <option value="">Default voice</option>
                  {voices.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name}
                    </option>
                  ))}
                </select>
              </dd>
            </div>
            <div className="grid grid-cols-[96px_1fr] items-center min-h-8 border-t border-line text-[14px]">
              <dt className="text-ink-muted">Chapters</dt>
              <dd className="tabular-nums">
                {state.total} · {state.done} ready
              </dd>
            </div>
            <div className="grid grid-cols-[96px_1fr] items-center min-h-8 border-t border-line text-[14px]">
              <dt className="text-ink-muted">Saved to</dt>
              <dd className="truncate" title={book.output_dir}>
                {folder || "—"}
              </dd>
            </div>
          </dl>
        </div>

        {/* Right: title, actions, chapters */}
        <div className="min-w-0">
          <h1 className="text-title leading-tight">{book.title}</h1>
          <p className="mt-1 text-[14px] text-ink-muted">
            {book.author || "Unknown author"} · narrated by {narrator}
          </p>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            {action && (
              <Button onClick={() => handlePrimary(action)} loading={busyAll}>
                {action.kind === "stop" ? (
                  <Square className="size-3 fill-current" aria-hidden="true" />
                ) : (
                  <Play className="size-3.5 fill-current" aria-hidden="true" />
                )}
                {action.label}
              </Button>
            )}
            <Link
              to={`/books/${bookId}/chapters`}
              className="inline-flex h-10 items-center rounded-full border border-line bg-card px-5 text-[14px] font-semibold hover:bg-oat"
            >
              Chapter breaks
            </Link>
            <Link
              to="/pronunciations"
              className="inline-flex h-10 items-center rounded-full border border-line bg-card px-5 text-[14px] font-semibold hover:bg-oat"
            >
              Tricky words
            </Link>
            <Link
              to="/voices"
              className="inline-flex h-10 items-center rounded-full border border-line bg-card px-5 text-[14px] font-semibold hover:bg-oat"
            >
              Voices
            </Link>
          </div>

          {contextNote && <p className="mt-4 text-meta text-ink-muted">{contextNote}</p>}

          <details className="group mt-2">
            <summary className="inline-flex h-8 items-center gap-1 text-meta font-semibold text-ink-muted hover:text-ink list-none [&::-webkit-details-marker]:hidden">
              Advanced
              <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />
            </summary>
            <div className="mt-2 mb-2 flex flex-col gap-3 rounded-tile bg-oat p-4">
              <div>
                <p className="text-meta font-semibold mb-1.5">Engine</p>
                <EnginePicker
                  engines={engines}
                  value={engine}
                  onChange={setChosenEngine}
                  hasVoice={book.voice_profile_id != null}
                  disabled={state.rendering}
                />
                {book.chapters.some((c) => c.engine) && (
                  <p className="mt-1.5 text-meta text-ink-muted">
                    Finished chapters used {engineLabel(book.engine, engines)}.
                  </p>
                )}
              </div>
              <div>
                <p className="text-meta font-semibold mb-1">Output folder</p>
                <p className="font-mono text-[12px] text-ink-muted break-all">{book.output_dir}</p>
              </div>
              <div>
                <Button
                  variant="quiet"
                  className="-ml-2"
                  disabled={state.rendering}
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 className="size-3.5" aria-hidden="true" />
                  Delete book
                </Button>
              </div>
            </div>
          </details>

          <ErrorNote>{error}</ErrorNote>

          <div className="mt-6">
            {activeIndex !== -1 && <NowReadingCard chapter={book.chapters[activeIndex]} number={activeIndex + 1} />}

            <div className="flex items-baseline justify-between border-b border-line pb-2">
              <h2 className="text-[19px]">Chapters</h2>
              <p className="text-meta text-ink-muted tabular-nums">
                {state.done} of {state.total} ready
              </p>
            </div>
            <ol>
              {book.chapters.map((chapter, i) =>
                chapter.status === "failed" ? (
                  <ChapterError
                    key={chapter.id}
                    number={i + 1}
                    chapter={chapter}
                    retrying={busyChapters.has(chapter.id)}
                    onRetry={() => forChapter(chapter.id, () => api.generateChapter(bookId, chapter.id, engine))}
                  />
                ) : (
                  <ChapterRow
                    key={chapter.id}
                    number={i + 1}
                    chapter={chapter}
                    busy={busyChapters.has(chapter.id)}
                    onRender={() => forChapter(chapter.id, () => api.generateChapter(bookId, chapter.id, engine))}
                    onCancel={() => forChapter(chapter.id, () => api.cancelChapter(bookId, chapter.id))}
                    playing={player.chapter?.id === chapter.id && player.playing}
                    onPlay={() =>
                      player.chapter?.id === chapter.id ? player.toggle() : player.play(book, chapter.id)
                    }
                  />
                ),
              )}
            </ol>
          </div>
        </div>
      </div>
      {confirmDelete && (
        <ConfirmDialog
          title="Delete book"
          message={`Delete “${book.title}”? Its chapters, render progress and book pronunciations are removed. MP3s already in ${book.output_dir} stay where they are.`}
          onConfirm={handleDelete}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}

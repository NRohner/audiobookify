import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Move, Plus, Trash2 } from "lucide-react";
import { api } from "../lib/api.js";
import { addBreak, guessTitle, moveBreak, removeBreak, summarize } from "../lib/chapterBreaks.js";
import Button, { ErrorNote, IconButton } from "../components/ui/Button.jsx";

// One paragraph of the book. Clicks are handled once on the reading pane (data-p), so
// thousands of these carry no handlers and skip re-rendering unless their own state changes.
const Paragraph = memo(function Paragraph({ index, text, selected }) {
  if (!text.trim()) return null;
  return (
    <p
      data-p={index}
      className={`-mx-3 my-0 cursor-pointer rounded-input px-3 py-1.5 transition-colors duration-150 ${
        selected ? "bg-accent-soft" : "hover:bg-oat/70"
      }`}
    >
      {text}
    </p>
  );
});

// A chapter: its break marker, then its paragraphs. content-visibility lets the browser skip
// layout and paint for chapters off screen, so a book thousands of pages long scrolls normally.
const ChapterSection = memo(function ChapterSection({
  index, start, end, title, paragraphs, selected, moving, onTitle, onNudge, onMove, onRemove, onAdd,
}) {
  const body = [];
  let chars = 0;
  for (let p = start; p < end; p++) {
    chars += paragraphs[p].length;
    body.push(<Paragraph key={p} index={p} text={paragraphs[p]} selected={selected === p} />);
    if (selected === p && p !== start && !moving) {
      body.push(
        <div key="toolbar" className="my-2 flex">
          <Button variant="dark" autoFocus onClick={() => onAdd(p)}>
            <Plus className="size-4" strokeWidth={1.7} aria-hidden="true" />
            Start a chapter here
          </Button>
        </div>,
      );
    }
  }
  return (
    <section
      id={`chapter-${index}`}
      data-chapter={index}
      className="scroll-mt-4 pb-6 [content-visibility:auto]"
      // Off-screen chapters are sized from their length (~65 characters per 35px line at the
      // 660px measure) so the scrollbar and chapter jumps land close before the real layout.
      style={{ containIntrinsicSize: `auto ${Math.round((chars / 65) * 35 + (end - start) * 24 + 120)}px` }}
      aria-label={`Chapter ${index + 1}: ${title}`}
    >
      <header className={`mb-4 ${index > 0 ? "mt-8 border-t border-line-strong pt-4" : ""}`}>
        <div className="flex items-center gap-2">
          <p className={`rr-eyebrow mr-auto ${moving ? "text-accent-text" : ""}`}>
            {moving ? "Moving this break" : `Chapter ${index + 1}`}
          </p>
          {index > 0 && (
            <>
              <IconButton aria-label="Start this chapter one paragraph earlier" title="Earlier" onClick={() => onNudge(index, -1)}>
                <ArrowUp className="size-4" strokeWidth={1.7} aria-hidden="true" />
              </IconButton>
              <IconButton aria-label="Start this chapter one paragraph later" title="Later" onClick={() => onNudge(index, 1)}>
                <ArrowDown className="size-4" strokeWidth={1.7} aria-hidden="true" />
              </IconButton>
              <Button variant="quiet" onClick={() => onMove(index)} aria-pressed={moving}>
                <Move className="size-4" strokeWidth={1.7} aria-hidden="true" />
                Move
              </Button>
              <Button variant="quiet" onClick={() => onRemove(index)}>
                <Trash2 className="size-4" strokeWidth={1.7} aria-hidden="true" />
                Remove break
              </Button>
            </>
          )}
        </div>
        <label className="sr-only" htmlFor={`chapter-title-${index}`}>
          Chapter {index + 1} title
        </label>
        <input
          id={`chapter-title-${index}`}
          value={title}
          onChange={(e) => onTitle(index, e.target.value)}
          className="mt-1 w-full rounded-input border border-transparent bg-transparent -mx-2 px-2 py-0.5 font-serif text-section
            hover:border-line focus:border-line focus:bg-card"
        />
      </header>
      <div className="font-serif text-reading leading-(--rr-reading-leading) flex flex-col gap-3">{body}</div>
    </section>
  );
});

export default function ChapterEditorPage() {
  const { bookId } = useParams();
  const [book, setBook] = useState(null);
  const [text, setText] = useState(null); // { paragraphs, chapters } as last saved
  const [draft, setDraft] = useState([]);
  const [selected, setSelected] = useState(null);
  const [moving, setMoving] = useState(null);
  const [active, setActive] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const paneRef = useRef(null);
  const railRef = useRef(null);

  const load = useCallback(async () => {
    const [b, t] = await Promise.all([api.getBook(bookId), api.getBookText(bookId)]);
    setBook(b);
    setText(t);
    setDraft(t.chapters);
  }, [bookId]);

  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);

  const total = text?.paragraphs.length ?? 0;
  const summary = useMemo(() => (text ? summarize(text.chapters, draft, total) : null), [text, draft, total]);

  useEffect(() => {
    if (!summary?.dirty) return;
    const warn = (e) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [summary?.dirty]);

  useEffect(() => {
    if (moving == null) return;
    const onKey = (e) => e.key === "Escape" && setMoving(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moving]);

  // The chapter in view is the last one whose top has scrolled past the top of the pane.
  useEffect(() => {
    const pane = paneRef.current;
    if (!pane) return;
    let frame = 0;
    const onScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const top = pane.getBoundingClientRect().top + 80;
        const sections = pane.querySelectorAll("section[data-chapter]");
        let current = 0;
        for (const s of sections) {
          if (s.getBoundingClientRect().top > top) break;
          current = Number(s.dataset.chapter);
        }
        setActive(current);
      });
    };
    pane.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => {
      pane.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
    };
  }, [text]);

  useEffect(() => {
    railRef.current?.querySelector(`[data-rail="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const onTitle = useCallback(
    (i, title) => setDraft((d) => d.map((b, j) => (j === i ? { ...b, title } : b))),
    [],
  );
  const onNudge = useCallback(
    (i, delta) => setDraft((d) => moveBreak(d, i, d[i].start + delta, total)),
    [total],
  );
  const onMove = useCallback((i) => setMoving((m) => (m === i ? null : i)), []);
  const onRemove = useCallback((i) => {
    setDraft((d) => removeBreak(d, i));
    setMoving(null);
  }, []);
  const onAdd = useCallback(
    (p) => {
      setDraft((d) => addBreak(d, p, guessTitle(text.paragraphs[p])));
      setSelected(null);
    },
    [text],
  );

  function handlePaneClick(e) {
    const el = e.target.closest("[data-p]");
    if (!el) return;
    const p = Number(el.dataset.p);
    if (moving != null) {
      setDraft((d) => moveBreak(d, moving, p, total));
      setMoving(null);
      return;
    }
    setSelected((s) => (s === p ? null : p));
  }

  function jumpTo(i) {
    const el = document.getElementById(`chapter-${i}`);
    el?.scrollIntoView({ block: "start" });
    // Chapters near the target render at their real size after the jump; settle once they have.
    requestAnimationFrame(() => requestAnimationFrame(() => el?.scrollIntoView({ block: "start" })));
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await api.saveChapterBreaks(bookId, draft);
      await load();
      setSelected(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  if (!text || !book) {
    return error ? <ErrorNote>{error}</ErrorNote> : <p className="text-meta text-ink-muted">Opening the book…</p>;
  }

  const changes = [
    summary.added && `${summary.added} new ${summary.added === 1 ? "break" : "breaks"}`,
    summary.removed && `${summary.removed} removed`,
    summary.renamed && `${summary.renamed} renamed`,
  ].filter(Boolean);

  return (
    <div className="-mb-10 -mr-11 -mt-6.5 flex h-[calc(100%+4.125rem)]">
      <div ref={paneRef} className="min-w-0 flex-1 overflow-y-auto pt-6.5 pb-24 pr-10">
        <div className="mx-auto max-w-[660px]">
          <Link
            to={`/books/${bookId}`}
            className="inline-flex items-center gap-1 rounded text-meta font-semibold text-accent-text hover:underline"
          >
            <ChevronLeft className="size-4" strokeWidth={1.7} aria-hidden="true" />
            {book.title}
          </Link>
          <h1 className="mt-3 text-title leading-tight">Chapter breaks</h1>
          <p className="mt-1 mb-8 text-[14px] text-ink-muted">
            {moving != null
              ? "Click the paragraph this chapter should start with. Press Esc to stop."
              : "Click any paragraph to start a new chapter there, or move and remove the breaks below."}
          </p>
          {/* Clicks bubble up from paragraphs; keyboard users move breaks with the arrow buttons. */}
          <div onClick={handlePaneClick} className={moving != null ? "[&_[data-p]]:cursor-crosshair" : ""}>
            {draft.map((b, i) => {
              const end = draft[i + 1]?.start ?? total;
              return (
                <ChapterSection
                  key={i}
                  index={i}
                  start={b.start}
                  end={end}
                  title={b.title}
                  paragraphs={text.paragraphs}
                  selected={selected != null && selected >= b.start && selected < end ? selected : null}
                  moving={moving === i}
                  onTitle={onTitle}
                  onNudge={onNudge}
                  onMove={onMove}
                  onRemove={onRemove}
                  onAdd={onAdd}
                />
              );
            })}
          </div>
        </div>
      </div>

      <aside className="flex w-(--rr-rail-w) shrink-0 flex-col border-l border-line bg-card">
        <div className="px-5 pt-6.5 pb-3">
          <h2 className="text-section">Chapters</h2>
          <p className="text-meta text-ink-muted tabular-nums">
            {draft.length} {draft.length === 1 ? "chapter" : "chapters"}
          </p>
        </div>
        <ol ref={railRef} className="min-h-0 flex-1 overflow-y-auto px-2.5">
          {draft.map((b, i) => (
            <li key={i}>
              <button
                type="button"
                data-rail={i}
                aria-current={i === active ? "location" : undefined}
                onClick={() => jumpTo(i)}
                className={`grid w-full grid-cols-[34px_1fr] items-baseline rounded-input px-2.5 py-2 text-left ${
                  i === active ? "bg-accent-soft text-accent-text" : "hover:bg-oat"
                }`}
              >
                <span className="text-meta tabular-nums text-ink-muted">{i + 1}</span>
                <span className="truncate font-serif text-[16px]">{b.title || "Untitled chapter"}</span>
              </button>
            </li>
          ))}
        </ol>
        <div className="border-t border-line px-5 pt-4 pb-5">
          <ErrorNote>{error}</ErrorNote>
          {summary.dirty ? (
            <>
              <p className="text-[14px]">{changes.length ? changes.join(" · ") : "Breaks moved"}</p>
              {summary.loseAudio > 0 && (
                <p className="mt-2 rounded-tile bg-accent-soft px-3 py-2 text-meta text-accent-text">
                  {summary.loseAudio} {summary.loseAudio === 1 ? "chapter has" : "chapters have"} audio that will
                  need rendering again.
                </p>
              )}
            </>
          ) : (
            <p className="text-meta text-ink-muted">No changes yet. Everything matches the book as it is.</p>
          )}
          <div className="mt-4 flex items-center gap-2">
            <Button onClick={save} loading={saving} disabled={!summary.dirty}>
              Save changes
            </Button>
            <Button variant="quiet" disabled={!summary.dirty || saving} onClick={() => setDraft(text.chapters)}>
              Discard
            </Button>
          </div>
          <details className="group mt-3">
            <summary className="inline-flex h-8 list-none items-center gap-1 text-meta font-semibold text-ink-muted hover:text-ink [&::-webkit-details-marker]:hidden">
              Advanced
              <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />
            </summary>
            <p className="text-meta text-ink-muted">
              Breaks sit between paragraphs; the words themselves never change. This book has{" "}
              <span className="tabular-nums">{total.toLocaleString()}</span> paragraphs, and chapter {active + 1}{" "}
              starts at paragraph <span className="tabular-nums">{(draft[active]?.start ?? 0) + 1}</span>. Chapters
              whose text is unchanged keep their audio.
            </p>
          </details>
        </div>
      </aside>
    </div>
  );
}

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Search, Upload } from "lucide-react";
import { api } from "../lib/api.js";
import { bookState, groupShelves, spineLook, spineNote } from "../lib/shelf.js";
import Button, { ErrorNote } from "../components/ui/Button.jsx";
import Modal from "../components/ui/Modal.jsx";
import DirectoryBrowser from "../components/DirectoryBrowser.jsx";
import BookCover from "../components/BookCover.jsx";

const SPINE_GAP = 8;
const SHELF_PAD = 22;

function ImportBookModal({ initialFile, onClose, onImported }) {
  const fileRef = useRef(null);
  const [outputDir, setOutputDir] = useState("");
  const [outputDirWritable, setOutputDirWritable] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!initialFile) return;
    const transfer = new DataTransfer();
    transfer.items.add(initialFile);
    fileRef.current.files = transfer.files;
  }, [initialFile]);

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", fileRef.current.files[0]);
      form.append("output_dir", outputDir.trim());
      onImported(await api.createBook(form));
    } catch (e) {
      setError(e.message);
      setSubmitting(false);
    }
  }

  return (
    <Modal title="Add a book" onClose={onClose} wide>
      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        <div>
          <label htmlFor="file" className="block text-meta font-semibold mb-1.5">
            EPUB or Markdown file
          </label>
          <input
            ref={fileRef}
            id="file"
            type="file"
            name="file"
            accept=".epub,.md"
            required
            className="block w-full text-meta text-ink-muted file:mr-3 file:h-9 file:rounded-full file:border file:border-line
              file:bg-card file:px-4 file:text-meta file:font-semibold file:text-ink file:cursor-pointer"
          />
        </div>
        <div>
          <p className="text-meta font-semibold mb-1.5">Where should the finished MP3s go?</p>
          <DirectoryBrowser value={outputDir} onChange={setOutputDir} onValidityChange={setOutputDirWritable} />
        </div>
        <ErrorNote>{error}</ErrorNote>
        <div className="flex justify-end gap-3">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting} disabled={!outputDirWritable}>
            Add to shelf
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function statusLine(state) {
  if (state.rendering) return { text: `Rendering · ${Math.round(state.progress * 100)}%`, tone: "text-accent-text" };
  if (state.failed > 0) return { text: spineNote(state), tone: "text-accent-text" };
  if (state.shelf === "finished") return { text: `Finished · ${spineNote(state)}`, tone: "text-sage" };
  if (state.done > 0) return { text: `${state.done} of ${state.total} chapters done`, tone: "text-ink" };
  return { text: "Not started", tone: "text-ink-muted" };
}

function Spine({ book, index, narrator }) {
  const look = spineLook(book);
  const state = bookState(book);
  const note = spineNote(state);
  const status = statusLine(state);
  const attention = state.failed > 0;
  return (
    <div className="rr-spine-wrap">
      <Link
        to={`/books/${book.id}`}
        aria-label={`${book.title}, ${status.text}`}
        className="rr-spine"
        data-lean={look.lean}
        data-attention={attention}
        style={{
          width: look.width,
          height: look.height,
          background: `var(--rr-spine-${look.color})`,
          color: `var(--rr-spine-text-${look.tone})`,
        }}
      >
        <span className="rr-rule" />
        <span className="rr-spine__title" style={{ fontSize: look.width < 42 ? 13 : 15 }}>
          {book.title}
        </span>
        <span className="rr-diamond" />
        {state.rendering && (
          <>
            <span className="rr-spine__fill" style={{ height: `${Math.max(4, state.progress * 100)}%` }} />
            <span className="rr-spine__dot" />
          </>
        )}
      </Link>
      {note && (
        <span
          className="rr-tag"
          data-tone={attention ? "attention" : undefined}
          style={{ "--string": `${6 + (index % 3) * 22}px`, "--tilt": index % 2 ? "-2.5deg" : "2deg" }}
          aria-hidden="true"
        >
          <span className="rr-tag__label">{note}</span>
        </span>
      )}
      <div className="rr-spine-tip flex gap-3 rounded-tile border border-line bg-card p-3 shadow-popover" aria-hidden="true">
        <BookCover book={book} mini />
        <div className="min-w-0">
          <p className="font-serif text-[17px] leading-tight line-clamp-2">{book.title}</p>
          {book.author && <p className="text-[12px] text-ink-muted truncate">{book.author}</p>}
          <p className={`mt-1.5 text-[12px] font-bold ${status.tone}`}>{status.text}</p>
          <p className="text-[12px] text-ink-muted">Narrated by {narrator}</p>
        </div>
      </div>
    </div>
  );
}

function useWidth(ref) {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    setWidth(ref.current.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

// Split books into rows that fit the shelf, so long shelves stack instead of scrolling.
function fitRows(books, width) {
  const available = Math.max(width - SHELF_PAD * 2, 200);
  const rows = [[]];
  let used = 0;
  for (const book of books) {
    const w = spineLook(book).width + SPINE_GAP;
    if (used + w > available && rows.at(-1).length > 0) {
      rows.push([]);
      used = 0;
    }
    rows.at(-1).push(book);
    used += w;
  }
  return rows;
}

function Shelf({ shelf, narratorOf }) {
  const ref = useRef(null);
  const width = useWidth(ref);
  const rows = fitRows(shelf.books, width);
  const states = shelf.books.map(bookState);
  const chapters = states.reduce((n, s) => n + s.total, 0);
  return (
    <section aria-label={shelf.label} className="grid grid-cols-[150px_1fr]">
      <div className="flex flex-col justify-end pb-3 pr-4">
        <h2 className="text-[19px] leading-tight">{shelf.label}</h2>
        <p className="text-meta text-ink-muted tabular-nums">
          {shelf.books.length} {shelf.books.length === 1 ? "book" : "books"} · {chapters} chapters
        </p>
      </div>
      <div ref={ref} className="min-w-0">
        {rows.map((row, r) => (
          <div key={r} className="pb-[72px] last:pb-[56px]">
            <div className="flex h-[170px] items-end px-[22px]" style={{ gap: SPINE_GAP }}>
              {row.map((book, i) => (
                <Spine key={book.id} book={book} index={i} narrator={narratorOf(book)} />
              ))}
            </div>
            <div className="rr-ledge" />
          </div>
        ))}
      </div>
    </section>
  );
}

function EmptyShelf({ onAdd }) {
  return (
    <div className="max-w-3xl mx-auto pt-16 text-center">
      <h2 className="text-title">Your shelf is waiting.</h2>
      <p className="mt-2 text-ui text-ink-muted">Add a book and it will be read aloud for you, right here.</p>
      <div className="mt-14 flex items-end gap-4 px-6">
        <button
          type="button"
          onClick={onAdd}
          className="flex-1 h-[170px] flex flex-col items-center justify-center gap-2 rounded-tile bg-card
            border-[1.5px] border-dashed border-line-strong hover:bg-oat/50 transition-colors"
        >
          <Upload className="size-6 text-accent-text" strokeWidth={1.7} aria-hidden="true" />
          <span className="text-[16px] font-bold">Drop an EPUB or Markdown file here</span>
          <span className="text-meta text-ink-muted">
            or <span className="text-accent-text font-semibold">choose one from your Mac</span>
          </span>
        </button>
        {/* L-shaped bookend */}
        <div aria-hidden="true" className="w-6 h-28 border-l-[6px] border-b-[6px] border-line-strong rounded-bl-sm" />
      </div>
      <div className="rr-ledge" />
      <p className="mt-10 text-meta text-ink-muted">Books are read and narrated on this Mac. Nothing is uploaded.</p>
    </div>
  );
}

const FILTERS = [
  ["all", "All", () => true],
  ["rendering", "Rendering", (s) => s.rendering],
  ["finished", "Finished", (s) => s.shelf === "finished"],
  ["review", "Needs review", (s) => s.failed > 0],
];

export default function HomePage() {
  const [books, setBooks] = useState([]);
  const [voices, setVoices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [importing, setImporting] = useState(null); // null | { file?: File }
  const [dragging, setDragging] = useState(false);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const searchRef = useRef(null);
  const navigate = useNavigate();

  async function refresh() {
    try {
      setBooks(await api.listBooks());
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    Promise.all([refresh(), api.listVoices().then(setVoices).catch(() => {})]).finally(() => setLoading(false));
  }, []);

  const anyRendering = books.some((b) => bookState(b).rendering);
  useEffect(() => {
    if (!anyRendering) return;
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [anyRendering]);

  useEffect(() => {
    function handleKey(e) {
      if (!(e.metaKey || e.ctrlKey)) return;
      if (e.key === "f" && searchRef.current) {
        e.preventDefault();
        searchRef.current.focus();
      } else if (e.key === "o") {
        e.preventDefault();
        setImporting({});
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  function handleDrop(e) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) setImporting({ file });
  }

  const narratorOf = (book) => voices.find((v) => v.id === book.voice_profile_id)?.name ?? "the default voice";
  const reviewCount = books.filter((b) => bookState(b).failed > 0).length;
  const test = FILTERS.find(([key]) => key === filter)[2];
  const q = query.trim().toLowerCase();
  const visible = books.filter(
    (b) => test(bookState(b)) && (!q || `${b.title} ${b.author ?? ""}`.toLowerCase().includes(q)),
  );

  if (loading) return <p className="text-ink-muted">Opening the library…</p>;

  return (
    <div
      className="min-h-full"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.relatedTarget) setDragging(false);
      }}
      onDrop={handleDrop}
    >
      {books.length === 0 ? (
        <>
          <ErrorNote>{error}</ErrorNote>
          <EmptyShelf onAdd={() => setImporting({})} />
        </>
      ) : (
        <>
          <header className="flex flex-wrap items-center gap-x-6 gap-y-3 mb-10">
            <h1 className="text-title">Library</h1>
            <div role="group" aria-label="Filter books" className="flex flex-wrap gap-2 mr-auto">
              {FILTERS.map(([key, label]) => {
                if (key === "review" && reviewCount === 0) return null;
                const active = filter === key;
                return (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setFilter(key)}
                    className={`h-8 px-3.5 rounded-full text-meta transition-colors ${
                      active
                        ? "bg-accent-soft text-accent-text font-bold"
                        : "border border-line text-ink hover:bg-card"
                    }`}
                  >
                    {label}
                    {key === "review" && ` · ${reviewCount}`}
                  </button>
                );
              })}
            </div>
            <label className="relative">
              <span className="sr-only">Search your library</span>
              <Search
                className="absolute left-3.5 top-1/2 -translate-y-1/2 size-4 text-ink-muted"
                strokeWidth={1.7}
                aria-hidden="true"
              />
              <input
                ref={searchRef}
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search  ⌘F"
                className="h-9 w-56 rounded-full border border-line bg-card pl-9 pr-4 text-meta placeholder:text-ink-muted"
              />
            </label>
            <Button onClick={() => setImporting({})}>
              <Plus className="size-4" strokeWidth={2} aria-hidden="true" />
              Add a book
            </Button>
          </header>

          <ErrorNote>{error}</ErrorNote>

          {visible.length === 0 ? (
            <p className="pt-16 text-center font-serif text-[19px] text-ink-muted">Nothing on the shelf matches that.</p>
          ) : (
            <div className="flex flex-col pt-14">
              {groupShelves(visible).map((shelf) => (
                <Shelf key={shelf.key} shelf={shelf} narratorOf={narratorOf} />
              ))}
            </div>
          )}
        </>
      )}

      {dragging && (
        <div className="fixed inset-0 z-90 flex items-center justify-center bg-accent-soft/90 pointer-events-none">
          <p className="font-serif text-title text-accent-text">Drop to add to your shelf</p>
        </div>
      )}

      {importing && (
        <ImportBookModal
          initialFile={importing.file}
          onClose={() => setImporting(null)}
          onImported={(book) => navigate(`/books/${book.id}`)}
        />
      )}
    </div>
  );
}

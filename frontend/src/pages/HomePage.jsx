import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BookOpen, Plus, Upload } from "lucide-react";
import { api, bookStatus, isBookInProgress } from "../lib/api.js";
import Badge from "../components/ui/Badge.jsx";
import Button from "../components/ui/Button.jsx";
import Modal from "../components/ui/Modal.jsx";

const TILE_COLORS = [
  "bg-primary/10 text-primary",
  "bg-accent/10 text-accent",
  "bg-success/10 text-success",
];

function CoverTile({ title }) {
  const colorClass = TILE_COLORS[title.charCodeAt(0) % TILE_COLORS.length];
  return (
    <div className={`aspect-square w-full rounded-lg flex items-center justify-center text-3xl font-bold ${colorClass}`}>
      {title.charAt(0).toUpperCase()}
    </div>
  );
}

function ImportBookModal({ onClose, onImported }) {
  const [outputDir, setOutputDir] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", event.target.elements.file.files[0]);
      form.append("output_dir", outputDir);
      const book = await api.createBook(form);
      onImported(book);
    } catch (e) {
      setError(e.message);
      setSubmitting(false);
    }
  }

  return (
    <Modal title="Import a book" onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div>
          <label htmlFor="file" className="block text-sm font-medium mb-1">
            EPUB or Markdown file
          </label>
          <input
            id="file"
            type="file"
            name="file"
            accept=".epub,.md"
            required
            className="block w-full text-sm cursor-pointer file:mr-3 file:cursor-pointer file:rounded-lg
              file:border-0 file:bg-muted file:px-3 file:py-2 file:text-sm file:font-medium"
          />
        </div>
        <div>
          <label htmlFor="output_dir" className="block text-sm font-medium mb-1">
            Output folder path
          </label>
          <input
            id="output_dir"
            type="text"
            value={outputDir}
            onChange={(e) => setOutputDir(e.target.value)}
            required
            placeholder="/Users/you/audiobooks/this-book"
            className="w-full h-10 rounded-lg border border-border bg-background px-3 text-sm
              focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          />
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-3 mt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting}>
            <Upload className="size-4" aria-hidden="true" />
            Import
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export default function HomePage() {
  const [books, setBooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showImport, setShowImport] = useState(false);
  const navigate = useNavigate();

  async function refresh() {
    try {
      setBooks(await api.listBooks());
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!books.some(isBookInProgress)) return;
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [books]);

  if (loading) return <p className="text-muted-foreground">Loading…</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold">Your books</h1>
        <Button onClick={() => setShowImport(true)}>
          <Plus className="size-4" aria-hidden="true" />
          Import book
        </Button>
      </div>

      {error && <p className="text-sm text-destructive mb-4">{error}</p>}

      {books.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border py-24 text-center">
          <BookOpen className="size-10 text-muted-foreground mb-3" aria-hidden="true" />
          <p className="text-muted-foreground mb-4">No books yet. Import an EPUB or Markdown file to get started.</p>
          <Button onClick={() => setShowImport(true)}>
            <Plus className="size-4" aria-hidden="true" />
            Import book
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {books.map((book) => {
            const status = bookStatus(book);
            return (
              <button
                key={book.id}
                onClick={() => navigate(`/books/${book.id}`)}
                className="text-left rounded-xl border border-border bg-surface p-4 cursor-pointer
                  hover:border-primary/40 transition-colors duration-150
                  focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                <CoverTile title={book.title} />
                <h2 className="mt-3 font-semibold truncate" title={book.title}>
                  {book.title}
                </h2>
                <p className="text-sm text-muted-foreground truncate">{book.author || "Unknown author"}</p>
                <div className="mt-2">
                  <Badge status={status.key} label={status.label} />
                </div>
              </button>
            );
          })}
        </div>
      )}

      {showImport && (
        <ImportBookModal
          onClose={() => setShowImport(false)}
          onImported={(book) => navigate(`/books/${book.id}`)}
        />
      )}
    </div>
  );
}

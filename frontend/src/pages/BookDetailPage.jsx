import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Loader2, Play } from "lucide-react";
import { api, chapterInProgress } from "../lib/api.js";
import Badge from "../components/ui/Badge.jsx";
import Button from "../components/ui/Button.jsx";
import ProgressBar from "../components/ui/ProgressBar.jsx";

export default function BookDetailPage() {
  const { bookId } = useParams();
  const [book, setBook] = useState(null);
  const [voices, setVoices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [generatingChapterIds, setGeneratingChapterIds] = useState(new Set());
  const [error, setError] = useState(null);

  async function refresh() {
    setBook(await api.getBook(bookId));
  }

  useEffect(() => {
    Promise.all([refresh(), api.listVoices().then(setVoices)])
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [bookId]);

  const anyInProgress = book?.chapters.some(chapterInProgress);

  useEffect(() => {
    if (!anyInProgress) return;
    const id = setInterval(() => refresh().catch((e) => setError(e.message)), 2000);
    return () => clearInterval(id);
  }, [anyInProgress]);

  async function handleGenerate() {
    setGenerating(true);
    try {
      await api.generateBook(bookId);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  }

  async function handleGenerateChapter(chapterId) {
    setGeneratingChapterIds((prev) => new Set(prev).add(chapterId));
    try {
      await api.generateChapter(bookId, chapterId);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setGeneratingChapterIds((prev) => {
        const next = new Set(prev);
        next.delete(chapterId);
        return next;
      });
    }
  }

  async function handleVoiceChange(event) {
    const value = event.target.value;
    try {
      await api.assignVoice(bookId, value ? Number(value) : null);
      await refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  if (loading) return <p className="text-muted-foreground">Loading…</p>;
  if (!book) return <p className="text-destructive">Book not found.</p>;

  const allDone = book.chapters.every((c) => c.status === "done");

  return (
    <div className="max-w-3xl">
      <Link
        to="/"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground mb-4
          focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring rounded"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Back to library
      </Link>

      <h1 className="text-2xl font-semibold">{book.title}</h1>
      <p className="text-muted-foreground mb-6">{book.author || "Unknown author"}</p>

      {error && <p className="text-sm text-destructive mb-4">{error}</p>}

      <div className="flex items-center gap-4 mb-6">
        <Button onClick={handleGenerate} loading={generating} disabled={anyInProgress || allDone}>
          <Play className="size-4" aria-hidden="true" />
          Generate all
        </Button>

        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Voice</span>
          <select
            value={book.voice_profile_id ?? ""}
            onChange={handleVoiceChange}
            className="h-10 rounded-lg border border-border bg-background px-3 text-sm cursor-pointer
              focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <option value="">Default</option>
            {voices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="rounded-xl border border-border divide-y divide-border overflow-hidden">
        {book.chapters.map((chapter) => {
          const queued = chapter.status === "pending" && chapter.progress != null;
          const badgeStatus = queued ? "queued" : chapter.status;
          const inProgress = chapterInProgress(chapter);
          return (
            <div key={chapter.id} className="p-4 flex items-center gap-4">
              <div className="flex-1 min-w-0">
                <p className="font-medium truncate">{chapter.title}</p>
                {chapter.status === "generating" && (
                  <div className="mt-2 max-w-xs">
                    <ProgressBar value={chapter.progress} />
                  </div>
                )}
                {chapter.error && <p className="text-sm text-destructive mt-1">{chapter.error}</p>}
              </div>
              <Badge status={badgeStatus} label={badgeStatus} />
              {chapter.output_mp3_path && (
                <span
                  className="text-xs text-muted-foreground font-mono truncate max-w-40"
                  title={chapter.output_mp3_path}
                >
                  {chapter.output_mp3_path.split("/").pop()}
                </span>
              )}
              <button
                onClick={() => handleGenerateChapter(chapter.id)}
                disabled={chapter.status === "done" || inProgress || generatingChapterIds.has(chapter.id)}
                aria-label={`Generate ${chapter.title}`}
                title={`Generate ${chapter.title}`}
                className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground
                  transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40
                  focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                {generatingChapterIds.has(chapter.id) ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Play className="size-4" aria-hidden="true" />
                )}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

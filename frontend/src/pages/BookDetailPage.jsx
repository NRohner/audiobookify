import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, Loader2, Play, Square } from "lucide-react";
import { api, chapterInProgress } from "../lib/api.js";
import Badge from "../components/ui/Badge.jsx";
import Button from "../components/ui/Button.jsx";
import ProgressBar from "../components/ui/ProgressBar.jsx";
import EnginePicker from "../components/EnginePicker.jsx";
import { effectiveEngine, engineLabel, engineMismatchWarning, initialBookEngine } from "../lib/engines.js";

export default function BookDetailPage() {
  const { bookId } = useParams();
  const [book, setBook] = useState(null);
  const [voices, setVoices] = useState([]);
  const [engines, setEngines] = useState([]);
  const [chosenEngine, setChosenEngine] = useState(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [generatingChapterIds, setGeneratingChapterIds] = useState(new Set());
  const [cancelingChapterIds, setCancelingChapterIds] = useState(new Set());
  const [cancelingAll, setCancelingAll] = useState(false);
  const [error, setError] = useState(null);

  async function refresh() {
    const next = await api.getBook(bookId);
    setBook(next);
    setChosenEngine((current) => current ?? initialBookEngine(next));
  }

  useEffect(() => {
    Promise.all([refresh(), api.listVoices().then(setVoices), api.listEngines().then((r) => setEngines(r.engines))])
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [bookId]);

  const anyInProgress = book?.chapters.some(chapterInProgress);
  const engine = book ? effectiveEngine(chosenEngine, engines, book.voice_profile_id != null) : null;

  useEffect(() => {
    if (!anyInProgress) return;
    const id = setInterval(() => refresh().catch((e) => setError(e.message)), 2000);
    return () => clearInterval(id);
  }, [anyInProgress]);

  async function handleGenerate() {
    setGenerating(true);
    try {
      await api.generateBook(bookId, engine);
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
      await api.generateChapter(bookId, chapterId, engine);
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

  async function handleCancelChapter(chapterId) {
    setCancelingChapterIds((prev) => new Set(prev).add(chapterId));
    try {
      await api.cancelChapter(bookId, chapterId);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setCancelingChapterIds((prev) => {
        const next = new Set(prev);
        next.delete(chapterId);
        return next;
      });
    }
  }

  async function handleCancelAll() {
    setCancelingAll(true);
    try {
      await api.cancelBook(bookId);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setCancelingAll(false);
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
  const mismatch = engineMismatchWarning(book, engine, engines);

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

      <div className="flex flex-wrap items-start gap-4 mb-6">
        <Button onClick={handleGenerate} loading={generating} disabled={anyInProgress || allDone}>
          <Play className="size-4" aria-hidden="true" />
          Generate all
        </Button>
        <Button variant="secondary" onClick={handleCancelAll} loading={cancelingAll} disabled={!anyInProgress}>
          <Square className="size-4" aria-hidden="true" />
          Cancel all
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

        <EnginePicker
          engines={engines}
          value={engine}
          onChange={setChosenEngine}
          hasVoice={book.voice_profile_id != null}
          disabled={anyInProgress}
        />
      </div>

      {mismatch && (
        <div className="mb-6 rounded-lg border border-accent/30 bg-accent/10 p-3 text-sm text-accent flex gap-2">
          <AlertTriangle className="size-4 shrink-0 mt-0.5" aria-hidden="true" />
          <p>{mismatch}</p>
        </div>
      )}

      <div className="rounded-xl border border-border divide-y divide-border overflow-hidden">
        {book.chapters.map((chapter) => {
          const queued = chapter.status === "pending" && chapter.progress != null;
          const badgeStatus = queued ? "queued" : chapter.status;
          const inProgress = chapterInProgress(chapter);
          return (
            <div key={chapter.id} className="p-4 flex items-center gap-4">
              <div className="flex-1 min-w-0">
                <p className="font-medium truncate">{chapter.title}</p>
                {chapter.status === "done" && chapter.engine && (
                  <p className="text-xs text-muted-foreground">
                    {engineLabel(chapter.engine, engines)}
                  </p>
                )}
                {chapter.status === "generating" && (
                  <div className="mt-2 max-w-xs">
                    <ProgressBar value={chapter.progress} startedAt={chapter.started_at} />
                  </div>
                )}
                {chapter.error && <p className="text-sm text-destructive mt-1">{chapter.error}</p>}
              </div>
              <Badge status={badgeStatus} label={badgeStatus} />
              {chapter.used_fallback && (
                <span
                  title="The output folder wasn't writable when this chapter finished — saved to the backup location instead."
                  className="inline-flex items-center gap-1 text-xs text-accent"
                >
                  <AlertTriangle className="size-3.5" aria-hidden="true" />
                  Backup location
                </span>
              )}
              {chapter.output_mp3_path && (
                <span
                  className="text-xs text-muted-foreground font-mono truncate max-w-40"
                  title={chapter.output_mp3_path}
                >
                  {chapter.output_mp3_path.split("/").pop()}
                </span>
              )}
              <button
                onClick={() => handleCancelChapter(chapter.id)}
                disabled={!inProgress || cancelingChapterIds.has(chapter.id)}
                aria-label={`Cancel ${chapter.title}`}
                title={`Cancel ${chapter.title}`}
                className="cursor-pointer rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive
                  transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40
                  focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                {cancelingChapterIds.has(chapter.id) ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Square className="size-4" aria-hidden="true" />
                )}
              </button>
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

import { useEffect, useState } from "react";

export default function App() {
  const [book, setBook] = useState(null);
  const [outputDir, setOutputDir] = useState("");
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!book) return;
    const interval = setInterval(async () => {
      const res = await fetch(`/api/books/${book.id}`);
      if (res.ok) setBook(await res.json());
    }, 2000);
    return () => clearInterval(interval);
  }, [book?.id]);

  async function handleImport(event) {
    event.preventDefault();
    setError(null);
    const form = new FormData();
    form.append("file", event.target.elements.file.files[0]);
    form.append("output_dir", outputDir);
    const res = await fetch("/api/books", { method: "POST", body: form });
    if (!res.ok) {
      setError((await res.json()).detail);
      return;
    }
    setBook(await res.json());
  }

  async function handleGenerate() {
    await fetch(`/api/books/${book.id}/generate`, { method: "POST" });
  }

  return (
    <div style={{ fontFamily: "sans-serif", maxWidth: 640, margin: "2rem auto" }}>
      <h1>Audiobookify</h1>
      {!book && (
        <form onSubmit={handleImport}>
          <div>
            <input type="file" name="file" accept=".epub,.md" required />
          </div>
          <div>
            <input
              type="text"
              placeholder="Output folder path"
              value={outputDir}
              onChange={(e) => setOutputDir(e.target.value)}
              required
            />
          </div>
          <button type="submit">Import</button>
          {error && <p style={{ color: "red" }}>{error}</p>}
        </form>
      )}
      {book && (
        <div>
          <h2>{book.title}</h2>
          <button onClick={handleGenerate}>Generate all</button>
          <ul>
            {book.chapters.map((chapter) => (
              <li key={chapter.id}>
                {chapter.title} &mdash; {chapter.status}
                {chapter.progress != null && ` (${Math.round(chapter.progress * 100)}%)`}
                {chapter.output_mp3_path && ` -> ${chapter.output_mp3_path}`}
                {chapter.error && ` (error: ${chapter.error})`}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

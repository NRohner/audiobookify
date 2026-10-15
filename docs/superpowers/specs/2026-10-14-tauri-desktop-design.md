# Audiobookify desktop app (Rust + Tauri) — design

Status: approved 2026-10-14 (defaults accepted: personal Macs only, CLI dropped). Branch: `feat/tauri-desktop`.

## Goal

Turn the localhost web app into a native macOS desktop app built on Tauri 2. Everything except ML inference moves to Rust. Python stays only for the model runtimes (mlx-audio TTS, Whisper, wav2vec2), because the Rust ports of Qwen3-TTS and Chatterbox Turbo are still experimental (checked 2026-10-14). The engine boundary is a narrow interface, so engines can move to Rust one at a time later. Whisper via whisper-rs is the likely first.

## Decisions

| Topic | Decision |
|---|---|
| Rust scope | DB, ingest, text pipeline, lexicon, chunking, job queue, synthesis orchestration, audio post-processing, and all API logic (as Tauri commands). |
| Python scope | One child process, the **engine worker**: loads one engine and answers synthesize / transcribe / extract-phonemes requests. No DB access and no pipeline logic. |
| Rust↔Python IPC | JSON lines over the child's stdin/stdout. One request in flight at a time. Audio goes through WAV files in a temp dir, not through the pipe. |
| Data | Same SQLite schema. Rust (`rusqlite`) opens existing databases, so no data migration is needed. Rust takes over the schema migrations in `db.py`. |
| Frontend | The React app is reused as is. `lib/api.js` switches from `fetch` to `invoke`. |
| Migration style | Strangler: the Tauri shell first runs the existing FastAPI + worker as sidecars, and endpoints move to Rust commands one group at a time. The app stays usable on the branch throughout. |
| Progress | Polling stays (it's now `invoke` instead of `fetch`). Tauri events can come later if polling ever feels laggy. |
| Platform | macOS on Apple Silicon only (MLX already requires it). |

## Architecture

```
Tauri app (Rust)
 ├─ webview: existing React UI ── invoke() ──┐
 ├─ commands  (replace FastAPI endpoints)  <─┘
 ├─ core
 │   ├─ db        rusqlite, existing schema + migrations
 │   ├─ ingest    EPUB / Markdown -> chapters
 │   ├─ text      normalize, lexicon substitution, chunking
 │   ├─ audio     trim / level / concat, WAV I/O, MP3 via ffmpeg, ID3 tags
 │   └─ jobs      queue, pause/cancel, chunk cache, progress
 └─ engine client  trait Engine { synthesize, transcribe, phonemes, ... }
        └─ PythonEngine ── stdio JSON ──> python -m audiobookify.engine_worker
                                            (mlx-audio Qwen3 / Turbo, Whisper, wav2vec2)
```

The previous design split the API and worker into separate processes so a hanging engine couldn't freeze the API. That isolation is preserved: the crash-prone part (ML) is still its own process. If the child dies, Rust fails the current job, respawns the child on the next job, and the UI keeps working.

### Engine worker protocol (Python side)

Requests are one JSON object per line, and every request gets exactly one response line (`{"ok": ...}` or `{"error": "..."}`).

- `load {engine}`: swap the loaded engine (today's `EngineSlot`).
- `engine_info {}`: returns `max_chunk_chars`, `min_chunk_chars`, `sample_rate`.
- `synthesize {text, voice: {reference_path, reference_text, trim_end, tempo, params}, out_wav}`: writes a WAV file. Voice handles stay cached inside the worker, keyed by the voice fields.
- `transcribe {path}`: returns Whisper text and word timings.
- `phonemes {path}`: returns the wav2vec2 phoneme string.
- `shutdown {}`

The protocol carries no paths except explicit in/out files, and no DB IDs. Python's stdout is reserved for the protocol, and all logging goes to stderr. The existing `engines/fake.py` gives Rust tests a real child process without model weights.

## Module map

| Python today | Destination | Notes |
|---|---|---|
| `db.py`, `models.py` | Rust `db` | `rusqlite` (bundled SQLite). Port `test_db.py`'s 64 tests. |
| `ingest/epub.py`, `ingest/markdown.py` | Rust `ingest` | `zip` + `scraper` (html5ever) instead of ebooklib/BeautifulSoup. Whitespace handling is the parity risk. |
| `normalize/text.py` | Rust `text` | Needs `fancy-regex`: the number regex and the sentence splitter use lookbehind/lookahead. `num2words` crate or a hand port of the English rules; decided by golden-test parity. |
| `lexicon/substitution.py`, `phoneme/respelling.py` | Rust `text` | Small, pure. |
| `synth/pipeline.py`, `synth/chunk_cache.py`, `worker.py` (job loop) | Rust `jobs` | Pause/cancel stay checked between chunks, same as today. |
| `postprocess/audio.py` | Rust `audio` | `hound` for WAV, plain f32 loops for trim/level/concat, `ffmpeg` child process for MP3 (as today), `id3` crate for tags. |
| `voice/validation.py`, `voice/analysis.py` (pure parts: trim point, WER, cadence, pace), `voice/storage.py` | Rust | Reading audio uses `symphonia` for non-WAV uploads. |
| `voice/analysis.transcribe`, `phoneme/wav2vec2.py`, `engines/*` | **Stay Python** | Behind the engine worker. |
| `api/app.py` | Deleted | Replaced by Tauri commands. |
| `cli.py` | Deleted | See open question 2. |
| `bench.py` | Stays Python | A separate ML benchmarking tool, not part of the app. |

## Frontend changes

- `lib/api.js`: each function calls `invoke("cmd", args)`. During migration, unported endpoints still call `fetch` against the FastAPI sidecar.
- **Book and voice import:** the Tauri dialog plugin returns real file paths, so the multipart upload goes away. Rust copies the file into the app data dir, as today's staging dir does, so moving or deleting the original can't break a book.
- **Output directory:** the native folder picker replaces `/api/browse-dir`. `validate-output-dir` stays as a command.
- **Audio playback** (Player, voice preview, text clips): Tauri's asset protocol (`convertFileSrc`) instead of `FileResponse` URLs.
- The UI keeps following `docs/design/DESIGN.md` and the `--rr-*` tokens. The window chrome uses native macOS title-bar styling.

## Data location

The app data dir (`~/Library/Application Support/com.audiobookify.app/`) holds the DB, staged uploads, voices, the chunk cache and text clips. On first launch, if the app data dir has no DB and a repo-root `audiobookify.db` exists, the app offers to import it, rewriting the stored relative paths for uploads and voices. Model weights stay in the Hugging Face cache.

## Testing

- **TDD port:** each Python test file has a Rust counterpart, written before the code.
- **Golden parity tests** for the text pipeline: a one-off Python script runs today's `ingest` → `normalize` → `chunk_text` over the test fixtures and a few real books, and writes the outputs as JSON. Rust tests must produce identical output. This is how we prove the port doesn't change what gets spoken.
- **Engine boundary:** Python tests for the worker protocol using the fake engine, plus a Rust integration test that spawns the real child in fake mode and generates a short chapter end to end.
- **Frontend:** existing `node --test` suites unchanged.
- **Real engines:** one manual listening check per engine at the end of phase 3, against chapters generated by the current app.

## Phases (each one mergeable on its own)

0. **Tauri shell.** Scaffold Tauri 2 in `desktop/`. It loads the Vite dev server or built frontend and spawns the existing uvicorn + worker as sidecars, killing them on quit. Exit: `cargo tauri dev` gives a working app identical to today's.
1. **Rust DB.** Port `db.py` and its tests. Exit: Rust opens the current `audiobookify.db` read/write, and the migrations are idempotent.
2. **Rust text pipeline.** Ingest, normalize, lexicon and chunking, with golden parity. Exit: parity on all fixtures and sample books.
3. **Engine worker + Rust job runner.** Python `engine_worker.py` plus the Rust job loop, chunk cache and audio post-processing. Exit: Rust generates a full book with both real engines, and the listening check passes.
4. **Commands + frontend switch.** Port the endpoint groups (books, chapters, worker, voices, pronunciations, text clips), add native dialogs and the asset protocol. Exit: no `fetch` calls left in `api.js`.
5. **Delete Python app code.** Remove FastAPI, the CLI, and the Python pipeline/DB. Shrink Python dependencies, and replace the Makefile `up`/`dev` targets with Tauri ones.
6. **Packaging.** The scope depends on open question 1.

## Risks

- **num2words parity:** Rust crates may phrase numbers differently ("one hundred and five" vs "one hundred five"). Golden tests catch it, and the fallback is hand-porting the English rules we actually hit.
- **EPUB HTML whitespace:** html5ever and BeautifulSoup differ on whitespace and nesting edge cases. The `\n\n` paragraph rule from the chunking overhaul has to survive the port.
- **Locating Python from a `.app` bundle:** dev uses the repo `.venv`. A packaged app needs an embedded Python or a first-run setup (see open question 1).
- **ffmpeg / espeak-ng:** still external binaries (Homebrew) unless they get bundled.

## Notes from phase 0

- Phase 3: the Python engine worker must exit on stdin EOF. The JSON-lines protocol pipe then gives the same die-with-the-app guarantee, so don't stack the phase 0 `sh` wrapper on it. The wrapper takes over the child's stdin.
- Phase 4: switch the release window from `WebviewUrl::External("http://127.0.0.1:8000")` to `WebviewUrl::App` (the bundled frontendDist). Remaining `fetch` calls go to `http://127.0.0.1:8000`, with CORS enabled on FastAPI. Never grant a `remote.urls` capability for 127.0.0.1:8000, because that would expose IPC to whatever serves the port. Set a real CSP at the same time. Phase 4 should also: log the children's output to a file under the app data dir, show a native dialog for setup errors, and avoid blocking the main thread before the window appears.

## Open questions

1. **Distribution:** personal Macs only (unsigned `.app`, uses the Homebrew ffmpeg/espeak-ng and the repo venv), or a shareable signed `.dmg` (embedded Python via python-build-standalone + uv, bundled ffmpeg, models downloaded on first run, and the phonemizer GPL-3.0 question)? **Default: personal Macs only.** Phase 6 grows if the answer changes.
2. **CLI:** is the command-line interface still used? **Default: drop it.** A thin Rust CLI over the same core crate can be added later if wanted.

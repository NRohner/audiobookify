# Audiobookify desktop app (Rust + Tauri) — design

Status: approved 2026-10-14 (defaults accepted: personal Macs only, CLI dropped). Branch: `feat/tauri-desktop`.

## Goal

Turn the localhost web app into a native macOS desktop app built on Tauri 2. Everything except ML inference moves to Rust. Python stays only for the model runtimes (mlx-audio TTS, Whisper), because the Rust ports of Qwen3-TTS and Chatterbox Turbo are still experimental (checked 2026-10-14). The engine boundary is a narrow interface, so engines can move to Rust one at a time later. Whisper via whisper-rs is the likely first.

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
 └─ engine client  trait Engine { synthesize, transcribe, ... }
        └─ PythonEngine ── stdio JSON ──> python -m audiobookify.engine_worker
                                            (mlx-audio Qwen3 / Turbo, Whisper)
```

The previous design split the API and worker into separate processes so a hanging engine couldn't freeze the API. That isolation is preserved: the crash-prone part (ML) is still its own process. If the child dies, Rust fails the current job, respawns the child on the next job, and the UI keeps working.

### Engine worker protocol (Python side)

Requests are one JSON object per line, and every request gets exactly one response line (`{"ok": ...}` or `{"error": "..."}`).

- `load {engine}`: swap the loaded engine (today's `EngineSlot`).
- `engine_info {}`: returns `max_chunk_chars`, `min_chunk_chars`, `sample_rate`.
- `synthesize {text, voice: {reference_path, reference_text, trim_end, tempo, params}, out_wav}`: writes a WAV file. Voice handles stay cached inside the worker, keyed by the voice fields.
- `transcribe {path}`: returns Whisper text and word timings.
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
| `voice/analysis.transcribe`, `engines/*` | **Stay Python** | Behind the engine worker. |
| `phoneme/wav2vec2.py` | Deleted in phase 5 | Pronunciation from audio was removed. |
| `api/app.py` | Deleted | Replaced by Tauri commands. |
| `cli.py` | Deleted | See open question 2. |
| `bench.py` | Stays Python | A separate ML benchmarking tool, not part of the app. |

## Frontend changes

- `lib/api.js`: each function calls `invoke("cmd", args)`.
- **Book and voice import:** the Tauri dialog plugin returns real file paths, so the multipart upload goes away. Rust copies the file into the app data dir, as today's staging dir does, so moving or deleting the original can't break a book.
- **Output directory:** the native folder picker replaces `/api/browse-dir`. `validate-output-dir` stays as a command.
- **Audio playback** (Player, voice preview, text clips): Tauri's asset protocol (`convertFileSrc`) instead of `FileResponse` URLs.
- The UI keeps following `docs/design/DESIGN.md` and the `--rr-*` tokens. The window chrome uses native macOS title-bar styling.

## Data location

The app has used the app data dir (`~/Library/Application Support/com.audiobookify.app/`) since phase 4. It holds the DB, staged uploads, voices, the chunk cache and text clips. On first launch, if the app data dir has no DB and a repo-root `audiobookify.db` exists, the app offers to import it, rewriting the stored relative paths for uploads and voices. The import copies the DB with `VACUUM INTO` plus `uploads/`, `voices/` and `text_clips/`, and rewrites the stored paths, including the relative `text_clips/…` ones the Python worker wrote. The chunk cache isn't copied. The repo's files are left in place. Model weights stay in the Hugging Face cache.

## Testing

- **TDD port:** each Python test file has a Rust counterpart, written before the code.
- **Golden parity tests** for the text pipeline: a one-off Python script runs today's `ingest` → `normalize` → `chunk_text` over the test fixtures and a few real books, and writes the outputs as JSON. Rust tests must produce identical output. This is how we prove the port doesn't change what gets spoken.
- **Engine boundary:** Python tests for the worker protocol using the fake engine, plus a Rust integration test that spawns the real child in fake mode and generates a short chapter end to end.
- **Frontend:** existing `node --test` suites unchanged.
- **Real engines:** one manual listening check per engine at the end of phase 3, against chapters generated by the current app.

## Settings page

A new Settings screen (added 2026-10-15). It has three parts: stats, models, and first-run setup. It is built directly on Tauri commands, so it comes after phase 4 and is never built against FastAPI. The UI follows `docs/design/DESIGN.md` and the `--rr-*` tokens.

### Stats

All stats are computed in Rust from the existing tables. No new tracking tables are needed.

| Stat | Source |
|---|---|
| Hours of audio generated | `chapters.duration` for done chapters, plus text clips |
| Words narrated | word count of done chapters' `text` |
| Books finished / chapters generated | `chapters.status`, grouped by book |
| Time spent generating, and average speed (x realtime) per engine | `jobs.finished_at - jobs.started_at` for done jobs, joined to `chapters.engine` and `duration` |
| Longest book, most-used voice, busiest day | `chapters`, `books.voice_profile_id`, `jobs.finished_at` |
| Disk space used by your audiobooks | size of each done chapter's `output_mp3_path` |
| Fun framings | for example "X days of nonstop listening" and "about N average novels (90k words)" |

- **Missing durations:** older chapters have `duration` NULL (21 of 26 done chapters had one on 2026-10-15). On first stats load, Rust backfills NULL durations from the MP3 headers, once, and writes them back.
- **Text clips:** they have no `duration` column. Add one in the same migration and backfill it the same way.

### Models

The page lists the models the app uses, and only those. Each row shows the role, the repo, the size on disk, and whether it is downloaded. Each model can be downloaded or deleted.

- **Registry:** a fixed list in Rust of the Hugging Face repos each feature needs:
  - Quality engine (Qwen3): `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16`
  - Speed engine (Turbo): `mlx-community/chatterbox-turbo-fp16`
  - Voice transcription (Whisper): `mlx-community/whisper-large-v3-turbo-asr-fp16`
  - Plus any repos those fetch at load time, such as `mlx-community/S3TokenizerV2`. Build the full list by loading each engine against an empty `HF_HOME` and recording what gets downloaded.
- **The HF cache is shared with other projects** on this machine (on 2026-10-15 it also held Gemma, Surya, F5-TTS and BERT models). The page must never list or delete repos outside the registry.
- **Sizes and deletion use `huggingface_hub`'s own cache API** (`scan_cache_dir`, `delete_revisions`), not hand-rolled `du`/`rm`. Snapshots are symlinks into blob stores, so naive sizing and deletion get this wrong.
- **Downloads run in a separate one-shot Python process** with `HF_HUB_OFFLINE` unset. The engine worker stays offline, so a download never blocks or affects synthesis. That process reports progress as JSON lines on stdout (the same convention as the engine worker), and the page shows it per model. It can be cancelled.
- **Delete is refused while it would break something:**
  - when the model is loaded in the engine worker
  - when a queued or running job needs it
  The confirm dialog shows the size that will be freed.

### First-run setup

On launch, if no TTS engine's models are present, the app opens Settings → Models with a short "Download a voice engine to get started" panel. It recommends one engine and shows the download size.

- Generating with an engine whose models are missing is blocked, with a link to this page.
- This replaces `make models` for app users. The make target stays for development.

## Phases (each one mergeable on its own)

0. **Tauri shell.** Scaffold Tauri 2 in `desktop/`. It loads the Vite dev server or built frontend and spawns the existing uvicorn + worker as sidecars, killing them on quit. Exit: `cargo tauri dev` gives a working app identical to today's.
1. **Rust DB.** Port `db.py` and its tests. Exit: Rust opens the current `audiobookify.db` read/write, and the migrations are idempotent.
2. **Rust text pipeline.** Ingest, normalize, lexicon and chunking, with golden parity. Exit: parity on all fixtures and sample books.
3. **Engine worker + Rust job runner.** Python `engine_worker.py` plus the Rust job loop, chunk cache and audio post-processing. Exit: Rust generates a full book with both real engines, and the listening check passes.
4. **Commands + frontend switch.** Port the endpoint groups (books, chapters, worker, voices, pronunciations, text clips), add native dialogs and the asset protocol. Exit: no `fetch` calls left in `api.js`.
5. **Settings page.** Stats, model management and first-run setup (see "Settings page"). Exit: on a fresh `HF_HOME` the app opens setup, downloads an engine and generates a chapter, and deleting a model frees the reported space without touching non-registry repos.
6. **Delete Python app code.** Remove FastAPI, the CLI, and the Python pipeline/DB, and shrink the Python dependencies. (The Makefile moved to Tauri targets in phase 4.)
7. **Packaging.** The scope depends on open question 1.

## Risks

- **num2words parity:** Rust crates may phrase numbers differently ("one hundred and five" vs "one hundred five"). Golden tests catch it, and the fallback is hand-porting the English rules we actually hit.
- **EPUB HTML whitespace:** html5ever and BeautifulSoup differ on whitespace and nesting edge cases. The `\n\n` paragraph rule from the chunking overhaul has to survive the port.
- **Locating Python from a `.app` bundle:** dev uses the repo `.venv`. A packaged app needs an embedded Python or a first-run setup (see open question 1).
- **ffmpeg / espeak-ng:** still external binaries (Homebrew) unless they get bundled.

## Notes from phase 0

- Phase 3: the Python engine worker must exit on stdin EOF. The JSON-lines protocol pipe then gives the same die-with-the-app guarantee, so don't stack the phase 0 `sh` wrapper on it. The wrapper takes over the child's stdin.
- Phase 4: switch the release window from `WebviewUrl::External("http://127.0.0.1:8000")` to `WebviewUrl::App` (the bundled frontendDist). Remaining `fetch` calls go to `http://127.0.0.1:8000`, with CORS enabled on FastAPI. Never grant a `remote.urls` capability for 127.0.0.1:8000, because that would expose IPC to whatever serves the port. Set a real CSP at the same time. Phase 4 should also: log the children's output to a file under the app data dir, show a native dialog for setup errors, and avoid blocking the main thread before the window appears.

## Notes from phase 3

- Exit check (2026-10-15), scaled down from "a full book": the same 2,200-character passage (Jim Dale voice) was generated as a text clip by the Python worker and by the Rust runner, with each engine. Speed matched (Turbo 71 s vs 68 s, Qwen3 128 s vs 131 s), and the user's listening check passed. Chapter resume, pause, cancel and respawn are covered by the Rust runner tests against the real engine worker child.
- The listening check caught Rust MP3s coming out stereo, each channel 3 dB down: ffmpeg reads hound's float32 WAV header as stereo. The encode now forces `-ac 1`, and a test checks the output is mono. Loudness then matched Python (-26.8 vs -26.9 LUFS).
- Engine WAVs are float32 and written atomically; Python's chunk cache was 16-bit. A chapter paused under the Python worker regenerates once under Rust (cache keys differ).
- Still to move in phase 4: `relabel_chapter_mp3` (chapter rename) and wav2vec2 phonemes (a `phonemes` request), both unused by the runner. Runner and engine errors only reach stderr, so route them to the phase 4 log file.
- Fake-engine test knobs (`AUDIOBOOKIFY_FAKE_*`) live on `FakeEngine` and `engine_worker`; the Rust runner tests rely on them. `engine_worker` imports `EngineSlot` from `worker.py`, so move it before phase 6 deletes that module.

## Notes from phase 4

- Every endpoint is a command in `desktop/src/commands.rs` over `audiobookify_core::app`, whose tests port `tests/test_api.py` and the API half of `tests/test_chapter_breaks.py`. The uvicorn sidecar is gone, so CORS was never needed.
- The browser app stopped working here: the frontend calls `invoke`. `make dev`/`make up` are Tauri targets. FastAPI and the CLI stay, untested by the UI, until phase 6.
- App data lives in `~/Library/Application Support/com.audiobookify.app` (see "Data location"). Release builds send stderr (runner errors, engine worker logging) to `logs/audiobookify.log` there, rotated at 5 MB on launch.
- Dropped: wav2vec2 `phonemes` (only the CLI's `lexicon add --from-audio` used it; the GUI uses typed respellings) and `browse-dir` (the native folder picker replaced it, with no typed path).
- The voice PATCH became `set_voice_transcript` and `set_voice_tempo`. Audio reaches `<audio>` through `*_audio_path` commands that add the one file to the asset scope; text-clip Download is a save dialog plus `save_text_clip`. The dialog plugin also adds every picked path to the asset scope (a picked output folder recursively), so the scope is not file-by-file only.
- One copy runs at a time: `main()` holds an exclusive `flock` on `audiobookify.lock` in the data dir.
- Audio files the user picks are read with symphonia (`core::media`). Reference trims are written as 16-bit WAV, matching soundfile, so Rust and the Python engine worker produce the same cached file.
- `create_book` and `create_voice` both copy the picked file into the app data dir.
- `relabel_chapter_mp3` failure handling changed: if retagging fails after the rename, the chapter keeps the renamed file. Python lost track of it.
- `import_repo_data` refuses to run when the app data dir already has a DB, removes its temporary `audiobookify.db.importing` if it fails, and rewrites rows stored under either the canonical or the as-given spelling of the repo path.

## Notes from phase 5

- wav2vec2 is gone (user decision, 2026-10-16): the weights were deleted from the HF cache, and `phoneme/`, the CLI's `lexicon add-from-audio`, `torch`, `torchaudio`, `phonemizer` and the espeak-ng requirement were removed. The `lexicon_entries.phonemes` column stays for existing rows.

## Open questions

1. **Distribution:** personal Macs only (unsigned `.app`, uses the Homebrew ffmpeg/espeak-ng and the repo venv), or a shareable signed `.dmg` (embedded Python via python-build-standalone + uv, bundled ffmpeg, models downloaded on first run, and the phonemizer GPL-3.0 question)? **Default: personal Macs only.** Phase 7 grows if the answer changes.
2. **CLI:** is the command-line interface still used? **Default: drop it.** A thin Rust CLI over the same core crate can be added later if wanted.

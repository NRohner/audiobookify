# Tauri phase 3: engine worker + Rust job runner — design

Status: approved 2026-10-15. Parent spec: `2026-10-14-tauri-desktop-design.md` (phase 3). Branch: `feat/tauri-phase3-jobs`.

## Goal

The Rust job runner replaces the Python `audiobookify worker` sidecar in the Tauri app. It runs chapter jobs, text clips and voice transcriptions against the same SQLite DB that FastAPI still enqueues into. ML runs in one Python child, `engine_worker.py`, over JSON lines on stdio. Exit: the Tauri app generates a full book with Qwen3 and with Turbo, and the listening check against chapters from the current app passes.

## Decisions

| Topic | Decision |
|---|---|
| Runner role | Replaces the Python worker in Tauri (strangler). `make up` keeps the Python worker until phase 6. |
| Engine abstraction | A concrete `PythonEngine`, no `trait Engine`. Add the trait when a second implementation (a Rust engine) exists. |
| Reference clip prep | Stays in Python. `engine_worker` calls `analysis.reference_clip` (trim + ffmpeg atempo, cached next to the original). |
| Chunk cache | Same on-disk layout as today. Rust cache keys need not match Python's, so a chapter paused under the Python worker regenerates once after the switch. |
| Paths | Repo-relative as today (`audiobookify.db`, `.cache/synth`, `text_clips`, `output/`), because FastAPI still serves these files. The app data dir move is phase 4. |
| Audio parity | Float tolerance (~1e-6) against Python goldens, not bit-exact. RMS/level math uses f64 as numpy does. |
| `relabel_chapter_mp3` | Deferred to phase 4; only the chapter rename endpoint uses it. |
| Chunk WAVs | float32 (`soundfile` subtype FLOAT), read losslessly by `hound`. |
| Errors before synthesis | Fail the chapter as well as the job (one error path; the chapter row shows the message). |

## Components

### Python: `src/audiobookify/engine_worker.py`

Entry point `python -m audiobookify.engine_worker`. Reads one JSON request per line from stdin, writes exactly one JSON response line to stdout (`{"ok": {...}}` or `{"error": "..."}`), and exits on stdin EOF or `shutdown`. All logging goes to stderr. Stdout carries nothing but protocol lines; anything a library prints to stdout must be redirected to stderr (`contextlib.redirect_stdout`) around engine calls.

| Request | Response `ok` | Notes |
|---|---|---|
| `load {engine}` | `{}` | `EngineSlot.get(engine)`; `"fake"` maps to `FakeEngine`. |
| `engine_info {}` | `{name, max_chunk_chars, min_chunk_chars, sample_rate, needs_transcript}` | For the loaded engine. |
| `synthesize {text, voice, out_wav}` | `{samples, sample_rate}` | `voice = {reference_path, reference_text, trim_end, tempo, exaggeration, cfg, temperature}`; any field may be null (default voice). Writes mono float32 WAV to `out_wav`. |

`synthesize` writes `out_wav` atomically (temp file in the same dir, then `os.replace`), so Rust can trust any WAV that exists.
| `transcribe {path}` | `{text, words: [{word, start, end}]}` | `analysis.transcribe`; loads and frees Whisper per call, as today. |
| `shutdown {}` | `{}` | Then exits. |

Voice handles: `synthesize` builds the engine params with the existing `params_from_profile` logic (reference clip, text, trim, tempo, sampling fields) and caches the handle in `EngineSlot.profile_cache`, keyed by `(reference_clip_path, reference_text)`, as `_ensure_profile_cached` does today. A `load` of a different engine clears it.

An unknown request type, bad JSON or an engine exception returns `{"error": ...}` and the worker keeps running.

### Rust: `core/src/engine.rs`

`PythonEngine { python: PathBuf, cwd: PathBuf, env, child: Option<…>, loaded: Option<String> }`.

- `PythonEngine::new(python, cwd)`; the child spawns lazily on the first request, with stdin/stdout piped and stderr inherited.
- Methods: `load(name)`, `info()`, `synthesize(text, &Voice, out_wav) -> Result<SynthOut>`, `transcribe(path) -> Result<Transcript>`, `shutdown()`.
- `load` is a no-op when `name` is already loaded.
- If the child dies, the pipe breaks, or a response line won't parse: the call returns `Err`, the child is killed and dropped, and `loaded` is cleared. The next call respawns the child and re-sends `load` as needed. `Drop` closes stdin (EOF), waits briefly, then kills.
- Types use `serde` derive (`serde` added to core's dependencies).

### Rust: `core/src/audio.rs`

Port of `postprocess/audio.py`:

- `PAUSE_SECONDS` keyed by `ChunkEnd` (from phase 2's `text::chunk`).
- `trim_silence`, `level_chunks`, `concatenate_chunks(chunks, ends_with, sample_rate)`. The constants are the same as in Python.
- `read_wav` / `write_wav` (mono f32) via `hound`.
- `sanitize_filename`, `chapter_filename`.
- `export_chapter_mp3(...) -> Result<(PathBuf, bool)>` and `export_mp3(...)`: write a temp WAV, run `ffmpeg -y -i tmp.wav -codec:a libmp3lame -qscale:a 2 out.mp3` with stdin set to `/dev/null` (never a closed fd 0; see the phase 0 note), delete the temp WAV, write ID3 tags (TIT2/TALB/TRCK/TPE1) with the `id3` crate. On an I/O error, retry once in the fallback dir and return `used_fallback = true`.

### Rust: `core/src/jobs/`

- `cache.rs`: `compute_cache_key(chunk_texts, voice_key) -> String` (SHA-256 over a JSON object with sorted keys, `sha2` crate), `load_cached_chunks`, `save_chunk`, `write_manifest`, `clear_cache`. Layout: `<cache_root>/chapter-N/manifest.json` plus `chunk-NNNN.wav`. A bad or missing manifest means an empty cache.
- `voice.rs`: `trim_point(words, max_seconds)`, a port of `analysis.trim_point` (same constants and sentence-end regex). Plus `Voice::from_profile(profile, tempo_override)`.
- `runner.rs`:
  - `Runner { db, engine, paths: Paths { cache_root, text_clip_dir, backup_dir } }`.
  - `run_once() -> Result<bool>`, the same order as `worker.run_worker_once`: paused → false; else one pending transcription; else one chapter job; else one text clip.
  - `synthesize_chapter`: mark generating, `apply_lexicon`, `chunk_text(info.max, info.min)`. The cache key covers the chunks, the voice and the engine. Resume from cached chunks. After each chunk: update progress, check pause (requeue job, chapter → pending) and cancel (job cancelled, chapter → pending, clear cache). Then concatenate, export, mark the chapter done with path, duration, fallback flag and engine, and clear the cache. Any other error fails the chapter and the job with the message.
  - `synthesize_text_clip`: the same without the cache. Output `text_clips/text-clip-{id}.mp3`, title `Text clip #{id}`.
  - Transcription: `engine.transcribe`, then `trim_point`, then `save_voice_transcript`; on error, `fail_voice_transcription`. A chapter or clip whose engine `needs_transcript` and whose voice has an empty transcript is transcribed first (today's `load_voice`).
  - `run_forever(stop: &AtomicBool)`: `reset_stale_generating_jobs` and `reset_stale_generating_text_clips` first, then loop `run_once` with a 2 s sleep when idle. A loop-iteration error is logged to stderr and the loop continues.

Chunk WAVs from the engine go straight into the cache dir (`out_wav` = the chunk's cache path), so no audio passes through the pipe and no copy is made. Text clips use a temp dir.

### Tauri: `desktop/src/`

- `sidecars::audiobookify_commands` returns only the uvicorn command.
- `main.rs` starts a `std::thread` that opens its own `Database` on `<repo>/audiobookify.db`, builds `PythonEngine::new(<repo>/.venv/bin/python, <repo>)`, and calls `run_forever`. On `RunEvent::Exit`, set the stop flag and drop the runner, which closes the child's stdin. The runner child does not use the `sh` wrapper.
- `desktop` depends on `audiobookify-core` from this phase on.

## Testing

TDD: each Rust test is written before the code it covers.

- **Python** `tests/test_engine_worker.py`: drive `engine_worker` as a subprocess with `fake`. Cover load/info/synthesize (WAV written, sample count matches), an error response on a bad request with the worker still alive, exit on EOF, and stdout cleanliness.
- **Rust audio** `core/tests/audio.rs`: ports of `test_postprocess_audio.py`, plus goldens. `core/tests/fixtures/audio/generate.py` writes small synthetic chunks and Python's `trim_silence` / `level_chunks` / `concatenate_chunks` outputs as JSON; Rust must match within 1e-6. The MP3 tests run real ffmpeg and read the tags back.
- **Rust cache** `core/tests/chunk_cache.rs`: port of `test_chunk_cache.py`.
- **Rust runner** `core/tests/runner.rs`: ports of the `test_pipeline.py` and `test_worker.py` cases, against a real `PythonEngine` in fake mode with a temp DB and temp paths. Cover chapter done, resume from cache, pause requeues, cancel clears, text clip done, transcription (fake transcribe), and engine child death → job failed and the next job succeeds after respawn.
- **Real engines (manual, exit check):** in `cargo tauri dev`, generate a full book with Qwen3 and one with Turbo, then listen against chapters the current app made.

There is no fake Whisper. When the child runs with `AUDIOBOOKIFY_FAKE_STT=1` (set through `PythonEngine`'s `env`), `engine_worker` answers `transcribe` with a fixed transcript and word timings longer than 15 s, so runner tests cover transcription and `trim_point` without Whisper weights. ponytail: an env test hook; replace it with a fake STT class if transcription grows.

## Out of scope

- Commands, frontend changes, native dialogs, the app data dir (phase 4).
- `relabel_chapter_mp3` and a `phonemes` (wav2vec2) request (phase 4). Nothing calls wav2vec2 today.
- Rust key parity with Python's chunk cache.
- Retiring the Python worker from `make up` (phase 6).

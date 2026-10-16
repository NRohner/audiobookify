# Audiobookify

Fully local EPUB/Markdown → audiobook (MP3 per chapter) converter. Requires Apple Silicon (TTS runs on [MLX](https://github.com/ml-explore/mlx)) and Python 3.12 (`brew install python@3.12`). See `project-spec.md` for the original design and `docs/superpowers/plans/` for implementation plans.

## Setup

1. Install [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg` on macOS, or see your platform's docs).
2. `make setup` — creates a Python 3.12 venv and installs the Python package + dev dependencies.
3. `make models` — a development shortcut that downloads and caches the Chatterbox Turbo TTS (MLX) weights through the same code the app uses (one-time; cached under your Hugging Face hub cache directory). Re-run any time to verify the cache. App users download engines from Settings instead.

## Desktop app

`make dev` opens Audiobookify in a native window (with hot reload). `make app` builds `desktop/target/release/bundle/macos/Audiobookify.app`, and `make up` builds it and opens it. You can also open it from Finder. The window serves everything itself; there is no separate API server or port. The app runs this checkout's `.venv` for the voice engine, so build it after `make setup` and don't move the repo. Building needs the Rust toolchain (rustup) and tauri-cli: `cargo install tauri-cli --version "^2"`. `make down` stops a Vite dev server left behind by an interrupted `make dev`.

Only one copy runs at a time: a second launch (say the `.app` while `make dev` is open) shows an alert and exits, because both would share one database.

**Where your data lives:** `~/Library/Application Support/com.audiobookify.app` holds the database (`audiobookify.db`), `uploads/`, `voices/`, `cache/synth/`, `text_clips/`, the `output/` fallback for chapters whose chosen folder can't be written, and (release builds only) `logs/audiobookify.log`. `make dev` and the `.app` use this same folder, so they see the same library.

On first launch the app offers to import this repo's library (books, voices, pronunciations, text clips; the originals stay put). Choosing "Start empty" is permanent. To import later, quit the app and delete `audiobookify.db` from that folder.

The backend is Rust; Python only runs the TTS engines. Design notes: `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md`.

## Engines

Two TTS engines, picked per book or text clip in the app. The choice is stored on the book, so resumed and requeued chapters keep it.

- **Quality (`qwen3`)**: Qwen3-TTS 1.7B Base. Most natural; about 0.85-0.9 s to generate a second of audio; peaks around 11 GB. Needs a voice profile with a transcript of its reference clip, and copies the clip's speaking pace.
- **Speed (`chatterbox-turbo`)**: Chatterbox Turbo. About twice as fast, slightly less natural; peaks around 5 GB. Works without a voice (built-in default voice).

The worker keeps one engine loaded at a time and swaps when a job needs the other (a few seconds). Each chapter records which engine made it, and the book page warns when a book's chapters would mix engines.

## Pronunciation lexicon

Register a respelling for any term (a name, an invented word, jargon) on the Pronunciations page so it is read correctly. Entries are global (every book) or scoped to one book; a book's own entry wins over a global one.

- Substitution is whole-word and case-insensitive, and supports multi-word terms ("New York"). When two terms overlap ("York" and "New York"), the longer one wins.
- Keep respellings to plain letters and hyphens: the term is substituted before number and abbreviation expansion runs, so digits or a period could be altered. A replacement can also be matched by a later entry, so keep respellings free of other registered terms.
- Each entry has a preview that speaks a short sentence with the respelling applied.
- Changes only affect chapters not yet generated. A chapter already marked done keeps its audio until you regenerate it.
- Only respellings are supported. Older entries may still hold stored phonemes; they are not used.

## Voice cloning

Import a short reference clip (10-30 s recommended, clean single-speaker audio) on the Voices page, preview it, then pick it when generating a book.

- Adding a voice validates the clip (sample rate, length, channels) and warns about anything outside the recommended range, clipping, or constant background noise. Only an unusably short (under 1 s) or unreadable file is rejected.
- Without a transcript, the app transcribes the clip with Whisper (`whisper-large-v3-turbo` on MLX), or the Quality engine does so the first time it needs it. Transcripts are shown and editable on the Voices page.
- Clips over 15 s are trimmed at a word (preferably sentence) boundary for cloning, because Qwen3 re-encodes the whole reference for every chunk and a 36 s clip ran about 15% slower than a 15 s one. The transcript then covers the kept part.
- The Voices page shows each clip's measured pace (speaking wpm with pauses excluded, and overall wpm) and a tempo slider (0.75-1.25x, with a sweet spot at about 174 wpm overall). A saved tempo plays the reference through `ffmpeg atempo` (pitch kept) before cloning; the derived clip is cached next to the reference. Preview the slowed reference or generate a short Quality sample at the slider's tempo before saving: Qwen3 follows only part of a reference pace change.
- The ID3 narrator tag defaults to the voice's name. Without a voice, the Speed engine uses its built-in default voice.

## Using the app

The app is a Tauri window: a Rust backend, a runner thread that owns the loaded TTS engine, and a React frontend, all local. See "Desktop app" above for running it.

In the Library, click "Add a book" (or drop an EPUB or Markdown file on the window), choose the file and an output folder with the native dialogs, click "Add to shelf", then click "Generate all". Output is one `NN - Chapter Title.mp3` per chapter, tagged with ID3 album, track, title and artist. If a folder goes bad after import (permissions changed, drive unmounted), that chapter's audio is saved under the app data folder's `output/` instead, and the chapter list flags it. The chapter list polls every couple of seconds and shows each chapter's status (`pending` / `generating` / `done` / `failed`), in-progress percentage, and, once done, the MP3's path.

Settings shows your listening stats and lets you download or delete the engines (Quality engine, Speed engine, Voice transcription). The first launch opens it when nothing is downloaded. `make models` is a development shortcut for the same download.

The app also covers chapter-break editing, voice cloning (with tempo previews), the pronunciation list, text clips and a player.

## Development

```bash
make setup
.venv/bin/pytest                              # engine worker and model store (fast, no GPU/weights required)
.venv/bin/pytest -m integration               # real engine loads, requires make models first
cd desktop && cargo test -p audiobookify-core # app core
cd frontend && npm test                       # UI helpers
```

The Python side is only the engine worker (`src/audiobookify/engine_worker.py`), the model store, the engines and voice transcription. Everything else is Rust.

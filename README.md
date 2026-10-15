# Audiobookify

Fully local EPUB/Markdown → audiobook (MP3 per chapter) converter. Requires Apple Silicon (TTS runs on [MLX](https://github.com/ml-explore/mlx)) and Python 3.12 (`brew install python@3.12`). See `project-spec.md` for the full design and `docs/superpowers/plans/` for implementation plans.

## Setup

1. Install [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg` on macOS, or see your platform's docs).
2. Install [espeak-ng](https://github.com/espeak-ng/espeak-ng) (`brew install espeak-ng` on macOS, or see your platform's docs) — required by the M3 pronunciation-from-audio feature's `phonemizer` dependency (see note below).
3. `make setup` — creates a Python 3.12 venv and installs the Python package + dev dependencies.
4. `make models` — downloads and caches the Chatterbox Turbo TTS (MLX) and phoneme-recognition weights (about 6 GB, one-time; cached under your Hugging Face hub cache directory). Re-run any time to verify the cache.

**Note on `phonemizer` (GPL-3.0):** `lexicon add-from-audio` (see M3 below) loads `facebook/wav2vec2-lv-60-espeak-cv-ft` via Hugging Face `transformers`, whose phoneme tokenizer unconditionally requires the `phonemizer` package (and the `espeak-ng` system binary it wraps) just to construct the tokenizer, even though this project only ever calls `decode`, never `encode`. Every other dependency in this project is Apache-2.0/permissive; this is a deliberate, known exception, accepted for `facebook/wav2vec2-lv-60-espeak-cv-ft`'s zero-additional-*Python*-dependency footprint otherwise. If this project is ever distributed in a form where GPL-3.0 is a problem, revisit this before shipping.

## Desktop app

`make app-dev` opens Audiobookify in a native window (with hot reload). `make app` builds `desktop/target/release/bundle/macos/Audiobookify.app`. Both start the API and worker for you and stop them when you quit. The app runs this checkout's `.venv`, so build it after `make setup` and don't move the repo. Building needs the Rust toolchain (rustup) and tauri-cli: `cargo install tauri-cli --version "^2"`. The app won't start while `make up` or `make dev` is running (port 8000), so run `make down` first. The port to a full Rust backend is in progress; see `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md`.

## Usage

```bash
.venv/bin/audiobookify generate --input book.epub --out ./audiobook/
```

- `--input`: an `.epub` or `.md` file.
- `--out`: output directory; one `NN - Chapter Title.mp3` per chapter, tagged with ID3 album/track/title/artist.
- `--voice`: sets the ID3 narrator/artist tag on the exported MP3s only — no effect on cloning. Defaults to `"default"`, or to the voice profile's name if `--voice-profile` is used.
- `--db`: path to the SQLite job database (default `audiobookify.db` in the current directory). Re-running `generate` against the same `--input`/`--db` resumes: already-completed chapters are skipped. The voice used on a book's first `generate` call is locked in for that book — a later call naming a different (or no) `--voice-profile` prints a warning and keeps using the original voice, so chapters within one book never mix voices silently.

The GUI is not yet implemented — see `project-spec.md` §9 for the milestone roadmap.

## Engines

Two TTS engines, picked per book or text clip (GUI picker, or `--engine` on `generate`, `voice preview` and `lexicon preview`). The choice is stored on the book, so resumed and requeued chapters keep it.

- **Quality (`qwen3`)**: Qwen3-TTS 1.7B Base. Most natural; about 0.85-0.9 s to generate a second of audio; peaks around 11 GB. Needs a voice profile with a transcript of its reference clip, and copies the clip's speaking pace.
- **Speed (`chatterbox-turbo`, the CLI default)**: Chatterbox Turbo. About twice as fast, slightly less natural; peaks around 5 GB. Works without a voice (built-in default voice).

The worker keeps one engine loaded at a time and swaps when a job needs the other (a few seconds). Each chapter records which engine made it, and the book page warns when a book's chapters would mix engines.

## Pronunciation lexicon (M2)

Register a respelling for any term (a name, an invented word, jargon) so it's read correctly. Entries are global (apply to every book) by default, or scoped to one book with `--book`:

```bash
.venv/bin/audiobookify lexicon add --term "Cadeyrn" --respell "KAH-dern"
.venv/bin/audiobookify lexicon list
.venv/bin/audiobookify lexicon preview --term "Cadeyrn"
.venv/bin/audiobookify lexicon remove --term "Cadeyrn"
```

- `lexicon add`: substitution is whole-word, case-insensitive, and supports multi-word terms (`--term "New York"`); when two terms overlap (e.g. "York" and "New York"), the longer one wins regardless of which was added first. `--book <path>` scopes the entry to one already-registered book (run `generate` on it at least once first) instead of applying it everywhere; the same term can have both a global and a book-specific entry — the book-specific one wins for that book. Respellings should stick to plain letters/hyphens: the term is substituted before the normal number/abbreviation expansion runs, so a respelling containing digits or a period could be altered by that later step. A term's replacement text can itself be matched by a later entry (e.g. a separate entry for "dern" would also match inside a "Cadeyrn" → "KAH dern" replacement) — keep respellings free of other registered terms if that's not the intent.
- `lexicon list` / `lexicon remove --term NAME`: pass `--book <path>` to see or remove a book-scoped entry instead of a global one.
- `lexicon preview --term NAME`: synthesizes a short carrier sentence (or `--sentence "..."`) with the term's override applied, optionally in a saved `--voice-profile` and/or `--book <path>` (to preview a book-specific override, falling back to the global one if the book has none), and writes a WAV file (same convention as `voice preview` — no auto-playback).
- `generate` applies every applicable lexicon entry (global + that book's own) automatically — no flag needed. Lexicon changes only affect chapters that haven't been generated yet; a chapter already marked done keeps its existing audio. Pass a fresh `--db` (or delete the chapter's MP3 and reset its status) to re-render with an updated pronunciation.

Only respelling overrides are supported in M2 — IPA/ARPAbet phoneme overrides are deferred until an engine that can actually consume them is added (the installed Chatterbox Turbo engine cannot).

## Audio-sample pronunciation (M3)

Instead of typing a respelling by hand, attach a short clip of the term spoken correctly and let the app derive one:

```bash
.venv/bin/audiobookify lexicon add-from-audio --term "Cadeyrn" --audio ./cadeyrn.wav
```

This runs the clip through a local phoneme recognizer (`facebook/wav2vec2-lv-60-espeak-cv-ft`, fetched once via `make models`), derives an ASCII respelling from the extracted phonemes, and creates a lexicon entry with `source: from_audio` — printing both the raw phonemes and the derived respelling so you can judge it immediately. `--book <path>` scopes it to one book, same as `lexicon add`. `--notes "..."` attaches a free-text note, same as `lexicon add`. `--device` picks the compute device for the phoneme model (`auto` detects CUDA, then MPS, then CPU; or name one).

The derived respelling is a starting point, not guaranteed to be right — audit it:

```bash
.venv/bin/audiobookify lexicon preview --term "Cadeyrn"
.venv/bin/audiobookify lexicon edit --term "Cadeyrn" --respell "kah-DAIRN"
```

- `lexicon preview` (unchanged from M2) works on any entry regardless of source — hear how the current respelling sounds.
- `lexicon edit --term NAME --respell NEW_VALUE`: corrects just the respelling in place. Use this instead of `remove` + `add` — it preserves the entry's extracted phonemes, attached clip path, and `source: from_audio` provenance, all of which `remove`+`add` would silently discard. `--book <path>` scopes it to a book-specific entry.
- `lexicon list` now also shows each entry's `source` (`manual` or `from_audio`) and, when present, its raw extracted phonemes.

Audio-derived entries substitute into text exactly like manual ones — nothing about `generate` changes. The phoneme string is stored for reference/audit only; it is never sent to the TTS engine directly (Chatterbox Turbo still can't consume phoneme input, same constraint as M2).

## Voice cloning (M1)

Import a short reference clip (10-30s recommended, clean single-speaker audio) as a named voice profile, preview it, then generate a book with it:

```bash
.venv/bin/audiobookify voice add --name narrator-1 --reference ./narrator.wav
.venv/bin/audiobookify voice list
.venv/bin/audiobookify voice preview --name narrator-1 --text "This is a preview of the voice."
.venv/bin/audiobookify generate --input book.epub --out ./audiobook/ --voice-profile narrator-1
```

- `voice add`: validates the reference clip (sample rate, length, mono/stereo channel count) and prints warnings (not errors) for anything outside the recommended range, clipping, or a lack of quiet passages (a sign of constant background noise). Only an unusably short (<1s) or unreadable file is rejected outright. Reference audio is stored by absolute path and is not copied — don't move or delete the original file. Optional tuning flags `--exaggeration`, `--cfg` and `--temperature` are stored on the profile but unused: neither engine has exaggeration/cfg, and both run at their own sampling defaults. `--transcript` gives the clip's words yourself; without it the worker transcribes the clip with Whisper (`whisper-large-v3-turbo` on MLX), or the Quality engine does so the first time it needs it. Clips over 15 s are trimmed at a word (preferably sentence) boundary for cloning, because Qwen3 re-encodes the whole reference for every chunk and a 36 s clip ran about 15% slower than a 15 s one; the transcript then covers the kept part. Transcripts are shown and editable on the GUI's Voices page. The Voices page also shows each clip's measured pace (speaking wpm, pauses excluded, and overall wpm) and a tempo slider (0.75-1.25x, with a sweet spot at about 174 wpm overall). A saved tempo plays the reference through `ffmpeg atempo` (pitch kept) before cloning; the derived clip is cached next to the reference. Preview the slowed reference or generate a short Quality sample at the slider's tempo before saving: Qwen3 follows only part of a reference pace change.
- `voice list`: prints each saved profile's name, reference audio path, and tuning params (or a message if none exist yet).
- `voice preview`: synthesizes `--text` with the named profile (`--engine` picks the engine) and writes a WAV file (`--out`, defaults to `<name>-preview.wav` in the current directory) — play it in any audio player to judge the voice before generating a whole book.
- `generate --voice-profile NAME`: uses that profile's reference audio and tuning params for the whole book. The ID3 narrator tag defaults to the profile name (override with `--voice`). Without `--voice-profile`, `generate` behaves exactly as in M0 (Chatterbox Turbo's built-in default voice, no cloning).

## GUI (M4)

The GUI wraps the same core library behind a local web UI: a FastAPI backend, a
separate worker process that owns the loaded TTS engine, and a React frontend — all
running on `localhost`, still fully local.

```bash
make dev    # backend (autoreload) + worker + frontend dev server, hot reload
```

Open `http://127.0.0.1:5173`, upload an EPUB or Markdown file, browse to (or type) an
output folder, click Import, then click "Generate all". The folder picker checks
writability live and won't let you import into a folder it can't write to; if a folder
somehow goes bad *after* import (permissions changed, drive unmounted), that chapter's
audio is saved under this repo's `output/` folder instead, and the chapter list flags it.
The chapter list polls the backend every couple of seconds and shows each chapter's status (`pending` / `generating` / `done` /
`failed`), in-progress percentage, and — once done — the resulting MP3's path.

For a non-hot-reload run (built frontend served by FastAPI itself):

```bash
make up     # builds the frontend, then runs backend + worker
make down   # stops everything make dev/up started
```

The API and worker communicate purely through the same SQLite database `generate`/
`voice`/`lexicon` already use — there's no separate message broker. You can run
`audiobookify worker --db audiobookify.db` and the API server independently if you want
more control than the Makefile targets give you.

This first GUI pass is deliberately bare: one page, upload → generate → watch chapters
finish. Chapter review/editing, voice profile management, the pronunciation lexicon, and
a real generation dashboard are still CLI-only (`voice`, `lexicon` subcommands) until
later GUI sub-projects land.

## Development

```bash
make setup
.venv/bin/pytest          # unit tests (fast, no GPU/weights required)
.venv/bin/pytest -m integration  # real Turbo + wav2vec2 load and run, requires `make models` first
```

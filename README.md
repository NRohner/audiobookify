# Audiobookify

Fully local EPUB/Markdown → audiobook (MP3 per chapter) converter. See `project-spec.md` for the full design and `docs/superpowers/plans/` for implementation plans.

## Setup

1. Install [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg` on macOS, or see your platform's docs).
2. Install [espeak-ng](https://github.com/espeak-ng/espeak-ng) (`brew install espeak-ng` on macOS, or see your platform's docs) — required by the M3 pronunciation-from-audio feature's `phonemizer` dependency (see note below).
3. `make setup` — creates a venv and installs the Python package + dev dependencies.
4. `make models` — downloads and caches the Chatterbox TTS and phoneme-recognition weights (several GB, one-time; cached under your Hugging Face hub cache directory). Re-run any time to verify the cache.

**Note on `phonemizer` (GPL-3.0):** `lexicon add-from-audio` (see M3 below) loads `facebook/wav2vec2-lv-60-espeak-cv-ft` via Hugging Face `transformers`, whose phoneme tokenizer unconditionally requires the `phonemizer` package (and the `espeak-ng` system binary it wraps) just to construct the tokenizer, even though this project only ever calls `decode`, never `encode`. Every other dependency in this project is Apache-2.0/permissive; this is a deliberate, known exception, accepted for `facebook/wav2vec2-lv-60-espeak-cv-ft`'s zero-additional-*Python*-dependency footprint otherwise. If this project is ever distributed in a form where GPL-3.0 is a problem, revisit this before shipping.

## Usage

```bash
.venv/bin/audiobookify generate --input book.epub --out ./audiobook/
```

- `--input`: an `.epub` or `.md` file.
- `--out`: output directory; one `NN - Chapter Title.mp3` per chapter, tagged with ID3 album/track/title/artist.
- `--voice`: sets the ID3 narrator/artist tag on the exported MP3s only — no effect on cloning. Defaults to `"default"`, or to the voice profile's name if `--voice-profile` is used.
- `--device`: `auto` (default), `cuda`, `mps`, or `cpu`. Auto-detects CUDA → MPS → CPU; falls back to CPU if Chatterbox fails to load on the detected accelerator (this is expected on some Apple Silicon MPS builds — see `project-spec.md` §2).
- `--db`: path to the SQLite job database (default `audiobookify.db` in the current directory). Re-running `generate` against the same `--input`/`--db` resumes: already-completed chapters are skipped. The voice used on a book's first `generate` call is locked in for that book — a later call naming a different (or no) `--voice-profile` prints a warning and keeps using the original voice, so chapters within one book never mix voices silently.

The GUI is not yet implemented — see `project-spec.md` §9 for the milestone roadmap.

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

Only respelling overrides are supported in M2 — IPA/ARPAbet phoneme overrides are deferred until an engine that can actually consume them is added (the installed Chatterbox engine cannot).

## Audio-sample pronunciation (M3)

Instead of typing a respelling by hand, attach a short clip of the term spoken correctly and let the app derive one:

```bash
.venv/bin/audiobookify lexicon add-from-audio --term "Cadeyrn" --audio ./cadeyrn.wav
```

This runs the clip through a local phoneme recognizer (`facebook/wav2vec2-lv-60-espeak-cv-ft`, fetched once via `make models`), derives an ASCII respelling from the extracted phonemes, and creates a lexicon entry with `source: from_audio` — printing both the raw phonemes and the derived respelling so you can judge it immediately. `--book <path>` scopes it to one book, same as `lexicon add`. `--notes "..."` attaches a free-text note, same as `lexicon add`. `--device` picks the compute device for the phoneme model (`auto`/`cpu`/`cuda`/`mps`), same convention as `generate --device`.

The derived respelling is a starting point, not guaranteed to be right — audit it:

```bash
.venv/bin/audiobookify lexicon preview --term "Cadeyrn"
.venv/bin/audiobookify lexicon edit --term "Cadeyrn" --respell "kah-DAIRN"
```

- `lexicon preview` (unchanged from M2) works on any entry regardless of source — hear how the current respelling sounds.
- `lexicon edit --term NAME --respell NEW_VALUE`: corrects just the respelling in place. Use this instead of `remove` + `add` — it preserves the entry's extracted phonemes, attached clip path, and `source: from_audio` provenance, all of which `remove`+`add` would silently discard. `--book <path>` scopes it to a book-specific entry.
- `lexicon list` now also shows each entry's `source` (`manual` or `from_audio`) and, when present, its raw extracted phonemes.

Audio-derived entries substitute into text exactly like manual ones — nothing about `generate` changes. The phoneme string is stored for reference/audit only; it is never sent to the TTS engine directly (Chatterbox still can't consume phoneme input, same constraint as M2).

## Voice cloning (M1)

Import a short reference clip (10-30s recommended, clean single-speaker audio) as a named voice profile, preview it, then generate a book with it:

```bash
.venv/bin/audiobookify voice add --name narrator-1 --reference ./narrator.wav
.venv/bin/audiobookify voice list
.venv/bin/audiobookify voice preview --name narrator-1 --text "This is a preview of the voice."
.venv/bin/audiobookify generate --input book.epub --out ./audiobook/ --voice-profile narrator-1
```

- `voice add`: validates the reference clip (sample rate, length, mono/stereo channel count) and prints warnings (not errors) for anything outside the recommended range, clipping, or a lack of quiet passages (a sign of constant background noise). Only an unusably short (<1s) or unreadable file is rejected outright. Reference audio is stored by absolute path and is not copied — don't move or delete the original file. Optional tuning flags: `--exaggeration` (default 0.5), `--cfg` (default 0.5), `--temperature` (default 0.8) — Chatterbox's own defaults. `--transcript` is stored as optional metadata only; the installed Chatterbox engine's cloning is zero-shot on audio alone and doesn't use it.
- `voice list`: prints each saved profile's name, reference audio path, and tuning params (or a message if none exist yet).
- `voice preview`: synthesizes `--text` with the named profile and writes a WAV file (`--out`, defaults to `<name>-preview.wav` in the current directory) — play it in any audio player to judge the voice before generating a whole book.
- `generate --voice-profile NAME`: uses that profile's reference audio and tuning params for the whole book. The ID3 narrator tag defaults to the profile name (override with `--voice`). Without `--voice-profile`, `generate` behaves exactly as in M0 (Chatterbox's built-in default voice, no cloning).

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
.venv/bin/pytest -m integration  # real Chatterbox load + synth, requires `make models` first
```

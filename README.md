# Audiobookify

Fully local EPUB/Markdown → audiobook (MP3 per chapter) converter. See `project-spec.md` for the full design and `docs/superpowers/plans/` for implementation plans.

## Setup

1. Install [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg` on macOS, or see your platform's docs).
2. `make setup` — creates a venv and installs the Python package + dev dependencies.
3. `make models` — downloads and caches the Chatterbox TTS weights (several GB, one-time; cached under your Hugging Face hub cache directory). Re-run any time to verify the cache.

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

- `lexicon add`: substitution is whole-word, case-insensitive, and supports multi-word terms (`--term "New York"`). `--book <path>` scopes the entry to one already-registered book (run `generate` on it at least once first) instead of applying it everywhere; the same term can have both a global and a book-specific entry — the book-specific one wins for that book. Respellings should stick to plain letters/hyphens: the term is substituted before the normal number/abbreviation expansion runs, so a respelling containing digits or a period could be altered by that later step.
- `lexicon list` / `lexicon remove --term NAME`: pass `--book <path>` to see or remove a book-scoped entry instead of a global one.
- `lexicon preview --term NAME`: synthesizes a short carrier sentence (or `--sentence "..."`) with the term's override applied, optionally in a saved `--voice-profile`, and writes a WAV file (same convention as `voice preview` — no auto-playback).
- `generate` applies every applicable lexicon entry (global + that book's own) automatically — no flag needed.

Only respelling overrides are supported in M2 — IPA/ARPAbet phoneme overrides are deferred until an engine that can actually consume them is added (the installed Chatterbox engine cannot).

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

## Development

```bash
make setup
.venv/bin/pytest          # unit tests (fast, no GPU/weights required)
.venv/bin/pytest -m integration  # real Chatterbox load + synth, requires `make models` first
```

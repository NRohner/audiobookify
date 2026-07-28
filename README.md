# Audiobookify

Fully local EPUB/Markdown → audiobook (MP3 per chapter) converter. See `project-spec.md` for the full design and `docs/superpowers/plans/` for implementation plans.

## Setup

1. Install [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg` on macOS, or see your platform's docs).
2. `make setup` — creates a venv and installs the Python package + dev dependencies.
3. `make models` — downloads and caches the Chatterbox TTS weights (several GB, one-time; cached under your Hugging Face hub cache directory). Re-run any time to verify the cache.

## Usage (M0: CLI only)

```bash
.venv/bin/audiobookify generate --input book.epub --out ./audiobook/
```

- `--input`: an `.epub` or `.md` file.
- `--out`: output directory; one `NN - Chapter Title.mp3` per chapter, tagged with ID3 album/track/title/artist.
- `--device`: `auto` (default), `cuda`, `mps`, or `cpu`. Auto-detects CUDA → MPS → CPU; falls back to CPU if Chatterbox fails to load on the detected accelerator (this is expected on some Apple Silicon MPS builds — see `project-spec.md` §2).
- `--db`: path to the SQLite job database (default `audiobookify.db` in the current directory). Re-running `generate` against the same `--input`/`--db` resumes: already-completed chapters are skipped.

Voice cloning (`--voice`), the pronunciation lexicon, and the GUI are not yet implemented — see `project-spec.md` §9 for the milestone roadmap.

## Development

```bash
make setup
.venv/bin/pytest          # unit tests (fast, no GPU/weights required)
.venv/bin/pytest -m integration  # real Chatterbox load + synth, requires `make models` first
```

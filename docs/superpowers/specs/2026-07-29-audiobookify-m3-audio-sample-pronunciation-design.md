# Audiobookify M3 — Audio-Sample Pronunciation: Design

**Status:** Approved by user (2026-07-29), ready for implementation planning.

## Goal

Per `project-spec.md` §5.2 and §9 (M3): let the user attach a short audio clip of a
problem word spoken correctly, extract its pronunciation locally, auto-populate a
`LexiconEntry` from it, and give the user an audit/edit/preview loop to correct and
confirm the derived pronunciation before it's used in generation.

This is the audio-derived half of the pronunciation subsystem. M2 already built the
text-substitution mechanism (`apply_lexicon`) and manual respelling entries; M3 adds a
second *source* of entries (`from_audio`) without changing how entries are consumed.

## Background

- M2 (merged) added `LexiconEntry` (SQLite `lexicon_entries` table), pure substitution
  module `lexicon/substitution.py` (`apply_lexicon`), and CLI `lexicon add/list/remove/preview`.
  Respelling-only: no `override_type` column exists, because `ChatterboxEngine.preferred_phoneme_scheme()`
  returns `None` — Chatterbox cannot consume inline IPA/ARPAbet, so a phoneme-scheme column would be inert.
  That constraint has not changed for M3.
- The engine-adapter pattern (`engines/base.py` `TTSEngine` Protocol + `FakeEngine` +
  `ChatterboxEngine`) is the established convention for wrapping a heavy/ML dependency
  behind a small interface with a no-download fake for tests. M3 follows the same shape
  for phoneme extraction.
- `voice/validation.py`'s `validate_reference_audio` is tuned for **voice-cloning**
  reference clips (`ABSOLUTE_MIN_SECONDS = 1.0`, recommended window 10–30s). A
  single-word pronunciation sample is expected to be shorter than that (well under a
  second is plausible) and would trip `validate_reference_audio`'s "too short to clone
  from" error incorrectly. M3 does **not** reuse it for pronunciation clips.

## Decision: phoneme recognition model

**Chosen: `facebook/wav2vec2-lv-60-espeak-cv-ft`** (a wav2vec2 CTC phoneme-recognition
checkpoint), confirmed with the user after comparing it against Allosaurus:

| | wav2vec2 CTC (chosen) | Allosaurus (rejected) |
|---|---|---|
| License | Apache-2.0 | GPL-3.0 — copyleft, conflicts with the project's permissive-license preference for distribution (`project-spec.md` §10) |
| New pip dependencies | **None** — `torch` and `transformers` are already installed (pulled in by `chatterbox-tts`) | 3 new deps: `panphon`, `resampy`, `editdistance` |
| Weights | ~1.2GB `pytorch_model.bin`, fetched via `make models` (same pattern as Chatterbox/Whisper weights) | Own pretrained checkpoint, smaller |
| Output | espeak-style IPA phoneme string | Custom phone inventory (allophone system) |

No new pip dependency continues the pattern set by M0/M1/M2, which added zero.

## Architecture

### New package: `src/audiobookify/phoneme/`

Mirrors the existing `engines/` package shape:

- **`base.py`** — `PhonemeExtractor` Protocol:
  ```
  load(device: str) -> None
  extract(audio_path: str) -> str        # space-separated phoneme tokens
  teardown() -> None
  ```
- **`wav2vec2.py`** — `Wav2Vec2PhonemeExtractor`: loads `Wav2Vec2ForCTC` +
  `Wav2Vec2Processor` from `facebook/wav2vec2-lv-60-espeak-cv-ft` via `transformers`,
  reads the clip with `soundfile`, resamples to the model's required 16kHz with
  `torchaudio.functional.resample` when the clip's native rate differs (`torchaudio` is
  already installed — it's a transitive dependency of `chatterbox-tts` — so this adds no
  new dependency either), runs CTC inference, decodes to a phoneme string via the
  processor's tokenizer.
- **`fake.py`** — `FakePhonemeExtractor`: returns a deterministic canned phoneme string
  (e.g. derived from the term, not the audio) with no model load and no download —
  same role `FakeEngine` plays for `ChatterboxEngine`. All non-integration tests use this.
- **`respelling.py`** — pure function `phonemes_to_respelling(phonemes: str) -> str`: a
  static espeak-IPA-symbol → ASCII-chunk dict, looked up per whitespace-separated token
  and joined. No ML, fully deterministic. This is intentionally simple — the user always
  reviews/edits the result before it's used (see CLI flow below), so the bar is "a
  reasonable starting point," not "phonetically perfect."

### Data model: additive schema change

In `Database.__init__`, after the existing `executescript(SCHEMA)` call, add a guarded
migration (checks `PRAGMA table_info(lexicon_entries)` so it's a no-op on a fresh DB and
safe to run against an existing M0–M2 database file):

```sql
ALTER TABLE lexicon_entries ADD COLUMN phonemes TEXT;           -- nullable
ALTER TABLE lexicon_entries ADD COLUMN sample_audio_path TEXT;  -- nullable
```

`LexiconEntry` dataclass gains `phonemes: Optional[str] = None` and
`sample_audio_path: Optional[str] = None`.

**No `override_type` column** — M2's reasoning still holds: the engine still can't
consume phoneme input, so `override_value` remains "always a respelling" and is the only
thing `apply_lexicon` ever reads. `phonemes`/`sample_audio_path` are audit/reference
metadata only. `source='from_audio'` requires no schema change — `source` is already a
free-text column (M2 only ever wrote `'manual'` into it).

### CLI additions

- **`lexicon add-from-audio --term TERM --audio PATH [--book BOOK] [--notes NOTES] [--db DB]`**
  - Lightly validates the clip is readable audio (via `soundfile`, non-empty) — does
    *not* call `validate_reference_audio` (wrong duration assumptions, see Background).
  - Runs `PhonemeExtractor.extract()` → `phonemes_to_respelling()` → creates the
    `LexiconEntry` with `source="from_audio"`, `phonemes` and `sample_audio_path`
    populated, `override_value` set to the derived respelling.
  - Prints **both** the raw phoneme string and the derived respelling to stdout, so the
    audit step (spec §5.2 step 3) has something concrete to look at immediately, e.g.:
    ```
    Extracted phonemes: k ə d eɪ ɹ n
    Derived respelling: kuh-DAY-run
    Added pronunciation for 'Cadeyrn' -> 'kuh-DAY-run' (global scope, source: from_audio)
    ```
  - Same scope rules as `lexicon add` (`--book` scopes it; book must already exist via
    `find_book_by_source`, same lookup `generate` uses).
- **`lexicon edit --term TERM --respell NEW_VALUE [--book BOOK] [--db DB]`**
  - New command. Updates only `override_value` in place; leaves `phonemes`,
    `sample_audio_path`, `source`, `notes` untouched. This closes the actual "audit/edit"
    gap: today there is no way to correct a derived respelling without delete+re-add,
    which would silently discard the `from_audio` provenance (source, phonemes, clip
    path) and force re-specifying scope/notes. Works equally for manual entries (editing
    a plain respelling is a reasonable thing to want too).
- **`lexicon preview`** — unchanged. Already takes `--term` and works against any
  `LexiconEntry` regardless of `source`, so a `from_audio` entry previews exactly like a
  manual one.
- **`lexicon list`** — no signature change; each entry line gains the extra metadata
  fields to display (e.g. show `source` and, if present, `phonemes`) so `from_audio`
  entries are visually distinguishable from manual ones in the listing.

### Pipeline

No changes to `synth/pipeline.py`. Substitution is driven entirely by `override_value`
via `apply_lexicon`, so `from_audio` entries flow through exactly like manual ones —
this is the same reason M2's substitution logic needed no changes to support book vs.
global scope.

### Testing strategy

- `FakePhonemeExtractor` (no download) backs all extraction-dependent tests, mirroring
  `FakeEngine`.
- One `@pytest.mark.integration` test exercises the real `Wav2Vec2PhonemeExtractor`
  against a real short recorded clip — deselected by default (`addopts = "-m 'not
  integration'"` in `pyproject.toml` already does this), consistent with the existing
  Chatterbox integration test. `make models` should also fetch the wav2vec2 weights.
- `phonemes_to_respelling` gets direct unit tests against known espeak phoneme strings
  (no audio/model involved — it's a pure string function).
- CLI tests for `lexicon add-from-audio` and `lexicon edit` follow the existing
  `test_cli.py` conventions (`monkeypatch.setattr(cli, "PhonemeExtractor-equivalent",
  FakePhonemeExtractor)`, tmp_path DB, capsys for output assertions).

## Out of scope for M3

- Concatenative splice fallback (§5.3) — M5, unchanged.
- Problem-word auto-scan (§5.4 priority 3 in spec numbering, M5 per §9) — unchanged.
- IPA/ARPAbet as a *substitution* mechanism (`override_type`) — still deferred until an
  engine that can consume it exists; `phonemes` in M3 is metadata/audit only, never fed
  to the TTS engine directly.
- GUI audit flow (M4) — this milestone is CLI-only, extending the M2 CLI surface.

## Open questions

None outstanding — model choice was the one open decision requiring user input, and
it's been made (see Decision section above).

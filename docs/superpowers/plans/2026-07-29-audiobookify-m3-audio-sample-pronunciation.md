# Audiobookify M3 — Audio-Sample Pronunciation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user attach a short audio clip of a problem word spoken correctly, extract its pronunciation locally with a wav2vec2 CTC phoneme model, auto-populate a `LexiconEntry` (respelling + raw phonemes + clip path, `source="from_audio"`), and give the user a `lexicon edit` command plus the existing `lexicon preview` to audit/correct/confirm it before it's used in generation.

**Architecture:** A new `src/audiobookify/phoneme/` package mirrors the existing `engines/` package: a `PhonemeExtractor` Protocol, a `FakePhonemeExtractor` test double, and a real `Wav2Vec2PhonemeExtractor` wrapping `facebook/wav2vec2-lv-60-espeak-cv-ft` via `transformers`. A pure `phoneme/respelling.py` module maps the extracted espeak-IPA phoneme string to an ASCII respelling. `LexiconEntry` gains two nullable columns (`phonemes`, `sample_audio_path`) via an additive, idempotent `ALTER TABLE` migration — no `override_type` column, since `ChatterboxEngine.preferred_phoneme_scheme()` still returns `None` and `override_value` remains the only thing `apply_lexicon` ever substitutes. The CLI gains `lexicon add-from-audio` (extract → derive → create entry → print both for audit) and `lexicon edit` (correct the respelling in place, keeping phoneme/audio provenance). `synth/pipeline.py` and `lexicon/substitution.py` need no changes.

**Tech Stack:** `transformers`/`torch`/`torchaudio` (all already installed — no new pip dependency), `soundfile` (already installed), `pytest`.

**Post-implementation addendum (found only once the real model was actually loaded — see Task 8's addendum below and the whole-branch review's finding #4): `phonemizer>=3.2` (GPL-3.0) had to be added as a dependency, plus the `espeak-ng` system binary, because `Wav2Vec2Processor.from_pretrained("facebook/wav2vec2-lv-60-espeak-cv-ft")` unconditionally constructs a `Wav2Vec2PhonemeCTCTokenizer`, whose `__init__` calls `init_backend()` requiring `phonemizer` even though this project only ever calls `decode`. This was surfaced to the user, who chose to accept the GPL-3.0 dependency rather than rework the extractor to bypass the phoneme tokenizer or switch models. See `README.md`'s "Note on `phonemizer`" for the user-facing record of this decision.**

**Reference:** `docs/superpowers/specs/2026-07-29-audiobookify-m3-audio-sample-pronunciation-design.md` (approved design spec — read it for the full rationale behind the decisions below).

## Global Constraints

- **Zero new pip dependencies.** `torch`, `torchaudio`, and `transformers` are already installed (pulled in by `chatterbox-tts==0.1.7`); do not add them to `pyproject.toml`. Do not add Allosaurus or any other package.
- **Model:** `facebook/wav2vec2-lv-60-espeak-cv-ft` (Apache-2.0), fetched via `make models`, same caching pattern as Chatterbox.
- **No `override_type` column.** `override_value` on `LexiconEntry` is always a respelling (M2's constraint, unchanged); `phonemes` is audit/reference metadata only and is never read by `apply_lexicon` or the synthesis pipeline.
- **Schema changes must be additive and idempotent.** New columns (`phonemes TEXT`, `sample_audio_path TEXT`, both nullable) are added via a guarded `ALTER TABLE` (checked against `PRAGMA table_info`) so it's a no-op on a fresh DB and safe to run against an existing M0–M2 database file.
- **Do not reuse `voice/validation.py`'s `validate_reference_audio`** for pronunciation clips — its thresholds (`ABSOLUTE_MIN_SECONDS = 1.0`, recommended 10–30s window) are tuned for voice-cloning reference clips and would incorrectly reject legitimate sub-second single-word samples. Pronunciation clips get a lighter check: readable via `soundfile`, non-empty.
- **CLI conventions (established in M0–M2, keep following them):** resolve user-supplied paths to absolute before storing; errors to stderr prefixed `"Error: "` + `sys.exit(1)`; warnings to stderr prefixed `"Warning: "`; engine/extractor classes stay importable as `cli.ClassName` for test monkeypatching (e.g. `cli.Wav2Vec2PhonemeExtractor`, matching the existing `cli.ChatterboxEngine` pattern); `--term`/`--respell`-style string args are trimmed and validated non-empty in the CLI layer.
- **Testing:** every extraction-dependent test uses `FakePhonemeExtractor` (no download, no model load) — mirrors `FakeEngine`. Exactly one `@pytest.mark.integration` test exercises the real `Wav2Vec2PhonemeExtractor` (deselected by default via the existing `addopts = "-m 'not integration'"` in `pyproject.toml`).
- **Respelling generation is intentionally simple** — a static IPA-symbol → ASCII-chunk lookup table, concatenated with no separators or stress detection. The user always reviews/corrects it via `lexicon edit` before use, so "reasonable starting point" is the bar, not phonetic precision. Do not add syllabification, stress marking, or any ML step here.

---

## File Structure

```
audiobookify/
  src/audiobookify/
    models.py                     # + LexiconEntry.phonemes, .sample_audio_path
    db.py                         # + migration, extended create_lexicon_entry, new update_lexicon_entry_override
    phoneme/
      __init__.py                 # new
      base.py                     # new: PhonemeExtractor Protocol
      fake.py                     # new: FakePhonemeExtractor
      wav2vec2.py                 # new: Wav2Vec2PhonemeExtractor
      respelling.py                # new: phonemes_to_respelling()
    cli.py                        # + lexicon add-from-audio, lexicon edit, lexicon list display update
  Makefile                        # + phoneme model weight caching in `models` target
  tests/
    test_db.py                    # + lexicon phonemes/sample_audio_path + migration + update tests
    test_respelling.py            # new
    test_phoneme_wav2vec2.py      # new
    test_cli.py                   # + lexicon add-from-audio, lexicon edit, lexicon list display tests
  README.md                       # + M3 usage docs
```

---

### Task 1: `LexiconEntry` audio-derived metadata (schema + model + CRUD)

**Files:**
- Modify: `src/audiobookify/models.py`
- Modify: `src/audiobookify/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: existing `Database` class, `SCHEMA` string, `_row_to_lexicon_entry` (`src/audiobookify/db.py`).
- Produces: `LexiconEntry.phonemes: Optional[str]`, `LexiconEntry.sample_audio_path: Optional[str]` (`audiobookify.models`); `Database.create_lexicon_entry(..., phonemes: Optional[str] = None, sample_audio_path: Optional[str] = None)` (extended signature, backward compatible); `Database.update_lexicon_entry_override(term: str, override_value: str, book_id: int = 0) -> LexiconEntry`. Tasks 5 and 6 (CLI) call these directly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_create_lexicon_entry_stores_phonemes_and_sample_audio_path(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    entry = db.create_lexicon_entry(
        term="Cadeyrn", override_value="kuhdayrn", scope="global",
        source="from_audio", phonemes="k ə d eɪ ɹ n", sample_audio_path="/clips/cadeyrn.wav",
    )

    assert entry.source == "from_audio"
    assert entry.phonemes == "k ə d eɪ ɹ n"
    assert entry.sample_audio_path == "/clips/cadeyrn.wav"

    found = db.find_lexicon_entry("Cadeyrn")
    assert found.phonemes == "k ə d eɪ ɹ n"
    assert found.sample_audio_path == "/clips/cadeyrn.wav"
    db.close()


def test_create_lexicon_entry_defaults_phonemes_and_sample_audio_path_to_none(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    entry = db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")

    assert entry.phonemes is None
    assert entry.sample_audio_path is None
    db.close()


def test_existing_m2_database_gains_new_lexicon_columns_via_migration(tmp_path):
    # Simulates opening a database file created before M3 (no phonemes/sample_audio_path
    # columns) - the migration in Database.__init__ must add them without raising.
    import sqlite3

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE lexicon_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL COLLATE NOCASE,
            override_value TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            scope TEXT NOT NULL,
            book_id INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at REAL NOT NULL,
            UNIQUE(book_id, term)
        )
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)  # must not raise
    entry = db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")
    assert entry.phonemes is None
    db.close()


def test_update_lexicon_entry_override_changes_value_and_keeps_metadata(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_lexicon_entry(
        term="Cadeyrn", override_value="kuhdayrn", scope="global",
        source="from_audio", phonemes="k ə d eɪ ɹ n", sample_audio_path="/clips/cadeyrn.wav",
        notes="derived from audio",
    )

    updated = db.update_lexicon_entry_override("Cadeyrn", "kah-DAIRN")

    assert updated.override_value == "kah-DAIRN"
    assert updated.source == "from_audio"
    assert updated.phonemes == "k ə d eɪ ɹ n"
    assert updated.sample_audio_path == "/clips/cadeyrn.wav"
    assert updated.notes == "derived from audio"
    db.close()


def test_update_lexicon_entry_override_raises_keyerror_for_missing_entry(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    with pytest.raises(KeyError, match="Cadeyrn"):
        db.update_lexicon_entry_override("Cadeyrn", "kah-DAIRN")
    db.close()


def test_update_lexicon_entry_override_respects_book_scope(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")
    db.create_lexicon_entry(term="Cadeyrn", override_value="kah-DAIRN", scope="book", book_id=book.id)

    db.update_lexicon_entry_override("Cadeyrn", "NEW-book-version", book_id=book.id)

    assert db.find_lexicon_entry("Cadeyrn", book_id=book.id).override_value == "NEW-book-version"
    assert db.find_lexicon_entry("Cadeyrn").override_value == "KAH-dern"  # global untouched
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v -k "phonemes or migration or update_lexicon"`
Expected: FAIL — `TypeError: create_lexicon_entry() got an unexpected keyword argument 'phonemes'` (and `AttributeError: 'Database' object has no attribute 'update_lexicon_entry_override'` for the update tests)

- [ ] **Step 3: Add the new fields to `LexiconEntry` in `src/audiobookify/models.py`**

Change:

```python
@dataclass
class LexiconEntry:
    id: int
    term: str
    override_value: str
    source: str
    scope: str
    book_id: int
    notes: Optional[str]
    created_at: float
```

to:

```python
@dataclass
class LexiconEntry:
    id: int
    term: str
    override_value: str
    source: str
    scope: str
    book_id: int
    notes: Optional[str]
    created_at: float
    phonemes: Optional[str] = None
    sample_audio_path: Optional[str] = None
```

- [ ] **Step 4: Update `src/audiobookify/db.py`**

Change the `lexicon_entries` table definition in `SCHEMA` (adds the two new columns for brand-new databases):

```sql
CREATE TABLE IF NOT EXISTS lexicon_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL COLLATE NOCASE,
    override_value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    scope TEXT NOT NULL,
    book_id INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at REAL NOT NULL,
    phonemes TEXT,
    sample_audio_path TEXT,
    UNIQUE(book_id, term)
);
```

Add a migration function right after the `SCHEMA` string (handles databases created before this column existed):

```python
def _migrate_lexicon_entries_columns(conn: sqlite3.Connection) -> None:
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(lexicon_entries)")}
    if "phonemes" not in existing_cols:
        conn.execute("ALTER TABLE lexicon_entries ADD COLUMN phonemes TEXT")
    if "sample_audio_path" not in existing_cols:
        conn.execute("ALTER TABLE lexicon_entries ADD COLUMN sample_audio_path TEXT")
```

Change `Database.__init__` from:

```python
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
```

to:

```python
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        _migrate_lexicon_entries_columns(self.conn)
        self.conn.commit()
```

Change `_row_to_lexicon_entry` from:

```python
def _row_to_lexicon_entry(row: sqlite3.Row) -> LexiconEntry:
    return LexiconEntry(
        id=row["id"], term=row["term"], override_value=row["override_value"],
        source=row["source"], scope=row["scope"], book_id=row["book_id"],
        notes=row["notes"], created_at=row["created_at"],
    )
```

to:

```python
def _row_to_lexicon_entry(row: sqlite3.Row) -> LexiconEntry:
    return LexiconEntry(
        id=row["id"], term=row["term"], override_value=row["override_value"],
        source=row["source"], scope=row["scope"], book_id=row["book_id"],
        notes=row["notes"], created_at=row["created_at"],
        phonemes=row["phonemes"], sample_audio_path=row["sample_audio_path"],
    )
```

Change `create_lexicon_entry` from:

```python
    def create_lexicon_entry(self, term: str, override_value: str, scope: str,
                              book_id: int = 0, notes: Optional[str] = None,
                              source: str = "manual") -> LexiconEntry:
        try:
            self.conn.execute(
                "INSERT INTO lexicon_entries (term, override_value, source, scope, book_id, notes, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (term, override_value, source, scope, book_id, notes, time.time()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"a lexicon entry for '{term}' already exists in this scope")
        self.conn.commit()
        return self.find_lexicon_entry(term, book_id=book_id)
```

to:

```python
    def create_lexicon_entry(self, term: str, override_value: str, scope: str,
                              book_id: int = 0, notes: Optional[str] = None,
                              source: str = "manual", phonemes: Optional[str] = None,
                              sample_audio_path: Optional[str] = None) -> LexiconEntry:
        try:
            self.conn.execute(
                "INSERT INTO lexicon_entries (term, override_value, source, scope, book_id, notes, "
                "created_at, phonemes, sample_audio_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (term, override_value, source, scope, book_id, notes, time.time(),
                 phonemes, sample_audio_path),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"a lexicon entry for '{term}' already exists in this scope")
        self.conn.commit()
        return self.find_lexicon_entry(term, book_id=book_id)
```

Add a new method, next to `delete_lexicon_entry`:

```python
    def update_lexicon_entry_override(self, term: str, override_value: str,
                                       book_id: int = 0) -> LexiconEntry:
        cur = self.conn.execute(
            "UPDATE lexicon_entries SET override_value = ? WHERE term = ? AND book_id = ?",
            (override_value, term, book_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"no lexicon entry for '{term}' in this scope")
        self.conn.commit()
        return self.find_lexicon_entry(term, book_id=book_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: all pass (18 existing + 6 new = 24)

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/models.py src/audiobookify/db.py tests/test_db.py
git commit -m "feat: LexiconEntry phonemes/sample_audio_path metadata and update_lexicon_entry_override"
```

---

### Task 2: `PhonemeExtractor` interface + fake double

**Files:**
- Create: `src/audiobookify/phoneme/__init__.py`
- Create: `src/audiobookify/phoneme/base.py`
- Create: `src/audiobookify/phoneme/fake.py`
- Test: `tests/test_phoneme_fake.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `PhonemeExtractor` Protocol (`load(device: str) -> None`, `extract(audio_path: str) -> str`, `teardown() -> None`) in `audiobookify.phoneme.base`; `FakePhonemeExtractor` in `audiobookify.phoneme.fake`, constructor `FakePhonemeExtractor(canned_phonemes: str = "k ə d eɪ ɹ n")`. Task 5 (CLI) and Task 3 (respelling tests) reference `"k ə d eɪ ɹ n"` as the standard test fixture phoneme string — keep this exact string so later tasks' expected outputs match.

- [ ] **Step 1: Write the failing test**

Create `tests/test_phoneme_fake.py`:

```python
from audiobookify.phoneme.fake import FakePhonemeExtractor


def test_fake_phoneme_extractor_full_lifecycle():
    extractor = FakePhonemeExtractor()
    extractor.load("cpu")

    assert extractor.extract("/any/path.wav") == "k ə d eɪ ɹ n"

    extractor.teardown()  # must not raise


def test_fake_phoneme_extractor_accepts_custom_canned_phonemes():
    extractor = FakePhonemeExtractor(canned_phonemes="h ə l oʊ")
    extractor.load("cpu")

    assert extractor.extract("/any/path.wav") == "h ə l oʊ"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_phoneme_fake.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.phoneme'`

- [ ] **Step 3: Write `src/audiobookify/phoneme/__init__.py`**

```python
```

- [ ] **Step 4: Write `src/audiobookify/phoneme/base.py`**

```python
from typing import Protocol


class PhonemeExtractor(Protocol):
    def load(self, device: str) -> None: ...
    def extract(self, audio_path: str) -> str: ...
    def teardown(self) -> None: ...
```

- [ ] **Step 5: Write `src/audiobookify/phoneme/fake.py`**

```python
class FakePhonemeExtractor:
    def __init__(self, canned_phonemes: str = "k ə d eɪ ɹ n"):
        self.canned_phonemes = canned_phonemes
        self.device = None

    def load(self, device: str) -> None:
        self.device = device

    def extract(self, audio_path: str) -> str:
        return self.canned_phonemes

    def teardown(self) -> None:
        pass
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_phoneme_fake.py -v`
Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git add src/audiobookify/phoneme/__init__.py src/audiobookify/phoneme/base.py \
        src/audiobookify/phoneme/fake.py tests/test_phoneme_fake.py
git commit -m "feat: PhonemeExtractor interface and fake test double"
```

---

### Task 3: Phoneme-to-respelling generation

**Files:**
- Create: `src/audiobookify/phoneme/respelling.py`
- Test: `tests/test_respelling.py`

**Interfaces:**
- Consumes: nothing (pure string function).
- Produces: `phonemes_to_respelling(phonemes: str) -> str` in `audiobookify.phoneme.respelling`. Task 5 (CLI `lexicon add-from-audio`) calls this directly. `phonemes_to_respelling("k ə d eɪ ɹ n") == "kuhdayrn"` is the fixture Task 5's tests rely on.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_respelling.py`:

```python
from audiobookify.phoneme.respelling import phonemes_to_respelling


def test_phonemes_to_respelling_maps_known_ipa_tokens():
    assert phonemes_to_respelling("k ə d eɪ ɹ n") == "kuhdayrn"


def test_phonemes_to_respelling_passes_through_unknown_tokens_unchanged():
    assert phonemes_to_respelling("k ə ? n") == "kuh?n"


def test_phonemes_to_respelling_handles_empty_string():
    assert phonemes_to_respelling("") == ""


def test_phonemes_to_respelling_handles_diphthongs_and_consonant_clusters():
    # "hello" -> h ə l oʊ
    assert phonemes_to_respelling("h ə l oʊ") == "huhloh"
    # affricates
    assert phonemes_to_respelling("tʃ ɪ p") == "chihp"
    assert phonemes_to_respelling("dʒ ʌ m p") == "juhmp"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_respelling.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.phoneme.respelling'`

- [ ] **Step 3: Write `src/audiobookify/phoneme/respelling.py`**

```python
# Static espeak-IPA-symbol -> ASCII mapping. Deliberately simple: no syllable
# boundaries or stress marking. The derived respelling is a starting point the
# user corrects with `lexicon edit` before it's used, not a phonetically
# precise transcription.
_IPA_TO_ASCII = {
    "p": "p", "b": "b", "t": "t", "d": "d", "k": "k", "ɡ": "g", "g": "g",
    "tʃ": "ch", "dʒ": "j", "f": "f", "v": "v", "θ": "th", "ð": "th",
    "s": "s", "z": "z", "ʃ": "sh", "ʒ": "zh", "h": "h", "m": "m", "n": "n",
    "ŋ": "ng", "l": "l", "ɹ": "r", "r": "r", "w": "w", "j": "y",
    "i": "ee", "iː": "ee", "ɪ": "ih", "ɛ": "eh", "e": "eh", "æ": "a",
    "ɑ": "ah", "ɑː": "ah", "ɔ": "aw", "ɔː": "aw", "ʊ": "uu", "u": "oo",
    "uː": "oo", "ʌ": "uh", "ə": "uh", "ɚ": "er", "ɝ": "er",
    "eɪ": "ay", "aɪ": "eye", "ɔɪ": "oy", "aʊ": "ow", "oʊ": "oh",
}


def phonemes_to_respelling(phonemes: str) -> str:
    tokens = phonemes.split()
    return "".join(_IPA_TO_ASCII.get(token, token) for token in tokens)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_respelling.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/audiobookify/phoneme/respelling.py tests/test_respelling.py
git commit -m "feat: derive an ASCII respelling from an extracted phoneme string"
```

---

### Task 4: `Wav2Vec2PhonemeExtractor` (real model wrapper)

**Files:**
- Create: `src/audiobookify/phoneme/wav2vec2.py`
- Modify: `Makefile`
- Test: `tests/test_phoneme_wav2vec2.py`

**Interfaces:**
- Consumes: `PhonemeExtractor` Protocol shape (Task 2), `detect_device()` (`src/audiobookify/engines/base.py`, existing).
- Produces: `Wav2Vec2PhonemeExtractor` in `audiobookify.phoneme.wav2vec2`, implementing `load`/`extract`/`teardown`. Task 5 (CLI) imports and instantiates this as `cli.Wav2Vec2PhonemeExtractor`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_phoneme_wav2vec2.py`:

```python
from unittest.mock import MagicMock

import numpy as np
import pytest
import soundfile as sf
import torch

from audiobookify.phoneme.wav2vec2 import Wav2Vec2PhonemeExtractor


def _patch_transformers(monkeypatch, decoded_phonemes="k ə d eɪ ɹ n"):
    """Replace Wav2Vec2ForCTC/Wav2Vec2Processor.from_pretrained with stubs. No weights needed."""
    import transformers

    mock_model = MagicMock()
    mock_model.to.return_value = mock_model
    mock_model.return_value.logits = torch.zeros(1, 1, 5)

    mock_inputs = MagicMock()
    mock_processor = MagicMock(return_value=mock_inputs)
    mock_processor.batch_decode.return_value = [decoded_phonemes]

    monkeypatch.setattr(
        transformers.Wav2Vec2ForCTC, "from_pretrained", MagicMock(return_value=mock_model)
    )
    monkeypatch.setattr(
        transformers.Wav2Vec2Processor, "from_pretrained", MagicMock(return_value=mock_processor)
    )
    return mock_model, mock_processor


def test_wav2vec2_extractor_loads_model_and_processor_on_requested_device(monkeypatch):
    mock_model, mock_processor = _patch_transformers(monkeypatch)

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")

    assert extractor.model is mock_model
    assert extractor.processor is mock_processor
    assert extractor.device == "cpu"


def test_wav2vec2_extractor_extract_returns_decoded_phoneme_string(tmp_path, monkeypatch):
    _patch_transformers(monkeypatch, decoded_phonemes="k ə d eɪ ɹ n")

    clip_path = tmp_path / "clip.wav"
    sf.write(str(clip_path), np.zeros(1600, dtype="float32"), 16000)

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")
    phonemes = extractor.extract(str(clip_path))

    assert phonemes == "k ə d eɪ ɹ n"


def test_wav2vec2_extractor_skips_resampling_when_already_16khz(tmp_path, monkeypatch):
    _, mock_processor = _patch_transformers(monkeypatch)

    clip_path = tmp_path / "clip.wav"
    sf.write(str(clip_path), np.zeros(1600, dtype="float32"), 16000)

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")
    extractor.extract(str(clip_path))

    passed_audio = mock_processor.call_args.args[0]
    assert len(passed_audio) == 1600


def test_wav2vec2_extractor_resamples_when_native_rate_differs(tmp_path, monkeypatch):
    _, mock_processor = _patch_transformers(monkeypatch)

    clip_path = tmp_path / "clip.wav"
    sf.write(str(clip_path), np.zeros(4410, dtype="float32"), 44100)  # 0.1s at 44.1kHz

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")
    extractor.extract(str(clip_path))

    passed_audio = mock_processor.call_args.args[0]
    # 0.1s resampled to 16kHz should land near 1600 samples, not the original 4410
    assert abs(len(passed_audio) - 1600) < 50


def test_wav2vec2_extractor_teardown_clears_model_and_processor(monkeypatch):
    _patch_transformers(monkeypatch)

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")
    extractor.teardown()

    assert extractor.model is None
    assert extractor.processor is None


@pytest.mark.integration
def test_wav2vec2_extractor_runs_end_to_end_on_a_real_clip(tmp_path):
    # Requires the real transformers weights (`make models`).
    # Run explicitly with: pytest tests/test_phoneme_wav2vec2.py -m integration
    clip_path = tmp_path / "clip.wav"
    sf.write(str(clip_path), (0.01 * np.random.randn(8000)).astype("float32"), 16000)

    extractor = Wav2Vec2PhonemeExtractor()
    extractor.load("cpu")
    phonemes = extractor.extract(str(clip_path))
    extractor.teardown()

    assert isinstance(phonemes, str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_phoneme_wav2vec2.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.phoneme.wav2vec2'`

- [ ] **Step 3: Write `src/audiobookify/phoneme/wav2vec2.py`**

```python
import soundfile as sf


class Wav2Vec2PhonemeExtractor:
    model_name = "facebook/wav2vec2-lv-60-espeak-cv-ft"
    target_sample_rate = 16000

    def __init__(self):
        self.model = None
        self.processor = None
        self.device = None

    def load(self, device: str = "auto") -> None:
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        from ..engines.base import detect_device

        self.device = detect_device() if device == "auto" else device
        self.processor = Wav2Vec2Processor.from_pretrained(self.model_name)
        self.model = Wav2Vec2ForCTC.from_pretrained(self.model_name).to(self.device)
        self.model.eval()

    def extract(self, audio_path: str) -> str:
        import torch

        data, sample_rate = sf.read(audio_path, always_2d=True, dtype="float32")
        mono = data.mean(axis=1)

        if sample_rate != self.target_sample_rate:
            import torchaudio

            waveform = torch.from_numpy(mono).unsqueeze(0)
            waveform = torchaudio.functional.resample(waveform, sample_rate, self.target_sample_rate)
            mono = waveform.squeeze(0).numpy()

        inputs = self.processor(mono, sampling_rate=self.target_sample_rate, return_tensors="pt")
        with torch.no_grad():
            logits = self.model(inputs.input_values.to(self.device)).logits
        predicted_ids = torch.argmax(logits, dim=-1)
        return self.processor.batch_decode(predicted_ids)[0]

    def teardown(self) -> None:
        self.model = None
        self.processor = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_phoneme_wav2vec2.py -v`
Expected: 6 passed, 1 deselected (the integration test)

- [ ] **Step 5: Add phoneme weight caching to the `models` Makefile target**

In `Makefile`, change:

```makefile
models:
	$(PY) -c "from audiobookify.engines.chatterbox import ChatterboxEngine; e = ChatterboxEngine(); e.load('cpu'); e.teardown(); print('Chatterbox weights cached.')"
```

to:

```makefile
models:
	$(PY) -c "from audiobookify.engines.chatterbox import ChatterboxEngine; e = ChatterboxEngine(); e.load('cpu'); e.teardown(); print('Chatterbox weights cached.')"
	$(PY) -c "from audiobookify.phoneme.wav2vec2 import Wav2Vec2PhonemeExtractor; e = Wav2Vec2PhonemeExtractor(); e.load('cpu'); e.teardown(); print('Phoneme recognition weights cached.')"
```

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (prior 112 + 6 from Task 1 + 2 from Task 2 + 4 from Task 3 + 6 from Task 4 = 130 passed, 2 deselected)

- [ ] **Step 7: Commit**

```bash
git add src/audiobookify/phoneme/wav2vec2.py Makefile tests/test_phoneme_wav2vec2.py
git commit -m "feat: wav2vec2 CTC phoneme extraction from an audio clip"
```

---

### Task 5: CLI `lexicon add-from-audio` + `lexicon list` display update

**Files:**
- Modify: `src/audiobookify/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Database.create_lexicon_entry` (Task 1, extended), `Wav2Vec2PhonemeExtractor` (Task 4), `phonemes_to_respelling` (Task 3), `_resolve_book_for_lexicon` (existing).
- Produces: `audiobookify lexicon add-from-audio --term --audio [--book] [--notes] [--device] [--db]` subcommand; updated `lexicon list` output showing `source` and `phonemes`. Task 6 (`lexicon edit`) is added as a sibling subcommand in the same `lexicon` subparser group.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py` (add `from audiobookify.phoneme.fake import FakePhonemeExtractor` to the existing imports at the top of the file, alongside `from audiobookify.engines.fake import FakeEngine`):

```python
def test_lexicon_add_from_audio_creates_entry_with_derived_respelling(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", FakePhonemeExtractor)

    audio_path = tmp_path / "cadeyrn.wav"
    _write_reference_wav(audio_path, duration_s=0.6)
    db_path = tmp_path / "test.db"

    cli.main([
        "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(audio_path),
        "--db", str(db_path),
    ])

    captured = capsys.readouterr()
    assert "Extracted phonemes: k ə d eɪ ɹ n" in captured.out
    assert "Derived respelling: kuhdayrn" in captured.out
    assert "Added pronunciation for 'Cadeyrn' -> 'kuhdayrn'" in captured.out

    db = Database(str(db_path))
    entry = db.find_lexicon_entry("Cadeyrn")
    assert entry.source == "from_audio"
    assert entry.phonemes == "k ə d eɪ ɹ n"
    assert entry.sample_audio_path == str(audio_path.resolve())
    db.close()


def test_lexicon_add_from_audio_creates_book_scoped_entry(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", FakePhonemeExtractor)
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"
    audio_path = tmp_path / "cadeyrn.wav"
    _write_reference_wav(audio_path, duration_s=0.6)

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    cli.main([
        "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(audio_path),
        "--book", str(md_path), "--db", str(db_path),
    ])

    db = Database(str(db_path))
    book = db.find_book_by_source(str(md_path.resolve()))
    entry = db.find_lexicon_entry("Cadeyrn", book_id=book.id)
    assert entry is not None
    assert entry.scope == "book"
    db.close()


def test_lexicon_add_from_audio_reports_unreadable_audio(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", FakePhonemeExtractor)
    db_path = tmp_path / "test.db"
    bad_path = tmp_path / "not-audio.txt"
    bad_path.write_text("not a wav file", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(bad_path),
            "--db", str(db_path),
        ])

    assert exc_info.value.code == 1
    assert "could not read pronunciation clip" in capsys.readouterr().err


def test_lexicon_add_from_audio_rejects_empty_term(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", FakePhonemeExtractor)
    audio_path = tmp_path / "cadeyrn.wav"
    _write_reference_wav(audio_path, duration_s=0.6)
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "lexicon", "add-from-audio", "--term", "  ", "--audio", str(audio_path),
            "--db", str(db_path),
        ])

    assert exc_info.value.code == 1
    assert "must not be empty" in capsys.readouterr().err


def test_lexicon_list_shows_source_and_phonemes_for_from_audio_entries(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", FakePhonemeExtractor)
    audio_path = tmp_path / "cadeyrn.wav"
    _write_reference_wav(audio_path, duration_s=0.6)
    db_path = tmp_path / "test.db"

    cli.main([
        "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(audio_path),
        "--db", str(db_path),
    ])
    capsys.readouterr()

    cli.main(["lexicon", "list", "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "from_audio" in captured.out
    assert "k ə d eɪ ɹ n" in captured.out


def test_lexicon_list_still_shows_manual_entries_without_phonemes_column_crashing(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])
    capsys.readouterr()

    cli.main(["lexicon", "list", "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Cadeyrn" in captured.out
    assert "manual" in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v -k "add_from_audio or lexicon_list"`
Expected: the 4 `add_from_audio` tests FAIL with `SystemExit` from argparse (`invalid choice: 'add-from-audio'`); the 2 new `lexicon_list` tests (`..._shows_source_and_phonemes...`, `..._without_phonemes_column_crashing`) FAIL on the `"from_audio"`/`"manual"` assertions since `_handle_lexicon_list` doesn't print `source` yet; the 3 pre-existing `lexicon_list` tests from M2 still PASS unchanged

- [ ] **Step 3: Add a light audio-readability check, the handler, the subparser, and the dispatch to `src/audiobookify/cli.py`**

Add the import at the top of `cli.py`, alphabetized with the other local imports (after `from .models import ...` and before `from .synth.pipeline import generate_book`):

```python
from .phoneme.respelling import phonemes_to_respelling
from .phoneme.wav2vec2 import Wav2Vec2PhonemeExtractor
```

Add this helper after `_sanitize_filename_component`:

```python
def _validate_pronunciation_clip(path: str) -> None:
    try:
        data, _ = sf.read(path, always_2d=True, dtype="float32")
    except (RuntimeError, OSError) as exc:
        raise ValueError(f"could not read pronunciation clip '{path}': {exc}") from exc
    if len(data) == 0:
        raise ValueError(f"pronunciation clip '{path}' contains no audio")
```

Add this handler after `_handle_lexicon_add`:

```python
def _handle_lexicon_add_from_audio(args) -> None:
    if not args.term.strip():
        print("Error: lexicon term must not be empty", file=sys.stderr)
        sys.exit(1)
    args.term = args.term.strip()

    if not re.match(r"^\w", args.term) or not re.search(r"\w$", args.term):
        print(
            f"Warning: '{args.term}' doesn't start and end with a letter or digit; "
            "whole-word substitution requires a word boundary there and may never match",
            file=sys.stderr,
        )

    audio_path = str(Path(args.audio).resolve())
    try:
        _validate_pronunciation_clip(audio_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    db = Database(args.db)
    book_id = 0
    scope = "global"
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id
        scope = "book"

    extractor = Wav2Vec2PhonemeExtractor()
    try:
        extractor.load(args.device)
        phonemes = extractor.extract(audio_path)
    finally:
        extractor.teardown()

    respelling = phonemes_to_respelling(phonemes)

    try:
        entry = db.create_lexicon_entry(
            term=args.term, override_value=respelling, scope=scope, book_id=book_id,
            notes=args.notes, source="from_audio", phonemes=phonemes,
            sample_audio_path=audio_path,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)

    print(f"Extracted phonemes: {phonemes}")
    print(f"Derived respelling: {respelling}")
    print(
        f"Added pronunciation for '{entry.term}' -> '{entry.override_value}' "
        f"({entry.scope} scope, source: from_audio)"
    )
    db.close()
```

Change `_handle_lexicon_list` from:

```python
def _handle_lexicon_list(args) -> None:
    db = Database(args.db)
    book_id = None
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id

    entries = db.list_lexicon_entries(book_id)
    if not entries:
        print("No lexicon entries yet. Add one with 'audiobookify lexicon add'.")
    for entry in entries:
        print(f"{entry.term}\t{entry.override_value}\t{entry.scope}")
    db.close()
```

to:

```python
def _handle_lexicon_list(args) -> None:
    db = Database(args.db)
    book_id = None
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id

    entries = db.list_lexicon_entries(book_id)
    if not entries:
        print("No lexicon entries yet. Add one with 'audiobookify lexicon add'.")
    for entry in entries:
        line = f"{entry.term}\t{entry.override_value}\t{entry.scope}\t{entry.source}"
        if entry.phonemes:
            line += f"\t{entry.phonemes}"
        print(line)
    db.close()
```

Add the subparser in `main()`, after `lexicon_add_parser`'s definition and before `lexicon_list_parser`'s definition:

```python
    lexicon_add_from_audio_parser = lexicon_subparsers.add_parser(
        "add-from-audio",
        help="Derive a pronunciation override from an audio clip of the term spoken correctly",
    )
    lexicon_add_from_audio_parser.add_argument("--term", required=True)
    lexicon_add_from_audio_parser.add_argument("--audio", required=True)
    lexicon_add_from_audio_parser.add_argument("--book", default=None)
    lexicon_add_from_audio_parser.add_argument("--notes", default=None)
    lexicon_add_from_audio_parser.add_argument("--device", default="auto")
    lexicon_add_from_audio_parser.add_argument("--db", default="audiobookify.db")
```

Add the dispatch branch, as a new `elif` alongside the existing `elif args.lexicon_command == "add":` block:

```python
        elif args.lexicon_command == "add-from-audio":
            _handle_lexicon_add_from_audio(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all pass (47 existing + 6 new = 53)

- [ ] **Step 5: Commit**

```bash
git add src/audiobookify/cli.py tests/test_cli.py
git commit -m "feat: audiobookify lexicon add-from-audio CLI command"
```

---

### Task 6: CLI `lexicon edit`

**Files:**
- Modify: `src/audiobookify/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Database.update_lexicon_entry_override` (Task 1), `_resolve_book_for_lexicon` (existing).
- Produces: `audiobookify lexicon edit --term --respell [--book] [--db]` subcommand.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_lexicon_edit_updates_respelling_and_keeps_metadata(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", FakePhonemeExtractor)
    audio_path = tmp_path / "cadeyrn.wav"
    _write_reference_wav(audio_path, duration_s=0.6)
    db_path = tmp_path / "test.db"

    cli.main([
        "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(audio_path),
        "--db", str(db_path),
    ])
    capsys.readouterr()

    cli.main(["lexicon", "edit", "--term", "Cadeyrn", "--respell", "kah-DAIRN", "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Updated pronunciation for 'Cadeyrn' -> 'kah-DAIRN'" in captured.out

    db = Database(str(db_path))
    entry = db.find_lexicon_entry("Cadeyrn")
    assert entry.override_value == "kah-DAIRN"
    assert entry.source == "from_audio"  # provenance preserved
    assert entry.phonemes == "k ə d eɪ ɹ n"
    db.close()


def test_lexicon_edit_reports_missing_entry(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "edit", "--term", "Ghost", "--respell", "GOST", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "no lexicon entry" in capsys.readouterr().err


def test_lexicon_edit_rejects_empty_respelling(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "edit", "--term", "Cadeyrn", "--respell", "   ", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "must not be empty" in capsys.readouterr().err


def test_lexicon_edit_respects_book_scope(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "GLOBAL-version", "--db", str(db_path)])
    cli.main([
        "lexicon", "add", "--term", "Cadeyrn", "--respell", "BOOK-version",
        "--book", str(md_path), "--db", str(db_path),
    ])

    cli.main([
        "lexicon", "edit", "--term", "Cadeyrn", "--respell", "BOOK-EDITED",
        "--book", str(md_path), "--db", str(db_path),
    ])

    db = Database(str(db_path))
    book = db.find_book_by_source(str(md_path.resolve()))
    assert db.find_lexicon_entry("Cadeyrn", book_id=book.id).override_value == "BOOK-EDITED"
    assert db.find_lexicon_entry("Cadeyrn").override_value == "GLOBAL-version"  # untouched
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v -k lexicon_edit`
Expected: FAIL with `SystemExit` from argparse (`invalid choice: 'edit'`)

- [ ] **Step 3: Add the handler, subparser, and dispatch to `src/audiobookify/cli.py`**

Add this handler after `_handle_lexicon_add_from_audio`:

```python
def _handle_lexicon_edit(args) -> None:
    args.term = args.term.strip()

    if not args.respell.strip():
        print("Error: lexicon respelling must not be empty", file=sys.stderr)
        sys.exit(1)
    args.respell = args.respell.strip()

    db = Database(args.db)
    book_id = 0
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id

    try:
        entry = db.update_lexicon_entry_override(args.term, args.respell, book_id=book_id)
    except KeyError as exc:
        print(f"Error: {exc.args[0]}", file=sys.stderr)
        db.close()
        sys.exit(1)

    print(f"Updated pronunciation for '{entry.term}' -> '{entry.override_value}'")
    db.close()
```

Add the subparser in `main()`, after `lexicon_remove_parser`'s definition and before `lexicon_preview_parser`'s definition:

```python
    lexicon_edit_parser = lexicon_subparsers.add_parser(
        "edit", help="Correct the respelling for an existing pronunciation override"
    )
    lexicon_edit_parser.add_argument("--term", required=True)
    lexicon_edit_parser.add_argument("--respell", required=True)
    lexicon_edit_parser.add_argument("--book", default=None)
    lexicon_edit_parser.add_argument("--db", default="audiobookify.db")
```

Add the dispatch branch, as a new `elif` alongside the existing `elif args.lexicon_command == "remove":` block:

```python
        elif args.lexicon_command == "edit":
            _handle_lexicon_edit(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all pass (53 from Task 5 + 4 new = 57)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass, 2 deselected (the Chatterbox and wav2vec2 integration tests)

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/cli.py tests/test_cli.py
git commit -m "feat: audiobookify lexicon edit CLI command"
```

---

### Task 7: README — M3 usage docs

**Files:**
- Modify: `README.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Update `README.md`**

Find the "Pronunciation lexicon (M2)" section (added by M2) and append immediately after its existing content (after the closing paragraph about IPA/ARPAbet being deferred):

```markdown

## Audio-sample pronunciation (M3)

Instead of typing a respelling by hand, attach a short clip of the term spoken correctly and let the app derive one:

```bash
.venv/bin/audiobookify lexicon add-from-audio --term "Cadeyrn" --audio ./cadeyrn.wav
```

This runs the clip through a local phoneme recognizer (`facebook/wav2vec2-lv-60-espeak-cv-ft`, fetched once via `make models`), derives an ASCII respelling from the extracted phonemes, and creates a lexicon entry with `source: from_audio` — printing both the raw phonemes and the derived respelling so you can judge it immediately. `--book <path>` scopes it to one book, same as `lexicon add`.

The derived respelling is a starting point, not guaranteed to be right — audit it:

```bash
.venv/bin/audiobookify lexicon preview --term "Cadeyrn"
.venv/bin/audiobookify lexicon edit --term "Cadeyrn" --respell "kah-DAIRN"
```

- `lexicon preview` (unchanged from M2) works on any entry regardless of source — hear how the current respelling sounds.
- `lexicon edit --term NAME --respell NEW_VALUE`: corrects just the respelling in place. Use this instead of `remove` + `add` — it preserves the entry's extracted phonemes, attached clip path, and `source: from_audio` provenance, all of which `remove`+`add` would silently discard. `--book <path>` scopes it to a book-specific entry.
- `lexicon list` now also shows each entry's `source` (`manual` or `from_audio`) and, when present, its raw extracted phonemes.

Audio-derived entries substitute into text exactly like manual ones — nothing about `generate` changes. The phoneme string is stored for reference/audit only; it is never sent to the TTS engine directly (Chatterbox still can't consume phoneme input, same constraint as M2).
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: M3 audio-sample pronunciation usage instructions"
```

---

## After Task 7

Run the full whole-branch review (per `subagent-driven-development`) before considering M3 done — M1 and M2's final reviews each caught real bugs a per-task lens missed (e.g. M2's longest-term-first substitution ordering bug, M1's voice-mixing bug). Add a "Task 8: final-review fix wave" to this plan only if that review finds something, following M2's precedent (`docs/superpowers/plans/2026-07-28-audiobookify-m2-pronunciation-lexicon.md` Task 8) — do not pre-emptively invent fixes for issues that haven't been found yet.

---

### Task 8: Final-review fix wave

**Context:** the whole-branch review of Tasks 1-7 (opus) found no Critical issues, 2 Important, and several Minor. This task fixes them in one pass, following the exact same "1 opus review -> 1 fix wave" precedent as M1 Task 8 and M2 Task 8.

Findings being fixed:
- **Important:** `_handle_lexicon_add_from_audio` wrote a derived respelling straight into `override_value` with no non-empty check. `phonemes_to_respelling("")` returns `""`, and an empty `override_value` is not NULL so it inserts cleanly — then `apply_lexicon` substitutes every occurrence of the term with the empty string, **silently deleting the word from the generated narration**. This is the same class of bug M2's own final review found and fixed (an empty `--respell` silently deleting terms) — `lexicon add`/`lexicon edit` both already guard against it; `add-from-audio` was the one hole.
- **Important:** an unmapped IPA token in the extracted phonemes passes straight through into `override_value` (per-plan, since `phonemes_to_respelling` intentionally passes unknown tokens through unchanged) with no signal to the user that the result contains characters the engine can't read naturally (M2's README already warns respellings should stay to plain letters/hyphens).
- **Minor:** an extraction failure (a real model/inference exception) was uncaught, leaking the DB connection and surfacing a raw traceback instead of the CLI's `"Error: "` convention.
- **Minor:** re-running `add-from-audio` on a term that already exists in scope pays for a full model load + inference before failing on the `UNIQUE` constraint — an early existence check makes the failure instant.
- **Minor:** the term-trim/empty-check/word-boundary-warning block was copy-pasted verbatim between `_handle_lexicon_add` and `_handle_lexicon_add_from_audio` — extract a shared helper.
- **Minor (test gaps):** no test covered `_validate_pronunciation_clip`'s "contains no audio" branch, the duplicate-term error path for `add-from-audio`, the word-boundary warning in the new handler, or `--notes` pass-through.
- **Minor (docs):** the README M3 section didn't mention the `--notes`/`--device` flags on `add-from-audio`, though both exist.

**Files:**
- Modify: `src/audiobookify/cli.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_lexicon_add_from_audio_rejects_silent_clip_with_no_detectable_speech(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", lambda: FakePhonemeExtractor(canned_phonemes=""))

    audio_path = tmp_path / "silent.wav"
    _write_reference_wav(audio_path, duration_s=0.6)
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(audio_path),
            "--db", str(db_path),
        ])

    assert exc_info.value.code == 1
    assert "no pronunciation could be derived" in capsys.readouterr().err

    db = Database(str(db_path))
    assert db.find_lexicon_entry("Cadeyrn") is None
    db.close()


def test_lexicon_add_from_audio_warns_when_respelling_has_non_ascii_letters(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", lambda: FakePhonemeExtractor(canned_phonemes="ɐ"))

    audio_path = tmp_path / "clip.wav"
    _write_reference_wav(audio_path, duration_s=0.6)
    db_path = tmp_path / "test.db"

    cli.main([
        "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(audio_path),
        "--db", str(db_path),
    ])

    assert "contains characters that aren't plain letters or hyphens" in capsys.readouterr().err


def test_lexicon_add_from_audio_rejects_duplicate_term_in_same_scope(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", FakePhonemeExtractor)

    audio_path = tmp_path / "cadeyrn.wav"
    _write_reference_wav(audio_path, duration_s=0.6)
    db_path = tmp_path / "test.db"

    cli.main([
        "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(audio_path),
        "--db", str(db_path),
    ])
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(audio_path),
            "--db", str(db_path),
        ])

    assert exc_info.value.code == 1
    assert "already exists" in capsys.readouterr().err


def test_lexicon_add_from_audio_warns_when_term_cannot_match_a_word_boundary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", FakePhonemeExtractor)

    audio_path = tmp_path / "clip.wav"
    _write_reference_wav(audio_path, duration_s=0.6)
    db_path = tmp_path / "test.db"

    cli.main([
        "lexicon", "add-from-audio", "--term", "Dr.", "--audio", str(audio_path),
        "--db", str(db_path),
    ])

    assert "word boundary" in capsys.readouterr().err


def test_lexicon_add_from_audio_stores_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", FakePhonemeExtractor)

    audio_path = tmp_path / "cadeyrn.wav"
    _write_reference_wav(audio_path, duration_s=0.6)
    db_path = tmp_path / "test.db"

    cli.main([
        "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(audio_path),
        "--notes", "derived from a recording", "--db", str(db_path),
    ])

    db = Database(str(db_path))
    entry = db.find_lexicon_entry("Cadeyrn")
    assert entry.notes == "derived from a recording"
    db.close()


def test_lexicon_add_from_audio_reports_extraction_failure(tmp_path, monkeypatch, capsys):
    class FailingExtractor(FakePhonemeExtractor):
        def extract(self, audio_path):
            raise RuntimeError("model inference failed")

    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", FailingExtractor)

    audio_path = tmp_path / "cadeyrn.wav"
    _write_reference_wav(audio_path, duration_s=0.6)
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(audio_path),
            "--db", str(db_path),
        ])

    assert exc_info.value.code == 1
    assert "could not extract phonemes" in capsys.readouterr().err

    db = Database(str(db_path))
    assert db.find_lexicon_entry("Cadeyrn") is None
    db.close()


def test_lexicon_add_from_audio_rejects_empty_audio_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Wav2Vec2PhonemeExtractor", FakePhonemeExtractor)

    audio_path = tmp_path / "empty.wav"
    sf.write(str(audio_path), np.zeros(0, dtype="float32"), 16000)
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "lexicon", "add-from-audio", "--term", "Cadeyrn", "--audio", str(audio_path),
            "--db", str(db_path),
        ])

    assert exc_info.value.code == 1
    assert "contains no audio" in capsys.readouterr().err
```

Add `from audiobookify.phoneme.fake import FakePhonemeExtractor` if it isn't already imported at the top of `tests/test_cli.py` (Task 5 already added it — check first, don't duplicate the import).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v -k "silent_clip or non_ascii or duplicate_term_in_same_scope and add_from_audio or word_boundary and add_from_audio or stores_notes or extraction_failure or empty_audio_file"`
Expected: FAIL — the silent-clip, non-ascii-warning, and extraction-failure tests fail because the guards don't exist yet; the duplicate-term test currently fails on model-load being attempted before the DB constraint check (behavior differs, not a crash, but the assertion on `"already exists"` still passes today since `create_lexicon_entry` already raises — so this one may already PASS; the point of Step 3 is to make it fail *fast*, not to fix a bug in this one). The word-boundary and notes tests likely already PASS since that behavior already exists — that's fine, they're regression coverage for the refactor in Step 3.

- [ ] **Step 3: Fix `src/audiobookify/cli.py`**

Add a shared term-validation helper. Insert it directly before `_handle_lexicon_add`:

```python
def _validate_lexicon_term(term: str) -> str:
    term = term.strip()
    if not term:
        print("Error: lexicon term must not be empty", file=sys.stderr)
        sys.exit(1)
    if not re.match(r"^\w", term) or not re.search(r"\w$", term):
        print(
            f"Warning: '{term}' doesn't start and end with a letter or digit; "
            "whole-word substitution requires a word boundary there and may never match",
            file=sys.stderr,
        )
    return term
```

Replace `_handle_lexicon_add`'s opening (term validation only — leave the rest of the function, including the respelling-empty check and everything after, unchanged):

```python
def _handle_lexicon_add(args) -> None:
    if not args.term.strip():
        print("Error: lexicon term must not be empty", file=sys.stderr)
        sys.exit(1)
    args.term = args.term.strip()

    if not args.respell.strip():
        print("Error: lexicon respelling must not be empty", file=sys.stderr)
        sys.exit(1)
    args.respell = args.respell.strip()

    if not re.match(r"^\w", args.term) or not re.search(r"\w$", args.term):
        print(
            f"Warning: '{args.term}' doesn't start and end with a letter or digit; "
            "whole-word substitution requires a word boundary there and may never match",
            file=sys.stderr,
        )
```

with:

```python
def _handle_lexicon_add(args) -> None:
    args.term = _validate_lexicon_term(args.term)

    if not args.respell.strip():
        print("Error: lexicon respelling must not be empty", file=sys.stderr)
        sys.exit(1)
    args.respell = args.respell.strip()
```

Replace the entire `_handle_lexicon_add_from_audio` function with:

```python
def _handle_lexicon_add_from_audio(args) -> None:
    args.term = _validate_lexicon_term(args.term)

    audio_path = str(Path(args.audio).resolve())
    try:
        _validate_pronunciation_clip(audio_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    db = Database(args.db)
    book_id = 0
    scope = "global"
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id
        scope = "book"

    if db.find_lexicon_entry(args.term, book_id=book_id) is not None:
        print(f"Error: a lexicon entry for '{args.term}' already exists in this scope", file=sys.stderr)
        db.close()
        sys.exit(1)

    extractor = Wav2Vec2PhonemeExtractor()
    try:
        extractor.load(args.device)
        phonemes = extractor.extract(audio_path)
    except Exception as exc:
        db.close()
        print(f"Error: could not extract phonemes from '{audio_path}': {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        extractor.teardown()

    respelling = phonemes_to_respelling(phonemes)

    if not respelling.strip():
        print(
            f"Error: no pronunciation could be derived from '{audio_path}' "
            "(the clip may contain no detectable speech)",
            file=sys.stderr,
        )
        db.close()
        sys.exit(1)

    if not re.fullmatch(r"[A-Za-z-]+", respelling):
        print(
            f"Warning: derived respelling '{respelling}' contains characters that aren't "
            "plain letters or hyphens; correct it with 'lexicon edit' before generating",
            file=sys.stderr,
        )

    try:
        entry = db.create_lexicon_entry(
            term=args.term, override_value=respelling, scope=scope, book_id=book_id,
            notes=args.notes, source="from_audio", phonemes=phonemes,
            sample_audio_path=audio_path,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)

    print(f"Extracted phonemes: {phonemes}")
    print(f"Derived respelling: {respelling}")
    print(
        f"Added pronunciation for '{entry.term}' -> '{entry.override_value}' "
        f"({entry.scope} scope, source: from_audio)"
    )
    db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all pass (57 existing + 6 new = 63)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass, 2 deselected

- [ ] **Step 6: Update `README.md`**

In the "Audio-sample pronunciation (M3)" section added by Task 7, find the sentence `--book <path> scopes it to one book, same as lexicon add.` and change it to also mention the two flags that weren't documented:

```markdown
`--book <path>` scopes it to one book, same as `lexicon add`. `--notes "..."` attaches a free-text note, same as `lexicon add`. `--device` picks the compute device for the phoneme model (`auto`/`cpu`/`cuda`/`mps`), same convention as `generate --device`.
```

- [ ] **Step 7: Commit**

```bash
git add src/audiobookify/cli.py tests/test_cli.py README.md
git commit -m "fix: address M3 whole-branch review findings"
```

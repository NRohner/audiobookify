# Audiobookify M2 — Pronunciation Lexicon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A persistent pronunciation lexicon — global or per-book term → respelling overrides — applied as whole-word, case-insensitive text substitution before synthesis, manageable and previewable from the CLI (`audiobookify lexicon add/list/remove/preview`), and automatically applied by `generate`.

**Architecture:** `LexiconEntry` is a new model persisted in the same SQLite database as `Book`/`Chapter`/`VoiceProfile` (new `lexicon_entries` table, same `Database` class). A new, pure `lexicon/substitution.py` module does regex-based term substitution over each chapter's **raw text, before** `chunk_text()` runs, so a multi-word term can never be split across a chunk boundary. `synth/pipeline.py` fetches the applicable entries once per book (mirroring how it already fetches a voice profile once) and applies them ahead of chunking. The CLI stays the only interface (no GUI yet).

**Tech Stack:** Same as M0/M1 (stdlib `sqlite3`/`re`/`argparse`, `pytest`) — no new dependencies.

## Global Constraints

- **Respelling only for M2** (user decision, 2026-07-28): the installed `chatterbox-tts==0.1.7` engine's `preferred_phoneme_scheme()` returns `None` — it cannot consume inline IPA/ARPAbet at all. Building `override_type` support for phoneme schemes now would ship an inert feature. `LexiconEntry` has no `override_type` column in M2; every entry is implicitly a respelling. Add `override_type`/`sample_audio_path` via an additive `ALTER TABLE` when a phoneme-capable engine or M3's audio→phoneme extraction actually lands — don't build those columns now.
- Substitution per spec §5.1: whole-word, case-insensitive, word-boundary matching; multi-word terms supported. Implemented with `re.IGNORECASE` and `\b...\b` around each escaped term — this is naturally substring-safe (`\bAnn\b` does not match inside `Anne-Marie`) so no extra care is needed for overlapping terms of different lengths.
- **Scope precedence:** an entry can exist at `global` scope (applies to every book) or `book` scope (applies only to one specific, already-known book). The same term may exist at both scopes simultaneously (a legitimate "override my usual global pronunciation for this one book" case) — when both exist, the book-scoped entry wins. This is implemented for free by list-ordering (book-scoped entries listed before global ones) plus the fact that a regex substitution can't match text that's already been substituted — no separate precedence/dedup logic needed.
- **NULL-safety** (lesson from M1's `Book.voice_profile_id` bug, see `docs/superpowers/plans/2026-07-28-audiobookify-m1-voice-cloning.md` Task 8): global entries store a sentinel `book_id = 0` (never `NULL`), so `UNIQUE(book_id, term)` actually enforces uniqueness — SQLite treats every `NULL` in a `UNIQUE` index as distinct from every other `NULL`, which would otherwise silently allow duplicate global terms.
- A book-scoped lexicon entry requires the book to already exist in the database (found via `find_book_by_source`, same lookup `generate` uses) — `lexicon add --book <path>` does **not** ingest a new book. If the book hasn't been through `generate` at least once yet, error and tell the user to run `generate` first. This avoids duplicating `_ingest`'s book-creation logic outside of `generate`.
- Lexicon substitution runs on each chapter's **raw text, before** `chunk_text()` (which internally still runs `normalize_text()` for abbreviation/number expansion, unchanged from M0) — not per-chunk. This guarantees a multi-word term is never split across a chunk boundary. The minor tradeoff (a respelling containing digits or an abbreviation-like substring could theoretically be touched by the normalizer that runs afterward) is accepted and documented in the README; respellings should stick to plain letters/hyphens.
- Terms and respellings are stored trimmed and must be non-empty (same `--name` whitespace/empty lesson from M1's voice profiles applies here too) — validated in the CLI layer, matching where that check already lives for `voice add --name`.
- `lexicon preview` follows M1's `voice preview` convention exactly: writes a WAV file and prints its path, no auto-playback.
- Follow existing CLI/error conventions: resolve paths to absolute before storing, errors to stderr prefixed `"Error: "` + exit 1, warnings to stderr prefixed `"Warning: "`, `ChatterboxEngine` stays importable as `cli.ChatterboxEngine` for test monkeypatching.
- Out of scope for M2 (later milestones per spec §9): audio-sample → phoneme extraction and the from-audio lexicon workflow (M3), problem-word auto-scan / candidate suggestion (M5, explicitly listed there), concatenative splice fallback (M5), GUI (M4).

---

## File Structure

```
audiobookify/
  src/audiobookify/
    models.py                # + LexiconEntry dataclass
    db.py                    # + lexicon_entries table, create/find/list/delete_lexicon_entry
    lexicon/
      __init__.py              # new
      substitution.py           # new: apply_lexicon()
    synth/
      pipeline.py                # + fetch entries once, apply before chunk_text
    cli.py                      # + `lexicon add|list|remove|preview` subcommands
  tests/
    test_db.py                 # + lexicon entry persistence tests
    test_lexicon_substitution.py  # new
    test_pipeline.py            # + lexicon-wired generation tests
    test_cli.py                 # + lexicon subcommand tests
  README.md                    # + M2 usage docs
```

---

### Task 1: `LexiconEntry` model + SQLite persistence

**Files:**
- Modify: `src/audiobookify/models.py`
- Modify: `src/audiobookify/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: existing `Database` class structure and `SCHEMA` string (`src/audiobookify/db.py`).
- Produces: `LexiconEntry` dataclass in `audiobookify.models` (`id, term, override_value, source, scope, book_id, notes, created_at`); `Database.create_lexicon_entry(term, override_value, scope, book_id=0, notes=None, source="manual") -> LexiconEntry`, `Database.find_lexicon_entry(term, book_id=0) -> Optional[LexiconEntry]`, `Database.list_lexicon_entries(book_id=None) -> List[LexiconEntry]`, `Database.delete_lexicon_entry(term, book_id=0) -> None`. Task 3 (pipeline wiring) and Task 4/5 (CLI) call these directly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_create_and_find_global_lexicon_entry(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    entry = db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")

    assert entry.id is not None
    assert entry.book_id == 0

    found = db.find_lexicon_entry("Cadeyrn")
    assert found is not None
    assert found.override_value == "KAH-dern"
    assert found.scope == "global"
    assert found.source == "manual"
    db.close()


def test_create_and_find_book_scoped_lexicon_entry(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    entry = db.create_lexicon_entry(
        term="Cadeyrn", override_value="kah-DAIRN", scope="book", book_id=book.id, notes="book-specific take"
    )

    assert entry.book_id == book.id

    found = db.find_lexicon_entry("Cadeyrn", book_id=book.id)
    assert found is not None
    assert found.override_value == "kah-DAIRN"
    assert found.notes == "book-specific take"

    # the global lookup (book_id=0, the default) must not see the book-scoped entry
    assert db.find_lexicon_entry("Cadeyrn") is None
    db.close()


def test_create_lexicon_entry_rejects_duplicate_term_in_same_scope(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")

    with pytest.raises(ValueError, match="Cadeyrn"):
        db.create_lexicon_entry(term="Cadeyrn", override_value="something-else", scope="global")
    db.close()


def test_create_lexicon_entry_allows_same_term_at_different_scopes(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")
    # must not raise - book-scoped override of a globally-defined term is legitimate
    db.create_lexicon_entry(term="Cadeyrn", override_value="kah-DAIRN", scope="book", book_id=book.id)
    db.close()


def test_list_lexicon_entries_without_book_id_returns_global_only(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.create_lexicon_entry(term="Global One", override_value="g-one", scope="global")
    db.create_lexicon_entry(term="Book One", override_value="b-one", scope="book", book_id=book.id)

    entries = db.list_lexicon_entries()
    assert [e.term for e in entries] == ["Global One"]
    db.close()


def test_list_lexicon_entries_with_book_id_returns_book_scoped_first_then_global(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.create_lexicon_entry(term="Global One", override_value="g-one", scope="global")
    db.create_lexicon_entry(term="Book One", override_value="b-one", scope="book", book_id=book.id)

    entries = db.list_lexicon_entries(book_id=book.id)
    assert [e.term for e in entries] == ["Book One", "Global One"]
    db.close()


def test_delete_lexicon_entry_removes_it(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")

    db.delete_lexicon_entry("Cadeyrn")

    assert db.find_lexicon_entry("Cadeyrn") is None
    db.close()


def test_delete_lexicon_entry_raises_keyerror_for_missing_entry(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    with pytest.raises(KeyError, match="Cadeyrn"):
        db.delete_lexicon_entry("Cadeyrn")
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v -k lexicon`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'create_lexicon_entry'`

- [ ] **Step 3: Add `LexiconEntry` to `src/audiobookify/models.py`**

Append to `src/audiobookify/models.py`:

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

- [ ] **Step 4: Add the `lexicon_entries` table and CRUD methods to `src/audiobookify/db.py`**

Change the import line at the top:

```python
from .models import Book, Chapter, ChapterStatus, LexiconEntry, SourceFormat, VoiceProfile
```

Append to the `SCHEMA` string, inside the triple-quoted block, after the `voice_profiles` table definition and before the closing `"""`:

```python

CREATE TABLE IF NOT EXISTS lexicon_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL COLLATE NOCASE,
    override_value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    scope TEXT NOT NULL,
    book_id INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at REAL NOT NULL,
    UNIQUE(book_id, term)
);
```

Add a row-mapping helper next to `_row_to_voice_profile`:

```python
def _row_to_lexicon_entry(row: sqlite3.Row) -> LexiconEntry:
    return LexiconEntry(
        id=row["id"], term=row["term"], override_value=row["override_value"],
        source=row["source"], scope=row["scope"], book_id=row["book_id"],
        notes=row["notes"], created_at=row["created_at"],
    )
```

Add these methods to the `Database` class (anywhere in the class body):

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

    def find_lexicon_entry(self, term: str, book_id: int = 0) -> Optional[LexiconEntry]:
        row = self.conn.execute(
            "SELECT * FROM lexicon_entries WHERE term = ? AND book_id = ?", (term, book_id)
        ).fetchone()
        return _row_to_lexicon_entry(row) if row else None

    def list_lexicon_entries(self, book_id: Optional[int] = None) -> List[LexiconEntry]:
        if book_id is None:
            rows = self.conn.execute(
                "SELECT * FROM lexicon_entries WHERE book_id = 0 ORDER BY id"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM lexicon_entries WHERE book_id = ? OR book_id = 0 "
                "ORDER BY (book_id = 0), id",
                (book_id,),
            ).fetchall()
        return [_row_to_lexicon_entry(row) for row in rows]

    def delete_lexicon_entry(self, term: str, book_id: int = 0) -> None:
        cur = self.conn.execute(
            "DELETE FROM lexicon_entries WHERE term = ? AND book_id = ?", (term, book_id)
        )
        if cur.rowcount == 0:
            raise KeyError(f"no lexicon entry for '{term}' in this scope")
        self.conn.commit()
```

Note on the `list_lexicon_entries` SQL: `ORDER BY (book_id = 0), id` sorts rows where `book_id = 0` is `0` (false, i.e. book-scoped rows) before rows where it's `1` (true, i.e. global rows) — this is what puts book-scoped entries first, then global entries, each in insertion (`id`) order, matching the "book-scoped wins" precedence rule from the Global Constraints.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: all pass (18 total: 10 existing + 8 new)

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/models.py src/audiobookify/db.py tests/test_db.py
git commit -m "feat: LexiconEntry model and SQLite persistence"
```

---

### Task 2: Lexicon substitution module

**Files:**
- Create: `src/audiobookify/lexicon/__init__.py`
- Create: `src/audiobookify/lexicon/substitution.py`
- Test: `tests/test_lexicon_substitution.py`

**Interfaces:**
- Consumes: `LexiconEntry` (Task 1) — only reads `.term` and `.override_value`, so a plain object/namedtuple with those two attributes works for testing too.
- Produces: `apply_lexicon(text: str, entries: List[LexiconEntry]) -> str` in `audiobookify.lexicon.substitution`. Task 3 (pipeline wiring) calls this directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lexicon_substitution.py`:

```python
from dataclasses import dataclass

from audiobookify.lexicon.substitution import apply_lexicon


@dataclass
class _Entry:
    term: str
    override_value: str


def test_apply_lexicon_substitutes_whole_word_case_insensitive():
    entries = [_Entry(term="cadeyrn", override_value="KAH-dern")]

    result = apply_lexicon("Cadeyrn walked into the hall.", entries)

    assert result == "KAH-dern walked into the hall."


def test_apply_lexicon_does_not_substitute_partial_word_matches():
    entries = [_Entry(term="Ann", override_value="ANN-override")]

    result = apply_lexicon("Anne-Marie waved at Ann.", entries)

    assert result == "Anne-Marie waved at ANN-override."


def test_apply_lexicon_supports_multi_word_terms():
    entries = [_Entry(term="New York", override_value="Noo Yawk")]

    result = apply_lexicon("She flew to New York yesterday.", entries)

    assert result == "She flew to Noo Yawk yesterday."


def test_apply_lexicon_leaves_unmatched_text_unchanged():
    entries = [_Entry(term="Cadeyrn", override_value="KAH-dern")]

    result = apply_lexicon("Nothing to substitute here.", entries)

    assert result == "Nothing to substitute here."


def test_apply_lexicon_processes_entries_in_given_order_first_match_wins():
    # Simulates the "book-scoped before global" precedence rule from the plan's
    # Global Constraints: once the first entry substitutes a term, the text no
    # longer contains the original word, so a later entry for the same term
    # (case-insensitively) is a no-op rather than a second substitution.
    entries = [
        _Entry(term="Cadeyrn", override_value="book-specific-version"),
        _Entry(term="Cadeyrn", override_value="global-version"),
    ]

    result = apply_lexicon("Cadeyrn spoke.", entries)

    assert result == "book-specific-version spoke."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_lexicon_substitution.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.lexicon'`

- [ ] **Step 3: Write `src/audiobookify/lexicon/__init__.py`**

```python
```

- [ ] **Step 4: Write `src/audiobookify/lexicon/substitution.py`**

```python
import re
from typing import List


def apply_lexicon(text: str, entries: List) -> str:
    for entry in entries:
        pattern = r"\b" + re.escape(entry.term) + r"\b"
        text = re.sub(pattern, entry.override_value, text, flags=re.IGNORECASE)
    return text
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_lexicon_substitution.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/lexicon tests/test_lexicon_substitution.py
git commit -m "feat: pronunciation lexicon text substitution"
```

---

### Task 3: Wire lexicon substitution into the synthesis pipeline

**Files:**
- Modify: `src/audiobookify/synth/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Database.list_lexicon_entries(book_id)` (Task 1), `apply_lexicon(text, entries)` (Task 2).
- Produces: no signature change to `generate_book(book, engine, db, voice_params, device)` — it now also fetches and applies this book's lexicon entries (global + book-scoped) once per invocation, before chunking each chapter's text.

- [ ] **Step 1: Write the failing tests**

Read the current `tests/test_pipeline.py` first to see the existing `_make_book` helper and import list, then append:

```python
@requires_ffmpeg
def test_generate_book_applies_lexicon_substitution_before_synthesis(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    db.create_lexicon_entry(term="chapter", override_value="CHAP-ter", scope="global")

    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    generate_book(book, RecordingEngine(), db, {"voice_name": "fake"})

    assert any("CHAP-ter" in text for text in RecordingEngine.synthesized_texts)
    assert not any("Body of chapter" in text for text in RecordingEngine.synthesized_texts)
    db.close()


@requires_ffmpeg
def test_generate_book_works_normally_with_no_lexicon_entries(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)

    results = generate_book(book, FakeEngine(), db, {"voice_name": "fake"})

    assert results[0].status == ChapterStatus.DONE
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pipeline.py -v -k lexicon`
Expected: FAIL — `RecordingEngine.synthesized_texts` still contains the original `"Body of chapter 1."` text, not the substituted `"CHAP-ter"` form, because nothing calls `apply_lexicon` yet

- [ ] **Step 3: Modify `src/audiobookify/synth/pipeline.py`**

Replace the full file contents with:

```python
from typing import List

from ..db import Database
from ..lexicon.substitution import apply_lexicon
from ..models import Book, Chapter, ChapterStatus
from ..normalize.text import chunk_text
from ..postprocess.audio import concatenate_chunks, export_chapter_mp3


def generate_book(book: Book, engine, db: Database, voice_params: dict, device: str = "auto") -> List[Chapter]:
    results = []
    try:
        engine.load(device)
        profile_handle = engine.clone_profile(
            voice_params.get("reference_audio"), voice_params.get("reference_text"), voice_params
        )
        lexicon_entries = db.list_lexicon_entries(book.id)

        for chapter in db.list_chapters(book.id):
            if chapter.status == ChapterStatus.DONE:
                results.append(chapter)
                continue

            db.update_chapter_status(chapter.id, ChapterStatus.GENERATING)
            try:
                text = apply_lexicon(chapter.text, lexicon_entries)
                chunks = chunk_text(text, engine.max_chunk_chars())
                audio_chunks = [engine.synthesize(c, profile_handle, voice_params) for c in chunks]
                combined = concatenate_chunks(audio_chunks, engine.sample_rate())
                mp3_path = export_chapter_mp3(
                    combined, engine.sample_rate(), book.output_dir, chapter.order_index,
                    chapter.title, book.title, voice_params.get("voice_name", engine.name),
                )
                db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=mp3_path)
            except Exception as exc:
                db.update_chapter_status(chapter.id, ChapterStatus.FAILED, error=str(exc))

            results.append(db.get_chapter(chapter.id))
    finally:
        engine.teardown()

    return results
```

No import changes are needed in `tests/test_pipeline.py` — `Database`, `FakeEngine`, `ChapterStatus`, `generate_book`, and `requires_ffmpeg` are all already imported there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: all pass (4 total: 2 existing + 2 new)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: 89 passed, 1 deselected (74 baseline + 8 from Task 1 + 5 from Task 2 + 2 from Task 3)

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/synth/pipeline.py tests/test_pipeline.py
git commit -m "feat: apply pronunciation lexicon substitution before synthesis"
```

---

### Task 4: CLI `lexicon add` and `lexicon list`

**Files:**
- Modify: `src/audiobookify/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Database.create_lexicon_entry`/`list_lexicon_entries`/`find_book_by_source` (Task 1).
- Produces: `audiobookify lexicon add --term --respell [--book] [--notes] [--db]` and `audiobookify lexicon list [--book] [--db]` subcommands. Task 5 (`lexicon remove`) and Task 6 (`lexicon preview`) extend this same `lexicon` subparser group.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_lexicon_add_creates_global_entry(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Added pronunciation for 'Cadeyrn'" in captured.out

    db = Database(str(db_path))
    entry = db.find_lexicon_entry("Cadeyrn")
    assert entry is not None
    assert entry.override_value == "KAH-dern"
    assert entry.scope == "global"
    db.close()


def test_lexicon_add_creates_book_scoped_entry(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])

    cli.main([
        "lexicon", "add", "--term", "Cadeyrn", "--respell", "kah-DAIRN",
        "--book", str(md_path), "--db", str(db_path),
    ])

    db = Database(str(db_path))
    book = db.find_book_by_source(str(md_path.resolve()))
    entry = db.find_lexicon_entry("Cadeyrn", book_id=book.id)
    assert entry is not None
    assert entry.override_value == "kah-DAIRN"
    assert entry.scope == "book"
    db.close()


def test_lexicon_add_reports_unknown_book(tmp_path, capsys):
    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern",
            "--book", str(md_path), "--db", str(db_path),
        ])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "no book found" in captured.err
    assert "generate" in captured.err


def test_lexicon_add_rejects_empty_term(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "add", "--term", "  ", "--respell", "KAH-dern", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "must not be empty" in capsys.readouterr().err


def test_lexicon_add_rejects_duplicate_term_in_same_scope(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "different", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "already exists" in capsys.readouterr().err


def test_lexicon_list_shows_global_entries(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])
    capsys.readouterr()

    cli.main(["lexicon", "list", "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Cadeyrn" in captured.out
    assert "KAH-dern" in captured.out


def test_lexicon_list_shows_book_scoped_and_global_entries(tmp_path, capsys):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    cli.main(["lexicon", "add", "--term", "Global One", "--respell", "g-one", "--db", str(db_path)])
    cli.main([
        "lexicon", "add", "--term", "Book One", "--respell", "b-one",
        "--book", str(md_path), "--db", str(db_path),
    ])
    capsys.readouterr()

    cli.main(["lexicon", "list", "--book", str(md_path), "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Global One" in captured.out
    assert "Book One" in captured.out


def test_lexicon_list_reports_when_empty(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    cli.main(["lexicon", "list", "--db", str(db_path)])

    assert "No lexicon entries yet" in capsys.readouterr().out
```

`shutil` is already imported at the top of `tests/test_cli.py` (it defines `requires_ffmpeg` at module level) — reuse it, don't add a duplicate import.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v -k lexicon`
Expected: FAIL with `SystemExit` from argparse (`invalid choice: 'lexicon'`)

- [ ] **Step 3: Add the lexicon handlers, parser, and dispatch to `src/audiobookify/cli.py`**

Add these two handler functions after `_handle_voice_preview`:

```python
def _resolve_book_for_lexicon(db: Database, book_path: str):
    resolved = str(Path(book_path).resolve())
    book = db.find_book_by_source(resolved)
    if book is None:
        print(
            f"Error: no book found for '{resolved}'; run 'generate' at least once to register it",
            file=sys.stderr,
        )
        db.close()
        sys.exit(1)
    return book


def _handle_lexicon_add(args) -> None:
    if not args.term.strip():
        print("Error: lexicon term must not be empty", file=sys.stderr)
        sys.exit(1)
    args.term = args.term.strip()

    db = Database(args.db)
    book_id = 0
    scope = "global"
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id
        scope = "book"

    try:
        entry = db.create_lexicon_entry(
            term=args.term, override_value=args.respell, scope=scope,
            book_id=book_id, notes=args.notes,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)

    print(f"Added pronunciation for '{entry.term}' -> '{entry.override_value}' ({entry.scope} scope)")
    db.close()


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

Add the new subparser in `main()`, after the `voice` subparsers block and before `args = parser.parse_args(argv)`:

```python
    lexicon_parser = subparsers.add_parser("lexicon", help="Manage pronunciation overrides")
    lexicon_subparsers = lexicon_parser.add_subparsers(dest="lexicon_command", required=True)

    lexicon_add_parser = lexicon_subparsers.add_parser(
        "add", help="Add a pronunciation override for a term"
    )
    lexicon_add_parser.add_argument("--term", required=True)
    lexicon_add_parser.add_argument("--respell", required=True)
    lexicon_add_parser.add_argument("--book", default=None)
    lexicon_add_parser.add_argument("--notes", default=None)
    lexicon_add_parser.add_argument("--db", default="audiobookify.db")

    lexicon_list_parser = lexicon_subparsers.add_parser("list", help="List pronunciation overrides")
    lexicon_list_parser.add_argument("--book", default=None)
    lexicon_list_parser.add_argument("--db", default="audiobookify.db")
```

Add the dispatch branch, as a new `elif` alongside the existing `elif args.command == "voice":` block:

```python
    elif args.command == "lexicon":
        if args.lexicon_command == "add":
            _handle_lexicon_add(args)
        elif args.lexicon_command == "list":
            _handle_lexicon_list(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all pass (35 total: 27 existing + 8 new — `test_lexicon_add_creates_global_entry`, `test_lexicon_add_creates_book_scoped_entry`, `test_lexicon_add_reports_unknown_book`, `test_lexicon_add_rejects_empty_term`, `test_lexicon_add_rejects_duplicate_term_in_same_scope`, `test_lexicon_list_shows_global_entries`, `test_lexicon_list_shows_book_scoped_and_global_entries`, `test_lexicon_list_reports_when_empty`)

- [ ] **Step 5: Commit**

```bash
git add src/audiobookify/cli.py tests/test_cli.py
git commit -m "feat: audiobookify lexicon add/list CLI commands"
```

---

### Task 5: CLI `lexicon remove`

**Files:**
- Modify: `src/audiobookify/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Database.delete_lexicon_entry` (Task 1), `_resolve_book_for_lexicon` (Task 4).
- Produces: `audiobookify lexicon remove --term [--book] [--db]` subcommand.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_lexicon_remove_deletes_global_entry(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])
    capsys.readouterr()

    cli.main(["lexicon", "remove", "--term", "Cadeyrn", "--db", str(db_path)])

    assert "Removed" in capsys.readouterr().out
    db = Database(str(db_path))
    assert db.find_lexicon_entry("Cadeyrn") is None
    db.close()


def test_lexicon_remove_reports_missing_entry(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "remove", "--term", "Ghost", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "no lexicon entry" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v -k lexicon_remove`
Expected: FAIL with `SystemExit` from argparse (`invalid choice: 'remove'`)

- [ ] **Step 3: Add the handler, subparser, and dispatch to `src/audiobookify/cli.py`**

Add this handler after `_handle_lexicon_list`:

```python
def _handle_lexicon_remove(args) -> None:
    db = Database(args.db)
    book_id = 0
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id

    try:
        db.delete_lexicon_entry(args.term, book_id=book_id)
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)

    print(f"Removed pronunciation override for '{args.term}'")
    db.close()
```

Add the subparser in `main()`, after `lexicon_list_parser`'s definition:

```python
    lexicon_remove_parser = lexicon_subparsers.add_parser(
        "remove", help="Remove a pronunciation override"
    )
    lexicon_remove_parser.add_argument("--term", required=True)
    lexicon_remove_parser.add_argument("--book", default=None)
    lexicon_remove_parser.add_argument("--db", default="audiobookify.db")
```

Add the dispatch branch:

```python
        elif args.lexicon_command == "remove":
            _handle_lexicon_remove(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all pass (37 total: 35 existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add src/audiobookify/cli.py tests/test_cli.py
git commit -m "feat: audiobookify lexicon remove CLI command"
```

---

### Task 6: CLI `lexicon preview`

**Files:**
- Modify: `src/audiobookify/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Database.find_lexicon_entry`, `Database.find_voice_profile_by_name` (existing), `apply_lexicon` (Task 2), `ChatterboxEngine`/`FakeEngine`'s `load`/`clone_profile`/`synthesize`/`sample_rate`/`teardown`.
- Produces: `audiobookify lexicon preview --term [--sentence] [--voice-profile] [--out] [--device] [--db]` subcommand — synthesizes a carrier sentence (default, or `--sentence`) with the term's override substituted in, using the given voice profile (or the built-in default voice), writes a WAV file, prints its path.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_lexicon_preview_writes_audio_with_substitution_applied(tmp_path, monkeypatch):
    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "ChatterboxEngine", RecordingEngine)

    db_path = tmp_path / "test.db"
    out_path = tmp_path / "preview.wav"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])

    cli.main([
        "lexicon", "preview", "--term", "Cadeyrn", "--out", str(out_path), "--db", str(db_path),
    ])

    assert out_path.exists()
    assert any("KAH-dern" in text for text in RecordingEngine.synthesized_texts)


def test_lexicon_preview_uses_custom_sentence_when_given(tmp_path, monkeypatch):
    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "ChatterboxEngine", RecordingEngine)

    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])

    cli.main([
        "lexicon", "preview", "--term", "Cadeyrn", "--sentence", "Cadeyrn walked home alone.",
        "--db", str(db_path),
    ])

    assert any("KAH-dern walked home alone." in text for text in RecordingEngine.synthesized_texts)


def test_lexicon_preview_supports_voice_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"
    out_path = tmp_path / "preview.wav"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])

    cli.main([
        "lexicon", "preview", "--term", "Cadeyrn", "--voice-profile", "narrator-1",
        "--out", str(out_path), "--db", str(db_path),
    ])

    assert out_path.exists()


def test_lexicon_preview_reports_missing_entry(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "preview", "--term", "Ghost", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "no lexicon entry" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v -k lexicon_preview`
Expected: FAIL with `SystemExit` from argparse (`invalid choice: 'preview'`)

- [ ] **Step 3: Add the handler, subparser, and dispatch to `src/audiobookify/cli.py`**

Add this handler after `_handle_lexicon_remove`:

```python
def _handle_lexicon_preview(args) -> None:
    db = Database(args.db)
    entry = db.find_lexicon_entry(args.term)
    if entry is None:
        print(f"Error: no lexicon entry for '{args.term}'", file=sys.stderr)
        db.close()
        sys.exit(1)

    profile = None
    if args.voice_profile:
        profile = db.find_voice_profile_by_name(args.voice_profile)
        if profile is None:
            print(f"Error: no voice profile named '{args.voice_profile}'", file=sys.stderr)
            db.close()
            sys.exit(1)
    db.close()

    _check_reference_audio_present(profile)

    sentence = args.sentence or f"The pronunciation of {entry.term} is being previewed."
    text = apply_lexicon(sentence, [entry])

    engine = ChatterboxEngine()
    try:
        engine.load(args.device)
        handle = engine.clone_profile(
            profile.reference_audio_path if profile else None,
            profile.reference_transcript if profile else None,
            {},
        )
        audio = engine.synthesize(text, handle, _voice_params(profile) if profile else {})
        out_path = args.out or f"{entry.term}-pronunciation-preview.wav"
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(out_path, audio, engine.sample_rate())
    finally:
        engine.teardown()

    print(f"Wrote preview to {out_path}")
```

Add `from .lexicon.substitution import apply_lexicon` to the imports at the top of `cli.py`, alphabetized with the other local imports (after `from .ingest import epub, markdown` and before `from .models import ...`):

```python
from .lexicon.substitution import apply_lexicon
```

Add the subparser in `main()`, after `lexicon_remove_parser`'s definition:

```python
    lexicon_preview_parser = lexicon_subparsers.add_parser(
        "preview", help="Synthesize a sample sentence with a term's pronunciation override applied"
    )
    lexicon_preview_parser.add_argument("--term", required=True)
    lexicon_preview_parser.add_argument("--sentence", default=None)
    lexicon_preview_parser.add_argument("--voice-profile", default=None)
    lexicon_preview_parser.add_argument("--out", default=None)
    lexicon_preview_parser.add_argument("--device", default="auto")
    lexicon_preview_parser.add_argument("--db", default="audiobookify.db")
```

Add the dispatch branch:

```python
        elif args.lexicon_command == "preview":
            _handle_lexicon_preview(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all pass (41 total: 37 existing + 4 new)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: 103 passed, 1 deselected (89 baseline after Task 3 + 8 from Task 4 + 2 from Task 5 + 4 from Task 6)

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/cli.py tests/test_cli.py
git commit -m "feat: audiobookify lexicon preview CLI command"
```

---

### Task 7: README — M2 usage docs

**Files:**
- Modify: `README.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Update `README.md`**

Change:

```markdown
The pronunciation lexicon and the GUI are not yet implemented — see `project-spec.md` §9 for the milestone roadmap.
```

to:

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: M2 pronunciation lexicon usage instructions"
```

---

### Task 8: Final-review fix wave

**Context:** the whole-branch review of Tasks 1-7 found no Critical issues but three Important, real, empirically-verified bugs plus several Minor findings. This task fixes all of them in one pass.

**Files:**
- Modify: `src/audiobookify/cli.py`
- Modify: `src/audiobookify/lexicon/substitution.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`, `tests/test_lexicon_substitution.py`, `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_lexicon_substitution.py`:

```python
def test_apply_lexicon_prefers_longer_overlapping_term_regardless_of_order():
    entries_short_first = [
        _Entry(term="York", override_value="Yorrk"),
        _Entry(term="New York", override_value="Noo Yawk"),
    ]
    result = apply_lexicon("She flew to New York yesterday.", entries_short_first)
    assert result == "She flew to Noo Yawk yesterday."

    entries_long_first = [
        _Entry(term="New York", override_value="Noo Yawk"),
        _Entry(term="York", override_value="Yorrk"),
    ]
    result = apply_lexicon("She flew to New York yesterday.", entries_long_first)
    assert result == "She flew to Noo Yawk yesterday."
```

Append to `tests/test_pipeline.py`:

```python
@requires_ffmpeg
def test_generate_book_prefers_book_scoped_lexicon_entry_over_global(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    db.create_lexicon_entry(term="chapter", override_value="GLOBAL-version", scope="global")
    db.create_lexicon_entry(term="chapter", override_value="BOOK-version", scope="book", book_id=book.id)

    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    generate_book(book, RecordingEngine(), db, {"voice_name": "fake"})

    assert any("BOOK-version" in text for text in RecordingEngine.synthesized_texts)
    assert not any("GLOBAL-version" in text for text in RecordingEngine.synthesized_texts)
    db.close()
```

Append to `tests/test_cli.py`:

```python
def test_lexicon_add_rejects_empty_respelling(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "add", "--term", "Ghost", "--respell", "   ", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "must not be empty" in capsys.readouterr().err


def test_lexicon_add_warns_when_term_cannot_match_a_word_boundary(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    cli.main(["lexicon", "add", "--term", "Dr.", "--respell", "Doctor", "--db", str(db_path)])

    assert "word boundary" in capsys.readouterr().err


def test_lexicon_remove_strips_whitespace_from_term(tmp_path):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])

    cli.main(["lexicon", "remove", "--term", "  Cadeyrn  ", "--db", str(db_path)])

    db = Database(str(db_path))
    assert db.find_lexicon_entry("Cadeyrn") is None
    db.close()


def test_lexicon_preview_sanitizes_term_for_default_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)
    monkeypatch.chdir(tmp_path)

    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "AC/DC", "--respell", "AY-SEE-DEE-SEE", "--db", str(db_path)])

    cli.main(["lexicon", "preview", "--term", "AC/DC", "--db", str(db_path)])

    assert not (tmp_path / "AC").exists()  # no unwanted subdirectory created
    assert any(p.name.endswith("-pronunciation-preview.wav") for p in tmp_path.iterdir())


@requires_ffmpeg
def test_lexicon_preview_prefers_book_scoped_entry_over_global(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "ChatterboxEngine", RecordingEngine)

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

    cli.main(["lexicon", "preview", "--term", "Cadeyrn", "--book", str(md_path), "--db", str(db_path)])

    assert any("BOOK-version" in text for text in RecordingEngine.synthesized_texts)


@requires_ffmpeg
def test_lexicon_preview_falls_back_to_global_entry_when_book_scope_has_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "ChatterboxEngine", RecordingEngine)

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "GLOBAL-version", "--db", str(db_path)])

    cli.main(["lexicon", "preview", "--term", "Cadeyrn", "--book", str(md_path), "--db", str(db_path)])

    assert any("GLOBAL-version" in text for text in RecordingEngine.synthesized_texts)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_lexicon_substitution.py tests/test_pipeline.py tests/test_cli.py -v -k "overlapping_term or prefers_book_scoped_lexicon_entry_over_global or empty_respelling or word_boundary or strips_whitespace_from_term or sanitizes_term or prefers_book_scoped_entry_over_global or falls_back_to_global_entry"`
Expected: FAIL — the overlapping-term test fails because entries are applied in list order with no length sort; the pipeline test fails because there's no scope precedence bug at the unit level to exercise (it currently DOES pass by luck of ordering, so this one is a regression-guard rather than a RED — that's fine, note it and move on); the empty-respelling test fails because no validation exists; the word-boundary warning test fails because no warning is printed; the strips-whitespace test fails because `remove` doesn't strip; the sanitize test fails because `AC/DC` creates a real `AC/` directory; the two preview-scope tests fail because `lexicon preview` has no `--book` flag (argparse rejects it with `SystemExit(2)`)

- [ ] **Step 3: Fix `src/audiobookify/lexicon/substitution.py` — longest-match-first ordering**

Replace the full file contents with:

```python
import re
from typing import List


def apply_lexicon(text: str, entries: List) -> str:
    for entry in sorted(entries, key=lambda e: -len(e.term)):
        pattern = r"\b" + re.escape(entry.term) + r"\b"
        text = re.sub(pattern, lambda m: entry.override_value, text, flags=re.IGNORECASE)
    return text
```

(Python's `sorted()` is stable, so entries of equal term length keep their original relative order — this preserves the book-scoped-before-global precedence for same-length/identical terms while fixing the case where a genuinely longer term would otherwise be defeated by a shorter one matching first.)

- [ ] **Step 4: Fix `src/audiobookify/cli.py`**

Add `import re` to the top of the file, alphabetized with the other stdlib imports:

```python
import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional
```

Add this helper function after `_check_reference_audio_present`:

```python
def _sanitize_filename_component(value: str) -> str:
    return re.sub(r"[^\w.-]+", "_", value)
```

In `_handle_lexicon_add`, add the respelling validation and the word-boundary warning. Replace:

```python
def _handle_lexicon_add(args) -> None:
    if not args.term.strip():
        print("Error: lexicon term must not be empty", file=sys.stderr)
        sys.exit(1)
    args.term = args.term.strip()

    db = Database(args.db)
```

with:

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

    db = Database(args.db)
```

In `_handle_lexicon_remove`, strip the term for consistency with `add`. Replace:

```python
def _handle_lexicon_remove(args) -> None:
    db = Database(args.db)
    book_id = 0
```

with:

```python
def _handle_lexicon_remove(args) -> None:
    args.term = args.term.strip()

    db = Database(args.db)
    book_id = 0
```

And its error-message unwrap — replace:

```python
    try:
        db.delete_lexicon_entry(args.term, book_id=book_id)
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)
```

with:

```python
    try:
        db.delete_lexicon_entry(args.term, book_id=book_id)
    except KeyError as exc:
        print(f"Error: {exc.args[0]}", file=sys.stderr)
        db.close()
        sys.exit(1)
```

Replace the whole `_handle_lexicon_preview` function with:

```python
def _handle_lexicon_preview(args) -> None:
    args.term = args.term.strip()

    db = Database(args.db)
    book_id = 0
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id

    entry = db.find_lexicon_entry(args.term, book_id=book_id)
    if entry is None and book_id:
        entry = db.find_lexicon_entry(args.term)  # fall back to the global entry
    if entry is None:
        print(f"Error: no lexicon entry for '{args.term}'", file=sys.stderr)
        db.close()
        sys.exit(1)

    profile = None
    if args.voice_profile:
        profile = db.find_voice_profile_by_name(args.voice_profile)
        if profile is None:
            print(f"Error: no voice profile named '{args.voice_profile}'", file=sys.stderr)
            db.close()
            sys.exit(1)
    db.close()

    _check_reference_audio_present(profile)

    sentence = args.sentence or f"The pronunciation of {entry.term} is being previewed."
    text = apply_lexicon(sentence, [entry])

    engine = ChatterboxEngine()
    try:
        engine.load(args.device)
        handle = engine.clone_profile(
            profile.reference_audio_path if profile else None,
            profile.reference_transcript if profile else None,
            {},
        )
        audio = engine.synthesize(text, handle, _voice_params(profile) if profile else {})
        out_path = args.out or f"{_sanitize_filename_component(entry.term)}-pronunciation-preview.wav"
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(out_path, audio, engine.sample_rate())
    finally:
        engine.teardown()

    print(f"Wrote preview to {out_path}")
```

In `main()`, add `--book` to the `lexicon preview` subparser. Replace:

```python
    lexicon_preview_parser = lexicon_subparsers.add_parser(
        "preview", help="Synthesize a sample sentence with a term's pronunciation override applied"
    )
    lexicon_preview_parser.add_argument("--term", required=True)
    lexicon_preview_parser.add_argument("--sentence", default=None)
    lexicon_preview_parser.add_argument("--voice-profile", default=None)
    lexicon_preview_parser.add_argument("--out", default=None)
    lexicon_preview_parser.add_argument("--device", default="auto")
    lexicon_preview_parser.add_argument("--db", default="audiobookify.db")
```

with:

```python
    lexicon_preview_parser = lexicon_subparsers.add_parser(
        "preview", help="Synthesize a sample sentence with a term's pronunciation override applied"
    )
    lexicon_preview_parser.add_argument("--term", required=True)
    lexicon_preview_parser.add_argument("--sentence", default=None)
    lexicon_preview_parser.add_argument("--voice-profile", default=None)
    lexicon_preview_parser.add_argument("--book", default=None)
    lexicon_preview_parser.add_argument("--out", default=None)
    lexicon_preview_parser.add_argument("--device", default="auto")
    lexicon_preview_parser.add_argument("--db", default="audiobookify.db")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_lexicon_substitution.py tests/test_pipeline.py tests/test_cli.py -v`
Expected: all pass

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (104 baseline + 7 new tests = 111 passed, 1 deselected)

- [ ] **Step 7: Update `README.md`**

Replace:

```markdown
- `lexicon add`: substitution is whole-word, case-insensitive, and supports multi-word terms (`--term "New York"`). `--book <path>` scopes the entry to one already-registered book (run `generate` on it at least once first) instead of applying it everywhere; the same term can have both a global and a book-specific entry — the book-specific one wins for that book. Respellings should stick to plain letters/hyphens: the term is substituted before the normal number/abbreviation expansion runs, so a respelling containing digits or a period could be altered by that later step.
- `lexicon list` / `lexicon remove --term NAME`: pass `--book <path>` to see or remove a book-scoped entry instead of a global one.
- `lexicon preview --term NAME`: synthesizes a short carrier sentence (or `--sentence "..."`) with the term's override applied, optionally in a saved `--voice-profile`, and writes a WAV file (same convention as `voice preview` — no auto-playback).
- `generate` applies every applicable lexicon entry (global + that book's own) automatically — no flag needed.
```

with:

```markdown
- `lexicon add`: substitution is whole-word, case-insensitive, and supports multi-word terms (`--term "New York"`); when two terms overlap (e.g. "York" and "New York"), the longer one wins regardless of which was added first. `--book <path>` scopes the entry to one already-registered book (run `generate` on it at least once first) instead of applying it everywhere; the same term can have both a global and a book-specific entry — the book-specific one wins for that book. Respellings should stick to plain letters/hyphens: the term is substituted before the normal number/abbreviation expansion runs, so a respelling containing digits or a period could be altered by that later step. A term's replacement text can itself be matched by a later entry (e.g. a separate entry for "dern" would also match inside a "Cadeyrn" → "KAH dern" replacement) — keep respellings free of other registered terms if that's not the intent.
- `lexicon list` / `lexicon remove --term NAME`: pass `--book <path>` to see or remove a book-scoped entry instead of a global one.
- `lexicon preview --term NAME`: synthesizes a short carrier sentence (or `--sentence "..."`) with the term's override applied, optionally in a saved `--voice-profile` and/or `--book <path>` (to preview a book-specific override, falling back to the global one if the book has none), and writes a WAV file (same convention as `voice preview` — no auto-playback).
- `generate` applies every applicable lexicon entry (global + that book's own) automatically — no flag needed. Lexicon changes only affect chapters that haven't been generated yet; a chapter already marked done keeps its existing audio. Pass a fresh `--db` (or delete the chapter's MP3 and reset its status) to re-render with an updated pronunciation.
```

- [ ] **Step 8: Commit**

```bash
git add src/audiobookify/cli.py src/audiobookify/lexicon/substitution.py README.md \
        tests/test_cli.py tests/test_lexicon_substitution.py tests/test_pipeline.py
git commit -m "fix: address M2 whole-branch review findings

Fix three Important, empirically-verified bugs: lexicon preview
ignored --book and could silently preview the wrong scope's
pronunciation when both existed; an empty --respell was accepted and
silently deleted the term from every chapter of every book using it;
and overlapping multi-word terms (e.g. \"York\" vs \"New York\") were
substituted in list order rather than longest-match-first, so a
shorter term could preempt a more specific one regardless of scope.
Also: unwrap KeyError's repr in lexicon remove's error message, strip
whitespace from --term in remove/preview for consistency with add,
sanitize the term used in lexicon preview's default output filename
so a term like \"AC/DC\" can't create an unwanted directory, warn at
add time when a term can't ever match a word boundary, and document
cascading substitution and the mid-book lexicon-change caveat."
```

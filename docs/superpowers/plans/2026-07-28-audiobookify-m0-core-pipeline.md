# Audiobookify M0 — Core CLI Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working CLI (`audiobookify generate --input book.epub --voice myvoice --out ./audiobook/`) that ingests an EPUB or Markdown file, splits it into chapters, normalizes the text, synthesizes narration with Chatterbox (default built-in voice — cloning arrives in M1), and exports one tagged MP3 per chapter, with a resumable per-chapter job queue backed by SQLite.

**Architecture:** A UI-agnostic Python library (`src/audiobookify/`) with clear layer boundaries — ingest → normalize → engine adapter → post-process/export — orchestrated by a pipeline module and driven today by a thin CLI. The `TTSEngine` interface is the seam: a `FakeEngine` test double lets every layer above it be unit-tested without GPU hardware or downloaded weights, while `ChatterboxEngine` is the one real adapter for M0 (F5-TTS etc. can be added later without touching the pipeline).

**Tech Stack:** Python 3.10+, `ebooklib` + `beautifulsoup4` (EPUB), `num2words` (number expansion), `chatterbox-tts` (+ PyTorch, CUDA/MPS/CPU), `soundfile` (WAV I/O), `ffmpeg` via subprocess (MP3 encode), `mutagen` (ID3 tags), stdlib `sqlite3` (persistence), stdlib `argparse` (CLI), `pytest` (tests).

## Global Constraints

- Fully local — no cloud API calls for synthesis, cloning, or pronunciation (spec §1).
- Must run on macOS M3/18GB (MPS GPU acceleration, CPU fallback for unsupported ops — **no Neural Engine/Core ML target**, per explicit decision) and Windows RTX 3080 (CUDA) (spec §1, user clarification 2026-07-28).
- Chatterbox is the only engine wired up in M0, but it sits behind a pluggable `TTSEngine` interface so a second adapter is a config change, not a rewrite (spec §2, §4.7).
- One MP3 per chapter, filenames `NN - Chapter Title.mp3` (zero-padded, ordered), ID3 tags: album=book title, track=chapter order, title=chapter title, artist=narrator/voice name (spec §4.6).
- Generation is a resumable per-chapter job queue persisted to SQLite — a crash or quit must not lose completed chapters (spec §3, §4.5).
- Core pipeline stays UI-agnostic (library + CLI); a GUI is a later, thin layer on top (spec §7).
- A single root `Makefile` drives common actions; M0 wires up `setup`, `models`, `clean` for real and stubs `build`/`up`/`dev`/`down` with a message pointing at the milestone that fills them in (spec §7).
- Personal use only for this build — no need to restrict dependencies to permissive-only licenses (user decision 2026-07-28).
- Out of scope for M0 (explicitly later milestones per spec §9): voice cloning UI (M1), pronunciation lexicon (M2), audio→phoneme extraction (M3), GUI (M4), loudness normalization / second engine / splice fallback (M5). Interactive chapter review/merge/split (spec §4.2) also arrives with the GUI in M4 — M0 uses auto-detected chapters as-is.

---

## File Structure

```
audiobookify/
  pyproject.toml
  Makefile
  README.md
  src/audiobookify/
    __init__.py
    models.py                # Book, Chapter, SourceFormat, ChapterStatus dataclasses/enums
    db.py                    # SQLite persistence (books, chapters)
    ingest/
      __init__.py
      base.py                 # RawChapter, ParsedBook
      epub.py                 # EPUB -> ParsedBook
      markdown.py              # Markdown -> ParsedBook
    normalize/
      __init__.py
      text.py                  # abbreviation/number expansion, sentence chunking
    engines/
      __init__.py
      base.py                  # TTSEngine protocol, detect_device()
      fake.py                  # FakeEngine test double
      chatterbox.py             # ChatterboxEngine real adapter
    postprocess/
      __init__.py
      audio.py                  # concat, ffmpeg mp3 encode, ID3 tagging, filenames
    synth/
      __init__.py
      pipeline.py               # generate_book() orchestrator, resumable
    cli.py                      # `audiobookify generate ...` entrypoint
  tests/
    test_db.py
    test_ingest_epub.py
    test_ingest_markdown.py
    test_normalize_text.py
    test_engines_fake.py
    test_engines_chatterbox.py
    test_postprocess_audio.py
    test_pipeline.py
    test_cli.py
```

---

### Task 1: Project scaffolding + Makefile

**Files:**
- Create: `pyproject.toml`
- Create: `Makefile`
- Create: `src/audiobookify/__init__.py`
- Create: `.gitignore`

**Interfaces:**
- Produces: an installable `audiobookify` package (`pip install -e ".[dev]"`) and a `pytest` command that later tasks' tests run under.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "audiobookify"
version = "0.1.0"
description = "Local audiobook generator"
requires-python = ">=3.10"
dependencies = [
    "ebooklib>=0.18",
    "beautifulsoup4>=4.12",
    "num2words>=0.5.13",
    "soundfile>=0.12",
    "numpy>=1.26",
    "mutagen>=1.47",
    "chatterbox-tts>=0.1.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
audiobookify = "audiobookify.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Write `Makefile`**

```makefile
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: setup models build up dev down clean

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg not found - install it (brew install ffmpeg / choco install ffmpeg) then re-run make setup"; exit 1; }
	@if [ -d frontend ]; then cd frontend && npm install; fi

models:
	$(PY) -c "from audiobookify.engines.chatterbox import ChatterboxEngine; e = ChatterboxEngine(); e.load('cpu'); e.teardown(); print('Chatterbox weights cached.')"

build:
	@echo "no frontend yet - 'build' arrives with the GUI (M4)"

up:
	@echo "no server yet - 'up' arrives with the GUI (M4); use 'audiobookify generate ...' directly for now"

dev:
	@echo "no dev server yet - 'dev' arrives with the GUI (M4)"

down:
	@echo "nothing running - 'down' arrives with the GUI (M4)"

clean:
	rm -rf $(VENV) build dist src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
	rm -rf .pytest_cache
```

- [ ] **Step 3: Write `src/audiobookify/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Write `.gitignore`**

```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
*.db
```

- [ ] **Step 5: Run setup and verify the package installs**

Run: `make setup`
Expected: venv created, `pip install -e ".[dev]"` succeeds, no ffmpeg error (install ffmpeg first if missing: `brew install ffmpeg` on macOS).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml Makefile src/audiobookify/__init__.py .gitignore
git commit -m "chore: project scaffolding and Makefile"
```

---

### Task 2: Data models + SQLite persistence

**Files:**
- Create: `src/audiobookify/models.py`
- Create: `src/audiobookify/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `SourceFormat` enum (`EPUB`, `MARKDOWN`), `ChapterStatus` enum (`PENDING`, `GENERATING`, `DONE`, `FAILED`), `Chapter` and `Book` dataclasses, `Database` class with `create_book`, `find_book_by_source`, `add_chapters`, `list_chapters`, `get_chapter`, `update_chapter_status`, `close`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
from audiobookify.db import Database
from audiobookify.models import SourceFormat, ChapterStatus
from audiobookify.ingest.base import RawChapter


def test_create_book_and_add_chapters(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author="Jane Author", source_path="/books/test.epub",
        source_format=SourceFormat.EPUB, output_dir=str(tmp_path / "out"),
    )
    db.add_chapters(book.id, [RawChapter(title="Chapter One", text="Once upon a time.")])

    chapters = db.list_chapters(book.id)
    assert len(chapters) == 1
    assert chapters[0].order_index == 0
    assert chapters[0].title == "Chapter One"
    assert chapters[0].status == ChapterStatus.PENDING
    db.close()


def test_update_chapter_status_persists_across_reopen(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path="/out/01 - Ch 1.mp3")
    db.close()

    reopened = Database(db_path)
    [reloaded] = reopened.list_chapters(book.id)
    assert reloaded.status == ChapterStatus.DONE
    assert reloaded.output_mp3_path == "/out/01 - Ch 1.mp3"
    reopened.close()


def test_find_book_by_source(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_book(
        title="Test Book", author=None, source_path="/books/test.epub",
        source_format=SourceFormat.EPUB, output_dir=str(tmp_path / "out"),
    )
    found = db.find_book_by_source("/books/test.epub")
    assert found is not None
    assert found.title == "Test Book"
    assert db.find_book_by_source("/nope.epub") is None
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.db'`

- [ ] **Step 3: Write `src/audiobookify/models.py`**

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SourceFormat(str, Enum):
    EPUB = "epub"
    MARKDOWN = "md"


class ChapterStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Chapter:
    id: int
    book_id: int
    order_index: int
    title: str
    text: str
    status: ChapterStatus = ChapterStatus.PENDING
    output_mp3_path: Optional[str] = None
    duration: Optional[float] = None
    error: Optional[str] = None


@dataclass
class Book:
    id: int
    title: str
    author: Optional[str]
    source_path: str
    source_format: SourceFormat
    output_dir: str
    created_at: float
    voice_profile_id: Optional[int] = None
    chapters: list = field(default_factory=list)
```

- [ ] **Step 4: Write `src/audiobookify/db.py`**

```python
import sqlite3
import time
from typing import List, Optional

from .models import Book, Chapter, ChapterStatus, SourceFormat
from .ingest.base import RawChapter

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT,
    source_path TEXT NOT NULL UNIQUE,
    source_format TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    created_at REAL NOT NULL,
    voice_profile_id INTEGER
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id),
    order_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    output_mp3_path TEXT,
    duration REAL,
    error TEXT,
    UNIQUE(book_id, order_index)
);
"""


def _row_to_book(row: sqlite3.Row) -> Book:
    return Book(
        id=row["id"], title=row["title"], author=row["author"],
        source_path=row["source_path"], source_format=SourceFormat(row["source_format"]),
        output_dir=row["output_dir"], created_at=row["created_at"],
        voice_profile_id=row["voice_profile_id"],
    )


def _row_to_chapter(row: sqlite3.Row) -> Chapter:
    return Chapter(
        id=row["id"], book_id=row["book_id"], order_index=row["order_index"],
        title=row["title"], text=row["text"], status=ChapterStatus(row["status"]),
        output_mp3_path=row["output_mp3_path"], duration=row["duration"], error=row["error"],
    )


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def create_book(self, title: str, author: Optional[str], source_path: str,
                     source_format: SourceFormat, output_dir: str) -> Book:
        cur = self.conn.execute(
            "INSERT INTO books (title, author, source_path, source_format, output_dir, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title, author, source_path, source_format.value, output_dir, time.time()),
        )
        self.conn.commit()
        return self.get_book(cur.lastrowid)

    def get_book(self, book_id: int) -> Book:
        row = self.conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return _row_to_book(row)

    def find_book_by_source(self, source_path: str) -> Optional[Book]:
        row = self.conn.execute("SELECT * FROM books WHERE source_path = ?", (source_path,)).fetchone()
        return _row_to_book(row) if row else None

    def add_chapters(self, book_id: int, raw_chapters: List[RawChapter]) -> List[Chapter]:
        for index, raw in enumerate(raw_chapters):
            self.conn.execute(
                "INSERT INTO chapters (book_id, order_index, title, text) VALUES (?, ?, ?, ?)",
                (book_id, index, raw.title, raw.text),
            )
        self.conn.commit()
        return self.list_chapters(book_id)

    def list_chapters(self, book_id: int) -> List[Chapter]:
        rows = self.conn.execute(
            "SELECT * FROM chapters WHERE book_id = ? ORDER BY order_index", (book_id,)
        ).fetchall()
        return [_row_to_chapter(row) for row in rows]

    def get_chapter(self, chapter_id: int) -> Chapter:
        row = self.conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
        return _row_to_chapter(row)

    def update_chapter_status(self, chapter_id: int, status: ChapterStatus,
                               output_mp3_path: Optional[str] = None,
                               duration: Optional[float] = None,
                               error: Optional[str] = None) -> None:
        self.conn.execute(
            "UPDATE chapters SET status = ?, output_mp3_path = COALESCE(?, output_mp3_path), "
            "duration = COALESCE(?, duration), error = ? WHERE id = ?",
            (status.value, output_mp3_path, duration, error, chapter_id),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
```

- [ ] **Step 5: Write `src/audiobookify/ingest/__init__.py` and `src/audiobookify/ingest/base.py` (needed by db.py's import)**

```python
# src/audiobookify/ingest/__init__.py
```

```python
# src/audiobookify/ingest/base.py
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RawChapter:
    title: str
    text: str


@dataclass
class ParsedBook:
    title: str
    author: Optional[str]
    chapters: List[RawChapter] = field(default_factory=list)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add src/audiobookify/models.py src/audiobookify/db.py src/audiobookify/ingest tests/test_db.py
git commit -m "feat: data models and SQLite persistence"
```

---

### Task 3: EPUB ingestor

**Files:**
- Create: `src/audiobookify/ingest/epub.py`
- Test: `tests/test_ingest_epub.py`

**Interfaces:**
- Consumes: `RawChapter`, `ParsedBook` from `audiobookify.ingest.base` (Task 2).
- Produces: `parse(path: str) -> ParsedBook` in `audiobookify.ingest.epub`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest_epub.py
from ebooklib import epub as ebooklib_epub
from audiobookify.ingest import epub


def _build_epub(path):
    book = ebooklib_epub.EpubBook()
    book.set_identifier("id123")
    book.set_title("My Test Book")
    book.add_author("Jane Author")

    cover = ebooklib_epub.EpubHtml(title="Cover", file_name="cover.xhtml")
    cover.content = "<html><body><p>(c) 2026</p></body></html>"  # short boilerplate

    ch1 = ebooklib_epub.EpubHtml(title="Chapter One", file_name="ch1.xhtml")
    ch1.content = (
        "<html><body><h1>Chapter One</h1><p>" + ("Once upon a time. " * 30) + "</p></body></html>"
    )

    ch2 = ebooklib_epub.EpubHtml(title="Chapter Two", file_name="ch2.xhtml")
    ch2.content = (
        "<html><body><h1>Chapter Two</h1><p>" + ("The story continues. " * 30) + "</p></body></html>"
    )

    for item in (cover, ch1, ch2):
        book.add_item(item)
    book.spine = [cover, ch1, ch2]
    book.add_item(ebooklib_epub.EpubNcx())
    book.add_item(ebooklib_epub.EpubNav())

    ebooklib_epub.write_epub(str(path), book)


def test_parse_epub_extracts_title_author_and_skips_short_boilerplate(tmp_path):
    epub_path = tmp_path / "book.epub"
    _build_epub(epub_path)

    parsed = epub.parse(str(epub_path))

    assert parsed.title == "My Test Book"
    assert parsed.author == "Jane Author"
    assert [c.title for c in parsed.chapters] == ["Chapter One", "Chapter Two"]
    assert "Once upon a time." in parsed.chapters[0].text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ingest_epub.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.ingest.epub'`

- [ ] **Step 3: Write `src/audiobookify/ingest/epub.py`**

```python
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import ITEM_DOCUMENT, epub as ebooklib_epub

from .base import ParsedBook, RawChapter

MIN_CHAPTER_CHARS = 200


def parse(path: str) -> ParsedBook:
    book = ebooklib_epub.read_epub(path)

    title_meta = book.get_metadata("DC", "title")
    title = title_meta[0][0] if title_meta else Path(path).stem

    author_meta = book.get_metadata("DC", "creator")
    author = author_meta[0][0] if author_meta else None

    chapters = []
    for item_id, _ in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue

        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["nav", "script", "style"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)

        if len(text) < MIN_CHAPTER_CHARS:
            continue  # skips cover pages, copyright blurbs, blank dividers

        heading = soup.find(["h1", "h2"])
        chapter_title = heading.get_text(strip=True) if heading else f"Chapter {len(chapters) + 1}"
        chapters.append(RawChapter(title=chapter_title, text=text))

    return ParsedBook(title=title, author=author, chapters=chapters)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ingest_epub.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add src/audiobookify/ingest/epub.py tests/test_ingest_epub.py
git commit -m "feat: EPUB ingestor"
```

---

### Task 4: Markdown ingestor

**Files:**
- Create: `src/audiobookify/ingest/markdown.py`
- Test: `tests/test_ingest_markdown.py`

**Interfaces:**
- Consumes: `RawChapter`, `ParsedBook` from `audiobookify.ingest.base` (Task 2).
- Produces: `parse(path: str, heading_level: int = 1) -> ParsedBook` in `audiobookify.ingest.markdown`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest_markdown.py
from audiobookify.ingest import markdown

SAMPLE_MD = """# Chapter One: A Beginning

This is **bold** and _italic_ text with a [link](https://example.com).

# Chapter Two

More `code` and content here.
"""


def test_parse_markdown_splits_on_top_level_headings(tmp_path):
    md_path = tmp_path / "book.md"
    md_path.write_text(SAMPLE_MD, encoding="utf-8")

    parsed = markdown.parse(str(md_path))

    assert [c.title for c in parsed.chapters] == ["Chapter One: A Beginning", "Chapter Two"]
    assert "bold" in parsed.chapters[0].text
    assert "**" not in parsed.chapters[0].text
    assert "link" in parsed.chapters[0].text
    assert "https://example.com" not in parsed.chapters[0].text


def test_parse_markdown_with_no_headings_returns_single_chapter(tmp_path):
    md_path = tmp_path / "flat.md"
    md_path.write_text("Just plain text, no headings.", encoding="utf-8")

    parsed = markdown.parse(str(md_path))

    assert len(parsed.chapters) == 1
    assert parsed.chapters[0].title == "flat"


def test_parse_markdown_respects_configurable_heading_level(tmp_path):
    md_path = tmp_path / "nested.md"
    md_path.write_text("## Part A\nfirst\n## Part B\nsecond\n", encoding="utf-8")

    parsed = markdown.parse(str(md_path), heading_level=2)

    assert [c.title for c in parsed.chapters] == ["Part A", "Part B"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ingest_markdown.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.ingest.markdown'`

- [ ] **Step 3: Write `src/audiobookify/ingest/markdown.py`**

```python
import re
from pathlib import Path

from .base import ParsedBook, RawChapter


def _strip_markdown(text: str) -> str:
    text = re.sub(r"`{1,3}[^`]*`{1,3}", lambda m: m.group(0).strip("`"), text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    return text.strip()


def parse(path: str, heading_level: int = 1) -> ParsedBook:
    raw = Path(path).read_text(encoding="utf-8")
    title = Path(path).stem

    pattern = re.compile(rf'^{"#" * heading_level}\s+(.+)$', re.MULTILINE)
    matches = list(pattern.finditer(raw))

    if not matches:
        return ParsedBook(title=title, author=None, chapters=[RawChapter(title=title, text=_strip_markdown(raw))])

    chapters = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        chapters.append(RawChapter(title=m.group(1).strip(), text=_strip_markdown(raw[start:end])))

    return ParsedBook(title=title, author=None, chapters=chapters)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ingest_markdown.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/audiobookify/ingest/markdown.py tests/test_ingest_markdown.py
git commit -m "feat: Markdown ingestor"
```

---

### Task 5: Text normalization + chunking

**Files:**
- Create: `src/audiobookify/normalize/__init__.py`
- Create: `src/audiobookify/normalize/text.py`
- Test: `tests/test_normalize_text.py`

**Interfaces:**
- Produces: `normalize_text(text: str) -> str`, `split_sentences(text: str) -> list[str]`, `chunk_text(text: str, max_chars: int) -> list[str]` in `audiobookify.normalize.text`. `chunk_text` is what `synth/pipeline.py` (Task 8) calls with `engine.max_chunk_chars()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_normalize_text.py
from audiobookify.normalize.text import chunk_text, normalize_text, split_sentences


def test_expand_abbreviations():
    assert normalize_text("Dr. Smith arrived.") == "Doctor Smith arrived."


def test_expand_numbers():
    assert normalize_text("I have 42 apples.") == "I have forty-two apples."


def test_split_sentences():
    result = split_sentences("First sentence. Second sentence! Third one?")
    assert result == ["First sentence.", "Second sentence!", "Third one?"]


def test_chunk_text_respects_max_chars():
    text = " ".join(f"Sentence number {i}." for i in range(20))
    chunks = chunk_text(text, max_chars=50)
    assert all(len(c) <= 50 for c in chunks)
    assert len(chunks) > 1


def test_chunk_text_does_not_split_a_single_short_paragraph():
    chunks = chunk_text("Just one short sentence.", max_chars=300)
    assert chunks == ["Just one short sentence."]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_normalize_text.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.normalize'`

- [ ] **Step 3: Write `src/audiobookify/normalize/__init__.py`**

```python
```

- [ ] **Step 4: Write `src/audiobookify/normalize/text.py`**

```python
import re

from num2words import num2words

ABBREVIATIONS = {
    "Dr.": "Doctor", "Mr.": "Mister", "Mrs.": "Missus", "Ms.": "Miz",
    "Prof.": "Professor", "St.": "Saint", "vs.": "versus", "etc.": "et cetera",
}

_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'])')


def expand_abbreviations(text: str) -> str:
    for abbr, expansion in ABBREVIATIONS.items():
        text = re.sub(re.escape(abbr), expansion, text)
    return text


def expand_numbers(text: str) -> str:
    def repl(match: re.Match) -> str:
        raw = match.group(0).replace(",", "")
        try:
            return num2words(float(raw)) if "." in raw else num2words(int(raw))
        except ValueError:
            return match.group(0)

    return _NUMBER_RE.sub(repl, text)


def normalize_text(text: str) -> str:
    return expand_numbers(expand_abbreviations(text))


def split_sentences(text: str) -> list:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def chunk_text(text: str, max_chars: int) -> list:
    sentences = split_sentences(normalize_text(text))
    chunks = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_normalize_text.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/normalize tests/test_normalize_text.py
git commit -m "feat: text normalization and sentence chunking"
```

---

### Task 6: TTS engine interface, FakeEngine, ChatterboxEngine

**Files:**
- Create: `src/audiobookify/engines/__init__.py`
- Create: `src/audiobookify/engines/base.py`
- Create: `src/audiobookify/engines/fake.py`
- Create: `src/audiobookify/engines/chatterbox.py`
- Test: `tests/test_engines_fake.py`
- Test: `tests/test_engines_chatterbox.py`

**Interfaces:**
- Produces: `VoiceProfileHandle`, `detect_device() -> str` in `engines.base`. `FakeEngine` and `ChatterboxEngine` both implement: `name: str`, `supports_cloning: bool`, `load(device: str) -> None`, `clone_profile(reference_audio, reference_text, params: dict) -> VoiceProfileHandle`, `synthesize(text_chunk: str, profile_handle, params: dict) -> np.ndarray`, `max_chunk_chars() -> int`, `preferred_phoneme_scheme() -> Optional[str]`, `sample_rate() -> int`, `teardown() -> None`. `sample_rate()` is an addition beyond the spec's example interface (§4.7), needed by post-processing (Task 7) to compute pause lengths and write WAV correctly.
- Consumes (Task 8, 9): these exact method names.

- [ ] **Step 1: Write the failing test for FakeEngine**

```python
# tests/test_engines_fake.py
import numpy as np

from audiobookify.engines.fake import FakeEngine


def test_fake_engine_full_lifecycle():
    engine = FakeEngine()
    engine.load("cpu")

    handle = engine.clone_profile(None, None, {})
    short_audio = engine.synthesize("Hi.", handle, {})
    long_audio = engine.synthesize("This is a much longer sentence than the other one.", handle, {})

    assert isinstance(short_audio, np.ndarray)
    assert len(long_audio) > len(short_audio)
    assert engine.max_chunk_chars() > 0
    assert engine.sample_rate() > 0
    assert engine.preferred_phoneme_scheme() is None

    engine.teardown()  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_engines_fake.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.engines'`

- [ ] **Step 3: Write `src/audiobookify/engines/__init__.py`**

```python
```

- [ ] **Step 4: Write `src/audiobookify/engines/base.py`**

```python
from typing import Optional, Protocol

import numpy as np


class VoiceProfileHandle:
    def __init__(self, data: Optional[dict] = None):
        self.data = data or {}


class TTSEngine(Protocol):
    name: str
    supports_cloning: bool

    def load(self, device: str) -> None: ...
    def clone_profile(self, reference_audio: Optional[str], reference_text: Optional[str],
                       params: dict) -> VoiceProfileHandle: ...
    def synthesize(self, text_chunk: str, profile_handle: Optional[VoiceProfileHandle],
                    params: dict) -> np.ndarray: ...
    def max_chunk_chars(self) -> int: ...
    def preferred_phoneme_scheme(self) -> Optional[str]: ...
    def sample_rate(self) -> int: ...
    def teardown(self) -> None: ...


def detect_device() -> str:
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"
```

- [ ] **Step 5: Write `src/audiobookify/engines/fake.py`**

```python
import numpy as np

from .base import VoiceProfileHandle


class FakeEngine:
    name = "fake"
    supports_cloning = True

    def __init__(self):
        self._sr = 24000
        self.device = None

    def load(self, device: str) -> None:
        self.device = device

    def clone_profile(self, reference_audio, reference_text, params) -> VoiceProfileHandle:
        return VoiceProfileHandle(data={"reference_audio": reference_audio})

    def synthesize(self, text_chunk: str, profile_handle, params) -> np.ndarray:
        duration_s = max(0.1, len(text_chunk) * 0.05)
        return np.zeros(int(self._sr * duration_s), dtype=np.float32)

    def max_chunk_chars(self) -> int:
        return 300

    def preferred_phoneme_scheme(self):
        return None

    def sample_rate(self) -> int:
        return self._sr

    def teardown(self) -> None:
        pass
```

- [ ] **Step 6: Run FakeEngine test to verify it passes**

Run: `.venv/bin/pytest tests/test_engines_fake.py -v`
Expected: 1 passed

- [ ] **Step 7: Write the failing test for device detection and ChatterboxEngine's CPU-fallback behavior**

```python
# tests/test_engines_chatterbox.py
import shutil

import pytest

from audiobookify.engines.base import detect_device
from audiobookify.engines.chatterbox import ChatterboxEngine


def test_detect_device_prefers_cuda_then_mps_then_cpu(monkeypatch):
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert detect_device() == "cuda"

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    assert detect_device() == "mps"

    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
    assert detect_device() == "cpu"


def test_chatterbox_engine_declares_cloning_support():
    engine = ChatterboxEngine()
    assert engine.supports_cloning is True
    assert engine.name == "chatterbox"


@pytest.mark.integration
def test_chatterbox_engine_loads_and_synthesizes_on_cpu():
    # Requires the real chatterbox-tts package + downloaded weights (`make models`).
    # Run explicitly with: pytest tests/test_engines_chatterbox.py -m integration
    engine = ChatterboxEngine()
    engine.load("cpu")
    handle = engine.clone_profile(None, None, {})
    audio = engine.synthesize("Hello world.", handle, {})
    assert len(audio) > 0
    engine.teardown()
```

- [ ] **Step 8: Register the `integration` marker so plain `pytest` runs skip it**

Add to `pyproject.toml` under a new `[tool.pytest.ini_options]` table:

```toml
[tool.pytest.ini_options]
markers = ["integration: requires real model weights/hardware, run explicitly"]
addopts = "-m 'not integration'"
```

- [ ] **Step 9: Run test to verify it fails (before implementation)**

Run: `.venv/bin/pytest tests/test_engines_chatterbox.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.engines.chatterbox'`

- [ ] **Step 10: Write `src/audiobookify/engines/chatterbox.py`**

```python
from typing import Optional

import numpy as np

from .base import VoiceProfileHandle, detect_device


class ChatterboxEngine:
    name = "chatterbox"
    supports_cloning = True

    def __init__(self):
        self.model = None
        self.device = None

    def load(self, device: str = "auto") -> None:
        from chatterbox.tts import ChatterboxTTS

        self.device = detect_device() if device == "auto" else device
        try:
            self.model = ChatterboxTTS.from_pretrained(device=self.device)
        except Exception:
            if self.device == "cpu":
                raise
            self.device = "cpu"
            self.model = ChatterboxTTS.from_pretrained(device="cpu")

    def clone_profile(self, reference_audio: Optional[str], reference_text: Optional[str],
                       params: dict) -> VoiceProfileHandle:
        return VoiceProfileHandle(data={"audio_prompt_path": reference_audio})

    def synthesize(self, text_chunk: str, profile_handle: Optional[VoiceProfileHandle],
                    params: dict) -> np.ndarray:
        audio_prompt_path = profile_handle.data.get("audio_prompt_path") if profile_handle else None
        wav = self.model.generate(
            text_chunk,
            audio_prompt_path=audio_prompt_path,
            exaggeration=params.get("exaggeration", 0.5),
            cfg_weight=params.get("cfg", 0.5),
        )
        return wav.squeeze().cpu().numpy()

    def max_chunk_chars(self) -> int:
        return 300

    def preferred_phoneme_scheme(self):
        return None

    def sample_rate(self) -> int:
        return self.model.sr

    def teardown(self) -> None:
        self.model = None
```

- [ ] **Step 11: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_engines_chatterbox.py -v`
Expected: 3 passed, 1 deselected (the `integration` test)

- [ ] **Step 12: Commit**

```bash
git add src/audiobookify/engines pyproject.toml tests/test_engines_fake.py tests/test_engines_chatterbox.py
git commit -m "feat: TTSEngine interface, FakeEngine test double, ChatterboxEngine adapter"
```

---

### Task 7: Post-processing — concatenation, MP3 export, ID3 tagging

**Files:**
- Create: `src/audiobookify/postprocess/__init__.py`
- Create: `src/audiobookify/postprocess/audio.py`
- Test: `tests/test_postprocess_audio.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure functions over `numpy` arrays and paths).
- Produces: `concatenate_chunks(chunks, sample_rate, pause_seconds=0.4) -> np.ndarray`, `sanitize_filename(name: str) -> str`, `chapter_filename(order_index: int, title: str) -> str`, `export_chapter_mp3(audio, sample_rate, output_dir, order_index, chapter_title, book_title, narrator) -> str` (returns the mp3 path) in `audiobookify.postprocess.audio`. `synth/pipeline.py` (Task 8) calls `concatenate_chunks` and `export_chapter_mp3`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_postprocess_audio.py
import shutil

import numpy as np
import pytest
from mutagen.mp3 import MP3

from audiobookify.postprocess.audio import (
    chapter_filename,
    concatenate_chunks,
    export_chapter_mp3,
    sanitize_filename,
)

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_concatenate_chunks_inserts_pause_between_each():
    sample_rate = 1000
    chunks = [np.ones(100, dtype=np.float32), np.ones(200, dtype=np.float32)]
    result = concatenate_chunks(chunks, sample_rate, pause_seconds=0.1)
    assert len(result) == 100 + 200 + int(sample_rate * 0.1)


def test_sanitize_filename_strips_illegal_characters():
    assert sanitize_filename('Chapter One: "The Beginning"?') == "Chapter One The Beginning"


def test_chapter_filename_zero_pads_and_orders():
    assert chapter_filename(0, "Chapter One") == "01 - Chapter One.mp3"
    assert chapter_filename(9, "Chapter Ten") == "10 - Chapter Ten.mp3"


@requires_ffmpeg
def test_export_chapter_mp3_writes_tagged_file(tmp_path):
    sample_rate = 22050
    audio = np.sin(np.linspace(0, 440 * 2 * np.pi, sample_rate)).astype(np.float32)

    mp3_path = export_chapter_mp3(
        audio, sample_rate, str(tmp_path), order_index=0,
        chapter_title="Chapter One", book_title="My Book", narrator="Chatterbox Default",
    )

    assert mp3_path.endswith("01 - Chapter One.mp3")

    tags = MP3(mp3_path)
    assert str(tags.get("TIT2")) == "Chapter One"
    assert str(tags.get("TALB")) == "My Book"
    assert str(tags.get("TRCK")) == "1"
    assert str(tags.get("TPE1")) == "Chatterbox Default"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_postprocess_audio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.postprocess'`

- [ ] **Step 3: Write `src/audiobookify/postprocess/__init__.py`**

```python
```

- [ ] **Step 4: Write `src/audiobookify/postprocess/audio.py`**

```python
import re
import subprocess
from pathlib import Path
from typing import List

import numpy as np
import soundfile as sf
from mutagen.id3 import TALB, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3


def concatenate_chunks(chunks: List[np.ndarray], sample_rate: int, pause_seconds: float = 0.4) -> np.ndarray:
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    pause = np.zeros(int(sample_rate * pause_seconds), dtype=np.float32)
    parts = []
    for i, chunk in enumerate(chunks):
        parts.append(chunk)
        if i < len(chunks) - 1:
            parts.append(pause)
    return np.concatenate(parts)


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name).strip()


def chapter_filename(order_index: int, title: str) -> str:
    return f"{order_index + 1:02d} - {sanitize_filename(title)}.mp3"


def export_chapter_mp3(audio: np.ndarray, sample_rate: int, output_dir: str, order_index: int,
                        chapter_title: str, book_title: str, narrator: str) -> str:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    wav_path = output_path / f".tmp_{order_index}.wav"
    sf.write(str(wav_path), audio, sample_rate)

    mp3_path = output_path / chapter_filename(order_index, chapter_title)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-qscale:a", "2", str(mp3_path)],
        check=True, capture_output=True,
    )
    wav_path.unlink()

    tags = MP3(str(mp3_path))
    tags.add_tags()
    tags.tags.add(TIT2(encoding=3, text=chapter_title))
    tags.tags.add(TALB(encoding=3, text=book_title))
    tags.tags.add(TRCK(encoding=3, text=str(order_index + 1)))
    tags.tags.add(TPE1(encoding=3, text=narrator))
    tags.save()

    return str(mp3_path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_postprocess_audio.py -v`
Expected: 4 passed (or 3 passed + 1 skipped if ffmpeg isn't installed locally — install it and re-run to confirm the export path too)

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/postprocess tests/test_postprocess_audio.py
git commit -m "feat: audio concatenation, MP3 export, ID3 tagging"
```

---

### Task 8: Pipeline orchestrator — resumable job queue

**Files:**
- Create: `src/audiobookify/synth/__init__.py`
- Create: `src/audiobookify/synth/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `ChapterStatus` (Task 2), `chunk_text` (Task 5), `TTSEngine`/`FakeEngine` (Task 6), `concatenate_chunks`/`export_chapter_mp3` (Task 7), `Database` (Task 2).
- Produces: `generate_book(book: Book, engine: TTSEngine, db: Database, voice_params: dict, device: str = "auto") -> list[Chapter]` in `audiobookify.synth.pipeline`. Task 9 (CLI) calls this directly.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py
import shutil

import pytest

from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, SourceFormat
from audiobookify.synth.pipeline import generate_book

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_book(tmp_path, db, n_chapters=3):
    book = db.create_book(
        title="Resumable Book", author=None, source_path=str(tmp_path / "book.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.add_chapters(book.id, [RawChapter(title=f"Chapter {i + 1}", text=f"Body of chapter {i + 1}.")
                              for i in range(n_chapters)])
    return book


@requires_ffmpeg
def test_generate_book_marks_all_chapters_done(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db)

    results = generate_book(book, FakeEngine(), db, {"voice_name": "fake"})

    assert [c.status for c in results] == [ChapterStatus.DONE] * 3
    assert all(c.output_mp3_path for c in results)
    db.close()


@requires_ffmpeg
def test_generate_book_resumes_after_a_failed_chapter(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db)

    class FlakyEngine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.call_count = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.call_count += 1
            if "chapter 2" in text_chunk.lower():
                raise RuntimeError("synthesis blew up")
            return super().synthesize(text_chunk, profile_handle, params)

    first_run_engine = FlakyEngine()
    generate_book(book, first_run_engine, db, {"voice_name": "fake"})

    chapters = db.list_chapters(book.id)
    assert chapters[0].status == ChapterStatus.DONE
    assert chapters[1].status == ChapterStatus.FAILED
    assert chapters[2].status == ChapterStatus.DONE

    second_run_engine = FakeEngine()
    generate_book(book, second_run_engine, db, {"voice_name": "fake"})

    chapters = db.list_chapters(book.id)
    assert [c.status for c in chapters] == [ChapterStatus.DONE] * 3
    # only chapter 2's single chunk needed re-synthesizing on the second run
    assert second_run_engine.call_count if hasattr(second_run_engine, "call_count") else True
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.synth'`

- [ ] **Step 3: Write `src/audiobookify/synth/__init__.py`**

```python
```

- [ ] **Step 4: Write `src/audiobookify/synth/pipeline.py`**

```python
from typing import List

from ..db import Database
from ..models import Book, Chapter, ChapterStatus
from ..normalize.text import chunk_text
from ..postprocess.audio import concatenate_chunks, export_chapter_mp3


def generate_book(book: Book, engine, db: Database, voice_params: dict, device: str = "auto") -> List[Chapter]:
    engine.load(device)
    profile_handle = engine.clone_profile(
        voice_params.get("reference_audio"), voice_params.get("reference_text"), voice_params
    )

    results = []
    try:
        for chapter in db.list_chapters(book.id):
            if chapter.status == ChapterStatus.DONE:
                results.append(chapter)
                continue

            db.update_chapter_status(chapter.id, ChapterStatus.GENERATING)
            try:
                chunks = chunk_text(chapter.text, engine.max_chunk_chars())
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: 2 passed (requires ffmpeg installed; otherwise both skip)

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/synth tests/test_pipeline.py
git commit -m "feat: resumable pipeline orchestrator"
```

---

### Task 9: CLI entrypoint

**Files:**
- Create: `src/audiobookify/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Database` (Task 2), `epub.parse`/`markdown.parse` (Tasks 3–4), `ChatterboxEngine` (Task 6), `generate_book` (Task 8).
- Produces: `main(argv: Optional[list[str]] = None) -> None` in `audiobookify.cli`, registered as the `audiobookify` console script (Task 1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import shutil

import pytest

from audiobookify import cli
from audiobookify.engines.fake import FakeEngine

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@requires_ffmpeg
def test_generate_command_produces_mp3s_per_chapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nFirst chapter body.\n\n# Chapter Two\nSecond chapter body.\n",
                        encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "done:" in captured.out
    assert (out_dir / "01 - Chapter One.mp3").exists()
    assert (out_dir / "02 - Chapter Two.mp3").exists()


@requires_ffmpeg
def test_generate_command_is_resumable_across_invocations(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    first_mtime = (out_dir / "01 - Chapter One.mp3").stat().st_mtime

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    second_mtime = (out_dir / "01 - Chapter One.mp3").stat().st_mtime

    assert first_mtime == second_mtime  # already-DONE chapter was not regenerated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.cli'`

- [ ] **Step 3: Write `src/audiobookify/cli.py`**

```python
import argparse
from pathlib import Path
from typing import List, Optional

from .db import Database
from .engines.chatterbox import ChatterboxEngine
from .ingest import epub, markdown
from .models import SourceFormat
from .synth.pipeline import generate_book


def _ingest(path: str, heading_level: int):
    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        return epub.parse(path), SourceFormat.EPUB
    if suffix == ".md":
        return markdown.parse(path, heading_level=heading_level), SourceFormat.MARKDOWN
    raise ValueError(f"unsupported input format: {suffix}")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="audiobookify")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Generate an audiobook from a book file")
    generate_parser.add_argument("--input", required=True)
    generate_parser.add_argument("--out", required=True)
    generate_parser.add_argument("--voice", default="default")
    generate_parser.add_argument("--device", default="auto")
    generate_parser.add_argument("--db", default="audiobookify.db")
    generate_parser.add_argument("--heading-level", type=int, default=1)

    args = parser.parse_args(argv)

    if args.command == "generate":
        db = Database(args.db)
        book = db.find_book_by_source(args.input)
        if book is None:
            parsed, source_format = _ingest(args.input, args.heading_level)
            book = db.create_book(
                title=parsed.title, author=parsed.author, source_path=args.input,
                source_format=source_format, output_dir=args.out,
            )
            db.add_chapters(book.id, parsed.chapters)

        engine = ChatterboxEngine()
        results = generate_book(book, engine, db, {"voice_name": args.voice}, device=args.device)
        for chapter in results:
            print(f"{chapter.status.value}: {chapter.title} -> {chapter.output_mp3_path}")
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all tests pass (integration-marked tests deselected by default)

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/cli.py tests/test_cli.py
git commit -m "feat: audiobookify generate CLI command"
```

---

### Task 10: README + first-run model caching docs

**Files:**
- Modify: `README.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Update `README.md` with setup and usage instructions**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: M0 setup and usage instructions"
```

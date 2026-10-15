# Tauri Desktop Phase 2 (Rust Text Pipeline) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port EPUB/Markdown ingest, text normalization, chunking, lexicon substitution and respelling into the `desktop/core` crate, test-first, and prove with golden tests that Rust turns the same books into the same chapters and the same chunks as today's Python, byte for byte.

**Architecture:** Two new modules in `audiobookify-core`: `ingest` (`mod.rs`, `markdown.rs`, `epub.rs`, `html.rs`) and `text` (`normalize.rs`, `num2words.rs`, `chunk.rs`, `lexicon.rs`, `respelling.rs`), plus `pystr.rs`, which holds the Python string and regex semantics both modules need (whitespace, `\w`, `\b`, code-point lengths). A one-off Python script (`tests/fixtures/text/generate.py`) runs today's pipeline and writes JSON goldens. Committed goldens cover synthetic books, unit-level cases and one public-domain book; goldens for three of the user's copyrighted books stay local and git-ignored. Nothing calls the new modules at runtime yet: the Python app keeps doing all of this until phase 3.

**Tech Stack:** Rust stable (cargo 1.98), `regex` 1, `fancy-regex` 0.19 (lookaround), `html5ever` 0.39 (tokenizer only; Tauri already depends on 0.39), `roxmltree` 0.21 (OPF/container XML), `zip` 9 (deflate only), `num-bigint` 0.5 + `num-integer` 0.1 + `num-traits` 0.2 (num2words up to 10^306), `serde_json` (already a dependency) for reading goldens. Python side: the repo `.venv` (num2words 0.5.14, ebooklib 0.20, beautifulsoup4 4.15, lxml 6.1.3 with libxml2 2.14.6).

**Spec:** `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md`, phase 2 and the Risks section.

**Branch:** `feat/tauri-phase2-text`, off `main` (phases 0 and 1 are merged).

## Global Constraints

- macOS on Apple Silicon only.
- **Parity is byte for byte.** For every committed fixture and every local book, Rust must produce exactly the chapter titles, chapter texts, chunk texts and `ends_with` values Python produced. Where this plan's code and a golden disagree, the golden wins: fix the Rust, never the golden.
- Python behavior that looks wrong is ported as is (see "Kept for parity" below). Fixing it is user-visible and belongs to a later change made in both languages.
- Nothing under `src/audiobookify/` and nothing under `desktop/src/` changes in this phase.
- Goldens are only ever written by `generate.py`. Never hand-edit a JSON fixture.
- Goldens for copyrighted books go to `desktop/core/tests/fixtures/text/local/`, which is git-ignored. Never commit them, and never commit anything from `uploads/`.
- Count characters with `pystr::len` (code points), never `str::len` (bytes). Python's `len()` counts code points, and every bound (`max_chars`, `min_chars`, `MIN_CHAPTER_CHARS`) is in code points.
- In regexes, never write a bare `\s`, `\w` or `\b`. Use `[{WS}]`, `[{WORD}]` and `{BOUNDARY}` from `pystr` (Python and Rust disagree on all three; the Gutenberg fixture fails without them).
- Use the `regex` crate unless the pattern needs lookaround. Only `NUMBER_RE`, `SENTENCE_SPLIT_RE` and the lexicon pattern use `fancy-regex`.
- Error text copied word for word from Python: `unsupported input format: {suffix}`.
- TDD: each task writes its tests first and watches them fail before writing the implementation.
- Run every cargo command from `desktop/`. In a worktree, the repo `.venv` and `uploads/` exist only in the main checkout: Task 1 sets `MAIN` to it.

## Decisions

| Fork | Choice | Why |
|---|---|---|
| EPUB HTML parsing | html5ever's **tokenizer** plus a small tree builder that follows Python `html.parser`'s rules, not `scraper`/HTML5 tree construction (the spec's module map says `scraper`) | Calibre writes empty headings as `<h1 id=".." title=".."/>`. `html.parser` treats that as an empty element, so Python's chapter title is `""` (Butcher's Crossing has two). An HTML5 tree builder ignores the `/` and leaves the `<h1>` open around the whole page, which changed the title in 4 of 27 Butcher's Crossing documents. The tokenizer is still the right one: ebooklib actually parses with libxml2 2.14, whose tokenizer follows HTML5 (see next row). |
| What Python actually parses | Reproduce ebooklib's `EpubHtml.get_content()` effects, not BeautifulSoup over the raw file | ebooklib parses each page with lxml (libxml2), keeps only `<body>`'s children, and re-serializes with `pretty_print=True` before BeautifulSoup sees it. Consequences the port reproduces: text before `<body>`'s first child is dropped, anything after `</body>` is dropped, unknown entities keep their `;`, CR/CRLF become LF, NUL becomes U+FFFD, CDATA is a comment, declared encodings are ignored (decoded as UTF-8, bad bytes become U+FFFD), and pretty-printing puts a space between `<span>a</span><span>b</span>` when no ancestor has text. On the 7 uploaded books (309 spine documents), raw-file parsing and the real pipeline give identical text, so only the synthetic fixtures exercise these rules. |
| num2words | Hand port of num2words 0.5.14's English cardinal and float code with `num-bigint` | Its phrasing is specific ("one hundred and five", "one thousand, nine hundred and eighty-four", "twenty-seven point five" for `27.50`, digits read one by one after "point"), and real books hit 13-digit ISBNs. The port follows `splitnum`/`clean`/`merge` step by step; 1,814 number cases in `cases.json` check it. |
| Python regex classes | `pystr::{WS, WORD, BOUNDARY}` | Python's `\w` is `isalnum()` or `_`, so `½` is a word character: "paying 4½ per cent" in Gutenberg #1661 stays "4½" in Python and became "four½" with Rust's `\b`. Python's `\s` and `str.split()` also count U+001C..U+001F as whitespace. `cases.json` carries Python's exact whitespace and `\w` sets to test against. |
| Parity samples | Local: Trust, The Abbess, The Winds of Winter (user's choice, 2026-10-15). Committed: Project Gutenberg #1661 (The Adventures of Sherlock Holmes), 7 ebooklib-built EPUBs (the Python tests' books), 1 hand-written EPUB, 4 Markdown files, `cases.json` | No uploaded book can be committed. The Gutenberg EPUB is real producer output that every test run checks; the hand-written EPUB covers what ebooklib can't produce (Calibre self-closing tags, a minified page, invalid UTF-8, odd manifests). |
| Golden size | About 5 MB of JSON committed (Gutenberg golden 2.1 MB, `cases.json` 2.6 MB) | Plain JSON keeps diffs readable when the Python pipeline changes on purpose. |
| Lexicon API | `apply_lexicon(text, impl IntoIterator<Item = (&str, &str)>)` | Tests and goldens need no `LexiconEntry` rows; phase 3 passes `entries.iter().map(\|e\| (e.term.as_str(), e.override_value.as_str()))`. |
| Lexicon speed | A plain case-insensitive search skips terms that aren't in the text before the fancy-regex pass | `BOUNDARY`'s lookarounds put matching on fancy-regex's slow path (1.1 s for 8 entries over a 1M-character book in release); the prefilter is exact because a whole-word match is also a plain match. |
| Where Python raises | Rust returns a value and documents it (see "Known divergences") | Python crashes on these inputs, so they can't be goldens and no imported book contains them. |
| Lenient EPUB reading | Rust reads only spine documents and doesn't parse the nav document | ebooklib eagerly reads every manifest file and requires `nav[epub:type=toc]`, so it raises on books Rust can read. Those books can't be imported today, so this changes no existing output. |

## Kept for parity (follow-ups, not this phase)

These are today's behavior. The port keeps them; fixing them later changes what gets spoken, so it needs its own change in Python and Rust together.

- Abbreviations are replaced as plain substrings: "devs." becomes "deversus".
- Heading strings are stripped and glued: `<h1>Chapter <em>One</em></h1>` gives the title "ChapterOne".
- Calibre's empty `<h1/>` gives an empty chapter title (two chapters in Butcher's Crossing).
- ebooklib's pretty-printing turns `<p><span>a</span><span>b</span></p>` into "a b" in minified pages, and text before `<body>`'s first child is dropped.
- Markdown emphasis stripping mangles `* * *` scene breaks into `  *`.

## Known divergences (Rust-only behavior, not in any golden)

| Input | Python | Rust |
|---|---|---|
| A digit run of 10^306 or more | `OverflowError` (the chapter fails) | Left as written |
| Non-ASCII digits (`٣`) | Expanded by `int()` | Left as written |
| Empty `<dc:title/>` | Title `None`, so `create_book` fails | File stem |
| Manifest file missing, or nav without `epub:type="toc"` | ebooklib raises | Reads the spine documents |
| Minified page (no whitespace in `<body>`) with invalid nesting | libxml2 auto-closes tags that `html.parser` keeps open, which can move pretty-print spaces | Follows `html.parser`; 0 of 309 uploaded spine documents are minified |
| Code points unassigned in Python 3.12's Unicode 15 | Not `\w` | Rust's Unicode 16 tables may call them letters (the `word_class_is_pythons` test allows exactly these) |

---

## File structure

| File | Responsibility |
|---|---|
| `.gitignore` | Gains `desktop/core/tests/fixtures/text/local/`. |
| `desktop/core/Cargo.toml` | The new dependencies. |
| `desktop/core/src/lib.rs` | Gains `ingest`, `pystr`, `text`. |
| `desktop/core/src/pystr.rs` | Python whitespace, `\w`, `\b`, `strip`, `" ".join(s.split())`, `len`. |
| `desktop/core/src/text/mod.rs` | Module list and public re-exports. |
| `desktop/core/src/text/respelling.rs` | `phonemes_to_respelling`. |
| `desktop/core/src/text/lexicon.rs` | `apply_lexicon`. |
| `desktop/core/src/text/num2words.rs` | num2words' English cardinal and float rules. |
| `desktop/core/src/text/normalize.rs` | Abbreviations, `expand_numbers`, `normalize_text`, `split_sentences`, `split_paragraphs`. |
| `desktop/core/src/text/chunk.rs` | `Chunk`, `EndsWith`, `chunk_text` with clause splitting, short-chunk merging and interrupted-line closing. |
| `desktop/core/src/ingest/mod.rs` | `ParsedBook`, `IngestError`, `ingest_by_path`. |
| `desktop/core/src/ingest/markdown.rs` | Markdown -> chapters. |
| `desktop/core/src/ingest/html.rs` | One spine document -> paragraph text and first heading (tokenizer, tree, ebooklib effects). |
| `desktop/core/src/ingest/epub.rs` | Zip, container, OPF, spine -> chapters. |
| `desktop/core/tests/fixture/mod.rs` | Fixture paths, JSON loading, `assert_same` for long strings. |
| `desktop/core/tests/fixtures/text/generate.py` | Writes every fixture and golden below from the Python pipeline. |
| `desktop/core/tests/fixtures/text/epub/*.epub` | 7 ebooklib-built books, `handmade.epub`, `gutenberg-1661.epub`. |
| `desktop/core/tests/fixtures/text/md/*.md` | 4 Markdown books. |
| `desktop/core/tests/fixtures/text/golden/*.json` | One golden per book: metadata, chapters, chunks at 300/80 (with lexicon) and 120/30. |
| `desktop/core/tests/fixtures/text/cases.json` | Unit-level cases: numbers, sentences, paragraphs, 420 `chunk_text` calls, lexicon, respelling, Python's whitespace and `\w` sets. |
| `desktop/core/tests/fixtures/text/local/` | Git-ignored goldens for the user's books. |
| `desktop/core/tests/{respelling,lexicon_substitution,normalize_text,ingest_markdown,ingest_epub,ingest_dispatch}.rs` | Ports of the Python test files. |
| `desktop/core/tests/text_cases.rs` | Unit-level parity against `cases.json`. |
| `desktop/core/tests/golden_books.rs` | End-to-end parity: ingest -> lexicon -> chunk over every golden. |

### Test map (Python -> Rust)

All Python tests in these files are ported, names unchanged without the `test_` prefix.
- `test_ingest_dispatch.py` (2) -> `ingest_dispatch.rs` (+1 new: suffix case-insensitive).
- `test_ingest_markdown.py` (3) -> `ingest_markdown.rs` (+1 new: CRLF and CR).
- `test_ingest_epub.py` (5) -> `ingest_epub.rs` (+1 new: ebooklib's spine rules on `handmade.epub`). The Python tests build EPUBs with ebooklib at test time; the Rust tests read the same books as committed fixtures.
- `test_normalize_text.py` (35) -> `normalize_text.rs` (+2 new: num2words phrasing, Python word boundaries). `split_sentences` keeps its name as `split_sentences_basic`. The random-text property test keeps its bounds with its own generator; `cases.json` holds the Python `Random(1234)` stream's exact outputs.
- `test_lexicon_substitution.py` (7) -> `lexicon_substitution.rs`.
- `test_respelling.py` (4) -> `respelling.rs`.
- `test_chapter_breaks.py`: nothing left for this phase. `book_paragraphs` and `replace_chapter_breaks` were ported in phase 1 (`tests/books.rs`), `relabel_chapter_mp3` is audio (phase 3), and the rest are API tests (phase 4).

### Execution order

1 through 9, in order. Each task's tests compile only against the previous tasks' code.

---

### Task 1: Golden generator and fixtures

**Files:**
- Modify: `.gitignore`
- Create: `desktop/core/tests/fixtures/text/generate.py`
- Create (generated): `desktop/core/tests/fixtures/text/{epub/*.epub, md/*.md, golden/*.json, cases.json}`
- Create (downloaded): `desktop/core/tests/fixtures/text/epub/gutenberg-1661.epub`

**Interfaces:**
- Produces: the fixture tree above. Golden JSON shape, which `golden_books.rs` (Task 9) reads:
  `{"source": "epub/x.epub" | "/abs/path", "heading_level": 1, "format": "epub" | "md", "title": str, "author": str | null, "chapters": [{"title", "text"}], "lexicon": [[term, value]], "chunkings": [{"max_chars", "min_chars", "lexicon": bool, "chapters": [[[chunk_text, ends_with]]]}]}`
- `cases.json` keys: `python_whitespace` (code points), `python_word` and `python_unassigned` (`[first, last]` ranges), `numbers`, `normalize` (`[input, output]`), `sentences`, `paragraphs` (`[input, [outputs]]`), `chunks` (`[text, max, min, [[text, ends_with]]]`), `lexicon` (`[text, [[term, value]], output]`), `respelling` (`[input, output]`).

- [ ] **Step 1: Find the main checkout**

The `.venv` and `uploads/` live only in the main checkout. Run from the worktree root, and keep this shell for Steps 4-7:
```bash
MAIN=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)") && ls "$MAIN/.venv/bin/python" "$MAIN/uploads" >/dev/null && echo "$MAIN"
```
Expected: the main checkout's path, e.g. `/Users/nickrohner/Documents/Code/audiobookify`. The venv has `audiobookify` installed in editable mode from `$MAIN/src`; that's fine because this phase changes no Python.

- [ ] **Step 2: Ignore local goldens**

Append to `.gitignore`:
```
desktop/core/tests/fixtures/text/local/
```

- [ ] **Step 3: Write `desktop/core/tests/fixtures/text/generate.py`**

```python
"""Golden fixtures for the Rust text pipeline in desktop/core (Tauri port, phase 2).

Runs today's Python ingest -> lexicon -> normalize -> chunk_text and writes what it
produces as JSON. The Rust tests must reproduce every value byte for byte. Rerun this
only when the Python pipeline changes on purpose, and commit the result.

    .venv/bin/python desktop/core/tests/fixtures/text/generate.py
    .venv/bin/python desktop/core/tests/fixtures/text/generate.py --local BOOK.epub ...

--local writes goldens for books that must not be committed (uploads/ is
git-ignored and the books are copyrighted) into local/, which is git-ignored too.
"""
import argparse
import json
import random
import re
import sys
import unicodedata
import zipfile
from collections import namedtuple
from pathlib import Path

from ebooklib import epub as ebooklib_epub

from audiobookify.ingest import ingest_by_path
from audiobookify.lexicon.substitution import apply_lexicon
from audiobookify.normalize.text import chunk_text, expand_numbers, normalize_text, split_paragraphs, split_sentences
from audiobookify.phoneme.respelling import phonemes_to_respelling

HERE = Path(__file__).resolve().parent
Entry = namedtuple("Entry", "term override_value")

# Applied before chunking, as synth/pipeline.py does. Terms a book lacks are no-ops.
# "Mr." never matches (\b after "." needs a word character next) and its value checks
# literal replacement; "the" -> "the" lowercases every "The", which moves sentence
# boundaries and stresses the splitter.
LEXICON = [
    ["Holmes", "HOHMZ"], ["Baker Street", "BAY-ker street"], ["Watson", "WOT-sun"],
    ["Abbess", "AB-ess"], ["Winterfell", "WIN-ter-fel"], ["Diaz", "DEE-ahs"],
    ["Mr.", "literal $1 and \\1"], ["the", "the"],
]
# (max_chars, min_chars, apply LEXICON first). 300/80 is what Turbo and Qwen3 ship
# with; 120/30 forces far more clause and word splits through the same code.
CHUNKINGS = [(300, 80, True), (120, 30, False)]

FILLER = "The rain kept on falling over the quiet town all through the night. " * 4
LONG = "The air was heavy over the marsh, and the reeds bent low under the wind from the sea. "


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def chunks_json(chunks):
    return [[c.text, c.ends_with] for c in chunks]


def book_golden(path, source, heading_level=1):
    parsed, source_format = ingest_by_path(str(path), heading_level)
    entries = [Entry(t, v) for t, v in LEXICON]
    return {
        "source": source,
        "heading_level": heading_level,
        "format": source_format.value,
        "title": parsed.title,
        "author": parsed.author,
        "chapters": [{"title": c.title, "text": c.text} for c in parsed.chapters],
        "lexicon": LEXICON,
        "chunkings": [
            {"max_chars": mx, "min_chars": mn, "lexicon": lex,
             "chapters": [chunks_json(chunk_text(apply_lexicon(c.text, entries) if lex else c.text, mx, mn))
                          for c in parsed.chapters]}
            for mx, mn, lex in CHUNKINGS
        ],
    }


# --- EPUBs written by ebooklib, the same way the Python tests build theirs ---

EBOOKLIB_BOOKS = {
    "basic": ("My Test Book", "Jane Author", [
        ("Cover", "<p>(c) 2026</p>"),
        ("Chapter One", "<h1>Chapter One</h1><p>" + "Once upon a time. " * 30 + "</p>"),
        ("Chapter Two", "<h1>Chapter Two</h1><p>" + "The story continues. " * 30 + "</p>"),
    ]),
    "blocks": ("Blocks", None, [("Chapter One", f"<h1>Chapter One</h1><p>{FILLER}</p><p>Second paragraph.</p>")]),
    "inline": ("Inline", None, [("Chapter One",
        f"<p>{FILLER}</p><p>He was <em>very</em> tired,\n   <span>so</span> he <a href='#n1'>slept</a>.</p>")]),
    "br": ("Br", None, [("Chapter One", f"<p>{FILLER}</p><p>Roses are red<br/>violets are blue.</p>")]),
    "scene": ("Scene", None, [("Chapter One",
        "<h1>Chapter One</h1>" f"<p>{FILLER}</p>" "<p>“<i>No.</i>”</p>" "<p class='break'>* * *</p>"
        "<p>Morning came, and with it a <em>terrible</em> silence that settled over the whole valley below.</p>")]),
    "dashes": ("Dashes", None, [("Chapter One",
        f"<p>{LONG}“The air is bad there, and if we were to—”</p>"
        f"<p>{LONG}They could build a city, or an aqueduct, or a—</p>"
        f"<p>{LONG}“But, Cosimo, only look–’</p>"
        f"<p>{LONG}He stopped short--</p>"
        f"<p>{LONG}The end came quickly.</p>")]),
    "dashes_inside": ("Dashes", None, [("Chapter One",
        f"<p>{LONG}She paused—only for a moment—and went on.</p><p>{LONG * 2}</p>")]),
}


def write_ebooklib_epub(path, title, author, docs):
    book = ebooklib_epub.EpubBook()
    book.set_identifier(f"id-{path.stem}")
    book.set_title(title)
    if author:
        book.add_author(author)
    items = []
    for i, (doc_title, body) in enumerate(docs):
        item = ebooklib_epub.EpubHtml(title=doc_title, file_name=f"ch{i}.xhtml")
        item.content = f"<html><body>{body}</body></html>"
        book.add_item(item)
        items.append(item)
    book.spine = items
    book.add_item(ebooklib_epub.EpubNcx())
    book.add_item(ebooklib_epub.EpubNav())
    path.parent.mkdir(parents=True, exist_ok=True)
    ebooklib_epub.write_epub(str(path), book)


# --- A hand-written EPUB with the markup ebooklib won't produce (it re-serializes
# content): Calibre's self-closing tags, odd manifests, entities, BOM, CRLF. ---

XHTML = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
         '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title><style>p {{}}</style></head>\n'
         '<body>{}</body></html>')
HANDMADE_DOCS = {
    # Calibre writes empty headings as <h1 .../>: the title is "", not the page text.
    "Text/calibre one.xhtml": XHTML.format(
        '<h1 id="h" title="Title Page" class="calibre1"/>\n'
        '<p class="c"><span class="x"/>First&nbsp;line &amp; more&#8212;and &#x2014; <a id="p1"/>text.</p>\n'
        '<div>Loose <b>div</b> text<p>nested para</p>tail of div</div><p>A<p>B</p>C</p>'
        f'<p>{FILLER}</p>'),
    "Text/two.xhtml": XHTML.format(
        '<h2>Part<!-- x --> <em>Two</em><br/>Again<![CDATA[!]]></h2>'
        '<section>Loose section text<figure>fig text</figure></section>'
        '<table><tr><td>cell1</td><td>cell2</td></tr></table>'
        '<p>ruby <ruby>漢<rt>kan</rt></ruby> and <template>tmpl</template> end</p>'
        '<script>if (a<b) {}</script><nav><p>nav text</p></nav>'
        '<p>Price: 1,000.50 dollars, ISBN 9780593420317, code 0801, 27.50, 80,000.3, COVID-19, pages 12-34b, the 1980-90s, 3-2.</p>'
        '<p>Dr. Smith vs. Mr. Jones met Mrs. Ng at St. Paul’s, etc. FIRST. The 5-year-old said 7 more.</p>'
        '<p>“Wait—”</p><p>* * *</p><p>The End.</p>'
        f'<p>{FILLER}</p>'),
    "Text/short.xhtml": XHTML.format('<p>(c) 2026</p>'),
    "Text/plain.html": XHTML.format(f'<h1>Plain HTML</h1><p>{FILLER}</p>'),
    "Text/aside.xhtml": XHTML.format(f'<h2>Aside</h2><p>one<br>two</br>three</p><p>{FILLER}</p>'),
    # No whitespace between tags: lxml's pretty-printer adds it, so "<span>a</span><span>b</span>"
    # reads "a b", and text before <body>'s first child is dropped.
    "Text/minified.xhtml": '<html><body>Lead text<div><p><span>Small</span><span>Caps</span> stay glued.</p>'
                           '<p><span>a</span><span>b</span></p><h1><span>Chapter</span><span>Nine</span></h1>'
                           f'<p><em>x</em><!-- c --><img src="i.png"/><em>y</em></p><p>{FILLER}</p></div></body></html>'
                           '<p>after body</p>',
    "Text/bytes.xhtml": XHTML.format(f'<h1>Bad\x00bytes</h1><p>caf\xe9 and \xff end</p><p>{FILLER}</p>'),
    "Text/bom.xhtml": "﻿" + XHTML.format(f'<h1>Byte\r\nOrder</h1>\r\n<p>{FILLER}</p>\r\n').replace("\n", "\r\n"),
    # The nav document is skipped even though it is in the spine (ebooklib: ITEM_NAVIGATION).
    "nav.xhtml": XHTML.format('<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc"><ol>'
                              f'<li><a href="Text/two.xhtml">Two</a></li></ol></nav><p>{FILLER}</p>'),
}
HANDMADE_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">handmade-1</dc:identifier>
    <dc:title>Handmade Book</dc:title>
    <dc:title>Ignored Second Title</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="one" href="Text/calibre%20one.xhtml" media-type="application/xhtml+xml"/>
    <item id="two" href="Text/../Text/two.xhtml" media-type="application/xhtml+xml"/>
    <item id="short" href="Text/short.xhtml" media-type="application/xhtml+xml"/>
    <item id="plain" href="Text/plain.html" media-type="text/html"/>
    <item id="aside" href="Text/aside.xhtml" media-type="application/xhtml+xml"/>
    <item id="bom" href="Text/bom.xhtml" media-type="application/xhtml+xml"/>
    <item id="minified" href="Text/minified.xhtml" media-type="application/xhtml+xml"/>
    <item id="bytes" href="Text/bytes.xhtml" media-type="application/xhtml+xml"/>
    <item id="coverpage" href="Text/plain.html" media-type="application/xhtml+xml" properties="cover"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="nav"/>
    <itemref idref="one"/>
    <itemref idref="short"/>
    <itemref idref="plain"/>
    <itemref idref="missing"/>
    <itemref idref="two"/>
    <itemref idref="aside" linear="no"/>
    <itemref idref="bom"/>
    <itemref idref="minified"/>
    <itemref idref="bytes"/>
    <itemref idref="coverpage"/>
  </spine>
</package>
"""
CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""
NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/><docTitle><text>Handmade Book</text></docTitle><navMap/></ncx>
"""


def write_handmade_epub(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", HANDMADE_OPF, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", NCX, compress_type=zipfile.ZIP_DEFLATED)
        for name, body in HANDMADE_DOCS.items():
            # latin-1 for the invalid-UTF-8 page: its \xe9 and \xff must stay single bytes.
            data = body.encode("latin-1") if name == "Text/bytes.xhtml" else body.encode("utf-8")
            z.writestr(f"OEBPS/{name}", data, compress_type=zipfile.ZIP_DEFLATED)


MARKDOWN = {
    "basic.md": "# Chapter One: A Beginning\n\nThis is **bold** and _italic_ text with a [link](https://example.com).\n\n"
                "# Chapter Two\n\nMore `code` and content here.\n",
    "flat.md": "Just plain text, no headings.",
    "nested.md": "## Part A\nfirst\n## Part B\nsecond\n",
    "messy.md": "﻿Preamble before any heading.\r\n\r\n# One  \r\nSee ![a map](map.png) and [the docs](http://x.y).\r\n"
                "```\r\ncode block\r\n```\r\n***Bold italic*** and __under__ and *a*b*.\r\n\r\n#NoSpace heading\r\n"
                "#   Two\rOld Mac line\r\n\r\n### deep heading\r\n\r\nDr. Who has 3 cats.\r\n\r\n* * *\r\n\r\nEnd.\r\n",
}
MARKDOWN_LEVELS = {"nested.md": 2}


# --- Unit-level cases ---

def number_inputs():
    rng = random.Random(2026)
    raws = [str(n) for n in range(0, 1101)]
    raws += [str(rng.randrange(10 ** k)) for k in range(4, 40)] + [str(rng.randrange(10 ** k)) for k in range(40, 307, 11)]
    raws += [str(10 ** k) for k in range(0, 306, 3)] + [str(10 ** 306 - 1), str(10 ** 306)]
    raws += ["0801", "06", "007", "10,000", "30,000,000", "1,,2", "9780593420317", "2021018182"]
    raws += ["1.5", "27.50", "80,000.3", "19.15", "0.1", "1.05", "3.14159", "2.675", "0.00001", "0.0000001",
             "1.10", "5.0", "10.00", "12345678901234567.0", "9007199254740993.00", "0.999999999999999999",
             "123456789.123", "1." + "0" * 30 + "1", "0." + "0" * 40 + "7"]
    for _ in range(500):
        whole = rng.randrange(10 ** rng.randint(1, 15))
        frac = "".join(rng.choice("0123456789") for _ in range(rng.randint(1, 18)))
        raws.append(f"{whole}.{frac}")
    texts = [f"Take {r} now." for r in raws]
    texts += ["She had COVID-19 last year.", "Room 7 - the attic.", "A 5-year-old, a 10-minute break.",
              "See pages 12-34.", "They won 3-2.", "See pages 12-34b for details.", "The 1980-90s recession.",
              "Table 3-2a shows it.", "Version 1.2.3 shipped.", "a1b2 c3", "the 1,000,000th visitor",
              "x-7 and -7 and 7-", "Chapters 1-3 cover it.", "1,000.5,000", "4.5.6.7", "12,", "_7_", "7_000",
              "3½ cups", "10x faster", "Q3 2026", "007: 0.5% of 1,234.5"]
    return texts


def number_cases():
    cases, skipped = [], 0
    for text in number_inputs():
        try:
            cases.append([text, expand_numbers(text)])
        except OverflowError:  # num2words above 10^306; Rust leaves those as written
            skipped += 1
    return cases, skipped


def random_text(rng):
    words = ["the", "a", "river", "old", "mill", "she", "said", "quietly", "darkness", "No", "Yes",
             "Dr.", "7", "1,200", "“Go!”", "(it", "was)", "—", "half-", "3.5", "St."]
    punct = [".", ".", "!", "?", ",", ";", ":", "", "", "—", "…", "”", "'"]
    paragraphs = []
    for _ in range(rng.randint(1, 8)):
        sentences = []
        for _ in range(rng.randint(1, 6)):
            body = " ".join(rng.choice(words) + rng.choice(punct) for _ in range(rng.randint(1, 40)))
            sentences.append(body.rstrip(".,;!?") + rng.choice([".", "?", "!", "—", ".”"]))
        paragraphs.append(" ".join(sentences))
        if rng.random() < 0.1:
            paragraphs.append(rng.choice(["* * *", "#", "⁂", "~~~"]))
    return rng.choice(["\n\n", "\n \n", "\n\n\n"]).join(paragraphs)


def chunk_cases():
    fixed = [
        ("Just one short sentence.", 300, 0), ("Six went home. 7 more came.", 20, 0),
        ("First paragraph here.\n\nSecond paragraph here.", 300, 0),
        ("Aaaa aaaa aaaa. Bbbb bbbb bbbb. Cccc cccc cccc.", 32, 0),
        ("He walked to the old mill, slowly; the river ran past it, cold and grey.", 50, 0),
        ("He walked to the old mill by the river, and the water ran past it cold and grey.", 50, 0),
        ("Yes; he walked to the old mill by the river and the water ran past it cold and grey all day.", 60, 20),
        ('"No."\n\nShe looked at him for a long moment before answering.', 300, 20),
        ("She looked at him for a long moment.\n\nNo.", 300, 20),
        ("Chapter One\n\nIt was a dark and stormy night.", 300, 20),
        ("No.\n\nYes.\n\nMaybe.\n\nThat settles it then, I suppose.", 300, 15),
        ("Hi.", 300, 80), ("* * *", 300, 0),
        ("The door closed behind him for good.\n\n* * *\n\nMorning came slowly over the hills.", 300, 10),
        ("The door closed behind him for good.\n\nThe end.\n\n#\n\nMorning came slowly over the hills.", 300, 20),
        ("The door closed behind him for good.\n\n* * *\n\nThe End.", 300, 20),
        (" ".join(["word"] * 200), 100, 0), ("x" * 500 + " tail", 100, 20), ("", 300, 80),
        (" ".join(f"Sentence number {i}." for i in range(20)), 50, 0),
    ]
    cases = [[t, mx, mn, chunks_json(chunk_text(t, mx, mn))] for t, mx, mn in fixed]
    rng = random.Random(1234)
    for mx, mn in [(120, 30)] * 200 + [(300, 80)] * 100 + [(50, 10)] * 50 + [(80, 0)] * 50:
        text = random_text(rng)
        cases.append([text, mx, mn, chunks_json(chunk_text(text, mx, mn))])
    return cases


def lexicon_cases():
    cases = [
        ("Cadeyrn walked into the hall.", [["cadeyrn", "KAH-dern"]]),
        ("Anne-Marie waved at Ann.", [["Ann", "ANN-override"]]),
        ("She flew to New York yesterday.", [["New York", "Noo Yawk"]]),
        ("Nothing to substitute here.", [["Cadeyrn", "KAH-dern"]]),
        ("Cadeyrn spoke.", [["Cadeyrn", "book-specific-version"], ["Cadeyrn", "global-version"]]),
        ("Cadeyrn spoke.", [["Cadeyrn", "KAH\\dern"]]),
        ("Cadeyrn spoke.", [["Cadeyrn", "$1 ${0} \\g<0>"]]),
        ("She flew to New York yesterday.", [["York", "Yorrk"], ["New York", "Noo Yawk"]]),
        ("She flew to New York yesterday.", [["New York", "Noo Yawk"], ["York", "Yorrk"]]),
        ("ÉLODIE and élodie and Élodie.", [["élodie", "ay-LO-dee"]]),
        ("Mr. Smith and C++ and a.b.", [["Mr.", "Mister"], ["C++", "see plus plus"], ["a.b", "A B"]]),
        ("Straße and STRASSE.", [["straße", "STRAH-seh"]]),
        ("Ab ab abab", [["ab", "x"], ["b", "y"]]),
        ("one two", [["", "_"]]),
        ("Zoë's café.", [["Zoë", "ZOH-ee"], ["café", "ka-FAY"]]),
    ]
    out = []
    for text, entries in cases:
        out.append([text, entries, apply_lexicon(text, [Entry(t, v) for t, v in entries])])
    return out


def respelling_cases():
    rng = random.Random(7)
    symbols = ["p", "b", "tʃ", "dʒ", "ə", "oʊ", "eɪ", "ɹ", "iː", "ŋ", "?", "x", "ʔ"]
    inputs = ["k ə d eɪ ɹ n", "k ə ? n", "", "h ə l oʊ", "tʃ ɪ p", "dʒ ʌ m p", "  t ə \u001c n  "]
    inputs += [" ".join(rng.choice(symbols) for _ in range(rng.randint(1, 12))) for _ in range(50)]
    return [[p, phonemes_to_respelling(p)] for p in inputs]


def char_ranges(pred):
    """[[first, last], ...] code point ranges where pred holds."""
    ranges = []
    for c in range(0x110000):
        if pred(chr(c)):
            if ranges and ranges[-1][1] == c - 1:
                ranges[-1][1] = c
            else:
                ranges.append([c, c])
    return ranges


def cases():
    numbers, skipped = number_cases()
    print(f"numbers: {len(numbers)} cases ({skipped} skipped: Python raises above 10^306)")
    paragraph_inputs = ["One\nstill one.\n\n  Two.  \n \n\nThree.", "Line one.\nLine two.", "\n\n", "",
                        "a\n\u001c\nb", "a\n \nb", "a\r\n\r\nb", "x   y\n\t\nz"]
    sentence_inputs = ["First sentence. Second sentence! Third one?", '"I\'m leaving." She turned away.',
                       "“Go now!” He ran. (It was late.) Then dawn came.", 'He waited. "Hello?" No answer.',
                       "'Why?' he asked. She shrugged.", "Wait… 3 more. ‘Yes.’ [Note.] Fine.", "e.g. this. no split",
                       "A. B. C.\u001cD.", "End?!” “Start.", "Ellipsis... Then."]
    normalize_inputs = ["Dr. Smith arrived.", "I have 42 apples.", "Mrs. and Mr. and Ms. and Prof.",
                        "FIRST. Fast. St. devs. vs. etc. etc.", "Prof.Dr. 3 cats", "Mr.Mrs."]
    return {
        "python_whitespace": [c for c in range(0x110000) if chr(c).isspace()],
        # re's \w, and the code points this Python's Unicode tables leave unassigned (a
        # newer Unicode in Rust may call some of those letters).
        "python_word": char_ranges(lambda ch: re.match(r"\w", ch) is not None),
        "python_unassigned": char_ranges(lambda ch: unicodedata.category(ch) == "Cn"),
        "numbers": numbers,
        "normalize": [[t, normalize_text(t)] for t in normalize_inputs],
        "sentences": [[t, split_sentences(t)] for t in sentence_inputs],
        "paragraphs": [[t, split_paragraphs(t)] for t in paragraph_inputs],
        "chunks": chunk_cases(),
        "lexicon": lexicon_cases(),
        "respelling": respelling_cases(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local", nargs="+", metavar="BOOK", help="books whose goldens go to local/ (not committed)")
    args = parser.parse_args()

    if args.local:
        for book in args.local:
            path = Path(book).resolve()
            write(HERE / "local" / f"{path.name}.json", book_golden(path, str(path)))
            print(f"local/{path.name}.json")
        return

    for name, (title, author, docs) in EBOOKLIB_BOOKS.items():
        write_ebooklib_epub(HERE / "epub" / f"{name}.epub", title, author, docs)
    write_handmade_epub(HERE / "epub" / "handmade.epub")
    for name, text in MARKDOWN.items():
        (HERE / "md").mkdir(exist_ok=True)
        (HERE / "md" / name).write_bytes(text.encode("utf-8"))

    sources = sorted((HERE / "epub").glob("*.epub")) + sorted((HERE / "md").glob("*.md"))
    for path in sources:
        source = path.relative_to(HERE).as_posix()
        write(HERE / "golden" / f"{path.name}.json", book_golden(path, source, MARKDOWN_LEVELS.get(path.name, 1)))
        print(f"golden/{path.name}.json")
    write(HERE / "cases.json", cases())
    print("cases.json")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Download the Gutenberg book**

The user approved committing this public-domain EPUB (2026-10-15).
```bash
curl -sSL -o desktop/core/tests/fixtures/text/epub/gutenberg-1661.epub https://www.gutenberg.org/ebooks/1661.epub.noimages && shasum -a 256 desktop/core/tests/fixtures/text/epub/gutenberg-1661.epub
```
Expected: `dad985e2300b30e6a5459444fb78ee1675c766411b62bdb3d6a21314cc927331` (the file as downloaded on 2026-10-15, 311,635 bytes). Gutenberg regenerates files now and then. A different hash is fine, because the goldens are generated from whatever file gets committed. Note the new hash in the commit message.

- [ ] **Step 5: Generate the committed fixtures**

```bash
"$MAIN/.venv/bin/python" -W ignore desktop/core/tests/fixtures/text/generate.py
```
Expected, ending with:
```
golden/basic.epub.json
golden/blocks.epub.json
golden/br.epub.json
golden/dashes.epub.json
golden/dashes_inside.epub.json
golden/gutenberg-1661.epub.json
golden/handmade.epub.json
golden/inline.epub.json
golden/scene.epub.json
golden/basic.md.json
golden/flat.md.json
golden/messy.md.json
golden/nested.md.json
numbers: 1814 cases (1 skipped: Python raises above 10^306)
cases.json
```
Check two values that the Rust tests rely on:
```bash
"$MAIN/.venv/bin/python" -c "import json,sys; g=json.load(open(sys.argv[1])); print([c['title'] for c in g['chapters']])" desktop/core/tests/fixtures/text/golden/handmade.epub.json
```
Expected: `['Chapter 1', '', 'PartTwoAgain', 'Aside', 'Byte\n\nOrder', 'ChapterNine', 'Bad�bytes']`.

- [ ] **Step 6: Generate the local goldens**

```bash
"$MAIN/.venv/bin/python" -W ignore desktop/core/tests/fixtures/text/generate.py --local "$MAIN/uploads/f3f1ae269ab147feb56ce892c8132e06/Trust - Hernan Diaz.epub" "$MAIN/uploads/e120e6e0880a44c6bd1c52dc173537d7/The Abbess.epub" "$MAIN/uploads/041da718ed36462491e94e87e6d5ce60/The Winds of Winter.epub"
```
Expected: `local/Trust - Hernan Diaz.epub.json`, `local/The Abbess.epub.json`, `local/The Winds of Winter.epub.json`.

- [ ] **Step 7: Check that nothing copyrighted is staged**

```bash
git add .gitignore desktop/core/tests/fixtures/text && git status --short | grep -c "fixtures/text/local"
```
Expected: `0`. Anything else means the `.gitignore` line is wrong: unstage `local/` and fix that first.

- [ ] **Step 8: Commit**

```bash
git commit -m "Add Python golden fixtures for the Rust text pipeline

Gutenberg #1661 sha256 dad985e2300b30e6a5459444fb78ee1675c766411b62bdb3d6a21314cc927331."
```

---

### Task 2: Dependencies, Python string semantics, respelling

**Files:**
- Modify: `desktop/core/Cargo.toml`, `desktop/core/src/lib.rs`
- Create: `desktop/core/src/pystr.rs`, `desktop/core/src/text/mod.rs`, `desktop/core/src/text/respelling.rs`
- Test: `desktop/core/tests/fixture/mod.rs`, `desktop/core/tests/respelling.rs`, `desktop/core/tests/text_cases.rs`

**Interfaces:**
- Consumes: `cases.json` from Task 1.
- Produces:
  - `audiobookify_core::pystr::{WS, WORD, BOUNDARY}` (`&str` regex fragments), `is_space(char) -> bool`, `strip(&str) -> &str`, `collapse(&str) -> String`, `len(&str) -> usize`
  - `audiobookify_core::text::phonemes_to_respelling(&str) -> String`
  - test helpers `fixture::{fixtures() -> PathBuf, load(path) -> Value, str_of(&Value) -> &str, usize_of(&Value) -> usize, assert_same(label, expected, actual)}`

All dependencies for the phase go in now, so later tasks only touch code.

- [ ] **Step 1: Add the dependencies**

In `desktop/core/Cargo.toml`, replace the `[dependencies]` section with:
```toml
[dependencies]
fancy-regex = "0.19"
html5ever = "0.39"
num-bigint = "0.5"
num-integer = "0.1"
num-traits = "0.2"
regex = "1"
roxmltree = "0.21"
rusqlite = { version = "0.40", features = ["bundled"] }
serde_json = "1"
thiserror = "2"
zip = { version = "9", default-features = false, features = ["deflate"] }
```
`[dev-dependencies]` stays `tempfile = "3"`.

- [ ] **Step 2: Write the test helpers, `desktop/core/tests/fixture/mod.rs`**

```rust
#![allow(dead_code)] // each test binary uses a different subset

//! Helpers for the text pipeline tests and their fixtures in tests/fixtures/text.

use std::path::PathBuf;

use serde_json::Value;

/// `desktop/core/tests/fixtures/text`
pub fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/text")
}

pub fn load(path: impl Into<PathBuf>) -> Value {
    let path = path.into();
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

pub fn str_of(v: &Value) -> &str {
    v.as_str().unwrap_or_else(|| panic!("expected a string, got {v}"))
}

pub fn usize_of(v: &Value) -> usize {
    v.as_u64().unwrap_or_else(|| panic!("expected a number, got {v}")) as usize
}

/// assert_eq! for long strings: on a mismatch, show where they first differ instead
/// of printing two whole chapters.
#[track_caller]
pub fn assert_same(label: &str, expected: &str, actual: &str) {
    if expected == actual {
        return;
    }
    let at = expected.chars().zip(actual.chars()).take_while(|(a, b)| a == b).count();
    let window = |s: &str| s.chars().skip(at.saturating_sub(60)).take(120).collect::<String>();
    panic!(
        "{label}: first difference at char {at}\n  expected: {:?}\n  actual:   {:?}\n  (lengths {} vs {})",
        window(expected), window(actual), expected.chars().count(), actual.chars().count()
    );
}
```

- [ ] **Step 3: Write the failing tests**

`desktop/core/tests/respelling.rs`:
```rust
//! Port of tests/test_respelling.py.

use audiobookify_core::text::phonemes_to_respelling;

#[test]
fn phonemes_to_respelling_maps_known_ipa_tokens() {
    assert_eq!(phonemes_to_respelling("k ə d eɪ ɹ n"), "kuhdayrn");
}

#[test]
fn phonemes_to_respelling_passes_through_unknown_tokens_unchanged() {
    assert_eq!(phonemes_to_respelling("k ə ? n"), "kuh?n");
}

#[test]
fn phonemes_to_respelling_handles_empty_string() {
    assert_eq!(phonemes_to_respelling(""), "");
}

#[test]
fn phonemes_to_respelling_handles_diphthongs_and_consonant_clusters() {
    assert_eq!(phonemes_to_respelling("h ə l oʊ"), "huhloh");
    assert_eq!(phonemes_to_respelling("tʃ ɪ p"), "chihp");
    assert_eq!(phonemes_to_respelling("dʒ ʌ m p"), "juhmp");
}
```

`desktop/core/tests/text_cases.rs` (Tasks 3-5 append to it):
```rust
//! Unit-level parity with the Python text functions. Each case in
//! tests/fixtures/text/cases.json is an input and what Python returned for it.

mod fixture;

use std::sync::LazyLock;

use audiobookify_core::pystr;
use fixture::{fixtures, load, str_of};
use serde_json::Value;

static CASES: LazyLock<Value> = LazyLock::new(|| load(fixtures().join("cases.json")));

fn cases(key: &str) -> &'static Vec<Value> {
    CASES[key].as_array().unwrap_or_else(|| panic!("cases.json has no {key}"))
}

fn in_ranges(ranges: &[Value], c: u32) -> bool {
    ranges.iter().any(|r| r[0].as_u64().unwrap() as u32 <= c && c <= r[1].as_u64().unwrap() as u32)
}

#[test]
fn whitespace_is_pythons() {
    let python: Vec<u32> = cases("python_whitespace").iter().map(|c| c.as_u64().unwrap() as u32).collect();
    let rust: Vec<u32> = (0..0x110000).filter(|&c| char::from_u32(c).is_some_and(pystr::is_space)).collect();
    assert_eq!(rust, python);
}

#[test]
fn word_class_is_pythons() {
    let word = regex::Regex::new(&format!("^[{}]$", pystr::WORD)).unwrap();
    let (python, unassigned) = (cases("python_word"), cases("python_unassigned"));
    for c in (0..0x110000).filter_map(char::from_u32) {
        let (py, rs) = (in_ranges(python, c as u32), word.is_match(c.encode_utf8(&mut [0; 4])));
        assert!(py == rs || in_ranges(unassigned, c as u32), "U+{:04X}: Python \\w {py}, Rust {rs}", c as u32);
    }
}

#[test]
fn respelling() {
    for case in cases("respelling") {
        let actual = audiobookify_core::text::phonemes_to_respelling(str_of(&case[0]));
        assert_eq!(actual, str_of(&case[1]), "{:?}", case[0]);
    }
}
```

- [ ] **Step 4: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test respelling --test text_cases`
Expected: compile errors, ``unresolved import `audiobookify_core::pystr` `` and ``could not find `text` in `audiobookify_core` ``.

- [ ] **Step 5: Write `desktop/core/src/pystr.rs`**

```rust
//! Python `str` and `re` semantics the ported text code relies on.
//!
//! - Python's `str.split()`, `str.strip()` and regex `\s` treat U+001C..U+001F as
//!   whitespace; Rust's `char::is_whitespace` and regex `\s` do not.
//! - Python's regex `\w` is `str.isalnum()` or "_" (letters and all numbers, so "½"
//!   is a word character); Rust's `\w` leaves out "½" and adds combining marks. `\b`
//!   follows `\w`, so "4½" has no boundary after the 4 in Python but has one in Rust.
//! - `len()` counts code points, not bytes.

/// Python regex `\s`, for use inside a character class: `[{WS}]`.
pub const WS: &str = r"\s\x1C-\x1F";
/// Python regex `\w`, for use inside a character class: `[{WORD}]`.
pub const WORD: &str = r"\p{L}\p{N}_";
/// Python regex `\b` (needs fancy-regex for the lookarounds).
pub const BOUNDARY: &str = r"(?:(?<=[\p{L}\p{N}_])(?![\p{L}\p{N}_])|(?<![\p{L}\p{N}_])(?=[\p{L}\p{N}_]))";

pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `s.strip()`
pub fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// `" ".join(s.split())`
pub fn collapse(s: &str) -> String {
    s.split(is_space).filter(|w| !w.is_empty()).collect::<Vec<_>>().join(" ")
}

/// `len(s)`
pub fn len(s: &str) -> usize {
    s.chars().count()
}
```

- [ ] **Step 6: Write `desktop/core/src/text/respelling.rs`**

```rust
use crate::pystr;

// Static espeak-IPA-symbol -> ASCII mapping. Deliberately simple: no syllable
// boundaries or stress marking. The derived respelling is a starting point the
// user corrects before it's used, not a phonetically precise transcription.
const IPA_TO_ASCII: &[(&str, &str)] = &[
    ("p", "p"), ("b", "b"), ("t", "t"), ("d", "d"), ("k", "k"), ("ɡ", "g"), ("g", "g"),
    ("tʃ", "ch"), ("dʒ", "j"), ("f", "f"), ("v", "v"), ("θ", "th"), ("ð", "th"),
    ("s", "s"), ("z", "z"), ("ʃ", "sh"), ("ʒ", "zh"), ("h", "h"), ("m", "m"), ("n", "n"),
    ("ŋ", "ng"), ("l", "l"), ("ɹ", "r"), ("r", "r"), ("w", "w"), ("j", "y"),
    ("i", "ee"), ("iː", "ee"), ("ɪ", "ih"), ("ɛ", "eh"), ("e", "eh"), ("æ", "a"),
    ("ɑ", "ah"), ("ɑː", "ah"), ("ɔ", "aw"), ("ɔː", "aw"), ("ʊ", "uu"), ("u", "oo"),
    ("uː", "oo"), ("ʌ", "uh"), ("ə", "uh"), ("ɚ", "er"), ("ɝ", "er"),
    ("eɪ", "ay"), ("aɪ", "eye"), ("ɔɪ", "oy"), ("aʊ", "ow"), ("oʊ", "oh"),
];

pub fn phonemes_to_respelling(phonemes: &str) -> String {
    phonemes
        .split(pystr::is_space)
        .filter(|token| !token.is_empty())
        .map(|token| IPA_TO_ASCII.iter().find(|(ipa, _)| *ipa == token).map_or(token, |(_, ascii)| ascii))
        .collect()
}
```

- [ ] **Step 7: Write `desktop/core/src/text/mod.rs` and register the modules**

```rust
//! The text pipeline: normalization, chunking, lexicon substitution and respelling,
//! ported from `src/audiobookify/normalize/text.py`, `lexicon/substitution.py` and
//! `phoneme/respelling.py`.

mod respelling;

pub use respelling::phonemes_to_respelling;
```

`desktop/core/src/lib.rs`:
```rust
pub mod db;
pub mod models;
pub mod pystr;
pub mod text;
pub mod voice_storage;
```

- [ ] **Step 8: Run the tests**

Run: `cargo test -p audiobookify-core --test respelling --test text_cases`
Expected: `respelling` 4 passed, `text_cases` 3 passed (`whitespace_is_pythons`, `word_class_is_pythons`, `respelling`).

- [ ] **Step 9: Commit**

```bash
git add desktop/Cargo.lock desktop/core/Cargo.toml desktop/core/src/lib.rs desktop/core/src/pystr.rs desktop/core/src/text desktop/core/tests/fixture desktop/core/tests/respelling.rs desktop/core/tests/text_cases.rs
git commit -m "Port phoneme respelling and Python string semantics to Rust"
```

---

### Task 3: Lexicon substitution

**Files:**
- Create: `desktop/core/src/text/lexicon.rs`
- Modify: `desktop/core/src/text/mod.rs`
- Test: `desktop/core/tests/lexicon_substitution.rs`, `desktop/core/tests/text_cases.rs` (append)

**Interfaces:**
- Consumes: `pystr::{BOUNDARY, len}`.
- Produces: `audiobookify_core::text::apply_lexicon<'a>(text: &str, entries: impl IntoIterator<Item = (&'a str, &'a str)>) -> String`

- [ ] **Step 1: Write the failing tests**

`desktop/core/tests/lexicon_substitution.rs`:
```rust
//! Port of tests/test_lexicon_substitution.py.

use audiobookify_core::text::apply_lexicon;

#[test]
fn apply_lexicon_substitutes_whole_word_case_insensitive() {
    assert_eq!(apply_lexicon("Cadeyrn walked into the hall.", [("cadeyrn", "KAH-dern")]), "KAH-dern walked into the hall.");
}

#[test]
fn apply_lexicon_does_not_substitute_partial_word_matches() {
    assert_eq!(apply_lexicon("Anne-Marie waved at Ann.", [("Ann", "ANN-override")]), "Anne-Marie waved at ANN-override.");
}

#[test]
fn apply_lexicon_supports_multi_word_terms() {
    assert_eq!(apply_lexicon("She flew to New York yesterday.", [("New York", "Noo Yawk")]), "She flew to Noo Yawk yesterday.");
}

#[test]
fn apply_lexicon_leaves_unmatched_text_unchanged() {
    assert_eq!(apply_lexicon("Nothing to substitute here.", [("Cadeyrn", "KAH-dern")]), "Nothing to substitute here.");
}

#[test]
fn apply_lexicon_processes_entries_in_given_order_first_match_wins() {
    let entries = [("Cadeyrn", "book-specific-version"), ("Cadeyrn", "global-version")];
    assert_eq!(apply_lexicon("Cadeyrn spoke.", entries), "book-specific-version spoke.");
}

#[test]
fn apply_lexicon_treats_override_value_as_a_literal_replacement() {
    assert_eq!(apply_lexicon("Cadeyrn spoke.", [("Cadeyrn", "KAH\\dern")]), "KAH\\dern spoke.");
    assert_eq!(apply_lexicon("Cadeyrn spoke.", [("Cadeyrn", "$1 ${0}")]), "$1 ${0} spoke.");
}

#[test]
fn apply_lexicon_prefers_longer_overlapping_term_regardless_of_order() {
    let text = "She flew to New York yesterday.";
    assert_eq!(apply_lexicon(text, [("York", "Yorrk"), ("New York", "Noo Yawk")]), "She flew to Noo Yawk yesterday.");
    assert_eq!(apply_lexicon(text, [("New York", "Noo Yawk"), ("York", "Yorrk")]), "She flew to Noo Yawk yesterday.");
}
```

Append to `desktop/core/tests/text_cases.rs`:
```rust

#[test]
fn lexicon() {
    for case in cases("lexicon") {
        let entries: Vec<(&str, &str)> = case[1].as_array().unwrap().iter().map(|e| (str_of(&e[0]), str_of(&e[1]))).collect();
        let actual = audiobookify_core::text::apply_lexicon(str_of(&case[0]), entries);
        assert_eq!(actual, str_of(&case[2]), "{}", case[1]);
    }
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test lexicon_substitution --test text_cases`
Expected: compile errors, ``unresolved import `audiobookify_core::text::apply_lexicon` `` and ``cannot find function `apply_lexicon` ``.

- [ ] **Step 3: Write `desktop/core/src/text/lexicon.rs`**

```rust
use fancy_regex::{NoExpand, Regex};

use crate::pystr::{self, BOUNDARY};

/// Replace each `(term, override)` pair as a whole word, case-insensitively. Longer
/// terms go first ("New York" before "York"); equal lengths keep the given order, so
/// a book-scoped entry listed before a global one wins. The override is literal text.
pub fn apply_lexicon<'a>(text: &str, entries: impl IntoIterator<Item = (&'a str, &'a str)>) -> String {
    let mut entries: Vec<_> = entries.into_iter().collect();
    entries.sort_by_key(|(term, _)| std::cmp::Reverse(pystr::len(term))); // stable, like sorted()
    let mut text = text.to_string();
    for (term, value) in entries {
        // BOUNDARY's lookarounds run on fancy-regex's slow path; most terms aren't in a
        // given chapter, and the plain search proves that cheaply.
        let present = regex::RegexBuilder::new(&regex::escape(term)).case_insensitive(true).build().expect("escaped term");
        if !present.is_match(&text) {
            continue;
        }
        let pattern = Regex::new(&format!("(?i){BOUNDARY}{}{BOUNDARY}", fancy_regex::escape(term)))
            .expect("escaped term is a valid regex");
        text = pattern.replace_all(&text, NoExpand(value)).into_owned();
    }
    text
}
```

- [ ] **Step 4: Register it in `desktop/core/src/text/mod.rs`**

```rust
//! The text pipeline: normalization, chunking, lexicon substitution and respelling,
//! ported from `src/audiobookify/normalize/text.py`, `lexicon/substitution.py` and
//! `phoneme/respelling.py`.

mod lexicon;
mod respelling;

pub use lexicon::apply_lexicon;
pub use respelling::phonemes_to_respelling;
```

- [ ] **Step 5: Run the tests**

Run: `cargo test -p audiobookify-core --test lexicon_substitution --test text_cases`
Expected: `lexicon_substitution` 7 passed, `text_cases` 4 passed.

- [ ] **Step 6: Commit**

```bash
git add desktop/core/src/text desktop/core/tests/lexicon_substitution.rs desktop/core/tests/text_cases.rs
git commit -m "Port lexicon substitution to Rust"
```

---

### Task 4: num2words and normalization

**Files:**
- Create: `desktop/core/src/text/num2words.rs`, `desktop/core/src/text/normalize.rs`
- Modify: `desktop/core/src/text/mod.rs`
- Test: `desktop/core/tests/normalize_text.rs` (Tasks 5 and 8 append), `desktop/core/tests/text_cases.rs` (append)

**Interfaces:**
- Consumes: `pystr::{BOUNDARY, WS, strip, collapse}`.
- Produces:
  - `audiobookify_core::text::{expand_abbreviations, expand_numbers, normalize_text}`, each `(&str) -> String`
  - `audiobookify_core::text::split_sentences(&str) -> Vec<String>`, `split_paragraphs(&str) -> Vec<String>`
  - crate-internal: `text::normalize::CLOSERS` (`pub(super)`, used by `chunk.rs`), `text::num2words::expand(&str) -> Option<String>`

- [ ] **Step 1: Write the failing tests**

`desktop/core/tests/normalize_text.rs`:
```rust
//! Port of tests/test_normalize_text.py.

use audiobookify_core::text::{normalize_text, split_paragraphs, split_sentences};

#[test]
fn expand_abbreviations() {
    assert_eq!(normalize_text("Dr. Smith arrived."), "Doctor Smith arrived.");
}

#[test]
fn expand_numbers() {
    assert_eq!(normalize_text("I have 42 apples."), "I have forty-two apples.");
}

#[test]
fn split_sentences_basic() {
    assert_eq!(split_sentences("First sentence. Second sentence! Third one?"), ["First sentence.", "Second sentence!", "Third one?"]);
}

#[test]
fn expand_numbers_leaves_hyphenated_identifiers_alone() {
    assert_eq!(normalize_text("She had COVID-19 last year."), "She had COVID-19 last year.");
}

#[test]
fn expand_numbers_still_expands_plain_numbers_near_hyphens() {
    assert_eq!(normalize_text("Room 7 - the attic."), "Room seven - the attic.");
}

#[test]
fn expand_numbers_expands_hyphenated_number_modifiers() {
    assert_eq!(normalize_text("She met a 5-year-old girl."), "She met a five-year-old girl.");
    assert_eq!(normalize_text("A 10-minute break."), "A ten-minute break.");
    assert_eq!(normalize_text("A 3-day trip."), "A three-day trip.");
}

#[test]
fn expand_numbers_leaves_numeric_ranges_intact() {
    assert_eq!(normalize_text("See pages 12-34."), "See pages 12-34.");
    assert_eq!(normalize_text("They won 3-2."), "They won 3-2.");
    assert_eq!(normalize_text("Chapters 1-3 cover it."), "Chapters 1-3 cover it.");
    assert_eq!(normalize_text("Read 12-34 then take 5 minutes."), "Read 12-34 then take five minutes.");
}

#[test]
fn expand_numbers_leaves_numeric_ranges_intact_when_followed_by_a_word_char() {
    assert_eq!(normalize_text("See pages 12-34b for details."), "See pages 12-34b for details.");
    assert_eq!(normalize_text("The 1980-90s recession."), "The 1980-90s recession.");
    assert_eq!(normalize_text("Table 3-2a shows it."), "Table 3-2a shows it.");
}

#[test]
fn expand_numbers_uses_num2words_phrasing() {
    assert_eq!(normalize_text("In 1984, 105 of 2,500,000 paid 27.50."),
        "In one thousand, nine hundred and eighty-four, one hundred and five of two million, five hundred thousand paid twenty-seven point five.");
}

#[test]
fn expand_numbers_uses_pythons_word_boundaries() {
    // Python's \w includes "½", so "4½" has no boundary after the 4 (Gutenberg #1661).
    assert_eq!(normalize_text("paying 4½ per cent"), "paying 4½ per cent");
}

#[test]
fn split_sentences_after_a_closing_quote() {
    assert_eq!(split_sentences("\"I'm leaving.\" She turned away."), ["\"I'm leaving.\"", "She turned away."]);
}

#[test]
fn split_sentences_after_closing_curly_quote_and_bracket() {
    assert_eq!(split_sentences("“Go now!” He ran. (It was late.) Then dawn came."), ["“Go now!”", "He ran.", "(It was late.)", "Then dawn came."]);
}

#[test]
fn split_sentences_before_an_opening_quote() {
    assert_eq!(split_sentences("He waited. \"Hello?\" No answer."), ["He waited.", "\"Hello?\"", "No answer."]);
}

#[test]
fn split_sentences_keeps_dialogue_tag_attached() {
    assert_eq!(split_sentences("'Why?' he asked. She shrugged."), ["'Why?' he asked.", "She shrugged."]);
}

#[test]
fn split_paragraphs_on_blank_lines_and_collapses_inner_whitespace() {
    assert_eq!(split_paragraphs("One\nstill one.\n\n  Two.  \n \n\nThree."), ["One still one.", "Two.", "Three."]);
}

#[test]
fn text_without_blank_lines_is_one_paragraph() {
    assert_eq!(split_paragraphs("Line one.\nLine two."), ["Line one. Line two."]);
}

```

Append to `desktop/core/tests/text_cases.rs`:
```rust

#[test]
fn numbers() {
    for case in cases("numbers") {
        let actual = audiobookify_core::text::expand_numbers(str_of(&case[0]));
        fixture::assert_same(str_of(&case[0]), str_of(&case[1]), &actual);
    }
}

#[test]
fn normalize() {
    for case in cases("normalize") {
        let actual = audiobookify_core::text::normalize_text(str_of(&case[0]));
        assert_eq!(actual, str_of(&case[1]), "{}", case[0]);
    }
}

#[test]
fn sentences() {
    for case in cases("sentences") {
        let expected: Vec<&str> = case[1].as_array().unwrap().iter().map(str_of).collect();
        assert_eq!(audiobookify_core::text::split_sentences(str_of(&case[0])), expected, "{}", case[0]);
    }
}

#[test]
fn paragraphs() {
    for case in cases("paragraphs") {
        let expected: Vec<&str> = case[1].as_array().unwrap().iter().map(str_of).collect();
        assert_eq!(audiobookify_core::text::split_paragraphs(str_of(&case[0])), expected, "{}", case[0]);
    }
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test normalize_text --test text_cases`
Expected: compile errors, ``unresolved imports `audiobookify_core::text::normalize_text` ``, and so on.

- [ ] **Step 3: Write `desktop/core/src/text/num2words.rs`**

```rust
//! A port of the parts of num2words 0.5.14 (`lang="en"`) that `expand_numbers` uses:
//! `num2words(int(raw))` and `num2words(float(raw))`. It follows the Python code step
//! by step (`Num2Word_Base.splitnum`/`clean`/`to_cardinal_float`, `Num2Word_EN.merge`),
//! so it keeps num2words' phrasing: "one hundred and five", "one thousand, nine
//! hundred and eighty-four", "twenty-seven point five" for "27.50".

use std::sync::LazyLock;

use num_bigint::BigUint;
use num_integer::Integer;
use num_traits::{FromPrimitive, One, Zero};

/// num2words' `cards`, largest first: 10^303 ("centillion") down to 10^6, then
/// thousand, hundred, ninety..thirty, twenty..zero.
static CARDS: LazyLock<Vec<(BigUint, String)>> = LazyLock::new(|| {
    let lows = ["non", "oct", "sept", "sext", "quint", "quadr", "tr", "b", "m"];
    let units = ["", "un", "duo", "tre", "quattuor", "quin", "sex", "sept", "octo", "novem"];
    let tens = ["dec", "vigint", "trigint", "quadragint", "quinquagint", "sexagint", "septuagint", "octogint", "nonagint"];
    let mut high: Vec<String> = tens.iter().flat_map(|t| units.iter().map(move |u| format!("{u}{t}"))).collect();
    high.reverse();
    high.extend(lows.iter().map(|l| l.to_string()));
    high.insert(0, "cent".to_string());

    let ten = BigUint::from(10u32);
    let mut cards: Vec<(BigUint, String)> =
        high.iter().zip((6..=303u32).rev().step_by(3)).map(|(word, n)| (ten.pow(n), format!("{word}illion"))).collect();
    let mid = [(1000, "thousand"), (100, "hundred"), (90, "ninety"), (80, "eighty"), (70, "seventy"),
               (60, "sixty"), (50, "fifty"), (40, "forty"), (30, "thirty")];
    cards.extend(mid.iter().map(|(n, w)| (BigUint::from(*n as u32), w.to_string())));
    let low = ["twenty", "nineteen", "eighteen", "seventeen", "sixteen", "fifteen", "fourteen", "thirteen",
               "twelve", "eleven", "ten", "nine", "eight", "seven", "six", "five", "four", "three", "two", "one", "zero"];
    cards.extend(low.iter().zip((0..=20u32).rev()).map(|(w, n)| (BigUint::from(n), w.to_string())));
    cards
});

/// num2words' MAXVAL: 1000 * the largest card. Python raises OverflowError at or above it.
static MAXVAL: LazyLock<BigUint> = LazyLock::new(|| BigUint::from(10u32).pow(306));

#[derive(Clone)]
enum Part {
    Word(String, BigUint),
    Group(Vec<Part>),
}

fn splitnum(value: &BigUint) -> Vec<Part> {
    for (elem, word) in CARDS.iter() {
        if elem > value {
            continue;
        }
        let (div, rem) = if value.is_zero() { (BigUint::one(), BigUint::zero()) } else { value.div_rem(elem) };
        let mut out = Vec::new();
        if div.is_one() {
            out.push(Part::Word("one".to_string(), BigUint::one()));
        } else {
            // ponytail: Python's "tally" branch (div == value) only fires for elem == 1,
            // which English never reaches with div > 1, so it isn't ported.
            out.push(Part::Group(splitnum(&div)));
        }
        out.push(Part::Word(word.clone(), elem.clone()));
        if !rem.is_zero() {
            out.push(Part::Group(splitnum(&rem)));
        }
        return out;
    }
    unreachable!("CARDS ends at zero, which is <= any value")
}

fn merge(left: &Part, right: &Part) -> Part {
    let (Part::Word(ltext, lnum), Part::Word(rtext, rnum)) = (left, right) else {
        unreachable!("clean() only merges two words")
    };
    let hundred = BigUint::from(100u32);
    if lnum.is_one() && *rnum < hundred {
        Part::Word(rtext.clone(), rnum.clone())
    } else if hundred > *lnum && lnum > rnum {
        Part::Word(format!("{ltext}-{rtext}"), lnum + rnum)
    } else if *lnum >= hundred && hundred > *rnum {
        Part::Word(format!("{ltext} and {rtext}"), lnum + rnum)
    } else if rnum > lnum {
        Part::Word(format!("{ltext} {rtext}"), lnum * rnum)
    } else {
        Part::Word(format!("{ltext}, {rtext}"), lnum + rnum)
    }
}

fn clean(mut val: Vec<Part>) -> Part {
    let mut out = val.clone();
    while val.len() != 1 {
        out = Vec::new();
        if let (Part::Word(..), Part::Word(..)) = (&val[0], &val[1]) {
            out.push(merge(&val[0], &val[1]));
            if val.len() > 2 {
                out.push(Part::Group(val[2..].to_vec()));
            }
        } else {
            for elem in &val {
                match elem {
                    Part::Group(group) if group.len() == 1 => out.push(group[0].clone()),
                    Part::Group(group) => out.push(clean(group.clone())),
                    word => out.push(word.clone()),
                }
            }
        }
        val = out.clone();
    }
    out.swap_remove(0)
}

/// `num2words(n)` for a non-negative integer; None where Python raises OverflowError.
fn cardinal(value: &BigUint) -> Option<String> {
    if *value >= *MAXVAL {
        return None;
    }
    match clean(splitnum(value)) {
        Part::Word(text, _) => Some(text),
        Part::Group(_) => unreachable!("clean() reduces to one word"),
    }
}

/// Decimal places in Python's `repr(value)`: `abs(Decimal(str(value)).as_tuple().exponent)`.
/// Rust's `{:e}` prints the same shortest round-trip digits as Python's repr.
fn decimal_places(value: f64) -> usize {
    let formatted = format!("{value:e}");
    let (mantissa, exponent) = formatted.split_once('e').expect("{:e} always has an exponent");
    let digits = mantissa.chars().filter(char::is_ascii_digit).count() as i64;
    (exponent.parse::<i64>().expect("integer exponent") - (digits - 1)).unsigned_abs() as usize
}

/// `Num2Word_Base.to_cardinal_float` for a finite, non-integral, non-negative value.
fn float_words(value: f64) -> Option<String> {
    let pre = value.trunc();
    let precision = decimal_places(value);
    let scale: f64 = format!("1e{precision}").parse().ok()?; // == float(10**precision)
    let mut post = (value - pre).abs() * scale;
    post = if (post.round_ties_even() - post).abs() < 0.01 { post.round_ties_even() } else { post.floor() };
    let digits = BigUint::from_f64(post)?.to_string();
    let digits = format!("{}{digits}", "0".repeat(precision.saturating_sub(digits.len())));
    let mut out = vec![cardinal(&BigUint::from_f64(pre)?)?, "point".to_string()];
    for digit in digits.chars().take(precision) {
        out.push(cardinal(&BigUint::from(digit.to_digit(10)?))?);
    }
    Some(out.join(" "))
}

/// `num2words(float(raw)) if "." in raw else num2words(int(raw))`, for a regex match
/// with its commas already removed. None means "leave the text as it was".
pub fn expand(raw: &str) -> Option<String> {
    // ponytail: Python's int()/float() also read non-ASCII digits ("٣" -> three); no
    // book has had them, so those stay as written.
    if !raw.is_ascii() {
        return None;
    }
    if !raw.contains('.') {
        return cardinal(&raw.parse().ok()?);
    }
    let value: f64 = raw.parse().ok()?;
    if !value.is_finite() {
        return None;
    }
    if value.fract() == 0.0 {
        return cardinal(&BigUint::from_f64(value)?); // "5.0" -> "five", like Python
    }
    float_words(value)
}
```

- [ ] **Step 4: Write `desktop/core/src/text/normalize.rs`**

```rust
use std::sync::LazyLock;

use fancy_regex::{Captures, Regex as FancyRegex};
use regex::Regex;

use super::num2words;
use crate::pystr::{self, BOUNDARY, WS};

/// Applied in this order with plain substring replacement, exactly like the Python
/// loop of `re.sub(re.escape(abbr), ...)`, so "St." inside "FIRST." is replaced too.
const ABBREVIATIONS: [(&str, &str); 8] = [
    ("Dr.", "Doctor"), ("Mr.", "Mister"), ("Mrs.", "Missus"), ("Ms.", "Miz"),
    ("Prof.", "Professor"), ("St.", "Saint"), ("vs.", "versus"), ("etc.", "et cetera"),
];

/// Closing quotes and brackets that may follow sentence-ending punctuation.
pub(super) const CLOSERS: &str = r#""'”’)\]"#;

const NUMBER: &str = r"\d[\d,]*(?:\.\d+)?";
// A digit run, optionally followed by more hyphen-joined digit runs, so a range
// ("12-34") is one match that is left alone. The lookbehind keeps "COVID-19" out.
// BOUNDARY is Python's \b: "4½" has none after the 4, so it stays as written.
static NUMBER_RE: LazyLock<FancyRegex> =
    LazyLock::new(|| FancyRegex::new(&format!(r"(?<!-){BOUNDARY}{NUMBER}(?:-{NUMBER})*{BOUNDARY}")).unwrap());
// A range whose right operand is glued to a word character ("12-34b", "1980-90s")
// can't close its \b, so NUMBER_RE matches only the left operand; this spots that.
static FOLLOWED_BY_RANGE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\A-\d").unwrap());
// A sentence ends at .!?… plus up to two closers; the next may open with a quote or bracket.
static SENTENCE_SPLIT_RE: LazyLock<FancyRegex> = LazyLock::new(|| {
    FancyRegex::new(&format!(
        r#"(?:(?<=[.!?…])|(?<=[.!?…][{c}])|(?<=[.!?…][{c}][{c}]))[{WS}]+(?=["'“‘(\[]?[A-Z0-9])"#,
        c = CLOSERS
    ))
    .unwrap()
});
static PARAGRAPH_SPLIT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!(r"\n[{WS}]*\n")).unwrap());

pub fn expand_abbreviations(text: &str) -> String {
    ABBREVIATIONS.iter().fold(text.to_string(), |text, (abbr, expansion)| text.replace(abbr, expansion))
}

pub fn expand_numbers(text: &str) -> String {
    NUMBER_RE
        .replace_all(text, |caps: &Captures<str>| {
            let m = caps.get(0).expect("group 0 always exists");
            let raw = m.as_str();
            if raw.contains('-') || FOLLOWED_BY_RANGE_RE.is_match(&text[m.end()..]) {
                return raw.to_string(); // a numeric range: leave both operands alone
            }
            num2words::expand(&raw.replace(',', "")).unwrap_or_else(|| raw.to_string())
        })
        .into_owned()
}

pub fn normalize_text(text: &str) -> String {
    expand_numbers(&expand_abbreviations(text))
}

pub fn split_sentences(text: &str) -> Vec<String> {
    let mut pieces = Vec::new();
    let mut last = 0;
    for m in SENTENCE_SPLIT_RE.find_iter(text) {
        let m = m.expect("sentence regex stays within the backtrack limit");
        pieces.push(&text[last..m.start()]);
        last = m.end();
    }
    pieces.push(&text[last..]);
    pieces.into_iter().map(pystr::strip).filter(|s| !s.is_empty()).map(String::from).collect()
}

/// Blank lines separate paragraphs; whitespace inside a paragraph collapses to one space.
pub fn split_paragraphs(text: &str) -> Vec<String> {
    PARAGRAPH_SPLIT_RE.split(text).map(pystr::collapse).filter(|p| !p.is_empty()).collect()
}
```

- [ ] **Step 5: Register them in `desktop/core/src/text/mod.rs`**

```rust
//! The text pipeline: normalization, chunking, lexicon substitution and respelling,
//! ported from `src/audiobookify/normalize/text.py`, `lexicon/substitution.py` and
//! `phoneme/respelling.py`.

mod lexicon;
mod normalize;
mod num2words;
mod respelling;

pub use lexicon::apply_lexicon;
pub use normalize::{expand_abbreviations, expand_numbers, normalize_text, split_paragraphs, split_sentences};
pub use respelling::phonemes_to_respelling;
```

- [ ] **Step 6: Run the tests**

Run: `cargo test -p audiobookify-core --test normalize_text --test text_cases`
Expected: `normalize_text` 16 passed, `text_cases` 8 passed. If `numbers` fails, `assert_same` prints the input and where the words first differ. Compare with `num2words` in `$MAIN/.venv/lib/python3.12/site-packages/num2words/base.py` (`splitnum`, `clean`, `float2tuple`, `to_cardinal_float`) and `lang_EN.py` (`merge`).

- [ ] **Step 7: Commit**

```bash
git add desktop/core/src/text desktop/core/tests/normalize_text.rs desktop/core/tests/text_cases.rs
git commit -m "Port number expansion, abbreviations and sentence splitting to Rust"
```

---

### Task 5: Chunking

**Files:**
- Create: `desktop/core/src/text/chunk.rs`
- Modify: `desktop/core/src/text/mod.rs`
- Test: `desktop/core/tests/normalize_text.rs` (append), `desktop/core/tests/text_cases.rs` (append)

**Interfaces:**
- Consumes: `normalize::{expand_abbreviations, expand_numbers, split_paragraphs, split_sentences, CLOSERS}`, `pystr::{WORD, WS, len, strip}`.
- Produces:
  - `audiobookify_core::text::EndsWith` (`Word | Clause | Sentence | Paragraph | Scene`, `as_str() -> &'static str` giving Python's strings)
  - `audiobookify_core::text::Chunk { pub text: String, pub ends_with: EndsWith }`, `Chunk::new(impl Into<String>, EndsWith)`
  - `audiobookify_core::text::chunk_text(text: &str, max_chars: usize, min_chars: usize) -> Vec<Chunk>` (Python's default `min_chars=0` becomes an explicit `0`)

- [ ] **Step 1: Write the failing tests**

Append to `desktop/core/tests/normalize_text.rs`:
```rust

use audiobookify_core::text::{chunk_text, Chunk, EndsWith};
use EndsWith::{Clause, Paragraph, Scene, Sentence};

fn texts(chunks: &[Chunk]) -> Vec<&str> {
    chunks.iter().map(|c| c.text.as_str()).collect()
}

fn chunk(text: &str, ends_with: EndsWith) -> Chunk {
    Chunk::new(text, ends_with)
}

#[test]
fn chunk_text_respects_max_chars() {
    let text = (0..20).map(|i| format!("Sentence number {i}.")).collect::<Vec<_>>().join(" ");
    let chunks = chunk_text(&text, 50, 0);
    assert!(chunks.iter().all(|c| c.text.chars().count() <= 50));
    assert!(chunks.len() > 1);
}

#[test]
fn chunk_text_does_not_split_a_single_short_paragraph() {
    assert_eq!(chunk_text("Just one short sentence.", 300, 0), [chunk("Just one short sentence.", Paragraph)]);
}

#[test]
fn chunk_text_hard_splits_a_long_unpunctuated_run() {
    let text = vec!["word"; 200].join(" ");
    let chunks = chunk_text(&text, 100, 0);
    assert!(chunks.len() > 1);
    assert!(chunks.iter().all(|c| c.text.chars().count() <= 100));
    assert_eq!(texts(&chunks).join(" "), text);
}

#[test]
fn chunk_text_splits_a_sentence_that_starts_with_a_number() {
    assert_eq!(texts(&chunk_text("Six went home. 7 more came.", 20, 0)), ["Six went home.", "seven more came."]);
}

#[test]
fn a_paragraph_always_ends_a_chunk() {
    assert_eq!(chunk_text("First paragraph here.\n\nSecond paragraph here.", 300, 0),
        [chunk("First paragraph here.", Paragraph), chunk("Second paragraph here.", Paragraph)]);
}

#[test]
fn sentences_packed_inside_a_paragraph_end_with_sentence() {
    assert_eq!(chunk_text("Aaaa aaaa aaaa. Bbbb bbbb bbbb. Cccc cccc cccc.", 32, 0),
        [chunk("Aaaa aaaa aaaa. Bbbb bbbb bbbb.", Sentence), chunk("Cccc cccc cccc.", Paragraph)]);
}

#[test]
fn long_sentence_splits_at_semicolon_before_comma() {
    let text = "He walked to the old mill, slowly; the river ran past it, cold and grey.";
    assert_eq!(chunk_text(text, 50, 0),
        [chunk("He walked to the old mill, slowly;", Clause), chunk("the river ran past it, cold and grey.", Paragraph)]);
}

#[test]
fn long_sentence_splits_at_comma_when_no_stronger_punctuation() {
    let text = "He walked to the old mill by the river, and the water ran past it cold and grey.";
    assert_eq!(chunk_text(text, 50, 0),
        [chunk("He walked to the old mill by the river,", Clause), chunk("and the water ran past it cold and grey.", Paragraph)]);
}

#[test]
fn clause_split_never_leaves_a_piece_under_the_minimum() {
    let text = "Yes; he walked to the old mill by the river and the water ran past it cold and grey all day.";
    let chunks = chunk_text(text, 60, 20);
    assert!(chunks.iter().all(|c| c.text.chars().count() >= 20));
    assert_eq!(texts(&chunks).join(" "), text);
}

#[test]
fn one_word_paragraph_merges_forward() {
    assert_eq!(chunk_text("\"No.\"\n\nShe looked at him for a long moment before answering.", 300, 20),
        [chunk("\"No.\" She looked at him for a long moment before answering.", Paragraph)]);
}

#[test]
fn short_final_paragraph_merges_backward() {
    assert_eq!(chunk_text("She looked at him for a long moment.\n\nNo.", 300, 20),
        [chunk("She looked at him for a long moment. No.", Paragraph)]);
}

#[test]
fn merged_heading_gets_a_period_so_the_model_pauses() {
    assert_eq!(texts(&chunk_text("Chapter One\n\nIt was a dark and stormy night.", 300, 20)), ["Chapter One. It was a dark and stormy night."]);
}

#[test]
fn consecutive_short_paragraphs_accumulate_until_long_enough() {
    assert_eq!(texts(&chunk_text("No.\n\nYes.\n\nMaybe.\n\nThat settles it then, I suppose.", 300, 15)),
        ["No. Yes. Maybe.", "That settles it then, I suppose."]);
}

#[test]
fn input_shorter_than_minimum_is_still_one_chunk() {
    assert_eq!(texts(&chunk_text("Hi.", 300, 80)), ["Hi."]);
}

/// The Python test draws from random.Random(1234); Rust can't replay that stream, so
/// this keeps the property (bounds) with its own generator, and cases.json holds the
/// Python stream's exact outputs for parity.
#[test]
fn chunk_lengths_stay_within_bounds_on_varied_text() {
    let mut seed: u64 = 1234;
    let mut next = |n: usize| {
        seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        (seed >> 33) as usize % n
    };
    let words = ["the", "a", "river", "old", "mill", "she", "said", "quietly", "darkness", "No", "Yes"];
    let punct = [".", ".", "!", "?", ",", ";", ""];
    for _ in 0..200 {
        let mut paragraphs = Vec::new();
        for _ in 0..1 + next(8) {
            let mut sentences = Vec::new();
            for _ in 0..1 + next(6) {
                let body: Vec<String> = (0..1 + next(40)).map(|_| format!("{}{}", words[next(words.len())], punct[next(punct.len())])).collect();
                sentences.push(format!("{}.", body.join(" ").trim_end_matches(['.', ',', ';', '!', '?'])));
            }
            paragraphs.push(sentences.join(" "));
        }
        let text = paragraphs.join("\n\n");
        let chunks = chunk_text(&text, 120, 30);
        assert!(!chunks.is_empty(), "{text}");
        let len = |c: &Chunk| c.text.chars().count();
        if chunks.len() > 1 {
            assert!(chunks.iter().all(|c| len(c) >= 30), "{text}");
        }
        // ponytail: a short last paragraph merging backward can push the final chunk past max+min
        assert!(chunks[..chunks.len() - 1].iter().all(|c| len(c) <= 120 + 30), "{text}");
        assert!(len(chunks.last().unwrap()) <= 120 + 2 * 30, "{text}");
    }
}

#[test]
fn scene_break_is_dropped_and_marks_the_previous_chunk() {
    let text = "The door closed behind him for good.\n\n* * *\n\nMorning came slowly over the hills.";
    assert_eq!(chunk_text(text, 300, 10),
        [chunk("The door closed behind him for good.", Scene), chunk("Morning came slowly over the hills.", Paragraph)]);
}

#[test]
fn short_chunk_before_a_scene_break_merges_backward_not_across_the_break() {
    let text = "The door closed behind him for good.\n\nThe end.\n\n#\n\nMorning came slowly over the hills.";
    assert_eq!(chunk_text(text, 300, 20),
        [chunk("The door closed behind him for good. The end.", Scene), chunk("Morning came slowly over the hills.", Paragraph)]);
}

#[test]
fn text_that_is_only_a_scene_break_produces_no_chunks() {
    assert_eq!(chunk_text("* * *", 300, 0), []);
}

#[test]
fn a_whole_scene_under_the_minimum_merges_across_the_break_rather_than_stand_alone() {
    let text = "The door closed behind him for good.\n\n* * *\n\nThe End.";
    assert_eq!(chunk_text(text, 300, 20), [chunk("The door closed behind him for good. The End.", Paragraph)]);
}
```

Append to `desktop/core/tests/text_cases.rs`:
```rust

#[test]
fn chunks() {
    for (i, case) in cases("chunks").iter().enumerate() {
        let (text, max, min) = (str_of(&case[0]), fixture::usize_of(&case[1]), fixture::usize_of(&case[2]));
        let chunks = audiobookify_core::text::chunk_text(text, max, min);
        let actual: Vec<(&str, &str)> = chunks.iter().map(|c| (c.text.as_str(), c.ends_with.as_str())).collect();
        let expected: Vec<(&str, &str)> = case[3].as_array().unwrap().iter().map(|c| (str_of(&c[0]), str_of(&c[1]))).collect();
        assert_eq!(actual, expected, "case {i}: {max}/{min} {text:?}");
    }
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test normalize_text --test text_cases`
Expected: compile errors, ``unresolved imports `audiobookify_core::text::chunk_text` ``, and so on.

- [ ] **Step 3: Write `desktop/core/src/text/chunk.rs`**

```rust
use std::sync::LazyLock;

use regex::Regex;

use super::normalize::{expand_abbreviations, expand_numbers, split_paragraphs, split_sentences, CLOSERS};
use crate::pystr::{self, WORD, WS};

/// Why a chunk ended, which sets the pause after it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EndsWith {
    Word,
    Clause,
    Sentence,
    Paragraph,
    Scene,
}

impl EndsWith {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Word => "word",
            Self::Clause => "clause",
            Self::Sentence => "sentence",
            Self::Paragraph => "paragraph",
            Self::Scene => "scene",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Chunk {
    pub text: String,
    pub ends_with: EndsWith,
}

impl Chunk {
    pub fn new(text: impl Into<String>, ends_with: EndsWith) -> Self {
        Self { text: text.into(), ends_with }
    }
}

// Chunk text never contains a newline (split_paragraphs collapsed it), so `$` here
// behaves like Python's `$`, which also matches before a trailing newline.
static ENDS_WITH_PUNCT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!(r"[.!?…,;:—–-][{CLOSERS}]*$")).unwrap());
// A chunk ending in an interrupted line ("if we were to—”", "or a—", "short--").
static TRAILING_DASH_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!(r"[{WS}]*(?:[—–]|-+)([{CLOSERS}]*)$")).unwrap());
// Clause split points, strongest first; a cut goes right after the match.
static CLAUSE_LEVELS: LazyLock<[Regex; 3]> = LazyLock::new(|| {
    [
        Regex::new(&format!(r"[;:][{WS}]+")).unwrap(),
        Regex::new(&format!(r"[{WS}][—–][{WS}]*|—[{WS}]*")).unwrap(),
        Regex::new(&format!(r",[{WS}]+")).unwrap(),
    ]
});
static WORD_GAP: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!(r"[{WS}]+")).unwrap());
static WORD_CHAR: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!("[{WORD}]")).unwrap());

fn is_scene_break(paragraph: &str) -> bool {
    !WORD_CHAR.is_match(paragraph) // "* * *", "#", "~~~", "⁂"
}

/// Largest cut position after a `pattern` match leaving both sides within bounds.
fn best_cut(text: &str, max_chars: usize, min_chars: usize, pattern: &Regex) -> Option<usize> {
    let mut best = None;
    for m in pattern.find_iter(text) {
        let left = pystr::len(pystr::strip(&text[..m.end()]));
        let right = pystr::len(pystr::strip(&text[m.end()..]));
        if min_chars <= left && left <= max_chars && right >= min_chars {
            best = Some(m.end());
        }
    }
    best
}

/// Split a sentence longer than max_chars at the strongest clause punctuation that
/// fits, falling back to a word boundary. The last piece's ends_with is a placeholder
/// the caller overwrites. Words are never broken, so one giant word stays oversized.
fn split_long_sentence(sentence: &str, max_chars: usize, min_chars: usize) -> Vec<Chunk> {
    let word_gap = std::slice::from_ref(&*WORD_GAP);
    let mut pieces = Vec::new();
    let mut rest = sentence.to_string();
    while pystr::len(&rest) > max_chars {
        let found = [(EndsWith::Clause, &CLAUSE_LEVELS[..]), (EndsWith::Word, word_gap)]
            .into_iter()
            .find_map(|(kind, patterns)| patterns.iter().find_map(|p| best_cut(&rest, max_chars, min_chars, p)).map(|cut| (cut, kind)))
            // No cut keeps both sides >= min_chars; respect max_chars alone.
            .or_else(|| best_cut(&rest, max_chars, 0, &WORD_GAP).map(|cut| (cut, EndsWith::Word)));
        let Some((cut, kind)) = found else { break }; // one giant word
        pieces.push(Chunk::new(pystr::strip(&rest[..cut]), kind));
        rest = pystr::strip(&rest[cut..]).to_string();
    }
    pieces.push(Chunk::new(rest, EndsWith::Sentence));
    pieces
}

fn chunk_paragraph(paragraph: &str, max_chars: usize, min_chars: usize) -> Vec<Chunk> {
    // Abbreviations are expanded before splitting so "Dr. Smith" isn't a sentence
    // break; numbers after, because "7 more" -> "seven more" would hide the boundary.
    let sentences: Vec<String> = split_sentences(&expand_abbreviations(paragraph)).iter().map(|s| expand_numbers(s)).collect();
    let mut packed = Vec::new();
    let mut current = String::new();
    for sentence in sentences {
        let candidate = pystr::strip(&format!("{current} {sentence}")).to_string();
        if pystr::len(&candidate) > max_chars && !current.is_empty() {
            packed.push(std::mem::replace(&mut current, sentence));
        } else {
            current = candidate;
        }
    }
    if !current.is_empty() {
        packed.push(current);
    }

    // A packed chunk only exceeds max_chars when it is a single sentence.
    let mut chunks = Vec::new();
    for text in packed {
        if pystr::len(&text) <= max_chars {
            chunks.push(Chunk::new(text, EndsWith::Sentence));
        } else {
            chunks.extend(split_long_sentence(&text, max_chars, min_chars));
        }
    }
    if let Some(last) = chunks.last_mut() {
        last.ends_with = EndsWith::Paragraph;
    }
    chunks
}

fn join(a: &Chunk, b: &Chunk) -> Chunk {
    // A merged paragraph with no closing punctuation (a heading like "Chapter One")
    // gets a period so the model pauses instead of running into the next sentence.
    let first = if ENDS_WITH_PUNCT_RE.is_match(&a.text) { a.text.clone() } else { format!("{}.", a.text) };
    Chunk::new(format!("{first} {}", b.text), b.ends_with)
}

/// A chunk under min_chars merges into the next one, or into the previous one when it
/// ends a scene or the text. It only crosses a scene break when the whole scene is
/// shorter than min_chars: a lost pause beats a garbled chunk.
///
/// ponytail: merging can push a chunk past max_chars by up to min_chars (2x for a
/// short final paragraph); max_chars is a quality limit, not the model's hard limit.
fn merge_short(chunks: Vec<Chunk>, min_chars: usize) -> Vec<Chunk> {
    let mut out: Vec<Chunk> = Vec::new();
    let mut pending: Option<Chunk> = None;
    for mut chunk in chunks {
        if let Some(p) = pending.take() {
            chunk = join(&p, &chunk);
        }
        if pystr::len(&chunk.text) >= min_chars {
            out.push(chunk);
        } else if chunk.ends_with == EndsWith::Scene && !out.is_empty() {
            let last = out.last_mut().expect("checked non-empty");
            *last = join(last, &chunk);
        } else {
            pending = Some(chunk);
        }
    }
    if let Some(p) = pending {
        match out.last_mut() {
            Some(last) => *last = join(last, &p),
            None => out.push(p),
        }
    }
    out
}

/// Give a chunk that ends in a dash a clean stop ("to—”" -> "to.”"). Turbo sometimes
/// babbled after a chunk-final dash. The pause after the chunk is unchanged.
fn close_interrupted(mut chunk: Chunk) -> Chunk {
    chunk.text = TRAILING_DASH_RE.replace(&chunk.text, ".${1}").into_owned();
    chunk
}

pub fn chunk_text(text: &str, max_chars: usize, min_chars: usize) -> Vec<Chunk> {
    let mut chunks: Vec<Chunk> = Vec::new();
    for paragraph in split_paragraphs(text) {
        if is_scene_break(&paragraph) {
            if let Some(last) = chunks.last_mut() {
                last.ends_with = EndsWith::Scene;
            }
            continue;
        }
        chunks.extend(chunk_paragraph(&paragraph, max_chars, min_chars));
    }
    merge_short(chunks, min_chars).into_iter().map(close_interrupted).collect()
}
```

- [ ] **Step 4: Register it in `desktop/core/src/text/mod.rs`**

```rust
//! The text pipeline: normalization, chunking, lexicon substitution and respelling,
//! ported from `src/audiobookify/normalize/text.py`, `lexicon/substitution.py` and
//! `phoneme/respelling.py`.

mod chunk;
mod lexicon;
mod normalize;
mod num2words;
mod respelling;

pub use chunk::{chunk_text, Chunk, EndsWith};
pub use lexicon::apply_lexicon;
pub use normalize::{expand_abbreviations, expand_numbers, normalize_text, split_paragraphs, split_sentences};
pub use respelling::phonemes_to_respelling;
```

- [ ] **Step 5: Run the tests**

Run: `cargo test -p audiobookify-core --test normalize_text --test text_cases`
Expected: `normalize_text` 35 passed, `text_cases` 9 passed. A `chunks` failure prints the case index, bounds and input. `case 0`-`19` are the Python tests' inputs, and the rest are random text.

- [ ] **Step 6: Commit**

```bash
git add desktop/core/src/text desktop/core/tests/normalize_text.rs desktop/core/tests/text_cases.rs
git commit -m "Port chunk_text to Rust"
```

---

### Task 6: Markdown ingest

**Files:**
- Create: `desktop/core/src/ingest/mod.rs`, `desktop/core/src/ingest/markdown.rs`
- Modify: `desktop/core/src/lib.rs`
- Test: `desktop/core/tests/ingest_markdown.rs`

**Interfaces:**
- Consumes: `models::RawChapter` (phase 1), `pystr::{WS, strip}`.
- Produces:
  - `audiobookify_core::ingest::ParsedBook { pub title: String, pub author: Option<String>, pub chapters: Vec<RawChapter> }`
  - `audiobookify_core::ingest::IngestError` (`UnsupportedFormat(String)`, `Io(std::io::Error)`, `Epub(String)`)
  - `audiobookify_core::ingest::markdown::parse(path: impl AsRef<Path>, heading_level: usize) -> Result<ParsedBook, IngestError>`

- [ ] **Step 1: Write the failing tests, `desktop/core/tests/ingest_markdown.rs`**

```rust
//! Port of tests/test_ingest_markdown.py.

use audiobookify_core::ingest::markdown;

const SAMPLE_MD: &str = "# Chapter One: A Beginning

This is **bold** and _italic_ text with a [link](https://example.com).

# Chapter Two

More `code` and content here.
";

fn write(name: &str, text: &str) -> (tempfile::TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join(name);
    std::fs::write(&path, text).unwrap();
    (dir, path)
}

#[test]
fn parse_markdown_splits_on_top_level_headings() {
    let (_dir, path) = write("book.md", SAMPLE_MD);

    let parsed = markdown::parse(&path, 1).unwrap();

    let titles: Vec<&str> = parsed.chapters.iter().map(|c| c.title.as_str()).collect();
    assert_eq!(titles, ["Chapter One: A Beginning", "Chapter Two"]);
    let text = &parsed.chapters[0].text;
    assert!(text.contains("bold"));
    assert!(!text.contains("**"));
    assert!(text.contains("link"));
    assert!(!text.contains("https://example.com"));
}

#[test]
fn parse_markdown_with_no_headings_returns_single_chapter() {
    let (_dir, path) = write("flat.md", "Just plain text, no headings.");

    let parsed = markdown::parse(&path, 1).unwrap();

    assert_eq!(parsed.chapters.len(), 1);
    assert_eq!(parsed.chapters[0].title, "flat");
}

#[test]
fn parse_markdown_respects_configurable_heading_level() {
    let (_dir, path) = write("nested.md", "## Part A\nfirst\n## Part B\nsecond\n");

    let parsed = markdown::parse(&path, 2).unwrap();

    let titles: Vec<&str> = parsed.chapters.iter().map(|c| c.title.as_str()).collect();
    assert_eq!(titles, ["Part A", "Part B"]);
}

#[test]
fn parse_markdown_reads_crlf_and_cr_as_newlines() {
    let (_dir, path) = write("crlf.md", "# One\r\nfirst\r# Two\r\nsecond");

    let parsed = markdown::parse(&path, 1).unwrap();

    let chapters: Vec<(&str, &str)> = parsed.chapters.iter().map(|c| (c.title.as_str(), c.text.as_str())).collect();
    assert_eq!(chapters, [("One", "first"), ("Two", "second")]);
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test ingest_markdown`
Expected: compile error, ``could not find `ingest` in `audiobookify_core` ``.

- [ ] **Step 3: Write `desktop/core/src/ingest/mod.rs`**

```rust
//! Books in, chapters out: `src/audiobookify/ingest/` in Rust.

pub mod markdown;

use crate::models::RawChapter;

#[derive(Debug, Clone, PartialEq)]
pub struct ParsedBook {
    pub title: String,
    pub author: Option<String>,
    pub chapters: Vec<RawChapter>,
}

#[derive(Debug, thiserror::Error)]
pub enum IngestError {
    #[error("unsupported input format: {0}")]
    UnsupportedFormat(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error("bad EPUB: {0}")]
    Epub(String),
}
```

- [ ] **Step 4: Write `desktop/core/src/ingest/markdown.rs`**

```rust
use std::path::Path;
use std::sync::LazyLock;

use regex::{Captures, Regex};

use super::{IngestError, ParsedBook};
use crate::models::RawChapter;
use crate::pystr::{self, WS};

static CODE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"`{1,3}[^`]*`{1,3}").unwrap());
static IMAGE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"!\[([^\]]*)\]\([^)]*\)").unwrap());
static LINK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\[([^\]]*)\]\([^)]*\)").unwrap());
static EMPHASIS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[*_]{1,3}([^*_]+)[*_]{1,3}").unwrap());
static HEADING_MARK: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!(r"(?m)^#{{1,6}}[{WS}]*")).unwrap());

fn strip_markdown(text: &str) -> String {
    let text = CODE.replace_all(text, |c: &Captures| c[0].trim_matches('`').to_string());
    let text = IMAGE.replace_all(&text, "${1}");
    let text = LINK.replace_all(&text, "${1}");
    let text = EMPHASIS.replace_all(&text, "${1}");
    let text = HEADING_MARK.replace_all(&text, "");
    pystr::strip(&text).to_string()
}

pub fn parse(path: impl AsRef<Path>, heading_level: usize) -> Result<ParsedBook, IngestError> {
    let path = path.as_ref();
    // Path.read_text() decodes with universal newlines: "\r\n" and "\r" become "\n".
    let raw = std::fs::read_to_string(path)?.replace("\r\n", "\n").replace('\r', "\n");
    let title = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();

    let pattern = Regex::new(&format!(r"(?m)^{}[{WS}]+(.+)$", "#".repeat(heading_level))).expect("valid heading regex");
    let matches: Vec<Captures> = pattern.captures_iter(&raw).collect();
    if matches.is_empty() {
        let chapters = vec![RawChapter { title: title.clone(), text: strip_markdown(&raw) }];
        return Ok(ParsedBook { title, author: None, chapters });
    }

    let chapters = matches
        .iter()
        .enumerate()
        .map(|(i, m)| {
            let start = m.get(0).expect("group 0").end();
            let end = matches.get(i + 1).map_or(raw.len(), |next| next.get(0).expect("group 0").start());
            RawChapter { title: pystr::strip(&m[1]).to_string(), text: strip_markdown(&raw[start..end]) }
        })
        .collect();
    Ok(ParsedBook { title, author: None, chapters })
}
```

- [ ] **Step 5: Register the module in `desktop/core/src/lib.rs`**

```rust
pub mod db;
pub mod ingest;
pub mod models;
pub mod pystr;
pub mod text;
pub mod voice_storage;
```

- [ ] **Step 6: Run the tests**

Run: `cargo test -p audiobookify-core --test ingest_markdown`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add desktop/core/src/lib.rs desktop/core/src/ingest desktop/core/tests/ingest_markdown.rs
git commit -m "Port Markdown ingest to Rust"
```

---

### Task 7: Spine documents (HTML)

**Files:**
- Create: `desktop/core/src/ingest/html.rs`
- Modify: `desktop/core/src/ingest/mod.rs`

**Interfaces:**
- Consumes: `pystr::{collapse, strip}`.
- Produces (crate-internal, for Task 8): `ingest::html::Document::parse(&str) -> Document`, `Document::block_text(&self) -> String` (`_block_text`), `Document::first_heading(&self) -> Option<String>` (`soup.find(["h1", "h2"]).get_text(strip=True)`; `None` when there is no h1/h2).

The expected values in this task's test table came from running each page through ebooklib's `EpubHtml.get_content()` and then the code in `ingest/epub.py`. To check a new case the same way:
```bash
"$MAIN/.venv/bin/python" -W ignore -c "
import sys; from bs4 import BeautifulSoup; from ebooklib import epub as E; from audiobookify.ingest.epub import _block_text
it = E.EpubHtml(file_name='x.xhtml'); it.content = sys.argv[1].encode(); it.book = E.EpubBook()
s = BeautifulSoup(it.get_content(), 'html.parser')
for t in s(['head', 'nav', 'script', 'style']): t.decompose()
h = s.find(['h1', 'h2']); print(repr(_block_text(s)), repr(h.get_text(strip=True) if h else None))" '<body><p>page</p></body>'
```

- [ ] **Step 1: Write the failing test with a stub**

Create `desktop/core/src/ingest/html.rs` with a stub and the test module:
```rust
pub struct Document;

impl Document {
    pub fn parse(_html: &str) -> Self {
        Document
    }

    pub fn block_text(&self) -> String {
        todo!()
    }

    pub fn first_heading(&self) -> Option<String> {
        todo!()
    }
}

#[cfg(test)]
mod tests {
    use super::Document;

    /// (name, page, block text, first heading). Expected values are what
    /// `ingest/epub.py` produces for each page through ebooklib + BeautifulSoup.
    const CASES: &[(&str, &str, &str, Option<&str>)] = &[
        ("calibre_self_closing_heading", "<body><h1 id=\"h\" class=\"c\"/><p>Text after.</p></body>", "Text after.", Some("")),
        ("nested_p", "<body>\n<p>a<p>b</p>c</p>\n</body>", "a\n\nb\n\nc", None),
        ("unclosed_div", "<body>\n<div><p>a</div>b\n</body>", "a\n\nb", None),
        ("inline_markup_and_wrapping", "<body>\n<p>He was <em>very</em> tired,\n   <span>so</span> he <a href='#n1'>slept</a>.</p>\n</body>", "He was very tired, so he slept.", None),
        ("br_is_a_space", "<body>\n<p>Roses are red<br/>violets are blue.</p>\n</body>", "Roses are red violets are blue.", None),
        ("br_without_slash_and_stray_end_tag", "<body>\n<p>one<br>two</br>three</p>\n</body>", "one twothree", None),
        ("heading_strings_are_glued", "<body>\n<h1>Chapter <em>One</em> x</h1>\n</body>", "Chapter One x", Some("ChapterOnex")),
        ("heading_skips_comments_and_cdata", "<body>\n<h2>Part<!-- x --> <em>Two</em><br/>Again<![CDATA[!]]></h2><p>p</p>\n</body>", "Part Two Again\n\np", Some("PartTwoAgain")),
        ("head_nav_script_style_are_dropped", "<html><head><title>T</title><style>p{}</style></head><body>\n<nav><p>nav</p></nav><script>if (a<b) {}</script><p>kept</p>\n</body></html>", "kept", None),
        ("loose_text_between_blocks", "<body>\n<div>Loose <b>div</b> text<p>nested para</p>tail of div</div>\n</body>", "Loose div text\n\nnested para\n\ntail of div", None),
        ("entities", "<body>\n<p>First&nbsp;line &amp; more&#8212;and &#x2014; &copy &#151; &foo; &lt;tag&gt;</p>\n</body>", "First line & more—and — © — &foo; <tag>", None),
        ("ruby_and_template_text", "<body>\n<p>ruby <ruby>漢<rt>kan</rt></ruby> and <template>tmpl</template> end</p>\n</body>", "ruby 漢kan and tmpl end", None),
        ("calibre_self_closing_span_and_anchor", "<body>\n<p><span class=\"x\"/>One <a id=\"p1\"/>two.</p><p>Three.</p>\n</body>", "One two.\n\nThree.", None),
        ("table_cells", "<body>\n<table><tr><td>cell1</td><td>cell2</td></tr></table>\n</body>", "cell1\n\ncell2", None),
        ("first_heading_is_h1_or_h2_in_document_order", "<body>\n<h3>Third</h3><h2>Second</h2><h1>First</h1>\n</body>", "Third\n\nSecond\n\nFirst", Some("Second")),
        ("cr_and_crlf_become_newlines", "<body>\n<h1>Byte\r\nOrder</h1>\r\n<p>x\ry</p>\n</body>", "Byte Order\n\nx y", Some("Byte\nOrder")),
        ("nul_becomes_replacement_char", "<body>\n<p>a\0b</p>\n</body>", "a�b", None),
        ("minified_inline_siblings_get_a_space", "<body><p><span>a</span><span>b</span></p></body>", "a b", None),
        ("minified_text_child_turns_padding_off", "<body><p><span>Small</span><span>Caps</span> glued.</p></body>", "SmallCaps glued.", None),
        ("leading_body_text_is_dropped", "<body>lead<p>para</p>tail</body>", "para\n\ntail", None),
        ("content_after_body_is_dropped", "<body>\n<p>in</p>\n</body><p>after</p>", "in", None),
        ("no_body_element", "<p>just a fragment</p>", "just a fragment", None),
    ];

    #[test]
    fn pages_read_like_the_python_pipeline() {
        for (name, page, text, heading) in CASES {
            let doc = Document::parse(page);
            assert_eq!(doc.block_text(), *text, "{name}: block text");
            assert_eq!(doc.first_heading().as_deref(), *heading, "{name}: heading");
        }
    }
}
```

Register it in `desktop/core/src/ingest/mod.rs` by replacing the `pub mod markdown;` line with:
```rust
#[allow(dead_code)] // epub.rs (Task 8) is its only caller
mod html;
pub mod markdown;
```

- [ ] **Step 2: Run it to see it fail**

Run: `cargo test -p audiobookify-core --lib ingest::html`
Expected: `pages_read_like_the_python_pipeline` FAILED, panicked at `not yet implemented`.

- [ ] **Step 3: Replace the stub (everything above `#[cfg(test)]`) with the implementation**

```rust
//! One EPUB spine document -> (paragraph text, first h1/h2 text), matching what
//! `ingest/epub.py` gets today. That is not BeautifulSoup over the file: ebooklib's
//! `EpubHtml.get_content()` first parses the page with libxml2 (lxml), keeps only the
//! children of `<body>` and re-serializes with `pretty_print=True`, and BeautifulSoup
//! (html.parser) reads that. This module reproduces the effects that change the text:
//!
//! - Tokens come from html5ever's tokenizer, which follows the HTML5 tokenizing rules
//!   libxml2 2.14 uses (entities, CR/LF, `<![CDATA[` as a comment, NUL as U+FFFD).
//! - The tree follows html.parser's rules, not HTML5 tree construction: `<tag/>` closes
//!   any tag, nothing closes implicitly, and an end tag pops to the most recent open
//!   tag of that name. Calibre writes empty headings as `<h1 id=".."/>`; an HTML5 tree
//!   builder would leave that h1 open around the whole page.
//! - Only `<body>`'s subtree counts, and text before its first child is dropped.
//! - Pretty-printing: an element whose children include no text (and whose ancestors
//!   are the same) gets whitespace around each child, so `<p><span>a</span><span>b</span></p>`
//!   reads "a b".

use std::cell::RefCell;

use html5ever::tendril::StrTendril;
use html5ever::tokenizer::states::RawKind;
use html5ever::tokenizer::{BufferQueue, TagKind, Token, TokenSink, TokenSinkResult, Tokenizer};

use crate::pystr;

const BLOCK_TAGS: [&str; 13] = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr", "td", "th"];
const SKIPPED_TAGS: [&str; 4] = ["head", "nav", "script", "style"]; // decomposed before reading
/// html.parser closes these at once (BeautifulSoup's HTMLTreeBuilder empty-element tags).
const VOID_TAGS: [&str; 24] = [
    "area", "base", "basefont", "bgsound", "br", "col", "command", "embed", "frame", "hr", "image", "img",
    "input", "isindex", "keygen", "link", "menuitem", "meta", "nextid", "param", "source", "spacer", "track", "wbr",
];
/// BeautifulSoup gives strings under these their own class, which get_text() skips.
const STRING_CONTAINERS: [&str; 3] = ["template", "rt", "rp"];

enum Tok {
    Start(String, bool),
    End(String),
    Text(String),
    Comment,
}

struct Sink(RefCell<Vec<Tok>>);

impl TokenSink for Sink {
    type Handle = ();

    fn process_token(&self, token: Token, _line: u64) -> TokenSinkResult<()> {
        let mut toks = self.0.borrow_mut();
        match token {
            Token::TagToken(tag) if tag.kind == TagKind::EndTag => toks.push(Tok::End(tag.name.to_string())),
            Token::TagToken(tag) => {
                let name = tag.name.to_string();
                // html.parser's raw-text elements (Python 3.12), unless written as <tag/>.
                let raw = match name.as_str() {
                    "script" | "style" | "xmp" | "iframe" | "noembed" | "noframes" => Some(RawKind::Rawtext),
                    "textarea" | "title" => Some(RawKind::Rcdata),
                    _ => None,
                };
                let self_closing = tag.self_closing;
                toks.push(Tok::Start(name, self_closing));
                if let (Some(kind), false) = (raw, self_closing) {
                    return TokenSinkResult::RawData(kind);
                }
            }
            Token::CharacterTokens(text) => toks.push(Tok::Text(text.to_string())),
            Token::NullCharacterToken => toks.push(Tok::Text("\u{fffd}".to_string())),
            Token::CommentToken(_) | Token::DoctypeToken(_) => toks.push(Tok::Comment),
            Token::EOFToken | Token::ParseError(_) => {}
        }
        TokenSinkResult::Continue
    }
}

fn tokenize(html: &str) -> Vec<Tok> {
    let input = BufferQueue::default();
    input.push_back(StrTendril::from(html));
    let tokenizer = Tokenizer::new(Sink(RefCell::new(Vec::new())), Default::default());
    let _ = tokenizer.feed(&input);
    tokenizer.end();
    tokenizer.sink.0.take()
}

enum Kind {
    Element(String),
    /// `in_heading` is false for strings under template/rt/rp (BeautifulSoup's
    /// TemplateString/RubyTextString), which _block_text keeps but get_text() skips.
    Text { text: String, in_heading: bool },
    Comment,
}

struct Node {
    kind: Kind,
    children: Vec<usize>,
}

/// A parsed spine document. `nodes[0]` is the root.
pub struct Document {
    nodes: Vec<Node>,
}

impl Document {
    pub fn parse(html: &str) -> Self {
        let mut doc = Document { nodes: vec![Node { kind: Kind::Element(String::new()), children: Vec::new() }] };
        let mut stack = vec![0usize];
        let mut names: Vec<String> = vec![String::new()]; // tag names along `stack`
        let mut already_closed: Vec<String> = Vec::new(); // void tags whose end tag we ignore once
        let mut text = String::new();

        let flush = |doc: &mut Document, stack: &[usize], names: &[String], text: &mut String| {
            if text.is_empty() {
                return;
            }
            let in_heading = !names.iter().any(|n| STRING_CONTAINERS.contains(&n.as_str()));
            let parent = *stack.last().expect("root never pops");
            doc.add(parent, Kind::Text { text: std::mem::take(text), in_heading });
        };

        for tok in tokenize(html) {
            if let Tok::Text(t) = &tok {
                text.push_str(t);
                continue;
            }
            flush(&mut doc, &stack, &names, &mut text);
            match tok {
                Tok::Start(name, self_closing) => {
                    let id = doc.add(*stack.last().expect("root never pops"), Kind::Element(name.clone()));
                    if self_closing {
                        continue; // opened and closed at once
                    }
                    if VOID_TAGS.contains(&name.as_str()) {
                        already_closed.push(name);
                        continue;
                    }
                    stack.push(id);
                    names.push(name);
                }
                Tok::End(name) => {
                    if let Some(i) = already_closed.iter().position(|n| *n == name) {
                        already_closed.remove(i);
                    } else if let Some(depth) = names.iter().rposition(|n| *n == name).filter(|d| *d > 0) {
                        stack.truncate(depth);
                        names.truncate(depth);
                    }
                }
                Tok::Comment => {
                    doc.add(*stack.last().expect("root never pops"), Kind::Comment);
                }
                Tok::Text(_) => unreachable!("handled above"),
            }
        }
        flush(&mut doc, &stack, &names, &mut text);
        doc
    }

    fn add(&mut self, parent: usize, kind: Kind) -> usize {
        self.nodes.push(Node { kind, children: Vec::new() });
        let id = self.nodes.len() - 1;
        self.nodes[parent].children.push(id);
        id
    }

    fn name(&self, id: usize) -> Option<&str> {
        match &self.nodes[id].kind {
            Kind::Element(name) => Some(name),
            _ => None,
        }
    }

    fn is_text(&self, id: usize) -> bool {
        matches!(self.nodes[id].kind, Kind::Text { .. })
    }

    /// The body ebooklib keeps: the first `<body>` (or the whole document when there is
    /// none), minus the text before its first child element or comment.
    fn body(&self) -> (usize, Vec<usize>) {
        let body = (0..self.nodes.len()).find(|&id| self.name(id) == Some("body")).unwrap_or(0);
        let children = self.nodes[body].children.iter().copied().skip_while(|&c| self.is_text(c)).collect();
        (body, children)
    }

    /// Strings in document order as (text, nearest block ancestor, counts for headings),
    /// with head/nav/script/style removed, `<br>` read as " ", and pretty-print whitespace.
    fn strings(&self, children: &[usize], formatted: bool, block: Option<usize>, out: &mut Vec<(String, Option<usize>, bool)>) {
        let formatted = formatted && !children.iter().any(|&c| self.is_text(c));
        let pad = |out: &mut Vec<(String, Option<usize>, bool)>| {
            if formatted {
                out.push((" ".to_string(), block, true));
            }
        };
        for &child in children {
            pad(out);
            match &self.nodes[child].kind {
                Kind::Text { text, in_heading } => out.push((text.clone(), block, *in_heading)),
                Kind::Comment => {}
                Kind::Element(name) if SKIPPED_TAGS.contains(&name.as_str()) => {}
                Kind::Element(name) if name == "br" => out.push((" ".to_string(), block, true)),
                Kind::Element(name) => {
                    let inner = if BLOCK_TAGS.contains(&name.as_str()) { Some(child) } else { block };
                    self.strings(&self.nodes[child].children, formatted, inner, out);
                }
            }
        }
        if !children.is_empty() {
            pad(out);
        }
    }

    /// `_block_text`: text under one block element joined as-is, paragraphs joined by
    /// blank lines, whitespace inside a paragraph collapsed.
    pub fn block_text(&self) -> String {
        let (body, children) = self.body();
        let mut strings = Vec::new();
        let block = if BLOCK_TAGS.contains(&self.name(body).unwrap_or("")) { Some(body) } else { None };
        self.strings(&children, true, block, &mut strings);

        let mut paragraphs = Vec::new();
        let mut current: Vec<&str> = Vec::new();
        let mut current_block = None;
        for (text, block, _) in &strings {
            if *block != current_block && !current.is_empty() {
                paragraphs.push(pystr::collapse(&current.concat()));
                current.clear();
            }
            current.push(text);
            current_block = *block;
        }
        if !current.is_empty() {
            paragraphs.push(pystr::collapse(&current.concat()));
        }
        paragraphs.into_iter().filter(|p| !p.is_empty()).collect::<Vec<_>>().join("\n\n")
    }

    /// `soup.find(["h1", "h2"]).get_text(strip=True)`: each string stripped, empty ones
    /// dropped, the rest glued together with nothing between them.
    pub fn first_heading(&self) -> Option<String> {
        let (_, children) = self.body();
        let heading = self.find_heading(&children)?;
        let mut strings = Vec::new();
        self.strings(&self.nodes[heading].children, false, None, &mut strings); // padding is stripped anyway
        Some(strings.iter().filter(|(_, _, in_heading)| *in_heading).map(|(t, _, _)| pystr::strip(t)).collect())
    }

    fn find_heading(&self, children: &[usize]) -> Option<usize> {
        children.iter().find_map(|&c| match self.name(c) {
            Some(n) if SKIPPED_TAGS.contains(&n) => None,
            Some("h1" | "h2") => Some(c),
            Some(_) => self.find_heading(&self.nodes[c].children),
            None => None,
        })
    }
}
```

- [ ] **Step 4: Run the test**

Run: `cargo test -p audiobookify-core --lib ingest::html`
Expected: 1 passed. A failure names the case and whether the block text or the heading differs.

- [ ] **Step 5: Commit**

```bash
git add desktop/core/src/ingest
git commit -m "Read EPUB spine documents the way ebooklib and BeautifulSoup do"
```

---

### Task 8: EPUB ingest and dispatch

**Files:**
- Create: `desktop/core/src/ingest/epub.rs`
- Modify: `desktop/core/src/ingest/mod.rs`
- Test: `desktop/core/tests/ingest_epub.rs`, `desktop/core/tests/ingest_dispatch.rs`, `desktop/core/tests/normalize_text.rs` (append)

**Interfaces:**
- Consumes: `ingest::html::Document`, the EPUB fixtures from Task 1, `models::SourceFormat` (phase 1).
- Produces:
  - `audiobookify_core::ingest::epub::parse(path: impl AsRef<Path>) -> Result<ParsedBook, IngestError>`
  - `audiobookify_core::ingest::ingest_by_path(path: impl AsRef<Path>, heading_level: usize) -> Result<(ParsedBook, SourceFormat), IngestError>`

- [ ] **Step 1: Write the failing tests**

`desktop/core/tests/ingest_epub.rs`:
```rust
//! Port of tests/test_ingest_epub.py. The Python tests build their EPUBs with
//! ebooklib at test time; here the same books are committed fixtures written by
//! tests/fixtures/text/generate.py (epub/basic.epub, blocks.epub, ...).

mod fixture;

use audiobookify_core::ingest::epub;
use audiobookify_core::text::{chunk_text, Chunk, EndsWith};
use fixture::fixtures;

const FILLER: &str = "The rain kept on falling over the quiet town all through the night. The rain kept on falling over the quiet town all through the night. The rain kept on falling over the quiet town all through the night. The rain kept on falling over the quiet town all through the night.";

fn chapter_text(name: &str) -> String {
    epub::parse(fixtures().join("epub").join(name)).unwrap().chapters.remove(0).text
}

#[test]
fn parse_epub_extracts_title_author_and_skips_short_boilerplate() {
    let parsed = epub::parse(fixtures().join("epub/basic.epub")).unwrap();

    assert_eq!(parsed.title, "My Test Book");
    assert_eq!(parsed.author.as_deref(), Some("Jane Author"));
    let titles: Vec<&str> = parsed.chapters.iter().map(|c| c.title.as_str()).collect();
    assert_eq!(titles, ["Chapter One", "Chapter Two"]);
    assert!(parsed.chapters[0].text.contains("Once upon a time."));
}

#[test]
fn parse_epub_separates_block_elements_with_blank_lines() {
    let text = chapter_text("blocks.epub");
    assert_eq!(text.split("\n\n").collect::<Vec<_>>(), ["Chapter One", FILLER, "Second paragraph."]);
}

#[test]
fn parse_epub_keeps_inline_markup_and_source_wrapping_inside_one_paragraph() {
    assert_eq!(chapter_text("inline.epub").split("\n\n").nth(1), Some("He was very tired, so he slept."));
}

#[test]
fn parse_epub_treats_br_as_a_space() {
    assert_eq!(chapter_text("br.epub").split("\n\n").nth(1), Some("Roses are red violets are blue."));
}

#[test]
fn real_epub_output_chunks_into_paragraph_aware_chunks() {
    let chunks = chunk_text(&chapter_text("scene.epub"), 300, 80);
    assert_eq!(chunks, [
        Chunk::new(format!("Chapter One. {FILLER} “No.”"), EndsWith::Scene),
        Chunk::new("Morning came, and with it a terrible silence that settled over the whole valley below.", EndsWith::Paragraph),
    ]);
}

#[test]
fn spine_rules_follow_ebooklib() {
    let parsed = epub::parse(fixtures().join("epub/handmade.epub")).unwrap();

    assert_eq!(parsed.title, "Handmade Book"); // the first of two dc:title elements
    assert_eq!(parsed.author, None);
    // nav is a chapter; the short page, the text/html page, the dangling idref and the
    // properties="cover" page are not; Calibre's empty <h1/> gives an empty title.
    let titles: Vec<&str> = parsed.chapters.iter().map(|c| c.title.as_str()).collect();
    assert_eq!(titles, ["Chapter 1", "", "PartTwoAgain", "Aside", "Byte\n\nOrder", "ChapterNine", "Bad\u{fffd}bytes"]);
}
```

`desktop/core/tests/ingest_dispatch.rs`:
```rust
//! Port of tests/test_ingest_dispatch.py.

use audiobookify_core::ingest::{ingest_by_path, IngestError};
use audiobookify_core::models::SourceFormat;

#[test]
fn ingest_by_path_parses_markdown() {
    let dir = tempfile::tempdir().unwrap();
    let md_path = dir.path().join("book.md");
    std::fs::write(&md_path, "# Chapter One\nBody.\n").unwrap();

    let (parsed, source_format) = ingest_by_path(&md_path, 1).unwrap();

    assert_eq!(source_format, SourceFormat::Markdown);
    assert_eq!(parsed.chapters[0].title, "Chapter One");
}

#[test]
fn ingest_by_path_rejects_unsupported_suffix() {
    let dir = tempfile::tempdir().unwrap();
    let bad_path = dir.path().join("book.txt");
    std::fs::write(&bad_path, "hello").unwrap();

    let err = ingest_by_path(&bad_path, 1).unwrap_err();

    assert!(matches!(err, IngestError::UnsupportedFormat(_)));
    assert_eq!(err.to_string(), "unsupported input format: .txt");
}

#[test]
fn ingest_by_path_matches_the_suffix_case_insensitively() {
    let dir = tempfile::tempdir().unwrap();
    let md_path = dir.path().join("BOOK.MD");
    std::fs::write(&md_path, "Body.").unwrap();
    assert_eq!(ingest_by_path(&md_path, 1).unwrap().1, SourceFormat::Markdown);
}
```

Append to `desktop/core/tests/normalize_text.rs` (the two Python tests that chunk real EPUB output):
```rust

mod fixture;

use audiobookify_core::ingest::epub;
use fixture::fixtures;

fn epub_chapter_text(name: &str) -> String {
    epub::parse(fixtures().join("epub").join(name)).unwrap().chapters.remove(0).text
}

#[test]
fn an_interrupted_line_ending_a_chunk_gets_a_clean_stop() {
    // Turbo babbled or repeated words after chunks ending in a dash (1 in 20 runs
    // with the dash, 0 in 20 with a period); the pause stays a paragraph pause.
    let chunks = chunk_text(&epub_chapter_text("dashes.epub"), 300, 80);
    let last_words: Vec<&str> = chunks.iter().map(|c| c.text.rsplit(' ').next().unwrap()).collect();
    assert_eq!(last_words, ["to.”", "a.", "look.’", "short.", "quickly."]);
    assert!(chunks.iter().all(|c| c.ends_with == Paragraph));
}

#[test]
fn dashes_inside_a_chunk_are_left_alone() {
    let chunks = chunk_text(&epub_chapter_text("dashes_inside.epub"), 300, 80);
    assert!(chunks[0].text.contains("paused—only for a moment—and went on."));
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test ingest_epub --test ingest_dispatch --test normalize_text`
Expected: compile errors, ``could not find `epub` in `ingest` `` and ``unresolved import `audiobookify_core::ingest::ingest_by_path` ``.

- [ ] **Step 3: Write `desktop/core/src/ingest/epub.rs`**

```rust
//! EPUB -> chapters, matching `ingest/epub.py` on top of ebooklib 0.20.
//!
//! ebooklib's rules that decide which spine entries become chapters:
//! - The OPF is the last `rootfile` in META-INF/container.xml with media type
//!   application/oebps-package+xml; hrefs are percent-decoded and resolved against
//!   its directory.
//! - A spine `itemref` names the first manifest item with that id. It is a document
//!   only when its media type is exactly application/xhtml+xml and its properties
//!   don't include "cover" (ebooklib renders its own cover page for those). The nav
//!   document counts as a document.
//! - The title is the first `dc:title` directly under `<metadata>`, else the file
//!   stem; the author is the first `dc:creator`.

use std::fs::File;
use std::io::Read;
use std::path::Path;

use roxmltree::{Document as Xml, Node};
use zip::ZipArchive;

use super::html::Document;
use super::{IngestError, ParsedBook};
use crate::models::RawChapter;
use crate::pystr;

const MIN_CHAPTER_CHARS: usize = 200;
const CONTAINER_NS: &str = "urn:oasis:names:tc:opendocument:xmlns:container";
const OPF_NS: &str = "http://www.idpf.org/2007/opf";
const DC_NS: &str = "http://purl.org/dc/elements/1.1/";

fn bad(message: impl Into<String>) -> IngestError {
    IngestError::Epub(message.into())
}

fn read(zip: &mut ZipArchive<File>, name: &str) -> Result<Vec<u8>, IngestError> {
    let mut file = zip.by_name(name).map_err(|e| bad(format!("{name}: {e}")))?;
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    Ok(bytes)
}

fn xml_text(bytes: &[u8], name: &str) -> Result<String, IngestError> {
    let text = String::from_utf8(bytes.to_vec()).map_err(|_| bad(format!("{name} is not UTF-8")))?;
    Ok(text.strip_prefix('\u{feff}').map(str::to_string).unwrap_or(text))
}

fn child<'a, 'i>(node: Node<'a, 'i>, ns: &str, name: &str) -> Option<Node<'a, 'i>> {
    node.children().find(|c| c.tag_name().namespace() == Some(ns) && c.tag_name().name() == name)
}

/// `urllib.parse.unquote`
fn unquote(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let hex = |b: u8| (b as char).to_digit(16);
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let (Some(h), Some(l)) = (hex(bytes[i + 1]), hex(bytes[i + 2])) {
                out.push((h * 16 + l) as u8);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `posixpath.normpath` for the relative paths inside a zip.
fn normpath(path: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." if parts.last().is_some_and(|p| *p != "..") => {
                parts.pop();
            }
            _ => parts.push(part),
        }
    }
    parts.join("/")
}

struct Item {
    id: Option<String>,
    path: String,
    is_document: bool,
}

pub fn parse(path: impl AsRef<Path>) -> Result<ParsedBook, IngestError> {
    let path = path.as_ref();
    let mut zip = ZipArchive::new(File::open(path)?).map_err(|e| bad(e.to_string()))?;

    let container = xml_text(&read(&mut zip, "META-INF/container.xml")?, "META-INF/container.xml")?;
    let container = Xml::parse(&container).map_err(|e| bad(format!("container.xml: {e}")))?;
    let opf_path = container
        .descendants()
        .filter(|n| n.tag_name().namespace() == Some(CONTAINER_NS) && n.tag_name().name() == "rootfile")
        .filter(|n| n.attribute("media-type") == Some("application/oebps-package+xml"))
        .filter_map(|n| n.attribute("full-path"))
        .next_back() // ebooklib keeps the last one
        .ok_or_else(|| bad("container.xml names no OPF file"))?
        .to_string();
    let opf_dir = opf_path.rsplit_once('/').map_or("", |(dir, _)| dir).to_string();

    let opf = xml_text(&read(&mut zip, &normpath(&opf_path))?, &opf_path)?;
    let opf = Xml::parse(&opf).map_err(|e| bad(format!("{opf_path}: {e}")))?;
    let package = opf.root_element();

    let metadata = child(package, OPF_NS, "metadata");
    let first_dc = |name: &str| -> Option<String> {
        metadata?.children().find(|c| c.tag_name().namespace() == Some(DC_NS) && c.tag_name().name() == name).map(|c| c.text().unwrap_or("").to_string())
    };
    let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    // ponytail: an empty <dc:title/> gives Python a None title (and a failed import); fall back to the stem.
    let title = first_dc("title").filter(|t| !t.is_empty()).unwrap_or(stem);
    let author = first_dc("creator").filter(|a| !a.is_empty());

    let items: Vec<Item> = child(package, OPF_NS, "manifest")
        .map(|manifest| {
            manifest
                .children()
                .filter(|n| n.tag_name().namespace() == Some(OPF_NS) && n.tag_name().name() == "item")
                .map(|n| {
                    let properties: Vec<&str> = n.attribute("properties").unwrap_or("").split(' ').collect();
                    let href = unquote(n.attribute("href").unwrap_or(""));
                    Item {
                        id: n.attribute("id").map(str::to_string),
                        path: normpath(&format!("{opf_dir}/{href}")),
                        is_document: n.attribute("media-type") == Some("application/xhtml+xml") && !properties.contains(&"cover"),
                    }
                })
                .collect()
        })
        .unwrap_or_default();

    let spine: Vec<String> = child(package, OPF_NS, "spine")
        .map(|spine| spine.children().filter(|n| n.is_element()).filter_map(|n| n.attribute("idref")).map(str::to_string).collect())
        .unwrap_or_default();

    let mut chapters = Vec::new();
    for idref in &spine {
        let Some(item) = items.iter().find(|i| i.id.as_deref() == Some(idref.as_str())) else { continue };
        if !item.is_document {
            continue;
        }
        let bytes = read(&mut zip, &item.path)?;
        // lxml is told the page is UTF-8 whatever it declares; bad bytes become U+FFFD.
        let html = String::from_utf8_lossy(bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes)).into_owned();
        let doc = Document::parse(&html);
        let text = doc.block_text();
        if pystr::len(&text) < MIN_CHAPTER_CHARS {
            continue; // skips cover pages, copyright blurbs, blank dividers
        }
        let title = doc.first_heading().unwrap_or_else(|| format!("Chapter {}", chapters.len() + 1));
        chapters.push(RawChapter { title, text });
    }
    Ok(ParsedBook { title, author, chapters })
}
```

- [ ] **Step 4: Replace `desktop/core/src/ingest/mod.rs`**

This drops Task 7's `#[allow(dead_code)]`, since `epub.rs` now uses `html`.
```rust
//! Books in, chapters out: `src/audiobookify/ingest/` in Rust.

pub mod epub;
mod html;
pub mod markdown;

use std::path::Path;

use crate::models::{RawChapter, SourceFormat};

#[derive(Debug, Clone, PartialEq)]
pub struct ParsedBook {
    pub title: String,
    pub author: Option<String>,
    pub chapters: Vec<RawChapter>,
}

#[derive(Debug, thiserror::Error)]
pub enum IngestError {
    #[error("unsupported input format: {0}")]
    UnsupportedFormat(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error("bad EPUB: {0}")]
    Epub(String),
}

/// `ingest_by_path`: pick the parser from the file suffix.
pub fn ingest_by_path(path: impl AsRef<Path>, heading_level: usize) -> Result<(ParsedBook, SourceFormat), IngestError> {
    let path = path.as_ref();
    // Python's Path.suffix: "" for "book." and for ".epub" alone.
    let suffix = match path.extension().map(|e| e.to_string_lossy().to_lowercase()) {
        Some(ext) if !ext.is_empty() => format!(".{ext}"),
        _ => String::new(),
    };
    match suffix.as_str() {
        ".epub" => Ok((epub::parse(path)?, SourceFormat::Epub)),
        ".md" => Ok((markdown::parse(path, heading_level)?, SourceFormat::Markdown)),
        _ => Err(IngestError::UnsupportedFormat(suffix)),
    }
}
```

- [ ] **Step 5: Run the tests**

Run: `cargo test -p audiobookify-core --test ingest_epub --test ingest_dispatch --test normalize_text`
Expected: `ingest_epub` 6 passed, `ingest_dispatch` 3 passed, `normalize_text` 37 passed.

- [ ] **Step 6: Commit**

```bash
git add desktop/core/src/ingest desktop/core/tests/ingest_epub.rs desktop/core/tests/ingest_dispatch.rs desktop/core/tests/normalize_text.rs
git commit -m "Port EPUB ingest and format dispatch to Rust"
```

---

### Task 9: End-to-end parity and the exit check

**Files:**
- Test: `desktop/core/tests/golden_books.rs`

**Interfaces:**
- Consumes: everything above, every `golden/*.json` and `local/*.json`.
- Produces: `committed_fixtures_match_python` and `local_books_match_python`, the phase 2 exit check.

- [ ] **Step 1: Write the test, `desktop/core/tests/golden_books.rs`**

```rust
//! End-to-end parity with the Python pipeline: ingest -> lexicon -> chunk_text over
//! real ingest output. The goldens come from tests/fixtures/text/generate.py.

mod fixture;

use std::path::Path;

use audiobookify_core::ingest::ingest_by_path;
use audiobookify_core::text::{apply_lexicon, chunk_text};
use fixture::{assert_same, fixtures, load, str_of, usize_of};

/// Checks one golden file and returns how many chapters it covered.
fn check(golden_path: &Path) -> usize {
    let golden = load(golden_path);
    let name = golden_path.file_name().unwrap().to_string_lossy().into_owned();
    let source = Path::new(str_of(&golden["source"]));
    let source = if source.is_absolute() { source.to_path_buf() } else { fixtures().join(source) };

    let (book, format) = ingest_by_path(&source, usize_of(&golden["heading_level"]))
        .unwrap_or_else(|e| panic!("{name}: ingest failed: {e}"));
    assert_eq!(format.as_str(), str_of(&golden["format"]), "{name}: format");
    assert_eq!(book.title, str_of(&golden["title"]), "{name}: title");
    assert_eq!(book.author.as_deref(), golden["author"].as_str(), "{name}: author");

    let chapters = golden["chapters"].as_array().unwrap();
    let titles: Vec<&str> = chapters.iter().map(|c| str_of(&c["title"])).collect();
    assert_eq!(book.chapters.iter().map(|c| c.title.as_str()).collect::<Vec<_>>(), titles, "{name}: chapter titles");
    for (i, (chapter, expected)) in book.chapters.iter().zip(chapters).enumerate() {
        assert_same(&format!("{name}: chapter {i} text"), str_of(&expected["text"]), &chapter.text);
    }

    let lexicon: Vec<(&str, &str)> =
        golden["lexicon"].as_array().unwrap().iter().map(|e| (str_of(&e[0]), str_of(&e[1]))).collect();
    for chunking in golden["chunkings"].as_array().unwrap() {
        let (max, min) = (usize_of(&chunking["max_chars"]), usize_of(&chunking["min_chars"]));
        for (i, (chapter, expected)) in book.chapters.iter().zip(chunking["chapters"].as_array().unwrap()).enumerate() {
            let text = if chunking["lexicon"].as_bool().unwrap() { apply_lexicon(&chapter.text, lexicon.iter().copied()) } else { chapter.text.clone() };
            let chunks = chunk_text(&text, max, min);
            let expected = expected.as_array().unwrap();
            let label = format!("{name}: chapter {i} at {max}/{min}");
            for (j, (chunk, want)) in chunks.iter().zip(expected).enumerate() {
                assert_same(&format!("{label}, chunk {j}"), str_of(&want[0]), &chunk.text);
                assert_eq!(chunk.ends_with.as_str(), str_of(&want[1]), "{label}, chunk {j} ends_with");
            }
            assert_eq!(chunks.len(), expected.len(), "{label}: chunk count");
        }
    }
    book.chapters.len()
}

fn goldens_in(dir: &Path) -> Vec<std::path::PathBuf> {
    let mut paths: Vec<_> = std::fs::read_dir(dir)
        .map(|entries| entries.map(|e| e.unwrap().path()).filter(|p| p.extension().is_some_and(|x| x == "json")).collect())
        .unwrap_or_default();
    paths.sort();
    paths
}

#[test]
fn committed_fixtures_match_python() {
    let paths = goldens_in(&fixtures().join("golden"));
    assert!(!paths.is_empty(), "no goldens: run generate.py");
    for path in paths {
        check(&path);
    }
}

/// Copyrighted books from uploads/ (see generate.py --local). Their goldens are
/// git-ignored, so this passes with a note when there are none.
#[test]
fn local_books_match_python() {
    let paths = goldens_in(&fixtures().join("local"));
    if paths.is_empty() {
        eprintln!("no local book goldens in tests/fixtures/text/local; skipped");
        return;
    }
    for path in &paths {
        let chapters = check(path);
        eprintln!("ok {} ({chapters} chapters)", path.file_name().unwrap().to_string_lossy());
    }
    eprintln!("checked {} local books", paths.len());
}
```

- [ ] **Step 2: Run it**

Run: `cargo test -p audiobookify-core --test golden_books -- --nocapture`
Expected: 2 passed, with
```
ok The Abbess.epub.json (62 chapters)
ok The Winds of Winter.epub.json (33 chapters)
ok Trust - Hernan Diaz.epub.json (82 chapters)
checked 3 local books
```
This test was written after the code, but it isn't a test-after: every value it checks is a golden from Task 1, and Tasks 2-8 were built against the same goldens at unit level. If it fails, the message names the book, chapter, chunk and the first differing character. Find the stage that differs (title, chapter text or chunks), reproduce it in the matching unit test file, fix it there, and rerun.

If `checked 3 local books` is missing, Task 1 Step 6 didn't run in this checkout. The exit criterion needs it.

- [ ] **Step 3: Run the whole suite, clippy, and the Python tests**

```bash
cargo test -p audiobookify-core && cargo clippy -p audiobookify-core --all-targets -- -D warnings && (cd .. && "$MAIN/.venv/bin/pytest" -q)
```
Expected: every Rust test binary `ok` (including phase 1's), no clippy warnings, and pytest passing as before. Python is untouched, so pytest only confirms nothing leaked.

- [ ] **Step 4: Confirm no copyrighted text is tracked**

```bash
git ls-files desktop/core/tests/fixtures/text | grep -c "/local/"; git status --short desktop/core/tests/fixtures/text
```
Expected: `0` and nothing listed.

- [ ] **Step 5: Commit**

```bash
git add desktop/core/tests/golden_books.rs
git commit -m "Add end-to-end parity tests for the Rust text pipeline"
```

---

## Exit criteria (phase 2)

- `cargo test -p audiobookify-core` passes with `checked 3 local books`, so all 13 committed goldens, `cases.json` and the 3 local books match Python byte for byte.
- Clippy is clean, and the Python suite is unchanged and passing.
- No file under `src/audiobookify/` or `desktop/src/` changed (`git diff --stat main -- src desktop/src` is empty).

## Not in this phase

- Nothing calls `ingest` or `text` at runtime. Phase 3 (job runner) wires `apply_lexicon` + `chunk_text` into synthesis, and phase 4 (commands) wires `ingest_by_path` into book import.
- No `serde` derives on `ParsedBook` or `Chunk`. Phase 4 adds them for command results.
- `voice/analysis.py` also calls `expand_numbers` (WER normalization). It moves with the rest of voice analysis.
- The fixes listed under "Kept for parity".

# EPUB Chapters From the Table of Contents: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build EPUB chapters from the book's own table of contents instead of one chapter per spine file. Augustus should import as Calibre shows it: 17 chapters, each opening with its heading ("CHAPTER ONE"), instead of 119 file-sized chapters with the headings lost.

**Architecture:** One rule, implemented twice: in Python (`ingest/epub.py`, which the app runs today) and in the Rust port (`desktop/core/src/ingest/epub.rs`, which takes over in phase 4). The golden generator is rerun, so the two stay byte-identical. When a book has no usable TOC, today's per-file behavior is kept unchanged.

**Branch:** `feat/epub-toc-chapters`, off `main` (phase 2 is merged).

## What's wrong today (Augustus, 2026-10-15)

- Calibre split the book into 141 spine files. Ingest makes one chapter per file of at least 200 characters, so the result is 119 "chapters".
- All 17 TOC entries (`toc.ncx`) point at files holding only a heading ("CHAPTER ONE", "BOOK I", "PROLOGUE"). Those files are under 200 characters, so they're dropped as boilerplate, and the heading text never reaches any chapter. That's why "Chapter One" is missing above "I. The Memoirs of Marcus Agrippa".

The TOC is also the only reliable chapter list for books that put several chapters in one file and point at them with `#anchor` links:

| Book | TOC entries | with `#anchor` |
|---|---|---|
| Brotherhood of Kings | 27 | 22, across 10 files |
| Gutenberg #1661 (fixture) | 18 | 18, across 14 files |

## Decisions (user, 2026-10-15)

- A TOC entry that is only a heading ("BOOK I") stays its own chapter, as in Calibre.
- Pages before the TOC's first entry (acclaim, bio, Introduction, Author's Note in Augustus) are dropped.

## The rule

1. **Find the TOC.** Prefer the EPUB 3 nav document: the manifest item with `properties` containing `nav`, and in it the first `<nav>` whose `epub:type` contains `toc`. Read its `<a href>` elements in document order, flattening any nesting. Otherwise use the EPUB 2 NCX: the manifest item named by `<spine toc="...">`, or else the first item with media type `application/x-dtbncx+xml`. Read its `navMap` `navPoint`s in document order, flattened, using `navLabel/text` and `content/@src`. Never read `pageList` or the `landmarks`/`page-list` navs: The Marriage Portrait's NCX has 343 page targets alongside its 31 chapters.
2. **Resolve each entry** to a start position (spine index, fragment or none). Resolve the href against the TOC file's own directory, percent-decode it, and normalize the path. Skip an entry that:
   - points outside the spine's documents (the document rules are unchanged: `application/xhtml+xml`, not `cover`);
   - starts before the previous kept entry in reading order;
   - repeats the previous entry's position.
3. **Use TOC mode only when at least 2 entries survive.** Otherwise fall back to today's per-file chapters, byte for byte unchanged.
4. **Chapter text.** Each entry runs from its start to the next entry's start. The last entry runs to the end of the spine. The pieces are:
   - the part of the start document after the anchor;
   - every whole document in between;
   - the part of the end document before the next anchor.

   Each piece is that document's `_block_text`. Pieces are joined with `"\n\n"`, empty ones skipped. A chapter with no text (e.g. a cover image) is dropped. `MIN_CHAPTER_CHARS` doesn't apply in TOC mode.
5. **Splitting a document at an anchor.** The anchor is the first element whose `id` equals the fragment (or an `<a name=...>`). Insert a sentinel string (U+E000) as a text node just before it, run `_block_text`, and split the result on the sentinel. Then clean each side the same way: split on `"\n\n"`, strip each paragraph, drop the empty ones, and rejoin with `"\n\n"`. If no element matches the fragment, the entry starts at the top of the document.
6. **Title.** The TOC label with its whitespace collapsed (`" ".join(label.split())`). If the label is empty, use the start document's first h1/h2 text, else `Chapter N`.
7. **Front matter** before the first entry is dropped. **Back matter** after the last entry stays in the last chapter (Calibre's semantics).

Expected for Augustus: 17 chapters:
- PROLOGUE: the PROLOGUE heading plus the Caesar–Atia letter;
- BOOK I: just "BOOK I";
- CHAPTER ONE: starts "CHAPTER ONE\n\nI. The Memoirs of Marcus Agrippa: Fragments (13 B.c.)\n\n… I was with him at Actium";
- and so on, through EPILOGUE.

## File structure

| File | Change |
|---|---|
| `src/audiobookify/ingest/epub.py` | `_toc_entries(book)`, `_split_at_anchors(soup, fragments)`, TOC-mode assembly in `parse`; per-file loop kept as the fallback. |
| `tests/test_ingest_epub.py` | New tests below. |
| `desktop/core/tests/fixtures/text/generate.py` | Two new hand-written fixtures: `calibre_split.epub` (an NCX, heading-only files, front matter, a back matter page) and `anchors.epub` (an EPUB 3 nav with nested entries, several chapters per file via `#id`, an `<a name>` anchor, an unknown fragment, an out-of-order entry and a `page-list` nav). Regenerate everything. |
| `desktop/core/src/ingest/html.rs` | Keep `id`/`name`/`href`/`epub:type` attributes on elements; `Document::block_text_split(&[&str]) -> Vec<String>` (rule 5); nav-link extraction for rule 1. |
| `desktop/core/src/ingest/epub.rs` | TOC reading (roxmltree for the NCX, `html::Document` for the nav), resolution, TOC-mode assembly, fallback. |
| `desktop/core/tests/ingest_epub.rs` | Rust ports of the new Python tests. |

## Tasks (TDD, one reviewed commit each)

### Task 1: Python TOC mode
Write the tests first in `tests/test_ingest_epub.py`. Build the EPUBs in-test with `zipfile` (not ebooklib, which rewrites content):
- `test_toc_chapters_join_split_files_and_keep_heading_files`: the Augustus shape in miniature. An NCX points at heading-only files; the chapter text starts with the heading and continues with the following files; the heading-only "BOOK I" entry is its own chapter.
- `test_pages_before_the_first_toc_entry_are_dropped` and `test_back_matter_stays_in_the_last_chapter`.
- `test_anchor_entries_split_one_file_into_chapters`, including an anchor mid-paragraph and an `<a name>` anchor.
- `test_nav_document_wins_over_ncx_and_nesting_is_flattened`.
- `test_page_list_and_landmarks_are_ignored`.
- `test_unknown_fragment_starts_at_top_of_file`, `test_out_of_order_and_duplicate_entries_are_skipped`, `test_entry_with_no_text_is_dropped`.
- `test_books_without_a_usable_toc_keep_per_file_chapters` (0 or 1 entries).
- `test_augustus_shape` *(local only, skipped unless the file exists)*: `/Volumes/Untitled/Augustus - Williams, John.epub` gives 17 chapters, and chapter 3 is titled "CHAPTER ONE" and starts as above.

Then implement in `epub.py` and run the whole pytest suite. The existing EPUB tests must pass unchanged: the ebooklib-built books in them have empty TOCs, so they take the fallback path.

### Task 2: Regenerate the goldens
Add the two fixtures to `generate.py` and regenerate the committed goldens and the 3 local goldens. Expect changes:
- `gutenberg-1661.epub.json`, the 3 local goldens, and the two new fixtures;
- `handmade.epub.json` stays the same: its nav has 1 entry, so it falls back.

Review the diffs of changed goldens by chapter titles and counts, not by text. The ebooklib fixtures and `cases.json` must not change.

### Task 3: Rust port
Port rules 1–7 to `html.rs`/`epub.rs` test-first. Write the new Rust tests in `ingest_epub.rs` against the new fixtures, and add `html.rs` CASES for the splitter. Done when:
- `cargo test -p audiobookify-core` passes with `REQUIRE_LOCAL_GOLDENS=3`;
- clippy is clean;
- the Rust stack-depth and backtrack regressions still pass.

### Task 4: Re-import note
No code change. Books already in the DB keep their old chapters. To get the new chapters, delete Augustus and import it again. Chapter edits made with the chapter-breaks editor would be lost, so the app doesn't do this automatically.

## Not in this change
- Skipping TOC entries such as "Cover", "Copyright" or "Contents" (The Marriage Portrait lists them in its TOC). An empty cover page is already dropped by rule 4; copyright and contents pages become chapters you can skip. EPUB 3 `landmarks` could filter them later.
- Choosing a TOC depth: nesting is always flattened, so "Part" entries become their own chapters, consistent with keeping "BOOK I".

# Chunking overhaul

**Goal:** stop chunk joins from sounding stitched: no mid-sentence cuts at arbitrary words, no tiny chunks, pauses that follow the text's structure, no level jumps between chunks.

## Design

1. **Sentence regex** (`normalize/text.py`): a sentence may end with closing quotes/brackets after `.!?…` (`."`, `?'`, `”`, `)`), and the next may open with a quote/bracket.
2. **Paragraphs = blank lines** for every source. EPUB ingest emits `\n\n` between block elements (`p, div, li, h1-h6, blockquote, tr, td, th`), joins inline nodes without a break, turns `<br>` into a space, and collapses whitespace. Markdown and text clips already use blank lines. Previously ingested books have no blank lines and chunk as before until re-imported.
3. **`chunk_text(text, max_chars, min_chars=0) -> List[Chunk(text, ends_with)]`**, `ends_with` in `word | clause | sentence | paragraph | scene`.
   - A paragraph always ends a chunk; sentences are packed within it.
   - An over-long sentence splits at the strongest punctuation that fits (`; :`, then dashes, then `,`), then on a word, never leaving a piece under `min_chars`.
   - Symbol-only paragraphs (`* * *`, `#`, `⁂`) are scene breaks: never synthesized; the previous chunk gets `ends_with="scene"`.
   - Chunks under `min_chars` merge forward into the next chunk, or backward when they end a scene or the text. Merged paragraphs that lack ending punctuation get a period so the model pauses.
4. **Engine protocol:** `min_chunk_chars()` alongside `max_chunk_chars()`. Chatterbox: 80.
5. **Joins** (`postprocess/audio.py::concatenate_chunks(chunks, ends_with, sample_rate)`): trim each chunk's own leading/trailing silence (frames below peak −40 dB, 30 ms pad kept), 10 ms fades, nudge chunks more than 3 dB off the chapter's median level toward it (max ±6 dB, never clipping), then pause by `ends_with`: word 0.1 s, clause 0.2, sentence 0.35, paragraph 0.75, scene 1.5. Overall level otherwise unchanged (no loudness normalization).
6. **Cache key** is computed over the chunk texts, so any chunking change invalidates cached audio automatically. Trimming happens at join time, so cached audio stays raw.
7. Both callers (`synthesize_chapter`, `synthesize_text_clip`) updated.

## Tests (written first)
Sentence regex cases; paragraph splitting; clause-split priority; min-length merging (forward, backward at end, scene-ending, heading gets a period); property check over generated text; scene breaks; trim/fade/pause lengths/level nudging; cache key; EPUB ingest block handling; end-to-end real `epub.parse` output through `chunk_text`.

## Out of scope
`<hr>` scene breaks, UI-editable pauses/minimum, re-ingesting existing books.

# Local Audiobook Generator — Project Spec

> **Purpose of this document:** A build spec to hand to Claude Code. It defines what to build, the recommended local TTS stack, the architecture, and a phased implementation plan. Sections marked **[DECISION NEEDED]** are choices the developer (you) should confirm before or during implementation.

---

## 1. Overview

A **fully local** desktop application that converts book files (EPUB and Markdown) into audiobooks, generating **one MP3 per chapter**. The app supports:

1. **Voice cloning** — the user supplies a short reference recording, and generated narration matches that voice. The user can tune/preview the voice before committing to a full book.
2. **Custom pronunciation ("problem words")** — the user can register hard words (names, invented terms, jargon) and correct their pronunciation, including by attaching a small audio sample of the word spoken correctly.

**Hard requirement:** everything runs on-device. No cloud API calls for synthesis, cloning, or pronunciation. Model weights are downloaded once and cached locally.

### Target hardware (must run on both)
- **macOS:** Apple M3 MacBook Pro, 18 GB unified memory (Metal/MPS acceleration, CPU fallback).
- **Windows:** RTX 3080 (10 GB VRAM; note the Ti variant has 12 GB), CUDA acceleration.

Both targets are comfortably within reach of the recommended models below (all sit in the 4–6 GB working-set range for normal-length clips).

### Non-goals (v1)
- No cloud/hosted mode.
- No real-time / streaming narration (batch generation is fine).
- No multi-speaker dialogue casting (single narrator voice per book).
- Formats beyond EPUB and Markdown (PDF, DOCX, etc.) are out of scope for v1 but the ingest layer should be pluggable to add them later.

---

## 2. Recommended TTS engine

The single most important design decision. Priorities from the brief: **high realism**, **not complex to train/tune**, runs on both target machines.

### Primary recommendation: **Chatterbox (Resemble AI)**
- **Zero-shot voice cloning** from a short reference clip (~10–30 s of clean audio). No training or fine-tuning step — this directly satisfies "not super complex to train/tune." The user just provides a reference recording.
- **Top-tier realism** — in 2026 blind comparisons it was frequently preferred over commercial cloud TTS; it's widely cited as the first local model where cloned output stops sounding obviously synthetic.
- **Permissive license (Apache 2.0)** — safe for a tool you may distribute.
- **Compact (~0.5B backbone)** — fits the 4–6 GB working range; runs on RTX 3080 (CUDA) and Apple Silicon.
- **Expressiveness controls** — exaggeration/emotion + temperature/CFG knobs, useful for narration tuning.
- **⚠️ Apple Silicon caveat:** MPS support has historically had rough edges (some builds fall back to CPU for stability; a few resampling ops are CPU-only). CPU fallback works but is slower. The app must handle "MPS with CPU fallback" gracefully and surface expected generation time. Validate current MPS status against the maintained server fork (`devnen/Chatterbox-TTS-Server`) during setup — it tracks MPS fixes.

### Strong alternative: **F5-TTS**
- Zero-shot cloning, flow-matching architecture (fewer inference steps, faster-than-real-time on GPU), fits comfortably under ~6 GB VRAM.
- **Optional** light fine-tuning available if the user later wants a rock-solid dedicated narrator voice — but zero-shot is enough to start.
- Slightly more prone to pronunciation instability on unusual English vowels/proper nouns per the issue tracker — which makes the pronunciation subsystem (§5) more important, not less.
- Good cross-platform story.

### Other engines considered
| Engine | Cloning | Realism | Notes |
|---|---|---|---|
| **Qwen3-TTS** | Zero-shot, ~3 s ref | High | Apache 2.0, 2026 arrival — strong secondary option. |
| **XTTS-v2 (Coqui)** | Zero-shot | Good | Mature + large ecosystem, but Coqui the company is defunct (community-maintained) and the model license is **non-commercial** — avoid if distributing commercially. |
| **CosyVoice2-0.5B** | Zero-shot | High | Viable; heavier ecosystem footprint. |
| **Kokoro-82M** | ❌ preset voices only | Good | No cloning, so it can't be the primary engine — but it's tiny and faster-than-real-time, so include it as an optional **fast preview / fallback** engine (see §4.6). |

### **[DECISION NEEDED]**
Confirm **Chatterbox as primary**. Regardless of the pick, build behind a **pluggable engine interface** (§4.7) so swapping F5-TTS/Qwen3-TTS later is a config change, not a rewrite. Do not hard-code one engine into the pipeline.

---

## 3. High-level pipeline

```
┌──────────┐   ┌───────────────┐   ┌────────────────────┐   ┌──────────────┐   ┌─────────────┐
│  Ingest  │──▶│  Chapterize   │──▶│  Text normalize +  │──▶│  Synthesize  │──▶│  Post &     │
│ epub/md  │   │  + structure  │   │  pronunciation     │   │  per chapter │   │  export mp3 │
└──────────┘   └───────────────┘   │  substitution      │   └──────────────┘   └─────────────┘
                                    └────────────────────┘         ▲
                                             ▲                     │
                                    ┌────────┴─────────┐   ┌───────┴────────┐
                                    │ Pronunciation    │   │ Voice profile  │
                                    │ lexicon manager  │   │ manager (clone)│
                                    └──────────────────┘   └────────────────┘
```

Generation is a **job queue**: each chapter is an independent unit that can be generated, re-generated, previewed, and exported on its own. Long books must be resumable — never force a full restart because chapter 30 failed.

---

## 4. Components

### 4.1 Ingest
- **EPUB:** parse with a library such as `ebooklib`; extract the spine (reading order), then per-document HTML → clean text. Strip nav/TOC/copyright boilerplate. Preserve chapter boundaries and titles from the spine + `<h1>/<h2>` headings and/or the EPUB NCX/nav.
- **Markdown:** parse headings to derive chapter structure. Top-level `#` (or a configurable heading level) delimits chapters by default.
- Output of ingest: an ordered list of `Chapter` objects (see §6), each with a title and cleaned plain text.

### 4.2 Chapterize + structure
- Detect chapter breaks robustly (EPUB spine is authoritative; Markdown uses heading level).
- Let the user **review and edit the detected chapter list** before generation (merge/split/rename/reorder, exclude front-matter like copyright pages). This is important — auto-detection is never perfect.
- Each chapter maps 1:1 to an output MP3.

### 4.3 Text normalization
Before synthesis, normalize text so the model reads it naturally:
- Expand/handle numbers, dates, currency, abbreviations, and units (e.g., "Dr." → "Doctor" where appropriate).
- Handle acronyms (spell-out vs read-as-word) — configurable, and overridable per-term via the lexicon (§5).
- Sentence segmentation and chunking to fit the engine's stable context length (most of these models degrade or drift on very long single passages — chunk at sentence boundaries and concatenate audio). Chunking should be part of the engine adapter since limits differ per engine.
- **Apply pronunciation substitutions from the lexicon here** (§5).

### 4.4 Voice profile manager (cloning)
- Import a reference recording (WAV/MP3). Recommend **10–30 s of clean, single-speaker audio**; validate sample rate, length, mono/stereo, and warn on background noise/clipping.
- Store as a reusable **Voice Profile** (see §6) with a name, the reference audio, its transcript (some engines need the reference text; auto-transcribe locally with Whisper if not provided), and engine-specific tuning params (exaggeration, temperature, CFG/pace, speed).
- **Preview panel:** synthesize a short user-supplied sentence with the current profile + params so the user can tune before committing to a whole book. This tight feedback loop is the main "tune the voice to preference" surface.
- Multiple profiles can be saved and reused across books.

### 4.5 Synthesis (per chapter)
- For each chapter: normalize → chunk → synthesize each chunk with the selected engine + voice profile → concatenate chunks → hand to post-processing.
- Runs as queued jobs with progress, cancel, and **resume**. Persist per-chapter status (pending / generating / done / failed) so a crash or quit doesn't lose completed chapters.
- Surface a realistic time estimate (esp. important on Apple Silicon CPU fallback).

### 4.6 Post-processing & export
- Concatenate chunk audio into one stream per chapter.
- Optional: peak/loudness normalization (target ~ -18 to -20 LUFS for audiobooks is a reasonable default), light silence trimming, and inter-paragraph pause insertion.
- Encode to **MP3** (via `ffmpeg`), one file per chapter. Filenames like `01 - Chapter Title.mp3`, zero-padded and ordered.
- Write **ID3 tags**: album = book title, track number = chapter order, title = chapter title, artist = narrator/voice name. This makes the output behave like a real audiobook in players. *(Optional stretch: also emit an M4B with chapter markers.)*
- Output all files to a user-chosen folder per book.

### 4.7 Engine interface (pluggable)
Define a narrow interface every engine adapter implements, e.g.:

```
TTSEngine:
  name: str
  supports_cloning: bool
  load(device: "cuda"|"mps"|"cpu") -> None
  clone_profile(reference_audio, reference_text?, params) -> VoiceProfileHandle
  synthesize(text_chunk, profile_handle, params) -> audio (np array / wav)
  max_chunk_chars() -> int          # engine-specific safe chunk size
  preferred_phoneme_scheme() -> "ipa" | "arpabet" | "respell" | None
  teardown() -> None
```

Chatterbox is the first adapter. F5-TTS / Qwen3-TTS / Kokoro (preview-only, `supports_cloning=False`) can follow. Device selection: auto-detect CUDA → MPS → CPU, with a manual override in settings.

---

## 5. Pronunciation subsystem ("problem words") — detailed

This is the trickiest feature, so it gets its own section. **Key reality from research:** current local cloning models (Chatterbox, F5-TTS, XTTS, etc.) do **not** support conditioning the pronunciation of a single word on an attached audio clip. The reference audio controls *voice/timbre*, not per-word pronunciation. So we implement the user's "attach an audio sample" intent by **converting that sample into a pronunciation the engine can actually consume**, plus a guaranteed-correctness fallback.

### 5.1 The lexicon (core mechanism)
A persistent **pronunciation lexicon**: a set of entries mapping a surface term → a corrected pronunciation. At normalization time (§4.3), the app substitutes matches before synthesis (whole-word, case-insensitive, with word-boundary matching; support multi-word terms).

Each entry stores:
- `term` — the word/phrase as it appears in text (e.g., "Cadeyrn").
- `override` — one of:
  - **Respelling** (simplest, most robust across engines): e.g., `Cadeyrn` → `KAH-dern`. Plain ASCII the model reads phonetically.
  - **Phonemes** (IPA or ARPAbet), for engines that accept phoneme input inline.
- `source` — `manual` | `from_audio` (see §5.2).
- `sample_audio` — optional attached clip (kept for reference/audit and for the splice fallback).
- `scope` — global (all books) or book-specific.
- `notes`.

Substitution strategy is engine-aware via `preferred_phoneme_scheme()`: if the engine takes inline IPA, inject IPA; otherwise fall back to respelling. **Respelling should always be available** as the universal path since every engine can read plain text.

### 5.2 Attaching an audio sample (the user's requested workflow)
Flow: user adds a problem word → attaches a short clip of it pronounced correctly → the app derives a pronunciation from the clip:

1. **Phoneme recognition on the clip** (fully local): run the sample through a local phoneme recognizer (e.g., a wav2vec2 CTC phoneme model, `allosaurus`, or an equivalent G2P-from-audio model) to produce an IPA/ARPAbet string.
2. **Auto-populate a lexicon entry** with that phoneme string (and a respelling generated from it), marked `source: from_audio`, with the clip attached.
3. **User audits/edits** the derived pronunciation in the lexicon UI and hears a preview (synthesize the term with the override in the current voice) to confirm it sounds right. This closes the loop: "add word + attach audio → it learns the word."

This is the honest, working version of the feature: the clip *teaches* the pronunciation, but by way of extracted phonemes the TTS engine can actually use, not by per-word audio conditioning (which the models don't support).

### 5.3 Guaranteed-correctness fallback: concatenative splice
For a word that still refuses to synthesize correctly even with a phoneme/respelling override, offer an **audio-splice** mode:
- Synthesize the sentence with a placeholder at the word position, then splice the correctly-pronounced clip in.
- Best results if the clip is re-synthesized **in the target voice** (once the term is even roughly pronounceable) so timbre matches; a raw user clip in a different voice will be audibly different and should be a last resort.
- This is fiddly (timing, prosody, level matching) — implement it as an **optional advanced fallback**, not the default path. The lexicon + phoneme override should handle the large majority of cases.

### 5.4 Priority for implementation
1. Manual lexicon with **respelling** overrides + text substitution (covers most needs, works with every engine).
2. IPA/ARPAbet overrides for engines that accept phonemes.
3. **Audio-sample → phoneme extraction** to auto-fill entries (the headline feature).
4. Concatenative splice fallback (advanced, optional).

---

## 6. Data model (suggested)

```
Book
  id, title, author, source_path, source_format(epub|md), output_dir, created_at
  chapters: [Chapter]
  voice_profile_id
  lexicon_scope: [LexiconEntry]   # book-specific entries

Chapter
  id, book_id, order_index, title, text,
  status(pending|generating|done|failed), output_mp3_path, duration, error?

VoiceProfile
  id, name, engine, reference_audio_path, reference_transcript,
  params { exaggeration, temperature, cfg, speed, ... }, created_at

LexiconEntry
  id, term, override_type(respell|ipa|arpabet), override_value,
  source(manual|from_audio), sample_audio_path?, scope(global|book), notes

Job
  id, chapter_id, status, progress, started_at, finished_at, error?
```

Persist to a local database (SQLite is plenty). Store audio and model cache under a per-user app data dir.

---

## 7. Tech stack (suggested)

**[DECISION NEEDED]** — confirm the stack. A pragmatic default that runs identically on Mac + Windows:

- **Backend / core:** Python. The entire TTS ecosystem (Chatterbox, F5-TTS, Whisper, phonemizers, `ebooklib`) is Python-first, so this avoids fighting the ecosystem. Wrap synthesis in a local service (e.g., FastAPI) or call directly from the app process.
- **Audio/encoding:** `ffmpeg` (MP3 encode, concat, loudness), `soundfile`/`librosa` for I/O.
- **GPU/accel:** PyTorch with CUDA (Windows) and MPS (Mac); auto-detect with CPU fallback.
- **UI — pick one:**
  - **Desktop-native feel:** a Tauri or Electron shell over the Python core, or a Python-native GUI. Good if you want a real installable app.
  - **Local web UI:** FastAPI + a simple React/Svelte front-end served at `localhost`. Fastest to build; still fully local. Good default for v1.
- **Packaging:** aim for a one-command setup per OS; document model download + first-run weight caching. Handle the Apple Silicon device/MPS quirks in setup.

Keep the **core pipeline UI-agnostic** (a library + CLI) so the GUI is a thin layer. A working CLI (`generate --input book.epub --voice myvoice --out ./audiobook/`) is a great first milestone and makes the whole thing testable without UI.

---

## 8. UX flow (v1)

1. **Import book** → app parses and shows detected chapters for review/edit.
2. **Choose or create a voice** → import reference clip → tune params in the preview panel until it sounds right.
3. **(Optional) Pronunciation** → app can pre-scan the book for likely problem words (rare/capitalized/OOV tokens) and suggest lexicon candidates; user adds overrides, optionally attaching audio samples.
4. **Generate** → queue all chapters (or a subset); watch progress; preview any finished chapter; regenerate individual chapters as needed.
5. **Export** → MP3 per chapter with ID3 tags in the chosen folder.

---

## 9. Milestones

- **M0 — Core pipeline (CLI):** ingest EPUB+MD → chapterize → normalize → synthesize with Chatterbox (default voice) → export tagged MP3s per chapter. Resumable job queue. Runs on both target machines.
- **M1 — Voice cloning:** reference import, voice profiles, preview/tuning panel.
- **M2 — Pronunciation lexicon:** manual respelling + IPA overrides, text substitution, per-term preview.
- **M3 — Audio-sample pronunciation:** local phoneme extraction from an attached clip → auto lexicon entry + audit UI.
- **M4 — GUI:** wrap the core in the chosen UI; chapter review, queue dashboard, export.
- **M5 — Polish / stretch:** second engine adapter (F5-TTS), problem-word auto-scan, loudness normalization, M4B + chapter markers, splice fallback.

---

## 10. Open questions / decisions to confirm

- **[DECISION]** Confirm **Chatterbox** as the primary engine (vs. F5-TTS / Qwen3-TTS).
- **[DECISION]** UI approach: local web UI (fastest) vs. native shell (Tauri/Electron).
- **[DECISION]** Distribution: personal use only, or shareable/commercial? (Affects engine license choice — keep to Apache-2.0 / permissive if commercial.)
- **[DECISION]** Default chapter heading level for Markdown.
- **[TODO]** Nice-to-haves — the original brief was cut off mid-list ("Some additional nice to haves…"). Fold the remaining items in here once provided.

---

## Appendix A — Key research notes (2026)

- Local cloning quality crossed the "not obviously synthetic" line in ~2025–2026; **Chatterbox** and **F5-TTS** are the strongest permissive zero-shot options that fit both target machines.
- **Zero-shot cloning** (reference clip, no training) is what satisfies "not complex to tune" — prefer it over any fine-tuning-first workflow. Fine-tuning is an optional later enhancement (F5-TTS), not a requirement.
- **RTX 3080 (10 GB):** comfortably runs these ~0.5B– classed models (typical 4–6 GB working set). Keep the model resident in VRAM.
- **M3 / 18 GB unified:** works, but expect **Chatterbox MPS caveats** — some ops/resampling may force CPU fallback, which is slower. Design for graceful CPU fallback + honest time estimates, and re-check MPS status against the maintained Chatterbox server fork at build time.
- **Pronunciation:** no local cloning model supports per-word audio conditioning. The working mechanism is a **phonetic override lexicon** (respelling / IPA / ARPAbet) applied as text substitution; the "attach audio" feature is realized by **extracting phonemes from the clip** into a lexicon entry, with an optional concatenative-splice fallback for stubborn words.

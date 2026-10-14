# Handover: move audiobookify to Python 3.12 + MLX (Qwen3-TTS and Chatterbox Turbo)

Written 2026-10-14 at the end of a research/benchmark session. Start a fresh session from this document. Follow the repo's usual flow: plan each phase, get approval, write tests first (TDD), implement, verify on real models.

## 1. Where things stand

- `main`: includes PR #6 (paragraph-aware chunking: `Chunk(text, ends_with)`, `min_chunk_chars()`, pause table, silence trim, level matching).
- PR #7 (merged): PyTorch Chatterbox prepares the reference voice once per profile (~0.7 s saved per chunk). The same pattern carries over to the Turbo engine in Phase 1.
- `feat/tts-bench` ([PR #8](https://github.com/NRohner/audiobookify/pull/8), merging it is Phase 0): `src/audiobookify/bench.py` + `tests/test_bench.py` + `make bench-mlx-audio-setup` + `.gitignore` fixes (`.venv*/` was not ignored on `main`; `bench/` must be ignored because it holds a copyrighted passage). This handover doc ships in the same PR.
- Benchmark data (gitignored): `bench/passage.txt` (copyrighted excerpt, never commit), `bench/out/baseline/`, `bench/out/slow-ref/`, `bench/refs/jim-dale-0.88.wav`, `bench/transcripts/`.
- `.venv` = app venv (Python 3.14, PyTorch Chatterbox). `.venv-mlx-audio` = Python 3.12 venv with upstream `mlx-audio==0.5.9`, used only by the benchmark.
- Machine: Apple M3 Pro, 18 GB unified memory, macOS 27. MLX means Apple Silicon only.

## 2. What the benchmark found

Same 4,547-char passage slice, Jim Dale reference, app chunking (300/80 chars). "Speed" = generation seconds per second of audio, after warm-up. WER = Whisper large-v3-turbo vs the text the model was given. Most WER at or below 2% is Whisper homophones ("heirs"/"airs").

| Engine | Speed | Peak memory | WER | User verdict |
|---|---|---|---|---|
| PyTorch Chatterbox (today's app) | 1.02 (0.96 with voice prepared once); ~2 min warm-up | ~20 GB MPS | 0.5-1.2% | baseline |
| Chatterbox on MLX, mlx-audio-plus fork | 0.94 | 5.5 GB | 1.9% | robotic (port issue; fork removed) |
| Chatterbox multilingual, upstream mlx-audio | 0.92 | 5.5 GB | 1.9% | better than the fork |
| **Chatterbox Turbo (MLX)** | **0.40** | 5.2 GB | 7.8% / 1.7% | slightly less natural; speed is "crazy" |
| **Qwen3-TTS 1.7B Base (MLX)** | 0.82-0.86 | 10.6-12.2 GB | 0.8-1.4% | **best**, especially with the 0.88x reference |
| Breeze TTS 2 (MLX) | 2.35 | 12.9 GB | 0.7% | more natural, better pace, too slow |

Decisions already made by the user:
- **Qwen3-TTS 1.7B Base = "quality" engine. Chatterbox Turbo = "speed" engine.** The user picks at generation time.
- Not pursuing: Fish S2 Pro (about 4B, expected Breeze-like speed), Higgs V3 (rated barely above Chatterbox), Voxtral (MLX port has no voice cloning), Kokoro (no cloning), regular Chatterbox on MLX.
- No automatic per-chunk Whisper quality check for now (too slow).

Chatterbox Turbo size (answers "is it small enough"): 350M parameters; **2.8 GB download** (`mlx-community/chatterbox-turbo-fp16`) plus the shared 0.47 GB `mlx-community/S3TokenizerV2`; **5.2 GB peak** while generating. Qwen3 is a 4.2 GB download and peaks at 10.6-12.2 GB. **Both loaded together (~17 GB) won't fit comfortably in 18 GB.** The worker must hold one engine at a time and tear down before switching. MLX loads take about 1-3 s once cached, so switching is cheap. Quantized Turbo builds exist (`chatterbox-turbo-4bit/5bit/6bit/8bit`), untested.

Pace findings (Whisper word timestamps, `bench --cadence`):
- Pauses after full stops are ~0.32-0.38 s for every model. **Speaking rate is what differs.** Breeze speaks at 193 wpm (speaking time only), Turbo 201, Qwen3 220.
- **Qwen3 copies the reference clip's pace.** Jim Dale's clip is 213 wpm and Qwen3 came out at 220.
- The clip slowed with `ffmpeg -filter:a atempo=0.88` (188 wpm speaking / 174 overall) sounded "totally natural" to the user. It slowed Qwen3 to 202 / 183 overall and Turbo to 186 / 165. **Qwen3 at 0.88x is the user's favourite output so far.** Qwen3 follows only part of the reference change (~8% slower for a 12% slower reference).
- Other references: Roy Dotrice 146 wpm (36 s clip), Luke Gawne 227 wpm (28 s clip).

Known failure: chunks ending in an interrupted line (`"...and if we were to—”` at a paragraph end, also the last chunk of the run). Turbo once babbled ~15 nonsense words there. Qwen3 once repeated the phrase. PyTorch Chatterbox read it cleanly.

## 3. Constraints and preferences

- Global CLAUDE.md: plan before code, TDD, **never emojis in UI** (use icons), issue tracking via `~/.claude/repotrack-issues.md`.
- User prefers robust/standard over YAGNI on architecture forks, and wants one step at a time with a measurable baseline before changes.
- **Don't hide things from the user.** Pace is shown and user-adjustable, never silently applied. Transcripts should be visible and editable.
- **Future direction** (not now, but keep in mind): a Rust + Tauri desktop app, or a hosted WASM + WebGPU app. So:
  - Keep the model runtime behind the `TTSEngine` protocol, in the worker process. MLX is today's runtime, not an assumption baked into the rest of the app.
  - Keep ingest, normalization and chunking pure, deterministic and well-tested (the most likely code to port to Rust).
  - Keep audio post-processing (trim, pauses, leveling, tempo) as simple, documented numeric operations.
  - Store user intent as data (tempo factor, measured wpm, transcript). Processed audio files are caches, not the source of truth.
  - The React frontend talks to the app only over HTTP; keep it that way (it can move into a Tauri webview).

## 4. Phases

### Phase 0: land the benchmark
- Merge the `feat/tts-bench` PR (it adds no runtime deps; the MLX imports are lazy). Then delete the branch locally and on origin. The branch's job ends here; the benchmark lives on in `main` as a developer tool.
- Long-term home for `bench.py`: Phase 1 removes its PyTorch Chatterbox variants. Phases 2-3 move `cadence_stats`, `word_error_rate` and the Whisper transcript helper into app modules (for example `voice/analysis.py`) and have `bench.py` import them, so the benchmark holds no app logic. Exclude `bench.py` from any future packaged build (Tauri/WASM).

### Phase 1: Python 3.12, MLX, Turbo replaces PyTorch Chatterbox
Verified by dry-run resolution (`uv pip compile`, Python 3.12, macOS 15 target):
- **`chatterbox-tts` cannot coexist with `mlx-audio`**: chatterbox-tts 0.1.7 pins `transformers==5.2.0`, mlx-audio 0.5.9 needs `transformers>=5.14`. Drop `chatterbox-tts` and its `setuptools<81` pin (that existed only for its watermark dependency).
- Without it everything resolves: mlx 0.32.3, mlx-audio 0.5.9, torch 2.14.1, transformers 5.19.0, numpy 2.5, librosa 1.0, phonemizer 3.4.
- torch, torchaudio and transformers are still needed by `phoneme/wav2vec2.py` (the M3 "pronunciation from audio" feature). They were transitive via chatterbox-tts; declare them explicitly.

Steps:
- `pyproject.toml`: `requires-python = ">=3.12"`; deps above plus `mlx-audio==0.5.9`. Makefile `setup`: `python3.12 -m venv` (Homebrew `/opt/homebrew/bin/python3.12` exists; plain `python3` is 3.14).
- New `engines/turbo.py` (`ChatterboxTurboEngine`): `load_model("mlx-community/chatterbox-turbo-fp16")`; prepare the voice once per profile; generate without `ref_audio`; join every yielded segment. The port stores prepared conditionals on the model (`model._conds`), so to support switching profiles, keep each profile's conditionals in the handle and restore them before generating (as PR #7 did for PyTorch). Turbo ignores exaggeration/cfg. Its sampling defaults: temperature 0.8, top_p 0.95, top_k 1000, repetition_penalty 1.2, `norm_loudness=True` (output ~3.5 dB quieter than PyTorch Chatterbox).
- Reference code to port: `MLXAudioEngine` / `ChatterboxTurbo` in `src/audiobookify/bench.py`.
- Remove `engines/chatterbox.py`, `detect_device` (torch MPS/CUDA), and the PyTorch Chatterbox tests. Update `cli.py` (four `ChatterboxEngine()` call sites), `make models`, README.
- Verify `wav2vec2` against real weights on transformers 5.19 (`pytest -m integration`). Mocked tests have hidden real-dependency breakage in this repo before.
- Then delete `.venv-mlx-audio` and `bench-mlx-audio-setup`; the benchmark runs from the app venv. Delete benchmark variants that need PyTorch Chatterbox. Delete `~/.cache/huggingface/hub/models--ResembleAI--chatterbox` (3.0 GB).

### Phase 2: Qwen3 engine, automatic transcripts, engine choice
- New `engines/qwen3.py`: `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16`, `generate(text, ref_audio=..., ref_text=..., lang_code="english")`. **"english", not "en".** Its `speed` argument is not implemented, so pace control goes through the reference clip (Phase 3). The port loads and encodes the reference on every call. Passing a preloaded `mx.array` avoids re-reading the file; measure whether that matters. Same 300/80 chunk limits as tested.
- **Transcript on upload** (`POST /api/voices`, `api/app.py:293`): one Whisper pass (`mlx-community/whisper-large-v3-turbo-asr-fp16` via `mlx_audio.stt.utils.load_model(...).generate(path, word_timestamps=True)`) gives the transcript **and** the word timings Phase 3 needs. Store in `reference_transcript` (column already exists). Show it in the Voices page, editable: Whisper can be wrong, and clips often end mid-sentence (Jim Dale's ends "...a small son called").
  - Backfill existing profiles: transcribe lazily the first time Qwen3 needs one, and save it.
  - Doing this in the API request blocks the request for ~30 s on a cold load. Consider a background job (the worker already polls queues) and show "transcribing...".
  - Reference length: Qwen3 does not trim the reference, so 28-36 s clips make every chunk slower. Measure with the Roy Dotrice clip; if it's notably slower, use the word timestamps to trim to about 15 s at a word boundary, with the matching transcript.
- **Engine choice**: voices become engine-agnostic (any voice works with either engine; the `voice_profiles.engine` column becomes legacy). The user picks "Quality (Qwen3)" or "Speed (Turbo)" when generating a book, chapter or text clip. Store it on the book or clip, so requeued and resumed jobs use the same engine. The engine name must be part of the chunk cache key (`synth/chunk_cache.py` keys on chunk texts + voice_params, so put the engine in voice_params). Warn when one book mixes engines across chapters.
- **Worker**: one engine loaded at a time; on a job needing the other engine, `teardown()` (free MLX memory) and then `load()`.
- `exaggeration` / `cfg_weight` are Chatterbox-only and unused by both new engines. Hide them in the UI and keep the DB columns (no destructive migration).

### Phase 3: visible, user-controlled pace
- On upload (same Whisper pass): measure the reference's speaking rate. Show both "speaking wpm" (pauses excluded) and "overall wpm". `cadence_stats()` in `bench.py` already does this from word timestamps, with tests.
- Store a per-voice `tempo` (factor, default 1.0) plus the measured wpm. Show a slider (suggest 0.75-1.25) with the resulting estimated reference pace, plus a recommended "sweet spot":
  - Evidence: the user's favourite came from a reference at **~174 wpm overall / 188 speaking**.
  - Commonly cited audiobook norm: ~150-160 wpm overall for the finished narration (general guidance, not measured here).
  - Qwen3 follows only part of a reference change, so label output-pace estimates as approximate.
- Preview: play the tempo-adjusted reference, and offer a short sample generation (text clips already exist). Never auto-apply a tempo.
- Apply with `ffmpeg -filter:a atempo=<tempo>` (pitch preserved). The derived clip is a cache keyed by (reference file hash, tempo); the transcript doesn't change with tempo. The tempo must be in the chunk cache key.

### Phase 4: interrupted-sentence chunk fix
Reproduce before choosing a fix. Use a small harness on Qwen3 and Turbo: same chunk ending in `—”` vs `…”` vs `.”`, about 10 runs each, judged by Whisper WER. The failing case was also the last chunk and a paragraph end, so separate those factors. Candidate fixes in `normalize/text.py`:
- (a) don't end a chunk on an interrupted line: merge it into the next paragraph (an exception to "a paragraph always ends a chunk");
- (b) give the model a clean stop by normalizing a trailing dash at chunk end (for example `—` followed by a period or ellipsis).

Keep the pause table's `ends_with` semantics intact. Add tests on real EPUB ingest output, not hand-built strings.

### Backlog (not scheduled)
- Pause stretching after full stops (all models measured ~0.35 s; audiobook narration usually pauses longer).
- Multilingual Chatterbox settings sweep (cfg_weight ~0.3 slows pacing per Resemble's docs). Postponed.
- Quantized Turbo/Qwen3 builds for lower memory.

## 5. Useful commands (current state)
- Benchmark: `.venv-mlx-audio/bin/python -m audiobookify.bench bench/passage.txt --voice bench/refs/jim-dale-0.88.wav --variant qwen3-tts-1.7b --out bench/out/<name>`
- Score: `... -m audiobookify.bench --score bench/out/<name>`. Pace: `--cadence bench/out/<name>`.
- Slow a reference: `ffmpeg -i in.mp3 -filter:a atempo=0.88 -ar 24000 out.wav`.

## 6. Models on disk (Hugging Face cache)
Keep: Qwen3 1.7B Base bf16 (4.2 GB), chatterbox-turbo-fp16 (2.8 GB), S3TokenizerV2 (0.47 GB), whisper-large-v3-turbo-asr-fp16 (1.5 GB), facebook/wav2vec2-lv-60-espeak-cv-ft (2.4 GB).
Delete in Phase 1: ResembleAI/chatterbox (3.0 GB).
Not from this project, left alone: google/gemma-4, datalab-to/surya*, SWivid/F5-TTS, charactr/vocos, openai/whisper-large-v3-turbo (PyTorch), google/bert_uncased_* (BookNLP, from the possibly abandoned `feat/audiobook-studio` branch).

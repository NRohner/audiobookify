# Phase 6: Delete Python app code — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Python keeps only what the app starts: `python -m audiobookify.engine_worker` and `python -m audiobookify.model_store`. Everything Rust replaced in phases 1-4 is deleted, along with the CLI, FastAPI, the benchmark tool and their dependencies.

**Architecture:** No new code. `EngineSlot` moves from `worker.py` into `engine_worker.py` (flagged in the spec's phase 3 notes). `voice/analysis.py` shrinks to what the worker calls: `transcribe` and `reference_clip`. Then the dead modules, their tests and their dependencies go.

**Tech Stack:** Python 3.12, setuptools, pytest; Rust core tests as the cross-check (they spawn the real `engine_worker` and `model_store` children).

## Global Constraints

- Branch: `feat/tauri-phase6-delete-python` off `main` (62701d9).
- User decision (2026-10-16): `bench.py` is deleted, not kept. `bench/out` is gitignored and stays on disk; tracked files under `bench/` (passage, refs, transcripts) are deleted with the tool.
- The CLI is dropped (spec open question 2, default accepted 2026-10-14).
- The first-launch import of the repo's `audiobookify.db` (`core::datadir`) stays; it's Rust and reads the repo files read-only.
- Rust test fixtures generated from Python (`desktop/core/tests/fixtures/**/golden*`) are committed JSON and stay. No Rust test runs Python pipeline code.
- Test commands: `.venv/bin/pytest`, `cd desktop && cargo test -p audiobookify-core`, `cd frontend && npm test`. All green after every task.

## File map

**Keep:** `src/audiobookify/{__init__,engine_worker,model_store}.py`, `engines/{__init__,base,fake,mlx_util,qwen3,turbo}.py`, `voice/{__init__,analysis}.py`. Tests: `test_engine_worker.py`, `test_engines_fake.py`, `test_engines_qwen3.py`, `test_engines_turbo.py`, `test_model_store.py`, `test_voice_analysis.py` (trimmed).

**Delete:** `api/`, `cli.py`, `db.py`, `models.py`, `worker.py`, `bench.py`, `synth/`, `ingest/`, `lexicon/`, `normalize/`, `postprocess/`, `voice/storage.py`, `voice/validation.py`, `phoneme/` and `analysis/` (only `__pycache__` left), `bench/` tracked files. Tests: `conftest.py`, `test_api.py`, `test_bench.py`, `test_chapter_breaks.py`, `test_chunk_cache.py`, `test_cli.py`, `test_db.py`, `test_ingest_dispatch.py`, `test_ingest_epub.py`, `test_ingest_markdown.py`, `test_lexicon_substitution.py`, `test_normalize_text.py`, `test_pipeline.py`, `test_postprocess_audio.py`, `test_voice_params.py`, `test_voice_storage.py`, `test_voice_validation.py`, `test_worker.py`, plus any test fixtures only those used.

**Modify:** `pyproject.toml`, `Makefile` (`clean`), `README.md`, the spec (phase 6 notes, module map, open question 2).

---

### Task 1: Make the kept modules stand alone

- [ ] Create the branch.
- [ ] Move `EngineSlot` (and only it) from `worker.py` into `engine_worker.py`; import `ENGINES` there directly. Keep its docstring.
- [ ] Cut `voice/analysis.py` to `transcribe`, `_result`, `_load_whisper`, `reference_clip` and the constants they use. Drop `trim_point`, `word_error_rate`, `cadence_stats`, `reference_pace`, `_words` (Rust owns trim point and pace; WER/cadence were bench and validation only). This removes the `normalize.text` import.
- [ ] Trim `tests/test_voice_analysis.py` to the tests of the kept functions. If a test of a dropped function has no Rust counterpart in `desktop/core/tests/trim_point.rs` / `voice_audio.rs`, list it in the task report rather than silently dropping it.
- [ ] Self-check: `grep -rn "from \.\.\?\(db\|models\|worker\|synth\|ingest\|lexicon\|normalize\|postprocess\|bench\)\|voice\.\(storage\|validation\)" src/audiobookify/{engine_worker,model_store}.py src/audiobookify/engines src/audiobookify/voice/analysis.py` prints nothing.
- [ ] All three test commands green. Commit.

### Task 2: Delete the replaced code and its tests

- [ ] `git rm` everything under "Delete" in the file map. Remove the untracked `phoneme/` and `analysis/` folders.
- [ ] `.venv/bin/pytest` green (only kept tests run). `cargo test -p audiobookify-core` green, which proves the engine worker and model store still start and answer.
- [ ] `git grep -n "audiobookify\.\(cli\|api\|worker\|db\|bench\)\|audiobookify:main"` outside `docs/` prints nothing.
- [ ] Commit.

### Task 3: Shrink the dependencies

- [ ] `pyproject.toml`: remove `ebooklib`, `beautifulsoup4`, `num2words`, `mutagen`, `librosa`, `fastapi`, `uvicorn`, `python-multipart` and the `[project.scripts]` table. Add `huggingface_hub` (pin the floor to the installed 2.2 minor; `model_store` imports it directly and relies on 2.2's Xet store layout) and `tqdm` (imported directly by `model_store`). Keep `numpy`, `soundfile`, `mlx-audio==0.5.9`, `transformers`.
- [ ] Check nothing kept imports a removed package: `grep -rn "^\s*\(import\|from\) " src/audiobookify` and compare against the dependency list.
- [ ] Clean install check in a throwaway venv in the scratchpad: `python3.12 -m venv <tmp> && <tmp>/bin/pip install -e ".[dev]" && <tmp>/bin/pytest`. Then reinstall the repo `.venv` (`make clean setup`) so stale packages and `src/audiobookify.egg-info` don't hide a missing dependency.
- [ ] Commit.

### Task 4: Docs

- [ ] `README.md`: remove the CLI sections (`generate`, `voice add`, `lexicon`, `--engine` mentions) and the FastAPI history paragraph; keep engine descriptions, reworded for the GUI. Test section: `.venv/bin/pytest` covers the engine worker and model store; core tests are `cargo test`.
- [ ] `Makefile`: `clean` also removes `src/audiobookify.egg-info` (already globbed; verify) and nothing CLI-related remains.
- [ ] Spec: add "Notes from phase 6" (what stayed, what went, bench decision, dependency list); mark `api/app.py`, `cli.py` and `bench.py` rows as deleted in the module map; mark open question 2 as resolved.
- [ ] Commit.

### Task 5: Exit check

- [ ] All three test commands green on the reinstalled `.venv`.
- [ ] `make dev`: generate one chapter with each engine, generate a text clip, add a voice without a transcript (Whisper runs), download/delete status in Settings still reads correctly.
- [ ] User's GUI check, then push and open the PR.

# Phase 5: Settings page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Settings screen with listening stats, model download/delete limited to the app's own Hugging Face repos, and a first-run "download a voice engine" panel. Also: remove wav2vec2 from the project for good.

**Architecture:** Rust owns a fixed model registry (`core::hub`) and checks presence by reading the HF cache layout directly (fast, used to gate generation). Sizes, deletion and downloads go through a one-shot Python process, `python -m audiobookify.model_store`, which uses `huggingface_hub`'s own cache API; downloads stream JSON progress lines and die when the app's pipe closes. Deleting an engine first makes the job runner drop its engine-worker child, so files and memory are really freed. Stats are SQL over the existing tables plus a one-time duration backfill.

**Tech Stack:** Rust (rusqlite, serde, std::process), Tauri 2 commands, Python 3.12 + huggingface_hub 2.2, React + Tailwind v4 (`--rr-*` tokens), `node --test`.

## Global Constraints

- Branch: `feat/tauri-phase5-settings` off `main` (b3e4b0c).
- UI follows `docs/design/DESIGN.md` and uses only `--rr-*` tokens from `frontend/src/styles/tokens.css` (via the existing Tailwind classes: `bg-card`, `text-ink-muted`, `border-line`, `rr-eyebrow`, etc.).
- No emoji anywhere in the UI; icons come from `lucide-react`.
- DESIGN.md §11: no stat cards, no charts, no dense tables. Stats render as a `<dl>` "card" in the Library-card style (§5).
- Copy voice (DESIGN.md §10): "Quality engine", "Speed engine", "Voice transcription", never "TTS model". Warm, brief, second person.
- **The HF cache is shared with other projects.** Nothing may list, size or delete a repo outside `hub::REGISTRY`.
- Children are never started with stdin closed (`<&-`); one-shots use `Stdio::null()`, the download child uses `Stdio::piped()` and exits on stdin EOF.
- The engine worker stays `HF_HUB_OFFLINE=1`; only the download process runs online (`HF_HUB_OFFLINE` removed from its env).
- Frontend command args must not end in `Id` unless numeric: `numericIds` (`lib/ids.js`) turns `"qwen3"` into `NaN`. Model commands take `model`.
- User decisions (2026-10-16): delete is refused only while queued/running work needs the model; an idle loaded engine is unloaded first. wav2vec2 is removed completely (weights already deleted from the HF cache on 2026-10-16).
- Test commands: `cd desktop && cargo test -p audiobookify-core`, `.venv/bin/pytest`, `cd frontend && npm test`. All must stay green after every task.

## File map

| File | Responsibility |
|---|---|
| `src/audiobookify/model_store.py` (new) | One-shot CLI: `sizes`, `delete`, `download` over the HF cache API; fake mode for tests |
| `tests/test_model_store.py` (new) | pytest for the above against a hand-built cache |
| `desktop/core/src/hub/mod.rs` (new) | Registry, cache dir resolution, `HubCache` presence check |
| `desktop/core/src/hub/store.rs` (new) | `Hub`: runs `model_store` one-shots, owns download children and their progress |
| `desktop/core/src/app/models.rs` (new) | Model commands' logic: list, progress, download, cancel, delete (with refusal rules), `require_engine` |
| `desktop/core/src/app/stats.rs` (new) | Stats queries and the duration backfill |
| `desktop/core/src/db/stats.rs` (new) | The SQL behind stats and "active work" |
| `desktop/core/src/jobs/runner.rs` | `RunnerControl` unload flag, missing-model check, text-clip duration |
| `desktop/core/src/engine.rs` | `PythonEngine::shutdown` |
| `desktop/src/commands.rs`, `desktop/src/main.rs` | Wire new commands and state |
| `frontend/src/lib/settings.js` (+ test) | Formatting: bytes, hours, framings, pace, setup check |
| `frontend/src/pages/SettingsPage.jsx` (new) | The screen |
| `frontend/src/components/AppShell.jsx`, `App.jsx`, `EnginePicker.jsx`, `lib/engines.js`, `lib/api.js` | Nav, route, first-run redirect, not-downloaded engines |

---

### Task 1: Remove wav2vec2 and its dependencies

The weights are already gone from the HF cache. This removes every code path that could download them again.

**Files:**
- Delete: `src/audiobookify/phoneme/` (whole package: `__init__.py`, `base.py`, `fake.py`, `respelling.py`, `wav2vec2.py`)
- Delete: `tests/test_phoneme_wav2vec2.py`, `tests/test_phoneme_fake.py`, `tests/test_respelling.py`
- Delete: `desktop/core/src/text/respelling.rs`, `desktop/core/tests/respelling.rs`
- Modify: `desktop/core/src/text/mod.rs` (drop `mod respelling;`, `pub use respelling::phonemes_to_respelling;`, and the `phoneme/respelling.py` mention in the module doc)
- Modify: `src/audiobookify/cli.py` (drop the `add-from-audio` subcommand, its handler, and the two `phoneme` imports)
- Modify: `tests/test_cli.py` (drop `FakePhonemeExtractor` import and every `add_from_audio` test)
- Modify: `pyproject.toml`, `Makefile`, `README.md`, `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md`

**Interfaces:** Produces nothing new. The DB keeps its `lexicon_entries.phonemes` / `sample_audio_path` columns (existing rows, existing schema).

- [ ] **Step 1: Create the branch**

```bash
git checkout -b feat/tauri-phase5-settings
```

- [ ] **Step 2: Delete the files**

```bash
git rm -r src/audiobookify/phoneme tests/test_phoneme_wav2vec2.py tests/test_phoneme_fake.py tests/test_respelling.py \
  desktop/core/src/text/respelling.rs desktop/core/tests/respelling.rs
```

- [ ] **Step 3: Edit `desktop/core/src/text/mod.rs`**

Remove the lines `mod respelling;` and `pub use respelling::phonemes_to_respelling;`, and remove ``, `phoneme/respelling.py` `` (or the equivalent phrase) from the module doc comment so it no longer mentions respelling. Run `grep -rn respelling desktop/core/src` — expected: no output.

- [ ] **Step 4: Edit `src/audiobookify/cli.py`**

Delete lines 14–15 (`from .phoneme.respelling import ...`, `from .phoneme.wav2vec2 import ...`), the whole `add-from-audio` handler function (the one that constructs `Wav2Vec2PhonemeExtractor`, around lines 280–350), the `"add-from-audio"` subparser block (around line 527) and its `elif args.lexicon_command == "add-from-audio":` dispatch branch (around line 586). Then:

```bash
grep -n "phoneme\|from_audio\|from-audio\|--device" src/audiobookify/cli.py
```
Expected: no output (if `--device` belonged only to `add-from-audio`, it goes too).

- [ ] **Step 5: Edit `tests/test_cli.py`**

Delete `from audiobookify.phoneme.fake import FakePhonemeExtractor` (line 11) and every test whose name contains `from_audio` (starting at `test_lexicon_add_from_audio_creates_entry_with_derived_respelling`, around line 966, plus its siblings). `grep -n "from_audio\|Phoneme" tests/test_cli.py` → no output.

- [ ] **Step 6: Edit `pyproject.toml`**

Remove `"phonemizer>=3.2",`, the comment `# phoneme/wav2vec2.py (pronunciation from audio)`, `"torch>=2.4",` and `"torchaudio>=2.4",`. Keep `"transformers>=5.14",` but move it under a comment: `# AutoTokenizer for the Turbo engine (mlx-audio chatterbox_turbo)`.

- [ ] **Step 7: Edit `Makefile`**

Delete the wav2vec2 line from `models:` (line 30). Delete the `espeak-ng` check line from `setup:` (phonemizer was its only user).

- [ ] **Step 8: Prove the engines don't need torch or phonemizer**

```bash
.venv/bin/pip uninstall -y torch torchaudio phonemizer
.venv/bin/python -c "import audiobookify.engines.turbo, audiobookify.engines.qwen3, audiobookify.voice.analysis, audiobookify.engine_worker, audiobookify.cli; print('ok')"
HF_HUB_OFFLINE=1 .venv/bin/python -c "from audiobookify.engines.turbo import ChatterboxTurboEngine as E; e=E(); e.load(); print(e.sample_rate()); e.teardown()"
```
Expected: `ok`, then a sample rate (24000) with no traceback. If the Turbo load fails on a missing `torch` import, reinstall torch (`.venv/bin/pip install "torch>=2.4"`), put `"torch>=2.4",  # mlx-audio chatterbox_turbo imports it` back in `pyproject.toml`, and note it in the commit message.

- [ ] **Step 9: Update docs**

`README.md`: delete the espeak-ng install step (line 8), the `phonemizer` GPL note (line 12), the whole `lexicon add-from-audio` section (around lines 66–73), and change line 130 to `.venv/bin/pytest -m integration  # real Turbo load and run, requires make models first`.

Spec `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md`: in Goal and the architecture diagram drop "wav2vec2"; in the protocol list delete the `phonemes {path}` bullet; in the module map change `phoneme/wav2vec2.py` row to say "Deleted in phase 5"; in Settings → Models delete the "Pronunciation from audio" registry bullet; add a new section before "Open questions":

```markdown
## Notes from phase 5

- wav2vec2 is gone (user decision, 2026-10-16): the weights were deleted from the HF cache, and `phoneme/`, the CLI's `lexicon add-from-audio`, `torch`, `torchaudio`, `phonemizer` and the espeak-ng requirement were removed. The `lexicon_entries.phonemes` column stays for existing rows.
```

- [ ] **Step 10: Run all suites**

```bash
.venv/bin/pytest -q
cd desktop && cargo test -p audiobookify-core 2>&1 | tail -3
```
Expected: all pass (pytest count drops by the removed tests).

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "Remove wav2vec2 and the phoneme-from-audio CLI command

The GUI stopped using it in phase 4 and its weights are deleted. torch,
torchaudio, phonemizer and the espeak-ng requirement go with it."
```

---

### Task 2: Python `model_store` one-shot

**Files:**
- Create: `src/audiobookify/model_store.py`
- Test: `tests/test_model_store.py`

**Interfaces:**
- Produces (CLI contract the Rust `Hub` relies on; one JSON object per stdout line, logging to stderr):
  - `python -m audiobookify.model_store sizes REPO...` → `{"sizes": {"REPO": bytes, ...}}` (0 when not cached)
  - `python -m audiobookify.model_store delete REPO...` → `{"freed": bytes}`
  - `python -m audiobookify.model_store download 'JSON'` where JSON is `[{"repo": str, "allow_patterns": [str], "required": [str]}]` → lines `{"downloaded": int, "total": int}` …, then `{"done": true}` or `{"error": str}`. Exits when stdin reaches EOF.
  - Fake knobs: `AUDIOBOOKIFY_FAKE_DOWNLOAD=1` writes each `required` file (1024 bytes) into the cache layout instead of downloading; `=error` replies `{"error": "fake download failed"}`; `AUDIOBOOKIFY_FAKE_DOWNLOAD_DELAY=<seconds>` sleeps before each file.
- The cache root is `huggingface_hub.constants.HF_HUB_CACHE` (honours `HF_HUB_CACHE` / `HF_HOME`), read at call time.

- [ ] **Step 1: Write the failing tests**

```python
"""model_store: sizes, delete and the fake download, against a hand-built HF cache."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from audiobookify import model_store


def cache_repo(cache: Path, repo: str, files: dict[str, int], commit: str = "abc123") -> Path:
    """The HF cache layout: blobs, a snapshot of symlinks into them, refs/main."""
    root = cache / ("models--" + repo.replace("/", "--"))
    for i, (name, size) in enumerate(files.items()):
        blob = root / "blobs" / f"{commit}{i:04d}"
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(b"x" * size)
        link = root / "snapshots" / commit / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(blob)
    (root / "refs").mkdir(parents=True, exist_ok=True)
    (root / "refs" / "main").write_text(commit)
    return root


@pytest.fixture
def cache(tmp_path, monkeypatch):
    c = tmp_path / "hub"
    c.mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(c))
    return c


def test_sizes_reports_cached_repos_and_zero_for_missing(cache):
    cache_repo(cache, "org/a", {"config.json": 10, "model.safetensors": 1000})
    assert model_store.sizes(["org/a", "org/missing"]) == {"org/a": 1010, "org/missing": 0}


def test_sizes_with_no_cache_dir_is_all_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "nope"))
    assert model_store.sizes(["org/a"]) == {"org/a": 0}


def test_delete_removes_only_the_named_repos(cache):
    cache_repo(cache, "org/a", {"model.safetensors": 1000})
    other = cache_repo(cache, "someone/else", {"model.safetensors": 500}, commit="def456")
    assert model_store.delete(["org/a"]) == 1000
    assert model_store.sizes(["org/a", "someone/else"]) == {"org/a": 0, "someone/else": 500}
    assert (other / "snapshots" / "def456" / "model.safetensors").read_bytes() == b"x" * 500


def test_delete_of_a_missing_repo_frees_nothing(cache):
    assert model_store.delete(["org/missing"]) == 0


def test_blob_bytes_counts_partial_downloads(cache):
    root = cache_repo(cache, "org/a", {"model.safetensors": 100})
    (root / "blobs" / "zzz.incomplete").write_bytes(b"x" * 50)
    assert model_store.blob_bytes(root) == 150


def run_cli(*args, env=None):
    return subprocess.run([sys.executable, "-m", "audiobookify.model_store", *args],
                          input="", capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=60)


def test_fake_download_writes_required_files_and_reports_done(cache):
    items = [{"repo": "org/a", "allow_patterns": ["*"], "required": ["config.json", "sub/model.safetensors"]}]
    out = run_cli("download", json.dumps(items), env={"AUDIOBOOKIFY_FAKE_DOWNLOAD": "1"})
    lines = [json.loads(line) for line in out.stdout.splitlines()]
    assert lines[-1] == {"done": True}
    assert {"downloaded": 2048, "total": 2048} in lines
    snap = cache / "models--org--a" / "snapshots"
    commit = (cache / "models--org--a" / "refs" / "main").read_text()
    assert (snap / commit / "sub" / "model.safetensors").stat().st_size == 1024


def test_fake_download_error(cache):
    items = [{"repo": "org/a", "allow_patterns": ["*"], "required": ["config.json"]}]
    out = run_cli("download", json.dumps(items), env={"AUDIOBOOKIFY_FAKE_DOWNLOAD": "error"})
    assert json.loads(out.stdout.splitlines()[-1]) == {"error": "fake download failed"}


def test_sizes_cli_prints_one_json_line(cache):
    cache_repo(cache, "org/a", {"model.safetensors": 7})
    out = run_cli("sizes", "org/a")
    assert json.loads(out.stdout) == {"sizes": {"org/a": 7}}


def test_download_exits_when_stdin_closes(cache):
    items = [{"repo": "org/a", "allow_patterns": ["*"], "required": ["a", "b", "c"]}]
    p = subprocess.Popen([sys.executable, "-m", "audiobookify.model_store", "download", json.dumps(items)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                         env={**os.environ, "AUDIOBOOKIFY_FAKE_DOWNLOAD": "1", "AUDIOBOOKIFY_FAKE_DOWNLOAD_DELAY": "30"})
    p.stdin.close()
    assert p.wait(timeout=10) != 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_model_store.py -q`
Expected: FAIL — `ImportError: cannot import name 'model_store'`.

- [ ] **Step 3: Implement `src/audiobookify/model_store.py`**

```python
"""Model files in the Hugging Face cache, for the desktop app's Settings page.

A one-shot process: `sizes REPO...`, `delete REPO...` or `download JSON`. Replies are
JSON lines on stdout (logging goes to stderr). The cache is shared with other projects,
so it only ever touches the repos it is given; the Rust registry decides which.
A download exits when stdin closes, so it dies with the app.
"""
import fnmatch
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path


def _cache_root() -> Path:
    from huggingface_hub import constants
    # constants is read at import; re-read the env so tests (and HF_HOME changes) apply.
    return Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME") or constants.HF_HOME) / "hub")


def _repo_dir(repo: str) -> Path:
    return _cache_root() / ("models--" + repo.replace("/", "--"))


def _scan():
    from huggingface_hub import scan_cache_dir
    from huggingface_hub.errors import CacheNotFound
    try:
        return scan_cache_dir(_cache_root())
    except CacheNotFound:
        return None


def sizes(repos: list[str]) -> dict[str, int]:
    info = _scan()
    cached = {r.repo_id: r.size_on_disk for r in info.repos if r.repo_type == "model"} if info else {}
    return {repo: cached.get(repo, 0) for repo in repos}


def delete(repos: list[str]) -> int:
    info = _scan()
    if info is None:
        return 0
    hashes = [rev.commit_hash for r in info.repos if r.repo_type == "model" and r.repo_id in repos
              for rev in r.revisions]
    if not hashes:
        return 0
    strategy = info.delete_revisions(*hashes)
    strategy.execute()
    return strategy.expected_freed_size


def blob_bytes(repo_dir: Path) -> int:
    """Bytes in a repo's blob store, partial (.incomplete) downloads included."""
    blobs = repo_dir / "blobs"
    return sum(f.stat().st_size for f in blobs.iterdir() if f.is_file()) if blobs.is_dir() else 0


def _reply(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _fake_download(items: list[dict]) -> None:
    if os.environ.get("AUDIOBOOKIFY_FAKE_DOWNLOAD") == "error":
        raise RuntimeError("fake download failed")
    delay = float(os.environ.get("AUDIOBOOKIFY_FAKE_DOWNLOAD_DELAY") or 0)
    total = 1024 * sum(len(i["required"]) for i in items)
    done = 0
    for item in items:
        root = _repo_dir(item["repo"])
        commit = hashlib.sha1(item["repo"].encode()).hexdigest()
        for name in item["required"]:
            time.sleep(delay)
            blob = root / "blobs" / hashlib.sha256(f"{item['repo']}/{name}".encode()).hexdigest()
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(b"x" * 1024)
            link = root / "snapshots" / commit / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.unlink(missing_ok=True)
            link.symlink_to(blob)
            done += 1024
            _reply({"downloaded": done, "total": total})
        (root / "refs").mkdir(parents=True, exist_ok=True)
        (root / "refs" / "main").write_text(commit)


def _real_download(items: list[dict]) -> None:
    from huggingface_hub import HfApi, snapshot_download
    api = HfApi()
    total = 0
    for item in items:
        siblings = api.model_info(item["repo"], files_metadata=True).siblings or []
        total += sum(s.size or 0 for s in siblings
                     if any(fnmatch.fnmatch(s.rfilename, p) for p in item["allow_patterns"]))
    dirs = [_repo_dir(i["repo"]) for i in items]
    stop = threading.Event()

    # ponytail: progress is the blob store's size on disk, polled; independent of HF's tqdm
    # internals. Coarse if a backend only writes each file at the end.
    def report():
        while not stop.wait(0.5):
            _reply({"downloaded": min(sum(blob_bytes(d) for d in dirs), total), "total": total})

    threading.Thread(target=report, daemon=True).start()
    try:
        for item in items:
            snapshot_download(item["repo"], allow_patterns=item["allow_patterns"])
    finally:
        stop.set()


def _exit_on_stdin_eof() -> None:
    def watch():
        sys.stdin.read()
        os._exit(1)
    threading.Thread(target=watch, daemon=True).start()


def main(argv: list[str]) -> int:
    command, args = argv[0], argv[1:]
    if command == "sizes":
        _reply({"sizes": sizes(args)})
        return 0
    if command == "delete":
        _reply({"freed": delete(args)})
        return 0
    if command == "download":
        _exit_on_stdin_eof()
        items = json.loads(args[0])
        try:
            (_fake_download if os.environ.get("AUDIOBOOKIFY_FAKE_DOWNLOAD") else _real_download)(items)
        except Exception as exc:
            _reply({"error": str(exc) or type(exc).__name__})
            return 1
        _reply({"done": True})
        return 0
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_model_store.py -q`
Expected: all pass. (`test_download_exits_when_stdin_closes` takes about a second.)

- [ ] **Step 5: Commit**

```bash
git add src/audiobookify/model_store.py tests/test_model_store.py
git commit -m "Add model_store: HF cache sizes, delete and download for the app"
```

---

### Task 3: Rust registry and presence check (`core::hub`)

**Files:**
- Create: `desktop/core/src/hub/mod.rs`
- Modify: `desktop/core/src/lib.rs` (add `pub mod hub;`)
- Modify: `desktop/core/tests/common/mod.rs` (add `cache_model`, `ready_cache`)
- Test: `desktop/core/tests/hub.rs`

**Interfaces:**
- Produces:
  ```rust
  pub struct RepoSpec { pub repo: &'static str, pub allow_patterns: &'static [&'static str], pub required: &'static [&'static str] }
  pub struct ModelSpec { pub id: &'static str, pub label: &'static str, pub purpose: &'static str,
                         pub engine: Option<&'static str>, pub download_bytes: u64, pub repos: &'static [RepoSpec] }
  pub const TRANSCRIPTION: &str = "whisper";
  pub static REGISTRY: [ModelSpec; 3];        // ids "qwen3", "chatterbox-turbo", "whisper"
  pub fn model(id: &str) -> Option<&'static ModelSpec>;
  pub fn model_for_engine(engine: &str) -> Option<&'static ModelSpec>;
  pub fn default_cache_dir() -> PathBuf;      // same env rules as huggingface_hub
  #[derive(Debug, Clone)] pub struct HubCache { pub root: PathBuf }
  impl HubCache { pub fn new(root: impl Into<PathBuf>) -> Self; pub fn is_present(&self, m: &ModelSpec) -> bool;
                  pub fn engine_ready(&self, engine: &str) -> bool /* true for engines not in the registry */ }
  ```
  Test helpers in `tests/common/mod.rs`: `pub fn cache_model(cache: &Path, id: &str)` and `pub fn ready_cache(dir: &Path) -> HubCache` (all three models present, under `dir/hub`).

- [ ] **Step 1: Write the failing tests** (`desktop/core/tests/hub.rs`)

```rust
mod common;

use std::fs;

use audiobookify_core::hub::{self, HubCache, REGISTRY};
use common::*;
use tempfile::TempDir;

#[test]
fn registry_engines_match_the_app_engines() {
    for spec in audiobookify_core::app::ENGINES.iter() {
        assert_eq!(hub::model_for_engine(spec.id).unwrap().id, spec.id);
    }
    assert!(hub::model(hub::TRANSCRIPTION).unwrap().engine.is_none());
    assert!(hub::model("facebook/wav2vec2-lv-60-espeak-cv-ft").is_none());
}

#[test]
fn turbo_includes_the_s3_tokenizer_it_fetches_at_load() {
    let repos: Vec<_> = hub::model("chatterbox-turbo").unwrap().repos.iter().map(|r| r.repo).collect();
    assert_eq!(repos, ["mlx-community/chatterbox-turbo-fp16", "mlx-community/S3TokenizerV2"]);
}

#[test]
fn present_only_when_every_required_file_resolves() {
    let dir = TempDir::new().unwrap();
    let cache = HubCache::new(dir.path());
    let qwen = hub::model("qwen3").unwrap();
    assert!(!cache.is_present(qwen));

    cache_model(dir.path(), "qwen3");
    assert!(cache.is_present(qwen));

    // A cancelled download: the weights' symlink never got made.
    let repo = dir.path().join("models--mlx-community--Qwen3-TTS-12Hz-1.7B-Base-bf16");
    let commit = fs::read_to_string(repo.join("refs/main")).unwrap();
    fs::remove_file(repo.join("snapshots").join(commit.trim()).join("speech_tokenizer/model.safetensors")).unwrap();
    assert!(!cache.is_present(qwen));
}

#[test]
fn a_dangling_symlink_is_not_present() {
    let dir = TempDir::new().unwrap();
    cache_model(dir.path(), "whisper");
    let repo = dir.path().join("models--mlx-community--whisper-large-v3-turbo-asr-fp16");
    for blob in fs::read_dir(repo.join("blobs")).unwrap() {
        fs::remove_file(blob.unwrap().path()).unwrap();
    }
    assert!(!HubCache::new(dir.path()).is_present(hub::model("whisper").unwrap()));
}

#[test]
fn engine_ready_follows_presence_and_ignores_unknown_engines() {
    let dir = TempDir::new().unwrap();
    let cache = HubCache::new(dir.path());
    assert!(!cache.engine_ready("chatterbox-turbo"));
    assert!(cache.engine_ready("fake"));
    cache_model(dir.path(), "chatterbox-turbo");
    assert!(cache.engine_ready("chatterbox-turbo"));
}

#[test]
fn ready_cache_has_everything() {
    let dir = TempDir::new().unwrap();
    let cache = ready_cache(dir.path());
    assert!(REGISTRY.iter().all(|m| cache.is_present(m)));
}
```

Add to `tests/common/mod.rs`:

```rust
use audiobookify_core::hub::{self, HubCache};

/// Writes `id`'s required files into an HF cache layout under `cache`, like a finished download.
pub fn cache_model(cache: &Path, id: &str) {
    for repo in hub::model(id).unwrap().repos {
        let root = cache.join(format!("models--{}", repo.repo.replace('/', "--")));
        let commit = "c0ffee";
        for (i, name) in repo.required.iter().enumerate() {
            let blob = root.join("blobs").join(format!("blob{i}"));
            std::fs::create_dir_all(blob.parent().unwrap()).unwrap();
            std::fs::write(&blob, b"weights").unwrap();
            let link = root.join("snapshots").join(commit).join(name);
            std::fs::create_dir_all(link.parent().unwrap()).unwrap();
            std::os::unix::fs::symlink(&blob, &link).unwrap();
        }
        std::fs::create_dir_all(root.join("refs")).unwrap();
        std::fs::write(root.join("refs/main"), commit).unwrap();
    }
}

/// An HF cache under `dir/hub` with every registry model downloaded.
pub fn ready_cache(dir: &Path) -> HubCache {
    let root = dir.join("hub");
    for m in hub::REGISTRY.iter() {
        cache_model(&root, m.id);
    }
    HubCache::new(root)
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd desktop && cargo test -p audiobookify-core --test hub`
Expected: compile error, `could not find hub in audiobookify_core`.

- [ ] **Step 3: Implement `desktop/core/src/hub/mod.rs`**

```rust
//! The Hugging Face models the app uses, and whether they're in the cache. The cache is
//! shared with other projects, so nothing outside `REGISTRY` is ever listed or touched.
//! Presence is read straight from the cache layout (fast enough to gate every generate);
//! sizes, deletion and downloads go through `audiobookify.model_store` (see `store`).

pub mod store;

use std::path::{Path, PathBuf};

pub struct RepoSpec {
    pub repo: &'static str,
    /// What `snapshot_download` fetches; the same patterns mlx-audio's loader uses.
    pub allow_patterns: &'static [&'static str],
    /// Snapshot paths that must resolve for the model to load.
    pub required: &'static [&'static str],
}

pub struct ModelSpec {
    /// The engine id for engines, `TRANSCRIPTION` for Whisper.
    pub id: &'static str,
    pub label: &'static str,
    pub purpose: &'static str,
    pub engine: Option<&'static str>,
    /// Shown before download.
    // ponytail: measured from the cache on 2026-10-16; query the Hub if repos change often.
    pub download_bytes: u64,
    pub repos: &'static [RepoSpec],
}

pub const TRANSCRIPTION: &str = "whisper";

/// mlx_audio.utils.DEFAULT_ALLOW_PATTERNS in mlx-audio 0.5.9 (pinned in pyproject.toml).
const MLX_PATTERNS: &[&str] = &["*.json", "*.safetensors", "*.py", "*.model", "*.tiktoken", "*.txt", "*.jinja",
                                "*.jsonl", "*.yaml", "*.npz", "*.pth"];

pub static REGISTRY: [ModelSpec; 3] = [
    ModelSpec {
        id: "qwen3",
        label: "Quality engine",
        purpose: "The most natural narration. Needs a narrator voice with a transcript.",
        engine: Some("qwen3"),
        download_bytes: 4_500_000_000,
        repos: &[RepoSpec {
            repo: "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16",
            allow_patterns: MLX_PATTERNS,
            required: &["config.json", "model.safetensors", "speech_tokenizer/config.json",
                        "speech_tokenizer/model.safetensors"],
        }],
    },
    ModelSpec {
        id: "chatterbox-turbo",
        label: "Speed engine",
        purpose: "About twice as fast, slightly less natural. Works without a narrator voice.",
        engine: Some("chatterbox-turbo"),
        download_bytes: 3_500_000_000,
        repos: &[
            RepoSpec {
                repo: "mlx-community/chatterbox-turbo-fp16",
                allow_patterns: MLX_PATTERNS,
                required: &["config.json", "model.safetensors", "conds.safetensors"],
            },
            // Fetched by mlx-audio at load time; offline, a miss is only a logged warning,
            // so it must be downloaded with the engine.
            RepoSpec {
                repo: "mlx-community/S3TokenizerV2",
                allow_patterns: &["*.json", "*.safetensors"],
                required: &["model.safetensors"],
            },
        ],
    },
    ModelSpec {
        id: TRANSCRIPTION,
        label: "Voice transcription",
        purpose: "Writes the transcript for a narrator voice you add, so the Quality engine can use it.",
        engine: None,
        download_bytes: 1_600_000_000,
        repos: &[RepoSpec {
            repo: "mlx-community/whisper-large-v3-turbo-asr-fp16",
            allow_patterns: MLX_PATTERNS,
            required: &["config.json", "model.safetensors"],
        }],
    },
];

pub fn model(id: &str) -> Option<&'static ModelSpec> {
    REGISTRY.iter().find(|m| m.id == id)
}

pub fn model_for_engine(engine: &str) -> Option<&'static ModelSpec> {
    REGISTRY.iter().find(|m| m.engine == Some(engine))
}

/// huggingface_hub's rule: HF_HUB_CACHE, else HF_HOME/hub, else XDG_CACHE_HOME/huggingface/hub,
/// else ~/.cache/huggingface/hub.
pub fn default_cache_dir() -> PathBuf {
    let var = |k: &str| std::env::var_os(k).filter(|v| !v.is_empty()).map(PathBuf::from);
    if let Some(p) = var("HF_HUB_CACHE") {
        return p;
    }
    let home = var("HF_HOME").unwrap_or_else(|| {
        var("XDG_CACHE_HOME")
            .unwrap_or_else(|| var("HOME").unwrap_or_default().join(".cache"))
            .join("huggingface")
    });
    home.join("hub")
}

#[derive(Debug, Clone)]
pub struct HubCache {
    pub root: PathBuf,
}

impl HubCache {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    fn repo_dir(&self, repo: &str) -> PathBuf {
        self.root.join(format!("models--{}", repo.replace('/', "--")))
    }

    /// Every required file of every repo resolves through `refs/main`. HF only links a
    /// snapshot file once its blob is complete, so a cancelled download reads as missing.
    pub fn is_present(&self, m: &ModelSpec) -> bool {
        m.repos.iter().all(|r| {
            let dir = self.repo_dir(r.repo);
            let Ok(commit) = std::fs::read_to_string(dir.join("refs/main")) else { return false };
            let snapshot = dir.join("snapshots").join(commit.trim());
            r.required.iter().all(|f| Path::new(&snapshot.join(f)).exists())
        })
    }

    /// False only for a registry engine whose files are missing; the test engines ("fake")
    /// have no files.
    pub fn engine_ready(&self, engine: &str) -> bool {
        model_for_engine(engine).is_none_or(|m| self.is_present(m))
    }
}
```

Create `desktop/core/src/hub/store.rs` with just `//! Filled in by the next task.` so the module compiles. Add `pub mod hub;` to `lib.rs` (alphabetical, after `engine`).

- [ ] **Step 4: Run tests**

Run: `cd desktop && cargo test -p audiobookify-core --test hub`
Expected: 6 passed. (`Option::is_none_or` needs Rust 1.82+; if the toolchain is older, use `map_or(true, |m| self.is_present(m))`.)

- [ ] **Step 5: Commit**

```bash
git add desktop/core/src/hub desktop/core/src/lib.rs desktop/core/tests/hub.rs desktop/core/tests/common/mod.rs
git commit -m "Add the model registry and an HF cache presence check"
```

---

### Task 4: `Hub`: sizes, delete and downloads through `model_store`

**Files:**
- Modify: `desktop/core/src/hub/store.rs`
- Test: `desktop/core/tests/hub_store.rs`

**Interfaces:**
- Consumes: `HubCache`, `ModelSpec`, `RepoSpec` (Task 3); the `model_store` CLI contract (Task 2).
- Produces:
  ```rust
  #[derive(Debug, Clone, PartialEq, Serialize)]
  #[serde(tag = "state", rename_all = "snake_case")]
  pub enum DownloadState { Running { downloaded: u64, total: u64 }, Failed { error: String } }

  pub struct Hub { /* cache, python, cwd, env, downloads */ }
  impl Hub {
      pub fn new(cache: HubCache, python: impl Into<PathBuf>, cwd: impl Into<PathBuf>) -> Self;
      pub fn env(self, key: impl Into<OsString>, value: impl Into<OsString>) -> Self;
      pub fn cache(&self) -> &HubCache;
      pub fn sizes(&self) -> Result<HashMap<String, u64>, String>;      // every registry repo
      pub fn delete(&self, m: &ModelSpec) -> Result<u64, String>;       // bytes freed
      pub fn start_download(&self, m: &'static ModelSpec) -> Result<(), String>;
      pub fn cancel_download(&self, id: &str);
      pub fn download_state(&self, id: &str) -> Option<DownloadState>;
      pub fn downloading(&self) -> bool;                                 // any Running
  }
  ```
  A finished download removes its entry (presence then reads true). A failed one stays `Failed` until the next `start_download` for that id. Cancel removes the entry.

- [ ] **Step 1: Write the failing tests** (`desktop/core/tests/hub_store.rs`)

```rust
mod common;

use std::time::{Duration, Instant};

use audiobookify_core::hub::store::{DownloadState, Hub};
use audiobookify_core::hub::{self, HubCache};
use common::*;
use tempfile::TempDir;

fn hub(dir: &std::path::Path, env: &[(&str, &str)]) -> Hub {
    let repo = repo_root();
    let cache = dir.join("hub");
    env.iter().fold(
        Hub::new(HubCache::new(&cache), repo.join(".venv/bin/python"), &repo).env("HF_HUB_CACHE", &cache),
        |h, (k, v)| h.env(*k, *v),
    )
}

fn wait_until(mut done: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(30);
    while !done() {
        assert!(Instant::now() < deadline, "timed out");
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[test]
fn sizes_cover_every_registry_repo() {
    let dir = TempDir::new().unwrap();
    cache_model(&dir.path().join("hub"), "whisper");
    let sizes = hub(dir.path(), &[]).sizes().unwrap();
    assert_eq!(sizes["mlx-community/whisper-large-v3-turbo-asr-fp16"], 14); // 2 required files x b"weights"
    assert_eq!(sizes["mlx-community/S3TokenizerV2"], 0);
    assert_eq!(sizes.len(), 4);
}

#[test]
fn fake_download_completes_and_clears_its_entry() {
    let dir = TempDir::new().unwrap();
    let h = hub(dir.path(), &[("AUDIOBOOKIFY_FAKE_DOWNLOAD", "1")]);
    let turbo = hub::model("chatterbox-turbo").unwrap();
    h.start_download(turbo).unwrap();
    wait_until(|| h.download_state("chatterbox-turbo").is_none());
    assert!(h.cache().is_present(turbo));
}

#[test]
fn delete_frees_the_model_and_reports_bytes() {
    let dir = TempDir::new().unwrap();
    let h = hub(dir.path(), &[]);
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    cache_model(&dir.path().join("hub"), "whisper");
    let turbo = hub::model("chatterbox-turbo").unwrap();
    assert_eq!(h.delete(turbo).unwrap(), 4 * 7); // 3 + 1 required files
    assert!(!h.cache().is_present(turbo));
    assert!(h.cache().is_present(hub::model("whisper").unwrap()));
}

#[test]
fn failed_download_is_reported() {
    let dir = TempDir::new().unwrap();
    let h = hub(dir.path(), &[("AUDIOBOOKIFY_FAKE_DOWNLOAD", "error")]);
    h.start_download(hub::model("whisper").unwrap()).unwrap();
    wait_until(|| matches!(h.download_state("whisper"), Some(DownloadState::Failed { .. })));
    assert_eq!(h.download_state("whisper"), Some(DownloadState::Failed { error: "fake download failed".into() }));
}

#[test]
fn one_download_at_a_time_and_cancel_stops_it() {
    let dir = TempDir::new().unwrap();
    let h = hub(dir.path(), &[("AUDIOBOOKIFY_FAKE_DOWNLOAD", "1"), ("AUDIOBOOKIFY_FAKE_DOWNLOAD_DELAY", "0.3")]);
    h.start_download(hub::model("qwen3").unwrap()).unwrap();
    wait_until(|| matches!(h.download_state("qwen3"), Some(DownloadState::Running { downloaded, .. }) if downloaded > 0));
    assert!(h.downloading());
    assert!(h.start_download(hub::model("whisper").unwrap()).is_err());

    h.cancel_download("qwen3");
    assert_eq!(h.download_state("qwen3"), None);
    std::thread::sleep(Duration::from_millis(800)); // the killed child would have finished by now
    assert!(!h.cache().is_present(hub::model("qwen3").unwrap()));
    assert!(!h.downloading());
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd desktop && cargo test -p audiobookify-core --test hub_store`
Expected: compile error, `Hub` not found.

- [ ] **Step 3: Implement `desktop/core/src/hub/store.rs`**

```rust
//! Sizes, deletion and downloads, through one-shot `python -m audiobookify.model_store`
//! processes (huggingface_hub's own cache API; snapshots are symlinks into blob stores, so
//! hand-rolled du/rm get them wrong). Downloads run online; the engine worker never does.

use std::collections::HashMap;
use std::ffi::OsString;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex, MutexGuard};

use serde::Serialize;
use serde_json::{json, Value};

use super::{HubCache, ModelSpec, REGISTRY};

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum DownloadState {
    Running { downloaded: u64, total: u64 },
    Failed { error: String },
}

struct Entry {
    state: DownloadState,
    pid: u32,
    cancelled: bool,
    /// Held so the child sees EOF, and exits, when the entry or the app goes away.
    _stdin: Option<ChildStdin>,
}

type Downloads = Arc<Mutex<HashMap<String, Entry>>>;

pub struct Hub {
    cache: HubCache,
    python: PathBuf,
    cwd: PathBuf,
    env: Vec<(OsString, OsString)>,
    downloads: Downloads,
}

fn lock(d: &Downloads) -> MutexGuard<'_, HashMap<String, Entry>> {
    d.lock().unwrap_or_else(|p| p.into_inner())
}

impl Hub {
    pub fn new(cache: HubCache, python: impl Into<PathBuf>, cwd: impl Into<PathBuf>) -> Self {
        Self { cache, python: python.into(), cwd: cwd.into(), env: Vec::new(), downloads: Downloads::default() }
    }

    pub fn env(mut self, key: impl Into<OsString>, value: impl Into<OsString>) -> Self {
        self.env.push((key.into(), value.into()));
        self
    }

    pub fn cache(&self) -> &HubCache {
        &self.cache
    }

    fn command(&self, args: &[&str]) -> Command {
        let mut cmd = Command::new(&self.python);
        cmd.args(["-m", "audiobookify.model_store"]).args(args).current_dir(&self.cwd)
            .env_remove("HF_HUB_OFFLINE")
            .env("HF_HUB_CACHE", &self.cache.root)
            .envs(self.env.iter().map(|(k, v)| (k, v)))
            .stderr(Stdio::inherit());
        cmd
    }

    fn one_shot(&self, args: &[&str]) -> Result<Value, String> {
        let out = self.command(args).stdin(Stdio::null()).output()
            .map_err(|e| format!("could not start {}: {e}", self.python.display()))?;
        let line = String::from_utf8_lossy(&out.stdout);
        serde_json::from_str(line.trim()).map_err(|_| format!("model_store {} failed ({})", args[0], out.status))
    }

    pub fn sizes(&self) -> Result<HashMap<String, u64>, String> {
        let repos: Vec<&str> = REGISTRY.iter().flat_map(|m| m.repos.iter().map(|r| r.repo)).collect();
        let reply = self.one_shot(&[&["sizes"], repos.as_slice()].concat())?;
        serde_json::from_value(reply["sizes"].clone()).map_err(|e| e.to_string())
    }

    pub fn delete(&self, m: &ModelSpec) -> Result<u64, String> {
        let repos: Vec<&str> = m.repos.iter().map(|r| r.repo).collect();
        let reply = self.one_shot(&[&["delete"], repos.as_slice()].concat())?;
        reply["freed"].as_u64().ok_or_else(|| format!("unexpected reply {reply}"))
    }

    pub fn downloading(&self) -> bool {
        lock(&self.downloads).values().any(|e| matches!(e.state, DownloadState::Running { .. }))
    }

    // ponytail: one download at a time; parallel downloads only split the same bandwidth.
    pub fn start_download(&self, m: &'static ModelSpec) -> Result<(), String> {
        let mut downloads = lock(&self.downloads);
        if downloads.values().any(|e| matches!(e.state, DownloadState::Running { .. })) {
            return Err("Another download is still running. Wait for it to finish, or cancel it.".into());
        }
        let items: Vec<Value> = m.repos.iter()
            .map(|r| json!({"repo": r.repo, "allow_patterns": r.allow_patterns, "required": r.required}))
            .collect();
        let mut child = self.command(&["download", &Value::from(items).to_string()])
            .stdin(Stdio::piped()).stdout(Stdio::piped()).spawn()
            .map_err(|e| format!("could not start {}: {e}", self.python.display()))?;
        downloads.insert(m.id.to_string(), Entry {
            state: DownloadState::Running { downloaded: 0, total: m.download_bytes },
            pid: child.id(),
            cancelled: false,
            _stdin: child.stdin.take(),
        });
        let (map, id) = (self.downloads.clone(), m.id.to_string());
        std::thread::spawn(move || watch(map, id, child));
        Ok(())
    }

    pub fn cancel_download(&self, id: &str) {
        let mut downloads = lock(&self.downloads);
        if let Some(entry) = downloads.get_mut(id) {
            entry.cancelled = true;
            if matches!(entry.state, DownloadState::Running { .. }) {
                unsafe { libc::kill(entry.pid as i32, libc::SIGTERM) };
            }
        }
        downloads.remove(id);
    }

    pub fn download_state(&self, id: &str) -> Option<DownloadState> {
        lock(&self.downloads).get(id).map(|e| e.state.clone())
    }
}

/// Reads one download's progress lines until the child exits, then records how it ended.
fn watch(downloads: Downloads, id: String, mut child: Child) {
    let stdout = BufReader::new(child.stdout.take().expect("piped"));
    let mut failure = Some("The download stopped before it finished.".to_string());
    for line in stdout.lines().map_while(Result::ok) {
        let Ok(v) = serde_json::from_str::<Value>(&line) else { continue };
        if v.get("done").is_some() {
            failure = None;
        } else if let Some(e) = v.get("error").and_then(Value::as_str) {
            failure = Some(e.to_string());
        } else if let (Some(downloaded), Some(total)) = (v["downloaded"].as_u64(), v["total"].as_u64()) {
            if let Some(entry) = lock(&downloads).get_mut(&id).filter(|e| !e.cancelled) {
                entry.state = DownloadState::Running { downloaded, total };
            }
        }
    }
    let _ = child.wait();
    let mut map = lock(&downloads);
    // A cancelled entry was already removed; a later download of the same id may own it now.
    let Some(entry) = map.get_mut(&id).filter(|e| e.pid == child.id()) else { return };
    match failure {
        None => {
            map.remove(&id);
        }
        Some(error) => entry.state = DownloadState::Failed { error },
    }
}
```

Add `libc = "0.2"` is already a core dependency (it is: `desktop/core/Cargo.toml`). Note `child.id()` after `wait()` still returns the pid.

- [ ] **Step 4: Run tests**

Run: `cd desktop && cargo test -p audiobookify-core --test hub_store`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add desktop/core/src/hub/store.rs desktop/core/tests/hub_store.rs
git commit -m "Add Hub: model sizes, delete and cancellable downloads via model_store"
```

---

### Task 5: Runner unload control and missing-model check

**Files:**
- Modify: `desktop/core/src/engine.rs` (add `shutdown`, make `Drop` call it)
- Modify: `desktop/core/src/jobs/runner.rs`, `desktop/core/src/jobs/mod.rs` (re-export `RunnerControl`)
- Modify: `desktop/core/src/app/mod.rs` (`AppPaths::runner_paths` takes the hub cache)
- Test: `desktop/core/tests/runner.rs`

**Interfaces:**
- Consumes: `HubCache::engine_ready`, `hub::model_for_engine` (Task 3).
- Produces:
  ```rust
  impl PythonEngine { pub fn shutdown(&mut self); }
  pub struct Paths { pub cache_root, pub text_clip_dir, pub backup_dir, pub models: HubCache }  // new field
  impl AppPaths { pub fn runner_paths(&self, models: HubCache) -> crate::jobs::Paths; }
  #[derive(Clone, Default)] pub struct RunnerControl { /* Arc<AtomicBool> */ }
  impl RunnerControl { pub fn unload_and_wait(&self, timeout: Duration) -> bool; }
  impl Runner { pub fn control(&self) -> RunnerControl; pub fn engine_pid(&self) -> Option<u32>;
                pub fn handle_unload(&mut self); }
  pub fn missing_model_message(label: &str) -> String  // in app::models later; runner uses the same text
  ```
  The runner fails a job whose engine isn't downloaded with: `"{label} isn't downloaded yet. Download it in Settings."` (label from the registry, e.g. "Speed engine"). The same text is used by Task 6's gate; define it once as `pub fn not_downloaded(label: &str) -> String` in `hub/mod.rs`.

- [ ] **Step 1: Write the failing tests** (append to `desktop/core/tests/runner.rs`; update its `Paths { .. }` construction at line 23 to add `models: HubCache::new(dir.join("hub"))`)

```rust
#[test]
fn unload_drops_the_engine_child_and_the_next_job_respawns_it() {
    let dir = TempDir::new().unwrap();
    let mut runner = runner(dir.path(), &[]);          // the file's existing helper
    queue_clip(&runner.db, "Hello there.");           // existing helper that queues a fake-engine text clip; add if missing
    assert!(runner.run_once().unwrap());
    assert!(runner.engine_pid().is_some());

    let control = runner.control();
    let waiter = std::thread::spawn(move || control.unload_and_wait(std::time::Duration::from_secs(5)));
    std::thread::sleep(std::time::Duration::from_millis(100));
    runner.handle_unload();
    assert!(waiter.join().unwrap());
    assert_eq!(runner.engine_pid(), None);
}

#[test]
fn unload_and_wait_times_out_when_nobody_answers() {
    let dir = TempDir::new().unwrap();
    let runner = runner(dir.path(), &[]);
    assert!(!runner.control().unload_and_wait(std::time::Duration::from_millis(200)));
}

#[test]
fn a_job_for_an_engine_that_is_not_downloaded_fails_plainly() {
    let dir = TempDir::new().unwrap();
    let mut runner = runner(dir.path(), &[]);          // its hub cache dir is empty
    let clip = runner.db.create_text_clip("Hello.", None, "chatterbox-turbo", None).unwrap();
    assert!(runner.run_once().unwrap());
    let clip = runner.db.get_text_clip(clip.id).unwrap();
    assert_eq!(clip.error.as_deref(), Some("Speed engine isn't downloaded yet. Download it in Settings."));
    assert_eq!(runner.engine_pid(), None); // never spawned the worker for it
}
```

Check the existing helper names in `tests/runner.rs` (the function at line ~20 that calls `Runner::new`, and how existing tests queue a text clip with engine `"fake"`) and use those; the snippets above assume `runner(dir, env)` and use `db.create_text_clip(text, voice_profile_id, engine, tempo)` (signature at `db/jobs.rs:93`).

- [ ] **Step 2: Run to verify failure**

Run: `cd desktop && cargo test -p audiobookify-core --test runner`
Expected: compile errors (`models` field, `engine_pid`, `control`).

- [ ] **Step 3: Implement**

`engine.rs` — replace the `Drop` impl:

```rust
impl PythonEngine {
    /// Close stdin (the worker exits on EOF), give it a moment, then kill. The next request
    /// starts a fresh child.
    pub fn shutdown(&mut self) {
        self.loaded = None;
        let Some(Proc { mut child, stdin, stdout }) = self.proc.take() else { return };
        drop(stdin);
        drop(stdout);
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

impl Drop for PythonEngine {
    fn drop(&mut self) {
        self.shutdown();
    }
}
```

`hub/mod.rs` — add:

```rust
/// What generating with a missing model says, from the gate and from the runner.
pub fn not_downloaded(label: &str) -> String {
    format!("{label} isn't downloaded yet. Download it in Settings.")
}
```

`runner.rs`:

```rust
use std::sync::Arc;
use std::time::Instant;
use crate::hub::{self, HubCache};

pub struct Paths {
    pub cache_root: PathBuf,
    pub text_clip_dir: PathBuf,
    pub backup_dir: PathBuf,
    /// Where the engines' weights must be; a job for a missing engine fails without loading it.
    pub models: HubCache,
}

/// Lets the Settings page ask the runner thread to drop its engine worker (and the
/// weights it holds) before deleting a model.
#[derive(Clone, Default)]
pub struct RunnerControl {
    unload: Arc<AtomicBool>,
}

impl RunnerControl {
    /// True once the runner has dropped its worker. Only answered between units of work.
    pub fn unload_and_wait(&self, timeout: Duration) -> bool {
        self.unload.store(true, Ordering::SeqCst);
        let deadline = Instant::now() + timeout;
        while self.unload.load(Ordering::SeqCst) {
            if Instant::now() >= deadline {
                self.unload.store(false, Ordering::SeqCst);
                return false;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        true
    }
}
```

Add `control: RunnerControl` to `Runner` (initialised `RunnerControl::default()` in `new`), and:

```rust
    pub fn control(&self) -> RunnerControl {
        self.control.clone()
    }

    pub fn engine_pid(&self) -> Option<u32> {
        self.engine.child_pid()
    }

    /// Drop the engine worker if the Settings page asked for it.
    pub fn handle_unload(&mut self) {
        if self.control.unload.load(Ordering::SeqCst) {
            self.engine.shutdown();
            self.control.unload.store(false, Ordering::SeqCst);
        }
    }
```

In `run_forever`, call `self.handle_unload();` at the top of the `while` body and inside the idle `for` loop before each sleep. In `engine_and_voice`, before `self.engine.load(engine)?`:

```rust
        if !self.paths.models.engine_ready(engine) {
            let label = hub::model_for_engine(engine).map_or(engine, |m| m.label);
            return Err(EngineError::Engine(hub::not_downloaded(label)).into());
        }
```

In `transcribe_voice`, before `self.engine.transcribe(..)`:

```rust
        let whisper = hub::model(hub::TRANSCRIPTION).expect("registry has transcription");
        if !self.paths.models.is_present(whisper) {
            self.db.fail_voice_transcription(profile.id, &hub::not_downloaded(whisper.label))?;
            return Ok(());
        }
```

`app/mod.rs`:

```rust
    pub fn runner_paths(&self, models: crate::hub::HubCache) -> crate::jobs::Paths {
        crate::jobs::Paths { cache_root: self.cache(), text_clip_dir: self.text_clips(), backup_dir: self.output(), models }
    }
```

`jobs/mod.rs`: re-export `RunnerControl` alongside `Runner`.

Existing runner tests that transcribe with `AUDIOBOOKIFY_FAKE_STT` now need Whisper "present": in the test helper, build the runner's `models` with `ready_cache(dir)` instead of an empty dir, and give `a_job_for_an_engine_that_is_not_downloaded_fails_plainly` its own runner whose `models` is `HubCache::new(dir.join("empty"))`. Fake engines are unaffected (`engine_ready("fake")` is true).

- [ ] **Step 4: Run tests**

Run: `cd desktop && cargo test -p audiobookify-core`
Expected: all pass. `desktop/src/main.rs` doesn't compile yet (`runner_paths` arity); that is fixed in Task 8. Check with `cargo test -p audiobookify-core` only.

- [ ] **Step 5: Commit**

```bash
git add desktop/core
git commit -m "Let Settings unload the engine worker; fail jobs for missing models plainly"
```

---

### Task 6: Model commands' logic and the generation gate

**Files:**
- Create: `desktop/core/src/app/models.rs`, `desktop/core/src/db/stats.rs` (the `active_work` query; Task 7 adds the stats queries to the same file)
- Modify: `desktop/core/src/app/mod.rs` (`pub mod models;`, `list_engines` takes the cache and reports `ready`)
- Modify: `desktop/core/src/app/jobs.rs`, `app/clips.rs`, `app/voices.rs` (gate)
- Modify: `desktop/core/src/db/mod.rs` (`mod stats;`)
- Modify tests: `desktop/core/tests/app_jobs.rs`, `app_voices.rs`, and wherever `clips::create_text_clip` / `list_engines` are called
- Test: `desktop/core/tests/app_models.rs`

**Interfaces:**
- Consumes: `Hub`, `DownloadState` (Task 4), `RunnerControl` (Task 5), `hub::not_downloaded`.
- Produces:
  ```rust
  // db/stats.rs
  pub struct ActiveWork { pub engines: BTreeSet<String>, pub transcriptions: bool }
  impl ActiveWork { pub fn any(&self) -> bool }
  impl Database { pub fn active_work(&self) -> Result<ActiveWork> }

  // app/models.rs
  #[derive(Debug, Serialize)] pub struct ModelRow { pub id: &'static str, pub label: &'static str, pub purpose: &'static str,
      pub repos: Vec<&'static str>, pub engine: Option<&'static str>, pub downloaded: bool, pub size: u64,
      pub download_bytes: u64, pub download: Option<DownloadState> }
  #[derive(Debug, Serialize)] pub struct ModelProgress { pub id: &'static str, pub downloaded: bool, pub download: Option<DownloadState> }
  #[derive(Debug, Serialize)] pub struct Deleted { pub freed: u64 }
  pub fn list_models(hub: &Hub) -> ApiResult<Vec<ModelRow>>;
  pub fn model_progress(hub: &Hub) -> Vec<ModelProgress>;
  pub fn download_model(hub: &Hub, id: &str) -> ApiResult<()>;
  pub fn cancel_model_download(hub: &Hub, id: &str);
  pub fn delete_model(db: &Database, hub: &Hub, runner: &RunnerControl, id: &str) -> ApiResult<Deleted>;
  pub(crate) fn require_engine(cache: &HubCache, engine: &str) -> ApiResult<()>;
  pub(crate) fn require_transcription(cache: &HubCache) -> ApiResult<()>;

  // changed signatures (cache inserted after db):
  app::list_engines(cache: &HubCache) -> Engines            // EngineDto gains `ready: bool`
  jobs::generate_book(db, cache: &HubCache, book_id, engine)
  jobs::generate_chapter(db, cache: &HubCache, book_id, chapter_id, engine)
  clips::create_text_clip(db, cache: &HubCache, text, voice_profile_id, engine, tempo)
  voices::transcribe_voice(db, cache: &HubCache, voice_id)
  ```
  `create_voice` stays ungated: without Whisper the runner records the plain "Voice transcription isn't downloaded yet…" on the voice (Task 5), and a typed transcript still works.

- [ ] **Step 1: Write the failing tests** (`desktop/core/tests/app_models.rs`)

```rust
mod common;

use std::time::Duration;

use audiobookify_core::app::{self, clips, jobs, models};
use audiobookify_core::hub::store::Hub;
use audiobookify_core::hub::HubCache;
use audiobookify_core::jobs::RunnerControl;
use audiobookify_core::models::JobStatus;
use common::*;
use tempfile::TempDir;

fn hub_at(dir: &std::path::Path) -> Hub {
    let repo = repo_root();
    let cache = dir.join("hub");
    Hub::new(HubCache::new(&cache), repo.join(".venv/bin/python"), &repo).env("HF_HUB_CACHE", &cache)
}

/// A runner thread stand-in that answers unload requests.
fn answering_runner() -> RunnerControl {
    // Built from a real Runner would need an engine; the control is just a flag, so a
    // helper thread clears it the way Runner::handle_unload does.
    let control = RunnerControl::default();
    let c = control.clone();
    std::thread::spawn(move || loop {
        c.answer_unload_for_tests();
        std::thread::sleep(Duration::from_millis(10));
    });
    control
}

#[test]
fn list_models_shows_only_the_registry_with_sizes() {
    let dir = TempDir::new().unwrap();
    cache_model(&dir.path().join("hub"), "whisper");
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    std::fs::create_dir_all(dir.path().join("hub/models--someone--else/blobs")).unwrap();
    let rows = models::list_models(&hub_at(dir.path())).unwrap();
    let ids: Vec<_> = rows.iter().map(|r| r.id).collect();
    assert_eq!(ids, ["qwen3", "chatterbox-turbo", "whisper"]);
    let turbo = &rows[1];
    assert!(turbo.downloaded);
    assert_eq!(turbo.size, 4 * 7);
    assert!(!rows[0].downloaded);
    assert_eq!(rows[0].size, 0);
}

#[test]
fn delete_is_refused_while_queued_work_needs_the_engine() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    clips::create_text_clip(&db, hub.cache(), "Hi.", None, Some("chatterbox-turbo"), None).unwrap();
    let _ = paths;

    let e = models::delete_model(&db, &hub, &answering_runner(), "chatterbox-turbo").unwrap_err();
    assert!(e.0.contains("in use"), "{e}");
    assert!(hub.cache().is_present(audiobookify_core::hub::model("chatterbox-turbo").unwrap()));
}

#[test]
fn delete_of_another_engine_while_busy_needs_no_unload() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    cache_model(&dir.path().join("hub"), "qwen3");
    clips::create_text_clip(&db, hub.cache(), "Hi.", None, Some("chatterbox-turbo"), None).unwrap();
    // No one answers unload requests: this must not wait for one.
    let freed = models::delete_model(&db, &hub, &RunnerControl::default(), "qwen3").unwrap().freed;
    assert_eq!(freed, 4 * 7);
}

#[test]
fn transcription_delete_is_refused_while_anything_is_working() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "whisper");
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    clips::create_text_clip(&db, hub.cache(), "Hi.", None, Some("chatterbox-turbo"), None).unwrap();
    assert!(models::delete_model(&db, &hub, &answering_runner(), "whisper").is_err());
}

#[test]
fn idle_delete_unloads_first_and_frees_the_space() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    assert_eq!(models::delete_model(&db, &hub, &answering_runner(), "chatterbox-turbo").unwrap().freed, 28);
    // Idle, but nobody answers: refused rather than deleting under a loaded engine.
    cache_model(&dir.path().join("hub"), "chatterbox-turbo");
    let e = models::delete_model(&db, &hub, &RunnerControl::default(), "chatterbox-turbo").unwrap_err();
    assert!(e.0.contains("Try again"), "{e}");
}

#[test]
fn unknown_model_ids_are_refused() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let hub = hub_at(dir.path());
    assert!(models::download_model(&hub, "facebook/wav2vec2-lv-60-espeak-cv-ft").is_err());
    assert!(models::delete_model(&db, &hub, &answering_runner(), "someone/else").is_err());
}

#[test]
fn generating_with_a_missing_engine_is_refused() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let src = write_file(dir.path(), "book.md", TWO_CHAPTERS_MD);
    let book = audiobookify_core::app::books::create_book(&db, &paths, &src, &path(dir.path(), "out")).unwrap();
    let empty = HubCache::new(dir.path().join("empty"));
    let e = jobs::generate_book(&db, &empty, book.id, Some("chatterbox-turbo")).unwrap_err();
    assert_eq!(e.0, "Speed engine isn't downloaded yet. Download it in Settings.");
    assert!(clips::create_text_clip(&db, &empty, "Hi.", None, Some("chatterbox-turbo"), None).is_err());
    assert!(db.list_text_clips().unwrap().iter().all(|c| c.status != JobStatus::Queued));
}

#[test]
fn list_engines_reports_readiness() {
    let dir = TempDir::new().unwrap();
    cache_model(dir.path(), "chatterbox-turbo");
    let engines = app::list_engines(&HubCache::new(dir.path()));
    let ready: Vec<_> = engines.engines.iter().map(|e| (e.spec.id, e.ready)).collect();
    assert_eq!(ready, [("qwen3", false), ("chatterbox-turbo", true)]);
}
```

`answer_unload_for_tests` is a `#[doc(hidden)] pub fn` on `RunnerControl` that does what `Runner::handle_unload` does to the flag (clear it if set) — add it in this task's Step 3. `app(dir)`, `write_file`, `TWO_CHAPTERS_MD`, `path` are existing `tests/common` helpers (used by `app_jobs.rs`).

- [ ] **Step 2: Run to verify failure**

Run: `cd desktop && cargo test -p audiobookify-core --test app_models`
Expected: compile errors (`app::models`, new signatures).

- [ ] **Step 3: Implement**

`db/stats.rs`:

```rust
//! Read-only queries for the Settings page.

use std::collections::BTreeSet;

use rusqlite::params;

use super::{Database, Result};
use crate::models::JobStatus;

/// Work that is queued or running, and so needs its models on disk.
#[derive(Debug, Default)]
pub struct ActiveWork {
    pub engines: BTreeSet<String>,
    pub transcriptions: bool,
}

impl ActiveWork {
    pub fn any(&self) -> bool {
        !self.engines.is_empty() || self.transcriptions
    }
}

impl Database {
    pub fn active_work(&self) -> Result<ActiveWork> {
        let (q, g) = (JobStatus::Queued, JobStatus::Generating);
        let engines = self.query_all(
            "SELECT b.engine FROM jobs j JOIN chapters c ON c.id = j.chapter_id JOIN books b ON b.id = c.book_id \
             WHERE j.status IN (?1, ?2) \
             UNION SELECT engine FROM text_clips WHERE status IN (?1, ?2)",
            params![q, g], |r| r.get::<_, String>(0))?;
        let transcriptions = self.query_opt(
            "SELECT 1 FROM voice_profiles WHERE transcript_status = 'pending' LIMIT 1", [], |r| r.get::<_, i64>(0))?
            .is_some();
        Ok(ActiveWork { engines: engines.into_iter().collect(), transcriptions })
    }
}
```

Check `query_all`/`query_opt` signatures in `db/mod.rs:79-90` and adapt (they take `sql, params, row_fn`). Register with `mod stats;` in `db/mod.rs` and `pub use stats::ActiveWork;`.

`app/models.rs`:

```rust
//! The Settings page's Models section: the registry's models, their downloads, and deletes
//! that never pull files out from under queued or running work.

use std::time::Duration;

use serde::Serialize;

use super::{err, ApiError, ApiResult};
use crate::db::Database;
use crate::hub::store::{DownloadState, Hub};
use crate::hub::{self, HubCache, ModelSpec, REGISTRY};
use crate::jobs::RunnerControl;

#[derive(Debug, Serialize)]
pub struct ModelRow {
    pub id: &'static str,
    pub label: &'static str,
    pub purpose: &'static str,
    pub repos: Vec<&'static str>,
    pub engine: Option<&'static str>,
    pub downloaded: bool,
    /// Bytes on disk now (partial downloads included).
    pub size: u64,
    /// Roughly what a download fetches.
    pub download_bytes: u64,
    pub download: Option<DownloadState>,
}

#[derive(Debug, Serialize)]
pub struct ModelProgress {
    pub id: &'static str,
    pub downloaded: bool,
    pub download: Option<DownloadState>,
}

#[derive(Debug, Serialize)]
pub struct Deleted {
    pub freed: u64,
}

fn known(id: &str) -> ApiResult<&'static ModelSpec> {
    hub::model(id).ok_or_else(|| ApiError(format!("unknown model '{id}'")))
}

pub fn list_models(h: &Hub) -> ApiResult<Vec<ModelRow>> {
    let sizes = h.sizes().map_err(ApiError)?;
    Ok(REGISTRY.iter().map(|m| ModelRow {
        id: m.id,
        label: m.label,
        purpose: m.purpose,
        repos: m.repos.iter().map(|r| r.repo).collect(),
        engine: m.engine,
        downloaded: h.cache().is_present(m),
        size: m.repos.iter().map(|r| sizes.get(r.repo).copied().unwrap_or(0)).sum(),
        download_bytes: m.download_bytes,
        download: h.download_state(m.id),
    }).collect())
}

/// The cheap poll while a download runs: no Python, just the cache layout and the progress.
pub fn model_progress(h: &Hub) -> Vec<ModelProgress> {
    REGISTRY.iter().map(|m| ModelProgress {
        id: m.id, downloaded: h.cache().is_present(m), download: h.download_state(m.id),
    }).collect()
}

pub fn download_model(h: &Hub, id: &str) -> ApiResult<()> {
    h.start_download(known(id)?).map_err(ApiError)
}

pub fn cancel_model_download(h: &Hub, id: &str) {
    h.cancel_download(id);
}

pub fn delete_model(db: &Database, h: &Hub, runner: &RunnerControl, id: &str) -> ApiResult<Deleted> {
    let m = known(id)?;
    if h.download_state(id).is_some_and(|s| matches!(s, DownloadState::Running { .. })) {
        return err("It's still downloading. Cancel the download first.");
    }
    let work = db.active_work()?;
    match m.engine {
        Some(engine) if work.engines.contains(engine) => {
            return err(format!("The {} is in use by queued or rendering work. Cancel it or let it finish, then delete.",
                               m.label.to_lowercase()));
        }
        None if work.any() => {
            return err("Voice transcription can't be deleted while the studio is working. Try again when it's idle.");
        }
        _ => {}
    }
    // Busy with another engine: that engine is the loaded one, so this model isn't in memory.
    // Idle: whatever was last used is still loaded, so drop the worker first.
    // ponytail: work queued between this check and the delete fails with "isn't downloaded yet".
    if !work.any() && !runner.unload_and_wait(Duration::from_secs(10)) {
        return err("The studio is still holding the model. Try again in a moment.");
    }
    Ok(Deleted { freed: h.delete(m).map_err(ApiError)? })
}

pub(crate) fn require_engine(cache: &HubCache, engine: &str) -> ApiResult<()> {
    match hub::model_for_engine(engine) {
        Some(m) if !cache.is_present(m) => err(hub::not_downloaded(m.label)),
        _ => Ok(()),
    }
}

pub(crate) fn require_transcription(cache: &HubCache) -> ApiResult<()> {
    let m = hub::model(hub::TRANSCRIPTION).expect("registry has transcription");
    if cache.is_present(m) { Ok(()) } else { err(hub::not_downloaded(m.label)) }
}
```

`RunnerControl` (runner.rs) — add:

```rust
    #[doc(hidden)]
    /// What `Runner::handle_unload` does to the flag, for tests without an engine.
    pub fn answer_unload_for_tests(&self) {
        let _ = self.unload.compare_exchange(true, false, Ordering::SeqCst, Ordering::SeqCst);
    }
```

`app/mod.rs` — `list_engines`:

```rust
#[derive(Debug, Serialize)]
pub struct EngineDto {
    #[serde(flatten)]
    pub spec: &'static EngineSpec,
    /// Its weights are downloaded.
    pub ready: bool,
}

#[derive(Debug, Serialize)]
pub struct Engines {
    pub default: &'static str,
    pub engines: Vec<EngineDto>,
}

pub fn list_engines(cache: &crate::hub::HubCache) -> Engines {
    let engines = ENGINES.iter().map(|spec| EngineDto { spec, ready: cache.engine_ready(spec.id) }).collect();
    Engines { default: crate::models::DEFAULT_ENGINE, engines }
}
```

Gate: in `jobs::generate_book` and `generate_chapter`, after `chosen_engine(..)?` resolves the engine, call `super::models::require_engine(cache, &engine)?;` before queueing anything. In `clips::create_text_clip`, the same after it resolves the engine. In `voices::transcribe_voice`, call `super::models::require_transcription(cache)?;` first.

Update every caller: in `tests/app_jobs.rs`, `tests/app_voices.rs` and any other test, add `let models = ready_cache(dir.path());` in each setup and pass `&models` (mechanical; `sed -i '' 's/jobs::generate_book(&db, /jobs::generate_book(\&db, \&models, /'` style per function, then fix setups to define `models`). Run `grep -rn "generate_book(\|generate_chapter(\|create_text_clip(\|transcribe_voice(\|list_engines(" desktop/core/tests desktop/core/src` to find them all.

- [ ] **Step 4: Run tests**

Run: `cd desktop && cargo test -p audiobookify-core`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/core
git commit -m "Model list, download and safe delete; refuse generating with missing models"
```

---

### Task 7: Stats, with the duration backfill

**Files:**
- Modify: `desktop/core/src/db/migrations.rs` (`("text_clips", "duration", "REAL")`), `desktop/core/src/db/schema.sql` (add `duration REAL` to `text_clips`)
- Modify: `desktop/core/src/db/stats.rs` (stats queries), `desktop/core/src/db/jobs.rs` (`set_text_clip_duration`)
- Modify: `desktop/core/src/jobs/runner.rs` (record a finished clip's duration)
- Create: `desktop/core/src/app/stats.rs`; modify `app/mod.rs` (`pub mod stats;`)
- Test: `desktop/core/tests/app_stats.rs`

**Interfaces:**
- Produces:
  ```rust
  #[derive(Debug, Serialize, PartialEq)] pub struct EngineStats { pub engine: String, pub audio_seconds: f64, pub generating_seconds: f64 }
  #[derive(Debug, Serialize, PartialEq)] pub struct Named { pub name: String, pub seconds: f64 }
  #[derive(Debug, Serialize, PartialEq)] pub struct BusyDay { pub day: String /* YYYY-MM-DD, local */, pub chapters: i64 }
  #[derive(Debug, Serialize, PartialEq)] pub struct Stats {
      pub audio_seconds: f64, pub words: u64, pub books_finished: i64, pub chapters_done: i64,
      pub engines: Vec<EngineStats>, pub longest_book: Option<Named>, pub top_voice: Option<Named>,
      pub busiest_day: Option<BusyDay>, pub disk_bytes: u64 }
  pub fn app::stats::stats(db: &Database) -> ApiResult<Stats>;   // backfills NULL durations first
  impl Database { pub fn set_text_clip_duration(&self, clip_id: i64, seconds: f64) -> Result<()>; }
  ```
  Fun framings ("X days of nonstop listening", "about N novels") are computed in the frontend (Task 9) from `audio_seconds` and `words`.

- [ ] **Step 1: Write the failing tests** (`desktop/core/tests/app_stats.rs`)

```rust
mod common;

use audiobookify_core::app::stats::{self, Named};
use audiobookify_core::models::{ChapterOutcome, ChapterStatus, JobStatus};
use common::*;
use tempfile::TempDir;

#[test]
fn empty_library_is_all_zero() {
    let dir = TempDir::new().unwrap();
    let (_p, db) = app(dir.path());
    let s = stats::stats(&db).unwrap();
    assert_eq!((s.audio_seconds, s.words, s.books_finished, s.chapters_done, s.disk_bytes), (0.0, 0, 0, 0, 0));
    assert!(s.longest_book.is_none() && s.top_voice.is_none() && s.busiest_day.is_none());
}

#[test]
fn totals_come_from_done_chapters_and_clips() {
    let dir = TempDir::new().unwrap();
    let (_p, db) = app(dir.path());
    let book = md_book(&db, dir.path(), &path(dir.path(), "b.md"));
    let chapters = db.add_chapters(book.id, &[raw("One", "three little words"), raw("Two", "not done yet")]).unwrap();
    let mp3 = dir.path().join("one.mp3");
    std::fs::write(&mp3, vec![0u8; 5000]).unwrap();
    let job = db.create_job(chapters[0].id).unwrap();
    db.pop_next_queued_job().unwrap();
    db.finish_job(job.id, JobStatus::Done, None).unwrap();
    db.update_chapter_status(chapters[0].id, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some(&mp3.to_string_lossy()), duration: Some(60.0), engine: Some("qwen3"),
        ..Default::default() }).unwrap();
    let clip = db.create_text_clip("Hi.", None, "chatterbox-turbo", None).unwrap();
    db.finish_text_clip(clip.id, JobStatus::Done, Some("/nope.mp3"), None, false).unwrap();
    db.set_text_clip_duration(clip.id, 4.0).unwrap();

    let s = stats::stats(&db).unwrap();
    assert_eq!(s.audio_seconds, 64.0);
    assert_eq!(s.words, 3);
    assert_eq!(s.chapters_done, 1);
    assert_eq!(s.books_finished, 0); // chapter two isn't done
    assert_eq!(s.disk_bytes, 5000);
    assert_eq!(s.longest_book, Some(Named { name: "Test Book".into(), seconds: 60.0 }));
    assert_eq!(s.busiest_day.as_ref().unwrap().chapters, 1);
    let qwen = s.engines.iter().find(|e| e.engine == "qwen3").unwrap();
    assert_eq!(qwen.audio_seconds, 60.0);
    assert!(qwen.generating_seconds >= 0.0);
}

#[test]
fn a_finished_book_counts_and_its_voice_is_top() {
    let dir = TempDir::new().unwrap();
    let (_p, db) = app(dir.path());
    let (book, chapter) = book_with_chapter(&db, dir.path());
    let v = voice(&db, dir.path(), "Harriet", Some("hi"), Some("done"));
    db.update_book_voice_profile(book.id, Some(v.id)).unwrap();
    db.update_chapter_status(chapter.id, ChapterStatus::Done,
        ChapterOutcome { duration: Some(30.0), ..Default::default() }).unwrap();
    let s = stats::stats(&db).unwrap();
    assert_eq!(s.books_finished, 1);
    assert_eq!(s.top_voice, Some(Named { name: "Harriet".into(), seconds: 30.0 }));
}

#[test]
fn missing_durations_are_backfilled_from_the_mp3_once() {
    let dir = TempDir::new().unwrap();
    let (_p, db) = app(dir.path());
    let (_book, chapter) = book_with_chapter(&db, dir.path());
    let wav_path = dir.path().join("ch.wav"); // symphonia reads any format; a WAV keeps the test small
    wav(&wav_path, 24000, 1, 48000, |_| 0.0);
    db.update_chapter_status(chapter.id, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some(&wav_path.to_string_lossy()), duration: None, ..Default::default() }).unwrap();

    assert_eq!(stats::stats(&db).unwrap().audio_seconds, 2.0);
    assert_eq!(db.get_chapter(chapter.id).unwrap().duration, Some(2.0));
}

#[test]
fn the_migration_adds_text_clip_duration() {
    let dir = TempDir::new().unwrap();
    let (_p, db) = app(dir.path());
    let clip = db.create_text_clip("Hi.", None, "chatterbox-turbo", None).unwrap();
    db.set_text_clip_duration(clip.id, 1.5).unwrap();
}
```

Check `ChapterOutcome` really has `duration: Option<f64>` and that `update_chapter_status` writes `duration` (it does: `runner.rs` sets it). If `update_chapter_status` writes `duration = NULL` when `None` is passed, that is what the backfill test relies on.

- [ ] **Step 2: Run to verify failure**

Run: `cd desktop && cargo test -p audiobookify-core --test app_stats`
Expected: compile errors.

- [ ] **Step 3: Implement**

`migrations.rs`: append `("text_clips", "duration", "REAL"),` to `ADDED_COLUMNS`. `schema.sql`: add `duration REAL` after `tempo REAL` in `text_clips` (with a comma after `tempo REAL`).

`db/jobs.rs`:

```rust
    pub fn set_text_clip_duration(&self, clip_id: i64, seconds: f64) -> Result<()> {
        self.conn.execute("UPDATE text_clips SET duration = ?1 WHERE id = ?2", params![seconds, clip_id])?;
        Ok(())
    }
```

`runner.rs` `run_text_clip`, right after `finish_text_clip(.. Done ..)`:

```rust
        self.db.set_text_clip_duration(clip.id, combined.len() as f64 / info.sample_rate as f64)?;
```

(`combined` must still be alive there: compute `let seconds = combined.len() as f64 / info.sample_rate as f64;` before `export_mp3` consumes or borrows it, and use `seconds`.)

`db/stats.rs` — append:

```rust
/// Rows whose duration is missing but whose file may still be there: (id, path).
pub type MissingDuration = (i64, String);

impl Database {
    pub fn chapters_missing_duration(&self) -> Result<Vec<MissingDuration>> {
        self.query_all("SELECT id, output_mp3_path FROM chapters \
                        WHERE status = 'done' AND duration IS NULL AND output_mp3_path IS NOT NULL",
                       [], |r| Ok((r.get(0)?, r.get(1)?)))
    }

    pub fn text_clips_missing_duration(&self) -> Result<Vec<MissingDuration>> {
        self.query_all("SELECT id, output_path FROM text_clips \
                        WHERE status = 'done' AND duration IS NULL AND output_path IS NOT NULL",
                       [], |r| Ok((r.get(0)?, r.get(1)?)))
    }

    pub fn done_chapter_texts_and_files(&self) -> Result<Vec<(String, Option<String>)>> {
        self.query_all("SELECT text, output_mp3_path FROM chapters WHERE status = 'done'", [],
                       |r| Ok((r.get(0)?, r.get(1)?)))
    }

    /// One query per figure; the tables are small (one row per chapter).
    pub fn stat_numbers(&self) -> Result<(f64, i64, i64)> {
        let audio: f64 = self.query_opt(
            "SELECT COALESCE((SELECT SUM(duration) FROM chapters WHERE status = 'done'), 0) \
                  + COALESCE((SELECT SUM(duration) FROM text_clips WHERE status = 'done'), 0)",
            [], |r| r.get(0))?.unwrap_or(0.0);
        let chapters: i64 = self.query_opt("SELECT COUNT(*) FROM chapters WHERE status = 'done'", [], |r| r.get(0))?
            .unwrap_or(0);
        let books: i64 = self.query_opt(
            "SELECT COUNT(*) FROM books b WHERE EXISTS (SELECT 1 FROM chapters c WHERE c.book_id = b.id) \
             AND NOT EXISTS (SELECT 1 FROM chapters c WHERE c.book_id = b.id AND c.status != 'done')",
            [], |r| r.get(0))?.unwrap_or(0);
        Ok((audio, chapters, books))
    }

    /// (engine, audio seconds, generating seconds). A chapter counts its latest done job, so
    /// re-renders aren't double-counted.
    // ponytail: a paused-and-resumed job's started_at is its last start, so its time is undercounted.
    pub fn engine_stats(&self) -> Result<Vec<(String, f64, f64)>> {
        self.query_all(
            "SELECT engine, SUM(audio), SUM(spent) FROM ( \
               SELECT c.engine AS engine, c.duration AS audio, j.finished_at - j.started_at AS spent \
               FROM chapters c JOIN jobs j ON j.id = (SELECT MAX(id) FROM jobs WHERE chapter_id = c.id AND status = 'done') \
               WHERE c.status = 'done' AND c.engine IS NOT NULL AND c.duration IS NOT NULL AND j.started_at IS NOT NULL \
               UNION ALL \
               SELECT engine, duration, finished_at - started_at FROM text_clips \
               WHERE status = 'done' AND duration IS NOT NULL AND started_at IS NOT NULL \
             ) GROUP BY engine ORDER BY engine",
            [], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
    }

    pub fn longest_book(&self) -> Result<Option<(String, f64)>> {
        self.query_opt(
            "SELECT b.title, SUM(c.duration) AS s FROM chapters c JOIN books b ON b.id = c.book_id \
             WHERE c.status = 'done' AND c.duration IS NOT NULL GROUP BY b.id ORDER BY s DESC LIMIT 1",
            [], |r| Ok((r.get(0)?, r.get(1)?)))
    }

    /// The voice with the most narrated time, by each book's current voice.
    pub fn top_voice(&self) -> Result<Option<(String, f64)>> {
        self.query_opt(
            "SELECT v.name, SUM(c.duration) AS s FROM chapters c JOIN books b ON b.id = c.book_id \
             JOIN voice_profiles v ON v.id = b.voice_profile_id \
             WHERE c.status = 'done' AND c.duration IS NOT NULL GROUP BY v.id ORDER BY s DESC LIMIT 1",
            [], |r| Ok((r.get(0)?, r.get(1)?)))
    }

    pub fn busiest_day(&self) -> Result<Option<(String, i64)>> {
        self.query_opt(
            "SELECT date(finished_at, 'unixepoch', 'localtime') AS d, COUNT(*) AS n FROM jobs \
             WHERE status = 'done' AND finished_at IS NOT NULL GROUP BY d ORDER BY n DESC, d DESC LIMIT 1",
            [], |r| Ok((r.get(0)?, r.get(1)?)))
    }
}
```

`set_chapter_duration` already exists (`db/mod.rs:170`).

`app/stats.rs`:

```rust
//! The Settings page's listening stats, from the existing tables.

use std::fs;
use std::path::Path;

use serde::Serialize;

use super::ApiResult;
use crate::db::Database;
use crate::media;

#[derive(Debug, Serialize, PartialEq)]
pub struct EngineStats {
    pub engine: String,
    pub audio_seconds: f64,
    pub generating_seconds: f64,
}

#[derive(Debug, Serialize, PartialEq)]
pub struct Named {
    pub name: String,
    pub seconds: f64,
}

#[derive(Debug, Serialize, PartialEq)]
pub struct BusyDay {
    pub day: String,
    pub chapters: i64,
}

#[derive(Debug, Serialize, PartialEq)]
pub struct Stats {
    pub audio_seconds: f64,
    pub words: u64,
    pub books_finished: i64,
    pub chapters_done: i64,
    pub engines: Vec<EngineStats>,
    pub longest_book: Option<Named>,
    pub top_voice: Option<Named>,
    pub busiest_day: Option<BusyDay>,
    pub disk_bytes: u64,
}

/// Older chapters (and every text clip before this phase) have no stored duration: read it
/// from the file once and keep it. A file that's gone stays NULL and is skipped.
fn backfill(db: &Database) -> ApiResult<()> {
    for (id, file) in db.chapters_missing_duration()? {
        if let Ok(seconds) = media::duration(Path::new(&file)) {
            db.set_chapter_duration(id, seconds)?;
        }
    }
    for (id, file) in db.text_clips_missing_duration()? {
        if let Ok(seconds) = media::duration(Path::new(&file)) {
            db.set_text_clip_duration(id, seconds)?;
        }
    }
    Ok(())
}

pub fn stats(db: &Database) -> ApiResult<Stats> {
    backfill(db)?;
    let (audio_seconds, chapters_done, books_finished) = db.stat_numbers()?;
    let mut words = 0u64;
    let mut disk_bytes = 0u64;
    for (text, file) in db.done_chapter_texts_and_files()? {
        words += text.split_whitespace().count() as u64;
        disk_bytes += file.and_then(|f| fs::metadata(f).ok()).map_or(0, |m| m.len());
    }
    Ok(Stats {
        audio_seconds,
        words,
        books_finished,
        chapters_done,
        engines: db.engine_stats()?.into_iter()
            .map(|(engine, audio_seconds, generating_seconds)| EngineStats { engine, audio_seconds, generating_seconds })
            .collect(),
        longest_book: db.longest_book()?.map(|(name, seconds)| Named { name, seconds }),
        top_voice: db.top_voice()?.map(|(name, seconds)| Named { name, seconds }),
        busiest_day: db.busiest_day()?.map(|(day, chapters)| BusyDay { day, chapters }),
        disk_bytes,
    })
}
```

If `query_opt` maps "no row" to `None` but the aggregate SELECTs always return one row, the `unwrap_or(0.0)` covers it; `longest_book` etc. on an empty table return one row of NULLs from `SUM` only when there's no `GROUP BY` — these have `GROUP BY`, so they return no row. Good.

- [ ] **Step 4: Run tests**

Run: `cd desktop && cargo test -p audiobookify-core`
Expected: all pass (including the existing migrations tests; if one asserts the exact `text_clips` column list, add `duration`).

- [ ] **Step 5: Commit**

```bash
git add desktop/core
git commit -m "Add listening stats with a one-time duration backfill"
```

---

### Task 8: Tauri commands and app wiring

**Files:**
- Modify: `desktop/src/commands.rs`, `desktop/src/main.rs`

**Interfaces:**
- Consumes: everything from Tasks 3–7.
- Produces (Tauri commands, args in camelCase on the JS side):
  `list_engines() -> Engines`, `list_models() -> Vec<ModelRow>`, `model_progress() -> Vec<ModelProgress>`, `download_model(model: String)`, `cancel_model_download(model: String)`, `delete_model(model: String) -> Deleted`, `get_stats() -> Stats`; changed: `generate_book`, `generate_chapter`, `create_text_clip`, `transcribe_voice` pass `&state.hub.cache()`.

No unit tests here (commands only lock and delegate; logic is tested in core). Verified by `cargo build` and the GUI check in Task 11.

- [ ] **Step 1: `commands.rs`**

Add to `AppState`:

```rust
pub struct AppState {
    pub db: Mutex<Database>,
    pub paths: AppPaths,
    pub hub: Hub,
    pub runner: RunnerControl,
}
```

Imports: `use audiobookify_core::app::models::{self, Deleted, ModelProgress, ModelRow};`, `use audiobookify_core::app::stats::{self, Stats};`, `use audiobookify_core::hub::store::Hub;`, `use audiobookify_core::jobs::RunnerControl;`.

Change:

```rust
#[tauri::command]
pub fn list_engines(state: State<'_, AppState>) -> Engines {
    app::list_engines(state.hub.cache())
}
```

and pass `state.hub.cache()` as the new second argument in `generate_book`, `generate_chapter`, `create_text_clip`, `transcribe_voice`. Add:

```rust
#[tauri::command(async)]
pub fn list_models(state: State<'_, AppState>) -> ApiResult<Vec<ModelRow>> {
    models::list_models(&state.hub)
}

#[tauri::command]
pub fn model_progress(state: State<'_, AppState>) -> Vec<ModelProgress> {
    models::model_progress(&state.hub)
}

#[tauri::command]
pub fn download_model(state: State<'_, AppState>, model: String) -> ApiResult<()> {
    models::download_model(&state.hub, &model)
}

#[tauri::command]
pub fn cancel_model_download(state: State<'_, AppState>, model: String) {
    models::cancel_model_download(&state.hub, &model)
}

/// Doesn't hold the DB lock while it waits for the runner or runs Python.
#[tauri::command(async)]
pub fn delete_model(state: State<'_, AppState>, model: String) -> ApiResult<Deleted> {
    let work_db = state.paths.open_db()?;
    models::delete_model(&work_db, &state.hub, &state.runner, &model)
}

#[tauri::command(async)]
pub fn get_stats(state: State<'_, AppState>) -> ApiResult<Stats> {
    stats::stats(&state.db())
}
```

(`open_db` returns `db::Result`; `?` converts via `From<DbError> for ApiError`.)

- [ ] **Step 2: `main.rs`**

Replace `start_runner` so the control comes back to `main`:

```rust
fn python() -> PathBuf {
    repo_root().join(".venv/bin/python")
}

fn start_runner(db: Database, paths: &AppPaths, models: HubCache) -> RunnerControl {
    let engine = PythonEngine::new(python(), repo_root())
        .env("HF_HUB_OFFLINE", "1")
        .env("PATH", child_path(std::env::var_os("PATH")));
    let mut runner = Runner::new(db, engine, paths.runner_paths(models));
    let control = runner.control();
    std::thread::Builder::new()
        .name("job-runner".into())
        .spawn(move || runner.run_forever(&AtomicBool::new(false)))
        .expect("spawn the job runner thread");
    control
}
```

In `main()`:

```rust
    let models = HubCache::new(hub::default_cache_dir());
    let runner = start_runner(open(), &paths, models.clone());
    let hub = Hub::new(models, python(), repo_root()).env("PATH", child_path(std::env::var_os("PATH")));
    let state = AppState { db: Mutex::new(open()), paths: paths.clone(), hub, runner };
```

Imports: `use audiobookify_core::hub::{self, store::Hub, HubCache};`, `use audiobookify_core::jobs::{Runner, RunnerControl};`. Register the six new commands in `generate_handler!` (`list_models`, `model_progress`, `download_model`, `cancel_model_download`, `delete_model`, `get_stats`).

`PythonEngine` must be `Send` to move the built `Runner` into the thread; it holds `Child`/`ChildStdin`/`BufReader<ChildStdout>`, all `Send`. If the compiler disagrees, build the runner inside the thread and send the control back over a `std::sync::mpsc::channel`.

- [ ] **Step 3: Build**

Run: `cd desktop && cargo build 2>&1 | tail -3`
Expected: `Finished`.

- [ ] **Step 4: Commit**

```bash
git add desktop/src
git commit -m "Wire the model and stats commands into the app"
```

---

### Task 9: Frontend helpers and API

**Files:**
- Create: `frontend/src/lib/settings.js`, `frontend/src/lib/settings.test.js`
- Modify: `frontend/src/lib/api.js`, `frontend/src/lib/engines.js`, `frontend/src/lib/engines.test.js`

**Interfaces:**
- Produces:
  ```js
  // settings.js
  export function formatBytes(n)            // 1000-based, like Finder: "3.5 GB", "512 MB", "0 MB"
  export function formatDuration(seconds)   // "41 hours", "1 hour", "35 minutes", "under a minute"
  export function framings(stats)           // array of sentences, possibly empty
  export function renderPace(engineStats)   // "about 28 minutes for every hour of audio" or null
  export function downloadText(download)    // "1.2 of 3.5 GB"
  export function downloadFraction(download)// 0..1
  export function needsSetup(engines)       // true when no engine is ready
  // engines.js: effectiveEngine skips engines whose ready === false
  // api.js
  listModels, modelProgress, downloadModel(model), cancelModelDownload(model), deleteModel(model), getStats
  ```

- [ ] **Step 1: Write the failing tests** (`frontend/src/lib/settings.test.js`)

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  downloadFraction, downloadText, formatBytes, formatDuration, framings, needsSetup, renderPace,
} from "./settings.js";

test("formatBytes uses decimal units like Finder", () => {
  assert.equal(formatBytes(3_500_000_000), "3.5 GB");
  assert.equal(formatBytes(512_000_000), "512 MB");
  assert.equal(formatBytes(0), "0 MB");
  assert.equal(formatBytes(14_000_000_000), "14 GB");
});

test("formatDuration speaks in hours or minutes", () => {
  assert.equal(formatDuration(41 * 3600 + 100), "41 hours");
  assert.equal(formatDuration(3600), "1 hour");
  assert.equal(formatDuration(35 * 60), "35 minutes");
  assert.equal(formatDuration(1 * 60), "1 minute");
  assert.equal(formatDuration(20), "under a minute");
});

test("framings appear only once there is enough to say", () => {
  assert.deepEqual(framings({ audio_seconds: 600, words: 5000 }), []);
  assert.deepEqual(framings({ audio_seconds: 41 * 3600, words: 225_000 }), [
    "That's 1.7 days of nonstop listening.",
    "About 2.5 average novels, at 90,000 words each.",
  ]);
});

test("renderPace says how long an hour of audio takes", () => {
  assert.equal(renderPace({ audio_seconds: 3600, generating_seconds: 1680 }), "about 28 minutes for every hour of audio");
  assert.equal(renderPace({ audio_seconds: 3600, generating_seconds: 7200 }), "about 2 hours for every hour of audio");
  assert.equal(renderPace({ audio_seconds: 0, generating_seconds: 10 }), null);
});

test("download progress", () => {
  const d = { state: "running", downloaded: 1_200_000_000, total: 3_500_000_000 };
  assert.equal(downloadText(d), "1.2 of 3.5 GB");
  assert.ok(Math.abs(downloadFraction(d) - 0.343) < 0.001);
  assert.equal(downloadFraction({ state: "running", downloaded: 5, total: 0 }), 0);
});

test("needsSetup when no engine is ready", () => {
  assert.equal(needsSetup([{ id: "qwen3", ready: false }, { id: "chatterbox-turbo", ready: false }]), true);
  assert.equal(needsSetup([{ id: "qwen3", ready: false }, { id: "chatterbox-turbo", ready: true }]), false);
  assert.equal(needsSetup([]), false); // not loaded yet
});
```

Append to `engines.test.js`:

```js
test("effectiveEngine skips an engine that isn't downloaded", () => {
  const engines = [
    { id: "qwen3", needs_voice: true, ready: true },
    { id: "chatterbox-turbo", needs_voice: false, ready: false },
  ];
  assert.equal(effectiveEngine("chatterbox-turbo", engines, true), "qwen3");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test`
Expected: FAIL, cannot find `./settings.js`; the new engines test fails.

- [ ] **Step 3: Implement `frontend/src/lib/settings.js`**

```js
// Wording for the Settings page, kept out of the component so it is testable.

export function formatBytes(n) {
  if (n >= 1e9) {
    const gb = n / 1e9;
    return `${gb >= 10 ? Math.round(gb) : gb.toFixed(1).replace(/\.0$/, "")} GB`;
  }
  return `${Math.round(n / 1e6)} MB`;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function formatDuration(seconds) {
  if (seconds >= 3600) return plural(Math.round(seconds / 3600), "hour");
  if (seconds >= 60) return plural(Math.round(seconds / 60), "minute");
  return "under a minute";
}

const NOVEL_WORDS = 90_000;

export function framings({ audio_seconds, words }) {
  const out = [];
  const days = audio_seconds / 86400;
  if (days >= 1) out.push(`That's ${days.toFixed(1).replace(/\.0$/, "")} days of nonstop listening.`);
  const novels = words / NOVEL_WORDS;
  if (novels >= 1) out.push(`About ${novels.toFixed(1).replace(/\.0$/, "")} average novels, at 90,000 words each.`);
  return out;
}

export function renderPace({ audio_seconds, generating_seconds }) {
  if (!audio_seconds) return null;
  return `about ${formatDuration((generating_seconds / audio_seconds) * 3600)} for every hour of audio`;
}

export function downloadText({ downloaded, total }) {
  const unit = (n) => (n / 1e9).toFixed(1);
  return `${unit(downloaded)} of ${formatBytes(total)}`;
}

export function downloadFraction({ downloaded, total }) {
  return total > 0 ? Math.min(downloaded / total, 1) : 0;
}

export function needsSetup(engines) {
  return engines.length > 0 && !engines.some((e) => e.ready);
}
```

`engines.js` `effectiveEngine`:

```js
// The engine a request will actually use: one that needs a voice can't run without one,
// and one that isn't downloaded can't run at all.
export function effectiveEngine(chosen, engines, hasVoice) {
  const usable = (e) => e.ready !== false && (!e.needs_voice || hasVoice);
  const spec = engines.find((e) => e.id === chosen);
  if (spec && usable(spec)) return chosen;
  return engines.find(usable)?.id ?? chosen;
}
```

(The existing test `effectiveEngine falls back to an engine that needs no voice when none is picked` still passes: its engines have no `ready` key.)

`api.js` — add (args named `model`, never `modelId`; see Global Constraints):

```js
  listModels: () => call("list_models"),
  modelProgress: () => call("model_progress"),
  downloadModel: (model) => call("download_model", { model }),
  cancelModelDownload: (model) => call("cancel_model_download", { model }),
  deleteModel: (model) => call("delete_model", { model }),
  getStats: () => call("get_stats"),
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib
git commit -m "Add Settings wording helpers and model/stats API calls"
```

---

### Task 10: Settings page, nav, first-run and engine picker

**Files:**
- Create: `frontend/src/pages/SettingsPage.jsx`
- Modify: `frontend/src/App.jsx` (route `/settings`), `frontend/src/components/AppShell.jsx` (nav item, first-run redirect), `frontend/src/components/EnginePicker.jsx` (not-downloaded engines)

**Interfaces:**
- Consumes: `api.*` and `lib/settings.js` (Task 9), existing `Button`, `PageHeader`, `ErrorNote`, `ProgressBar`, `ConfirmDialog`.
- Produces: route `/settings`. Navigating to `/settings` with `state: { setup: true }` is not needed; the page shows the setup panel whenever `needsSetup(engines)`.

Before writing JSX, read DESIGN.md §3 (tokens/typography), §5 "Library card" and "Progress", §9 and §10, and look at `PronunciationsPage.jsx` for the page's spacing and class idioms. Use only existing Tailwind token classes (`bg-card`, `bg-oat`, `border-line`, `text-ink-muted`, `text-accent-text`, `rr-eyebrow`, `text-title`, `text-ui`, `text-meta`, `rounded-tile`, `rounded-input`, `font-serif`). If a class you need doesn't exist, check `frontend/src/index.css` for the `@theme` mapping of `--rr-*` tokens rather than writing a raw colour.

- [ ] **Step 1: Write `SettingsPage.jsx`**

```jsx
import { useEffect, useRef, useState } from "react";
import { Download, HardDrive, Trash2, X } from "lucide-react";
import { api } from "../lib/api.js";
import {
  downloadFraction, downloadText, formatBytes, formatDuration, framings, needsSetup, renderPace,
} from "../lib/settings.js";
import { engineLabel } from "../lib/engines.js";
import Button, { ErrorNote, PageHeader } from "../components/ui/Button.jsx";
import ProgressBar from "../components/ui/ProgressBar.jsx";
import ConfirmDialog from "../components/ui/ConfirmDialog.jsx";

const RECOMMENDED = "chatterbox-turbo"; // needs no narrator voice, so it works straight away

// The stats as a reader's card (DESIGN.md "Library card"): a <dl>, not stat tiles.
function ListeningCard({ stats, engines }) {
  if (stats.chapters_done === 0 && stats.audio_seconds === 0) {
    return (
      <p className="text-ui text-ink-muted">Nothing rendered yet. Your totals show up here after your first chapter.</p>
    );
  }
  const rows = [
    ["Words read aloud", stats.words.toLocaleString()],
    ["Chapters", stats.chapters_done.toLocaleString()],
    ["Books finished", stats.books_finished.toLocaleString()],
    stats.longest_book && ["Longest book", `${stats.longest_book.name} · ${formatDuration(stats.longest_book.seconds)}`],
    stats.top_voice && ["Favourite narrator", `${stats.top_voice.name} · ${formatDuration(stats.top_voice.seconds)}`],
    stats.busiest_day && [
      "Busiest day",
      `${new Date(`${stats.busiest_day.day}T12:00:00`).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })} · ${stats.busiest_day.chapters} chapters`,
    ],
    ...stats.engines.map((e) => {
      const pace = renderPace(e);
      return pace && [engineLabel(e.engine, engines), pace];
    }),
    ["On your disk", formatBytes(stats.disk_bytes)],
  ].filter(Boolean);
  return (
    <div className="max-w-[560px]">
      <p className="font-serif text-[30px] leading-tight">You've rendered {formatDuration(stats.audio_seconds)} of audio.</p>
      {framings(stats).map((line) => (
        <p key={line} className="text-ui text-ink-muted mt-1">{line}</p>
      ))}
      <dl className="mt-5 rounded-[4px_4px_14px_14px] border border-line border-t-[3px] border-t-accent-deco bg-card px-5 pt-3 pb-2">
        <p className="rr-eyebrow mb-1">Your listening so far</p>
        {rows.map(([label, value]) => (
          <div key={label} className="grid grid-cols-[150px_1fr] items-center min-h-8 border-t border-line text-meta">
            <dt className="text-ink-muted">{label}</dt>
            <dd className="text-ink tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function ModelRow({ model, busy, onDownload, onCancel, onDelete }) {
  const running = model.download?.state === "running";
  const failed = model.download?.state === "failed";
  return (
    <li className="flex flex-wrap items-start gap-x-6 gap-y-2 py-4 border-t border-line">
      <div className="flex-1 min-w-[260px]">
        <p className="font-serif text-[18px]">{model.label}</p>
        <p className="text-meta text-ink-muted mt-0.5">{model.purpose}</p>
        <p className="text-[12px] text-ink-muted mt-1">{model.repos.join(" · ")}</p>
        {running && (
          <div className="mt-3 max-w-[360px]">
            <ProgressBar value={downloadFraction(model.download)} label={`${model.label} download`} />
            <p className="text-[12px] text-ink-muted mt-1 tabular-nums">Downloading · {downloadText(model.download)}</p>
          </div>
        )}
        {failed && <p className="text-meta text-accent-text mt-2">Didn't finish: {model.download.error}</p>}
      </div>
      <div className="flex items-center gap-3">
        <span className="text-meta text-ink-muted tabular-nums">
          {model.downloaded ? `Downloaded · ${formatBytes(model.size)}` : `Not downloaded · about ${formatBytes(model.download_bytes)}`}
        </span>
        {running ? (
          <Button variant="secondary" onClick={onCancel}>
            <X className="size-4" aria-hidden="true" /> Cancel
          </Button>
        ) : model.downloaded ? (
          <Button variant="secondary" onClick={onDelete} disabled={busy} aria-label={`Delete ${model.label}`}>
            <Trash2 className="size-4" aria-hidden="true" /> Delete
          </Button>
        ) : (
          <Button variant="secondary" onClick={onDownload} disabled={busy}>
            <Download className="size-4" aria-hidden="true" /> {failed ? "Try again" : "Download"}
          </Button>
        )}
      </div>
    </li>
  );
}

export default function SettingsPage() {
  const [stats, setStats] = useState(null);
  const [models, setModels] = useState([]);
  const [engines, setEngines] = useState([]);
  const [error, setError] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);
  const wasRunning = useRef(false);

  async function refreshModels() {
    const [rows, eng] = await Promise.all([api.listModels(), api.listEngines()]);
    setModels(rows);
    setEngines(eng.engines);
  }

  useEffect(() => {
    api.getStats().then(setStats).catch((e) => setError(e.message));
    refreshModels().catch((e) => setError(e.message));
  }, []);

  const running = models.some((m) => m.download?.state === "running");

  // While a download runs, poll the cheap progress command; re-list (sizes) once it ends.
  useEffect(() => {
    if (!running) {
      if (wasRunning.current) refreshModels().catch((e) => setError(e.message));
      wasRunning.current = false;
      return undefined;
    }
    wasRunning.current = true;
    const id = setInterval(async () => {
      try {
        const progress = await api.modelProgress();
        setModels((rows) => rows.map((r) => ({ ...r, ...progress.find((p) => p.id === r.id) })));
      } catch (e) {
        setError(e.message);
      }
    }, 1000);
    return () => clearInterval(id);
  }, [running]);

  async function act(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await refreshModels();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const recommended = models.find((m) => m.id === RECOMMENDED);

  return (
    <div className="max-w-[880px]">
      <PageHeader title="Settings" />
      <ErrorNote>{error}</ErrorNote>

      {needsSetup(engines) && recommended && !running && (
        <section className="mb-10 rounded-tile bg-oat px-6 py-5 flex flex-wrap items-center gap-x-6 gap-y-3">
          <HardDrive className="size-5 text-accent-text shrink-0" strokeWidth={1.7} aria-hidden="true" />
          <div className="flex-1 min-w-[260px]">
            <p className="font-serif text-[22px]">Download a voice engine to get started.</p>
            <p className="text-meta text-ink-muted mt-1">
              We suggest the {recommended.label.toLowerCase()}: it reads without a narrator voice, so you can start
              right away. It's about {formatBytes(recommended.download_bytes)} and stays on this Mac.
            </p>
          </div>
          <Button onClick={() => act(() => api.downloadModel(RECOMMENDED))} disabled={busy}>
            <Download className="size-4" aria-hidden="true" /> Download the {recommended.label.toLowerCase()}
          </Button>
        </section>
      )}

      <section className="mb-12">
        <h2 className="rr-eyebrow mb-4">Listening</h2>
        {stats ? <ListeningCard stats={stats} engines={engines} /> : <p className="text-meta text-ink-muted">Counting…</p>}
      </section>

      <section>
        <h2 className="rr-eyebrow mb-1">Voice engines</h2>
        <p className="text-meta text-ink-muted mb-3">
          Downloaded once from Hugging Face and kept on this Mac. Other apps' models in the same folder are never touched.
        </p>
        <ul>
          {models.map((m) => (
            <ModelRow
              key={m.id}
              model={m}
              busy={busy || (running && m.download?.state !== "running")}
              onDownload={() => act(() => api.downloadModel(m.id))}
              onCancel={() => act(() => api.cancelModelDownload(m.id))}
              onDelete={() => setConfirm(m)}
            />
          ))}
        </ul>
      </section>

      {confirm && (
        <ConfirmDialog
          title={`Delete the ${confirm.label.toLowerCase()}?`}
          message={`This frees ${formatBytes(confirm.size)}. You can download it again any time.`}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            const m = confirm;
            setConfirm(null);
            act(() => api.deleteModel(m.id));
          }}
        />
      )}
    </div>
  );
}
```

Check `Button` accepts `variant`, `disabled`, `aria-label` and children with an icon (read `components/ui/Button.jsx:10-31`). If `border-t-accent-deco` isn't a defined Tailwind colour, use the `@theme` name for `--rr-accent-deco` from `index.css`.

- [ ] **Step 2: Route and nav**

`App.jsx`: `import SettingsPage from "./pages/SettingsPage.jsx";` and `<Route path="/settings" element={<SettingsPage />} />` after `/player`.

`AppShell.jsx`: add `Settings` to the lucide import and `{ to: "/settings", label: "Settings", icon: Settings }` as the last `navItems` entry. First-run redirect, once per launch:

```jsx
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { needsSetup } from "../lib/settings.js";
// inside AppShell():
  const navigate = useNavigate();
  useEffect(() => {
    // First run: no engine downloaded yet, so nothing can be rendered. Start on Settings.
    api.listEngines().then((r) => needsSetup(r.engines) && navigate("/settings")).catch(() => {});
  }, []);
```

- [ ] **Step 3: EnginePicker**

An engine with `ready === false` is disabled with a note linking to Settings:

```jsx
import { Link } from "react-router-dom";
// in the component:
  const missing = engines.filter((e) => e.ready === false);
  // per button: const unavailable = disabled || e.ready === false || (engine.needs_voice && !hasVoice);
  // after the existing "needs a narrator voice" note:
      {missing.length > 0 && !disabled && (
        <p className="text-meta text-ink-muted">
          {missing.map((e) => e.label).join(", ")} isn't downloaded yet.{" "}
          <Link to="/settings" className="font-bold text-accent-text hover:underline">Get it in Settings</Link>
        </p>
      )}
```

Keep `blocked` (needs-voice) from listing engines that are also missing: `const blocked = engines.filter((e) => e.ready !== false && e.needs_voice && !hasVoice);`.

- [ ] **Step 4: Build and test**

Run: `cd frontend && npm test && npm run build 2>&1 | tail -3`
Expected: tests pass; build `✓ built`.

- [ ] **Step 5: Look at it**

Run `make dev` (it opens the Tauri window). Check Settings in light and dark (ThemeToggle): the card, the three model rows with real sizes, nothing non-registry listed. Take a screenshot for the PR. Do not download or delete anything here; that is Task 11.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "Add the Settings page: listening card, voice engines, first-run setup"
```

---

### Task 11: Exit check and docs

**Files:**
- Modify: `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md` ("Notes from phase 5"), `README.md` (GUI section: Settings, `make models` now dev-only), `Makefile` (`models:` fetches through `model_store` so it matches the app)

- [ ] **Step 1: `make models` uses the same download path as the app**

The download exits on stdin EOF, so `make` must keep its stdin open: `</dev/null` would end it at once. A `sleep` pipe does that portably:

```make
TURBO_REPOS := [{"repo":"mlx-community/chatterbox-turbo-fp16","allow_patterns":["*.json","*.safetensors","*.py","*.model","*.tiktoken","*.txt","*.jinja","*.jsonl","*.yaml","*.npz","*.pth"],"required":[]},{"repo":"mlx-community/S3TokenizerV2","allow_patterns":["*.json","*.safetensors"],"required":[]}]

# Development shortcut; app users download from Settings. Same files the app fetches.
models:
	sleep 86400 | $(PY) -m audiobookify.model_store download '$(TURBO_REPOS)' | tail -1
```

Verify `make models` prints `{"done": true}` on this machine, which already has the files (it returns quickly).

- [ ] **Step 2: Fresh-cache exit check (needs the user at the keyboard; ~3.5 GB download)**

```bash
export HF_HOME="$(mktemp -d)"
make dev
```

1. The app opens on Settings with the setup panel.
2. Click "Download the speed engine": the bar moves, then the row reads "Downloaded".
3. Library → a book → render one chapter with the Speed engine: it finishes and plays.
4. Back in Settings: the Quality engine shows "Not downloaded", and its picker option in the book view shows "isn't downloaded yet · Get it in Settings".
5. Delete the speed engine: the dialog's size equals the row's size; afterwards `du -sh "$HF_HOME/hub"` dropped by about that much.
6. Quit, `rm -rf "$HF_HOME"`, `unset HF_HOME`.

Then, on the real cache: `.venv/bin/python -c "from huggingface_hub import scan_cache_dir as s; print(sorted(r.repo_id for r in s().repos))"` before and after opening Settings (no delete) — identical, and the non-registry repos (Gemma, Surya, F5-TTS, BERT, `openai/whisper-large-v3-turbo`) are untouched.

Record anything surprising (progress smoothness with the Xet backend, actual download sizes vs `download_bytes`) in the spec notes.

- [ ] **Step 3: Docs**

Spec, "Notes from phase 5" (append to the section Task 1 created):

```markdown
- Presence is read by Rust from the HF cache layout (`refs/main` → snapshot → each `required` file), so generate can be gated without starting Python. Sizes, delete and downloads run `python -m audiobookify.model_store` one-shots on `huggingface_hub`'s cache API.
- Turbo's registry entry includes `mlx-community/S3TokenizerV2`: mlx-audio fetches it at load, and offline a miss is only a logged warning.
- Deleting is refused while queued or running work needs the model (Whisper: while anything runs). When the studio is idle, the runner drops its engine worker first, so the weights leave memory too.
- Text clips gained a `duration` column; stats backfill missing chapter and clip durations from the files on first load.
- One download at a time. Progress is the repo's blob store size on disk, polled every 0.5 s.
```

README GUI section: a short paragraph that Settings shows listening stats and downloads/deletes the engines; first launch opens it when nothing is downloaded; `make models` is a development shortcut.

- [ ] **Step 4: Full suites**

```bash
.venv/bin/pytest -q
cd desktop && cargo test -p audiobookify-core 2>&1 | tail -3 && cargo build 2>&1 | tail -1
cd ../frontend && npm test 2>&1 | tail -3
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add Makefile README.md docs
git commit -m "Document phase 5 and route make models through model_store"
```

# Tauri Phase 3: Engine Worker + Rust Job Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Tauri app's job runner moves from the Python `audiobookify worker` to Rust. Rust runs chapter jobs, text clips and voice transcriptions; ML runs in one Python child, `engine_worker.py`, over JSON lines on stdio.

**Architecture:** `audiobookify-core` gains three modules: `audio` (trim/level/concat, WAV I/O, MP3 via ffmpeg + ID3), `engine` (`PythonEngine`, the stdio client) and `jobs` (chunk cache, `trim_point`, `Runner`). The desktop crate drops the `worker` sidecar and runs `Runner::run_forever` on a thread. FastAPI keeps enqueueing into the same SQLite DB.

**Tech Stack:** Rust 2021 (`rusqlite`, `serde`, `hound`, `id3`, `sha2`), Python 3.12 (`numpy`, `soundfile`, mlx-audio engines), ffmpeg (Homebrew).

**Spec:** `docs/superpowers/specs/2026-10-15-tauri-phase3-jobs-design.md`

## Global Constraints

- macOS on Apple Silicon only; personal Macs, repo `.venv` and Homebrew ffmpeg.
- The engine worker's stdout carries only protocol lines: one JSON object per request, `{"ok": {...}}` or `{"error": "..."}`. All logging goes to stderr.
- The engine worker exits on stdin EOF. The runner's child does not use the phase 0 `sh` wrapper.
- Never start a child with a closed fd 0: every `ffmpeg` call gets `stdin(Stdio::null())`.
- Paths stay repo-relative as today: `<repo>/audiobookify.db`, `<repo>/voices`, `<repo>/.cache/synth`, `<repo>/text_clips`, `<repo>/output`.
- Audio constants are identical to `src/audiobookify/postprocess/audio.py`. Pauses: word 0.1, clause 0.2, sentence 0.35, paragraph 0.75, scene 1.5 s.
- Never send a tiny chunk: chunking goes through phase 2's `chunk_text(text, max, min)` with the engine's own limits.
- TDD: each test is written and seen failing before the code it covers.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Deviations from the spec (decided while planning)

- **`phonemes` request dropped from phase 3.** Nothing calls wav2vec2 today (`api/app.py` never imports it), so it waits for the phase 4 command that needs it.
- **An error before synthesis also fails the chapter**, not just the job (Python left the chapter `pending`). The runner has one error path, and the chapter row shows the message.
- **Engine WAVs are float32** (`soundfile` subtype `FLOAT`), read losslessly by `hound`. Python's cache wrote 16-bit PCM.
- **Fake-engine test knobs are env vars on `FakeEngine`** (`AUDIOBOOKIFY_FAKE_LOG`, `_FAIL_ON`, `_CRASH_ON`, `_DELAY`, `_NEEDS_TRANSCRIPT`) plus `AUDIOBOOKIFY_FAKE_STT` in the worker. Rust tests drive the real child, so they can't subclass the engine the way the Python tests do.

## File Structure

| File | Responsibility |
|---|---|
| `src/audiobookify/engines/fake.py` (modify) | Env test knobs. |
| `src/audiobookify/engine_worker.py` (create) | The JSON-lines server: `Handler` + `serve` + `main`. |
| `tests/test_engine_worker.py` (create) | Protocol tests (subprocess) and fake-knob tests. |
| `desktop/core/Cargo.toml` (modify) | `serde`, `hound`, `id3`, `sha2`. |
| `desktop/core/src/audio.rs` (create) | Port of `postprocess/audio.py` (minus `relabel_chapter_mp3`), WAV I/O, `child_path`. |
| `desktop/core/tests/audio.rs` (create) | Ports of `test_postprocess_audio.py` + goldens. |
| `desktop/core/tests/fixtures/audio/generate.py` (create) | Writes `golden.json` from the Python audio code. |
| `desktop/core/tests/fixtures/audio/golden.json` (generated, committed) | Inputs and Python outputs. |
| `desktop/core/src/engine.rs` (create) | `PythonEngine`, protocol types. |
| `desktop/core/tests/engine.rs` (create) | `PythonEngine` against the real child in fake mode. |
| `desktop/core/src/jobs/mod.rs` (create) | Re-exports. |
| `desktop/core/src/jobs/cache.rs` (create) | `ChunkCache`, `compute_cache_key`. |
| `desktop/core/src/jobs/voice.rs` (create) | `trim_point`, `voice_for`. |
| `desktop/core/src/jobs/runner.rs` (create) | `Runner`, `Paths`, `JobError`. |
| `desktop/core/tests/chunk_cache.rs`, `tests/trim_point.rs`, `tests/runner.rs` (create) | Tests. |
| `desktop/core/src/lib.rs` (modify) | `pub mod audio; pub mod engine; pub mod jobs;` |
| `desktop/Cargo.toml`, `desktop/src/main.rs`, `desktop/src/sidecars.rs` (modify) | Depend on core, start the runner thread, drop the worker sidecar, use core's `child_path`. |

Test commands (run from `desktop/`):
- Rust: `cargo test -p audiobookify-core --test <name>`
- Python (repo root): `.venv/bin/pytest tests/test_engine_worker.py -v`

---

### Task 1: Python engine worker

**Files:**
- Modify: `src/audiobookify/engines/fake.py`
- Create: `src/audiobookify/engine_worker.py`
- Test: `tests/test_engine_worker.py`

**Interfaces:**
- Produces, used by Task 4 and Task 6:
  - The command `.venv/bin/python -m audiobookify.engine_worker`, run with cwd = repo root.
  - Requests, one JSON line each, with an `op` field:
    - `{"op":"load","engine":str}` → `{}`.
    - `{"op":"engine_info"}` → `{"name","max_chunk_chars","min_chunk_chars","sample_rate","needs_transcript"}`.
    - `{"op":"synthesize","text":str,"voice":{reference_path,reference_text,trim_end,tempo,exaggeration,cfg,temperature},"out_wav":str}` → `{"samples":int,"sample_rate":int}`. Every voice field may be null.
    - `{"op":"transcribe","path":str}` → `{"text":str,"words":[{"word","start","end"}]}`.
    - `{"op":"shutdown"}` → `{}`, then exit.
  - Engine names: `"qwen3"`, `"chatterbox-turbo"`, and any name starting with `"fake"` (a `FakeEngine` carrying that name).
  - Env knobs:
    - `AUDIOBOOKIFY_FAKE_LOG=<file>`: append `json.dumps(text)` + newline per synthesized chunk, written before synthesizing.
    - `AUDIOBOOKIFY_FAKE_FAIL_ON=<s>`: raise `RuntimeError("synthesis blew up")` if `s` is in `text.lower()`.
    - `AUDIOBOOKIFY_FAKE_CRASH_ON=<s>`: `os._exit(3)` on the same match.
    - `AUDIOBOOKIFY_FAKE_DELAY=<seconds>`: sleep per chunk.
    - `AUDIOBOOKIFY_FAKE_NEEDS_TRANSCRIPT=1`.
    - `AUDIOBOOKIFY_FAKE_STT=1` returns `FAKE_TRANSCRIPT`; `=error` raises `RuntimeError("no speech found")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_engine_worker.py`:

```python
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

from audiobookify.engine_worker import FAKE_TRANSCRIPT
from audiobookify.engines.fake import FakeEngine

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _run(requests, env=None, code=None):
    """Feed request lines to a fresh worker; returns (process, parsed response lines)."""
    lines = "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in requests)
    args = [sys.executable, "-c", code] if code else [sys.executable, "-m", "audiobookify.engine_worker"]
    proc = subprocess.run(args, input=lines, capture_output=True, text=True, timeout=120,
                          env={**os.environ, **(env or {})})
    return proc, [json.loads(line) for line in proc.stdout.splitlines()]


def test_load_and_engine_info_for_the_fake_engine_then_exit_on_eof():
    proc, out = _run([{"op": "load", "engine": "fake"}, {"op": "engine_info"}])
    assert proc.returncode == 0
    assert out == [{"ok": {}}, {"ok": {"name": "fake", "max_chunk_chars": 300, "min_chunk_chars": 20,
                                       "sample_rate": 24000, "needs_transcript": False}}]


def test_a_fake_prefixed_name_is_a_fake_engine_with_that_name():
    _, out = _run([{"op": "load", "engine": "fake-b"}, {"op": "engine_info"}])
    assert out[1]["ok"]["name"] == "fake-b"


def test_synthesize_writes_a_float_wav_and_reports_its_length(tmp_path):
    wav = tmp_path / "out.wav"
    _, out = _run([{"op": "load", "engine": "fake"},
                   {"op": "synthesize", "text": "Hello there.", "voice": {}, "out_wav": str(wav)}])
    data, sr = sf.read(wav, dtype="float32")
    assert sf.info(wav).subtype == "FLOAT"
    assert out[1] == {"ok": {"samples": len(data), "sample_rate": 24000}} and sr == 24000


def test_errors_get_an_error_line_and_the_worker_keeps_going(tmp_path):
    _, out = _run(["not json", {"op": "nope"},
                   {"op": "synthesize", "text": "x", "voice": {}, "out_wav": str(tmp_path / "a.wav")},
                   {"op": "load", "engine": "fake"}])
    assert [("error" in r) for r in out] == [True, True, True, False]
    assert out[2]["error"] == "no engine loaded"


def test_stdout_carries_only_protocol_lines_even_when_an_engine_prints(tmp_path):
    code = ("import audiobookify.engines.fake as f, audiobookify.engine_worker as w\n"
            "orig = f.FakeEngine.synthesize\n"
            "def noisy(self, *a):\n    print('noise from the model')\n    return orig(self, *a)\n"
            "f.FakeEngine.synthesize = noisy\nw.main()\n")
    proc, out = _run([{"op": "load", "engine": "fake"},
                      {"op": "synthesize", "text": "Hi.", "voice": {}, "out_wav": str(tmp_path / "a.wav")}],
                     code=code)
    assert len(out) == 2 and "ok" in out[1]  # every stdout line parsed as JSON
    assert "noise from the model" in proc.stderr


def test_shutdown_replies_and_exits_without_reading_further():
    proc, out = _run([{"op": "shutdown"}, {"op": "load", "engine": "fake"}])
    assert proc.returncode == 0 and out == [{"ok": {}}]


def test_transcribe_uses_the_fake_transcript_when_asked(tmp_path):
    _, out = _run([{"op": "transcribe", "path": str(tmp_path / "x.wav")}], env={"AUDIOBOOKIFY_FAKE_STT": "1"})
    assert out == [{"ok": FAKE_TRANSCRIPT}]
    _, out = _run([{"op": "transcribe", "path": str(tmp_path / "x.wav")}], env={"AUDIOBOOKIFY_FAKE_STT": "error"})
    assert out == [{"error": "no speech found"}]


@requires_ffmpeg
def test_synthesize_clones_from_the_trimmed_clip_at_the_voice_tempo(tmp_path):
    ref = tmp_path / "ref.wav"
    sf.write(ref, np.zeros(24000 * 20, dtype=np.float32), 24000)
    voice = {"reference_path": str(ref), "reference_text": "One two.", "trim_end": 14.15, "tempo": 0.9}
    _, out = _run([{"op": "load", "engine": "fake"},
                   {"op": "synthesize", "text": "Hello.", "voice": voice, "out_wav": str(tmp_path / "a.wav")}])
    assert "ok" in out[1]
    assert (tmp_path / "ref.trim-14.15s.wav").is_file()
    assert (tmp_path / "ref.trim-14.15s.tempo-0.90.wav").is_file()


def test_fake_engine_knobs(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("AUDIOBOOKIFY_FAKE_LOG", str(log))
    monkeypatch.setenv("AUDIOBOOKIFY_FAKE_FAIL_ON", "boom")
    monkeypatch.setenv("AUDIOBOOKIFY_FAKE_NEEDS_TRANSCRIPT", "1")
    engine = FakeEngine()
    engine.synthesize("Fine.", None, {})
    with pytest.raises(RuntimeError, match="synthesis blew up"):
        engine.synthesize("Then BOOM.", None, {})
    assert [json.loads(line) for line in log.read_text().splitlines()] == ["Fine.", "Then BOOM."]
    assert engine.needs_transcript is True
    assert FakeEngine.needs_transcript is False  # the class default is untouched
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_engine_worker.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'audiobookify.engine_worker'`.

- [ ] **Step 3: Add the knobs to `FakeEngine`**

Replace `src/audiobookify/engines/fake.py` with:

```python
import json
import os
import time

import numpy as np

from .base import VoiceProfileHandle


class FakeEngine:
    """Silence, no weights. The AUDIOBOOKIFY_FAKE_* env vars are test knobs for the
    Rust runner tests, which drive this engine through a real engine_worker child."""

    name = "fake"
    supports_cloning = True
    needs_transcript = False

    def __init__(self):
        self._sr = 24000
        self.device = None
        if os.environ.get("AUDIOBOOKIFY_FAKE_NEEDS_TRANSCRIPT") == "1":
            self.needs_transcript = True

    def load(self, device: str = "auto") -> None:
        self.device = device

    def clone_profile(self, reference_audio, reference_text, params) -> VoiceProfileHandle:
        return VoiceProfileHandle(data={"reference_audio": reference_audio})

    def synthesize(self, text_chunk: str, profile_handle, params) -> np.ndarray:
        log = os.environ.get("AUDIOBOOKIFY_FAKE_LOG")
        if log:
            with open(log, "a") as f:
                f.write(json.dumps(text_chunk) + "\n")
        time.sleep(float(os.environ.get("AUDIOBOOKIFY_FAKE_DELAY", "0")))
        crash_on = os.environ.get("AUDIOBOOKIFY_FAKE_CRASH_ON")
        if crash_on and crash_on in text_chunk.lower():
            os._exit(3)
        fail_on = os.environ.get("AUDIOBOOKIFY_FAKE_FAIL_ON")
        if fail_on and fail_on in text_chunk.lower():
            raise RuntimeError("synthesis blew up")
        duration_s = max(0.1, len(text_chunk) * 0.05)
        return np.zeros(int(self._sr * duration_s), dtype=np.float32)

    def max_chunk_chars(self) -> int:
        return 300

    def min_chunk_chars(self) -> int:
        return 20

    def preferred_phoneme_scheme(self):
        return None

    def sample_rate(self) -> int:
        return self._sr

    def teardown(self) -> None:
        pass
```

- [ ] **Step 4: Write `engine_worker.py`**

Create `src/audiobookify/engine_worker.py`:

```python
"""The desktop app's ML process: one TTS engine behind JSON lines on stdin/stdout.

One request per line ({"op": ..., ...}), exactly one response line each:
{"ok": {...}} or {"error": "..."}. Exits on stdin EOF, so it dies with the app.
No DB access and no pipeline logic; the Rust runner owns those.
"""
import json
import os
import sys

import numpy as np
import soundfile as sf

from .engines import ENGINES
from .engines.fake import FakeEngine
from .voice import analysis
from .worker import EngineSlot

# What AUDIOBOOKIFY_FAKE_STT=1 returns: 18 s of words, so trim_point cuts at "two." (14.15 s).
FAKE_TRANSCRIPT = {"text": "One two. Three", "words": [
    {"word": " One", "start": 0.0, "end": 10.0},
    {"word": " two.", "start": 10.1, "end": 14.0},
    {"word": " Three", "start": 14.2, "end": 18.0},
]}


def _make(name: str):
    if name.startswith("fake"):
        engine = FakeEngine()
        engine.name = name  # ponytail: "fake-b" lets tests switch engines without a second fake class
        return engine
    return ENGINES[name].cls()


def _params(voice: dict) -> dict:
    """Engine params from the protocol's voice fields (worker.params_from_profile's shape)."""
    ref = voice.get("reference_path")
    tempo = voice.get("tempo") or 1.0
    return {
        "reference_audio": analysis.reference_clip(ref, voice.get("trim_end"), tempo) if ref else None,
        "reference_text": voice.get("reference_text"),
        "reference_trim_end": voice.get("trim_end"),
        "exaggeration": voice.get("exaggeration"),
        "cfg": voice.get("cfg"),
        "temperature": voice.get("temperature"),
        "tempo": tempo,
    }


class Handler:
    def __init__(self, slot: EngineSlot = None):
        self.slot = slot or EngineSlot(_make)

    def _engine(self):
        if self.slot.engine is None:
            raise RuntimeError("no engine loaded")
        return self.slot.engine

    def load(self, engine: str) -> dict:
        self.slot.get(engine)
        return {}

    def engine_info(self) -> dict:
        e = self._engine()
        return {"name": e.name, "max_chunk_chars": e.max_chunk_chars(), "min_chunk_chars": e.min_chunk_chars(),
                "sample_rate": e.sample_rate(), "needs_transcript": e.needs_transcript}

    def synthesize(self, text: str, voice: dict, out_wav: str) -> dict:
        e = self._engine()
        params = _params(voice)
        # Keyed on the clip and transcript, so an edited transcript, trim or tempo takes effect.
        key = (params["reference_audio"], params["reference_text"])
        cache = self.slot.profile_cache
        if cache.get("key") != key:
            cache["key"] = key
            cache["handle"] = e.clone_profile(params["reference_audio"], params["reference_text"], params)
        audio = np.asarray(e.synthesize(text, cache["handle"], params), dtype=np.float32).reshape(-1)
        sf.write(out_wav, audio, e.sample_rate(), subtype="FLOAT")
        return {"samples": len(audio), "sample_rate": e.sample_rate()}

    def transcribe(self, path: str) -> dict:
        fake = os.environ.get("AUDIOBOOKIFY_FAKE_STT")
        if fake == "error":
            raise RuntimeError("no speech found")
        if fake:
            return FAKE_TRANSCRIPT
        return analysis.transcribe(path)


OPS = {"load", "engine_info", "synthesize", "transcribe"}


def _reply(out, message: dict) -> None:
    out.write(json.dumps(message) + "\n")
    out.flush()


def serve(lines, out, handler: Handler) -> None:
    for line in lines:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            op = request.pop("op")
            if op == "shutdown":
                _reply(out, {"ok": {}})
                return
            if op not in OPS:
                raise ValueError(f"unknown op {op!r}")
            _reply(out, {"ok": getattr(handler, op)(**request)})
        except Exception as exc:
            _reply(out, {"error": str(exc) or type(exc).__name__})


def main() -> None:
    # Keep the real stdout for the protocol and point fd 1 at stderr, so a library
    # print (Python or C level) can never corrupt a response line.
    protocol = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    serve(sys.stdin, protocol, Handler())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_engine_worker.py tests/test_engines_fake.py tests/test_worker.py -v`
Expected: all PASS. The existing fake and worker tests must still pass with the new `FakeEngine`.

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/engines/fake.py src/audiobookify/engine_worker.py tests/test_engine_worker.py
git commit -m "Add engine_worker: one engine behind JSON lines on stdio

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Rust audio DSP with Python goldens

**Files:**
- Modify: `desktop/core/Cargo.toml`, `desktop/core/src/lib.rs`
- Create: `desktop/core/src/audio.rs`
- Create: `desktop/core/tests/fixtures/audio/generate.py`, `desktop/core/tests/fixtures/audio/golden.json` (generated)
- Test: `desktop/core/tests/audio.rs`

**Interfaces:**
- Consumes: `audiobookify_core::text::EndsWith` (phase 2), with variants `Word, Clause, Sentence, Paragraph, Scene`.
- Produces, used by Task 3 and Task 7:
  - `pub fn pause_seconds(e: EndsWith) -> f64`
  - `pub fn trim_silence(audio: &[f32], sample_rate: u32) -> Vec<f32>`
  - `pub fn level_chunks(chunks: Vec<Vec<f32>>) -> Vec<Vec<f32>>`
  - `pub fn concatenate_chunks(chunks: Vec<Vec<f32>>, ends_with: &[EndsWith], sample_rate: u32) -> Vec<f32>`
  - `pub fn write_wav(path: &Path, audio: &[f32], sample_rate: u32) -> io::Result<()>` (mono float32)
  - `pub fn read_wav(path: &Path) -> io::Result<(Vec<f32>, u32)>` (mono float32 or 16-bit int)

- [ ] **Step 1: Add dependencies and the module**

In `desktop/core/Cargo.toml` `[dependencies]` add:

```toml
hound = "3.5"
id3 = "1.17"
serde = { version = "1", features = ["derive"] }
sha2 = "0.11"
```

In `desktop/core/src/lib.rs` add `pub mod audio;` (keep the list alphabetical):

```rust
pub mod audio;
pub mod db;
pub mod ingest;
pub mod models;
pub mod pystr;
pub mod text;
pub mod voice_storage;
```

Create `desktop/core/src/audio.rs` with only `//! Audio post-processing, ported from src/audiobookify/postprocess/audio.py.` so the crate builds.

- [ ] **Step 2: Write the golden generator and generate**

Create `desktop/core/tests/fixtures/audio/generate.py`:

```python
"""Writes golden.json: inputs and Python's trim/level/concatenate outputs.
Run from the repo root: .venv/bin/python desktop/core/tests/fixtures/audio/generate.py"""
import json
from pathlib import Path

import numpy as np

from audiobookify.postprocess.audio import concatenate_chunks, level_chunks, trim_silence

SR = 1000


def speech(n, amp=0.5):
    return (amp * np.sin(np.arange(n) * 0.3)).astype(np.float32)


def padded(n_speech, n_pad, amp=0.5):
    pad = np.zeros(n_pad, dtype=np.float32)
    return np.concatenate([pad, speech(n_speech, amp), pad])


def spiky():
    s = speech(500, 0.05)
    s[250] = 0.95
    return s


def ramp_up():  # a fade-in that crosses the -40 dB threshold mid-chunk
    return (np.linspace(0, 1, 800, dtype=np.float32) ** 3 * np.sin(np.arange(800) * 0.2)).astype(np.float32)


trim_cases = [padded(500, 400), speech(500), np.zeros(300, dtype=np.float32), ramp_up(), speech(5, 0.3)]
level_cases = [
    [speech(500), speech(500), speech(500, 0.05), speech(500)],
    [speech(500, 0.5), speech(500, 0.4)],
    [speech(500), speech(500), spiky()],
    [np.zeros(100, dtype=np.float32), speech(300, 0.1), speech(300, 0.6)],
]
concat_cases = [
    ([padded(200, 300), padded(200, 300, 0.1), speech(200)], ["scene", "paragraph", "sentence"]),
    ([speech(200), speech(200), speech(200)], ["word", "clause", "paragraph"]),
]

out = {
    "sample_rate": SR,
    "trim": [{"input": c.tolist(), "output": trim_silence(c, SR).tolist()} for c in trim_cases],
    "level": [{"input": [c.tolist() for c in cs], "output": [o.tolist() for o in level_chunks(cs)]}
              for cs in level_cases],
    "concat": [{"input": [c.tolist() for c in cs], "ends_with": ends,
                "output": concatenate_chunks(cs, ends, SR).tolist()} for cs, ends in concat_cases],
}
Path(__file__).with_name("golden.json").write_text(json.dumps(out))
```

Run (repo root): `.venv/bin/python desktop/core/tests/fixtures/audio/generate.py`
Expected: `desktop/core/tests/fixtures/audio/golden.json` exists (well under 1 MB).

- [ ] **Step 3: Write the failing tests**

Create `desktop/core/tests/audio.rs`:

```rust
use std::path::Path;

use audiobookify_core::audio::*;
use audiobookify_core::text::EndsWith;
use serde_json::Value;
use tempfile::TempDir;

const SR: u32 = 1000;

fn speech(n: usize, amp: f32) -> Vec<f32> {
    (0..n).map(|i| amp * (i as f32 * 0.3).sin()).collect()
}

fn padded(n_speech: usize, n_pad: usize, amp: f32) -> Vec<f32> {
    let mut v = vec![0.0; n_pad];
    v.extend(speech(n_speech, amp));
    v.extend(vec![0.0; n_pad]);
    v
}

fn peak(a: &[f32]) -> f32 {
    a.iter().fold(0.0, |m, x| m.max(x.abs()))
}

fn ends(names: &[Value]) -> Vec<EndsWith> {
    names.iter().map(|n| match n.as_str().unwrap() {
        "word" => EndsWith::Word, "clause" => EndsWith::Clause, "sentence" => EndsWith::Sentence,
        "paragraph" => EndsWith::Paragraph, "scene" => EndsWith::Scene, other => panic!("{other}"),
    }).collect()
}

fn floats(v: &Value) -> Vec<f32> {
    v.as_array().unwrap().iter().map(|x| x.as_f64().unwrap() as f32).collect()
}

fn chunks(v: &Value) -> Vec<Vec<f32>> {
    v.as_array().unwrap().iter().map(floats).collect()
}

fn assert_close(got: &[f32], want: &[f32], what: &str) {
    assert_eq!(got.len(), want.len(), "{what}: length");
    for (i, (g, w)) in got.iter().zip(want).enumerate() {
        assert!((g - w).abs() <= 1e-6, "{what}: sample {i}: {g} vs {w}");
    }
}

fn golden() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/audio/golden.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn trim_silence_matches_python() {
    for (i, case) in golden()["trim"].as_array().unwrap().iter().enumerate() {
        assert_close(&trim_silence(&floats(&case["input"]), SR), &floats(&case["output"]), &format!("trim {i}"));
    }
}

#[test]
fn level_chunks_matches_python() {
    for (i, case) in golden()["level"].as_array().unwrap().iter().enumerate() {
        let got = level_chunks(chunks(&case["input"]));
        for (j, (g, w)) in got.iter().zip(chunks(&case["output"])).enumerate() {
            assert_close(g, &w, &format!("level {i}.{j}"));
        }
    }
}

#[test]
fn concatenate_chunks_matches_python() {
    for (i, case) in golden()["concat"].as_array().unwrap().iter().enumerate() {
        let got = concatenate_chunks(chunks(&case["input"]), &ends(case["ends_with"].as_array().unwrap()), SR);
        assert_close(&got, &floats(&case["output"]), &format!("concat {i}"));
    }
}

#[test]
fn trim_silence_removes_padding_but_keeps_speech_and_a_small_margin() {
    let trimmed = trim_silence(&padded(500, 400, 0.5), SR);
    let margin = (SR as f64 * 0.03) as usize;
    assert!((500..=500 + 2 * margin + 2 * 10).contains(&trimmed.len()), "{}", trimmed.len());
}

#[test]
fn trim_silence_fades_edges_to_zero() {
    let trimmed = trim_silence(&speech(500, 0.5), SR);
    assert_eq!((trimmed[0], *trimmed.last().unwrap()), (0.0, 0.0));
}

#[test]
fn trim_silence_leaves_an_all_silent_chunk_alone() {
    assert_eq!(trim_silence(&[0.0; 300], SR), vec![0.0; 300]);
}

#[test]
fn level_chunks_nudges_an_outlier_toward_the_median_capped_at_6_db() {
    let quiet = speech(500, 0.05);
    let out = level_chunks(vec![speech(500, 0.5), speech(500, 0.5), quiet.clone(), speech(500, 0.5)]);
    let gain_db = 20.0 * (peak(&out[2]) as f64 / peak(&quiet) as f64).log10();
    assert!((gain_db - 6.0).abs() < 0.01, "{gain_db}");
    assert_eq!(out[0], speech(500, 0.5));
}

#[test]
fn level_chunks_never_clips() {
    let mut spiky = speech(500, 0.05);
    spiky[250] = 0.95;
    let out = level_chunks(vec![speech(500, 0.5), speech(500, 0.5), spiky]);
    assert!(peak(&out[2]) <= 0.99);
}

#[test]
fn concatenate_chunks_pauses_by_boundary_type() {
    let out = concatenate_chunks(vec![speech(200, 0.5); 3],
                                 &[EndsWith::Sentence, EndsWith::Paragraph, EndsWith::Paragraph], SR);
    assert_eq!(out.len(), 600 + 350 + 750);
}

#[test]
fn concatenate_chunks_handles_empty_input() {
    assert!(concatenate_chunks(vec![], &[], SR).is_empty());
}

#[test]
fn pause_table_matches_python() {
    let got: Vec<f64> = [EndsWith::Word, EndsWith::Clause, EndsWith::Sentence, EndsWith::Paragraph, EndsWith::Scene]
        .into_iter().map(pause_seconds).collect();
    assert_eq!(got, vec![0.1, 0.2, 0.35, 0.75, 1.5]);
}

#[test]
fn wav_round_trips_float32_exactly() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("a.wav");
    let audio = speech(1000, 0.7);
    write_wav(&path, &audio, 24000).unwrap();
    assert_eq!(read_wav(&path).unwrap(), (audio, 24000));
}

#[test]
fn read_wav_reads_16_bit_pcm_as_floats() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("a.wav");
    let spec = hound::WavSpec { channels: 1, sample_rate: 16000, bits_per_sample: 16,
                                sample_format: hound::SampleFormat::Int };
    let mut w = hound::WavWriter::create(&path, spec).unwrap();
    for s in [0i16, 16384, -32768] {
        w.write_sample(s).unwrap();
    }
    w.finalize().unwrap();
    assert_eq!(read_wav(&path).unwrap(), (vec![0.0, 0.5, -1.0], 16000));
}
```

`hound` is a normal dependency, so the test can use it directly.

- [ ] **Step 4: Run the tests to verify they fail**

Run (in `desktop/`): `cargo test -p audiobookify-core --test audio`
Expected: compile errors, `cannot find function trim_silence` and so on.

- [ ] **Step 5: Implement**

Write `desktop/core/src/audio.rs`:

```rust
//! Audio post-processing, ported from src/audiobookify/postprocess/audio.py.

use std::io;
use std::path::Path;

use crate::text::EndsWith;

/// Silence inserted after a chunk, by why the chunk ended (see text::Chunk).
pub fn pause_seconds(ends_with: EndsWith) -> f64 {
    match ends_with {
        EndsWith::Word => 0.1,
        EndsWith::Clause => 0.2,
        EndsWith::Sentence => 0.35,
        EndsWith::Paragraph => 0.75,
        EndsWith::Scene => 1.5,
    }
}

const FRAME_SECONDS: f64 = 0.01;
const SILENCE_BELOW_PEAK_DB: f64 = 40.0;
const KEEP_MARGIN_SECONDS: f64 = 0.03; // keeps breaths and soft consonant onsets
const FADE_SECONDS: f64 = 0.01;
const LEVEL_TOLERANCE_DB: f64 = 3.0;
const MAX_LEVEL_GAIN_DB: f64 = 6.0;
const PEAK_CEILING: f64 = 0.99;

fn peak(audio: &[f32]) -> f32 {
    audio.iter().fold(0.0, |m, x| m.max(x.abs()))
}

/// RMS in f64, as numpy computes it for these float32 arrays.
fn rms(audio: &[f32]) -> f64 {
    (audio.iter().map(|&x| (x as f64) * (x as f64)).sum::<f64>() / audio.len() as f64).sqrt()
}

/// Cut the model's own leading/trailing silence (so pauses come only from
/// pause_seconds) and fade the edges so joins don't click.
pub fn trim_silence(audio: &[f32], sample_rate: u32) -> Vec<f32> {
    let peak = peak(audio) as f64;
    let frame = ((sample_rate as f64 * FRAME_SECONDS) as usize).max(1);
    let n_frames = audio.len() / frame;
    if peak == 0.0 || n_frames == 0 {
        return audio.to_vec();
    }
    let threshold = peak * 10f64.powf(-SILENCE_BELOW_PEAK_DB / 20.0);
    let mut loud = (0..n_frames).filter(|&i| rms(&audio[i * frame..(i + 1) * frame]) > threshold);
    let Some(first) = loud.next() else {
        return audio.to_vec();
    };
    let last = loud.last().unwrap_or(first);
    let margin = (sample_rate as f64 * KEEP_MARGIN_SECONDS) as usize;
    let start = (first * frame).saturating_sub(margin);
    let end = ((last + 1) * frame + margin).min(audio.len());
    let mut out = audio[start..end].to_vec();
    let fade = ((sample_rate as f64 * FADE_SECONDS) as usize).min(out.len() / 2);
    let n = out.len();
    for i in 0..fade {
        // np.linspace(0.0, 1.0, fade, dtype=float32): i * step in f64, last value exactly 1.
        let r = if i + 1 == fade { 1.0 } else { (i as f64 * (1.0 / (fade - 1) as f64)) as f32 };
        out[i] *= r;
        out[n - 1 - i] *= r;
    }
    out
}

fn level_db(audio: &[f32]) -> Option<f64> {
    if audio.is_empty() {
        return None;
    }
    let r = rms(audio);
    (r > 0.0).then(|| 20.0 * r.log10())
}

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let mid = values.len() / 2;
    if values.len() % 2 == 1 { values[mid] } else { (values[mid - 1] + values[mid]) / 2.0 }
}

/// Nudge chunks more than LEVEL_TOLERANCE_DB off the median level toward it
/// (capped, never clipping). The overall level is left as the model made it.
pub fn level_chunks(chunks: Vec<Vec<f32>>) -> Vec<Vec<f32>> {
    let levels: Vec<Option<f64>> = chunks.iter().map(|c| level_db(c)).collect();
    let mut known: Vec<f64> = levels.iter().flatten().copied().collect();
    if known.is_empty() {
        return chunks;
    }
    let median = median(&mut known);
    chunks.into_iter().zip(levels).map(|(chunk, level)| match level {
        Some(level) if (median - level).abs() > LEVEL_TOLERANCE_DB => {
            let gain = 10f64.powf((median - level).clamp(-MAX_LEVEL_GAIN_DB, MAX_LEVEL_GAIN_DB) / 20.0)
                .min(PEAK_CEILING / peak(&chunk) as f64);
            // numpy 2 multiplies a float32 array by a Python float in float32.
            let gain = gain as f32;
            chunk.iter().map(|&x| x * gain).collect()
        }
        _ => chunk,
    }).collect()
}

pub fn concatenate_chunks(chunks: Vec<Vec<f32>>, ends_with: &[EndsWith], sample_rate: u32) -> Vec<f32> {
    let chunks = level_chunks(chunks.iter().map(|c| trim_silence(c, sample_rate)).collect());
    let last = chunks.len().saturating_sub(1);
    let mut out = Vec::new();
    for (i, chunk) in chunks.into_iter().enumerate() {
        out.extend(chunk);
        if i < last {
            let pause = (sample_rate as f64 * pause_seconds(ends_with[i])) as usize;
            out.resize(out.len() + pause, 0.0);
        }
    }
    out
}

/// Mono float32 WAV.
pub fn write_wav(path: &Path, audio: &[f32], sample_rate: u32) -> io::Result<()> {
    let spec = hound::WavSpec { channels: 1, sample_rate, bits_per_sample: 32,
                                sample_format: hound::SampleFormat::Float };
    let mut w = hound::WavWriter::create(path, spec).map_err(io::Error::other)?;
    for &s in audio {
        w.write_sample(s).map_err(io::Error::other)?;
    }
    w.finalize().map_err(io::Error::other)
}

/// A mono WAV (float32 or 16-bit int) as floats in [-1, 1].
pub fn read_wav(path: &Path) -> io::Result<(Vec<f32>, u32)> {
    let mut r = hound::WavReader::open(path).map_err(io::Error::other)?;
    let spec = r.spec();
    if spec.channels != 1 {
        return Err(io::Error::other(format!("{}: expected mono, got {} channels", path.display(), spec.channels)));
    }
    let samples = match (spec.sample_format, spec.bits_per_sample) {
        (hound::SampleFormat::Float, 32) => r.samples::<f32>().collect::<Result<Vec<_>, _>>(),
        (hound::SampleFormat::Int, 16) => r.samples::<i16>().map(|s| s.map(|s| s as f32 / 32768.0)).collect(),
        (format, bits) => return Err(io::Error::other(format!("{}: unsupported WAV {format:?} {bits}-bit", path.display()))),
    };
    Ok((samples.map_err(io::Error::other)?, spec.sample_rate))
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cargo test -p audiobookify-core --test audio`
Expected: all PASS. If a golden case is off by more than 1e-6, find the operation whose order or precision differs from numpy. Don't loosen the tolerance.

- [ ] **Step 7: Commit**

```bash
git add desktop/core/Cargo.toml desktop/Cargo.lock desktop/core/src/lib.rs desktop/core/src/audio.rs desktop/core/tests/audio.rs desktop/core/tests/fixtures/audio
git commit -m "Port audio trim, leveling and concatenation to Rust with Python goldens

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(If `Cargo.lock` lives somewhere other than `desktop/`, add it from there.)

---

### Task 3: MP3 export and the Homebrew tool path

**Files:**
- Modify: `desktop/core/src/audio.rs`
- Test: `desktop/core/tests/audio.rs` (append)

**Interfaces:**
- Consumes: `write_wav` (Task 2), `voice_storage::sanitize_filename` (phase 1, the same character set as `postprocess/audio.py`).
- Produces, used by Task 7, Task 8 and Task 9:
  - `pub fn child_path(existing: Option<OsString>) -> OsString`: Homebrew dirs, then the existing PATH.
  - `pub fn chapter_filename(order_index: i64, title: &str) -> String`
  - `#[derive(Default)] pub struct Tags<'a> { pub title: Option<&'a str>, pub album: Option<&'a str>, pub track: Option<u32>, pub artist: Option<&'a str> }`
  - `pub enum AudioError { Io(io::Error), Ffmpeg(String), Tag(String) }` (`thiserror`)
  - `pub fn export_mp3(audio: &[f32], sample_rate: u32, output_path: &Path, tags: &Tags, fallback_dir: Option<&Path>) -> Result<(PathBuf, bool), AudioError>`. The bool is `used_fallback`.
  - `pub fn export_chapter_mp3(audio: &[f32], sample_rate: u32, output_dir: &Path, order_index: i64, chapter_title: &str, book_title: &str, narrator: &str, fallback_dir: Option<&Path>) -> Result<(PathBuf, bool), AudioError>`

- [ ] **Step 1: Write the failing tests**

Append to `desktop/core/tests/audio.rs`:

```rust
use std::ffi::OsString;
use std::os::unix::fs::PermissionsExt;

fn tone(sample_rate: u32) -> Vec<f32> {
    (0..sample_rate).map(|i| (i as f32 / sample_rate as f32 * 440.0 * std::f32::consts::TAU).sin()).collect()
}

/// A dir the test can't write into; restored on drop so TempDir cleanup works.
struct Locked(std::path::PathBuf);
impl Locked {
    fn new(dir: &Path) -> Self {
        let p = dir.join("locked");
        std::fs::create_dir(&p).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o500)).unwrap();
        Locked(p)
    }
}
impl Drop for Locked {
    fn drop(&mut self) {
        let _ = std::fs::set_permissions(&self.0, std::fs::Permissions::from_mode(0o700));
    }
}

#[test]
fn child_path_puts_homebrew_first_and_handles_missing_and_empty() {
    assert_eq!(child_path(None), OsString::from("/opt/homebrew/bin:/usr/local/bin"));
    assert_eq!(child_path(Some(OsString::new())), OsString::from("/opt/homebrew/bin:/usr/local/bin"));
    assert_eq!(child_path(Some("/usr/bin:/bin".into())), OsString::from("/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"));
}

#[test]
fn chapter_filename_zero_pads_and_strips_illegal_characters() {
    assert_eq!(chapter_filename(0, "Chapter One"), "01 - Chapter One.mp3");
    assert_eq!(chapter_filename(9, r#"Chapter Ten: "The End"?"#), "10 - Chapter Ten The End.mp3");
}

#[test]
fn export_chapter_mp3_writes_a_tagged_file() {
    let dir = TempDir::new().unwrap();
    let (path, used_fallback) = export_chapter_mp3(&tone(22050), 22050, dir.path(), 0, "Chapter One", "My Book",
                                                    "Chatterbox Default", None).unwrap();
    assert!(!used_fallback);
    assert_eq!(path, dir.path().join("01 - Chapter One.mp3"));
    let tag = id3::Tag::read_from_path(&path).unwrap();
    use id3::TagLike;
    assert_eq!((tag.title(), tag.album(), tag.track(), tag.artist()),
               (Some("Chapter One"), Some("My Book"), Some(1), Some("Chatterbox Default")));
    assert!(!dir.path().join("01 - Chapter One.tmp.wav").exists(), "temp WAV left behind");
}

#[test]
fn export_chapter_mp3_falls_back_when_the_output_dir_is_unwritable() {
    let dir = TempDir::new().unwrap();
    let locked = Locked::new(dir.path());
    let fallback = dir.path().join("fallback");
    let (path, used_fallback) = export_chapter_mp3(&tone(22050), 22050, &locked.0, 0, "Chapter One", "My Book",
                                                    "Narrator", Some(&fallback)).unwrap();
    assert!(used_fallback);
    assert_eq!(path, fallback.join("01 - Chapter One.mp3"));
    assert!(path.is_file());
}

#[test]
fn export_chapter_mp3_errors_when_unwritable_and_no_fallback_given() {
    let dir = TempDir::new().unwrap();
    let locked = Locked::new(dir.path());
    let err = export_chapter_mp3(&tone(22050), 22050, &locked.0, 0, "Chapter One", "My Book", "N", None).unwrap_err();
    assert!(matches!(err, AudioError::Io(_)), "{err:?}");
}

#[test]
fn export_mp3_falls_back_to_the_same_file_name() {
    let dir = TempDir::new().unwrap();
    let locked = Locked::new(dir.path());
    let fallback = dir.path().join("fallback");
    let tags = Tags { title: Some("Text clip #1"), ..Default::default() };
    let (path, used_fallback) = export_mp3(&tone(22050), 22050, &locked.0.join("clip.mp3"), &tags, Some(&fallback)).unwrap();
    assert!(used_fallback);
    assert_eq!(path, fallback.join("clip.mp3"));
    assert!(path.is_file());
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cargo test -p audiobookify-core --test audio`
Expected: compile errors for `child_path`, `chapter_filename`, `export_chapter_mp3`, `Tags`, `AudioError`, `export_mp3`.

- [ ] **Step 3: Implement**

Append to `desktop/core/src/audio.rs` and merge these imports into the existing `use` lines at the top:

```rust
use std::ffi::OsString;
use std::fs;
use std::path::PathBuf;
use std::process::{Command, Stdio};

use crate::voice_storage::sanitize_filename;
```

```rust
/// Homebrew dirs first, then the existing PATH (only when present and non-empty, so no trailing `:`).
/// ponytail: hardcoded for this personal-Macs-only build; a Finder-launched .app only has
/// /usr/bin:/bin:/usr/sbin:/sbin. A shareable build would bundle ffmpeg.
pub fn child_path(existing: Option<OsString>) -> OsString {
    let mut p = OsString::from("/opt/homebrew/bin:/usr/local/bin");
    if let Some(e) = existing.filter(|e| !e.is_empty()) {
        p.push(":");
        p.push(e);
    }
    p
}

pub fn chapter_filename(order_index: i64, title: &str) -> String {
    format!("{:02} - {}.mp3", order_index + 1, sanitize_filename(title))
}

#[derive(Debug, Clone, Copy, Default)]
pub struct Tags<'a> {
    pub title: Option<&'a str>,
    pub album: Option<&'a str>,
    pub track: Option<u32>,
    pub artist: Option<&'a str>,
}

#[derive(Debug, thiserror::Error)]
pub enum AudioError {
    /// Writing failed (e.g. an unwritable output dir); the only error that tries the fallback dir.
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error("ffmpeg failed: {0}")]
    Ffmpeg(String),
    #[error("could not write MP3 tags: {0}")]
    Tag(String),
}

fn encode_mp3(audio: &[f32], sample_rate: u32, mp3_path: &Path, tags: &Tags) -> Result<(), AudioError> {
    if let Some(dir) = mp3_path.parent() {
        fs::create_dir_all(dir)?;
    }
    let wav = mp3_path.with_extension("tmp.wav");
    write_wav(&wav, audio, sample_rate)?;
    let out = Command::new("ffmpeg")
        .env("PATH", child_path(std::env::var_os("PATH")))
        .args(["-y", "-loglevel", "error", "-i"]).arg(&wav)
        .args(["-codec:a", "libmp3lame", "-qscale:a", "2"]).arg(mp3_path)
        .stdin(Stdio::null()) // never a closed fd 0: ffmpeg would open its output there
        .output();
    let _ = fs::remove_file(&wav);
    let out = out.map_err(|e| AudioError::Ffmpeg(format!("could not run ffmpeg: {e}")))?;
    if !out.status.success() {
        return Err(AudioError::Ffmpeg(String::from_utf8_lossy(&out.stderr).trim().to_string()));
    }
    write_tags(mp3_path, tags)
}

fn write_tags(path: &Path, tags: &Tags) -> Result<(), AudioError> {
    use id3::{Tag, TagLike, Version};
    let mut tag = Tag::read_from_path(path).unwrap_or_else(|_| Tag::new());
    if let Some(t) = tags.title { tag.set_title(t); }
    if let Some(a) = tags.album { tag.set_album(a); }
    if let Some(n) = tags.track { tag.set_track(n); }
    if let Some(a) = tags.artist { tag.set_artist(a); }
    tag.write_to_path(path, Version::Id3v24).map_err(|e| AudioError::Tag(e.to_string()))
}

/// Encode to `output_path`; on a write error retry once as `<fallback_dir>/<same name>`.
/// Returns the path written and whether the fallback was used.
pub fn export_mp3(audio: &[f32], sample_rate: u32, output_path: &Path, tags: &Tags,
                  fallback_dir: Option<&Path>) -> Result<(PathBuf, bool), AudioError> {
    match encode_mp3(audio, sample_rate, output_path, tags) {
        Ok(()) => Ok((output_path.to_path_buf(), false)),
        Err(AudioError::Io(e)) => {
            let Some(dir) = fallback_dir else { return Err(AudioError::Io(e)) };
            let path = dir.join(output_path.file_name().expect("output path has a file name"));
            encode_mp3(audio, sample_rate, &path, tags)?;
            Ok((path, true))
        }
        Err(e) => Err(e),
    }
}

#[allow(clippy::too_many_arguments)]
pub fn export_chapter_mp3(audio: &[f32], sample_rate: u32, output_dir: &Path, order_index: i64, chapter_title: &str,
                          book_title: &str, narrator: &str, fallback_dir: Option<&Path>)
                          -> Result<(PathBuf, bool), AudioError> {
    let tags = Tags { title: Some(chapter_title), album: Some(book_title), track: Some((order_index + 1) as u32),
                      artist: Some(narrator) };
    export_mp3(audio, sample_rate, &output_dir.join(chapter_filename(order_index, chapter_title)), &tags, fallback_dir)
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cargo test -p audiobookify-core --test audio`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/core/src/audio.rs desktop/core/tests/audio.rs
git commit -m "Port MP3 export (ffmpeg + ID3 tags, fallback dir) to Rust

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `PythonEngine`, the stdio client

**Files:**
- Modify: `desktop/core/src/lib.rs` (add `pub mod engine;`)
- Create: `desktop/core/src/engine.rs`
- Test: `desktop/core/tests/engine.rs`, `desktop/core/tests/common/mod.rs` (append helpers)

**Interfaces:**
- Consumes: the Task 1 protocol and env knobs; `audio::read_wav` (Task 2).
- Produces, used by Task 6, Task 7, Task 8 and Task 9:
  - `pub enum EngineError { Engine(String), Process(String) }`. Display: `Engine` shows the bare message; `Process` shows `"engine worker failed: {0}"`.
  - `#[derive(Deserialize)] pub struct EngineInfo { pub name: String, pub max_chunk_chars: usize, pub min_chunk_chars: usize, pub sample_rate: u32, pub needs_transcript: bool }`
  - `#[derive(Serialize, Default)] pub struct Voice { pub reference_path: Option<String>, pub reference_text: Option<String>, pub trim_end: Option<f64>, pub tempo: Option<f64>, pub exaggeration: Option<f64>, pub cfg: Option<f64>, pub temperature: Option<f64> }`
  - `#[derive(Serialize, Deserialize)] pub struct Word { pub word: String, pub start: f64, pub end: f64 }`
  - `#[derive(Deserialize)] pub struct Transcript { pub text: String, pub words: Vec<Word> }`
  - `#[derive(Deserialize)] pub struct SynthOut { pub samples: usize, pub sample_rate: u32 }`
  - `impl PythonEngine`:
    - `pub fn new(python: impl Into<PathBuf>, cwd: impl Into<PathBuf>) -> Self`
    - `pub fn env(self, key: impl Into<OsString>, value: impl Into<OsString>) -> Self`
    - `pub fn load(&mut self, name: &str) -> Result<EngineInfo, EngineError>`
    - `pub fn synthesize(&mut self, text: &str, voice: &Voice, out_wav: &Path) -> Result<SynthOut, EngineError>`
    - `pub fn transcribe(&mut self, path: &Path) -> Result<Transcript, EngineError>`
  - Test helpers in `tests/common/mod.rs`: `pub fn repo_root() -> PathBuf` and `pub fn fake_engine(env: &[(&str, &str)]) -> PythonEngine`.

- [ ] **Step 1: Write the failing tests**

Append to `desktop/core/tests/common/mod.rs`:

```rust
use std::path::PathBuf;

use audiobookify_core::engine::PythonEngine;

pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().unwrap()
}

/// The real engine_worker child (repo .venv), for the fake engine, with test knobs set.
pub fn fake_engine(env: &[(&str, &str)]) -> PythonEngine {
    let repo = repo_root();
    env.iter().fold(PythonEngine::new(repo.join(".venv/bin/python"), &repo), |e, (k, v)| e.env(*k, *v))
}
```

Create `desktop/core/tests/engine.rs`:

```rust
mod common;

use audiobookify_core::audio::read_wav;
use audiobookify_core::engine::{EngineError, Voice};
use common::*;
use tempfile::TempDir;

#[test]
fn load_returns_engine_info_and_synthesize_writes_the_wav() {
    let dir = TempDir::new().unwrap();
    let mut engine = fake_engine(&[]);
    let info = engine.load("fake").unwrap();
    assert_eq!((info.name.as_str(), info.max_chunk_chars, info.min_chunk_chars, info.sample_rate, info.needs_transcript),
               ("fake", 300, 20, 24000, false));
    let wav = dir.path().join("a.wav");
    let out = engine.synthesize("Hello there.", &Voice::default(), &wav).unwrap();
    let (audio, sr) = read_wav(&wav).unwrap();
    assert_eq!((out.samples, out.sample_rate), (audio.len(), sr));
}

#[test]
fn an_engine_error_keeps_the_child_and_its_loaded_engine() {
    let dir = TempDir::new().unwrap();
    let mut engine = fake_engine(&[("AUDIOBOOKIFY_FAKE_FAIL_ON", "boom")]);
    engine.load("fake").unwrap();
    let err = engine.synthesize("Boom.", &Voice::default(), &dir.path().join("a.wav")).unwrap_err();
    assert!(matches!(&err, EngineError::Engine(m) if m == "synthesis blew up"), "{err:?}");
    engine.synthesize("Fine.", &Voice::default(), &dir.path().join("b.wav")).unwrap();
}

#[test]
fn a_dead_child_is_a_process_error_and_the_next_load_respawns_it() {
    let dir = TempDir::new().unwrap();
    let mut engine = fake_engine(&[("AUDIOBOOKIFY_FAKE_CRASH_ON", "crash")]);
    engine.load("fake").unwrap();
    let err = engine.synthesize("Crash now.", &Voice::default(), &dir.path().join("a.wav")).unwrap_err();
    assert!(matches!(err, EngineError::Process(_)), "{err:?}");
    assert!(err.to_string().starts_with("engine worker failed: "));
    // The loaded engine is forgotten with the child, so load really reloads.
    engine.load("fake").unwrap();
    engine.synthesize("Fine.", &Voice::default(), &dir.path().join("b.wav")).unwrap();
}

#[test]
fn synthesize_before_load_is_an_engine_error() {
    let dir = TempDir::new().unwrap();
    let err = fake_engine(&[]).synthesize("Hi.", &Voice::default(), &dir.path().join("a.wav")).unwrap_err();
    assert!(matches!(&err, EngineError::Engine(m) if m == "no engine loaded"), "{err:?}");
}

#[test]
fn transcribe_returns_text_and_word_timings() {
    let mut engine = fake_engine(&[("AUDIOBOOKIFY_FAKE_STT", "1")]);
    let t = engine.transcribe(std::path::Path::new("/nowhere.wav")).unwrap();
    assert_eq!(t.text, "One two. Three");
    assert_eq!((t.words.len(), t.words[1].word.as_str(), t.words[1].end), (3, " two.", 14.0));
}

#[test]
fn switching_engines_reports_the_new_engine() {
    let mut engine = fake_engine(&[]);
    assert_eq!(engine.load("fake").unwrap().name, "fake");
    assert_eq!(engine.load("fake-b").unwrap().name, "fake-b");
}

#[test]
fn dropping_the_engine_ends_the_child() {
    let mut engine = fake_engine(&[]);
    engine.load("fake").unwrap();
    let pid = engine.child_pid().unwrap();
    drop(engine);
    let alive = std::process::Command::new("kill").args(["-0", &pid.to_string()])
        .stderr(std::process::Stdio::null()).status().unwrap().success();
    assert!(!alive, "engine worker {pid} outlived its PythonEngine");
}
```

`child_pid` is a small public accessor added for this test and for logging: `pub fn child_pid(&self) -> Option<u32>`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cargo test -p audiobookify-core --test engine`
Expected: compile error, `could not find engine in audiobookify_core`.

- [ ] **Step 3: Implement**

Add `pub mod engine;` to `desktop/core/src/lib.rs`. Create `desktop/core/src/engine.rs`:

```rust
//! The client for the Python engine worker (`python -m audiobookify.engine_worker`):
//! JSON lines over the child's stdin/stdout, one request in flight at a time.
//! Audio moves through WAV files, never through the pipe.

use std::ffi::OsString;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Debug, thiserror::Error)]
pub enum EngineError {
    /// The engine answered with an error (bad input, a model exception); the child is fine.
    #[error("{0}")]
    Engine(String),
    /// The child couldn't start, died, or broke the protocol; it has been dropped
    /// and the next request starts a new one.
    #[error("engine worker failed: {0}")]
    Process(String),
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct EngineInfo {
    pub name: String,
    pub max_chunk_chars: usize,
    pub min_chunk_chars: usize,
    pub sample_rate: u32,
    pub needs_transcript: bool,
}

/// What the engines clone from. All None = the engine's built-in voice.
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Voice {
    pub reference_path: Option<String>,
    pub reference_text: Option<String>,
    pub trim_end: Option<f64>,
    /// None = 1.0.
    pub tempo: Option<f64>,
    pub exaggeration: Option<f64>,
    pub cfg: Option<f64>,
    pub temperature: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Word {
    pub word: String,
    pub start: f64,
    pub end: f64,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Transcript {
    pub text: String,
    pub words: Vec<Word>,
}

#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
pub struct SynthOut {
    pub samples: usize,
    pub sample_rate: u32,
}

struct Proc {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

pub struct PythonEngine {
    python: PathBuf,
    cwd: PathBuf,
    env: Vec<(OsString, OsString)>,
    proc: Option<Proc>,
    loaded: Option<EngineInfo>,
}

impl PythonEngine {
    /// The child starts lazily, on the first request.
    pub fn new(python: impl Into<PathBuf>, cwd: impl Into<PathBuf>) -> Self {
        Self { python: python.into(), cwd: cwd.into(), env: Vec::new(), proc: None, loaded: None }
    }

    pub fn env(mut self, key: impl Into<OsString>, value: impl Into<OsString>) -> Self {
        self.env.push((key.into(), value.into()));
        self
    }

    pub fn child_pid(&self) -> Option<u32> {
        self.proc.as_ref().map(|p| p.child.id())
    }

    /// Make `name` the loaded engine (a no-op if it already is) and return its info.
    pub fn load(&mut self, name: &str) -> Result<EngineInfo, EngineError> {
        if let Some(info) = self.loaded.as_ref().filter(|i| i.name == name) {
            return Ok(info.clone());
        }
        self.loaded = None; // the worker tears the old engine down before loading
        self.request(json!({"op": "load", "engine": name}))?;
        let ok = self.request(json!({"op": "engine_info"}))?;
        let info: EngineInfo = self.parse(ok)?;
        self.loaded = Some(info.clone());
        Ok(info)
    }

    pub fn synthesize(&mut self, text: &str, voice: &Voice, out_wav: &Path) -> Result<SynthOut, EngineError> {
        let ok = self.request(json!({"op": "synthesize", "text": text, "voice": voice, "out_wav": out_wav}))?;
        self.parse(ok)
    }

    pub fn transcribe(&mut self, path: &Path) -> Result<Transcript, EngineError> {
        let ok = self.request(json!({"op": "transcribe", "path": path}))?;
        self.parse(ok)
    }

    fn parse<T: serde::de::DeserializeOwned>(&mut self, ok: Value) -> Result<T, EngineError> {
        serde_json::from_value(ok).map_err(|e| self.fail(format!("unexpected response: {e}")))
    }

    fn request(&mut self, req: Value) -> Result<Value, EngineError> {
        let response = self.round_trip(&req).map_err(|e| self.fail(e))?;
        if let Some(ok) = response.get("ok") {
            return Ok(ok.clone());
        }
        match response.get("error").and_then(Value::as_str) {
            Some(message) => Err(EngineError::Engine(message.to_string())),
            None => Err(self.fail(format!("malformed response {response}"))),
        }
    }

    fn round_trip(&mut self, req: &Value) -> Result<Value, String> {
        if self.proc.is_none() {
            self.proc = Some(self.spawn()?);
        }
        let p = self.proc.as_mut().expect("just spawned");
        writeln!(p.stdin, "{req}").and_then(|_| p.stdin.flush()).map_err(|e| format!("could not send request: {e}"))?;
        let mut line = String::new();
        let n = p.stdout.read_line(&mut line).map_err(|e| format!("could not read response: {e}"))?;
        if n == 0 {
            return Err("the engine worker exited".into());
        }
        serde_json::from_str(&line).map_err(|e| format!("bad response line {line:?}: {e}"))
    }

    fn spawn(&self) -> Result<Proc, String> {
        let mut child = Command::new(&self.python)
            .args(["-m", "audiobookify.engine_worker"])
            .current_dir(&self.cwd)
            .envs(self.env.iter().map(|(k, v)| (k, v)))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| format!("could not start {}: {e}", self.python.display()))?;
        let stdin = child.stdin.take().expect("piped");
        let stdout = BufReader::new(child.stdout.take().expect("piped"));
        Ok(Proc { child, stdin, stdout })
    }

    /// Drop a broken child; the next request starts a fresh one.
    fn fail(&mut self, message: String) -> EngineError {
        self.loaded = None;
        if let Some(mut p) = self.proc.take() {
            let _ = p.child.kill();
            let _ = p.child.wait();
        }
        EngineError::Process(message)
    }
}

impl Drop for PythonEngine {
    /// Close stdin (the worker exits on EOF), give it a moment, then kill.
    fn drop(&mut self) {
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
```

`parse` and `request` both take `&mut self`, which is why each call site binds the response (`let ok = ...`) before parsing it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cargo test -p audiobookify-core --test engine`
Expected: all PASS. Each test spawns a Python child, so allow about 10 s.

- [ ] **Step 5: Commit**

```bash
git add desktop/core/src/lib.rs desktop/core/src/engine.rs desktop/core/tests/engine.rs desktop/core/tests/common/mod.rs
git commit -m "Add PythonEngine: stdio client for the engine worker with respawn on death

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Chunk cache, `trim_point` and `voice_for`

**Files:**
- Modify: `desktop/core/src/lib.rs` (add `pub mod jobs;`)
- Create: `desktop/core/src/jobs/mod.rs`, `desktop/core/src/jobs/cache.rs`, `desktop/core/src/jobs/voice.rs`
- Test: `desktop/core/tests/chunk_cache.rs`, `desktop/core/tests/trim_point.rs`

**Interfaces:**
- Consumes: `audio::read_wav`/`write_wav` (Task 2); `engine::{Voice, Word}` (Task 4); `models::VoiceProfile` (phase 1); `pystr::strip` (phase 2).
- Produces, used by Task 6 and Task 7:
  - `pub fn compute_cache_key(chunk_texts: &[&str], voice: &serde_json::Value) -> String` (64 lowercase hex chars)
  - `pub struct ChunkCache`, with:
    - `new(root: impl Into<PathBuf>)`
    - `chapter_dir(&self, chapter_id: i64) -> PathBuf`
    - `chunk_path(&self, chapter_id: i64, index: usize) -> PathBuf`
    - `load(&self, chapter_id: i64, key: &str) -> Vec<Vec<f32>>`
    - `write_manifest(&self, chapter_id: i64, key: &str) -> io::Result<()>`
    - `clear(&self, chapter_id: i64)`
  - `pub const MAX_REFERENCE_SECONDS: f64 = 15.0;`
  - `pub fn trim_point(words: &[Word], max_seconds: f64) -> Option<(f64, String)>`
  - `pub fn voice_for(profile: Option<&VoiceProfile>, tempo_override: Option<f64>) -> Voice`
  - Everything re-exported from `audiobookify_core::jobs`.

- [ ] **Step 1: Write the failing tests**

Create `desktop/core/tests/chunk_cache.rs`:

```rust
use audiobookify_core::audio::write_wav;
use audiobookify_core::jobs::{compute_cache_key, ChunkCache};
use serde_json::json;
use tempfile::TempDir;

fn key(chunks: &[&str], voice: &str) -> String {
    compute_cache_key(chunks, &json!({"voice": voice}))
}

#[test]
fn the_key_is_deterministic_and_changes_with_text_voice_and_boundaries() {
    assert_eq!(key(&["Hello."], "fake"), key(&["Hello."], "fake"));
    assert_ne!(key(&["Hello."], "fake"), key(&["Goodbye."], "fake"));
    assert_ne!(key(&["Hello."], "fake"), key(&["Hello."], "other"));
    assert_ne!(key(&["Hello. World."], "fake"), key(&["Hello.", "World."], "fake"));
    assert_eq!(key(&["x"], "v").len(), 64);
}

#[test]
fn chunks_round_trip_in_order_until_the_first_missing_one() {
    let dir = TempDir::new().unwrap();
    let cache = ChunkCache::new(dir.path());
    let k = key(&["a", "b"], "v");
    cache.write_manifest(1, &k).unwrap();
    write_wav(&cache.chunk_path(1, 0), &[0.1, 0.2], 24000).unwrap();
    write_wav(&cache.chunk_path(1, 1), &[0.3], 24000).unwrap();
    write_wav(&cache.chunk_path(1, 3), &[0.9], 24000).unwrap(); // after a gap: ignored
    assert_eq!(cache.load(1, &k), vec![vec![0.1, 0.2], vec![0.3]]);
    assert_eq!(cache.chunk_path(1, 1), dir.path().join("chapter-1/chunk-0001.wav"));
}

#[test]
fn load_is_empty_without_a_cache_or_with_another_key() {
    let dir = TempDir::new().unwrap();
    let cache = ChunkCache::new(dir.path());
    assert!(cache.load(999, &key(&["x"], "v")).is_empty());
    cache.write_manifest(2, &key(&["old"], "v")).unwrap();
    write_wav(&cache.chunk_path(2, 0), &[0.0; 10], 24000).unwrap();
    assert!(cache.load(2, &key(&["new"], "v")).is_empty());
}

#[test]
fn a_half_written_chunk_ends_the_cached_run() {
    let dir = TempDir::new().unwrap();
    let cache = ChunkCache::new(dir.path());
    let k = key(&["a", "b"], "v");
    cache.write_manifest(4, &k).unwrap();
    write_wav(&cache.chunk_path(4, 0), &[0.5], 24000).unwrap();
    std::fs::write(cache.chunk_path(4, 1), b"RIFF").unwrap(); // killed mid-write
    assert_eq!(cache.load(4, &k), vec![vec![0.5]]);
}

#[test]
fn clear_removes_the_chapter_dir_and_is_a_noop_without_one() {
    let dir = TempDir::new().unwrap();
    let cache = ChunkCache::new(dir.path());
    cache.write_manifest(3, "k").unwrap();
    assert!(cache.chapter_dir(3).is_dir());
    cache.clear(3);
    assert!(!cache.chapter_dir(3).exists());
    cache.clear(999);
}
```

Create `desktop/core/tests/trim_point.rs` (ports of `test_voice_analysis.py`'s `trim_point` cases, plus `voice_for`):

```rust
use audiobookify_core::engine::{Voice, Word};
use audiobookify_core::jobs::{trim_point, voice_for};
use audiobookify_core::models::VoiceProfile;

fn w(word: &str, start: f64, end: f64) -> Word {
    Word { word: word.into(), start, end }
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

#[test]
fn keeps_a_short_clip_whole() {
    assert_eq!(trim_point(&[w(" One", 0.0, 5.0), w(" two.", 5.2, 14.9)], 15.0), None);
}

#[test]
fn cuts_after_the_last_whole_word_before_the_limit() {
    let (end, text) = trim_point(&[w(" The", 0.0, 0.3), w(" quick", 0.4, 7.0), w(" fox", 7.1, 14.8),
                                   w(" ran.", 15.0, 15.6)], 15.0).unwrap();
    assert!(close(end, 14.8 + 0.15), "{end}");
    assert_eq!(text, "The quick fox");
}

#[test]
fn never_cuts_into_the_next_word() {
    let (end, _) = trim_point(&[w(" a", 0.0, 14.0), w(" b", 14.05, 16.0)], 15.0).unwrap();
    assert!(close(end, 14.05), "{end}");
}

#[test]
fn prefers_a_sentence_end_close_to_the_limit() {
    let (end, text) = trim_point(&[w(" One", 0.0, 1.0), w(" two.", 1.1, 11.5), w(" Three", 11.7, 13.0),
                                   w(" four", 13.1, 14.9), w(" five", 15.0, 16.0)], 15.0).unwrap();
    assert_eq!(text, "One two.");
    assert!(close(end, 11.65), "{end}");
}

#[test]
fn a_single_word_longer_than_the_limit_keeps_the_clip_whole() {
    assert_eq!(trim_point(&[w(" Hmm", 0.0, 16.0), w(" yes", 16.1, 17.0)], 15.0), None);
}

fn profile() -> VoiceProfile {
    VoiceProfile {
        id: 1, name: "Nova".into(), engine: "chatterbox-turbo".into(), reference_audio_path: "/v/ref.wav".into(),
        reference_transcript: Some("One two.".into()), exaggeration: 0.5, cfg_weight: 0.4, temperature: 0.8,
        created_at: 0.0, transcript_status: Some("done".into()), transcript_error: None, reference_words: None,
        reference_trim_end: Some(14.15), tempo: 1.1,
    }
}

#[test]
fn voice_for_maps_the_profile_and_lets_a_clip_override_the_tempo() {
    let v = voice_for(Some(&profile()), None);
    assert_eq!(v, Voice {
        reference_path: Some("/v/ref.wav".into()), reference_text: Some("One two.".into()), trim_end: Some(14.15),
        tempo: Some(1.1), exaggeration: Some(0.5), cfg: Some(0.4), temperature: Some(0.8),
    });
    assert_eq!(voice_for(Some(&profile()), Some(0.9)).tempo, Some(0.9));
    assert_eq!(voice_for(None, Some(0.9)), Voice::default());
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cargo test -p audiobookify-core --test chunk_cache --test trim_point`
Expected: compile error, `could not find jobs in audiobookify_core`.

- [ ] **Step 3: Implement**

Add `pub mod jobs;` to `desktop/core/src/lib.rs`.

`desktop/core/src/jobs/mod.rs`:

```rust
//! The job runner and its pieces, ported from worker.py, synth/pipeline.py and synth/chunk_cache.py.

mod cache;
mod voice;

pub use cache::{compute_cache_key, ChunkCache};
pub use voice::{trim_point, voice_for, MAX_REFERENCE_SECONDS};
```

`desktop/core/src/jobs/cache.rs`:

```rust
use std::fs;
use std::io;
use std::path::PathBuf;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::audio::read_wav;

/// Keyed on the chunks themselves, not the source text, so any chunking change
/// (chunker logic, engine min/max) invalidates audio cached under old boundaries.
pub fn compute_cache_key(chunk_texts: &[&str], voice: &Value) -> String {
    let payload = json!({"chunks": chunk_texts, "voice_params": voice}).to_string();
    Sha256::digest(payload.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

/// Synthesized chunks of an unfinished chapter, so a pause or crash resumes where it stopped:
/// `<root>/chapter-N/manifest.json` + `chunk-NNNN.wav`.
pub struct ChunkCache {
    root: PathBuf,
}

impl ChunkCache {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn chapter_dir(&self, chapter_id: i64) -> PathBuf {
        self.root.join(format!("chapter-{chapter_id}"))
    }

    pub fn chunk_path(&self, chapter_id: i64, index: usize) -> PathBuf {
        self.chapter_dir(chapter_id).join(format!("chunk-{index:04}.wav"))
    }

    /// The cached chunks from 0 up to the first missing or unreadable one; empty
    /// when the manifest is missing, unreadable or for another key.
    pub fn load(&self, chapter_id: i64, key: &str) -> Vec<Vec<f32>> {
        let manifest = fs::read_to_string(self.chapter_dir(chapter_id).join("manifest.json")).ok()
            .and_then(|s| serde_json::from_str::<Value>(&s).ok());
        if manifest.as_ref().and_then(|m| m["cache_key"].as_str()) != Some(key) {
            return Vec::new();
        }
        // An unreadable chunk was cut off mid-write; it gets synthesized again.
        (0..).map_while(|i| read_wav(&self.chunk_path(chapter_id, i)).ok().map(|(audio, _)| audio)).collect()
    }

    pub fn write_manifest(&self, chapter_id: i64, key: &str) -> io::Result<()> {
        let dir = self.chapter_dir(chapter_id);
        fs::create_dir_all(&dir)?;
        fs::write(dir.join("manifest.json"), json!({"cache_key": key}).to_string())
    }

    pub fn clear(&self, chapter_id: i64) {
        let _ = fs::remove_dir_all(self.chapter_dir(chapter_id));
    }
}
```

`desktop/core/src/jobs/voice.rs`:

```rust
use std::sync::LazyLock;

use regex::Regex;

use crate::engine::{Voice, Word};
use crate::models::VoiceProfile;
use crate::pystr;

/// Qwen3 encodes the whole reference per chunk; 36 s ran ~15% slower than 15 s.
pub const MAX_REFERENCE_SECONDS: f64 = 15.0;
const SENTENCE_WINDOW_SECONDS: f64 = 5.0; // cut at a sentence end if one falls this close to the limit
const TRAIL_SECONDS: f64 = 0.15; // air kept after the last word
static SENTENCE_END_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"[.!?…]["'”’)\]]*$"#).unwrap());

/// (end_seconds, transcript_of_kept_words) for a clip longer than max_seconds, cut at a
/// word boundary (a sentence end if one is near the limit); None if the clip already fits.
/// Port of voice/analysis.py's trim_point.
pub fn trim_point(words: &[Word], max_seconds: f64) -> Option<(f64, String)> {
    if words.last()?.end <= max_seconds {
        return None;
    }
    let fits: Vec<usize> = (0..words.len()).filter(|&i| words[i].end <= max_seconds).collect();
    let mut last = *fits.last()?; // ponytail: one word longer than the limit; use the clip whole
    if let Some(&s) = fits.iter().rev().find(|&&i| {
        SENTENCE_END_RE.is_match(pystr::strip(&words[i].word)) && words[i].end >= max_seconds - SENTENCE_WINDOW_SECONDS
    }) {
        last = s;
    }
    // `last` < len - 1: the final word ends past max_seconds, so it isn't in `fits`.
    let end = (words[last].end + TRAIL_SECONDS).min(words[last + 1].start);
    let text: String = words[..=last].iter().map(|w| w.word.as_str()).collect();
    Some((end, pystr::strip(&text).to_string()))
}

/// What a job's engine clones from; `tempo_override` is a text clip's unsaved sample tempo.
pub fn voice_for(profile: Option<&VoiceProfile>, tempo_override: Option<f64>) -> Voice {
    let Some(p) = profile else { return Voice::default() };
    Voice {
        reference_path: Some(p.reference_audio_path.clone()),
        reference_text: p.reference_transcript.clone(),
        trim_end: p.reference_trim_end,
        tempo: Some(tempo_override.unwrap_or(p.tempo)),
        exaggeration: Some(p.exaggeration),
        cfg: Some(p.cfg_weight),
        temperature: Some(p.temperature),
    }
}
```

If `pystr::strip` isn't `pub`, use the phase 2 equivalent that strips Python whitespace. It is listed as `pub fn strip(s: &str) -> &str` in `src/pystr.rs`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cargo test -p audiobookify-core --test chunk_cache --test trim_point`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/core/src/lib.rs desktop/core/src/jobs desktop/core/tests/chunk_cache.rs desktop/core/tests/trim_point.rs
git commit -m "Add chunk cache, trim_point and voice_for for the Rust runner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Runner, part 1: chapter jobs

**Files:**
- Create: `desktop/core/src/jobs/runner.rs`
- Modify: `desktop/core/src/jobs/mod.rs`
- Test: `desktop/core/tests/runner.rs`

**Interfaces:**
- Consumes:
  - From Tasks 2–5: `audio::{concatenate_chunks, export_chapter_mp3, read_wav, AudioError}`, `engine::{PythonEngine, EngineError, EngineInfo}`, `jobs::{ChunkCache, compute_cache_key, voice_for}`.
  - From phases 1–2: `text::{apply_lexicon, chunk_text, EndsWith}` and the DB methods `pop_next_queued_job`, `get_chapter`, `get_book`, `get_voice_profile_by_id`, `list_lexicon_entries`, `update_chapter_status`, `update_job_progress`, `is_paused`, `is_job_cancel_requested`, `requeue_job`, `finish_job`.
- Produces, used by Task 7 and Task 8:
  - `pub struct Paths { pub cache_root: PathBuf, pub text_clip_dir: PathBuf, pub backup_dir: PathBuf }`
  - `pub enum JobError { Db(DbError), Engine(EngineError), Audio(AudioError), Io(io::Error) }`, with `Display` delegating to the inner error.
  - `pub struct Runner { pub db: Database, ... }`, with:
    - `pub fn new(db: Database, engine: PythonEngine, paths: Paths) -> Self`
    - `pub fn run_once(&mut self) -> Result<bool, JobError>`. In this task it handles only paused checks and chapter jobs; Task 7 adds the rest.

- [ ] **Step 1: Write the failing tests**

Create `desktop/core/tests/runner.rs`:

```rust
mod common;

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use audiobookify_core::db::Database;
use audiobookify_core::jobs::{Paths, Runner};
use audiobookify_core::models::*;
use common::*;
use tempfile::TempDir;

const LONG: &str = "She looked out over the water for a long time before she finally spoke again.";

/// A runner on a temp DB and temp paths, driving the real engine worker in fake mode.
/// Every synthesized chunk's text is logged to <dir>/log.jsonl.
fn runner(dir: &Path, env: &[(&str, &str)]) -> Runner {
    let log = dir.join("log.jsonl");
    let mut env = env.to_vec();
    let log_str = log.to_string_lossy().into_owned();
    env.push(("AUDIOBOOKIFY_FAKE_LOG", &log_str));
    Runner::new(open(dir), fake_engine(&env), Paths {
        cache_root: dir.join("cache"), text_clip_dir: dir.join("clips"), backup_dir: dir.join("backup"),
    })
}

fn logged(dir: &Path) -> Vec<String> {
    std::fs::read_to_string(dir.join("log.jsonl")).unwrap_or_default().lines()
        .map(|l| serde_json::from_str(l).unwrap()).collect()
}

/// A "fake"-engine book with one chapter per text and a queued job for each.
fn book_with_jobs(db: &Database, dir: &Path, texts: &[&str]) -> (Book, Vec<Chapter>, Vec<Job>) {
    let book = md_book(db, dir, &path(dir, "b.md"));
    db.update_book_engine(book.id, "fake").unwrap();
    let raws: Vec<RawChapter> = texts.iter().enumerate().map(|(i, t)| raw(&format!("Ch {}", i + 1), t)).collect();
    let chapters = db.add_chapters(book.id, &raws).unwrap();
    let jobs = chapters.iter().map(|c| db.create_job(c.id).unwrap()).collect();
    (db.get_book(book.id).unwrap(), chapters, jobs)
}

/// Three chunks: each paragraph is far over the fake engine's 20-char minimum.
fn three_paragraphs() -> String {
    format!("{LONG}\n\n{LONG} Again.\n\n{LONG} Once more.")
}

/// Run `act` on a second DB connection as soon as the first chunk has started.
fn when_first_chunk_starts(dir: &Path, act: impl FnOnce(&Database) + Send + 'static) -> std::thread::JoinHandle<()> {
    let dir: PathBuf = dir.to_path_buf();
    std::thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(60);
        while logged(&dir).is_empty() {
            assert!(Instant::now() < deadline, "first chunk never started");
            std::thread::sleep(Duration::from_millis(20));
        }
        act(&open(&dir));
    })
}

#[test]
fn run_once_is_false_with_nothing_queued() {
    let dir = TempDir::new().unwrap();
    assert!(!runner(dir.path(), &[]).run_once().unwrap());
}

#[test]
fn a_chapter_job_produces_a_tagged_mp3_and_marks_everything_done() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    let (_, chapters, jobs) = book_with_jobs(&r.db, dir.path(), &["Body one is long enough to speak."]);

    assert!(r.run_once().unwrap());

    let job = r.db.get_job(jobs[0].id).unwrap();
    let chapter = r.db.get_chapter(chapters[0].id).unwrap();
    assert_eq!((job.status, job.progress), (JobStatus::Done, Some(1.0)));
    assert_eq!((chapter.status, chapter.engine.as_deref(), chapter.used_fallback), (ChapterStatus::Done, Some("fake"), false));
    assert!(chapter.duration.unwrap() > 0.0);
    assert_eq!(chapter.output_mp3_path.as_deref(), Some(path(dir.path(), "out/01 - Ch 1.mp3").as_str()));
    assert!(Path::new(chapter.output_mp3_path.as_deref().unwrap()).is_file());
    assert!(!dir.path().join(format!("cache/chapter-{}", chapter.id)).exists(), "cache not cleared");
}

#[test]
fn chunks_by_paragraph_and_never_sends_a_tiny_chunk() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    book_with_jobs(&r.db, dir.path(), &[&format!("{LONG}\n\nNo.\n\n* * *\n\n{LONG}")]);
    r.run_once().unwrap();
    assert_eq!(logged(dir.path()), vec![format!("{LONG} No."), LONG.to_string()]);
}

#[test]
fn applies_the_lexicon_with_book_entries_winning_over_global_ones() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    let (book, _, _) = book_with_jobs(&r.db, dir.path(), &["Cadeyrn rode out at dawn with the others."]);
    r.db.create_lexicon_entry(&NewLexiconEntry::new("Cadeyrn", "global-take", "global")).unwrap();
    r.db.create_lexicon_entry(&NewLexiconEntry { book_id: book.id, ..NewLexiconEntry::new("Cadeyrn", "kah-DAIRN", "book") }).unwrap();
    r.run_once().unwrap();
    assert_eq!(logged(dir.path()), vec!["kah-DAIRN rode out at dawn with the others.".to_string()]);
}

#[test]
fn a_synthesis_error_fails_the_job_and_the_chapter() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_FAIL_ON", "body one")]);
    let (_, chapters, jobs) = book_with_jobs(&r.db, dir.path(), &["Body one is long enough to speak."]);
    assert!(r.run_once().unwrap());
    let job = r.db.get_job(jobs[0].id).unwrap();
    assert_eq!(job.status, JobStatus::Failed);
    assert!(job.error.unwrap().contains("synthesis blew up"));
    assert_eq!(r.db.get_chapter(chapters[0].id).unwrap().status, ChapterStatus::Failed);
}

#[test]
fn an_unknown_engine_fails_the_job() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    let (book, _, jobs) = book_with_jobs(&r.db, dir.path(), &["Body one is long enough to speak."]);
    r.db.update_book_engine(book.id, "no-such-engine").unwrap();
    assert!(r.run_once().unwrap());
    let job = r.db.get_job(jobs[0].id).unwrap();
    assert_eq!(job.status, JobStatus::Failed);
    assert!(job.error.unwrap().contains("no-such-engine"));
}

#[test]
fn a_crashed_engine_fails_its_job_and_the_next_job_respawns_it() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_CRASH_ON", "crash")]);
    let (_, _, jobs) = book_with_jobs(&r.db, dir.path(), &["Crash here, long enough to speak.", "Body two is long enough to speak."]);
    r.run_once().unwrap();
    let first = r.db.get_job(jobs[0].id).unwrap();
    assert_eq!(first.status, JobStatus::Failed);
    assert!(first.error.unwrap().starts_with("engine worker failed"));
    r.run_once().unwrap();
    assert_eq!(r.db.get_job(jobs[1].id).unwrap().status, JobStatus::Done);
}

#[test]
fn records_used_fallback_when_the_output_dir_is_unwritable() {
    use std::os::unix::fs::PermissionsExt;
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    let (book, chapters, _) = book_with_jobs(&r.db, dir.path(), &["Body one is long enough to speak."]);
    let out = dir.path().join("out");
    std::fs::create_dir(&out).unwrap();
    std::fs::set_permissions(&out, std::fs::Permissions::from_mode(0o500)).unwrap();
    r.run_once().unwrap();
    std::fs::set_permissions(&out, std::fs::Permissions::from_mode(0o700)).unwrap();
    let chapter = r.db.get_chapter(chapters[0].id).unwrap();
    assert!(chapter.used_fallback);
    assert!(chapter.output_mp3_path.unwrap().starts_with(&path(dir.path(), &format!("backup/{}", book.id))));
}

#[test]
fn a_pause_mid_chapter_requeues_the_job_keeps_the_cache_and_resumes_without_redoing_chunks() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_DELAY", "0.5")]);
    let (_, chapters, jobs) = book_with_jobs(&r.db, dir.path(), &[&three_paragraphs()]);
    let pauser = when_first_chunk_starts(dir.path(), |db| db.set_paused(true).unwrap());

    assert!(r.run_once().unwrap());
    pauser.join().unwrap();

    assert_eq!(r.db.get_job(jobs[0].id).unwrap().status, JobStatus::Queued);
    assert_eq!(r.db.get_chapter(chapters[0].id).unwrap().status, ChapterStatus::Pending);
    assert!(dir.path().join(format!("cache/chapter-{}/chunk-0000.wav", chapters[0].id)).is_file());
    assert!(!r.run_once().unwrap(), "paused: no work");

    r.db.set_paused(false).unwrap();
    r.run_once().unwrap();
    assert_eq!(r.db.get_job(jobs[0].id).unwrap().status, JobStatus::Done);
    assert_eq!(logged(dir.path()).len(), 3, "a cached chunk was synthesized again");
}

#[test]
fn a_cancel_mid_chapter_cancels_the_job_and_clears_the_cache() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_DELAY", "0.5")]);
    let (_, chapters, jobs) = book_with_jobs(&r.db, dir.path(), &[&three_paragraphs()]);
    let job_id = jobs[0].id;
    let canceller = when_first_chunk_starts(dir.path(), move |db| db.request_job_cancel(job_id).unwrap());

    assert!(r.run_once().unwrap());
    canceller.join().unwrap();

    assert_eq!(r.db.get_job(job_id).unwrap().status, JobStatus::Cancelled);
    assert_eq!(r.db.get_chapter(chapters[0].id).unwrap().status, ChapterStatus::Pending);
    assert!(!dir.path().join(format!("cache/chapter-{}", chapters[0].id)).exists());
}

#[test]
fn another_engines_cached_audio_is_never_reused() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_DELAY", "0.5")]);
    let (book, chapters, _) = book_with_jobs(&r.db, dir.path(), &[&three_paragraphs()]);
    let pauser = when_first_chunk_starts(dir.path(), |db| db.set_paused(true).unwrap());
    r.run_once().unwrap();
    pauser.join().unwrap();

    r.db.update_book_engine(book.id, "fake-b").unwrap();
    r.db.set_paused(false).unwrap();
    r.run_once().unwrap();

    let log = logged(dir.path());
    assert_eq!(log.len(), 4, "chunk 0 is regenerated by fake-b: {log:?}");
    assert_eq!(log[0], log[1]);
    assert_eq!(r.db.get_chapter(chapters[0].id).unwrap().engine.as_deref(), Some("fake-b"));
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cargo test -p audiobookify-core --test runner`
Expected: compile error, `no Paths in jobs`.

- [ ] **Step 3: Implement**

Add to `desktop/core/src/jobs/mod.rs`:

```rust
mod runner;

pub use runner::{JobError, Paths, Runner};
```

Create `desktop/core/src/jobs/runner.rs`:

```rust
//! The generation loop, ported from worker.py and synth/pipeline.py. Pause and
//! cancel are checked between chunks, as before.

use std::io;
use std::path::{Path, PathBuf};

use serde_json::json;

use super::cache::{compute_cache_key, ChunkCache};
use super::voice::voice_for;
use crate::audio::{concatenate_chunks, export_chapter_mp3, read_wav, AudioError};
use crate::db::{Database, DbError};
use crate::engine::{EngineError, EngineInfo, PythonEngine};
use crate::models::{ChapterOutcome, ChapterStatus, Job, JobStatus, VoiceProfile};
use crate::text::{apply_lexicon, chunk_text, EndsWith};

pub struct Paths {
    /// Chunks of unfinished chapters (`.cache/synth`).
    pub cache_root: PathBuf,
    /// Finished text clips (`text_clips`).
    pub text_clip_dir: PathBuf,
    /// Where MP3s go when the chosen output dir can't be written (`output`).
    pub backup_dir: PathBuf,
}

#[derive(Debug, thiserror::Error)]
pub enum JobError {
    #[error(transparent)]
    Db(#[from] DbError),
    #[error(transparent)]
    Engine(#[from] EngineError),
    #[error(transparent)]
    Audio(#[from] AudioError),
    #[error(transparent)]
    Io(#[from] io::Error),
}

enum Stopped {
    Paused,
    Cancelled,
}

pub struct Runner {
    pub db: Database,
    engine: PythonEngine,
    cache: ChunkCache,
    paths: Paths,
}

impl Runner {
    pub fn new(db: Database, engine: PythonEngine, paths: Paths) -> Self {
        Self { db, engine, cache: ChunkCache::new(&paths.cache_root), paths }
    }

    /// One unit of work, if any; false when paused or idle.
    pub fn run_once(&mut self) -> Result<bool, JobError> {
        if self.db.is_paused()? {
            return Ok(false);
        }
        self.process_next_chapter_job()
    }

    fn process_next_chapter_job(&mut self) -> Result<bool, JobError> {
        let Some(job) = self.db.pop_next_queued_job()? else { return Ok(false) };
        match self.run_chapter_job(&job) {
            Ok(None) => self.db.finish_job(job.id, JobStatus::Done, None)?,
            Ok(Some(Stopped::Paused)) => {
                self.db.update_chapter_status(job.chapter_id, ChapterStatus::Pending, ChapterOutcome::default())?;
                self.db.requeue_job(job.id)?;
            }
            Ok(Some(Stopped::Cancelled)) => {
                self.db.update_chapter_status(job.chapter_id, ChapterStatus::Pending, ChapterOutcome::default())?;
                self.db.finish_job(job.id, JobStatus::Cancelled, None)?;
                self.cache.clear(job.chapter_id);
            }
            Err(e) => {
                let message = e.to_string();
                // Ignored: the chapter may be the thing that's missing.
                let _ = self.db.update_chapter_status(
                    job.chapter_id, ChapterStatus::Failed, ChapterOutcome { error: Some(&message), ..Default::default() });
                self.db.finish_job(job.id, JobStatus::Failed, Some(&message))?;
            }
        }
        Ok(true)
    }

    fn run_chapter_job(&mut self, job: &Job) -> Result<Option<Stopped>, JobError> {
        let chapter = self.db.get_chapter(job.chapter_id)?;
        let book = self.db.get_book(chapter.book_id)?;
        let (info, profile) = self.engine_and_voice(&book.engine, book.voice_profile_id)?;
        let voice = voice_for(profile.as_ref(), None);
        let narrator = profile.as_ref().map_or("default", |p| p.name.as_str()).to_string();
        let entries = self.db.list_lexicon_entries(Some(book.id))?;
        self.db.update_chapter_status(chapter.id, ChapterStatus::Generating, ChapterOutcome::default())?;

        let text = apply_lexicon(&chapter.text, entries.iter().map(|e| (e.term.as_str(), e.override_value.as_str())));
        let chunks = chunk_text(&text, info.max_chunk_chars, info.min_chunk_chars);
        let texts: Vec<&str> = chunks.iter().map(|c| c.text.as_str()).collect();
        // The engine is part of the key: same text and voice from another engine is different audio.
        let key = compute_cache_key(&texts, &json!({"voice": voice, "engine": info.name}));
        let mut audio = self.cache.load(chapter.id, &key);
        if audio.is_empty() {
            self.cache.clear(chapter.id);
            self.cache.write_manifest(chapter.id, &key)?;
        }

        let total = chunks.len();
        for i in audio.len()..total {
            let chunk_path = self.cache.chunk_path(chapter.id, i);
            self.engine.synthesize(&chunks[i].text, &voice, &chunk_path)?;
            audio.push(read_wav(&chunk_path)?.0);
            self.db.update_job_progress(job.id, (i + 1) as f64 / total as f64)?;
            if self.db.is_paused()? {
                return Ok(Some(Stopped::Paused));
            }
            if self.db.is_job_cancel_requested(job.id)? {
                return Ok(Some(Stopped::Cancelled));
            }
        }

        let ends: Vec<EndsWith> = chunks.iter().map(|c| c.ends_with).collect();
        let combined = concatenate_chunks(audio, &ends, info.sample_rate);
        let fallback = self.paths.backup_dir.join(book.id.to_string());
        let (mp3, used_fallback) = export_chapter_mp3(
            &combined, info.sample_rate, Path::new(&book.output_dir), chapter.order_index, &chapter.title,
            &book.title, &narrator, Some(&fallback))?;
        let mp3 = mp3.to_string_lossy();
        self.db.update_chapter_status(chapter.id, ChapterStatus::Done, ChapterOutcome {
            output_mp3_path: Some(&mp3),
            duration: Some(combined.len() as f64 / info.sample_rate as f64),
            error: None,
            used_fallback,
            engine: Some(&info.name),
        })?;
        self.cache.clear(chapter.id);
        Ok(None)
    }

    /// The loaded engine and the job's voice profile.
    fn engine_and_voice(&mut self, engine: &str, voice_profile_id: Option<i64>)
                        -> Result<(EngineInfo, Option<VoiceProfile>), JobError> {
        let info = self.engine.load(engine)?;
        let profile = match voice_profile_id {
            Some(id) => self.db.get_voice_profile_by_id(id)?,
            None => None,
        };
        Ok((info, profile))
    }
}
```

(Task 7 extends `engine_and_voice` with lazy transcription.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cargo test -p audiobookify-core --test runner`
Expected: all PASS. The suite takes a while because every test starts a Python child.

- [ ] **Step 5: Commit**

```bash
git add desktop/core/src/jobs desktop/core/tests/runner.rs
git commit -m "Add the Rust runner's chapter jobs: cache resume, pause, cancel, fallback

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Runner, part 2: text clips, transcriptions, the loop

**Files:**
- Modify: `desktop/core/src/jobs/runner.rs`
- Test: `desktop/core/tests/runner.rs` (append)

**Interfaces:**
- Consumes:
  - From Task 6: `Runner`, `run_chapter_job`, `engine_and_voice`.
  - `audio::{export_mp3, Tags}` (Task 3); `jobs::{trim_point, MAX_REFERENCE_SECONDS}` (Task 5); `engine::Transcript` (Task 4).
  - DB methods: `next_pending_transcription`, `save_voice_transcript`, `fail_voice_transcription`, `pop_next_queued_text_clip`, `update_text_clip_progress`, `finish_text_clip`, `reset_stale_generating_jobs`, `reset_stale_generating_text_clips`.
- Produces, used by Task 8:
  - `run_once` order: paused → false; then one pending transcription; then one chapter job; then one text clip.
  - `pub fn run_forever(&mut self, stop: &AtomicBool)`

- [ ] **Step 1: Write the failing tests**

Append to `desktop/core/tests/runner.rs`:

```rust
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// A voice whose 20 s reference clip is real silence, so trim and tempo clips can be cut from it.
fn voice_with_clip(db: &Database, dir: &Path, transcript_status: Option<&str>) -> VoiceProfile {
    let reference = dir.join("ref.wav");
    audiobookify_core::audio::write_wav(&reference, &vec![0.0; 24000 * 20], 24000).unwrap();
    db.create_voice_profile(&NewVoiceProfile {
        name: "Nova", engine: "chatterbox-turbo", reference_audio_path: &reference.to_string_lossy(),
        reference_transcript: None, exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8, transcript_status,
    }).unwrap()
}

#[test]
fn a_text_clip_is_chunked_by_paragraph_and_exported() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    let clip = r.db.create_text_clip(&format!("{LONG}\n\n{LONG}"), None, "fake", None).unwrap();
    assert!(r.run_once().unwrap());
    let clip = r.db.get_text_clip(clip.id).unwrap();
    assert_eq!(clip.status, JobStatus::Done);
    assert_eq!(clip.output_path.as_deref(), Some(path(dir.path(), &format!("clips/text-clip-{}.mp3", clip.id)).as_str()));
    assert!(Path::new(clip.output_path.as_deref().unwrap()).is_file());
    assert_eq!(logged(dir.path()), vec![LONG.to_string(), LONG.to_string()]);
    let leftovers: Vec<_> = std::fs::read_dir(dir.path().join("clips")).unwrap().map(|e| e.unwrap().file_name()).collect();
    assert_eq!(leftovers.len(), 1, "temp chunk WAV left behind: {leftovers:?}");
}

#[test]
fn a_failed_text_clip_records_the_error() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_FAIL_ON", "water")]);
    let clip = r.db.create_text_clip(LONG, None, "fake", None).unwrap();
    r.run_once().unwrap();
    let clip = r.db.get_text_clip(clip.id).unwrap();
    assert_eq!(clip.status, JobStatus::Failed);
    assert!(clip.error.unwrap().contains("synthesis blew up"));
}

#[test]
fn transcribes_pending_voices_and_trims_long_clips() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_STT", "1")]);
    let voice = voice_with_clip(&r.db, dir.path(), Some("pending"));
    assert!(r.run_once().unwrap());
    let saved = r.db.get_voice_profile_by_id(voice.id).unwrap().unwrap();
    assert_eq!(saved.transcript_status.as_deref(), Some("done"));
    assert_eq!((saved.reference_transcript.as_deref(), saved.reference_trim_end), (Some("One two."), Some(14.15)));
    assert_eq!(saved.reference_words.unwrap()[1]["word"], " two.");
    assert!(!r.run_once().unwrap());
}

#[test]
fn records_a_failed_transcription_instead_of_retrying_forever() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_STT", "error")]);
    let voice = voice_with_clip(&r.db, dir.path(), Some("pending"));
    r.run_once().unwrap();
    let saved = r.db.get_voice_profile_by_id(voice.id).unwrap().unwrap();
    assert_eq!((saved.transcript_status.as_deref(), saved.transcript_error.as_deref()), (Some("failed"), Some("no speech found")));
    assert!(!r.run_once().unwrap());
}

#[test]
fn transcribes_a_voice_lazily_when_the_engine_needs_a_transcript() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_STT", "1"), ("AUDIOBOOKIFY_FAKE_NEEDS_TRANSCRIPT", "1")]);
    let voice = voice_with_clip(&r.db, dir.path(), None); // made before transcripts existed
    r.db.create_text_clip(LONG, Some(voice.id), "fake", None).unwrap();
    r.run_once().unwrap();
    assert_eq!(r.db.get_voice_profile_by_id(voice.id).unwrap().unwrap().transcript_status.as_deref(), Some("done"));
    assert!(dir.path().join("ref.trim-14.15s.wav").is_file(), "the engine didn't clone from the trimmed clip");
}

#[test]
fn does_not_transcribe_for_engines_that_clone_from_audio_alone() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_STT", "error")]);
    let voice = voice_with_clip(&r.db, dir.path(), None);
    let clip = r.db.create_text_clip(LONG, Some(voice.id), "fake", None).unwrap();
    r.run_once().unwrap();
    assert_eq!(r.db.get_text_clip(clip.id).unwrap().status, JobStatus::Done);
    assert_eq!(r.db.get_voice_profile_by_id(voice.id).unwrap().unwrap().transcript_status, None);
}

#[test]
fn a_text_clip_tempo_overrides_the_voice_tempo_for_that_clip_only() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[]);
    let voice = voice_with_clip(&r.db, dir.path(), None);
    r.db.set_voice_tempo(voice.id, 1.1).unwrap();
    r.db.create_text_clip(LONG, Some(voice.id), "fake", Some(0.9)).unwrap();
    r.db.create_text_clip(LONG, Some(voice.id), "fake", None).unwrap();
    r.run_once().unwrap();
    assert!(dir.path().join("ref.tempo-0.90.wav").is_file());
    assert!(!dir.path().join("ref.tempo-1.10.wav").exists());
    r.run_once().unwrap();
    assert!(dir.path().join("ref.tempo-1.10.wav").is_file());
}

#[test]
fn transcriptions_run_before_chapter_jobs() {
    let dir = TempDir::new().unwrap();
    let mut r = runner(dir.path(), &[("AUDIOBOOKIFY_FAKE_STT", "1")]);
    let (_, _, jobs) = book_with_jobs(&r.db, dir.path(), &["Body one is long enough to speak."]);
    voice_with_clip(&r.db, dir.path(), Some("pending"));
    r.run_once().unwrap();
    assert_eq!(r.db.get_job(jobs[0].id).unwrap().status, JobStatus::Queued);
}

#[test]
fn run_forever_resets_stale_jobs_works_the_queue_and_stops_on_the_flag() {
    let dir = TempDir::new().unwrap();
    let r = runner(dir.path(), &[]);
    let (_, _, jobs) = book_with_jobs(&r.db, dir.path(), &["Body one is long enough to speak."]);
    r.db.pop_next_queued_job().unwrap(); // left "generating" by a runner that died
    let job_id = jobs[0].id;
    let stop = Arc::new(AtomicBool::new(false));
    let handle = {
        let stop = stop.clone();
        let mut r = r;
        std::thread::spawn(move || r.run_forever(&stop))
    };
    let db = open(dir.path());
    let deadline = Instant::now() + Duration::from_secs(60);
    while db.get_job(job_id).unwrap().status != JobStatus::Done {
        assert!(Instant::now() < deadline, "job never finished");
        std::thread::sleep(Duration::from_millis(100));
    }
    stop.store(true, Ordering::Relaxed);
    handle.join().unwrap();
}
```

`Runner` must be `Send` to move into the thread. `rusqlite::Connection` and `std::process::Child` are both `Send`, so it is.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cargo test -p audiobookify-core --test runner`
Expected: compile error, `no method named run_forever`. Once that's stubbed, the text clip and transcription tests fail with `run_once` returning false.

- [ ] **Step 3: Implement**

In `desktop/core/src/jobs/runner.rs`, merge these imports into the existing `use` lines:

```rust
use std::fs;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use super::voice::{trim_point, MAX_REFERENCE_SECONDS};
use crate::audio::{export_mp3, Tags};
use crate::models::TextClip;
```

Replace `run_once`:

```rust
    /// One unit of work, if any; false when paused or idle. Transcriptions go
    /// first: they take seconds and the Voices page is waiting on them.
    pub fn run_once(&mut self) -> Result<bool, JobError> {
        if self.db.is_paused()? {
            return Ok(false);
        }
        if self.process_next_transcription()? {
            return Ok(true);
        }
        if self.process_next_chapter_job()? {
            return Ok(true);
        }
        self.process_next_text_clip()
    }

    /// The runner thread's body: work the queue until `stop` is set.
    pub fn run_forever(&mut self, stop: &AtomicBool) {
        // Jobs a dead runner left mid-generation go back in the queue.
        if let Err(e) = self.db.reset_stale_generating_jobs().and_then(|_| self.db.reset_stale_generating_text_clips()) {
            eprintln!("Error: could not reset stale jobs: {e}");
        }
        while !stop.load(Ordering::Relaxed) {
            let did_work = self.run_once().unwrap_or_else(|e| {
                eprintln!("Error: worker loop iteration failed: {e}");
                false
            });
            if !did_work {
                for _ in 0..20 { // 2 s idle poll, in steps so `stop` is seen promptly
                    if stop.load(Ordering::Relaxed) {
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
            }
        }
    }

    fn process_next_transcription(&mut self) -> Result<bool, JobError> {
        let Some(profile) = self.db.next_pending_transcription()? else { return Ok(false) };
        self.transcribe_voice(&profile)?;
        Ok(true)
    }

    /// One Whisper pass: transcript, word timings, and the reference trim. A failure
    /// is recorded on the voice, so it isn't retried forever.
    fn transcribe_voice(&mut self, profile: &VoiceProfile) -> Result<(), JobError> {
        match self.engine.transcribe(Path::new(&profile.reference_audio_path)) {
            Ok(t) => {
                let (trim_end, text) = match trim_point(&t.words, MAX_REFERENCE_SECONDS) {
                    Some((end, text)) => (Some(end), text),
                    None => (None, t.text),
                };
                let words = serde_json::to_value(&t.words).expect("words serialize");
                self.db.save_voice_transcript(profile.id, &text, Some(&words), trim_end)?;
            }
            Err(e) => self.db.fail_voice_transcription(profile.id, &e.to_string())?,
        }
        Ok(())
    }

    fn process_next_text_clip(&mut self) -> Result<bool, JobError> {
        let Some(clip) = self.db.pop_next_queued_text_clip()? else { return Ok(false) };
        if let Err(e) = self.run_text_clip(&clip) {
            self.db.finish_text_clip(clip.id, JobStatus::Failed, None, Some(&e.to_string()), false)?;
        }
        Ok(true)
    }

    fn run_text_clip(&mut self, clip: &TextClip) -> Result<(), JobError> {
        let (info, profile) = self.engine_and_voice(&clip.engine, clip.voice_profile_id)?;
        let voice = voice_for(profile.as_ref(), clip.tempo);
        let narrator = profile.as_ref().map_or("default", |p| p.name.as_str()).to_string();
        let entries = self.db.list_lexicon_entries(None)?;
        let text = apply_lexicon(&clip.text, entries.iter().map(|e| (e.term.as_str(), e.override_value.as_str())));
        let chunks = chunk_text(&text, info.max_chunk_chars, info.min_chunk_chars);

        fs::create_dir_all(&self.paths.text_clip_dir)?;
        let part = self.paths.text_clip_dir.join(format!(".text-clip-{}.part.wav", clip.id));
        let audio = self.synthesize_all(&chunks, &voice, &part, clip.id);
        let _ = fs::remove_file(&part);
        let audio = audio?;

        let ends: Vec<EndsWith> = chunks.iter().map(|c| c.ends_with).collect();
        let combined = concatenate_chunks(audio, &ends, info.sample_rate);
        let title = format!("Text clip #{}", clip.id);
        let tags = Tags { title: Some(&title), artist: Some(&narrator), ..Default::default() };
        let (path, used_fallback) = export_mp3(
            &combined, info.sample_rate, &self.paths.text_clip_dir.join(format!("text-clip-{}.mp3", clip.id)), &tags,
            Some(&self.paths.backup_dir.join("text-clips")))?;
        self.db.finish_text_clip(clip.id, JobStatus::Done, Some(&path.to_string_lossy()), None, used_fallback)?;
        Ok(())
    }

    /// Every chunk through one scratch WAV; text clips aren't cached.
    fn synthesize_all(&mut self, chunks: &[crate::text::Chunk], voice: &crate::engine::Voice, part: &Path,
                      clip_id: i64) -> Result<Vec<Vec<f32>>, JobError> {
        let mut audio = Vec::with_capacity(chunks.len());
        for (i, chunk) in chunks.iter().enumerate() {
            self.engine.synthesize(&chunk.text, voice, part)?;
            audio.push(read_wav(part)?.0);
            self.db.update_text_clip_progress(clip_id, (i + 1) as f64 / chunks.len() as f64)?;
        }
        Ok(audio)
    }
```

Replace `engine_and_voice`:

```rust
    /// The loaded engine and the job's voice profile, transcribed first if this
    /// engine needs a transcript and the voice has none yet (voices made before
    /// transcripts existed).
    fn engine_and_voice(&mut self, engine: &str, voice_profile_id: Option<i64>)
                        -> Result<(EngineInfo, Option<VoiceProfile>), JobError> {
        let info = self.engine.load(engine)?;
        let Some(id) = voice_profile_id else { return Ok((info, None)) };
        let Some(profile) = self.db.get_voice_profile_by_id(id)? else { return Ok((info, None)) };
        if !info.needs_transcript || !profile.reference_transcript.as_deref().unwrap_or("").trim().is_empty() {
            return Ok((info, Some(profile)));
        }
        self.transcribe_voice(&profile)?;
        // A worker crash during transcription drops the loaded engine; load is a no-op otherwise.
        let info = self.engine.load(engine)?;
        Ok((info, self.db.get_voice_profile_by_id(id)?))
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cargo test -p audiobookify-core --test runner`
Expected: all PASS, Task 6's tests included.

- [ ] **Step 5: Run the whole core suite**

Run: `cargo test -p audiobookify-core`
Expected: all PASS (phase 1 and 2 tests unchanged).

- [ ] **Step 6: Commit**

```bash
git add desktop/core/src/jobs/runner.rs desktop/core/tests/runner.rs
git commit -m "Add text clips, voice transcription and run_forever to the Rust runner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Tauri wiring: the Rust runner replaces the Python worker

**Files:**
- Modify: `desktop/Cargo.toml`, `desktop/src/sidecars.rs`, `desktop/src/main.rs`

**Interfaces:**
- Consumes: `audiobookify_core::{audio::child_path, db::Database, engine::PythonEngine, jobs::{Paths, Runner}}`.
- Produces: the app runs uvicorn as its only sidecar, plus a `job-runner` thread that owns the engine worker child.

- [ ] **Step 1: Update the sidecar test first**

In `desktop/src/sidecars.rs`:
- Delete `fn child_path` and its test `child_path_handles_missing_empty_and_present`. Both now live in core (Task 3).
- Change `audiobookify_commands_use_repo_venv` to expect a single uvicorn command. Read the current test body (around line 374) and replace its assertions with:

```rust
    #[test]
    fn audiobookify_commands_start_only_the_api() {
        let repo = Path::new("/repo");
        let cmds = audiobookify_commands(repo);
        assert_eq!(cmds.len(), 1, "the job runner is Rust now; no Python worker sidecar");
        assert_eq!(cmds[0].get_program(), "/repo/.venv/bin/uvicorn");
        assert_eq!(cmds[0].get_current_dir(), Some(repo));
    }
```

Run (in `desktop/`): `cargo test -p audiobookify-desktop sidecars`
Expected: FAIL. `child_path` is unresolved and the command count is 2.

- [ ] **Step 2: Make it pass**

`desktop/Cargo.toml` `[dependencies]`:

```toml
audiobookify-core = { path = "core" }
tauri = { version = "2", features = [] }
```

`desktop/src/sidecars.rs`: rename the doc comment and the function body so only the API starts:

```rust
use audiobookify_core::audio::child_path;

/// The FastAPI app. Generation runs in-process (`jobs::Runner`), not as a sidecar.
pub fn audiobookify_commands(repo: &Path) -> Vec<Command> {
    let mut api = Command::new(repo.join(".venv/bin/uvicorn"));
    api.args(["audiobookify.api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000"]);
    // The repo root as cwd: create_app() defaults to ./audiobookify.db, ./uploads, ./voices.
    api.current_dir(repo).env("HF_HUB_OFFLINE", "1");
    api.env("PATH", child_path(std::env::var_os("PATH")));
    vec![api]
}
```

Remove `use std::ffi::OsString;` from `sidecars.rs` if nothing else uses it.

Run: `cargo test -p audiobookify-desktop sidecars`
Expected: PASS.

- [ ] **Step 3: Start the runner thread**

In `desktop/src/main.rs`, add:

```rust
use audiobookify_core::audio::child_path;
use audiobookify_core::db::Database;
use audiobookify_core::engine::PythonEngine;
use audiobookify_core::jobs::{Paths, Runner};
use std::sync::atomic::AtomicBool;

/// Generation, on its own thread with its own DB connection. It owns the Python engine
/// worker child, whose stdin closes when this process exits, however it exits; the
/// worker then finishes its chunk and quits. A job cut off mid-chapter is requeued by
/// `reset_stale_generating_jobs` on the next launch, as before.
fn start_runner() {
    let repo = repo_root().to_path_buf();
    std::thread::Builder::new()
        .name("job-runner".into())
        .spawn(move || {
            let db = match Database::open(repo.join("audiobookify.db"), repo.join("voices")) {
                Ok(db) => db,
                Err(e) => return eprintln!("Error: the job runner could not open the database: {e}"),
            };
            let engine = PythonEngine::new(repo.join(".venv/bin/python"), &repo)
                .env("HF_HUB_OFFLINE", "1")
                .env("PATH", child_path(std::env::var_os("PATH")));
            let paths = Paths {
                cache_root: repo.join(".cache/synth"),
                text_clip_dir: repo.join("text_clips"),
                backup_dir: repo.join("output"),
            };
            Runner::new(db, engine, paths).run_forever(&AtomicBool::new(false));
        })
        .expect("spawn the job runner thread");
}
```

In `main()`, start it right after the sidecars are ready. FastAPI has then already created and migrated the DB:

```rust
fn main() {
    let sc = start_sidecars().unwrap_or_else(|e| fail(&e));
    start_runner();
    // ... unchanged
```

- [ ] **Step 4: Build and smoke-test with the fake engine**

Run (in `desktop/`): `cargo build && cargo test`
Expected: builds; all tests PASS.

Then smoke-test the wiring. The runner can't be pointed at a fake engine from the UI, so check that the worker sidecar is gone and the runner picks up work:
1. `cargo tauri dev`.
2. `ps -ef | grep -E "audiobookify (worker|\.engine_worker)" | grep -v grep` shows no `audiobookify worker`. `audiobookify.engine_worker` appears only after the first job starts.
3. In the app, queue a short text clip with the Speed (Turbo) engine. It finishes and plays.
4. Quit the app. Within about 30 s, `ps` shows no `engine_worker` (it exits after its current chunk on stdin EOF).

- [ ] **Step 5: Commit**

```bash
git add desktop/Cargo.toml desktop/Cargo.lock desktop/src/sidecars.rs desktop/src/main.rs
git commit -m "Run generation in Rust: drop the Python worker sidecar, start the job runner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Exit check: real engines and the listening check

**Files:**
- Modify: `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md` (mark phase 3 done, add "Notes from phase 3")

This task is manual and needs the user for the listening part.

- [ ] **Step 1: Generate a full book with each engine**

In `cargo tauri dev`, pick a short book that already has chapters generated by the Python worker. Copy its existing MP3s aside first, because regenerating overwrites them. Then:
1. Set the book to Quality (Qwen3) with a voice that has a transcript, and regenerate every chapter.
2. Set a second short book (or the same one after copying its MP3s again) to Speed (Turbo) and regenerate every chapter.

Expected: all chapters reach `done`. Pause and resume one chapter mid-way; it resumes without redoing chunks (watch the progress bar continue from where it stopped).

- [ ] **Step 2: Listening check (user)**

Ask the user to compare the Rust-generated chapters with the copied Python-generated ones: pause lengths between sentences and paragraphs, no clicks at joins, even levels, and the tags in a player. Record the verdict.

- [ ] **Step 3: Update the parent spec**

In `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md`, add a section after "Notes from phase 0":

```markdown
## Notes from phase 3

- The Tauri app runs generation in Rust (`audiobookify_core::jobs::Runner`); `make up` still runs the Python worker until phase 6. Don't run both against the same DB at once (two engines loaded).
- Engine WAVs are float32; Python's chunk cache was 16-bit. A chapter paused under the Python worker regenerates once under Rust (cache keys differ).
- Still to move in phase 4: `relabel_chapter_mp3` (chapter rename) and wav2vec2 phonemes (`phonemes` request), both unused by the runner.
- Fake-engine test knobs (`AUDIOBOOKIFY_FAKE_*`) live on `FakeEngine` and `engine_worker`; the Rust runner tests rely on them.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-10-14-tauri-desktop-design.md
git commit -m "Record phase 3 exit check and notes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

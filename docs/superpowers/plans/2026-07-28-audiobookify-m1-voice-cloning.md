# Audiobookify M1 — Voice Cloning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the M0 CLI so a user can import a reference clip as a named, persisted **Voice Profile** (with validation warnings per spec §4.4), preview a short sentence in that voice before committing to a full book, and generate a whole book using that cloned voice — via `audiobookify voice add/list/preview` and `audiobookify generate --voice-profile`.

**Architecture:** A new `voice/validation.py` module does pure, dependency-free audio-quality checks (reusing `soundfile`+`numpy`, already installed) and returns structured warnings rather than raising for anything but truly unusable input. `VoiceProfile` is a new dataclass persisted in the same SQLite database as `Book`/`Chapter` (new `voice_profiles` table, same `Database` class). `ChatterboxEngine.clone_profile()`/`synthesize()` already pass `audio_prompt_path` through to `ChatterboxTTS.generate()` — M1 completes that wiring by also passing `temperature` and by giving the CLI a way to build a real `voice_params` dict from a stored profile instead of only ever synthesizing with no reference audio. The CLI stays the only interface (no GUI yet, per user decision M1 scope).

**Tech Stack:** Same as M0 (Python 3.10+, `soundfile`, `numpy`, stdlib `sqlite3`/`argparse`, `pytest`) — no new dependencies for M1.

## Global Constraints

- Fully local — no cloud calls (spec §1). Reference-audio validation and preview synthesis run entirely on-device.
- **No Whisper / auto-transcription in M1** (user decision, 2026-07-28): the installed `chatterbox-tts==0.1.7`'s `ChatterboxTTS.generate()` has no reference-text parameter — it's zero-shot on `audio_prompt_path` alone, so a transcript has no effect on cloning quality. `VoiceProfile.reference_transcript` is an optional user-supplied string only. Revisit if a future engine adapter actually consumes it.
- **Preview writes a file and prints its path; no auto-playback** (user decision, 2026-07-28). Real in-app playback arrives with the GUI (M4).
- Validation per spec §4.4: check sample rate, length, mono/stereo; **warn** (don't block) on clipping/background noise and on stereo/off-recommended-length; only a truly unusable clip (under 1 second, or unreadable) is a hard failure.
- `VoiceProfile` fields per spec §6: `id, name, engine, reference_audio_path, reference_transcript, params{exaggeration, cfg, temperature}, created_at`. (Spec also lists `speed` as an example param — the installed Chatterbox API has no such parameter, so it's omitted; don't fabricate a knob the engine can't use.)
- Do not wire `Book.voice_profile_id` in M1 — the CLI selects a profile per `generate` invocation via `--voice-profile NAME`; persisting a profile association on the book row is unneeded complexity for a personal CLI tool (YAGNI) and can be added later if the GUI needs it.
- Personal use only — no dependency license restrictions (prior decision).
- Follow existing CLI conventions exactly: resolve paths to absolute before storing (`cli.py`'s existing `Path(...).resolve()` pattern), print errors to `stderr` prefixed `"Error: "` and exit 1, print warnings to `stderr` prefixed `"Warning: "`, `ChatterboxEngine` is imported into `cli` module namespace so tests can `monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)`.
- Out of scope for M1 (later milestones per spec §9): pronunciation lexicon (M2), audio→phoneme extraction (M3), GUI (M4), loudness normalization / splice fallback (M5).

---

## File Structure

```
audiobookify/
  src/audiobookify/
    models.py                # + VoiceProfile dataclass
    db.py                    # + voice_profiles table, create/find/list_voice_profile(s)
    voice/
      __init__.py             # new
      validation.py            # new: validate_reference_audio()
    engines/
      chatterbox.py             # + temperature passthrough in synthesize()
    cli.py                      # + `voice add|list|preview` subcommands, `generate --voice-profile`
  tests/
    test_db.py                 # + voice profile persistence tests
    test_voice_validation.py    # new
    test_engines_chatterbox.py  # + param-passthrough test
    test_cli.py                 # + voice subcommand + --voice-profile tests
  README.md                    # + M1 usage docs
```

---

### Task 1: `VoiceProfile` model + SQLite persistence

**Files:**
- Modify: `src/audiobookify/models.py`
- Modify: `src/audiobookify/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: existing `Database` class structure and `SCHEMA` string (`src/audiobookify/db.py`).
- Produces: `VoiceProfile` dataclass in `audiobookify.models`; `Database.create_voice_profile(name, engine, reference_audio_path, reference_transcript, exaggeration, cfg_weight, temperature) -> VoiceProfile`, `Database.find_voice_profile_by_name(name) -> Optional[VoiceProfile]`, `Database.list_voice_profiles() -> List[VoiceProfile]`. Task 4 (CLI `voice add/list`) and Task 6 (CLI `generate --voice-profile`) call these directly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_create_and_find_voice_profile_by_name(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    profile = db.create_voice_profile(
        name="narrator-1", engine="chatterbox", reference_audio_path="/refs/clip.wav",
        reference_transcript="Hello there.", exaggeration=0.6, cfg_weight=0.4, temperature=0.75,
    )
    assert profile.id is not None
    assert profile.name == "narrator-1"

    found = db.find_voice_profile_by_name("narrator-1")
    assert found is not None
    assert found.reference_audio_path == "/refs/clip.wav"
    assert found.reference_transcript == "Hello there."
    assert found.exaggeration == 0.6
    assert found.cfg_weight == 0.4
    assert found.temperature == 0.75
    assert db.find_voice_profile_by_name("nope") is None
    db.close()


def test_list_voice_profiles_returns_all_in_creation_order(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_voice_profile(name="a", engine="chatterbox", reference_audio_path="/a.wav",
                             reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8)
    db.create_voice_profile(name="b", engine="chatterbox", reference_audio_path="/b.wav",
                             reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8)

    profiles = db.list_voice_profiles()
    assert [p.name for p in profiles] == ["a", "b"]
    db.close()


def test_create_voice_profile_rejects_duplicate_name(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_voice_profile(name="dup", engine="chatterbox", reference_audio_path="/a.wav",
                             reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8)

    with pytest.raises(ValueError, match="dup"):
        db.create_voice_profile(name="dup", engine="chatterbox", reference_audio_path="/b.wav",
                                 reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8)
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v -k voice_profile`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'create_voice_profile'`

- [ ] **Step 3: Add `VoiceProfile` to `src/audiobookify/models.py`**

Append to `src/audiobookify/models.py`:

```python
@dataclass
class VoiceProfile:
    id: int
    name: str
    engine: str
    reference_audio_path: str
    reference_transcript: Optional[str]
    exaggeration: float
    cfg_weight: float
    temperature: float
    created_at: float
```

- [ ] **Step 4: Add the `voice_profiles` table and CRUD methods to `src/audiobookify/db.py`**

Change the import line at the top:

```python
from .models import Book, Chapter, ChapterStatus, SourceFormat, VoiceProfile
```

Append to the `SCHEMA` string, inside the triple-quoted block, after the `chapters` table definition and before the closing `"""`:

```python

CREATE TABLE IF NOT EXISTS voice_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    engine TEXT NOT NULL,
    reference_audio_path TEXT NOT NULL,
    reference_transcript TEXT,
    exaggeration REAL NOT NULL,
    cfg_weight REAL NOT NULL,
    temperature REAL NOT NULL,
    created_at REAL NOT NULL
);
```

Add a row-mapping helper next to `_row_to_chapter`:

```python
def _row_to_voice_profile(row: sqlite3.Row) -> VoiceProfile:
    return VoiceProfile(
        id=row["id"], name=row["name"], engine=row["engine"],
        reference_audio_path=row["reference_audio_path"],
        reference_transcript=row["reference_transcript"],
        exaggeration=row["exaggeration"], cfg_weight=row["cfg_weight"],
        temperature=row["temperature"], created_at=row["created_at"],
    )
```

Add these methods to the `Database` class (e.g. after `close`, or anywhere inside the class body):

```python
    def create_voice_profile(self, name: str, engine: str, reference_audio_path: str,
                              reference_transcript: Optional[str], exaggeration: float,
                              cfg_weight: float, temperature: float) -> VoiceProfile:
        try:
            self.conn.execute(
                "INSERT INTO voice_profiles (name, engine, reference_audio_path, reference_transcript, "
                "exaggeration, cfg_weight, temperature, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, engine, reference_audio_path, reference_transcript, exaggeration, cfg_weight,
                 temperature, time.time()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"a voice profile named '{name}' already exists")
        self.conn.commit()
        return self.find_voice_profile_by_name(name)

    def find_voice_profile_by_name(self, name: str) -> Optional[VoiceProfile]:
        row = self.conn.execute("SELECT * FROM voice_profiles WHERE name = ?", (name,)).fetchone()
        return _row_to_voice_profile(row) if row else None

    def list_voice_profiles(self) -> List[VoiceProfile]:
        rows = self.conn.execute("SELECT * FROM voice_profiles ORDER BY id").fetchall()
        return [_row_to_voice_profile(row) for row in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: all tests pass (8 total: 5 existing + 3 new)

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/models.py src/audiobookify/db.py tests/test_db.py
git commit -m "feat: VoiceProfile model and SQLite persistence"
```

---

### Task 2: Reference audio validation

**Files:**
- Create: `src/audiobookify/voice/__init__.py`
- Create: `src/audiobookify/voice/validation.py`
- Test: `tests/test_voice_validation.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure function over a file path, using already-installed `soundfile`/`numpy`).
- Produces: `ReferenceAudioInfo` dataclass (`path`, `sample_rate`, `channels`, `duration_seconds`, `warnings: List[str]`) and `validate_reference_audio(path: str) -> ReferenceAudioInfo` in `audiobookify.voice.validation`. Raises `ValueError` if the clip is unusably short. Task 4 (CLI `voice add`) calls this directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voice_validation.py`:

```python
import numpy as np
import pytest
import soundfile as sf

from audiobookify.voice.validation import validate_reference_audio


def _write_wav(path, samples, sample_rate, channels=1):
    if channels > 1:
        samples = np.tile(samples.reshape(-1, 1), (1, channels))
    sf.write(str(path), samples, sample_rate)


def _clean_tone(total_duration_s, sample_rate=22050, amplitude=0.6):
    """A tone followed by a silent tail (~10% of the clip) - like real speech
    with a pause, so the noise-floor check has a genuine quiet passage to find."""
    n_total = int(total_duration_s * sample_rate)
    n_silence = int(n_total * 0.1)
    n_tone = n_total - n_silence
    t = np.linspace(0, n_tone / sample_rate, n_tone, endpoint=False)
    tone = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    silence = np.zeros(n_silence, dtype=np.float32)
    return np.concatenate([tone, silence])


def test_validate_reference_audio_reports_rate_channels_and_duration(tmp_path):
    path = tmp_path / "clean.wav"
    samples = _clean_tone(15.0)
    _write_wav(path, samples, 22050)

    info = validate_reference_audio(str(path))

    assert info.sample_rate == 22050
    assert info.channels == 1
    assert info.duration_seconds == pytest.approx(15.0, rel=1e-3)
    assert info.warnings == []


def test_validate_reference_audio_raises_for_too_short_clip(tmp_path):
    path = tmp_path / "tiny.wav"
    _write_wav(path, np.zeros(int(0.3 * 22050), dtype=np.float32), 22050)

    with pytest.raises(ValueError, match="too short"):
        validate_reference_audio(str(path))


def test_validate_reference_audio_warns_when_shorter_than_recommended(tmp_path):
    path = tmp_path / "short.wav"
    _write_wav(path, _clean_tone(5.0), 22050)

    info = validate_reference_audio(str(path))

    assert any("shorter than the recommended" in w for w in info.warnings)


def test_validate_reference_audio_warns_when_longer_than_recommended(tmp_path):
    path = tmp_path / "long.wav"
    _write_wav(path, _clean_tone(40.0), 22050)

    info = validate_reference_audio(str(path))

    assert any("longer than the recommended" in w for w in info.warnings)


def test_validate_reference_audio_warns_on_stereo(tmp_path):
    path = tmp_path / "stereo.wav"
    _write_wav(path, _clean_tone(15.0), 22050, channels=2)

    info = validate_reference_audio(str(path))

    assert info.channels == 2
    assert any("channels" in w for w in info.warnings)


def test_validate_reference_audio_warns_on_clipping(tmp_path):
    path = tmp_path / "clipped.wav"
    samples = _clean_tone(15.0)
    samples[:1000] = 1.0  # force a run of full-scale samples
    _write_wav(path, samples, 22050)

    info = validate_reference_audio(str(path))

    assert any("clipping" in w for w in info.warnings)


def test_validate_reference_audio_warns_on_constant_background_noise(tmp_path):
    path = tmp_path / "noisy.wav"
    rng = np.random.default_rng(42)
    samples = rng.normal(0, 0.05, int(15.0 * 22050)).astype(np.float32)
    _write_wav(path, samples, 22050)

    info = validate_reference_audio(str(path))

    assert any("background noise" in w for w in info.warnings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_voice_validation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobookify.voice'`

- [ ] **Step 3: Write `src/audiobookify/voice/__init__.py`**

```python
```

- [ ] **Step 4: Write `src/audiobookify/voice/validation.py`**

```python
from dataclasses import dataclass, field
from typing import List

import numpy as np
import soundfile as sf

ABSOLUTE_MIN_SECONDS = 1.0
RECOMMENDED_MIN_SECONDS = 10.0
RECOMMENDED_MAX_SECONDS = 30.0

CLIP_AMPLITUDE = 0.999
CLIP_FRACTION_WARN = 0.001

FRAME_SECONDS = 0.02
SILENCE_RMS_THRESHOLD = 0.02
SILENCE_FRACTION_MIN = 0.02


@dataclass
class ReferenceAudioInfo:
    path: str
    sample_rate: int
    channels: int
    duration_seconds: float
    warnings: List[str] = field(default_factory=list)


def _quiet_frame_fraction(mono: np.ndarray, sample_rate: int) -> float:
    frame_len = max(1, int(FRAME_SECONDS * sample_rate))
    n_frames = len(mono) // frame_len
    if n_frames == 0:
        return 1.0  # too short to frame - don't false-alarm on noise
    frames = mono[: n_frames * frame_len].reshape(n_frames, frame_len)
    frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return float(np.mean(frame_rms < SILENCE_RMS_THRESHOLD))


def validate_reference_audio(path: str) -> ReferenceAudioInfo:
    data, sample_rate = sf.read(path, always_2d=True, dtype="float32")
    channels = data.shape[1]
    duration_seconds = len(data) / sample_rate

    if duration_seconds < ABSOLUTE_MIN_SECONDS:
        raise ValueError(
            f"reference audio is too short to clone from ({duration_seconds:.2f}s); "
            f"need at least {ABSOLUTE_MIN_SECONDS:.0f}s"
        )

    warnings: List[str] = []
    if duration_seconds < RECOMMENDED_MIN_SECONDS:
        warnings.append(
            f"reference audio is {duration_seconds:.1f}s, shorter than the recommended "
            f"{RECOMMENDED_MIN_SECONDS:.0f}-{RECOMMENDED_MAX_SECONDS:.0f}s window"
        )
    elif duration_seconds > RECOMMENDED_MAX_SECONDS:
        warnings.append(
            f"reference audio is {duration_seconds:.1f}s, longer than the recommended "
            f"{RECOMMENDED_MIN_SECONDS:.0f}-{RECOMMENDED_MAX_SECONDS:.0f}s window"
        )

    if channels > 1:
        warnings.append(f"reference audio has {channels} channels; mono is recommended")

    mono = data.mean(axis=1)

    clipped_fraction = float(np.mean(np.abs(mono) >= CLIP_AMPLITUDE))
    if clipped_fraction > CLIP_FRACTION_WARN:
        warnings.append(
            f"possible clipping detected ({clipped_fraction * 100:.2f}% of samples at peak amplitude)"
        )

    # ponytail: crude quiet-frame heuristic (no sustained quiet passage found =
    # probable constant background noise); swap for a spectral noise estimate
    # if this produces false positives on real recordings.
    if _quiet_frame_fraction(mono, sample_rate) < SILENCE_FRACTION_MIN:
        warnings.append("no quiet passages detected; recording may have constant background noise")

    return ReferenceAudioInfo(
        path=path, sample_rate=sample_rate, channels=channels,
        duration_seconds=duration_seconds, warnings=warnings,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_voice_validation.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/voice tests/test_voice_validation.py
git commit -m "feat: reference audio validation for voice cloning"
```

---

### Task 3: Pass `temperature` through to Chatterbox synthesis

**Files:**
- Modify: `src/audiobookify/engines/chatterbox.py`
- Test: `tests/test_engines_chatterbox.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: no signature changes — `ChatterboxEngine.synthesize()`'s `params` dict now also honors a `"temperature"` key (default `0.8`, matching `ChatterboxTTS.generate()`'s own default). `clone_profile()`'s existing behavior (passing `reference_audio` through as `audio_prompt_path`) is unchanged but is now covered by a test — this is the "wiring real cloning" the M1 brief calls out as needing verification, not a new code path.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engines_chatterbox.py` (add `import numpy as np` to the existing imports at the top):

```python
def test_chatterbox_engine_passes_reference_audio_and_params_to_generate():
    engine = ChatterboxEngine()
    engine.model = MagicMock()
    engine.model.generate.return_value.squeeze.return_value.cpu.return_value.numpy.return_value = (
        np.zeros(5, dtype=np.float32)
    )

    handle = engine.clone_profile("/refs/narrator.wav", "transcript text", {})
    audio = engine.synthesize(
        "Hello there.", handle,
        {"exaggeration": 0.7, "cfg": 0.3, "temperature": 0.9},
    )

    assert isinstance(audio, np.ndarray)
    engine.model.generate.assert_called_once_with(
        "Hello there.",
        audio_prompt_path="/refs/narrator.wav",
        exaggeration=0.7,
        cfg_weight=0.3,
        temperature=0.9,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_engines_chatterbox.py -v -k passes_reference_audio_and_params`
Expected: FAIL — `assert_called_once_with` mismatch because `temperature` is missing from the actual call

- [ ] **Step 3: Add `temperature` to the `generate()` call in `src/audiobookify/engines/chatterbox.py`**

In `synthesize()`, change:

```python
        wav = self.model.generate(
            text_chunk,
            audio_prompt_path=audio_prompt_path,
            exaggeration=params.get("exaggeration", 0.5),
            cfg_weight=params.get("cfg", 0.5),
        )
```

to:

```python
        wav = self.model.generate(
            text_chunk,
            audio_prompt_path=audio_prompt_path,
            exaggeration=params.get("exaggeration", 0.5),
            cfg_weight=params.get("cfg", 0.5),
            temperature=params.get("temperature", 0.8),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_engines_chatterbox.py -v`
Expected: all tests pass (5 total, `integration` test deselected)

- [ ] **Step 5: Commit**

```bash
git add src/audiobookify/engines/chatterbox.py tests/test_engines_chatterbox.py
git commit -m "feat: pass temperature param through to Chatterbox synthesis"
```

---

### Task 4: CLI `voice add` and `voice list`

**Files:**
- Modify: `src/audiobookify/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `validate_reference_audio` (Task 2), `Database.create_voice_profile`/`list_voice_profiles` (Task 1), `ChatterboxEngine.name` (`"chatterbox"`, Task 3/existing).
- Produces: `audiobookify voice add --name --reference [--transcript] [--exaggeration] [--cfg] [--temperature] [--db]` and `audiobookify voice list [--db]` subcommands. Task 5 (`voice preview`) and Task 6 (`generate --voice-profile`) extend this same `voice` subparser group.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py` (add `import numpy as np` and `import soundfile as sf` to the existing imports at the top):

```python
def _write_reference_wav(path, duration_s=15.0, sample_rate=22050):
    n_total = int(duration_s * sample_rate)
    n_silence = int(n_total * 0.1)
    n_tone = n_total - n_silence
    t = np.linspace(0, n_tone / sample_rate, n_tone, endpoint=False)
    tone = (0.6 * np.sin(2 * np.pi * 220 * t)).astype("float32")
    silence = np.zeros(n_silence, dtype="float32")
    sf.write(str(path), np.concatenate([tone, silence]), sample_rate)


def test_voice_add_creates_profile_and_reports_reference_info(tmp_path, capsys):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Created voice profile 'narrator-1'" in captured.out

    db = Database(str(db_path))
    profile = db.find_voice_profile_by_name("narrator-1")
    assert profile is not None
    assert profile.reference_audio_path == str(ref_path.resolve())
    assert profile.engine == "chatterbox"
    db.close()


def test_voice_add_rejects_too_short_reference(tmp_path, capsys):
    ref_path = tmp_path / "tiny.wav"
    sf.write(str(ref_path), np.zeros(1000, dtype="float32"), 22050)
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["voice", "add", "--name", "tiny", "--reference", str(ref_path), "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "too short" in capsys.readouterr().err


def test_voice_add_rejects_duplicate_name(tmp_path, capsys):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "already exists" in capsys.readouterr().err


def test_voice_list_shows_added_profiles(tmp_path, capsys):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    capsys.readouterr()

    cli.main(["voice", "list", "--db", str(db_path)])

    assert "narrator-1" in capsys.readouterr().out


def test_voice_list_reports_when_empty(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    cli.main(["voice", "list", "--db", str(db_path)])

    assert "No voice profiles yet" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v -k voice_add or voice_list`
Expected: FAIL with `SystemExit` from argparse (`invalid choice: 'voice'`) since the `voice` subcommand doesn't exist yet

- [ ] **Step 3: Rewrite `src/audiobookify/cli.py`**

Replace the full file contents with:

```python
import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .db import Database
from .engines.chatterbox import ChatterboxEngine
from .ingest import epub, markdown
from .models import ChapterStatus, SourceFormat
from .synth.pipeline import generate_book
from .voice.validation import validate_reference_audio


def _ingest(path: str, heading_level: int):
    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        return epub.parse(path), SourceFormat.EPUB
    if suffix == ".md":
        return markdown.parse(path, heading_level=heading_level), SourceFormat.MARKDOWN
    raise ValueError(f"unsupported input format: {suffix}")


def _handle_generate(args) -> None:
    source_path = str(Path(args.input).resolve())
    output_dir = str(Path(args.out).resolve())

    db = Database(args.db)
    book = db.find_book_by_source(source_path)
    if book is None:
        try:
            parsed, source_format = _ingest(source_path, args.heading_level)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        book = db.create_book(
            title=parsed.title, author=parsed.author, source_path=source_path,
            source_format=source_format, output_dir=output_dir,
        )
        db.add_chapters(book.id, parsed.chapters)
    elif book.output_dir != output_dir:
        # Switching output_dir mid-book would orphan the paths already recorded
        # for finished chapters, so keep the original and make the mismatch loud.
        print(
            f"Warning: this book was previously generated to {book.output_dir}; "
            "continuing to use that directory. Pass a fresh --db to start over.",
            file=sys.stderr,
        )

    engine = ChatterboxEngine()
    results = generate_book(book, engine, db, {"voice_name": args.voice or "default"}, device=args.device)
    for chapter in results:
        print(f"{chapter.status.value}: {chapter.title} -> {chapter.output_mp3_path}")

    failed = [c for c in results if c.status == ChapterStatus.FAILED]
    for chapter in failed:
        print(f"Error: chapter '{chapter.title}' failed: {chapter.error}", file=sys.stderr)

    db.close()
    if failed:
        sys.exit(1)


def _handle_voice_add(args) -> None:
    reference_path = str(Path(args.reference).resolve())
    try:
        info = validate_reference_audio(reference_path)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    for warning in info.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    db = Database(args.db)
    try:
        profile = db.create_voice_profile(
            name=args.name, engine=ChatterboxEngine.name, reference_audio_path=reference_path,
            reference_transcript=args.transcript, exaggeration=args.exaggeration,
            cfg_weight=args.cfg, temperature=args.temperature,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)

    print(
        f"Created voice profile '{profile.name}' ({info.duration_seconds:.1f}s, "
        f"{info.sample_rate} Hz, {info.channels}ch reference)"
    )
    db.close()


def _handle_voice_list(args) -> None:
    db = Database(args.db)
    profiles = db.list_voice_profiles()
    if not profiles:
        print("No voice profiles yet. Add one with 'audiobookify voice add'.")
    for profile in profiles:
        print(
            f"{profile.name}\t{profile.reference_audio_path}\t"
            f"exaggeration={profile.exaggeration} cfg={profile.cfg_weight} "
            f"temperature={profile.temperature}"
        )
    db.close()


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="audiobookify")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Generate an audiobook from a book file")
    generate_parser.add_argument("--input", required=True)
    generate_parser.add_argument("--out", required=True)
    generate_parser.add_argument("--voice", default=None)
    generate_parser.add_argument("--device", default="auto")
    generate_parser.add_argument("--db", default="audiobookify.db")
    generate_parser.add_argument("--heading-level", type=int, default=1)

    voice_parser = subparsers.add_parser("voice", help="Manage voice profiles")
    voice_subparsers = voice_parser.add_subparsers(dest="voice_command", required=True)

    voice_add_parser = voice_subparsers.add_parser(
        "add", help="Import a reference clip as a named voice profile"
    )
    voice_add_parser.add_argument("--name", required=True)
    voice_add_parser.add_argument("--reference", required=True)
    voice_add_parser.add_argument("--transcript", default=None)
    voice_add_parser.add_argument("--exaggeration", type=float, default=0.5)
    voice_add_parser.add_argument("--cfg", type=float, default=0.5)
    voice_add_parser.add_argument("--temperature", type=float, default=0.8)
    voice_add_parser.add_argument("--db", default="audiobookify.db")

    voice_list_parser = voice_subparsers.add_parser("list", help="List saved voice profiles")
    voice_list_parser.add_argument("--db", default="audiobookify.db")

    args = parser.parse_args(argv)

    if args.command == "generate":
        _handle_generate(args)
    elif args.command == "voice":
        if args.voice_command == "add":
            _handle_voice_add(args)
        elif args.voice_command == "list":
            _handle_voice_list(args)


if __name__ == "__main__":
    main()
```

Note: `--voice` changes from `default="default"` to `default=None`, with the fallback moved inline (`args.voice or "default"`) — this keeps `generate`'s existing behavior byte-for-byte identical when `--voice-profile` doesn't exist yet (added in Task 6), while letting Task 6 later default the narrator name to the voice profile's name instead of the literal string `"default"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all tests pass (11 total: 6 existing + 5 new)

- [ ] **Step 5: Commit**

```bash
git add src/audiobookify/cli.py tests/test_cli.py
git commit -m "feat: audiobookify voice add/list CLI commands"
```

---

### Task 5: CLI `voice preview`

**Files:**
- Modify: `src/audiobookify/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Database.find_voice_profile_by_name` (Task 1), `ChatterboxEngine`/`FakeEngine`'s `load`/`clone_profile`/`synthesize`/`sample_rate`/`teardown` (existing `TTSEngine` interface).
- Produces: `audiobookify voice preview --name --text [--out] [--device] [--db]` subcommand — synthesizes `--text` with the named profile's reference audio and params, writes a WAV file, and prints its path (per user decision: no auto-playback in M1).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_voice_preview_writes_audio_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"
    out_path = tmp_path / "preview.wav"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    cli.main([
        "voice", "preview", "--name", "narrator-1", "--text", "Hello there, listener.",
        "--out", str(out_path), "--db", str(db_path),
    ])

    assert out_path.exists()
    data, _sr = sf.read(str(out_path))
    assert len(data) > 0


def test_voice_preview_defaults_output_filename_to_profile_name(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)
    monkeypatch.chdir(tmp_path)

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    cli.main(["voice", "preview", "--name", "narrator-1", "--text", "Hi.", "--db", str(db_path)])

    assert (tmp_path / "narrator-1-preview.wav").exists()


def test_voice_preview_reports_missing_profile(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["voice", "preview", "--name", "ghost", "--text", "hi", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "no voice profile named 'ghost'" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v -k voice_preview`
Expected: FAIL with `SystemExit` from argparse (`invalid choice: 'preview'`)

- [ ] **Step 3: Rewrite `src/audiobookify/cli.py`**

Add `import soundfile as sf` to the imports at the top (after `from .voice.validation import validate_reference_audio`):

```python
import soundfile as sf
```

Add this handler function after `_handle_voice_list`:

```python
def _handle_voice_preview(args) -> None:
    db = Database(args.db)
    profile = db.find_voice_profile_by_name(args.name)
    db.close()
    if profile is None:
        print(f"Error: no voice profile named '{args.name}'", file=sys.stderr)
        sys.exit(1)

    engine = ChatterboxEngine()
    engine.load(args.device)
    try:
        handle = engine.clone_profile(profile.reference_audio_path, profile.reference_transcript, {})
        audio = engine.synthesize(
            args.text, handle,
            {
                "exaggeration": profile.exaggeration,
                "cfg": profile.cfg_weight,
                "temperature": profile.temperature,
            },
        )
        out_path = args.out or f"{profile.name}-preview.wav"
        sf.write(out_path, audio, engine.sample_rate())
    finally:
        engine.teardown()

    print(f"Wrote preview to {out_path}")
```

Add the new subparser in `main()`, after `voice_list_parser`'s definition:

```python
    voice_preview_parser = voice_subparsers.add_parser(
        "preview", help="Synthesize a sample sentence with a saved voice profile"
    )
    voice_preview_parser.add_argument("--name", required=True)
    voice_preview_parser.add_argument("--text", required=True)
    voice_preview_parser.add_argument("--out", default=None)
    voice_preview_parser.add_argument("--device", default="auto")
    voice_preview_parser.add_argument("--db", default="audiobookify.db")
```

And add the dispatch branch in the `elif args.command == "voice":` block:

```python
        elif args.voice_command == "preview":
            _handle_voice_preview(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all tests pass (14 total)

- [ ] **Step 5: Commit**

```bash
git add src/audiobookify/cli.py tests/test_cli.py
git commit -m "feat: audiobookify voice preview CLI command"
```

---

### Task 6: Wire `generate --voice-profile` into real cloning

**Files:**
- Modify: `src/audiobookify/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Database.find_voice_profile_by_name` (Task 1), existing `generate_book(book, engine, db, voice_params, device)` (unchanged signature from M0).
- Produces: `audiobookify generate ... --voice-profile NAME` — looks up the named profile and builds a `voice_params` dict with `reference_audio`/`reference_text`/`exaggeration`/`cfg`/`temperature` so `ChatterboxEngine.clone_profile()`/`synthesize()` actually receive the cloned reference. Without `--voice-profile`, `generate` behaves exactly as in M0.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
@requires_ffmpeg
def test_generate_command_uses_voice_profile_reference_and_params(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main([
        "voice", "add", "--name", "narrator-1", "--reference", str(ref_path),
        "--exaggeration", "0.9", "--db", str(db_path),
    ])

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    cli.main([
        "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
        "--voice-profile", "narrator-1",
    ])

    from mutagen.mp3 import MP3
    tags = MP3(str(out_dir / "01 - Chapter One.mp3"))
    assert str(tags.get("TPE1")) == "narrator-1"  # narrator tag defaults to the profile name


@requires_ffmpeg
def test_generate_command_voice_flag_overrides_profile_name_as_narrator_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    cli.main([
        "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
        "--voice-profile", "narrator-1", "--voice", "Custom Narrator Name",
    ])

    from mutagen.mp3 import MP3
    tags = MP3(str(out_dir / "01 - Chapter One.mp3"))
    assert str(tags.get("TPE1")) == "Custom Narrator Name"


@requires_ffmpeg
def test_generate_command_reports_missing_voice_profile(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
            "--voice-profile", "ghost",
        ])

    assert exc_info.value.code == 1
    assert "no voice profile named 'ghost'" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v -k voice_profile`
Expected: FAIL with `SystemExit` from argparse (`unrecognized arguments: --voice-profile`)

- [ ] **Step 3: Modify `_handle_generate` and `main()` in `src/audiobookify/cli.py`**

Replace `_handle_generate` with:

```python
def _handle_generate(args) -> None:
    source_path = str(Path(args.input).resolve())
    output_dir = str(Path(args.out).resolve())

    db = Database(args.db)
    book = db.find_book_by_source(source_path)
    if book is None:
        try:
            parsed, source_format = _ingest(source_path, args.heading_level)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        book = db.create_book(
            title=parsed.title, author=parsed.author, source_path=source_path,
            source_format=source_format, output_dir=output_dir,
        )
        db.add_chapters(book.id, parsed.chapters)
    elif book.output_dir != output_dir:
        # Switching output_dir mid-book would orphan the paths already recorded
        # for finished chapters, so keep the original and make the mismatch loud.
        print(
            f"Warning: this book was previously generated to {book.output_dir}; "
            "continuing to use that directory. Pass a fresh --db to start over.",
            file=sys.stderr,
        )

    profile = None
    if args.voice_profile:
        profile = db.find_voice_profile_by_name(args.voice_profile)
        if profile is None:
            print(f"Error: no voice profile named '{args.voice_profile}'", file=sys.stderr)
            db.close()
            sys.exit(1)

    voice_name = args.voice or (profile.name if profile else "default")
    voice_params = {"voice_name": voice_name}
    if profile:
        voice_params.update({
            "reference_audio": profile.reference_audio_path,
            "reference_text": profile.reference_transcript,
            "exaggeration": profile.exaggeration,
            "cfg": profile.cfg_weight,
            "temperature": profile.temperature,
        })

    engine = ChatterboxEngine()
    results = generate_book(book, engine, db, voice_params, device=args.device)
    for chapter in results:
        print(f"{chapter.status.value}: {chapter.title} -> {chapter.output_mp3_path}")

    failed = [c for c in results if c.status == ChapterStatus.FAILED]
    for chapter in failed:
        print(f"Error: chapter '{chapter.title}' failed: {chapter.error}", file=sys.stderr)

    db.close()
    if failed:
        sys.exit(1)
```

Add one line to the `generate_parser` argument block in `main()`, after `generate_parser.add_argument("--voice", default=None)`:

```python
    generate_parser.add_argument("--voice-profile", default=None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all tests pass (17 total)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all tests pass (integration-marked tests deselected by default)

- [ ] **Step 6: Commit**

```bash
git add src/audiobookify/cli.py tests/test_cli.py
git commit -m "feat: wire generate --voice-profile into real Chatterbox cloning"
```

---

### Task 7: README — M1 usage docs

**Files:**
- Modify: `README.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Update `README.md`**

Replace the line `Voice cloning (` `--voice` `), the pronunciation lexicon, and the GUI are not yet implemented — see ` project-spec.md` §9 for the milestone roadmap.` with:

```markdown
Voice profile management, the pronunciation lexicon, and the GUI are not yet implemented — see `project-spec.md` §9 for the milestone roadmap.

## Voice cloning (M1)

Import a short reference clip (10-30s recommended, clean single-speaker audio) as a named voice profile, preview it, then generate a book with it:

```bash
.venv/bin/audiobookify voice add --name narrator-1 --reference ./narrator.wav
.venv/bin/audiobookify voice list
.venv/bin/audiobookify voice preview --name narrator-1 --text "This is a preview of the voice."
.venv/bin/audiobookify generate --input book.epub --out ./audiobook/ --voice-profile narrator-1
```

- `voice add`: validates the reference clip (sample rate, length, mono/stereo) and prints warnings (not errors) for anything outside the recommended range, clipping, or a lack of quiet passages (a sign of constant background noise). Only an unusably short (<1s) or unreadable file is rejected outright. Optional tuning flags: `--exaggeration` (default 0.5), `--cfg` (default 0.5), `--temperature` (default 0.8) — Chatterbox's own defaults. `--transcript` is stored as optional metadata only; the installed Chatterbox engine's cloning is zero-shot on audio alone and doesn't use it.
- `voice preview`: synthesizes `--text` with the named profile and writes a WAV file (`--out`, defaults to `<name>-preview.wav` in the current directory) — play it in any audio player to judge the voice before generating a whole book.
- `generate --voice-profile NAME`: uses that profile's reference audio and tuning params for the whole book. The ID3 narrator tag defaults to the profile name (override with `--voice`). Without `--voice-profile`, `generate` behaves exactly as in M0 (Chatterbox's built-in default voice, no cloning).
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: M1 voice cloning usage instructions"
```

---

### Task 8: Final-review fix wave

**Context:** the whole-branch review of Tasks 1-7 found no Critical issues but several Important/Minor findings. This task addresses all of them in one pass, per user decision (2026-07-28) on the one design question the review raised: **persist `Book.voice_profile_id`** (reversing M1's original "don't wire it" call) so that switching voices mid-book is caught and handled, rather than silently mixing voices across chapters.

**Files:**
- Modify: `src/audiobookify/db.py` (two new methods)
- Modify: `src/audiobookify/cli.py` (full replacement)
- Modify: `src/audiobookify/voice/validation.py` (add a sample-rate warning)
- Modify: `README.md` (restore "sample rate" to the validated-checks list; minor clarifications)
- Test: `tests/test_db.py`, `tests/test_cli.py`, `tests/test_voice_validation.py`
- Also: `git add` the previously-untracked plan file at `docs/superpowers/plans/2026-07-28-audiobookify-m1-voice-cloning.md` (this file) alongside these changes — it was never committed, unlike the M0 plan.

**Interfaces:**
- Produces: `Database.update_book_voice_profile(book_id: int, voice_profile_id: Optional[int]) -> None`, `Database.get_voice_profile_by_id(voice_profile_id: int) -> Optional[VoiceProfile]`.

- [ ] **Step 1: Write the failing db tests**

Append to `tests/test_db.py`:

```python
def test_update_book_voice_profile_persists(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    profile = db.create_voice_profile(
        name="narrator-1", engine="chatterbox", reference_audio_path="/refs/clip.wav",
        reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8,
    )

    db.update_book_voice_profile(book.id, profile.id)

    reloaded = db.get_book(book.id)
    assert reloaded.voice_profile_id == profile.id
    db.close()


def test_get_voice_profile_by_id(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    profile = db.create_voice_profile(
        name="narrator-1", engine="chatterbox", reference_audio_path="/refs/clip.wav",
        reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8,
    )

    found = db.get_voice_profile_by_id(profile.id)
    assert found is not None
    assert found.name == "narrator-1"
    assert db.get_voice_profile_by_id(999999) is None
    db.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v -k "voice_profile_persists or voice_profile_by_id"`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'update_book_voice_profile'`

- [ ] **Step 3: Add the two methods to `src/audiobookify/db.py`**

Add these methods to the `Database` class (anywhere in the class body, e.g. near the other voice-profile methods):

```python
    def update_book_voice_profile(self, book_id: int, voice_profile_id: Optional[int]) -> None:
        self.conn.execute(
            "UPDATE books SET voice_profile_id = ? WHERE id = ?",
            (voice_profile_id, book_id),
        )
        self.conn.commit()

    def get_voice_profile_by_id(self, voice_profile_id: int) -> Optional[VoiceProfile]:
        row = self.conn.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (voice_profile_id,)
        ).fetchone()
        return _row_to_voice_profile(row) if row else None
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: all pass (10 total: 8 existing + 2 new)

- [ ] **Step 5: Write the failing sample-rate validation test**

Append to `tests/test_voice_validation.py`:

```python
def test_validate_reference_audio_warns_on_low_sample_rate(tmp_path):
    path = tmp_path / "low_rate.wav"
    _write_wav(path, _clean_tone(15.0, sample_rate=8000), 8000)

    info = validate_reference_audio(str(path))

    assert any("sample rate" in w for w in info.warnings)
```

- [ ] **Step 6: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_voice_validation.py -v -k low_sample_rate`
Expected: FAIL — no warning about sample rate is produced today

- [ ] **Step 7: Add the sample-rate check to `src/audiobookify/voice/validation.py`**

Add a new constant near the top, with the others:

```python
RECOMMENDED_MIN_SAMPLE_RATE = 16000
```

Add this check in `validate_reference_audio`, anywhere after `warnings: List[str] = []` is initialized (e.g. right before the duration checks):

```python
    if sample_rate < RECOMMENDED_MIN_SAMPLE_RATE:
        warnings.append(
            f"reference audio sample rate is {sample_rate} Hz, below the recommended "
            f"{RECOMMENDED_MIN_SAMPLE_RATE} Hz minimum"
        )
```

- [ ] **Step 8: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_voice_validation.py -v`
Expected: all pass (9 total: 8 existing + 1 new)

- [ ] **Step 9: Write the failing CLI tests**

Append to `tests/test_cli.py`:

```python
@requires_ffmpeg
def test_generate_command_persists_voice_profile_association_on_first_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    cli.main([
        "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
        "--voice-profile", "narrator-1",
    ])

    db = Database(str(db_path))
    book = db.find_book_by_source(str(md_path.resolve()))
    profile = db.find_voice_profile_by_name("narrator-1")
    assert book.voice_profile_id == profile.id
    db.close()


@requires_ffmpeg
def test_generate_command_uses_original_voice_for_new_chapters_after_profile_mismatch(
    tmp_path, monkeypatch, capsys
):
    ref_path_a = tmp_path / "narrator-a.wav"
    _write_reference_wav(ref_path_a)
    ref_path_b = tmp_path / "narrator-b.wav"
    _write_reference_wav(ref_path_b)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-a", "--reference", str(ref_path_a), "--db", str(db_path)])
    cli.main(["voice", "add", "--name", "narrator-b", "--reference", str(ref_path_b), "--db", str(db_path)])

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n\n# Chapter Two\nBody two.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    class FlakyOnce(FakeEngine):
        def synthesize(self, text_chunk, profile_handle, params):
            if "chapter two" in text_chunk.lower():
                raise RuntimeError("pause before chapter two")
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "ChatterboxEngine", FlakyOnce)
    with pytest.raises(SystemExit):
        cli.main([
            "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
            "--voice-profile", "narrator-a",
        ])
    capsys.readouterr()

    class RecordingEngine(FakeEngine):
        clone_calls = []

        def clone_profile(self, reference_audio, reference_text, params):
            type(self).clone_calls.append(reference_audio)
            return super().clone_profile(reference_audio, reference_text, params)

    monkeypatch.setattr(cli, "ChatterboxEngine", RecordingEngine)
    cli.main([
        "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
        "--voice-profile", "narrator-b",
    ])

    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert "narrator-a" in captured.err
    # chapter two (not yet done at the time of the mismatch) was synthesized
    # with the originally-associated voice (narrator-a), not the requested one
    assert RecordingEngine.clone_calls == [str(ref_path_a.resolve())]


def test_voice_add_rejects_empty_name(tmp_path, capsys):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["voice", "add", "--name", "  ", "--reference", str(ref_path), "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "must not be empty" in capsys.readouterr().err


def test_voice_add_does_not_print_warnings_on_duplicate_name_rejection(tmp_path, capsys):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path, duration_s=3.0)  # short enough to trigger a real warning

    db_path = tmp_path / "test.db"
    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    capsys.readouterr()

    with pytest.raises(SystemExit):
        cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Warning" not in captured.err
    assert "already exists" in captured.err


def test_voice_preview_creates_output_directory_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"
    out_path = tmp_path / "nested" / "dir" / "preview.wav"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    cli.main([
        "voice", "preview", "--name", "narrator-1", "--text", "Hi.",
        "--out", str(out_path), "--db", str(db_path),
    ])

    assert out_path.exists()


@requires_ffmpeg
def test_generate_command_reports_missing_reference_audio(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    ref_path.unlink()  # simulate the user moving/deleting the reference clip

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
            "--voice-profile", "narrator-1",
        ])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "reference audio" in captured.err
    assert "missing" in captured.err
```

Note: `_write_reference_wav` needs a `duration_s` parameter for the new duplicate-name test above — check its current signature in `tests/test_cli.py` (it should already accept `duration_s=15.0` as a default parameter from Task 4; if not, add that parameter, defaulting to `15.0`, without changing any existing call sites).

- [ ] **Step 10: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v -k "persists_voice_profile_association or original_voice_for_new_chapters or rejects_empty_name or does_not_print_warnings or creates_output_directory or reports_missing_reference_audio"`
Expected: FAIL (association/mismatch tests fail because `book.voice_profile_id` is never set today; empty-name/output-dir/missing-reference tests fail because those checks don't exist yet; the warning-ordering test fails because warnings currently print before the duplicate-name check)

- [ ] **Step 11: Replace `src/audiobookify/cli.py` in full**

```python
import argparse
import sys
from pathlib import Path
from typing import List, Optional

import soundfile as sf

from .db import Database
from .engines.chatterbox import ChatterboxEngine
from .ingest import epub, markdown
from .models import ChapterStatus, SourceFormat
from .synth.pipeline import generate_book
from .voice.validation import validate_reference_audio


def _ingest(path: str, heading_level: int):
    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        return epub.parse(path), SourceFormat.EPUB
    if suffix == ".md":
        return markdown.parse(path, heading_level=heading_level), SourceFormat.MARKDOWN
    raise ValueError(f"unsupported input format: {suffix}")


def _voice_params(profile) -> dict:
    return {
        "reference_audio": profile.reference_audio_path,
        "reference_text": profile.reference_transcript,
        "exaggeration": profile.exaggeration,
        "cfg": profile.cfg_weight,
        "temperature": profile.temperature,
    }


def _check_reference_audio_present(profile) -> None:
    if profile is not None and not Path(profile.reference_audio_path).is_file():
        print(
            f"Error: reference audio for voice profile '{profile.name}' is missing: "
            f"{profile.reference_audio_path}",
            file=sys.stderr,
        )
        sys.exit(1)


def _handle_generate(args) -> None:
    source_path = str(Path(args.input).resolve())
    output_dir = str(Path(args.out).resolve())

    db = Database(args.db)
    book = db.find_book_by_source(source_path)
    if book is None:
        try:
            parsed, source_format = _ingest(source_path, args.heading_level)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        book = db.create_book(
            title=parsed.title, author=parsed.author, source_path=source_path,
            source_format=source_format, output_dir=output_dir,
        )
        db.add_chapters(book.id, parsed.chapters)
    elif book.output_dir != output_dir:
        # Switching output_dir mid-book would orphan the paths already recorded
        # for finished chapters, so keep the original and make the mismatch loud.
        print(
            f"Warning: this book was previously generated to {book.output_dir}; "
            "continuing to use that directory. Pass a fresh --db to start over.",
            file=sys.stderr,
        )

    requested_profile = None
    if args.voice_profile:
        requested_profile = db.find_voice_profile_by_name(args.voice_profile)
        if requested_profile is None:
            print(f"Error: no voice profile named '{args.voice_profile}'", file=sys.stderr)
            db.close()
            sys.exit(1)
    requested_profile_id = requested_profile.id if requested_profile else None

    if book.voice_profile_id is None:
        # First time this book has been associated with a voice (or explicitly
        # with none) - lock it in so later runs stay consistent.
        db.update_book_voice_profile(book.id, requested_profile_id)
        profile = requested_profile
    elif book.voice_profile_id != requested_profile_id:
        # A different voice than the one already used for this book's earlier
        # chapters was requested - keep the original for consistency and warn
        # loudly rather than silently mixing voices across chapters.
        original = db.get_voice_profile_by_id(book.voice_profile_id) if book.voice_profile_id else None
        print(
            f"Warning: this book was previously generated with voice profile "
            f"'{original.name if original else 'the default voice'}'; continuing to use that "
            "voice for consistency. Pass a fresh --db to use a different voice.",
            file=sys.stderr,
        )
        profile = original
    else:
        profile = requested_profile

    _check_reference_audio_present(profile)

    voice_name = args.voice or (profile.name if profile else "default")
    voice_params = {"voice_name": voice_name}
    if profile:
        voice_params.update(_voice_params(profile))

    engine = ChatterboxEngine()
    results = generate_book(book, engine, db, voice_params, device=args.device)
    for chapter in results:
        print(f"{chapter.status.value}: {chapter.title} -> {chapter.output_mp3_path}")

    failed = [c for c in results if c.status == ChapterStatus.FAILED]
    for chapter in failed:
        print(f"Error: chapter '{chapter.title}' failed: {chapter.error}", file=sys.stderr)

    db.close()
    if failed:
        sys.exit(1)


def _handle_voice_add(args) -> None:
    if not args.name.strip():
        print("Error: voice profile name must not be empty", file=sys.stderr)
        sys.exit(1)

    reference_path = str(Path(args.reference).resolve())
    try:
        info = validate_reference_audio(reference_path)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    db = Database(args.db)
    try:
        profile = db.create_voice_profile(
            name=args.name, engine=ChatterboxEngine.name, reference_audio_path=reference_path,
            reference_transcript=args.transcript, exaggeration=args.exaggeration,
            cfg_weight=args.cfg, temperature=args.temperature,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)

    for warning in info.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    print(
        f"Created voice profile '{profile.name}' ({info.duration_seconds:.1f}s, "
        f"{info.sample_rate} Hz, {info.channels}ch reference)"
    )
    db.close()


def _handle_voice_list(args) -> None:
    db = Database(args.db)
    profiles = db.list_voice_profiles()
    if not profiles:
        print("No voice profiles yet. Add one with 'audiobookify voice add'.")
    for profile in profiles:
        print(
            f"{profile.name}\t{profile.reference_audio_path}\t"
            f"exaggeration={profile.exaggeration} cfg={profile.cfg_weight} "
            f"temperature={profile.temperature}"
        )
    db.close()


def _handle_voice_preview(args) -> None:
    db = Database(args.db)
    profile = db.find_voice_profile_by_name(args.name)
    db.close()
    if profile is None:
        print(f"Error: no voice profile named '{args.name}'", file=sys.stderr)
        sys.exit(1)

    _check_reference_audio_present(profile)

    engine = ChatterboxEngine()
    try:
        engine.load(args.device)
        handle = engine.clone_profile(profile.reference_audio_path, profile.reference_transcript, {})
        audio = engine.synthesize(args.text, handle, _voice_params(profile))
        out_path = args.out or f"{profile.name}-preview.wav"
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(out_path, audio, engine.sample_rate())
    finally:
        engine.teardown()

    print(f"Wrote preview to {out_path}")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="audiobookify")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Generate an audiobook from a book file")
    generate_parser.add_argument("--input", required=True)
    generate_parser.add_argument("--out", required=True)
    generate_parser.add_argument("--voice", default=None)
    generate_parser.add_argument("--voice-profile", default=None)
    generate_parser.add_argument("--device", default="auto")
    generate_parser.add_argument("--db", default="audiobookify.db")
    generate_parser.add_argument("--heading-level", type=int, default=1)

    voice_parser = subparsers.add_parser("voice", help="Manage voice profiles")
    voice_subparsers = voice_parser.add_subparsers(dest="voice_command", required=True)

    voice_add_parser = voice_subparsers.add_parser(
        "add", help="Import a reference clip as a named voice profile"
    )
    voice_add_parser.add_argument("--name", required=True)
    voice_add_parser.add_argument("--reference", required=True)
    voice_add_parser.add_argument("--transcript", default=None)
    voice_add_parser.add_argument("--exaggeration", type=float, default=0.5)
    voice_add_parser.add_argument("--cfg", type=float, default=0.5)
    voice_add_parser.add_argument("--temperature", type=float, default=0.8)
    voice_add_parser.add_argument("--db", default="audiobookify.db")

    voice_list_parser = voice_subparsers.add_parser("list", help="List saved voice profiles")
    voice_list_parser.add_argument("--db", default="audiobookify.db")

    voice_preview_parser = voice_subparsers.add_parser(
        "preview", help="Synthesize a sample sentence with a saved voice profile"
    )
    voice_preview_parser.add_argument("--name", required=True)
    voice_preview_parser.add_argument("--text", required=True)
    voice_preview_parser.add_argument("--out", default=None)
    voice_preview_parser.add_argument("--device", default="auto")
    voice_preview_parser.add_argument("--db", default="audiobookify.db")

    args = parser.parse_args(argv)

    if args.command == "generate":
        _handle_generate(args)
    elif args.command == "voice":
        if args.voice_command == "add":
            _handle_voice_add(args)
        elif args.voice_command == "list":
            _handle_voice_list(args)
        elif args.voice_command == "preview":
            _handle_voice_preview(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 12: Run the full CLI test suite**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all pass (25 total: 19 existing + 6 new)

- [ ] **Step 13: Update `README.md`**

Restore "sample rate" to the validated-checks list (it's now actually true) — change:

```markdown
- `voice add`: validates the reference clip (length, mono/stereo channel count) and prints warnings (not errors) for anything outside the recommended range, clipping, or a lack of quiet passages (a sign of constant background noise). Only an unusably short (<1s) or unreadable file is rejected outright. Optional tuning flags: `--exaggeration` (default 0.5), `--cfg` (default 0.5), `--temperature` (default 0.8) — Chatterbox's own defaults. `--transcript` is stored as optional metadata only; the installed Chatterbox engine's cloning is zero-shot on audio alone and doesn't use it.
```

to:

```markdown
- `voice add`: validates the reference clip (sample rate, length, mono/stereo channel count) and prints warnings (not errors) for anything outside the recommended range, clipping, or a lack of quiet passages (a sign of constant background noise). Only an unusably short (<1s) or unreadable file is rejected outright. Reference audio is stored by absolute path and is not copied — don't move or delete the original file. Optional tuning flags: `--exaggeration` (default 0.5), `--cfg` (default 0.5), `--temperature` (default 0.8) — Chatterbox's own defaults. `--transcript` is stored as optional metadata only; the installed Chatterbox engine's cloning is zero-shot on audio alone and doesn't use it.
```

Change the generate flag list to document `--voice` and note the new mismatch warning — change:

```markdown
- `--device`: `auto` (default), `cuda`, `mps`, or `cpu`. Auto-detects CUDA → MPS → CPU; falls back to CPU if Chatterbox fails to load on the detected accelerator (this is expected on some Apple Silicon MPS builds — see `project-spec.md` §2).
- `--db`: path to the SQLite job database (default `audiobookify.db` in the current directory). Re-running `generate` against the same `--input`/`--db` resumes: already-completed chapters are skipped.
```

to:

```markdown
- `--voice`: sets the ID3 narrator/artist tag on the exported MP3s only — no effect on cloning. Defaults to `"default"`, or to the voice profile's name if `--voice-profile` is used.
- `--device`: `auto` (default), `cuda`, `mps`, or `cpu`. Auto-detects CUDA → MPS → CPU; falls back to CPU if Chatterbox fails to load on the detected accelerator (this is expected on some Apple Silicon MPS builds — see `project-spec.md` §2).
- `--db`: path to the SQLite job database (default `audiobookify.db` in the current directory). Re-running `generate` against the same `--input`/`--db` resumes: already-completed chapters are skipped. The voice used on a book's first `generate` call is locked in for that book — a later call naming a different (or no) `--voice-profile` prints a warning and keeps using the original voice, so chapters within one book never mix voices silently.
```

Rename the section heading — change `## Usage (M0: CLI only)` to `## Usage`.

- [ ] **Step 14: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all pass (in the 70s, exact count depends on final tallies above; 1 deselected)

- [ ] **Step 15: Commit**

```bash
git add src/audiobookify/db.py src/audiobookify/cli.py src/audiobookify/voice/validation.py \
        tests/test_db.py tests/test_cli.py tests/test_voice_validation.py README.md \
        docs/superpowers/plans/2026-07-28-audiobookify-m1-voice-cloning.md
git commit -m "fix: address M1 whole-branch review findings

Persist Book.voice_profile_id so switching --voice-profile mid-book
warns and keeps the original voice instead of silently mixing voices
across chapters (user decision, 2026-07-28, reversing M1's original
choice not to wire this field). Also: fail fast when a voice profile's
reference audio has been moved/deleted (generate and preview), warn on
low sample rate, reject an empty voice profile name, don't print
validation warnings when voice add's duplicate-name check rejects the
profile, create voice preview's --out parent directory if missing, and
extract a shared _voice_params() helper so generate and preview build
the same dict shape from one place."
```

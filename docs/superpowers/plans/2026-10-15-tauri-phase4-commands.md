# Tauri Phase 4: Commands + Frontend Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Tauri app stops using FastAPI. Every endpoint in `api/app.py` becomes a Tauri command backed by `audiobookify-core`. The frontend calls `invoke`, picks files and folders with native dialogs, and plays audio over the asset protocol. App data moves to `~/Library/Application Support/com.audiobookify.app`.

**Architecture:** A new `audiobookify_core::app` module holds what each endpoint does, as plain functions over `&Database` and `AppPaths`, tested like the other core modules. The desktop crate's `commands.rs` is a thin layer: it locks a second `Database` connection (the runner keeps its own), calls `app::*`, and adds returned audio paths to the asset-protocol scope. The uvicorn sidecar and `sidecars.rs` are deleted. The window loads the bundled frontend with a real CSP. Release builds send stderr to a log file in the app data dir.

**Tech Stack:** Rust 2021 (`rusqlite`, `serde`, `symphonia`, `hound`, `id3`, `uuid`, `libc`), Tauri 2.12 (`protocol-asset` feature, `tauri-plugin-dialog`), React 18 + Vite 5 (`@tauri-apps/api`, `@tauri-apps/plugin-dialog`), ffmpeg (Homebrew).

**Spec:** `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md` (phase 4, "Frontend changes", "Data location", "Notes from phase 0", "Notes from phase 3")

## Global Constraints

- macOS on Apple Silicon only; personal Macs. Python and ffmpeg come from the repo `.venv` and Homebrew.
- Every user-visible error string matches `src/audiobookify/api/app.py`'s `detail` text exactly. The UI shows `Error.message` and never looks at status codes.
- Commands are `snake_case` in Rust; `invoke` args are `camelCase` in JS (`book_id` is sent as `bookId`). Tauri converts them.
- Never start a child with a closed fd 0: every `ffmpeg` call gets `stdin(Stdio::null())` and `PATH` from `audio::child_path`.
- UI follows `docs/design/DESIGN.md` and uses only the `--rr-*` tokens (via the existing Tailwind classes). No emojis; icons come from `lucide-react`.
- TDD: each Rust test is written and seen failing before the code it covers. The frontend has no component tests; its tasks end in `npm test`, `npm run build` and a manual check in `make dev`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Decisions made while planning (record them in the spec in Task 10)

- **App data moves in phase 4, not later.** The Tauri app uses `~/Library/Application Support/com.audiobookify.app/` (`audiobookify.db`, `uploads/`, `voices/`, `cache/synth/`, `text_clips/`, `output/`, `logs/`). On first launch, if there is no DB there and the repo has `audiobookify.db`, a native dialog offers to import it. The import copies the DB (`VACUUM INTO`), `uploads/`, `voices/` and `text_clips/`, and rewrites the stored paths. The chunk cache is not copied, because its keys changed in phase 3 anyway.
- **The browser app stops working in phase 4.** The frontend is shared, and it now calls `invoke`. `make up`/`make dev` are replaced by Tauri targets now (the spec had this in phase 6). FastAPI, the CLI and the Python pipeline stay in the repo, untouched and tested, until phase 6.
- **No CORS, no `fetch` fallback.** Phase 4 is one phase, so the app never runs half-migrated against FastAPI. The phase 0 note about CORS doesn't apply.
- **wav2vec2 `phonemes` request dropped.** Only the CLI's `lexicon add --from-audio` uses it. Typed respellings (the Pronunciations page plus `apply_lexicon` in the runner) are how the GUI handles pronunciation. A "from a recording" option can come back as a GUI feature later.
- **`browse-dir` dropped.** The macOS folder picker replaces it, and there is no typed path field. `validate_output_dir` stays.
- **PATCH `/api/voices/{id}` splits** into `set_voice_transcript` and `set_voice_tempo`, matching the two frontend calls. The "nothing to update" error goes away.
- **Imported files aren't staged twice.** `create_book` copies the picked file into `uploads/<uuid>/` (as today, so moving the original can't break a book). `create_voice` validates the picked file in place, and `store_reference_audio` copies it into `voices/`.
- **`relabel_chapter_mp3` failure handling is fixed.** If retagging fails after the rename, the chapter points at the renamed file. Python pointed at the parked name, which no longer existed.
- **Text-clip Download** uses a native save dialog plus a `save_text_clip` copy command. A `download` link doesn't work in a webview.
- **Setup errors** keep the existing `osascript` alert (`fail()` in `main.rs`): it already is the native dialog the phase 0 note asks for. With the sidecar gone, nothing blocks the main thread before the window appears.

## File Structure

| File | Responsibility |
|---|---|
| `desktop/core/Cargo.toml` (modify) | Add `symphonia`, `uuid`, `libc`. |
| `desktop/core/src/media.rs` (create) | Decode any audio file (symphonia); `duration`. |
| `desktop/core/src/voice_audio.rs` (create) | `validate_reference_audio`, `reference_pace`, `reference_clip`, `TEMPO_RANGE`. |
| `desktop/core/src/audio.rs` (modify) | Add `relabel_chapter_mp3`. |
| `desktop/core/src/voice_storage.rs` (modify) | Add `delete_voice_storage`. |
| `desktop/core/src/app/mod.rs` (create) | `AppPaths`, `ApiError`, engine registry, tempo check. |
| `desktop/core/src/app/books.rs` (create) | Books, chapter text and breaks, output-dir check. |
| `desktop/core/src/app/jobs.rs` (create) | Generate, cancel, worker pause, chapter audio path. |
| `desktop/core/src/app/voices.rs` (create) | Voices. |
| `desktop/core/src/app/lexicon.rs` (create) | Pronunciations. |
| `desktop/core/src/app/clips.rs` (create) | Text clips. |
| `desktop/core/src/datadir.rs` (create) | First-launch import of the repo's data. |
| `desktop/core/src/lib.rs` (modify) | Declare the new modules. |
| `desktop/core/tests/common/mod.rs` (modify) | App test helpers. |
| `desktop/core/tests/{media,voice_audio,app_books,app_jobs,app_voices,datadir}.rs` (create) | Tests (ports of `tests/test_api.py` and `tests/test_chapter_breaks.py`). |
| `desktop/Cargo.toml` (modify) | `protocol-asset`, `tauri-plugin-dialog`, `libc`. |
| `desktop/src/commands.rs` (create) | `AppState` and one `#[tauri::command]` per endpoint. |
| `desktop/src/logging.rs` (create) | Release stderr goes to `logs/audiobookify.log`. |
| `desktop/src/main.rs` (rewrite) | Data dir, import prompt, two DB connections, runner, commands, window. |
| `desktop/src/sidecars.rs` (delete) | No more uvicorn. |
| `desktop/tauri.conf.json` (modify) | CSP, asset protocol. |
| `desktop/capabilities/default.json` (create) | Core and dialog permissions for the `main` window. |
| `Makefile` (modify) | `dev`/`up`/`app` become Tauri targets. |
| `frontend/package.json`, `frontend/vite.config.js` (modify) | Tauri packages; no `/api` proxy; fixed port. |
| `frontend/src/lib/status.js` (create) | The pure status helpers moved out of `api.js`. |
| `frontend/src/lib/api.js` (rewrite) | `invoke` wrappers. |
| `frontend/src/components/FileAudio.jsx` (create) | `<audio>` for a file on disk. |
| `frontend/src/components/PathPick.jsx` (create) | "Choose…" button plus the chosen path. |
| `frontend/src/components/DirectoryBrowser.jsx` (rewrite) | Native folder picker plus the writable check. |
| `frontend/src/components/player/PlayerContext.jsx`, `pages/{HomePage,VoicesPage,TextPage,BookDetailPage}.jsx`, `lib/shelf.js` (modify) | Call sites. |

Test commands (run from `desktop/`): `cargo test -p audiobookify-core --test <name>`. All core tests: `cargo test -p audiobookify-core`. Frontend (from `frontend/`): `npm test`, `npm run build`.

---

### Task 1: Decode any audio file (`media`)

Python read uploads and MP3s with `soundfile`. Rust's `hound` reads only WAV. `media` decodes WAV, MP3, FLAC, OGG, AIFF and M4A with symphonia. Later tasks use it for voice validation, reference trims, voice length and chapter durations.

**Files:**
- Modify: `desktop/core/Cargo.toml`
- Create: `desktop/core/src/media.rs`
- Modify: `desktop/core/src/lib.rs`
- Modify: `desktop/core/tests/common/mod.rs`
- Create: `desktop/core/tests/media.rs`

**Interfaces:**
- Produces: `media::decode(&Path) -> Result<Decoded, MediaError>`; `Decoded { samples: Vec<f32> (interleaved), sample_rate: u32, channels: usize }` with `frames()`, `duration()`, `mono()`; `media::duration(&Path) -> Result<f64, MediaError>`; `MediaError` (`Display`).
- Produces (tests): `common::wav(path, sample_rate, channels, frames, sample_fn)`, `common::silent_mp3(path, seconds)`.

- [ ] **Step 1: Add the dependencies**

In `desktop/core/Cargo.toml` `[dependencies]`, keeping the list alphabetical:

```toml
libc = "0.2"
symphonia = { version = "0.5", features = ["all"] }
uuid = { version = "1", features = ["v4"] }
```

(`libc` and `uuid` are used from Task 3 on; adding them now keeps `Cargo.toml` to one change.)

- [ ] **Step 2: Add test helpers**

Append to `desktop/core/tests/common/mod.rs`:

```rust
/// A float WAV where every channel of frame `i` is `sample(i)`.
pub fn wav(path: &Path, sample_rate: u32, channels: u16, frames: usize, sample: impl Fn(usize) -> f32) {
    let spec = hound::WavSpec { channels, sample_rate, bits_per_sample: 32, sample_format: hound::SampleFormat::Float };
    let mut w = hound::WavWriter::create(path, spec).unwrap();
    for i in 0..frames {
        for _ in 0..channels {
            w.write_sample(sample(i)).unwrap();
        }
    }
    w.finalize().unwrap();
}

/// test_api.py's `_clean_tone_wav_bytes`: 15 s of a 220 Hz tone with a silent last tenth.
/// Passes `validate_reference_audio` with no warnings.
pub fn clean_tone_wav(path: &Path) {
    let rate = 22050;
    let n = 15 * rate as usize;
    wav(path, rate, 1, n, |i| {
        if i < n * 9 / 10 { 0.6 * (2.0 * std::f32::consts::PI * 220.0 * i as f32 / rate as f32).sin() } else { 0.0 }
    });
}

/// `seconds` of mono silence as an MP3, written by ffmpeg.
pub fn silent_mp3(path: &Path, seconds: f64) {
    let status = std::process::Command::new("ffmpeg")
        .env("PATH", audiobookify_core::audio::child_path(std::env::var_os("PATH")))
        .args(["-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", &seconds.to_string()])
        .arg(path)
        .stdin(std::process::Stdio::null())
        .status()
        .unwrap();
    assert!(status.success());
}
```

- [ ] **Step 3: Write the failing tests**

Create `desktop/core/tests/media.rs`:

```rust
mod common;

use audiobookify_core::media;
use common::*;
use tempfile::TempDir;

#[test]
fn decodes_a_stereo_wav_interleaved() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("a.wav");
    wav(&path, 16000, 2, 24000, |i| if i == 0 { 0.5 } else { 0.0 });

    let d = media::decode(&path).unwrap();

    assert_eq!((d.sample_rate, d.channels, d.frames()), (16000, 2, 24000));
    assert_eq!(&d.samples[..3], &[0.5, 0.5, 0.0]);
    assert!((d.duration() - 1.5).abs() < 1e-9);
}

#[test]
fn mono_averages_the_channels() {
    let d = media::Decoded { samples: vec![1.0, 0.0, 0.5, 0.5], sample_rate: 8000, channels: 2 };
    assert_eq!(d.mono(), vec![0.5, 0.5]);
}

#[test]
fn reads_an_mp3_and_its_duration() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("ch.mp3");
    silent_mp3(&path, 2.0);

    assert!((media::duration(&path).unwrap() - 2.0).abs() < 0.1);
    let d = media::decode(&path).unwrap();
    assert_eq!(d.channels, 1);
    assert!((d.duration() - 2.0).abs() < 0.1);
}

#[test]
fn wav_duration_comes_from_the_header() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("a.wav");
    wav(&path, 22050, 1, 22050 * 3, |_| 0.0);
    assert!((media::duration(&path).unwrap() - 3.0).abs() < 1e-9);
}

#[test]
fn unreadable_files_are_errors() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("x.wav");
    std::fs::write(&path, b"not audio").unwrap();
    assert!(media::decode(&path).is_err());
    assert!(media::duration(&dir.path().join("missing.mp3")).is_err());
}
```

- [ ] **Step 4: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test media`
Expected: compile error, `unresolved import audiobookify_core::media`.

- [ ] **Step 5: Implement `media`**

Create `desktop/core/src/media.rs`:

```rust
//! Reading audio files the user picks (WAV, MP3, FLAC, OGG, AIFF, M4A) with symphonia.
//! Python used soundfile. hound (audio.rs) stays for the WAVs the app writes itself.

use std::fs::File;
use std::io;
use std::path::Path;

use symphonia::core::audio::SampleBuffer;
use symphonia::core::codecs::{DecoderOptions, CODEC_TYPE_NULL};
use symphonia::core::errors::Error as SymphoniaError;
use symphonia::core::formats::{FormatOptions, FormatReader};
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;

#[derive(Debug, thiserror::Error)]
pub enum MediaError {
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error("{0}")]
    Decode(#[from] SymphoniaError),
    #[error("no audio track")]
    NoTrack,
}

/// Interleaved samples in -1..1, as soundfile's `sf.read(dtype="float32")` gives them.
#[derive(Debug)]
pub struct Decoded {
    pub samples: Vec<f32>,
    pub sample_rate: u32,
    pub channels: usize,
}

impl Decoded {
    pub fn frames(&self) -> usize {
        self.samples.len() / self.channels.max(1)
    }

    pub fn duration(&self) -> f64 {
        self.frames() as f64 / self.sample_rate as f64
    }

    /// The channels averaged, like numpy's `data.mean(axis=1)`.
    pub fn mono(&self) -> Vec<f32> {
        self.samples.chunks(self.channels.max(1)).map(|f| f.iter().sum::<f32>() / f.len() as f32).collect()
    }
}

fn open(path: &Path) -> Result<Box<dyn FormatReader>, MediaError> {
    let mss = MediaSourceStream::new(Box::new(File::open(path)?), Default::default());
    let mut hint = Hint::new();
    if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
        hint.with_extension(ext);
    }
    let probed = symphonia::default::get_probe()
        .format(&hint, mss, &FormatOptions::default(), &MetadataOptions::default())?;
    Ok(probed.format)
}

pub fn decode(path: &Path) -> Result<Decoded, MediaError> {
    let mut format = open(path)?;
    let track = format.tracks().iter().find(|t| t.codec_params.codec != CODEC_TYPE_NULL).ok_or(MediaError::NoTrack)?;
    let track_id = track.id;
    let mut decoder = symphonia::default::get_codecs().make(&track.codec_params, &DecoderOptions::default())?;
    let mut out = Decoded {
        samples: Vec::new(),
        sample_rate: track.codec_params.sample_rate.unwrap_or(0),
        channels: track.codec_params.channels.map_or(0, |c| c.count()),
    };
    loop {
        let packet = match format.next_packet() {
            Ok(p) => p,
            Err(SymphoniaError::IoError(e)) if e.kind() == io::ErrorKind::UnexpectedEof => break,
            Err(e) => return Err(e.into()),
        };
        if packet.track_id() != track_id {
            continue;
        }
        let decoded = match decoder.decode(&packet) {
            Ok(d) => d,
            Err(SymphoniaError::DecodeError(_)) => continue, // a corrupt frame: skip it, like libsndfile
            Err(e) => return Err(e.into()),
        };
        let spec = *decoded.spec();
        out.sample_rate = spec.rate;
        out.channels = spec.channels.count();
        let mut buf = SampleBuffer::<f32>::new(decoded.capacity() as u64, spec);
        buf.copy_interleaved_ref(decoded);
        out.samples.extend_from_slice(buf.samples());
    }
    if out.sample_rate == 0 || out.channels == 0 {
        return Err(MediaError::NoTrack);
    }
    Ok(out)
}

/// Seconds of audio. Uses the header's frame count when there is one (WAV, FLAC, MP3 with
/// a Xing/Info header), so listing voices every 2 s doesn't decode every clip.
pub fn duration(path: &Path) -> Result<f64, MediaError> {
    let format = open(path)?;
    if let Some(t) = format.tracks().iter().find(|t| t.codec_params.codec != CODEC_TYPE_NULL) {
        if let (Some(frames), Some(rate)) = (t.codec_params.n_frames, t.codec_params.sample_rate) {
            return Ok(frames as f64 / rate as f64);
        }
    }
    Ok(decode(path)?.duration())
}
```

Add `pub mod media;` to `desktop/core/src/lib.rs` (alphabetical, after `pub mod jobs;`).

- [ ] **Step 6: Run the tests to see them pass**

Run: `cargo test -p audiobookify-core --test media`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add desktop/core/Cargo.toml desktop/Cargo.lock desktop/core/src/media.rs desktop/core/src/lib.rs desktop/core/tests/common/mod.rs desktop/core/tests/media.rs
git commit -m "Decode user-picked audio files with symphonia

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Voice-clip helpers, chapter relabel, voice storage delete

These are ports of the remaining Python pieces that the endpoints call: `voice/validation.py`, the parts of `voice/analysis.py` the API uses, `relabel_chapter_mp3`, and `delete_voice_storage`.

**Files:**
- Create: `desktop/core/src/voice_audio.rs`
- Modify: `desktop/core/src/audio.rs` (after `export_chapter_mp3`)
- Modify: `desktop/core/src/voice_storage.rs`
- Modify: `desktop/core/src/lib.rs`
- Create: `desktop/core/tests/voice_audio.rs`

**Interfaces:**
- Consumes: `media::decode`, `audio::{child_path, chapter_filename, write_tags, Tags, AudioError}`, `engine::Word`.
- Produces:
  - `voice_audio::TEMPO_RANGE: (f64, f64) = (0.75, 1.25)`
  - `voice_audio::validate_reference_audio(&Path) -> Result<ReferenceInfo, String>`, where `ReferenceInfo { sample_rate: u32, channels: usize, duration_seconds: f64, warnings: Vec<String> }`
  - `voice_audio::Pace { speaking_wpm: f64, overall_wpm: f64 }` (Serialize); `voice_audio::reference_pace(Option<&serde_json::Value>, Option<f64>) -> Option<Pace>`
  - `voice_audio::reference_clip(&Path, Option<f64>, f64) -> Result<PathBuf, String>`
  - `audio::relabel_chapter_mp3(&Path, i64, &str) -> Result<PathBuf, AudioError>`
  - `voice_storage::delete_voice_storage(voices_dir: &Path, profile_id: i64, name: &str)`

- [ ] **Step 1: Write the failing tests**

Create `desktop/core/tests/voice_audio.rs`:

```rust
mod common;

use std::fs;

use audiobookify_core::audio::{chapter_filename, export_chapter_mp3, relabel_chapter_mp3};
use audiobookify_core::media;
use audiobookify_core::voice_audio::{reference_clip, reference_pace, validate_reference_audio, Pace};
use audiobookify_core::voice_storage::{delete_voice_storage, permanent_voice_dir};
use common::*;
use serde_json::json;
use tempfile::TempDir;

#[test]
fn a_clean_clip_has_no_warnings() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("clean.wav");
    clean_tone_wav(&path);

    let info = validate_reference_audio(&path).unwrap();

    assert_eq!((info.sample_rate, info.channels), (22050, 1));
    assert!((info.duration_seconds - 15.0).abs() < 1e-9);
    assert_eq!(info.warnings, Vec::<String>::new());
}

#[test]
fn a_poor_clip_gets_every_warning_python_gives() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("loud.wav");
    wav(&path, 8000, 2, 5 * 8000, |_| 1.0);

    let info = validate_reference_audio(&path).unwrap();

    assert_eq!(info.warnings, vec![
        "reference audio sample rate is 8000 Hz, below the recommended 16000 Hz minimum",
        "reference audio is 5.0s, shorter than the recommended 10-30s window",
        "reference audio has 2 channels; mono is recommended",
        "possible clipping detected (100.00% of samples at peak amplitude)",
        "no quiet passages detected; recording may have constant background noise",
    ]);
}

#[test]
fn a_long_clip_is_warned_about() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("long.wav");
    wav(&path, 16000, 1, 31 * 16000, |i| if i % 2000 < 1000 { 0.3 } else { 0.0 });
    assert_eq!(validate_reference_audio(&path).unwrap().warnings,
               vec!["reference audio is 31.0s, longer than the recommended 10-30s window"]);
}

#[test]
fn too_short_and_unreadable_clips_are_errors() {
    let dir = TempDir::new().unwrap();
    let short = dir.path().join("short.wav");
    wav(&short, 16000, 1, 8000, |_| 0.1);
    assert_eq!(validate_reference_audio(&short).unwrap_err(),
               "reference audio is too short to clone from (0.50s); need at least 1s");

    let junk = dir.path().join("junk.wav");
    fs::write(&junk, b"nope").unwrap();
    let err = validate_reference_audio(&junk).unwrap_err();
    assert!(err.starts_with(&format!("could not read reference audio '{}': ", junk.display())), "{err}");
}

#[test]
fn pace_uses_the_words_engines_hear() {
    let words = json!([
        {"word": "Hello.", "start": 0.0, "end": 0.5},
        {"word": "there", "start": 1.0, "end": 1.5},
        {"word": "friend", "start": 1.5, "end": 2.0},
    ]);
    assert_eq!(reference_pace(Some(&words), None), Some(Pace { speaking_wpm: 120.0, overall_wpm: 90.0 }));
    assert_eq!(reference_pace(Some(&words), Some(1.5)), Some(Pace { speaking_wpm: 120.0, overall_wpm: 80.0 }));
    assert_eq!(reference_pace(Some(&words), Some(0.5)), None);
    assert_eq!(reference_pace(None, None), None);
}

#[test]
fn reference_clip_trims_then_changes_tempo_and_caches_both() {
    let dir = TempDir::new().unwrap();
    let reference = dir.path().join("reference.wav");
    wav(&reference, 16000, 2, 3 * 16000, |i| ((i % 100) as f32 / 100.0) - 0.5);

    assert_eq!(reference_clip(&reference, None, 1.0).unwrap(), reference);

    let trimmed = reference_clip(&reference, Some(1.0), 1.0).unwrap();
    assert_eq!(trimmed, dir.path().join("reference.trim-1.00s.wav"));
    let t = media::decode(&trimmed).unwrap();
    assert_eq!((t.sample_rate, t.channels, t.frames()), (16000, 2, 16000));

    let slow = reference_clip(&reference, Some(1.0), 0.8).unwrap();
    assert_eq!(slow, dir.path().join("reference.trim-1.00s.tempo-0.80.wav"));
    assert!((media::duration(&slow).unwrap() - 1.25).abs() < 0.05);

    let written = fs::metadata(&slow).unwrap().modified().unwrap();
    reference_clip(&reference, Some(1.0), 0.8).unwrap();
    assert_eq!(fs::metadata(&slow).unwrap().modified().unwrap(), written);
}

#[test]
fn reference_clip_of_a_missing_file_is_an_error() {
    let dir = TempDir::new().unwrap();
    assert!(reference_clip(&dir.path().join("gone.wav"), Some(1.0), 1.0).is_err());
}

#[test]
fn relabel_chapter_mp3_renames_and_retags() {
    let dir = TempDir::new().unwrap();
    let (path, _) = export_chapter_mp3(&vec![0.0; 2205], 22050, dir.path(), 0, "Old", "Book", "N", None).unwrap();

    let new_path = relabel_chapter_mp3(&path, 4, "New").unwrap();

    assert_eq!(new_path, dir.path().join(chapter_filename(4, "New")));
    assert!(!dir.path().join(chapter_filename(0, "Old")).exists());
    use id3::TagLike;
    let tag = id3::Tag::read_from_path(&new_path).unwrap();
    assert_eq!((tag.title(), tag.track(), tag.album()), (Some("New"), Some(5), Some("Book")));
}

#[test]
fn delete_voice_storage_removes_the_folder_and_ignores_a_missing_one() {
    let dir = TempDir::new().unwrap();
    let voice_dir = permanent_voice_dir(dir.path(), 3, "Nova");
    fs::create_dir_all(&voice_dir).unwrap();
    fs::write(voice_dir.join("reference.wav"), b"x").unwrap();

    delete_voice_storage(dir.path(), 3, "Nova");
    delete_voice_storage(dir.path(), 3, "Nova");

    assert!(!voice_dir.exists());
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test voice_audio`
Expected: compile errors for `voice_audio`, `relabel_chapter_mp3` and `delete_voice_storage`.

- [ ] **Step 3: Implement `voice_audio`**

Create `desktop/core/src/voice_audio.rs`:

```rust
//! Checks and previews of a voice's reference clip, ported from voice/validation.py and
//! the API-facing parts of voice/analysis.py.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde::Serialize;

use crate::audio::child_path;
use crate::engine::Word;
use crate::media;

pub const TEMPO_RANGE: (f64, f64) = (0.75, 1.25);

const ABSOLUTE_MIN_SECONDS: f64 = 1.0;
const RECOMMENDED_MIN_SECONDS: f64 = 10.0;
const RECOMMENDED_MAX_SECONDS: f64 = 30.0;
const RECOMMENDED_MIN_SAMPLE_RATE: u32 = 16000;
const CLIP_AMPLITUDE: f32 = 0.999;
const CLIP_FRACTION_WARN: f64 = 0.001;
const FRAME_SECONDS: f64 = 0.02;
const SILENCE_RMS_THRESHOLD: f64 = 0.02;
const SILENCE_FRACTION_MIN: f64 = 0.02;
const PAUSE_MIN_SECONDS: f64 = 0.15;

#[derive(Debug)]
pub struct ReferenceInfo {
    pub sample_rate: u32,
    pub channels: usize,
    pub duration_seconds: f64,
    pub warnings: Vec<String>,
}

fn quiet_frame_fraction(mono: &[f32], sample_rate: u32) -> f64 {
    let frame_len = ((FRAME_SECONDS * sample_rate as f64) as usize).max(1);
    let n_frames = mono.len() / frame_len;
    if n_frames == 0 {
        return 1.0; // too short to frame - don't false-alarm on noise
    }
    let quiet = mono.chunks_exact(frame_len).filter(|frame| {
        let mean_square = frame.iter().map(|&x| f64::from(x) * f64::from(x)).sum::<f64>() / frame_len as f64;
        mean_square.sqrt() < SILENCE_RMS_THRESHOLD
    });
    quiet.count() as f64 / n_frames as f64
}

/// Too short or unreadable is an error; everything else that could hurt cloning is a warning.
pub fn validate_reference_audio(path: &Path) -> Result<ReferenceInfo, String> {
    let d = media::decode(path).map_err(|e| format!("could not read reference audio '{}': {e}", path.display()))?;
    let duration = d.duration();
    if duration < ABSOLUTE_MIN_SECONDS {
        return Err(format!(
            "reference audio is too short to clone from ({duration:.2}s); need at least {ABSOLUTE_MIN_SECONDS:.0}s"
        ));
    }
    let mut warnings = Vec::new();
    if d.sample_rate < RECOMMENDED_MIN_SAMPLE_RATE {
        warnings.push(format!(
            "reference audio sample rate is {} Hz, below the recommended {RECOMMENDED_MIN_SAMPLE_RATE} Hz minimum",
            d.sample_rate
        ));
    }
    let window = format!("{RECOMMENDED_MIN_SECONDS:.0}-{RECOMMENDED_MAX_SECONDS:.0}s window");
    if duration < RECOMMENDED_MIN_SECONDS {
        warnings.push(format!("reference audio is {duration:.1}s, shorter than the recommended {window}"));
    } else if duration > RECOMMENDED_MAX_SECONDS {
        warnings.push(format!("reference audio is {duration:.1}s, longer than the recommended {window}"));
    }
    if d.channels > 1 {
        warnings.push(format!("reference audio has {} channels; mono is recommended", d.channels));
    }
    let mono = d.mono();
    let clipped = mono.iter().filter(|x| x.abs() >= CLIP_AMPLITUDE).count() as f64 / mono.len() as f64;
    if clipped > CLIP_FRACTION_WARN {
        warnings.push(format!("possible clipping detected ({:.2}% of samples at peak amplitude)", clipped * 100.0));
    }
    if quiet_frame_fraction(&mono, d.sample_rate) < SILENCE_FRACTION_MIN {
        warnings.push("no quiet passages detected; recording may have constant background noise".into());
    }
    Ok(ReferenceInfo { sample_rate: d.sample_rate, channels: d.channels, duration_seconds: duration, warnings })
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Pace {
    pub speaking_wpm: f64,
    pub overall_wpm: f64,
}

/// Words per minute in the part of the clip engines hear (words ending by `trim_end`).
/// `speaking_wpm` leaves out pauses of 0.15 s or more. None with fewer than two words.
pub fn reference_pace(words: Option<&serde_json::Value>, trim_end: Option<f64>) -> Option<Pace> {
    let words: Vec<Word> = serde_json::from_value(words?.clone()).ok()?;
    let kept: Vec<&Word> = words.iter().filter(|w| trim_end.is_none_or(|t| w.end <= t)).collect();
    if kept.len() < 2 {
        return None;
    }
    let pauses: f64 = kept
        .windows(2)
        .map(|pair| ((pair[1].start - pair[0].end) * 1000.0).round() / 1000.0)
        .filter(|&gap| gap >= PAUSE_MIN_SECONDS)
        .sum();
    let span = kept[kept.len() - 1].end - kept[0].start;
    let n = kept.len() as f64;
    Some(Pace { speaking_wpm: n / (span - pauses) * 60.0, overall_wpm: n / span * 60.0 })
}

fn stem(path: &Path) -> String {
    path.file_stem().unwrap_or_default().to_string_lossy().into_owned()
}

fn hidden_twin(path: &Path) -> PathBuf {
    path.with_file_name(format!(".{}", path.file_name().unwrap_or_default().to_string_lossy()))
}

/// soundfile's default WAV subtype is PCM_16. Matching it means the Python engine worker
/// (which makes the same trim, same name) and this write the same cached file.
fn write_pcm16(path: &Path, samples: &[f32], sample_rate: u32, channels: usize) -> Result<(), String> {
    let spec = hound::WavSpec {
        channels: channels as u16, sample_rate, bits_per_sample: 16, sample_format: hound::SampleFormat::Int,
    };
    let tmp = hidden_twin(path); // never leave a half-written clip under the cached name
    let mut w = hound::WavWriter::create(&tmp, spec).map_err(|e| e.to_string())?;
    for &s in samples {
        w.write_sample((s.clamp(-1.0, 1.0) * 32767.0).round() as i16).map_err(|e| e.to_string())?;
    }
    w.finalize().map_err(|e| e.to_string())?;
    fs::rename(&tmp, path).map_err(|e| e.to_string())
}

/// The clip engines clone from: the reference cut at `trim_end`, at `tempo`. Both steps
/// are cached next to the reference under the names voice/analysis.py uses.
pub fn reference_clip(reference: &Path, trim_end: Option<f64>, tempo: f64) -> Result<PathBuf, String> {
    let mut clip = reference.to_path_buf();
    if let Some(end) = trim_end {
        clip = reference.with_file_name(format!("{}.trim-{end:.2}s.wav", stem(reference)));
        if !clip.is_file() {
            let d = media::decode(reference).map_err(|e| e.to_string())?;
            let keep = ((end * d.sample_rate as f64).round() as usize * d.channels).min(d.samples.len());
            write_pcm16(&clip, &d.samples[..keep], d.sample_rate, d.channels)?;
        }
    }
    if (tempo * 100.0).round() == 100.0 {
        return Ok(clip);
    }
    let out = clip.with_file_name(format!("{}.tempo-{tempo:.2}.wav", stem(&clip)));
    if !out.is_file() {
        let tmp = hidden_twin(&out);
        let result = Command::new("ffmpeg")
            .env("PATH", child_path(std::env::var_os("PATH")))
            .args(["-y", "-loglevel", "error", "-i"])
            .arg(&clip)
            .args(["-filter:a", &format!("atempo={tempo:.2}")])
            .arg(&tmp)
            .stdin(Stdio::null())
            .output()
            .map_err(|e| format!("could not run ffmpeg: {e}"))?;
        if !result.status.success() {
            return Err(format!("ffmpeg failed: {}", String::from_utf8_lossy(&result.stderr).trim()));
        }
        fs::rename(&tmp, &out).map_err(|e| e.to_string())?;
    }
    Ok(out)
}
```

If `engine::Word`'s fields aren't `pub`, make `word`, `start` and `end` `pub` in `desktop/core/src/engine.rs`.

Add `pub mod voice_audio;` to `desktop/core/src/lib.rs` (alphabetical, after `pub mod text;`).

- [ ] **Step 4: Add `relabel_chapter_mp3` and `delete_voice_storage`**

In `desktop/core/src/audio.rs`, after `export_chapter_mp3`:

```rust
/// Moves a finished chapter's MP3 to its new name (same folder) and fixes its title and
/// track tags; album and artist stay. Port of postprocess/audio.py's relabel_chapter_mp3.
pub fn relabel_chapter_mp3(path: &Path, order_index: i64, chapter_title: &str) -> Result<PathBuf, AudioError> {
    let new_path = path.with_file_name(chapter_filename(order_index, chapter_title));
    std::fs::rename(path, &new_path)?;
    let tags = Tags { title: Some(chapter_title), track: Some((order_index + 1) as u32), ..Default::default() };
    write_tags(&new_path, &tags)?;
    Ok(new_path)
}
```

In `desktop/core/src/voice_storage.rs`, after `store_reference_audio`:

```rust
/// Removes a voice's folder: the reference and its cached trims and tempo clips.
pub fn delete_voice_storage(voices_dir: &Path, profile_id: i64, name: &str) {
    let _ = fs::remove_dir_all(permanent_voice_dir(voices_dir, profile_id, name));
}
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `cargo test -p audiobookify-core --test voice_audio`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add desktop/core/src/voice_audio.rs desktop/core/src/audio.rs desktop/core/src/voice_storage.rs desktop/core/src/engine.rs desktop/core/src/lib.rs desktop/core/tests/voice_audio.rs
git commit -m "Port voice-clip validation, pace, trims and chapter relabel to Rust

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `app` foundation and the book endpoints

**Files:**
- Create: `desktop/core/src/app/mod.rs`
- Create: `desktop/core/src/app/books.rs`
- Modify: `desktop/core/src/lib.rs`
- Modify: `desktop/core/tests/common/mod.rs`
- Create: `desktop/core/tests/app_books.rs`

**Interfaces:**
- Consumes: `Database` (all book/chapter/job methods), `book_paragraphs`, `ingest::ingest_by_path`, `jobs::{ChunkCache, Paths}`, `audio::{chapter_filename, relabel_chapter_mp3}`, `media::duration`.
- Produces (`audiobookify_core::app`):
  - `AppPaths { root: PathBuf }`: `new(root)`, `db()`, `uploads()`, `voices()`, `cache()`, `text_clips()`, `output()`, `log()`, `open_db() -> db::Result<Database>`, `runner_paths() -> jobs::Paths`
  - `ApiError(pub String)` (Serialize as a plain string; `From<DbError>`, `From<io::Error>`); `ApiResult<T>`
  - `ENGINES: [EngineSpec; 2]`, `EngineSpec { id, label, description, needs_voice }`, `Engines { default, engines }`, `list_engines() -> Engines`
  - `pub(crate) fn chosen_engine(Option<&str>, current: &str, Option<i64>) -> ApiResult<String>`, `pub(crate) fn check_tempo(f64) -> ApiResult<f64>`, `pub(crate) fn found<T>(db::Result<T>, what: &str) -> ApiResult<T>`, `pub(crate) fn err<T>(impl Into<String>) -> ApiResult<T>`
- Produces (`app::books`):
  - `BookDto`, `ChapterDto`, `BookText { paragraphs, chapters: Vec<ChapterStart> }`, `ChapterBreak { start: usize, title: String }` (Deserialize), `DirCheck { path, writable, error }`
  - `book_dto(&Database, &Book) -> ApiResult<BookDto>`, `list_books(&Database)`, `get_book(&Database, i64)`, `create_book(&Database, &AppPaths, source: &Path, output_dir: &str)`, `delete_book(&Database, &AppPaths, i64) -> ApiResult<()>`, `book_text(&Database, i64)`, `save_chapter_breaks(&mut Database, &AppPaths, i64, &[ChapterBreak])`, `assign_voice(&Database, i64, Option<i64>) -> ApiResult<()>`, `validate_output_dir(&str) -> DirCheck`
  - `pub(crate) fn active_job(&Database, chapter_id) -> ApiResult<Option<Job>>`, `pub(crate) fn rendering(&Database, book_id) -> ApiResult<bool>`
- Produces (tests): `common::{app, write_file, api_err, THREE, TWO_CHAPTERS_MD}`

- [ ] **Step 1: Add the test helpers**

Append to `desktop/core/tests/common/mod.rs`:

```rust
use audiobookify_core::app::{ApiResult, AppPaths};

/// test_chapter_breaks.py's THREE: (title, text).
pub const THREE: [(&str, &str); 3] = [("One", "a\n\nb"), ("Two", "c\n\nd\n\ne"), ("Three", "f")];

pub const TWO_CHAPTERS_MD: &str = "# Chapter One\nFirst chapter body.\n\n# Chapter Two\nSecond chapter body.\n";

/// The app's data under `<dir>/data`, and a connection to its DB.
pub fn app(dir: &Path) -> (AppPaths, Database) {
    let paths = AppPaths::new(dir.join("data"));
    let db = paths.open_db().unwrap();
    (paths, db)
}

pub fn write_file(dir: &Path, name: &str, body: &str) -> PathBuf {
    let path = dir.join(name);
    std::fs::write(&path, body).unwrap();
    path
}

pub fn api_err<T: Debug>(result: ApiResult<T>) -> String {
    match result {
        Err(e) => e.0,
        Ok(v) => panic!("expected an error, got {v:?}"),
    }
}
```

- [ ] **Step 2: Write the failing tests**

Create `desktop/core/tests/app_books.rs`. Each test names the Python test it ports.

```rust
mod common;

use std::fs;
use std::os::unix::fs::PermissionsExt;

use audiobookify_core::app::books::{self, ChapterBreak};
use audiobookify_core::app::list_engines;
use audiobookify_core::audio::{chapter_filename, export_chapter_mp3};
use audiobookify_core::jobs::ChunkCache;
use audiobookify_core::models::{ChapterOutcome, ChapterStatus, JobStatus};
use common::*;
use tempfile::TempDir;

fn import(dir: &std::path::Path, paths: &audiobookify_core::app::AppPaths, db: &audiobookify_core::db::Database)
          -> books::BookDto {
    let src = write_file(dir, "book.md", TWO_CHAPTERS_MD);
    books::create_book(db, paths, &src, &path(dir, "out")).unwrap()
}

/// test_chapter_breaks.py's `_book`: a Markdown book with THREE, created straight in the DB.
fn three(db: &audiobookify_core::db::Database, dir: &std::path::Path) -> i64 {
    let book = md_book(db, dir, &path(dir, "b.md"));
    db.add_chapters(book.id, &THREE.map(|(t, x)| raw(t, x))).unwrap();
    book.id
}

// test_create_book_ingests_uploaded_markdown_and_returns_chapters
#[test]
fn create_book_ingests_markdown_and_returns_chapters() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    assert_eq!(book.title, "book");
    assert_eq!(book.chapters.len(), 2);
    assert_eq!(book.chapters[0].title, "Chapter One");
    assert_eq!(book.chapters[0].status, "pending");
    assert_eq!(book.chapters[0].progress, None);
}

#[test]
fn create_book_stages_a_copy_and_leaves_the_original() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    let source = db.get_book(book.id).unwrap().source_path;
    assert!(std::path::Path::new(&source).starts_with(fs::canonicalize(paths.uploads()).unwrap()), "{source}");
    assert_eq!(fs::read_to_string(&source).unwrap(), TWO_CHAPTERS_MD);
    assert!(dir.path().join("book.md").is_file());
}

// test_create_book_strips_whitespace_from_output_dir
#[test]
fn create_book_strips_whitespace_from_output_dir() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let src = write_file(dir.path(), "book.md", TWO_CHAPTERS_MD);
    let book = books::create_book(&db, &paths, &src, &format!("  {}  ", path(dir.path(), "out"))).unwrap();
    assert_eq!(book.output_dir, fs::canonicalize(dir.path().join("out")).unwrap().to_string_lossy());
}

// test_create_book_rejects_unsupported_format
#[test]
fn create_book_rejects_unsupported_format() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let src = write_file(dir.path(), "book.txt", "hi");
    assert_eq!(api_err(books::create_book(&db, &paths, &src, &path(dir.path(), "out"))),
               "unsupported input format: .txt");
}

// test_create_book_creates_a_new_book_row_for_each_upload_even_with_the_same_filename
#[test]
fn create_book_adds_a_row_for_each_import_of_the_same_file() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let a = import(dir.path(), &paths, &db);
    let b = import(dir.path(), &paths, &db);
    assert_ne!(a.id, b.id);
    assert_eq!(books::list_books(&db).unwrap().len(), 2);
}

// test_create_book_rejects_an_unwritable_output_dir
#[test]
fn create_book_rejects_an_unwritable_output_dir() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let locked = dir.path().join("locked");
    fs::create_dir(&locked).unwrap();
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o500)).unwrap();
    let src = write_file(dir.path(), "book.md", TWO_CHAPTERS_MD);

    let err = api_err(books::create_book(&db, &paths, &src, &path(&locked, "out")));

    assert!(err.starts_with("cannot write to '"), "{err}");
    assert!(err.ends_with("' — check the folder exists and you have permission to write to it"), "{err}");
    assert!(books::list_books(&db).unwrap().is_empty());
}

// test_list_books_returns_all_imported_books, test_get_book_returns_404_for_unknown_id
#[test]
fn list_and_get_books() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    assert_eq!(books::list_books(&db).unwrap().iter().map(|b| b.id).collect::<Vec<_>>(), vec![book.id]);
    assert_eq!(books::get_book(&db, book.id).unwrap().id, book.id);
    assert_eq!(api_err(books::get_book(&db, 999)), "book not found");
}

// test_get_book_surfaces_started_at_for_a_generating_chapter, test_get_book_still_reports_progress_for_a_genuinely_queued_job
#[test]
fn get_book_reports_progress_and_start_of_the_active_job() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    db.create_job(book.chapters[0].id).unwrap();
    let running = db.pop_next_queued_job().unwrap().unwrap();
    db.update_job_progress(running.id, 0.5).unwrap();
    let queued = db.create_job(book.chapters[1].id).unwrap();
    db.update_job_progress(queued.id, 0.0).unwrap();

    let chapters = books::get_book(&db, book.id).unwrap().chapters;

    let first = chapters.iter().find(|c| c.id == running.chapter_id).unwrap();
    assert_eq!(first.progress, Some(0.5));
    assert!(first.started_at.is_some());
    let second = chapters.iter().find(|c| c.id == queued.chapter_id).unwrap();
    assert_eq!((second.progress, second.started_at), (Some(0.0), None));
}

// test_get_book_surfaces_used_fallback_per_chapter,
// test_book_reports_which_engine_made_each_chapter_and_whether_they_are_mixed
#[test]
fn get_book_reports_fallback_engine_and_mixed_engines() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    let (a, b) = (book.chapters[0].id, book.chapters[1].id);
    db.update_chapter_status(a, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some("/a.mp3"), used_fallback: true, engine: Some("qwen3"), duration: Some(1.0),
        ..Default::default()
    }).unwrap();

    let got = books::get_book(&db, book.id).unwrap();
    assert_eq!(got.chapters.iter().map(|c| c.engine.as_deref()).collect::<Vec<_>>(), vec![Some("qwen3"), None]);
    assert_eq!(got.chapters.iter().map(|c| c.used_fallback).collect::<Vec<_>>(), vec![true, false]);
    assert!(!got.mixed_engines);

    db.update_chapter_status(b, ChapterStatus::Done, ChapterOutcome {
        output_mp3_path: Some("/b.mp3"), engine: Some("chatterbox-turbo"), duration: Some(1.0), ..Default::default()
    }).unwrap();
    assert!(books::get_book(&db, book.id).unwrap().mixed_engines);
}

// test_book_reports_duration_only_for_done_chapters,
// test_book_measures_and_stores_duration_of_chapters_finished_before_durations_were_kept
#[test]
fn chapter_duration_is_reported_for_done_chapters_and_measured_once() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    assert_eq!(books::get_book(&db, book.id).unwrap().chapters[0].duration, None);

    let mp3 = dir.path().join("ch1.mp3");
    silent_mp3(&mp3, 2.0);
    let id = book.chapters[0].id;
    db.update_chapter_status(id, ChapterStatus::Done,
        ChapterOutcome { output_mp3_path: Some(&mp3.to_string_lossy()), ..Default::default() }).unwrap();

    let duration = books::get_book(&db, book.id).unwrap().chapters[0].duration.unwrap();
    assert!((duration - 2.0).abs() < 0.1);
    assert_eq!(db.get_chapter(id).unwrap().duration, Some(duration));
}

// test_assign_voice_to_book_persists, test_assign_voice_to_book_returns_404_for_unknown_book
#[test]
fn assign_voice_persists() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    let voice = voice(&db, dir.path(), "Nova", None, None);
    books::assign_voice(&db, book.id, Some(voice.id)).unwrap();
    assert_eq!(db.get_book(book.id).unwrap().voice_profile_id, Some(voice.id));
    books::assign_voice(&db, book.id, None).unwrap();
    assert_eq!(db.get_book(book.id).unwrap().voice_profile_id, None);
    assert_eq!(api_err(books::assign_voice(&db, 999, None)), "book not found");
}

// test_delete_book_removes_book_cache_and_staged_upload_but_keeps_mp3s
#[test]
fn delete_book_removes_rows_cache_and_staged_copy_but_keeps_mp3s() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    let staged = std::path::PathBuf::from(db.get_book(book.id).unwrap().source_path);
    let cache = ChunkCache::new(paths.cache());
    fs::create_dir_all(cache.chapter_dir(book.chapters[0].id)).unwrap();
    fs::create_dir_all(dir.path().join("out")).unwrap();
    let mp3 = write_file(&dir.path().join("out"), "01 - Chapter One.mp3", "ID3");

    books::delete_book(&db, &paths, book.id).unwrap();

    assert_eq!(api_err(books::get_book(&db, book.id)), "book not found");
    assert!(!cache.chapter_dir(book.chapters[0].id).exists());
    assert!(!staged.parent().unwrap().exists());
    assert!(paths.uploads().is_dir());
    assert!(mp3.is_file());
}

// test_delete_book_keeps_a_source_file_outside_the_upload_dir,
// test_delete_book_never_removes_upload_dir_for_a_file_directly_inside_it
#[test]
fn delete_book_only_removes_a_staged_folder_inside_uploads() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let outside = write_file(dir.path(), "mine.md", "# A\nB.\n");
    let a = md_book(&db, dir.path(), &outside.to_string_lossy());
    fs::create_dir_all(paths.uploads()).unwrap();
    let direct = write_file(&paths.uploads(), "direct.md", "# A\nB.\n");
    let b = md_book(&db, dir.path(), &fs::canonicalize(&direct).unwrap().to_string_lossy());

    books::delete_book(&db, &paths, a.id).unwrap();
    books::delete_book(&db, &paths, b.id).unwrap();

    assert!(outside.is_file());
    assert!(direct.is_file());
}

// test_delete_book_returns_404_for_unknown_book, test_delete_book_returns_409_while_a_job_is_queued
#[test]
fn delete_book_refuses_unknown_and_rendering_books() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let book = import(dir.path(), &paths, &db);
    db.create_job(book.chapters[1].id).unwrap();
    assert_eq!(api_err(books::delete_book(&db, &paths, 999)), "book not found");
    assert_eq!(api_err(books::delete_book(&db, &paths, book.id)),
               "This book is rendering. Stop rendering it before deleting it.");
}

// test_text_endpoint_returns_paragraphs_and_chapter_starts
#[test]
fn book_text_returns_paragraphs_and_chapter_starts() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let id = three(&db, dir.path());
    let text = books::book_text(&db, id).unwrap();
    assert_eq!(text.paragraphs, vec!["a", "b", "c", "d", "e", "f"]);
    assert_eq!(text.chapters.iter().map(|c| (c.title.as_str(), c.start, c.status)).collect::<Vec<_>>(),
               vec![("One", 0, "pending"), ("Two", 2, "pending"), ("Three", 5, "pending")]);
    assert_eq!(api_err(books::book_text(&db, 999)), "book not found");
}

fn breaks(list: &[(usize, &str)]) -> Vec<ChapterBreak> {
    list.iter().map(|&(start, title)| ChapterBreak { start, title: title.into() }).collect()
}

// test_put_chapters_renumbers_kept_audio_and_deletes_stale_audio
#[test]
fn chapter_breaks_renumber_kept_audio() {
    let dir = TempDir::new().unwrap();
    let (paths, mut db) = app(dir.path());
    let id = three(&db, dir.path());
    let out = dir.path().join("out");
    for ch in db.list_chapters(id).unwrap().into_iter().skip(1) {
        let (mp3, _) = export_chapter_mp3(&vec![0.0; 2205], 22050, &out, ch.order_index, &ch.title, "B", "N", None).unwrap();
        db.update_chapter_status(ch.id, ChapterStatus::Done,
            ChapterOutcome { output_mp3_path: Some(&mp3.to_string_lossy()), ..Default::default() }).unwrap();
    }

    // Split "One" in two: "Two" stays intact but moves to position 2; "Three" too.
    let book = books::save_chapter_breaks(&mut db, &paths, id,
        &breaks(&[(0, "One"), (1, "One b"), (2, "Two"), (5, "Three")])).unwrap();

    let ch = &book.chapters;
    assert_eq!(ch.iter().map(|c| c.title.as_str()).collect::<Vec<_>>(), vec!["One", "One b", "Two", "Three"]);
    assert_eq!(ch[2].status, "done");
    assert_eq!(ch[2].output_mp3_path.as_deref(), Some(&*out.join(chapter_filename(2, "Two")).to_string_lossy()));
    assert_eq!(ch[3].output_mp3_path.as_deref(), Some(&*out.join(chapter_filename(3, "Three")).to_string_lossy()));
    let mut names: Vec<String> = fs::read_dir(&out).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into()).collect();
    names.sort();
    assert_eq!(names, vec![chapter_filename(2, "Two"), chapter_filename(3, "Three")]);
}

// test_put_chapters_deletes_audio_of_removed_chapters
#[test]
fn chapter_breaks_delete_audio_and_cache_of_removed_chapters() {
    let dir = TempDir::new().unwrap();
    let (paths, mut db) = app(dir.path());
    let id = three(&db, dir.path());
    let two = db.list_chapters(id).unwrap().remove(1);
    let stale = write_file(dir.path(), "stale.mp3", "ID3fake");
    db.update_chapter_status(two.id, ChapterStatus::Done,
        ChapterOutcome { output_mp3_path: Some(&stale.to_string_lossy()), ..Default::default() }).unwrap();
    let cache = ChunkCache::new(paths.cache());
    fs::create_dir_all(cache.chapter_dir(two.id)).unwrap();

    books::save_chapter_breaks(&mut db, &paths, id, &breaks(&[(0, "One"), (3, "Two")])).unwrap();

    assert!(!stale.exists());
    assert!(!cache.chapter_dir(two.id).exists());
}

// test_put_chapters_rejects_invalid_breaks_and_unknown_books,
// test_put_chapters_refuses_while_the_book_is_rendering, test_put_chapters_allows_edits_once_jobs_have_finished
#[test]
fn chapter_breaks_refuse_bad_breaks_unknown_books_and_rendering_books() {
    let dir = TempDir::new().unwrap();
    let (paths, mut db) = app(dir.path());
    let id = three(&db, dir.path());
    assert!(!api_err(books::save_chapter_breaks(&mut db, &paths, id, &breaks(&[(2, "X")]))).is_empty());
    assert_eq!(api_err(books::save_chapter_breaks(&mut db, &paths, 999, &breaks(&[(0, "X")]))), "book not found");

    let job = db.create_job(db.list_chapters(id).unwrap()[2].id).unwrap();
    assert_eq!(api_err(books::save_chapter_breaks(&mut db, &paths, id, &breaks(&[(0, "X")]))),
               "This book is rendering. Pause or cancel it before changing chapter breaks.");
    db.finish_job(job.id, JobStatus::Done, None).unwrap();
    assert_eq!(books::save_chapter_breaks(&mut db, &paths, id, &breaks(&[(0, "X")])).unwrap().chapters.len(), 1);
}

// test_validate_output_dir_reports_writable_for_a_creatable_path,
// test_validate_output_dir_does_not_create_intermediate_directories,
// test_validate_output_dir_reports_error_for_a_permission_denied_path
#[test]
fn validate_output_dir_checks_the_nearest_existing_folder_without_creating_anything() {
    let dir = TempDir::new().unwrap();
    let wanted = dir.path().join("a/b/c");
    let ok = books::validate_output_dir(&wanted.to_string_lossy());
    assert!(ok.writable && ok.error.is_none());
    assert!(!dir.path().join("a").exists());

    let locked = dir.path().join("locked");
    fs::create_dir(&locked).unwrap();
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o500)).unwrap();
    let denied = books::validate_output_dir(&locked.join("x").to_string_lossy());
    assert!(!denied.writable);
    assert_eq!(denied.error.unwrap(), format!(
        "cannot write to '{}' — check the folder exists and you have permission", locked.join("x").display()));
}

// test_engines_endpoint_lists_both_engines_with_labels
#[test]
fn engines_lists_both_with_labels() {
    let engines = list_engines();
    assert_eq!(engines.default, "chatterbox-turbo");
    let ids: Vec<_> = engines.engines.iter().map(|e| (e.id, e.label, e.needs_voice)).collect();
    assert_eq!(ids, vec![("qwen3", "Quality (Qwen3)", true), ("chatterbox-turbo", "Speed (Turbo)", false)]);
}

#[test]
fn api_errors_serialize_as_their_message() {
    let e = audiobookify_core::app::ApiError("book not found".into());
    assert_eq!(serde_json::to_string(&e).unwrap(), "\"book not found\"");
}
```

- [ ] **Step 3: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test app_books`
Expected: compile error, `unresolved import audiobookify_core::app`.

- [ ] **Step 4: Implement `app/mod.rs`**

Create `desktop/core/src/app/mod.rs`:

```rust
//! What each Tauri command does, ported from api/app.py: one function per endpoint, over a
//! `Database` and the app's folders. desktop/src/commands.rs only adds locking and the
//! asset-protocol scope, so everything testable lives here.

pub mod books;
pub mod clips;
pub mod jobs;
pub mod lexicon;
pub mod voices;

use std::fmt;
use std::fs;
use std::path::PathBuf;

use serde::{Serialize, Serializer};

use crate::db::{self, Database, DbError};
use crate::voice_audio::TEMPO_RANGE;

/// Everything the app writes, under one root: the app data dir in the app, a temp dir in tests.
#[derive(Debug, Clone)]
pub struct AppPaths {
    pub root: PathBuf,
}

impl AppPaths {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }
    pub fn db(&self) -> PathBuf { self.root.join("audiobookify.db") }
    pub fn uploads(&self) -> PathBuf { self.root.join("uploads") }
    pub fn voices(&self) -> PathBuf { self.root.join("voices") }
    pub fn cache(&self) -> PathBuf { self.root.join("cache/synth") }
    pub fn text_clips(&self) -> PathBuf { self.root.join("text_clips") }
    pub fn output(&self) -> PathBuf { self.root.join("output") }
    pub fn log(&self) -> PathBuf { self.root.join("logs/audiobookify.log") }

    pub fn open_db(&self) -> db::Result<Database> {
        fs::create_dir_all(&self.root)?;
        Database::open(self.db(), self.voices())
    }

    pub fn runner_paths(&self) -> crate::jobs::Paths {
        crate::jobs::Paths { cache_root: self.cache(), text_clip_dir: self.text_clips(), backup_dir: self.output() }
    }
}

/// A command's error: the message the UI shows, FastAPI's `detail`. It reaches JS as a
/// plain string.
#[derive(Debug, Clone, PartialEq)]
pub struct ApiError(pub String);

pub type ApiResult<T> = Result<T, ApiError>;

impl Serialize for ApiError {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str(&self.0)
    }
}

impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl From<DbError> for ApiError {
    fn from(e: DbError) -> Self {
        match e {
            DbError::NotFound(m) | DbError::Invalid(m) => ApiError(m),
            other => ApiError(other.to_string()),
        }
    }
}

impl From<std::io::Error> for ApiError {
    fn from(e: std::io::Error) -> Self {
        ApiError(e.to_string())
    }
}

pub(crate) fn err<T>(message: impl Into<String>) -> ApiResult<T> {
    Err(ApiError(message.into()))
}

/// `result`, with a missing row reported as app.py's 404 detail, `"{what} not found"`.
pub(crate) fn found<T>(result: db::Result<T>, what: &str) -> ApiResult<T> {
    match result {
        Err(DbError::NotFound(_)) => err(format!("{what} not found")),
        other => Ok(other?),
    }
}

#[derive(Debug, Serialize)]
pub struct EngineSpec {
    pub id: &'static str,
    pub label: &'static str,
    pub description: &'static str,
    pub needs_voice: bool,
}

// ponytail: copied from engines/*.py so commands can validate without loading a model;
// phase 6 deletes the Python registry and this becomes the only copy.
pub const ENGINES: [EngineSpec; 2] = [
    EngineSpec {
        id: "qwen3",
        label: "Quality (Qwen3)",
        description: "Most natural; needs a voice with a transcript. About 0.85 s per second of audio.",
        needs_voice: true,
    },
    EngineSpec {
        id: "chatterbox-turbo",
        label: "Speed (Turbo)",
        description: "About twice as fast, slightly less natural.",
        needs_voice: false,
    },
];

#[derive(Debug, Serialize)]
pub struct Engines {
    pub default: &'static str,
    pub engines: &'static [EngineSpec],
}

pub fn list_engines() -> Engines {
    Engines { default: crate::models::DEFAULT_ENGINE, engines: &ENGINES }
}

/// The engine a request asks for (or the stored one), validated.
pub(crate) fn chosen_engine(requested: Option<&str>, current: &str, voice_profile_id: Option<i64>) -> ApiResult<String> {
    let id = requested.filter(|e| !e.is_empty()).unwrap_or(current);
    let Some(spec) = ENGINES.iter().find(|e| e.id == id) else {
        return err(format!("unknown engine '{id}'"));
    };
    if spec.needs_voice && voice_profile_id.is_none() {
        return err(format!("{} needs a voice: pick one first", spec.label));
    }
    Ok(id.to_string())
}

pub(crate) fn check_tempo(tempo: f64) -> ApiResult<f64> {
    let (lo, hi) = TEMPO_RANGE;
    if !(lo..=hi).contains(&tempo) {
        return err(format!("tempo must be a number from {lo} to {hi}"));
    }
    Ok((tempo * 100.0).round() / 100.0)
}
```

If `DbError::Io` has no `#[from] io::Error`, add it in `desktop/core/src/db/mod.rs`, because `open_db` uses `?` on an `io::Error`.

- [ ] **Step 5: Implement `app/books.rs`**

```rust
//! Book endpoints: import, list, delete, chapter text and breaks, voice, output folder.

use std::collections::HashSet;
use std::ffi::{CString, OsStr};
use std::fs;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::{err, found, ApiResult, AppPaths};
use crate::audio::{chapter_filename, relabel_chapter_mp3};
use crate::db::{book_paragraphs, Database};
use crate::ingest::ingest_by_path;
use crate::jobs::ChunkCache;
use crate::media;
use crate::models::{Book, Chapter, ChapterStatus, Job, JobStatus};

#[derive(Debug, Serialize)]
pub struct ChapterDto {
    pub id: i64,
    pub order_index: i64,
    pub title: String,
    pub status: &'static str,
    pub output_mp3_path: Option<String>,
    pub error: Option<String>,
    pub progress: Option<f64>,
    pub started_at: Option<f64>,
    pub used_fallback: bool,
    pub engine: Option<String>,
    pub duration: Option<f64>,
}

#[derive(Debug, Serialize)]
pub struct BookDto {
    pub id: i64,
    pub title: String,
    pub author: Option<String>,
    pub output_dir: String,
    pub voice_profile_id: Option<i64>,
    pub engine: String,
    pub mixed_engines: bool,
    pub chapters: Vec<ChapterDto>,
}

/// The chapter's latest job, if it is queued or generating.
pub(crate) fn active_job(db: &Database, chapter_id: i64) -> ApiResult<Option<Job>> {
    Ok(db.latest_job_for_chapter(chapter_id)?
        .filter(|j| matches!(j.status, JobStatus::Queued | JobStatus::Generating)))
}

pub(crate) fn rendering(db: &Database, book_id: i64) -> ApiResult<bool> {
    for chapter in db.list_chapters(book_id)? {
        if active_job(db, chapter.id)?.is_some() {
            return Ok(true);
        }
    }
    Ok(false)
}

fn chapter_duration(db: &Database, chapter: &Chapter) -> Option<f64> {
    if chapter.status != ChapterStatus::Done {
        return None;
    }
    if chapter.duration.is_none() {
        // Chapters finished before durations were stored: measure once, keep it.
        let duration = media::duration(Path::new(chapter.output_mp3_path.as_deref()?)).ok()?;
        db.set_chapter_duration(chapter.id, duration).ok()?;
        return Some(duration);
    }
    chapter.duration
}

pub fn book_dto(db: &Database, book: &Book) -> ApiResult<BookDto> {
    let mut chapters = Vec::new();
    for c in db.list_chapters(book.id)? {
        let job = active_job(db, c.id)?;
        let duration = chapter_duration(db, &c);
        chapters.push(ChapterDto {
            id: c.id,
            order_index: c.order_index,
            status: c.status.as_str(),
            progress: job.as_ref().and_then(|j| j.progress),
            started_at: job.as_ref().and_then(|j| j.started_at),
            used_fallback: c.used_fallback,
            duration,
            title: c.title,
            output_mp3_path: c.output_mp3_path,
            error: c.error,
            engine: c.engine,
        });
    }
    let done_engines: HashSet<&Option<String>> =
        chapters.iter().filter(|c| c.status == ChapterStatus::Done.as_str()).map(|c| &c.engine).collect();
    Ok(BookDto {
        id: book.id,
        title: book.title.clone(),
        author: book.author.clone(),
        output_dir: book.output_dir.clone(),
        voice_profile_id: book.voice_profile_id,
        engine: book.engine.clone(),
        mixed_engines: done_engines.len() > 1,
        chapters,
    })
}

pub fn list_books(db: &Database) -> ApiResult<Vec<BookDto>> {
    db.list_books()?.iter().map(|b| book_dto(db, b)).collect()
}

pub fn get_book(db: &Database, book_id: i64) -> ApiResult<BookDto> {
    book_dto(db, &found(db.get_book(book_id), "book")?)
}

fn check_writable(path: &Path) -> bool {
    let probe = path.join(".audiobookify_write_test");
    fs::create_dir_all(path).is_ok() && fs::write(&probe, b"").is_ok() && fs::remove_file(&probe).is_ok()
}

/// Imports an EPUB or Markdown file. The file is copied to `uploads/<uuid>/` first, so
/// moving or deleting the original can't break the book.
pub fn create_book(db: &Database, paths: &AppPaths, source: &Path, output_dir: &str) -> ApiResult<BookDto> {
    let Some(file_name) = source.file_name() else { return err("no file chosen") };
    let suffix = source.extension().map(|e| format!(".{}", e.to_string_lossy().to_lowercase())).unwrap_or_default();
    if suffix != ".epub" && suffix != ".md" {
        return err(format!("unsupported input format: {suffix}"));
    }
    let output = std::path::absolute(output_dir.trim())?;
    if !check_writable(&output) {
        return err(format!(
            "cannot write to '{}' — check the folder exists and you have permission to write to it",
            output.display()
        ));
    }
    let output = fs::canonicalize(&output)?;

    let staged_dir = paths.uploads().join(uuid::Uuid::new_v4().simple().to_string());
    fs::create_dir_all(&staged_dir)?;
    let staged = staged_dir.join(file_name);
    fs::copy(source, &staged)?;
    let staged = fs::canonicalize(&staged)?;
    let (parsed, format) = match ingest_by_path(&staged, 1) {
        Ok(parsed) => parsed,
        Err(e) => {
            let _ = fs::remove_dir_all(&staged_dir);
            return err(e.to_string());
        }
    };
    let book = db.create_book(&parsed.title, parsed.author.as_deref(), &staged.to_string_lossy(), format,
                              &output.to_string_lossy())?;
    db.add_chapters(book.id, &parsed.chapters)?;
    book_dto(db, &book)
}

pub fn delete_book(db: &Database, paths: &AppPaths, book_id: i64) -> ApiResult<()> {
    let book = found(db.get_book(book_id), "book")?;
    if rendering(db, book_id)? {
        return err("This book is rendering. Stop rendering it before deleting it.");
    }
    let cache = ChunkCache::new(paths.cache());
    for chapter in db.delete_book(book_id)? {
        cache.clear(chapter.id);
    }
    // Rendered MP3s stay. Only remove the copy we staged, never anything outside uploads.
    let root = fs::canonicalize(paths.uploads());
    let staged = Path::new(&book.source_path).parent().map(fs::canonicalize);
    if let (Ok(root), Some(Ok(staged))) = (root, staged) {
        if staged != root && staged.starts_with(&root) {
            let _ = fs::remove_dir_all(staged);
        }
    }
    Ok(())
}

#[derive(Debug, Serialize)]
pub struct ChapterStart {
    pub id: i64,
    pub title: String,
    pub start: usize,
    pub status: &'static str,
}

#[derive(Debug, Serialize)]
pub struct BookText {
    pub paragraphs: Vec<String>,
    pub chapters: Vec<ChapterStart>,
}

pub fn book_text(db: &Database, book_id: i64) -> ApiResult<BookText> {
    found(db.get_book(book_id), "book")?;
    let chapters = db.list_chapters(book_id)?;
    let (paragraphs, starts) = book_paragraphs(&chapters);
    Ok(BookText {
        paragraphs: paragraphs.into_iter().map(String::from).collect(),
        chapters: chapters.iter().zip(starts)
            .map(|(c, start)| ChapterStart { id: c.id, title: c.title.clone(), start, status: c.status.as_str() })
            .collect(),
    })
}

#[derive(Debug, Clone, Deserialize)]
pub struct ChapterBreak {
    pub start: usize,
    pub title: String,
}

pub fn save_chapter_breaks(db: &mut Database, paths: &AppPaths, book_id: i64, breaks: &[ChapterBreak])
                           -> ApiResult<BookDto> {
    let book = found(db.get_book(book_id), "book")?;
    if rendering(db, book_id)? {
        return err("This book is rendering. Pause or cancel it before changing chapter breaks.");
    }
    let pairs: Vec<(usize, &str)> = breaks.iter().map(|b| (b.start, b.title.as_str())).collect();
    let removed = db.replace_chapter_breaks(book_id, &pairs)?;

    let cache = ChunkCache::new(paths.cache());
    for chapter in &removed {
        // their audio no longer matches any chapter's text
        if let Some(mp3) = &chapter.output_mp3_path {
            let _ = fs::remove_file(mp3);
        }
        cache.clear(chapter.id);
    }

    // Kept chapters may have moved or been renamed. Park the files first so a chapter
    // taking a neighbour's old filename can't overwrite it, then give them their new names.
    let stale: Vec<(Chapter, PathBuf)> = db.list_chapters(book_id)?.into_iter()
        .filter_map(|c| {
            let mp3 = PathBuf::from(c.output_mp3_path.as_deref()?);
            let wanted = chapter_filename(c.order_index, &c.title);
            (mp3.is_file() && mp3.file_name()? != OsStr::new(&wanted)).then_some((c, mp3))
        })
        .collect();
    let mut parked = Vec::new();
    for (chapter, mp3) in stale {
        let tmp = mp3.with_extension(format!("relabel-{}.mp3", chapter.id));
        fs::rename(&mp3, &tmp)?;
        parked.push((chapter, tmp));
    }
    for (chapter, tmp) in parked {
        let path = relabel_chapter_mp3(&tmp, chapter.order_index, &chapter.title).unwrap_or_else(|_| {
            // Keep the audio playable wherever it ended up rather than lose track of it.
            if tmp.is_file() { tmp.clone() } else { tmp.with_file_name(chapter_filename(chapter.order_index, &chapter.title)) }
        });
        db.set_chapter_audio_path(chapter.id, &path.to_string_lossy())?;
    }
    book_dto(db, &book)
}

pub fn assign_voice(db: &Database, book_id: i64, voice_profile_id: Option<i64>) -> ApiResult<()> {
    found(db.get_book(book_id), "book")?;
    db.update_book_voice_profile(book_id, voice_profile_id)?;
    Ok(())
}

#[derive(Debug, Serialize)]
pub struct DirCheck {
    pub path: String,
    pub writable: bool,
    pub error: Option<String>,
}

fn can_write_into(dir: &Path) -> bool {
    let Ok(c) = CString::new(dir.as_os_str().as_bytes()) else { return false };
    // SAFETY: `c` is a valid NUL-terminated path for the duration of the call.
    unsafe { libc::access(c.as_ptr(), libc::W_OK | libc::X_OK) == 0 }
}

/// Whether the app could create and write `path`, judged from its nearest existing folder.
/// Creates nothing.
pub fn validate_output_dir(path: &str) -> DirCheck {
    let resolved = std::path::absolute(path.trim()).unwrap_or_else(|_| PathBuf::from(path));
    let existing = resolved.ancestors().find(|p| p.exists()).unwrap_or(Path::new("/"));
    let writable = existing.is_dir() && can_write_into(existing);
    let shown = resolved.display().to_string();
    DirCheck {
        error: (!writable).then(|| format!("cannot write to '{shown}' — check the folder exists and you have permission")),
        path: shown,
        writable,
    }
}
```

`as_str()` must return `&'static str` for the `status` fields. If the `text_enum!` macro's `as_str` borrows `self`, change it to `pub fn as_str(self) -> &'static str` (the enums are `Copy`).

Create empty `desktop/core/src/app/{clips,jobs,lexicon,voices}.rs` (each with a one-line `//!` doc: "Text-clip endpoints.", "Generation endpoints.", "Pronunciation endpoints.", "Voice endpoints.") so the module compiles. Tasks 4 and 5 fill them.

Add `pub mod app;` to `desktop/core/src/lib.rs` (first, alphabetically).

- [ ] **Step 6: Run the tests to see them pass**

Run: `cargo test -p audiobookify-core --test app_books`
Expected: 21 passed.

Then run the whole core suite to catch fallout from the `as_str`, `Word` or `DbError` tweaks: `cargo test -p audiobookify-core`. Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add desktop/core/src/app desktop/core/src/lib.rs desktop/core/src/db/mod.rs desktop/core/src/models.rs desktop/core/tests/common/mod.rs desktop/core/tests/app_books.rs
git commit -m "Port the book endpoints to audiobookify_core::app

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Generation, cancel, pause and chapter audio

**Files:**
- Modify: `desktop/core/src/app/jobs.rs`
- Create: `desktop/core/tests/app_jobs.rs`

**Interfaces:**
- Consumes: `books::{active_job, BookDto}`, `chosen_engine`, `found`, `err`, `ChunkCache`.
- Produces (`app::jobs`): `Queued { queued_job_ids: Vec<i64> }`, `QueuedOne { job_id: Option<i64> }`, `WorkerStatus { paused: bool }`; `generate_book(&Database, i64, Option<&str>) -> ApiResult<Queued>`, `generate_chapter(&Database, i64, i64, Option<&str>) -> ApiResult<QueuedOne>`, `cancel_chapter(&Database, &AppPaths, i64, i64) -> ApiResult<()>`, `cancel_book(&Database, &AppPaths, i64) -> ApiResult<()>`, `worker_status(&Database) -> ApiResult<WorkerStatus>`, `set_paused(&Database, bool) -> ApiResult<WorkerStatus>`, `chapter_audio_path(&Database, i64, i64) -> ApiResult<PathBuf>`.

- [ ] **Step 1: Write the failing tests**

Create `desktop/core/tests/app_jobs.rs`:

```rust
mod common;

use std::fs;

use audiobookify_core::app::books::{self, BookDto};
use audiobookify_core::app::{jobs, AppPaths};
use audiobookify_core::db::Database;
use audiobookify_core::jobs::ChunkCache;
use audiobookify_core::models::{ChapterOutcome, ChapterStatus, JobStatus};
use common::*;
use tempfile::TempDir;

fn setup(dir: &std::path::Path) -> (AppPaths, Database, BookDto) {
    let (paths, db) = app(dir);
    let src = write_file(dir, "book.md", TWO_CHAPTERS_MD);
    let book = books::create_book(&db, &paths, &src, &path(dir, "out")).unwrap();
    (paths, db, book)
}

fn finish(db: &Database, chapter_id: i64, mp3: Option<&str>) {
    db.update_chapter_status(chapter_id, ChapterStatus::Done,
        ChapterOutcome { output_mp3_path: mp3, duration: Some(1.0), ..Default::default() }).unwrap();
}

// test_generate_endpoint_creates_queued_jobs_for_all_chapters,
// ..._does_not_duplicate_jobs_for_already_queued_chapters, ..._does_not_requeue_already_done_chapters
#[test]
fn generate_book_queues_each_unfinished_chapter_once() {
    let dir = TempDir::new().unwrap();
    let (_p, db, book) = setup(dir.path());
    finish(&db, book.chapters[0].id, Some("/a.mp3"));

    let first = jobs::generate_book(&db, book.id, None).unwrap();
    let again = jobs::generate_book(&db, book.id, None).unwrap();

    assert_eq!(first.queued_job_ids.len(), 1);
    assert_eq!(db.get_job(first.queued_job_ids[0]).unwrap().chapter_id, book.chapters[1].id);
    assert!(again.queued_job_ids.is_empty());
}

// test_generate_endpoint_returns_404_for_unknown_book,
// test_generate_rejects_unknown_engines_and_quality_without_a_voice
#[test]
fn generate_book_rejects_unknown_books_and_bad_engines() {
    let dir = TempDir::new().unwrap();
    let (_p, db, book) = setup(dir.path());
    assert_eq!(api_err(jobs::generate_book(&db, 999, None)), "book not found");
    assert_eq!(api_err(jobs::generate_book(&db, book.id, Some("nope"))), "unknown engine 'nope'");
    assert_eq!(api_err(jobs::generate_book(&db, book.id, Some("qwen3"))),
               "Quality (Qwen3) needs a voice: pick one first");
}

// test_generating_a_book_stores_the_chosen_engine_for_every_later_job
#[test]
fn generate_book_stores_the_engine() {
    let dir = TempDir::new().unwrap();
    let (_p, db, book) = setup(dir.path());
    let voice = voice(&db, dir.path(), "Nova", Some("hi"), Some("done"));
    books::assign_voice(&db, book.id, Some(voice.id)).unwrap();
    jobs::generate_book(&db, book.id, Some("qwen3")).unwrap();
    assert_eq!(db.get_book(book.id).unwrap().engine, "qwen3");
}

// test_generate_chapter_endpoint_queues_only_that_chapter, ..._does_not_requeue_an_already_queued_chapter,
// ..._does_not_requeue_an_already_done_chapter
#[test]
fn generate_chapter_queues_only_that_chapter_once() {
    let dir = TempDir::new().unwrap();
    let (_p, db, book) = setup(dir.path());
    let (a, b) = (book.chapters[0].id, book.chapters[1].id);

    let queued = jobs::generate_chapter(&db, book.id, b, None).unwrap();
    assert_eq!(db.get_job(queued.job_id.unwrap()).unwrap().chapter_id, b);
    assert!(db.latest_job_for_chapter(a).unwrap().is_none());
    assert_eq!(jobs::generate_chapter(&db, book.id, b, None).unwrap().job_id, None);

    finish(&db, a, Some("/a.mp3"));
    assert_eq!(jobs::generate_chapter(&db, book.id, a, None).unwrap().job_id, None);
}

// test_generate_chapter_endpoint_returns_404_for_unknown_book, ..._for_unknown_chapter,
// ..._when_chapter_belongs_to_a_different_book
#[test]
fn generate_chapter_rejects_unknown_and_foreign_chapters() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    let src = write_file(dir.path(), "other.md", TWO_CHAPTERS_MD);
    let other = books::create_book(&db, &paths, &src, &path(dir.path(), "out")).unwrap();
    assert_eq!(api_err(jobs::generate_chapter(&db, 999, book.chapters[0].id, None)), "book not found");
    assert_eq!(api_err(jobs::generate_chapter(&db, book.id, 999, None)), "chapter not found");
    assert_eq!(api_err(jobs::generate_chapter(&db, book.id, other.chapters[0].id, None)), "chapter not found");
}

// test_regenerating_one_chapter_with_another_engine_switches_the_book
#[test]
fn generating_a_chapter_with_another_engine_switches_the_book() {
    let dir = TempDir::new().unwrap();
    let (_p, db, book) = setup(dir.path());
    let voice = voice(&db, dir.path(), "Nova", Some("hi"), Some("done"));
    books::assign_voice(&db, book.id, Some(voice.id)).unwrap();
    jobs::generate_chapter(&db, book.id, book.chapters[0].id, Some("qwen3")).unwrap();
    assert_eq!(db.get_book(book.id).unwrap().engine, "qwen3");
}

// test_worker_status_reports_not_paused_by_default, test_worker_pause_then_status_reports_paused,
// test_worker_resume_after_pause_reports_not_paused
#[test]
fn worker_pause_and_resume() {
    let dir = TempDir::new().unwrap();
    let (_p, db, _b) = setup(dir.path());
    assert!(!jobs::worker_status(&db).unwrap().paused);
    assert!(jobs::set_paused(&db, true).unwrap().paused);
    assert!(jobs::worker_status(&db).unwrap().paused);
    assert!(!jobs::set_paused(&db, false).unwrap().paused);
    assert!(!jobs::worker_status(&db).unwrap().paused);
}

// test_cancel_chapter_with_a_queued_job_marks_it_cancelled_and_resets_the_chapter,
// test_cancel_chapter_clears_the_cache_for_a_paused_then_cancelled_job,
// test_get_book_does_not_report_stale_progress_after_cancelling_a_queued_job
#[test]
fn cancelling_a_queued_chapter_cancels_the_job_resets_the_chapter_and_clears_its_cache() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    let ch = book.chapters[0].id;
    let job = db.create_job(ch).unwrap();
    db.update_job_progress(job.id, 0.4).unwrap();
    let cache = ChunkCache::new(paths.cache());
    fs::create_dir_all(cache.chapter_dir(ch)).unwrap();

    jobs::cancel_chapter(&db, &paths, book.id, ch).unwrap();

    assert_eq!(db.get_job(job.id).unwrap().status, JobStatus::Cancelled);
    assert_eq!(db.get_chapter(ch).unwrap().status, ChapterStatus::Pending);
    assert!(!cache.chapter_dir(ch).exists());
    assert_eq!(books::get_book(&db, book.id).unwrap().chapters[0].progress, None);
}

// test_cancel_chapter_with_a_generating_job_sets_cancel_requested_without_changing_status_yet
#[test]
fn cancelling_a_generating_chapter_asks_the_runner_to_stop() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    db.create_job(book.chapters[0].id).unwrap();
    let job = db.pop_next_queued_job().unwrap().unwrap();

    jobs::cancel_chapter(&db, &paths, book.id, book.chapters[0].id).unwrap();

    assert!(db.is_job_cancel_requested(job.id).unwrap());
    assert_eq!(db.get_job(job.id).unwrap().status, JobStatus::Generating);
}

// test_cancel_chapter_with_no_active_job_is_a_noop, test_cancel_chapter_is_a_noop_for_an_already_cancelled_job
#[test]
fn cancelling_a_chapter_without_an_active_job_changes_nothing() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    let ch = book.chapters[0].id;
    jobs::cancel_chapter(&db, &paths, book.id, ch).unwrap();
    let job = db.create_job(ch).unwrap();
    jobs::cancel_chapter(&db, &paths, book.id, ch).unwrap();
    jobs::cancel_chapter(&db, &paths, book.id, ch).unwrap();
    assert_eq!(db.get_job(job.id).unwrap().status, JobStatus::Cancelled);
}

// test_cancel_book_returns_404_for_an_unknown_book, test_cancel_chapter_returns_404_for_a_chapter_in_a_different_book
#[test]
fn cancel_rejects_unknown_books_and_foreign_chapters() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    let src = write_file(dir.path(), "other.md", TWO_CHAPTERS_MD);
    let other = books::create_book(&db, &paths, &src, &path(dir.path(), "out")).unwrap();
    assert_eq!(api_err(jobs::cancel_book(&db, &paths, 999)), "book not found");
    assert_eq!(api_err(jobs::cancel_chapter(&db, &paths, 999, book.chapters[0].id)), "book not found");
    assert_eq!(api_err(jobs::cancel_chapter(&db, &paths, book.id, other.chapters[0].id)), "chapter not found");
}

// test_cancel_book_cancels_every_chapter_with_an_active_job
#[test]
fn cancel_book_cancels_every_active_chapter() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    let ids = jobs::generate_book(&db, book.id, None).unwrap().queued_job_ids;
    jobs::cancel_book(&db, &paths, book.id).unwrap();
    for id in ids {
        assert_eq!(db.get_job(id).unwrap().status, JobStatus::Cancelled);
    }
}

// test_chapter_audio_serves_the_finished_mp3, test_chapter_audio_404s_until_the_chapter_is_done,
// test_chapter_audio_404s_when_the_file_is_missing, test_chapter_audio_404s_for_unknown_book_or_a_chapter_of_another_book
#[test]
fn chapter_audio_path_is_only_given_for_a_finished_file() {
    let dir = TempDir::new().unwrap();
    let (paths, db, book) = setup(dir.path());
    let (a, b) = (book.chapters[0].id, book.chapters[1].id);
    assert_eq!(api_err(jobs::chapter_audio_path(&db, book.id, a)), "audio not ready");

    let mp3 = write_file(dir.path(), "a.mp3", "ID3");
    finish(&db, a, Some(&mp3.to_string_lossy()));
    assert_eq!(jobs::chapter_audio_path(&db, book.id, a).unwrap(), mp3);

    finish(&db, b, Some(&path(dir.path(), "gone.mp3")));
    assert_eq!(api_err(jobs::chapter_audio_path(&db, book.id, b)), "audio not ready");

    let src = write_file(dir.path(), "other.md", TWO_CHAPTERS_MD);
    let other = books::create_book(&db, &paths, &src, &path(dir.path(), "out")).unwrap();
    assert_eq!(api_err(jobs::chapter_audio_path(&db, other.id, a)), "chapter not found");
    assert_eq!(api_err(jobs::chapter_audio_path(&db, book.id, 999)), "chapter not found");
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test app_jobs`
Expected: compile errors, `cannot find function generate_book in module jobs` and similar.

- [ ] **Step 3: Implement `app/jobs.rs`**

```rust
//! Generation endpoints: queue, cancel, pause, and the finished chapter's file.

use std::path::{Path, PathBuf};

use serde::Serialize;

use super::books::active_job;
use super::{chosen_engine, err, found, ApiResult, AppPaths};
use crate::db::Database;
use crate::jobs::ChunkCache;
use crate::models::{Chapter, ChapterOutcome, ChapterStatus, JobStatus};

#[derive(Debug, Serialize)]
pub struct Queued {
    pub queued_job_ids: Vec<i64>,
}

#[derive(Debug, Serialize)]
pub struct QueuedOne {
    pub job_id: Option<i64>,
}

#[derive(Debug, Serialize)]
pub struct WorkerStatus {
    pub paused: bool,
}

/// The chapter, if it exists and belongs to `book_id`.
fn chapter_of(db: &Database, book_id: i64, chapter_id: i64) -> ApiResult<Chapter> {
    let chapter = found(db.get_chapter(chapter_id), "chapter")?;
    if chapter.book_id != book_id {
        return err("chapter not found");
    }
    Ok(chapter)
}

pub fn generate_book(db: &Database, book_id: i64, engine: Option<&str>) -> ApiResult<Queued> {
    let book = found(db.get_book(book_id), "book")?;
    db.update_book_engine(book.id, &chosen_engine(engine, &book.engine, book.voice_profile_id)?)?;
    let mut queued_job_ids = Vec::new();
    for chapter in db.list_chapters(book.id)? {
        if chapter.status == ChapterStatus::Done || active_job(db, chapter.id)?.is_some() {
            continue;
        }
        queued_job_ids.push(db.create_job(chapter.id)?.id);
    }
    Ok(Queued { queued_job_ids })
}

pub fn generate_chapter(db: &Database, book_id: i64, chapter_id: i64, engine: Option<&str>) -> ApiResult<QueuedOne> {
    let book = found(db.get_book(book_id), "book")?;
    let chapter = chapter_of(db, book_id, chapter_id)?;
    let engine = chosen_engine(engine, &book.engine, book.voice_profile_id)?;
    if chapter.status == ChapterStatus::Done || active_job(db, chapter.id)?.is_some() {
        return Ok(QueuedOne { job_id: None });
    }
    db.update_book_engine(book.id, &engine)?; // the book's chapters all use one engine
    Ok(QueuedOne { job_id: Some(db.create_job(chapter.id)?.id) })
}

/// A queued job is cancelled here; a generating one is asked to stop, and the runner
/// cancels it after its current chunk.
fn cancel_active(db: &Database, cache: &ChunkCache, chapter: &Chapter) -> ApiResult<()> {
    match active_job(db, chapter.id)? {
        Some(job) if job.status == JobStatus::Queued => {
            db.finish_job(job.id, JobStatus::Cancelled, None)?;
            db.update_chapter_status(chapter.id, ChapterStatus::Pending, ChapterOutcome::default())?;
            cache.clear(chapter.id);
        }
        Some(job) => db.request_job_cancel(job.id)?,
        None => {}
    }
    Ok(())
}

pub fn cancel_chapter(db: &Database, paths: &AppPaths, book_id: i64, chapter_id: i64) -> ApiResult<()> {
    found(db.get_book(book_id), "book")?;
    let chapter = chapter_of(db, book_id, chapter_id)?;
    cancel_active(db, &ChunkCache::new(paths.cache()), &chapter)
}

pub fn cancel_book(db: &Database, paths: &AppPaths, book_id: i64) -> ApiResult<()> {
    found(db.get_book(book_id), "book")?;
    let cache = ChunkCache::new(paths.cache());
    for chapter in db.list_chapters(book_id)? {
        cancel_active(db, &cache, &chapter)?;
    }
    Ok(())
}

pub fn worker_status(db: &Database) -> ApiResult<WorkerStatus> {
    Ok(WorkerStatus { paused: db.is_paused()? })
}

pub fn set_paused(db: &Database, paused: bool) -> ApiResult<WorkerStatus> {
    db.set_paused(paused)?;
    Ok(WorkerStatus { paused })
}

pub fn chapter_audio_path(db: &Database, book_id: i64, chapter_id: i64) -> ApiResult<PathBuf> {
    let chapter = chapter_of(db, book_id, chapter_id)?;
    match chapter.output_mp3_path {
        Some(mp3) if chapter.status == ChapterStatus::Done && Path::new(&mp3).is_file() => Ok(mp3.into()),
        _ => err("audio not ready"),
    }
}
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `cargo test -p audiobookify-core --test app_jobs`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add desktop/core/src/app/jobs.rs desktop/core/tests/app_jobs.rs
git commit -m "Port the generate, cancel, pause and chapter-audio endpoints

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Voices, pronunciations and text clips

**Files:**
- Modify: `desktop/core/src/app/voices.rs`, `desktop/core/src/app/lexicon.rs`, `desktop/core/src/app/clips.rs`
- Create: `desktop/core/tests/app_voices.rs`

**Interfaces:**
- Consumes: `voice_audio::{validate_reference_audio, reference_pace, reference_clip, Pace}`, `voice_storage::{store_reference_audio, delete_voice_storage}`, `media::duration`, `check_tempo`, `chosen_engine`, `found`, `err`.
- Produces (`app::voices`): `VoiceDto`; `list_voices(&Database)`, `create_voice(&Database, &AppPaths, source: &Path, name: &str, reference_transcript: Option<&str>)`, `set_voice_transcript(&Database, i64, &str)`, `set_voice_tempo(&Database, i64, f64)`, `transcribe_voice(&Database, i64)` (each `-> ApiResult<VoiceDto>`), `delete_voice(&Database, &AppPaths, i64) -> ApiResult<()>`, `voice_audio_path(&Database, i64, Option<f64>) -> ApiResult<PathBuf>`.
- Produces (`app::lexicon`): `LexiconDto { term, override_value, notes }`; `list_pronunciations(&Database)`, `create_pronunciation(&Database, &str, &str, Option<&str>)`, `update_pronunciation(&Database, &str, &str)`, `delete_pronunciation(&Database, &str) -> ApiResult<()>`.
- Produces (`app::clips`): `TextClipDto`; `list_text_clips(&Database)`, `create_text_clip(&Database, &str, Option<i64>, Option<&str>, Option<f64>)`, `get_text_clip(&Database, i64)`, `delete_text_clip(&Database, i64) -> ApiResult<()>`, `text_clip_audio_path(&Database, i64) -> ApiResult<PathBuf>`, `save_text_clip(&Database, i64, dest: &Path) -> ApiResult<()>`.

- [ ] **Step 1: Write the failing tests**

Create `desktop/core/tests/app_voices.rs`:

```rust
mod common;

use std::fs;
use std::path::Path;

use audiobookify_core::app::{clips, lexicon, voices, AppPaths};
use audiobookify_core::db::Database;
use audiobookify_core::models::JobStatus;
use audiobookify_core::voice_storage::permanent_voice_dir;
use common::*;
use serde_json::json;
use tempfile::TempDir;

fn clone_nova(dir: &Path, paths: &AppPaths, db: &Database, transcript: Option<&str>) -> voices::VoiceDto {
    let clip = dir.join("nova.wav");
    clean_tone_wav(&clip);
    voices::create_voice(db, paths, &clip, "Nova", transcript).unwrap()
}

// --- voices ---

// test_create_voice_uploads_reference_audio_and_returns_profile,
// test_create_voice_stores_reference_audio_under_voices_dir_not_uploads,
// test_a_new_voice_is_queued_for_transcription_and_reports_its_length
#[test]
fn create_voice_stores_a_copy_and_queues_transcription() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, None);

    assert_eq!(voice.name, "Nova");
    assert_eq!(voice.warnings, Vec::<String>::new());
    assert_eq!(voice.transcript_status.as_deref(), Some("pending"));
    assert!((voice.reference_seconds.unwrap() - 15.0).abs() < 1e-6);
    let stored = db.get_voice_profile_by_id(voice.id).unwrap().unwrap().reference_audio_path;
    assert_eq!(Path::new(&stored), permanent_voice_dir(&paths.voices(), voice.id, "Nova").join("reference.wav"));
    assert!(dir.path().join("nova.wav").is_file());
}

// test_a_voice_uploaded_with_a_typed_transcript_skips_whisper
#[test]
fn a_typed_transcript_skips_whisper() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, Some("  Hello there.  "));
    assert_eq!(voice.reference_transcript.as_deref(), Some("Hello there."));
    assert_eq!(voice.transcript_status.as_deref(), Some("done"));
}

// test_create_voice_rejects_audio_too_short, test_create_voice_rejects_duplicate_name
#[test]
fn create_voice_rejects_short_clips_and_duplicate_names() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let short = dir.path().join("short.wav");
    wav(&short, 16000, 1, 8000, |_| 0.1);
    assert_eq!(api_err(voices::create_voice(&db, &paths, &short, "Short", None)),
               "reference audio is too short to clone from (0.50s); need at least 1s");

    clone_nova(dir.path(), &paths, &db, None);
    let again = dir.path().join("again.wav");
    clean_tone_wav(&again);
    assert!(!api_err(voices::create_voice(&db, &paths, &again, "Nova", None)).is_empty());
    assert_eq!(voices::list_voices(&db).unwrap().len(), 1);
}

// test_create_voice_rolls_back_the_profile_row_when_permanent_storage_fails
#[test]
fn create_voice_rolls_back_when_the_copy_fails() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    fs::write(paths.voices(), b"a file where the voices folder should be").unwrap();
    let clip = dir.path().join("nova.wav");
    clean_tone_wav(&clip);

    let err = api_err(voices::create_voice(&db, &paths, &clip, "Nova", None));

    assert!(err.starts_with("failed to store reference audio: "), "{err}");
    assert!(voices::list_voices(&db).unwrap().is_empty());
}

// test_list_voices_returns_all_profiles, test_delete_voice_removes_profile,
// test_delete_voice_returns_404_for_unknown_id, test_delete_voice_removes_the_permanent_voice_storage_folder
#[test]
fn list_and_delete_voices() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, None);
    assert_eq!(voices::list_voices(&db).unwrap().iter().map(|v| v.id).collect::<Vec<_>>(), vec![voice.id]);

    voices::delete_voice(&db, &paths, voice.id).unwrap();

    assert!(voices::list_voices(&db).unwrap().is_empty());
    assert!(!permanent_voice_dir(&paths.voices(), voice.id, "Nova").exists());
    assert_eq!(api_err(voices::delete_voice(&db, &paths, voice.id)), "voice profile not found");
}

// test_editing_a_voice_transcript_saves_it, test_requesting_a_transcription_requeues_it
#[test]
fn transcripts_can_be_edited_or_requested_again() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, None);

    let edited = voices::set_voice_transcript(&db, voice.id, "  Fixed words.  ").unwrap();
    assert_eq!(edited.reference_transcript.as_deref(), Some("Fixed words."));
    assert_eq!(api_err(voices::set_voice_transcript(&db, voice.id, "   ")), "transcript must not be empty");

    db.fail_voice_transcription(voice.id, "Whisper broke").unwrap();
    let requeued = voices::transcribe_voice(&db, voice.id).unwrap();
    assert_eq!((requeued.transcript_status.as_deref(), requeued.transcript_error), (Some("pending"), None));
    assert_eq!(api_err(voices::transcribe_voice(&db, 999)), "voice profile not found");
}

// test_a_voice_reports_its_tempo_and_the_pace_of_the_clip_engines_hear
#[test]
fn a_voice_reports_its_tempo_and_pace() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, None);
    let words = json!([
        {"word": "Hello.", "start": 0.0, "end": 0.5},
        {"word": "there", "start": 1.0, "end": 1.5},
        {"word": "friend", "start": 1.5, "end": 2.0},
    ]);
    db.save_voice_transcript(voice.id, "Hello. there friend", Some(&words), Some(1.5)).unwrap();

    let got = voices::list_voices(&db).unwrap().remove(0);

    assert_eq!(got.tempo, 1.0);
    assert_eq!(got.reference_trim_end, Some(1.5));
    let pace = got.pace.unwrap();
    assert_eq!((pace.speaking_wpm, pace.overall_wpm), (120.0, 80.0));
}

// test_setting_a_voice_tempo_validates_the_range_and_keeps_the_transcript
#[test]
fn setting_a_tempo_validates_and_rounds_it() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, Some("Hi."));
    assert_eq!(api_err(voices::set_voice_tempo(&db, voice.id, 1.3)), "tempo must be a number from 0.75 to 1.25");
    let set = voices::set_voice_tempo(&db, voice.id, 0.904).unwrap();
    assert_eq!((set.tempo, set.reference_transcript.as_deref()), (0.9, Some("Hi.")));
}

// test_voice_audio_serves_the_clip_the_engines_clone_from, test_voice_audio_plays_the_saved_tempo_or_a_preview_tempo
#[test]
fn voice_audio_is_the_clip_engines_clone_from_at_the_saved_or_preview_tempo() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, Some("Hi."));
    let reference = permanent_voice_dir(&paths.voices(), voice.id, "Nova").join("reference.wav");

    assert_eq!(voices::voice_audio_path(&db, voice.id, None).unwrap(), reference);
    voices::set_voice_tempo(&db, voice.id, 0.9).unwrap();
    assert_eq!(voices::voice_audio_path(&db, voice.id, None).unwrap(), reference.with_file_name("reference.tempo-0.90.wav"));
    assert_eq!(voices::voice_audio_path(&db, voice.id, Some(1.1)).unwrap(), reference.with_file_name("reference.tempo-1.10.wav"));
    assert_eq!(api_err(voices::voice_audio_path(&db, voice.id, Some(2.0))), "tempo must be a number from 0.75 to 1.25");
    assert_eq!(api_err(voices::voice_audio_path(&db, 999, None)), "voice profile not found");

    fs::remove_dir_all(reference.parent().unwrap()).unwrap();
    assert_eq!(api_err(voices::voice_audio_path(&db, voice.id, None)), "reference audio missing");
}

// --- pronunciations ---

// test_list_pronunciations_returns_global_entries, test_create_pronunciation_rejects_duplicate_term,
// test_update_pronunciation_override_value, test_update_pronunciation_returns_404_for_missing_term,
// test_delete_pronunciation_entry, test_delete_pronunciation_returns_404_for_missing_term
#[test]
fn pronunciations_round_trip() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let made = lexicon::create_pronunciation(&db, "Hermione", "her-MY-oh-nee", Some("HP")).unwrap();
    assert_eq!((made.term.as_str(), made.notes.as_deref()), ("Hermione", Some("HP")));
    assert!(!api_err(lexicon::create_pronunciation(&db, "Hermione", "x", None)).is_empty());

    assert_eq!(lexicon::update_pronunciation(&db, "Hermione", "her-MY-uh-nee").unwrap().override_value, "her-MY-uh-nee");
    assert_eq!(api_err(lexicon::update_pronunciation(&db, "Nope", "x")), "pronunciation entry not found");
    assert_eq!(lexicon::list_pronunciations(&db).unwrap().len(), 1);

    lexicon::delete_pronunciation(&db, "Hermione").unwrap();
    assert!(lexicon::list_pronunciations(&db).unwrap().is_empty());
    assert_eq!(api_err(lexicon::delete_pronunciation(&db, "Hermione")), "pronunciation entry not found");
}

// --- text clips ---

// test_create_text_clip_queues_it_with_default_voice, test_create_text_clip_rejects_empty_text,
// test_create_text_clip_returns_404_for_unknown_voice_profile
#[test]
fn create_text_clip_queues_it_and_validates() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let clip = clips::create_text_clip(&db, "  Hello.  ", None, None, None).unwrap();
    assert_eq!((clip.text.as_str(), clip.status, clip.engine.as_str(), clip.voice_profile_id),
               ("Hello.", "queued", "chatterbox-turbo", None));
    assert_eq!(api_err(clips::create_text_clip(&db, "   ", None, None, None)), "text must not be empty");
    assert_eq!(api_err(clips::create_text_clip(&db, "Hi", Some(999), None, None)), "voice profile not found");
}

// test_text_clips_store_their_engine, test_a_text_clip_can_carry_a_sample_tempo
#[test]
fn text_clips_store_engine_and_tempo() {
    let dir = TempDir::new().unwrap();
    let (paths, db) = app(dir.path());
    let voice = clone_nova(dir.path(), &paths, &db, Some("Hi."));
    let clip = clips::create_text_clip(&db, "Hi", Some(voice.id), Some("qwen3"), Some(0.9)).unwrap();
    assert_eq!((clip.engine.as_str(), clip.tempo), ("qwen3", Some(0.9)));
    assert_eq!(api_err(clips::create_text_clip(&db, "Hi", None, Some("qwen3"), None)),
               "Quality (Qwen3) needs a voice: pick one first");
    assert_eq!(api_err(clips::create_text_clip(&db, "Hi", None, None, Some(0.9))), "a tempo needs a voice");
    assert_eq!(api_err(clips::create_text_clip(&db, "Hi", Some(voice.id), None, Some(3.0))),
               "tempo must be a number from 0.75 to 1.25");
}

// test_list_text_clips_returns_most_recent_first, test_get_text_clip_returns_404_for_unknown_id,
// test_get_text_clip_surfaces_used_fallback, test_get_text_clip_surfaces_started_at_while_generating
#[test]
fn list_and_get_text_clips() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let a = clips::create_text_clip(&db, "A", None, None, None).unwrap();
    let b = clips::create_text_clip(&db, "B", None, None, None).unwrap();
    assert_eq!(clips::list_text_clips(&db).unwrap().iter().map(|c| c.id).collect::<Vec<_>>(), vec![b.id, a.id]);
    assert_eq!(api_err(clips::get_text_clip(&db, 999)), "text clip not found");

    let running = db.pop_next_queued_text_clip().unwrap().unwrap();
    assert!(clips::get_text_clip(&db, running.id).unwrap().started_at.is_some());
    db.finish_text_clip(running.id, JobStatus::Done, Some("/x.mp3"), None, true).unwrap();
    assert!(clips::get_text_clip(&db, running.id).unwrap().used_fallback);
}

// test_get_text_clip_audio_returns_404_before_generation_completes, test_delete_text_clip_removes_it,
// test_delete_text_clip_returns_404_for_unknown_id
#[test]
fn text_clip_audio_save_and_delete() {
    let dir = TempDir::new().unwrap();
    let (_paths, db) = app(dir.path());
    let clip = clips::create_text_clip(&db, "Hi", None, None, None).unwrap();
    assert_eq!(api_err(clips::text_clip_audio_path(&db, clip.id)), "audio not ready");

    let mp3 = write_file(dir.path(), "text-clip-1.mp3", "ID3");
    db.finish_text_clip(clip.id, JobStatus::Done, Some(&mp3.to_string_lossy()), None, false).unwrap();
    assert_eq!(clips::text_clip_audio_path(&db, clip.id).unwrap(), mp3);

    let copy = dir.path().join("saved.mp3");
    clips::save_text_clip(&db, clip.id, &copy).unwrap();
    assert_eq!(fs::read_to_string(&copy).unwrap(), "ID3");

    clips::delete_text_clip(&db, clip.id).unwrap();
    assert!(!mp3.exists());
    assert_eq!(api_err(clips::get_text_clip(&db, clip.id)), "text clip not found");
    assert_eq!(api_err(clips::delete_text_clip(&db, clip.id)), "text clip not found");
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test app_voices`
Expected: compile errors, `cannot find function create_voice in module voices` and similar.

- [ ] **Step 3: Implement `app/voices.rs`**

```rust
//! Voice endpoints: clone, list, edit, delete, and the clip engines clone from.

use std::path::{Path, PathBuf};

use serde::Serialize;

use super::{check_tempo, err, ApiError, ApiResult, AppPaths};
use crate::db::Database;
use crate::media;
use crate::models::{NewVoiceProfile, VoiceProfile};
use crate::voice_audio::{reference_clip, reference_pace, validate_reference_audio, Pace};
use crate::voice_storage::{delete_voice_storage, store_reference_audio};

#[derive(Debug, Serialize)]
pub struct VoiceDto {
    pub id: i64,
    pub name: String,
    pub engine: String,
    pub reference_transcript: Option<String>,
    pub transcript_status: Option<String>,
    pub transcript_error: Option<String>,
    pub reference_seconds: Option<f64>,
    pub reference_trim_end: Option<f64>,
    pub tempo: f64,
    pub pace: Option<Pace>,
    pub exaggeration: f64,
    pub cfg_weight: f64,
    pub temperature: f64,
    pub warnings: Vec<String>,
}

fn voice_dto(p: VoiceProfile, warnings: Vec<String>) -> VoiceDto {
    // A missing or unreadable file is None here; generation reports it properly.
    let reference_seconds = media::duration(Path::new(&p.reference_audio_path)).ok();
    let pace = reference_pace(p.reference_words.as_ref(), p.reference_trim_end);
    VoiceDto {
        id: p.id,
        name: p.name,
        engine: p.engine,
        reference_transcript: p.reference_transcript,
        transcript_status: p.transcript_status,
        transcript_error: p.transcript_error,
        reference_seconds,
        reference_trim_end: p.reference_trim_end,
        tempo: p.tempo,
        pace,
        exaggeration: p.exaggeration,
        cfg_weight: p.cfg_weight,
        temperature: p.temperature,
        warnings,
    }
}

fn existing(db: &Database, voice_id: i64) -> ApiResult<VoiceProfile> {
    db.get_voice_profile_by_id(voice_id)?.ok_or_else(|| ApiError("voice profile not found".into()))
}

pub fn list_voices(db: &Database) -> ApiResult<Vec<VoiceDto>> {
    Ok(db.list_voice_profiles()?.into_iter().map(|p| voice_dto(p, Vec::new())).collect())
}

/// Clones a voice from a recording the user picked. The recording is copied to
/// `voices/<id>_<name>/reference.<ext>`; the original stays where it is.
pub fn create_voice(db: &Database, paths: &AppPaths, source: &Path, name: &str, reference_transcript: Option<&str>)
                    -> ApiResult<VoiceDto> {
    let info = validate_reference_audio(source).map_err(ApiError)?;
    // A typed transcript is used as-is; otherwise the runner runs Whisper.
    let transcript = reference_transcript.map(str::trim).filter(|t| !t.is_empty());
    let profile = db.create_voice_profile(&NewVoiceProfile {
        name,
        engine: "chatterbox-turbo",
        reference_audio_path: &source.to_string_lossy(),
        reference_transcript: transcript,
        exaggeration: 0.5,
        cfg_weight: 0.5,
        temperature: 0.8,
        transcript_status: Some(if transcript.is_some() { "done" } else { "pending" }),
    })?;
    match store_reference_audio(&paths.voices(), source, profile.id, &profile.name) {
        Ok(stored) => db.update_voice_profile_reference_audio_path(profile.id, &stored.to_string_lossy())?,
        Err(e) => {
            db.delete_voice_profile(profile.id)?;
            return err(format!("failed to store reference audio: {e}"));
        }
    }
    Ok(voice_dto(existing(db, profile.id)?, info.warnings))
}

pub fn set_voice_transcript(db: &Database, voice_id: i64, transcript: &str) -> ApiResult<VoiceDto> {
    existing(db, voice_id)?;
    let text = transcript.trim();
    if text.is_empty() {
        return err("transcript must not be empty");
    }
    db.save_voice_transcript(voice_id, text, None, None)?;
    Ok(voice_dto(existing(db, voice_id)?, Vec::new()))
}

pub fn set_voice_tempo(db: &Database, voice_id: i64, tempo: f64) -> ApiResult<VoiceDto> {
    existing(db, voice_id)?;
    db.set_voice_tempo(voice_id, check_tempo(tempo)?)?;
    Ok(voice_dto(existing(db, voice_id)?, Vec::new()))
}

/// Queues Whisper again; the runner picks it up within a couple of seconds.
pub fn transcribe_voice(db: &Database, voice_id: i64) -> ApiResult<VoiceDto> {
    existing(db, voice_id)?;
    db.request_voice_transcription(voice_id)?;
    Ok(voice_dto(existing(db, voice_id)?, Vec::new()))
}

pub fn delete_voice(db: &Database, paths: &AppPaths, voice_id: i64) -> ApiResult<()> {
    let profile = existing(db, voice_id)?;
    db.delete_voice_profile(voice_id)?;
    delete_voice_storage(&paths.voices(), voice_id, &profile.name);
    Ok(())
}

/// The clip engines clone from (trimmed, at the voice's tempo), or a preview at another
/// tempo without saving it.
pub fn voice_audio_path(db: &Database, voice_id: i64, tempo: Option<f64>) -> ApiResult<PathBuf> {
    let profile = existing(db, voice_id)?;
    let tempo = match tempo {
        Some(t) => check_tempo(t)?,
        None => profile.tempo,
    };
    match reference_clip(Path::new(&profile.reference_audio_path), profile.reference_trim_end, tempo) {
        Ok(path) if path.is_file() => Ok(path),
        _ => err("reference audio missing"),
    }
}
```

- [ ] **Step 4: Implement `app/lexicon.rs`**

```rust
//! Pronunciation endpoints: global entries only, as in the browser app.

use serde::Serialize;

use super::{found, ApiResult};
use crate::db::Database;
use crate::models::{LexiconEntry, NewLexiconEntry};

#[derive(Debug, Serialize)]
pub struct LexiconDto {
    pub term: String,
    pub override_value: String,
    pub notes: Option<String>,
}

fn dto(e: LexiconEntry) -> LexiconDto {
    LexiconDto { term: e.term, override_value: e.override_value, notes: e.notes }
}

pub fn list_pronunciations(db: &Database) -> ApiResult<Vec<LexiconDto>> {
    Ok(db.list_lexicon_entries(None)?.into_iter().map(dto).collect())
}

pub fn create_pronunciation(db: &Database, term: &str, override_value: &str, notes: Option<&str>) -> ApiResult<LexiconDto> {
    let mut entry = NewLexiconEntry::new(term, override_value, "global");
    entry.notes = notes;
    Ok(dto(db.create_lexicon_entry(&entry)?))
}

pub fn update_pronunciation(db: &Database, term: &str, override_value: &str) -> ApiResult<LexiconDto> {
    Ok(dto(found(db.update_lexicon_entry_override(term, override_value, 0), "pronunciation entry")?))
}

pub fn delete_pronunciation(db: &Database, term: &str) -> ApiResult<()> {
    found(db.delete_lexicon_entry(term, 0), "pronunciation entry")
}
```

- [ ] **Step 5: Implement `app/clips.rs`**

```rust
//! Text-clip endpoints: queue a passage, list, play, save a copy, delete.

use std::fs;
use std::path::{Path, PathBuf};

use serde::Serialize;

use super::{check_tempo, chosen_engine, err, found, ApiResult};
use crate::db::Database;
use crate::models::{JobStatus, TextClip, DEFAULT_ENGINE};

#[derive(Debug, Serialize)]
pub struct TextClipDto {
    pub id: i64,
    pub text: String,
    pub voice_profile_id: Option<i64>,
    pub status: &'static str,
    pub progress: Option<f64>,
    pub started_at: Option<f64>,
    pub error: Option<String>,
    pub created_at: f64,
    pub used_fallback: bool,
    pub engine: String,
    pub tempo: Option<f64>,
}

fn dto(c: TextClip) -> TextClipDto {
    TextClipDto {
        id: c.id,
        text: c.text,
        voice_profile_id: c.voice_profile_id,
        status: c.status.as_str(),
        progress: c.progress,
        started_at: c.started_at,
        error: c.error,
        created_at: c.created_at,
        used_fallback: c.used_fallback,
        engine: c.engine,
        tempo: c.tempo,
    }
}

pub fn list_text_clips(db: &Database) -> ApiResult<Vec<TextClipDto>> {
    Ok(db.list_text_clips()?.into_iter().map(dto).collect())
}

pub fn create_text_clip(db: &Database, text: &str, voice_profile_id: Option<i64>, engine: Option<&str>,
                        tempo: Option<f64>) -> ApiResult<TextClipDto> {
    let text = text.trim();
    if text.is_empty() {
        return err("text must not be empty");
    }
    if let Some(id) = voice_profile_id {
        if db.get_voice_profile_by_id(id)?.is_none() {
            return err("voice profile not found");
        }
    }
    let engine = chosen_engine(engine, DEFAULT_ENGINE, voice_profile_id)?;
    let tempo = tempo.map(check_tempo).transpose()?;
    if tempo.is_some() && voice_profile_id.is_none() {
        return err("a tempo needs a voice");
    }
    Ok(dto(db.create_text_clip(text, voice_profile_id, &engine, tempo)?))
}

pub fn get_text_clip(db: &Database, clip_id: i64) -> ApiResult<TextClipDto> {
    Ok(dto(found(db.get_text_clip(clip_id), "text clip")?))
}

pub fn delete_text_clip(db: &Database, clip_id: i64) -> ApiResult<()> {
    let clip = found(db.get_text_clip(clip_id), "text clip")?;
    db.delete_text_clip(clip_id)?;
    if let Some(mp3) = clip.output_path.filter(|p| Path::new(p).is_file()) {
        fs::remove_file(mp3)?;
    }
    Ok(())
}

pub fn text_clip_audio_path(db: &Database, clip_id: i64) -> ApiResult<PathBuf> {
    let clip = found(db.get_text_clip(clip_id), "text clip")?;
    match clip.output_path {
        Some(mp3) if clip.status == JobStatus::Done && Path::new(&mp3).is_file() => Ok(mp3.into()),
        _ => err("audio not ready"),
    }
}

/// Copies a finished clip to where the user chose in the save dialog.
pub fn save_text_clip(db: &Database, clip_id: i64, dest: &Path) -> ApiResult<()> {
    fs::copy(text_clip_audio_path(db, clip_id)?, dest)?;
    Ok(())
}
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `cargo test -p audiobookify-core --test app_voices`
Expected: 14 passed. Then `cargo test -p audiobookify-core`: all pass.

- [ ] **Step 7: Commit**

```bash
git add desktop/core/src/app desktop/core/tests/app_voices.rs
git commit -m "Port the voice, pronunciation and text-clip endpoints

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Import the repo's library into the app data dir

**Files:**
- Create: `desktop/core/src/datadir.rs`
- Modify: `desktop/core/src/lib.rs`
- Create: `desktop/core/tests/datadir.rs`

**Interfaces:**
- Consumes: `AppPaths`, `rusqlite::Connection`.
- Produces: `datadir::import_repo_data(repo: &Path, paths: &AppPaths) -> db::Result<()>`.

Stored paths today are absolute under the repo (`<repo>/uploads/…`, `<repo>/voices/…`), except text clips from the Python worker, which are relative (`text_clips/text-clip-2.mp3`). Chapter MP3s live in user-chosen folders and are left alone.

- [ ] **Step 1: Write the failing tests**

Create `desktop/core/tests/datadir.rs`:

```rust
mod common;

use std::fs;
use std::path::Path;

use audiobookify_core::app::AppPaths;
use audiobookify_core::datadir::import_repo_data;
use audiobookify_core::db::Database;
use audiobookify_core::models::{JobStatus, SourceFormat};
use common::*;
use tempfile::TempDir;

#[test]
fn imports_the_db_and_files_and_points_the_rows_at_the_copies() {
    let dir = TempDir::new().unwrap();
    let repo = dir.path().join("repo");
    for sub in ["uploads/abc", "voices/1_Nova", "text_clips"] {
        fs::create_dir_all(repo.join(sub)).unwrap();
    }
    let repo = fs::canonicalize(repo).unwrap();
    fs::write(repo.join("uploads/abc/book.md"), "# One\nHi.\n").unwrap();
    fs::write(repo.join("voices/1_Nova/reference.wav"), "wav").unwrap();
    fs::write(repo.join("text_clips/text-clip-1.mp3"), "mp3").unwrap();
    {
        let db = Database::open(repo.join("audiobookify.db"), repo.join("voices")).unwrap();
        db.create_book("B", None, &path(&repo, "uploads/abc/book.md"), SourceFormat::Markdown, "/out").unwrap();
        voice_at(&db, "Nova", &path(&repo, "voices/1_Nova/reference.wav"));
        let clip = db.create_text_clip("Hi", None, "chatterbox-turbo", None).unwrap();
        // Relative, as the Python worker wrote it.
        db.finish_text_clip(clip.id, JobStatus::Done, Some("text_clips/text-clip-1.mp3"), None, false).unwrap();
    }
    let paths = AppPaths::new(dir.path().join("data"));

    import_repo_data(&repo, &paths).unwrap();

    let db = paths.open_db().unwrap();
    let book = db.list_books().unwrap().remove(0);
    assert_eq!(book.source_path, path(&paths.uploads(), "abc/book.md"));
    let voice = db.list_voice_profiles().unwrap().remove(0);
    assert_eq!(voice.reference_audio_path, path(&paths.voices(), "1_Nova/reference.wav"));
    let clip = db.list_text_clips().unwrap().remove(0);
    assert_eq!(clip.output_path.unwrap(), path(&paths.text_clips(), "text-clip-1.mp3"));
    assert_eq!(fs::read_to_string(&book.source_path).unwrap(), "# One\nHi.\n");
    assert!(Path::new(&voice.reference_audio_path).is_file());
    assert!(repo.join("audiobookify.db").is_file() && repo.join("uploads/abc/book.md").is_file());
    assert!(!paths.root.join("audiobookify.db.importing").exists());
}

#[test]
fn a_failed_import_leaves_no_db_behind() {
    let dir = TempDir::new().unwrap();
    let repo = dir.path().join("repo");
    fs::create_dir_all(&repo).unwrap();
    let paths = AppPaths::new(dir.path().join("data"));

    assert!(import_repo_data(&repo, &paths).is_err());
    assert!(!paths.db().exists());
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p audiobookify-core --test datadir`
Expected: compile error, `unresolved import audiobookify_core::datadir`.

- [ ] **Step 3: Implement `datadir`**

Create `desktop/core/src/datadir.rs`:

```rust
//! First launch of the desktop app: copy the browser app's library out of the repo into the
//! app data dir, and point the stored paths at the copies. The repo is left as it was.

use std::fs;
use std::io;
use std::path::Path;

use rusqlite::{Connection, OpenFlags};

use crate::app::AppPaths;
use crate::db;

fn copy_dir(from: &Path, to: &Path) -> io::Result<()> {
    fs::create_dir_all(to)?;
    for entry in fs::read_dir(from)? {
        let entry = entry?;
        let dest = to.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_dir(&entry.path(), &dest)?;
        } else {
            fs::copy(entry.path(), dest)?;
        }
    }
    Ok(())
}

/// Copies `<repo>/audiobookify.db`, `uploads/`, `voices/` and `text_clips/` into `paths`.
/// The DB gets its real name last, so a failed import leaves nothing that looks like a library.
pub fn import_repo_data(repo: &Path, paths: &AppPaths) -> db::Result<()> {
    let repo = fs::canonicalize(repo)?;
    fs::create_dir_all(&paths.root)?;
    let tmp = paths.root.join("audiobookify.db.importing");
    let _ = fs::remove_file(&tmp);
    // Read-only, so a missing DB is an error instead of a new empty one. VACUUM INTO takes a
    // consistent copy even while another process has the DB open.
    Connection::open_with_flags(repo.join("audiobookify.db"), OpenFlags::SQLITE_OPEN_READ_ONLY)?
        .execute("VACUUM INTO ?1", [tmp.to_string_lossy()])?;

    let conn = Connection::open(&tmp)?;
    for (dir, new_dir, table, column) in [
        ("uploads", paths.uploads(), "books", "source_path"),
        ("voices", paths.voices(), "voice_profiles", "reference_audio_path"),
        ("text_clips", paths.text_clips(), "text_clips", "output_path"),
    ] {
        let old = repo.join(dir);
        if old.is_dir() {
            copy_dir(&old, &new_dir)?;
        }
        let new_prefix = format!("{}/", new_dir.display());
        // Absolute paths, plus the relative ones the Python worker wrote for text clips.
        for old_prefix in [format!("{}/", old.display()), format!("{dir}/")] {
            conn.execute(
                &format!("UPDATE {table} SET {column} = ?2 || substr({column}, length(?1) + 1) \
                          WHERE substr({column}, 1, length(?1)) = ?1"),
                [&old_prefix, &new_prefix],
            )?;
        }
    }
    drop(conn);
    fs::rename(&tmp, paths.db())?;
    Ok(())
}
```

Add `pub mod datadir;` to `desktop/core/src/lib.rs` (alphabetical, after `pub mod app;`).

- [ ] **Step 4: Run the tests to see them pass**

Run: `cargo test -p audiobookify-core --test datadir`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add desktop/core/src/datadir.rs desktop/core/src/lib.rs desktop/core/tests/datadir.rs
git commit -m "Import the repo's library into the app data dir

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Tauri wiring: commands, data dir, log file, CSP; no more uvicorn

After this task the desktop app no longer starts FastAPI. The UI stays broken until Task 8, because it still calls `fetch`.

**Files:**
- Modify: `desktop/Cargo.toml`
- Create: `desktop/src/commands.rs`, `desktop/src/logging.rs`, `desktop/capabilities/default.json`
- Rewrite: `desktop/src/main.rs`
- Delete: `desktop/src/sidecars.rs`
- Modify: `desktop/tauri.conf.json`, `Makefile`

**Interfaces:**
- Consumes: every `app::*` function from Tasks 3 to 5, `datadir::import_repo_data`, `AppPaths`, `Runner`.
- Produces (to the frontend): the commands `list_engines`, `list_books`, `get_book`, `create_book`, `delete_book`, `get_book_text`, `save_chapter_breaks`, `assign_voice`, `validate_output_dir`, `generate_book`, `generate_chapter`, `cancel_chapter`, `cancel_book`, `worker_status`, `pause_worker`, `resume_worker`, `chapter_audio_path`, `list_voices`, `create_voice`, `set_voice_transcript`, `set_voice_tempo`, `transcribe_voice`, `delete_voice`, `voice_audio_path`, `list_pronunciations`, `create_pronunciation`, `update_pronunciation`, `delete_pronunciation`, `list_text_clips`, `create_text_clip`, `get_text_clip`, `delete_text_clip`, `text_clip_audio_path`, `save_text_clip`. Argument names are listed in `commands.rs` below; the JS side sends them in camelCase.

- [ ] **Step 1: Dependencies**

`desktop/Cargo.toml` `[dependencies]`:

```toml
audiobookify-core = { path = "core" }
libc = "0.2"
tauri = { version = "2", features = ["protocol-asset"] }
tauri-plugin-dialog = "2"
```

- [ ] **Step 2: Write `commands.rs`**

Create `desktop/src/commands.rs`:

```rust
//! The Tauri commands: lock the DB, call `audiobookify_core::app`, and let the webview play
//! the audio files they return. The logic and its tests live in the core crate.

use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};

use audiobookify_core::app::books::{self, BookDto, BookText, ChapterBreak, DirCheck};
use audiobookify_core::app::clips::{self, TextClipDto};
use audiobookify_core::app::jobs::{self, Queued, QueuedOne, WorkerStatus};
use audiobookify_core::app::lexicon::{self, LexiconDto};
use audiobookify_core::app::voices::{self, VoiceDto};
use audiobookify_core::app::{self, ApiError, ApiResult, AppPaths, Engines};
use audiobookify_core::db::Database;
use tauri::{AppHandle, Manager, State};

/// The commands' connection; the job runner has its own. WAL mode and the busy timeout let
/// them share the file, as FastAPI and the Python worker did.
// ponytail: one connection behind one Mutex; an import holds it for a second or two and the
// 2 s polls wait. A small pool if that ever shows.
pub struct AppState {
    pub db: Mutex<Database>,
    pub paths: AppPaths,
}

impl AppState {
    fn db(&self) -> MutexGuard<'_, Database> {
        self.db.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

/// Lets the webview load `path` over the asset protocol, then returns it for `convertFileSrc`.
fn playable(app: &AppHandle, path: PathBuf) -> ApiResult<PathBuf> {
    app.asset_protocol_scope().allow_file(&path).map_err(|e| ApiError(e.to_string()))?;
    Ok(path)
}

#[tauri::command]
pub fn list_engines() -> Engines {
    app::list_engines()
}

#[tauri::command]
pub fn validate_output_dir(path: String) -> DirCheck {
    books::validate_output_dir(&path)
}

#[tauri::command(async)]
pub fn list_books(state: State<'_, AppState>) -> ApiResult<Vec<BookDto>> {
    books::list_books(&state.db())
}

#[tauri::command(async)]
pub fn get_book(state: State<'_, AppState>, book_id: i64) -> ApiResult<BookDto> {
    books::get_book(&state.db(), book_id)
}

#[tauri::command(async)]
pub fn create_book(state: State<'_, AppState>, path: PathBuf, output_dir: String) -> ApiResult<BookDto> {
    books::create_book(&state.db(), &state.paths, &path, &output_dir)
}

#[tauri::command(async)]
pub fn delete_book(state: State<'_, AppState>, book_id: i64) -> ApiResult<()> {
    books::delete_book(&state.db(), &state.paths, book_id)
}

#[tauri::command(async)]
pub fn get_book_text(state: State<'_, AppState>, book_id: i64) -> ApiResult<BookText> {
    books::book_text(&state.db(), book_id)
}

#[tauri::command(async)]
pub fn save_chapter_breaks(state: State<'_, AppState>, book_id: i64, chapters: Vec<ChapterBreak>) -> ApiResult<BookDto> {
    books::save_chapter_breaks(&mut state.db(), &state.paths, book_id, &chapters)
}

#[tauri::command(async)]
pub fn assign_voice(state: State<'_, AppState>, book_id: i64, voice_profile_id: Option<i64>) -> ApiResult<()> {
    books::assign_voice(&state.db(), book_id, voice_profile_id)
}

#[tauri::command(async)]
pub fn generate_book(state: State<'_, AppState>, book_id: i64, engine: Option<String>) -> ApiResult<Queued> {
    jobs::generate_book(&state.db(), book_id, engine.as_deref())
}

#[tauri::command(async)]
pub fn generate_chapter(state: State<'_, AppState>, book_id: i64, chapter_id: i64, engine: Option<String>)
                        -> ApiResult<QueuedOne> {
    jobs::generate_chapter(&state.db(), book_id, chapter_id, engine.as_deref())
}

#[tauri::command(async)]
pub fn cancel_chapter(state: State<'_, AppState>, book_id: i64, chapter_id: i64) -> ApiResult<()> {
    jobs::cancel_chapter(&state.db(), &state.paths, book_id, chapter_id)
}

#[tauri::command(async)]
pub fn cancel_book(state: State<'_, AppState>, book_id: i64) -> ApiResult<()> {
    jobs::cancel_book(&state.db(), &state.paths, book_id)
}

#[tauri::command(async)]
pub fn worker_status(state: State<'_, AppState>) -> ApiResult<WorkerStatus> {
    jobs::worker_status(&state.db())
}

#[tauri::command(async)]
pub fn pause_worker(state: State<'_, AppState>) -> ApiResult<WorkerStatus> {
    jobs::set_paused(&state.db(), true)
}

#[tauri::command(async)]
pub fn resume_worker(state: State<'_, AppState>) -> ApiResult<WorkerStatus> {
    jobs::set_paused(&state.db(), false)
}

#[tauri::command(async)]
pub fn chapter_audio_path(app: AppHandle, state: State<'_, AppState>, book_id: i64, chapter_id: i64)
                          -> ApiResult<PathBuf> {
    let path = jobs::chapter_audio_path(&state.db(), book_id, chapter_id)?;
    playable(&app, path)
}

#[tauri::command(async)]
pub fn list_voices(state: State<'_, AppState>) -> ApiResult<Vec<VoiceDto>> {
    voices::list_voices(&state.db())
}

#[tauri::command(async)]
pub fn create_voice(state: State<'_, AppState>, path: PathBuf, name: String, reference_transcript: Option<String>)
                    -> ApiResult<VoiceDto> {
    voices::create_voice(&state.db(), &state.paths, &path, &name, reference_transcript.as_deref())
}

#[tauri::command(async)]
pub fn set_voice_transcript(state: State<'_, AppState>, voice_id: i64, transcript: String) -> ApiResult<VoiceDto> {
    voices::set_voice_transcript(&state.db(), voice_id, &transcript)
}

#[tauri::command(async)]
pub fn set_voice_tempo(state: State<'_, AppState>, voice_id: i64, tempo: f64) -> ApiResult<VoiceDto> {
    voices::set_voice_tempo(&state.db(), voice_id, tempo)
}

#[tauri::command(async)]
pub fn transcribe_voice(state: State<'_, AppState>, voice_id: i64) -> ApiResult<VoiceDto> {
    voices::transcribe_voice(&state.db(), voice_id)
}

#[tauri::command(async)]
pub fn delete_voice(state: State<'_, AppState>, voice_id: i64) -> ApiResult<()> {
    voices::delete_voice(&state.db(), &state.paths, voice_id)
}

#[tauri::command(async)]
pub fn voice_audio_path(app: AppHandle, state: State<'_, AppState>, voice_id: i64, tempo: Option<f64>)
                        -> ApiResult<PathBuf> {
    let path = voices::voice_audio_path(&state.db(), voice_id, tempo)?;
    playable(&app, path)
}

#[tauri::command(async)]
pub fn list_pronunciations(state: State<'_, AppState>) -> ApiResult<Vec<LexiconDto>> {
    lexicon::list_pronunciations(&state.db())
}

#[tauri::command(async)]
pub fn create_pronunciation(state: State<'_, AppState>, term: String, override_value: String, notes: Option<String>)
                            -> ApiResult<LexiconDto> {
    lexicon::create_pronunciation(&state.db(), &term, &override_value, notes.as_deref())
}

#[tauri::command(async)]
pub fn update_pronunciation(state: State<'_, AppState>, term: String, override_value: String) -> ApiResult<LexiconDto> {
    lexicon::update_pronunciation(&state.db(), &term, &override_value)
}

#[tauri::command(async)]
pub fn delete_pronunciation(state: State<'_, AppState>, term: String) -> ApiResult<()> {
    lexicon::delete_pronunciation(&state.db(), &term)
}

#[tauri::command(async)]
pub fn list_text_clips(state: State<'_, AppState>) -> ApiResult<Vec<TextClipDto>> {
    clips::list_text_clips(&state.db())
}

#[tauri::command(async)]
pub fn create_text_clip(state: State<'_, AppState>, text: String, voice_profile_id: Option<i64>,
                        engine: Option<String>, tempo: Option<f64>) -> ApiResult<TextClipDto> {
    clips::create_text_clip(&state.db(), &text, voice_profile_id, engine.as_deref(), tempo)
}

#[tauri::command(async)]
pub fn get_text_clip(state: State<'_, AppState>, clip_id: i64) -> ApiResult<TextClipDto> {
    clips::get_text_clip(&state.db(), clip_id)
}

#[tauri::command(async)]
pub fn delete_text_clip(state: State<'_, AppState>, clip_id: i64) -> ApiResult<()> {
    clips::delete_text_clip(&state.db(), clip_id)
}

#[tauri::command(async)]
pub fn text_clip_audio_path(app: AppHandle, state: State<'_, AppState>, clip_id: i64) -> ApiResult<PathBuf> {
    let path = clips::text_clip_audio_path(&state.db(), clip_id)?;
    playable(&app, path)
}

#[tauri::command(async)]
pub fn save_text_clip(state: State<'_, AppState>, clip_id: i64, dest: PathBuf) -> ApiResult<()> {
    clips::save_text_clip(&state.db(), clip_id, &dest)
}
```

- [ ] **Step 3: Write `logging.rs`**

Create `desktop/src/logging.rs`:

```rust
//! A release build has no terminal. Its stderr (runner errors, the engine worker's logging,
//! ffmpeg failures) goes to a file in the app data dir.

use std::fs::{self, OpenOptions};
use std::os::fd::AsRawFd;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

// ponytail: one old generation, rotated at launch; enough to read back the last crash.
const ROTATE_BYTES: u64 = 5 * 1024 * 1024;

pub fn stderr_to(log: &Path) {
    let Some(dir) = log.parent() else { return };
    if fs::create_dir_all(dir).is_err() {
        return;
    }
    if fs::metadata(log).is_ok_and(|m| m.len() > ROTATE_BYTES) {
        let _ = fs::rename(log, log.with_extension("log.1"));
    }
    let Ok(file) = OpenOptions::new().create(true).append(true).open(log) else { return };
    // SAFETY: both fds are open. Afterwards fd 2 is the log for this process and for every
    // child that inherits stderr, such as the engine worker.
    unsafe { libc::dup2(file.as_raw_fd(), libc::STDERR_FILENO) };
    let now = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs());
    eprintln!("--- Audiobookify started (unix time {now}) ---");
}
```

- [ ] **Step 4: Rewrite `main.rs`; delete `sidecars.rs`**

`git rm desktop/src/sidecars.rs`, then replace `desktop/src/main.rs` with:

```rust
mod commands;
mod logging;

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::AtomicBool;
use std::sync::Mutex;

use audiobookify_core::app::AppPaths;
use audiobookify_core::audio::child_path;
use audiobookify_core::datadir::import_repo_data;
use audiobookify_core::db::Database;
use audiobookify_core::engine::PythonEngine;
use audiobookify_core::jobs::Runner;
use commands::AppState;
use tauri::{WebviewUrl, WebviewWindowBuilder};

// ponytail: the repo path is baked in at compile time; this is a personal-Macs-only build
// (spec open question 1). A shareable app would bundle Python instead.
fn repo_root() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().expect("desktop/ has a parent")
}

/// The bundle identifier's data dir. Worked out by hand because Tauri's path resolver needs
/// a running app, and the DB opens before that.
fn data_dir() -> PathBuf {
    let home = std::env::var_os("HOME").unwrap_or_else(|| fail("HOME is not set."));
    PathBuf::from(home).join("Library/Application Support/com.audiobookify.app")
}

/// Generation, on its own thread with its own DB connection (opened by the caller, so a
/// failure shows the startup alert). It owns the Python engine worker child, whose stdin
/// closes when this process exits, however it exits; the worker then finishes its chunk
/// and quits. A job cut off mid-chapter is requeued by `reset_stale_generating_jobs` on
/// the next launch.
fn start_runner(db: Database, paths: &AppPaths) {
    let repo = repo_root().to_path_buf();
    let runner_paths = paths.runner_paths();
    std::thread::Builder::new()
        .name("job-runner".into())
        .spawn(move || {
            let engine = PythonEngine::new(repo.join(".venv/bin/python"), &repo)
                .env("HF_HUB_OFFLINE", "1")
                .env("PATH", child_path(std::env::var_os("PATH")));
            Runner::new(db, engine, runner_paths).run_forever(&AtomicBool::new(false));
        })
        .expect("spawn the job runner thread");
}

/// Startup failures happen before Tauri runs: an error returned from Tauri's setup hook
/// aborts inside the macOS app delegate, which shows up as a crash report, not a message.
fn fail(msg: &str) -> ! {
    eprintln!("{msg}");
    // The message goes in as argv, so it needs no AppleScript escaping.
    let _ = Command::new("/usr/bin/osascript")
        .args([
            "-e", "on run argv",
            "-e", "display alert \"Audiobookify could not start\" message (item 1 of argv) as critical",
            "-e", "end run",
            msg,
        ])
        .status();
    std::process::exit(1);
}

/// A two-button question before the window exists (same reason as `fail`). True for `yes`.
fn ask(message: &str, yes: &str, no: &str) -> bool {
    let out = Command::new("/usr/bin/osascript")
        .args([
            "-e", "on run argv",
            "-e", "display dialog (item 1 of argv) with title \"Audiobookify\" buttons {item 3 of argv, item 2 of argv} default button 2",
            "-e", "end run",
            message, yes, no,
        ])
        .output();
    out.is_ok_and(|o| String::from_utf8_lossy(&o.stdout).trim() == format!("button returned:{yes}"))
}

/// First launch only: offer to bring over the browser app's library from the repo.
fn offer_import(paths: &AppPaths) {
    if paths.db().exists() || !repo_root().join("audiobookify.db").is_file() {
        return;
    }
    let message = "Import your library from the browser version of Audiobookify?\n\n\
                   Books, voices, pronunciations and text clips are copied. The originals stay where they are.";
    if ask(message, "Import", "Start empty") {
        import_repo_data(repo_root(), paths).unwrap_or_else(|e| {
            fail(&format!("Could not import the library from {}: {e}", repo_root().display()))
        });
    }
}

fn main() {
    let paths = AppPaths::new(data_dir());
    if let Err(e) = std::fs::create_dir_all(&paths.root) {
        fail(&format!("Could not create {}: {e}", paths.root.display()));
    }
    if !tauri::is_dev() {
        logging::stderr_to(&paths.log());
    }
    offer_import(&paths);
    let open = || {
        paths.open_db().unwrap_or_else(|e| fail(&format!("Could not open the database in {}: {e}", paths.root.display())))
    };
    start_runner(open(), &paths);
    let state = AppState { db: Mutex::new(open()), paths: paths.clone() };

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            commands::list_engines,
            commands::validate_output_dir,
            commands::list_books,
            commands::get_book,
            commands::create_book,
            commands::delete_book,
            commands::get_book_text,
            commands::save_chapter_breaks,
            commands::assign_voice,
            commands::generate_book,
            commands::generate_chapter,
            commands::cancel_chapter,
            commands::cancel_book,
            commands::worker_status,
            commands::pause_worker,
            commands::resume_worker,
            commands::chapter_audio_path,
            commands::list_voices,
            commands::create_voice,
            commands::set_voice_transcript,
            commands::set_voice_tempo,
            commands::transcribe_voice,
            commands::delete_voice,
            commands::voice_audio_path,
            commands::list_pronunciations,
            commands::create_pronunciation,
            commands::update_pronunciation,
            commands::delete_pronunciation,
            commands::list_text_clips,
            commands::create_text_clip,
            commands::get_text_clip,
            commands::delete_text_clip,
            commands::text_clip_audio_path,
            commands::save_text_clip,
        ])
        .setup(|app| {
            // Dev: Tauri points App at the Vite dev server (devUrl). Release: the bundled frontend/dist.
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Audiobookify")
                .inner_size(1280.0, 860.0)
                .min_inner_size(900.0, 600.0)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Audiobookify");
}
```

- [ ] **Step 5: Config: CSP, asset protocol, capabilities**

In `desktop/tauri.conf.json`, replace `"security": { "csp": null }` with:

```json
"security": {
  "csp": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; media-src 'self' asset: http://asset.localhost; connect-src ipc: http://ipc.localhost",
  "dangerousDisableAssetCspModification": ["style-src"],
  "assetProtocol": { "enable": true, "scope": [] }
}
```

- `script-src 'self'`: Tauri adds the hash of `index.html`'s inline theme script at build time.
- `style-src` keeps `'unsafe-inline'` for React `style={…}` attributes. That is why style-src is excluded from Tauri's nonce injection: a nonce would switch `'unsafe-inline'` off.
- The asset scope starts empty. `commands::playable` allows one file at a time.

Create `desktop/capabilities/default.json`:

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "The main window: core APIs (events, drag and drop) and the open and save dialogs.",
  "windows": ["main"],
  "permissions": ["core:default", "dialog:allow-open", "dialog:allow-save"]
}
```

The app's own commands need no permission entry: Tauri allows them unless `build.rs` declares an app manifest, and this one doesn't.

- [ ] **Step 6: Makefile**

Delete the `check-not-running`, `up`, `dev` and `app-dev` targets and their comments. Replace them, and the `app` target, with:

```make
# The desktop app with hot reload. Data lives in ~/Library/Application Support/com.audiobookify.app.
dev:
	cd desktop && cargo tauri dev

# Builds desktop/target/release/bundle/macos/Audiobookify.app. It runs this checkout's
# .venv, so keep the repo where it is.
app:
	cd desktop && cargo tauri build
	@echo "built desktop/target/release/bundle/macos/Audiobookify.app"

up: app
	open desktop/target/release/bundle/macos/Audiobookify.app
```

Replace `down` with:

```make
# Stops a Vite dev server left behind by an interrupted `make dev`.
down:
	@command -v lsof >/dev/null 2>&1 && lsof -ti:5173 | xargs kill 2>/dev/null; true
	@echo "stopped."
```

Drop `check-not-running` and `app-dev` from `.PHONY` if they're listed there.

- [ ] **Step 7: Build and smoke-test**

Run (from `desktop/`): `cargo build` and `cargo clippy --all-targets`.
Expected: both succeed with no warnings.

Run: `cargo test -p audiobookify-core`. Expected: all pass.

Run: `make dev`. Expected: on a machine with no app data dir yet, the import dialog appears before the window. Choose **Start empty** for now (Task 10 checks the import itself), then quit. The window opens. Its pages show errors because the frontend still calls `fetch`; Task 8 fixes that. Then `rm -rf ~/Library/Application\ Support/com.audiobookify.app` so Task 10 starts fresh.

- [ ] **Step 8: Commit**

```bash
git add desktop/Cargo.toml desktop/Cargo.lock desktop/src desktop/capabilities desktop/tauri.conf.json Makefile
git commit -m "Serve the UI from Tauri commands; data dir, log file and CSP

The uvicorn sidecar is gone: the window loads the bundled frontend and every
endpoint is a command over audiobookify_core::app.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Frontend: `invoke` instead of `fetch`, audio over the asset protocol

**Files:**
- Modify: `frontend/package.json` (via npm), `frontend/vite.config.js`
- Create: `frontend/src/lib/status.js`, `frontend/src/components/FileAudio.jsx`
- Rewrite: `frontend/src/lib/api.js`
- Modify: `frontend/src/lib/shelf.js:2`, `frontend/src/pages/TextPage.jsx`, `frontend/src/pages/VoicesPage.jsx`, `frontend/src/pages/BookDetailPage.jsx:4`, `frontend/src/components/player/PlayerContext.jsx`

**Interfaces:**
- Consumes: the Task 7 commands.
- Produces: `api.*` as listed below. `createBook(path, outputDir)` and `createVoice(path, name, transcript)` take paths now; Task 9 updates their callers. `api.chapterAudioSrc`, `api.voiceAudioSrc`, `api.textClipAudioSrc` resolve to asset URLs. `api.saveTextClip(id, dest)`. `browseDir` is gone. The status helpers move to `lib/status.js`.

- [ ] **Step 1: Packages and Vite**

Run (from `frontend/`): `npm install @tauri-apps/api@^2 @tauri-apps/plugin-dialog@^2`

Replace `frontend/vite.config.js` with:

```js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Served to the Tauri window in dev (desktop/tauri.conf.json devUrl), so the port is fixed.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: { port: 5173, strictPort: true },
});
```

- [ ] **Step 2: Move the status helpers**

Create `frontend/src/lib/status.js` and move into it, unchanged, these four exports from `api.js`: `textClipInProgress`, `chapterInProgress` (with its comment), `bookStatus`, `isBookInProgress`. That keeps `api.js`, and so `@tauri-apps/api`, out of the `node --test` suites (`shelf.test.js` imports `shelf.js`).

Update the imports:
- `frontend/src/lib/shelf.js:2`: `import { chapterInProgress } from "./status.js";`
- `frontend/src/pages/TextPage.jsx:3`: `import { api } from "../lib/api.js";` plus `import { textClipInProgress } from "../lib/status.js";`
- `frontend/src/pages/VoicesPage.jsx:3`: the same split.
- `frontend/src/pages/BookDetailPage.jsx:4`: `import { api } from "../lib/api.js";` plus `import { chapterInProgress } from "../lib/status.js";`

Run: `npm test`. Expected: all pass, as before.

- [ ] **Step 3: Rewrite `api.js`**

Replace `frontend/src/lib/api.js` with:

```js
import { convertFileSrc, invoke } from "@tauri-apps/api/core";

// Commands reject with the Rust error's message as a plain string; callers show e.message.
async function call(command, args) {
  try {
    return await invoke(command, args);
  } catch (e) {
    throw new Error(typeof e === "string" ? e : e?.message ?? String(e));
  }
}

// A command that returns a file path, as a URL an <audio> can play.
const audioSrc = (command, args) => call(command, args).then((path) => convertFileSrc(path));

export const api = {
  listBooks: () => call("list_books"),
  getBook: (id) => call("get_book", { bookId: id }),
  createBook: (path, outputDir) => call("create_book", { path, outputDir }),
  validateOutputDir: (path) => call("validate_output_dir", { path }),
  getWorkerStatus: () => call("worker_status"),
  pauseWorker: () => call("pause_worker"),
  resumeWorker: () => call("resume_worker"),
  listEngines: () => call("list_engines"),
  generateBook: (id, engine) => call("generate_book", { bookId: id, engine }),
  generateChapter: (bookId, chapterId, engine) => call("generate_chapter", { bookId, chapterId, engine }),
  cancelChapter: (bookId, chapterId) => call("cancel_chapter", { bookId, chapterId }),
  cancelBook: (bookId) => call("cancel_book", { bookId }),
  assignVoice: (bookId, voiceProfileId) => call("assign_voice", { bookId, voiceProfileId }),
  chapterAudioSrc: (bookId, chapterId) => audioSrc("chapter_audio_path", { bookId, chapterId }),

  getBookText: (id) => call("get_book_text", { bookId: id }),
  saveChapterBreaks: (id, breaks) =>
    call("save_chapter_breaks", { bookId: id, chapters: breaks.map(({ start, title }) => ({ start, title })) }),

  listVoices: () => call("list_voices"),
  createVoice: (path, name, transcript) =>
    call("create_voice", { path, name, referenceTranscript: transcript || null }),
  deleteBook: (id) => call("delete_book", { bookId: id }),
  deleteVoice: (id) => call("delete_voice", { voiceId: id }),
  updateVoiceTranscript: (id, transcript) => call("set_voice_transcript", { voiceId: id, transcript }),
  setVoiceTempo: (id, tempo) => call("set_voice_tempo", { voiceId: id, tempo }),
  transcribeVoice: (id) => call("transcribe_voice", { voiceId: id }),
  voiceAudioSrc: (id, tempo) => audioSrc("voice_audio_path", { voiceId: id, tempo }),

  listPronunciations: () => call("list_pronunciations"),
  createPronunciation: ({ term, override_value, notes }) =>
    call("create_pronunciation", { term, overrideValue: override_value, notes: notes ?? null }),
  updatePronunciation: (term, overrideValue) => call("update_pronunciation", { term, overrideValue }),
  deletePronunciation: (term) => call("delete_pronunciation", { term }),

  listTextClips: () => call("list_text_clips"),
  createTextClip: (text, voiceProfileId, engine, tempo = null) =>
    call("create_text_clip", { text, voiceProfileId, engine, tempo }),
  getTextClip: (id) => call("get_text_clip", { clipId: id }),
  deleteTextClip: (id) => call("delete_text_clip", { clipId: id }),
  textClipAudioSrc: (id) => audioSrc("text_clip_audio_path", { clipId: id }),
  saveTextClip: (id, dest) => call("save_text_clip", { clipId: id, dest }),
};
```

- [ ] **Step 4: `FileAudio`**

Create `frontend/src/components/FileAudio.jsx`:

```jsx
import { useEffect, useState } from "react";

// An <audio> for a file on disk. `load` resolves to an asset URL (api.*AudioSrc); it runs
// again when `cacheKey` changes. Renders nothing until then, or if the file is missing.
export default function FileAudio({ load, cacheKey, ...props }) {
  const [src, setSrc] = useState(null);

  useEffect(() => {
    let live = true;
    setSrc(null);
    load().then(
      (url) => live && setSrc(url),
      () => {},
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey]);

  return src ? <audio controls src={src} {...props} /> : null;
}
```

- [ ] **Step 5: Use it on the Voices and Text pages**

`frontend/src/pages/VoicesPage.jsx`: add `import FileAudio from "../components/FileAudio.jsx";`, then replace the three `<audio>` elements.

The preview (around line 219):

```jsx
<FileAudio
  load={() => api.voiceAudioSrc(voice.id, previewTempo)}
  cacheKey={`${voice.id}@${previewTempo}@${voice.reference_trim_end}`}
  autoPlay
  className="h-9 w-full"
/>
```

The sample (around line 229):

```jsx
<FileAudio load={() => api.textClipAudioSrc(sample.id)} cacheKey={sample.id} className="h-9 w-full" />
```

The saved reference (around line 299):

```jsx
<FileAudio
  load={() => api.voiceAudioSrc(voice.id, voice.tempo)}
  cacheKey={`${voice.id}@${voice.tempo}@${voice.reference_trim_end}`}
  className="h-9 w-full"
/>
```

(`reference_trim_end` is in the key because Whisper changes the trim once a transcription finishes.)

`frontend/src/pages/TextPage.jsx`:
- Add `import { save } from "@tauri-apps/plugin-dialog";` and `import FileAudio from "../components/FileAudio.jsx";`.
- Inside `TextPage`, after `refreshClips`, add:

```js
  async function download(clip) {
    const dest = await save({
      defaultPath: `text-clip-${clip.id}.mp3`,
      filters: [{ name: "MP3 audio", extensions: ["mp3"] }],
    });
    if (!dest) return;
    try {
      await api.saveTextClip(clip.id, dest);
    } catch (e) {
      setError(e.message);
    }
  }
```

- Replace the `clip.status === "done"` block (around lines 185-196) with:

```jsx
{clip.status === "done" && (
  <div className="flex items-center gap-3">
    <FileAudio load={() => api.textClipAudioSrc(clip.id)} cacheKey={clip.id} className="h-10 max-w-xs" />
    <button
      type="button"
      onClick={() => download(clip)}
      className="inline-flex items-center gap-1.5 text-meta text-ink-muted hover:text-ink rounded cursor-pointer"
    >
      <Download className="size-4" strokeWidth={1.7} aria-hidden="true" />
      Download
    </button>
  </div>
)}
```

- [ ] **Step 6: The player**

In `frontend/src/components/player/PlayerContext.jsx`:
- Delete the `audioUrl` helper (line 10).
- Add `import { api } from "../../lib/api.js";`.
- Add `const pending = useRef(0);` next to `startAt`.
- Replace `play` with:

```js
  function play(nextBook, nextChapterId, offset = 0, autoplay = true) {
    const a = audio.current;
    setBook(nextBook);
    if (nextChapterId === chapterId && book?.id === nextBook.id) {
      a.currentTime = offset;
      if (autoplay) a.play().catch(() => {});
      return;
    }
    setChapterId(nextChapterId);
    setTime(offset);
    startAt.current = offset;
    // The path comes from a command; only the latest request may set the source.
    const request = ++pending.current;
    api.chapterAudioSrc(nextBook.id, nextChapterId).then(
      (src) => {
        if (request !== pending.current) return;
        a.src = src;
        if (autoplay) a.play().catch(() => {});
      },
      () => {},
    );
  }
```

`onLoadedMetadata` still seeks to `startAt.current`, so resuming mid-chapter works as before.

- [ ] **Step 7: Check**

Run (from `frontend/`): `npm test`, then `npm run build`. Expected: tests pass; the build succeeds.

Run: `grep -rn "fetch(\|/api/" frontend/src`. Expected: no output.

Run `make dev`, then check:
- The Library loads.
- A book page loads, pause and resume work, and generating a chapter makes progress.
- The Player plays a finished chapter and seeks.
- The Voices page plays each reference and a tempo preview. A Quality sample generates and plays.
- On the Text page, a clip plays, and Download opens the save dialog and writes the file.
- The Pronunciations page adds, edits and deletes an entry.

Importing a book or cloning a voice still fails until Task 9.

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.js frontend/src
git commit -m "Call Tauri commands from the frontend and play files over the asset protocol

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Frontend: native file and folder pickers, drag and drop

**Files:**
- Create: `frontend/src/components/PathPick.jsx`
- Rewrite: `frontend/src/components/DirectoryBrowser.jsx`
- Modify: `frontend/src/pages/HomePage.jsx` (`ImportBookModal`, `HomePage` drag and drop)
- Modify: `frontend/src/pages/VoicesPage.jsx` (`CloneVoiceModal`)

**Interfaces:**
- Consumes: `api.createBook(path, outputDir)`, `api.createVoice(path, name, transcript)`, `api.validateOutputDir(path)`, `open` from `@tauri-apps/plugin-dialog`, `getCurrentWebview().onDragDropEvent` from `@tauri-apps/api/webview`.
- Produces: `<PathPick label path onPick icon />`; `<DirectoryBrowser value onChange onValidityChange />` with the same props as before.

- [ ] **Step 1: `PathPick`**

Create `frontend/src/components/PathPick.jsx`:

```jsx
import Button from "./ui/Button.jsx";

// A "Choose…" button that opens a native dialog, and the path it picked.
export default function PathPick({ label, path, onPick, icon: Icon }) {
  return (
    <div className="flex items-center gap-3 min-w-0">
      <Button type="button" variant="secondary" onClick={onPick}>
        {Icon && <Icon className="size-4" aria-hidden="true" />}
        {label}
      </Button>
      <span
        className={`truncate font-mono text-[12px] ${path ? "text-ink" : "text-ink-muted"}`}
        title={path ?? undefined}
      >
        {path ?? "Nothing chosen yet"}
      </span>
    </div>
  );
}
```

- [ ] **Step 2: `DirectoryBrowser` becomes the folder picker**

Replace `frontend/src/components/DirectoryBrowser.jsx` with:

```jsx
import { useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { AlertCircle, Check, Folder, Loader2 } from "lucide-react";
import { api } from "../lib/api.js";
import PathPick from "./PathPick.jsx";

// Where the MP3s go: the macOS folder picker, then the same writable check as before.
export default function DirectoryBrowser({ value, onChange, onValidityChange }) {
  const [status, setStatus] = useState(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    if (!value) {
      setStatus(null);
      onValidityChange?.(false);
      return;
    }
    let live = true;
    setChecking(true);
    api
      .validateOutputDir(value)
      .then(
        (result) => {
          if (!live) return;
          setStatus(result);
          onValidityChange?.(result.writable);
        },
        (e) => {
          if (!live) return;
          setStatus({ writable: false, error: e.message });
          onValidityChange?.(false);
        },
      )
      .finally(() => live && setChecking(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  async function choose() {
    const picked = await open({ directory: true, multiple: false, defaultPath: value || undefined });
    if (picked) onChange(picked);
  }

  return (
    <div>
      <PathPick label="Choose folder…" icon={Folder} path={value || null} onPick={choose} />
      <div className="mt-1 min-h-5 flex items-center gap-1.5 text-[12px]">
        {checking && (
          <>
            <Loader2 className="size-3.5 animate-spin text-ink-muted" aria-hidden="true" />
            <span className="text-ink-muted">Checking…</span>
          </>
        )}
        {!checking && status?.writable === true && (
          <>
            <Check className="size-3.5 text-sage" aria-hidden="true" />
            <span className="text-sage">This folder is writable</span>
          </>
        )}
        {!checking && status?.writable === false && (
          <>
            <AlertCircle className="size-3.5 text-accent-text" aria-hidden="true" />
            <span className="text-accent-text">{status.error || "Can't write here"}</span>
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Import a book from a path**

In `frontend/src/pages/HomePage.jsx`:
- Add the imports `import { open } from "@tauri-apps/plugin-dialog";`, `import { getCurrentWebview } from "@tauri-apps/api/webview";` and `import PathPick from "../components/PathPick.jsx";`, and add `FileText` to the `lucide-react` import.
- Replace `ImportBookModal` up to its `return (` with:

```jsx
function ImportBookModal({ initialPath, onClose, onImported }) {
  const [path, setPath] = useState(initialPath ?? null);
  const [outputDir, setOutputDir] = useState("");
  const [outputDirWritable, setOutputDirWritable] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function chooseFile() {
    const picked = await open({
      multiple: false,
      filters: [{ name: "EPUB or Markdown", extensions: ["epub", "md"] }],
    });
    if (picked) setPath(picked);
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      onImported(await api.createBook(path, outputDir));
    } catch (e) {
      setError(e.message);
      setSubmitting(false);
    }
  }
```

- In its JSX, replace the first field (the `<label htmlFor="file">` and the `<input type="file">`) with:

```jsx
        <div>
          <p className="text-meta font-semibold mb-1.5">EPUB or Markdown file</p>
          <PathPick label="Choose file…" icon={FileText} path={path} onPick={chooseFile} />
        </div>
```

- Change the submit button to `disabled={!path || !outputDirWritable}`.
- Remove the now-unused `useRef` from the `react` import if nothing else in the file uses it. `HomePage` uses `useRef` for `searchRef`, so it stays.

- [ ] **Step 4: Drag and drop through Tauri**

Tauri takes OS file drops before the webview sees them, so the DOM `drop` event never carries files. In `HomePage`:
- Change the `importing` state comment to `// null | { path?: string }`.
- Delete `handleDrop`.
- Delete the `onDragOver`, `onDragLeave` and `onDrop` props from the outer `<div className="min-h-full">`.
- Add this effect after the keyboard-shortcut effect, before `if (loading) return …`:

```js
  useEffect(() => {
    const stop = getCurrentWebview().onDragDropEvent(({ payload }) => {
      if (payload.type === "enter" || payload.type === "over") setDragging(true);
      else if (payload.type === "leave") setDragging(false);
      else if (payload.type === "drop") {
        setDragging(false);
        if (payload.paths[0]) setImporting({ path: payload.paths[0] });
      }
    });
    return () => {
      stop.then((unlisten) => unlisten());
    };
  }, []);
```

- Change the modal's props from `initialFile={importing.file}` to `initialPath={importing.path}`.

- [ ] **Step 5: Clone a voice from a path**

In `frontend/src/pages/VoicesPage.jsx` `CloneVoiceModal`:
- Add `import { open } from "@tauri-apps/plugin-dialog";` and `import PathPick from "../components/PathPick.jsx";` at the top of the file.
- Add `const [path, setPath] = useState(null);` and:

```js
  async function chooseFile() {
    const picked = await open({
      multiple: false,
      filters: [{ name: "Audio", extensions: ["wav", "mp3", "flac", "ogg", "m4a", "aif", "aiff"] }],
    });
    if (picked) setPath(picked);
  }
```

- In `handleSubmit`, replace the `FormData` lines and the `api.createVoice(form)` call with `const voice = await api.createVoice(path, name, transcript.trim());`.
- Replace the recording field (its `<label htmlFor="file">` and `<input type="file">`) with:

```jsx
        <div>
          <p className="text-meta font-semibold mb-1.5">
            A recording of the voice
            <span className="block font-normal text-ink-muted">10 to 30 seconds of one person reading, somewhere quiet.</span>
          </p>
          <PathPick label="Choose recording…" icon={AudioLines} path={path} onPick={chooseFile} />
        </div>
```

- Change the "Save voice" button to `<Button type="submit" loading={submitting} disabled={!path}>`.

- [ ] **Step 6: Check**

Run: `npm test` and `npm run build`. Expected: pass.

Run: `grep -rn "type=\"file\"\|FormData\|DataTransfer" frontend/src`. Expected: no output.

Run `make dev`, then check:
- Add a book with "Choose file…" and "Choose folder…". It opens on its book page.
- Drag an `.epub` onto the Library: the overlay shows, and the modal opens with the path filled in.
- Choose a folder you can't write to (for example `/System`): the red "cannot write to …" message shows and "Add to shelf" stays disabled.
- Clone a voice from an MP3. Warnings, if any, show as before, and Whisper fills in the transcript within a few seconds.
- Both modals and the Library look right in light and dark themes, per `docs/design/DESIGN.md`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "Pick books, voices and output folders with native dialogs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Exit check and spec notes

**Files:**
- Modify: `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md`

- [ ] **Step 1: Every suite passes**

Run:
- `cd desktop && cargo test -p audiobookify-core && cargo clippy --all-targets`
- `cd frontend && npm test && npm run build`
- `.venv/bin/pytest -q`, from the repo root. The Python app is untouched until phase 6 and must still pass.

Expected: all green. Report counts.

- [ ] **Step 2: The spec's exit condition**

Run: `grep -n "fetch" frontend/src/lib/api.js`. Expected: no output.

- [ ] **Step 3: First-launch import, end to end**

1. Make sure `~/Library/Application Support/com.audiobookify.app` does not exist. If it does, move it aside: `mv ~/Library/Application\ Support/com.audiobookify.app /tmp/audiobookify-data-backup`.
2. Run `make dev` and choose **Import**.
3. Check:
   - The Library shows every book from the browser app.
   - The Voices page shows every voice with its length and pace, and the references play.
   - The Pronunciations page shows every entry.
   - Old text clips play and download.
   - A finished chapter from before plays in the Player.
4. Generate one short text clip with each engine and listen. Then generate one chapter, pause and resume it, and cancel another.
5. Check that the repo's `audiobookify.db`, `uploads/` and `voices/` are unchanged: `git status` shows nothing new.

- [ ] **Step 4: Release build**

1. Run `make up`.
2. Check:
   - The window shows the bundled UI with its fonts.
   - Audio plays, which means the CSP allows `asset:`.
   - The dialogs open.
   - `~/Library/Application Support/com.audiobookify.app/logs/audiobookify.log` has the startup line and the engine worker's log lines after a generation.
3. Quit with Cmd+Q, then run `pgrep -fl engine_worker`. Expected: nothing, within a few seconds.

- [ ] **Step 5: Update the spec**

In `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md`:

- **"Data location":** change the first sentence's tense to say the app has used the app data dir since phase 4. Add: "The import copies the DB with `VACUUM INTO` plus `uploads/`, `voices/` and `text_clips/`, and rewrites the stored paths, including the relative `text_clips/…` ones the Python worker wrote. The chunk cache isn't copied. The repo's files are left in place."
- **"Frontend changes":** remove "During migration, unported endpoints still call `fetch` against the FastAPI sidecar."
- **Phase 6, in "Phases":** change it to "**Delete Python app code.** Remove FastAPI, the CLI, and the Python pipeline/DB, and shrink the Python dependencies. (The Makefile moved to Tauri targets in phase 4.)"
- **"Notes from phase 3":** delete the bullet starting "The Tauri app runs generation in Rust" (the two apps no longer share a DB).
- Add after "Notes from phase 3":

```markdown
## Notes from phase 4

- Every endpoint is a command in `desktop/src/commands.rs` over `audiobookify_core::app`, whose tests port `tests/test_api.py` and the API half of `tests/test_chapter_breaks.py`. The uvicorn sidecar is gone, so CORS was never needed.
- The browser app stopped working here: the frontend calls `invoke`. `make dev`/`make up` are Tauri targets. FastAPI and the CLI stay, untested by the UI, until phase 6.
- App data lives in `~/Library/Application Support/com.audiobookify.app` (see "Data location"). Release builds send stderr (runner errors, engine worker logging) to `logs/audiobookify.log` there, rotated at 5 MB on launch.
- Dropped: wav2vec2 `phonemes` (only the CLI's `lexicon add --from-audio` used it; the GUI uses typed respellings) and `browse-dir` (the native folder picker replaced it, with no typed path).
- The voice PATCH became `set_voice_transcript` and `set_voice_tempo`. Audio reaches `<audio>` through `*_audio_path` commands that add the one file to the asset scope; text-clip Download is a save dialog plus `save_text_clip`.
- Audio files the user picks are read with symphonia (`core::media`). Reference trims are written as 16-bit WAV, matching soundfile, so Rust and the Python engine worker produce the same cached file.
- `create_book` and `create_voice` both copy the picked file into the app data dir.
- `relabel_chapter_mp3` failure handling changed: if retagging fails after the rename, the chapter keeps the renamed file. Python lost track of it.
```

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-10-14-tauri-desktop-design.md
git commit -m "Record phase 4 exit check and notes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

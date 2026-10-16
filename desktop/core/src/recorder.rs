//! Records whatever plays through the default output device to a WAV, for voice-clone
//! reference clips (#31). The OS capture sits behind `SystemAudioCapture` so Windows
//! (WASAPI loopback) can slot in next to the macOS ScreenCaptureKit one.

use std::fs::File;
use std::io::BufWriter;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde::ser::SerializeStruct;
use serde::{Serialize, Serializer};

#[cfg(target_os = "macos")]
mod macos;

/// What the capture is asked for and the WAV is written at. Mono happens at export (step 2).
pub const SAMPLE_RATE: u32 = 48_000;
pub const CHANNELS: u16 = 2;

/// Receives interleaved f32 frames at `SAMPLE_RATE` / `CHANNELS`, on the capture's own thread.
pub type AudioSink = Box<dyn FnMut(&[f32]) + Send>;

pub trait SystemAudioCapture: Send {
    fn start(&mut self, sink: AudioSink) -> Result<(), RecordError>;
    fn stop(&mut self) -> Result<(), RecordError>;
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum RecordError {
    #[error("Audiobookify needs permission to record system audio. Open System Settings > Privacy & Security > \
             Screen & System Audio Recording, turn on Audiobookify, then quit and reopen the app.")]
    PermissionDenied,
    #[error("A recording is already running.")]
    AlreadyRecording,
    #[error("Nothing is being recorded.")]
    NotRecording,
    #[error("Recording system audio is not supported on this system yet.")]
    Unsupported,
    #[error("{0}")]
    Failed(String),
}

impl RecordError {
    fn kind(&self) -> &'static str {
        match self {
            Self::PermissionDenied => "permission_denied",
            Self::AlreadyRecording => "already_recording",
            Self::NotRecording => "not_recording",
            Self::Unsupported => "unsupported",
            Self::Failed(_) => "failed",
        }
    }
}

/// `{kind, message}`: the UI branches on `kind` (e.g. to explain the permission) and shows `message`.
impl Serialize for RecordError {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut st = s.serialize_struct("RecordError", 2)?;
        st.serialize_field("kind", self.kind())?;
        st.serialize_field("message", &self.to_string())?;
        st.end()
    }
}

/// Planar channel buffers to interleaved frames. Stops at the shortest channel.
pub fn interleave(planes: &[&[f32]]) -> Vec<f32> {
    let frames = planes.iter().map(|p| p.len()).min().unwrap_or(0);
    (0..frames).flat_map(|i| planes.iter().map(move |p| p[i])).collect()
}

/// An OS buffer of native-endian f32s. A trailing partial sample is dropped.
pub fn samples_from_ne_bytes(bytes: &[u8]) -> Vec<f32> {
    bytes.chunks_exact(4).map(|b| f32::from_ne_bytes([b[0], b[1], b[2], b[3]])).collect()
}

/// A fresh, unique file name for a take in `dir`.
pub fn recording_path(dir: &Path) -> PathBuf {
    dir.join(format!("recording-{}.wav", uuid::Uuid::new_v4()))
}

/// The platform's capture: ScreenCaptureKit on macOS, `Unsupported` elsewhere for now.
pub fn system_capture() -> Box<dyn SystemAudioCapture> {
    #[cfg(target_os = "macos")]
    return Box::new(macos::ScreenCaptureKit::default());
    #[cfg(not(target_os = "macos"))]
    return Box::new(Unsupported);
}

#[cfg(not(target_os = "macos"))]
struct Unsupported;

#[cfg(not(target_os = "macos"))]
impl SystemAudioCapture for Unsupported {
    fn start(&mut self, _: AudioSink) -> Result<(), RecordError> {
        Err(RecordError::Unsupported)
    }
    fn stop(&mut self) -> Result<(), RecordError> {
        Ok(())
    }
}

type Wav = hound::WavWriter<BufWriter<File>>;

/// What the capture thread writes into. The first write error stops writing and is reported at stop.
#[derive(Default)]
struct Take {
    wav: Option<Wav>,
    error: Option<String>,
}

/// Idle or recording one take, streamed to disk so long takes don't sit in memory.
pub struct Recorder {
    capture: Box<dyn SystemAudioCapture>,
    current: Option<(PathBuf, Arc<Mutex<Take>>)>,
}

impl Recorder {
    pub fn new(capture: Box<dyn SystemAudioCapture>) -> Self {
        Self { capture, current: None }
    }

    pub fn is_recording(&self) -> bool {
        self.current.is_some()
    }

    pub fn start(&mut self, path: PathBuf) -> Result<(), RecordError> {
        if self.is_recording() {
            return Err(RecordError::AlreadyRecording);
        }
        let failed = |e: &dyn std::fmt::Display| RecordError::Failed(format!("Could not create {}: {e}", path.display()));
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| failed(&e))?;
        }
        let spec = hound::WavSpec {
            channels: CHANNELS, sample_rate: SAMPLE_RATE, bits_per_sample: 16, sample_format: hound::SampleFormat::Int,
        };
        let wav = hound::WavWriter::create(&path, spec).map_err(|e| failed(&e))?;
        let take = Arc::new(Mutex::new(Take { wav: Some(wav), error: None }));
        let sink_take = take.clone();
        let sink: AudioSink = Box::new(move |frames| {
            let mut t = sink_take.lock().unwrap_or_else(|p| p.into_inner());
            let Take { wav: Some(wav), error: None } = &mut *t else { return };
            let written = frames.iter().try_for_each(|&s| wav.write_sample((s.clamp(-1.0, 1.0) * 32767.0).round() as i16));
            if let Err(e) = written {
                t.error = Some(e.to_string());
            }
        });
        if let Err(e) = self.capture.start(sink) {
            drop(take);
            let _ = std::fs::remove_file(&path);
            return Err(e);
        }
        self.current = Some((path, take));
        Ok(())
    }

    /// Stops the capture and finishes the WAV. Idle afterwards whatever happens.
    pub fn stop(&mut self) -> Result<PathBuf, RecordError> {
        let (path, take) = self.current.take().ok_or(RecordError::NotRecording)?;
        let stopped = self.capture.stop();
        let mut t = take.lock().unwrap_or_else(|p| p.into_inner());
        let finalized = t.wav.take().map(Wav::finalize);
        stopped?;
        if let Some(e) = t.error.take() {
            return Err(RecordError::Failed(format!("Could not write the recording: {e}")));
        }
        if let Some(Err(e)) = finalized {
            return Err(RecordError::Failed(format!("Could not finish the recording: {e}")));
        }
        Ok(path)
    }
}

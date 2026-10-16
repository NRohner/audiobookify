//! System audio on macOS 13+ through ScreenCaptureKit (the doom-fish `screencapturekit`
//! bindings). Checked by hand: `cargo run -p audiobookify-core --example record_system_audio`.

use std::sync::Mutex;

use screencapturekit::error::SCStreamErrorCode;
use screencapturekit::prelude::*;

use super::{interleave, samples_from_ne_bytes, AudioSink, RecordError, SystemAudioCapture, CHANNELS, SAMPLE_RATE};

#[link(name = "CoreGraphics", kind = "framework")]
extern "C" {
    fn CGPreflightScreenCaptureAccess() -> bool;
    fn CGRequestScreenCaptureAccess() -> bool;
}

#[derive(Default)]
pub struct ScreenCaptureKit {
    stream: Option<SCStream>,
}

struct AudioOut(Mutex<AudioSink>);

impl SCStreamOutputTrait for AudioOut {
    fn did_output_sample_buffer(&self, sample: CMSampleBuffer, of_type: SCStreamOutputType) {
        if !matches!(of_type, SCStreamOutputType::Audio) {
            return;
        }
        let Ok(list) = sample.audio_buffer_list() else { return };
        // ScreenCaptureKit hands over float32, one buffer per channel; a single buffer is
        // already interleaved.
        let planes: Vec<Vec<f32>> = list.iter().map(|b| samples_from_ne_bytes(b.data())).collect();
        let frames = match planes.as_slice() {
            [one] => one.clone(),
            many => interleave(&many.iter().map(Vec::as_slice).collect::<Vec<_>>()),
        };
        (self.0.lock().unwrap_or_else(|p| p.into_inner()))(&frames);
    }
}

fn to_record_error(e: SCError) -> RecordError {
    match e {
        SCError::PermissionDenied(_) | SCError::SCStreamError { code: SCStreamErrorCode::UserDeclined, .. } => {
            RecordError::PermissionDenied
        }
        other => RecordError::Failed(format!("Could not record system audio: {other}")),
    }
}

impl SystemAudioCapture for ScreenCaptureKit {
    fn start(&mut self, sink: AudioSink) -> Result<(), RecordError> {
        // Audio-only capture still needs the Screen Recording permission. The request
        // shows the system prompt the first time and returns at once; access only takes
        // effect after a relaunch, so a fresh grant still reports denied here.
        if !unsafe { CGPreflightScreenCaptureAccess() } {
            unsafe { CGRequestScreenCaptureAccess() };
            return Err(RecordError::PermissionDenied);
        }
        let content = SCShareableContent::get().map_err(to_record_error)?;
        let display = content
            .displays()
            .into_iter()
            .next()
            .ok_or_else(|| RecordError::Failed("No display to capture system audio from.".into()))?;
        let filter = SCContentFilter::create().with_display(&display).with_excluding_windows(&[]).build()
            .map_err(to_record_error)?;
        // ponytail: a display filter is the only route to system audio before macOS 14.2's
        // process taps; the video it drags along is kept tiny and slow and never read.
        let config = SCStreamConfiguration::new()
            .with_width(2)
            .with_height(2)
            .with_minimum_frame_interval(&CMTime::new(1, 1))
            .with_captures_audio(true)
            .with_sample_rate(SAMPLE_RATE as i32)
            .with_channel_count(CHANNELS as i32)
            // The app's own playback (e.g. previewing a take) stays out of the recording.
            .with_excludes_current_process_audio(true);
        let mut stream = SCStream::new(&filter, &config).map_err(to_record_error)?;
        stream.add_output_handler(AudioOut(Mutex::new(sink)), SCStreamOutputType::Audio).map_err(to_record_error)?;
        stream.start_capture().map_err(to_record_error)?;
        self.stream = Some(stream);
        Ok(())
    }

    fn stop(&mut self) -> Result<(), RecordError> {
        match self.stream.take() {
            Some(stream) => stream.stop_capture().map_err(to_record_error),
            None => Ok(()),
        }
    }
}

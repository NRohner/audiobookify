use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use audiobookify_core::recorder::{
    interleave, recording_path, samples_from_ne_bytes, AudioSink, Recorder, RecordError, SystemAudioCapture,
    CHANNELS, SAMPLE_RATE,
};
use tempfile::TempDir;

/// Stands in for the OS: hands the test the sink so it can "play" audio into it.
#[derive(Clone, Default)]
struct FakeCapture {
    sink: Arc<Mutex<Option<AudioSink>>>,
    fail_start: Option<RecordError>,
    stops: Arc<Mutex<u32>>,
}

impl FakeCapture {
    fn play(&self, frames: &[f32]) {
        (self.sink.lock().unwrap().as_mut().expect("capture is running"))(frames);
    }
}

impl SystemAudioCapture for FakeCapture {
    fn start(&mut self, sink: AudioSink) -> Result<(), RecordError> {
        if let Some(e) = self.fail_start.clone() {
            return Err(e);
        }
        *self.sink.lock().unwrap() = Some(sink);
        Ok(())
    }

    fn stop(&mut self) -> Result<(), RecordError> {
        *self.sink.lock().unwrap() = None;
        *self.stops.lock().unwrap() += 1;
        Ok(())
    }
}

fn read(path: &PathBuf) -> (hound::WavSpec, Vec<i16>) {
    let mut r = hound::WavReader::open(path).unwrap();
    let spec = r.spec();
    (spec, r.samples::<i16>().map(Result::unwrap).collect())
}

#[test]
fn planar_channels_interleave_frame_by_frame() {
    assert_eq!(interleave(&[&[1.0, 2.0, 3.0], &[-1.0, -2.0, -3.0]]), vec![1.0, -1.0, 2.0, -2.0, 3.0, -3.0]);
    assert_eq!(interleave(&[&[0.5, 0.25]]), vec![0.5, 0.25]);
    // A short channel truncates to whole frames rather than misaligning the rest.
    assert_eq!(interleave(&[&[1.0, 2.0], &[9.0]]), vec![1.0, 9.0]);
    assert_eq!(interleave(&[]), Vec::<f32>::new());
}

#[test]
fn native_endian_bytes_become_samples_and_a_partial_tail_is_dropped() {
    let mut bytes: Vec<u8> = [0.5f32, -1.0].iter().flat_map(|s| s.to_ne_bytes()).collect();
    bytes.push(7);
    assert_eq!(samples_from_ne_bytes(&bytes), vec![0.5, -1.0]);
}

#[test]
fn recording_paths_are_unique_wavs_in_the_given_dir() {
    let dir = TempDir::new().unwrap();
    let (a, b) = (recording_path(dir.path()), recording_path(dir.path()));
    assert_ne!(a, b);
    assert_eq!(a.parent(), Some(dir.path()));
    assert_eq!(a.extension().unwrap(), "wav");
}

#[test]
fn start_then_stop_writes_a_16_bit_stereo_wav_of_what_played() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("take.wav");
    let capture = FakeCapture::default();
    let mut recorder = Recorder::new(Box::new(capture.clone()));

    recorder.start(path.clone()).unwrap();
    assert!(recorder.is_recording());
    capture.play(&[0.0, 0.5, -0.5, 1.0]);
    capture.play(&[2.0, -2.0, f32::NAN, -1.0]);
    assert_eq!(recorder.stop().unwrap(), path);

    assert!(!recorder.is_recording());
    assert_eq!(*capture.stops.lock().unwrap(), 1);
    let (spec, samples) = read(&path);
    assert_eq!((spec.channels, spec.sample_rate, spec.bits_per_sample), (CHANNELS, SAMPLE_RATE, 16));
    // Out-of-range samples clip; NaN becomes silence.
    assert_eq!(samples, vec![0, 16384, -16384, 32767, 32767, -32767, 0, -32767]);
}

#[test]
fn a_second_start_is_refused_and_the_first_take_keeps_going() {
    let dir = TempDir::new().unwrap();
    let capture = FakeCapture::default();
    let mut recorder = Recorder::new(Box::new(capture.clone()));
    recorder.start(dir.path().join("one.wav")).unwrap();

    assert_eq!(recorder.start(dir.path().join("two.wav")), Err(RecordError::AlreadyRecording));
    assert!(!dir.path().join("two.wav").exists());

    capture.play(&[0.5, 0.5]);
    let (_, samples) = read(&recorder.stop().unwrap());
    assert_eq!(samples.len(), 2);
}

#[test]
fn stop_without_a_recording_is_an_error() {
    let mut recorder = Recorder::new(Box::new(FakeCapture::default()));
    assert_eq!(recorder.stop(), Err(RecordError::NotRecording));
}

#[test]
fn a_denied_permission_surfaces_typed_and_leaves_no_file() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("take.wav");
    let capture = FakeCapture { fail_start: Some(RecordError::PermissionDenied), ..Default::default() };
    let mut recorder = Recorder::new(Box::new(capture));

    assert_eq!(recorder.start(path.clone()), Err(RecordError::PermissionDenied));
    assert!(!recorder.is_recording());
    assert!(!path.exists());
}

#[test]
fn errors_serialize_with_a_kind_the_ui_can_branch_on() {
    let json = serde_json::to_value(RecordError::PermissionDenied).unwrap();
    assert_eq!(json["kind"], "permission_denied");
    assert!(json["message"].as_str().unwrap().contains("Screen & System Audio Recording"));
    let json = serde_json::to_value(RecordError::Failed("boom".into())).unwrap();
    assert_eq!((json["kind"].as_str(), json["message"].as_str()), (Some("failed"), Some("boom")));
}

#[test]
fn the_start_dir_is_created_if_missing() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("nested/take.wav");
    let mut recorder = Recorder::new(Box::new(FakeCapture::default()));
    recorder.start(path.clone()).unwrap();
    recorder.stop().unwrap();
    assert!(path.is_file());
}

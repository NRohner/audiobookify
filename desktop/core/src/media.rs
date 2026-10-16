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

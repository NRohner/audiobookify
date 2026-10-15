//! Where voice reference clips live permanently, ported from voice/storage.py.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

/// Drops the characters macOS/Windows file names can't hold, like postprocess/audio.py.
pub fn sanitize_filename(name: &str) -> String {
    name.chars().filter(|c| !r#"\/:*?"<>|"#.contains(*c)).collect::<String>().trim().to_string()
}

pub fn permanent_voice_dir(voices_dir: &Path, profile_id: i64, name: &str) -> PathBuf {
    voices_dir.join(format!("{profile_id}_{}", sanitize_filename(name)))
}

/// Copies a staged clip to `<voices_dir>/<id>_<name>/reference<ext>` and returns that path.
pub fn store_reference_audio(voices_dir: &Path, staged: &Path, profile_id: i64, name: &str) -> io::Result<PathBuf> {
    let dest_dir = permanent_voice_dir(voices_dir, profile_id, name);
    fs::create_dir_all(&dest_dir)?;
    let suffix = staged.extension().map(|ext| format!(".{}", ext.to_string_lossy())).unwrap_or_default();
    let dest = dest_dir.join(format!("reference{suffix}"));
    if fs::canonicalize(&dest).is_ok_and(|d| fs::canonicalize(staged).is_ok_and(|s| s == d)) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "source and destination are the same file"));
    }
    fs::copy(staged, &dest)?;
    Ok(dest)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitize_filename_drops_reserved_characters_and_trims() {
        assert_eq!(sanitize_filename(r#" a/b\c:d*e?f"g<h>i|j "#), "abcdefghij");
        assert_eq!(sanitize_filename("Jim Dale"), "Jim Dale");
    }

    #[test]
    fn store_reference_audio_refuses_to_copy_a_clip_onto_itself() {
        let tmp = tempfile::TempDir::new().unwrap();
        let clip = permanent_voice_dir(tmp.path(), 1, "Nova").join("reference.wav");
        fs::create_dir_all(clip.parent().unwrap()).unwrap();
        fs::write(&clip, b"precious audio").unwrap();

        let err = store_reference_audio(tmp.path(), &clip, 1, "Nova").unwrap_err();

        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);
        assert_eq!(fs::read(&clip).unwrap(), b"precious audio");
    }
}

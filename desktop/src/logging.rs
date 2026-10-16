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

//! Manual check for the system audio recorder: play something, then
//! `cargo run -p audiobookify-core --example record_system_audio -- 5 /tmp/take.wav`.
//! The Screen Recording permission attaches to the terminal app running this.

use std::path::PathBuf;
use std::time::Duration;

use audiobookify_core::recorder::{system_capture, Recorder};

fn main() {
    let mut args = std::env::args().skip(1);
    let seconds: u64 = args.next().and_then(|s| s.parse().ok()).unwrap_or(5);
    let path = args.next().map_or_else(|| std::env::temp_dir().join("system-audio.wav"), PathBuf::from);
    let mut recorder = Recorder::new(system_capture());
    if let Err(e) = recorder.start(path) {
        eprintln!("{e}");
        std::process::exit(1);
    }
    std::thread::sleep(Duration::from_secs(seconds));
    match recorder.stop() {
        Ok(path) => println!("{}", path.display()),
        Err(e) => eprintln!("{e}"),
    }
}

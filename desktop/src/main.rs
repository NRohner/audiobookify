mod commands;
mod logging;

use std::os::fd::AsRawFd;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::AtomicBool;
use std::sync::{Mutex, RwLock};

use audiobookify_core::app::AppPaths;
use audiobookify_core::audio::child_path;
use audiobookify_core::datadir::import_repo_data;
use audiobookify_core::db::Database;
use audiobookify_core::engine::PythonEngine;
use audiobookify_core::hub::{self, store::Hub, HubCache};
use audiobookify_core::jobs::{Runner, RunnerControl};
use audiobookify_core::recorder::{system_capture, Recorder};
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

fn python() -> PathBuf {
    repo_root().join(".venv/bin/python")
}

/// Generation, on its own thread with its own DB connection (opened by the caller, so a
/// failure shows the startup alert). It owns the Python engine worker child, whose stdin
/// closes when this process exits, however it exits; the worker then finishes its chunk
/// and quits. A job cut off mid-chapter is requeued by `reset_stale_generating_jobs` on
/// the next launch. The returned control shares state with the runner.
fn start_runner(db: Database, paths: &AppPaths, models: HubCache) -> RunnerControl {
    let engine = PythonEngine::new(python(), repo_root())
        .env("HF_HUB_OFFLINE", "1")
        // The exact root Rust gates on, so the worker never reads a different cache.
        .env("HF_HUB_CACHE", &models.root)
        .env("PATH", child_path(std::env::var_os("PATH")));
    let mut runner = Runner::new(db, engine, paths.runner_paths(models));
    let control = runner.control();
    std::thread::Builder::new()
        .name("job-runner".into())
        .spawn(move || runner.run_forever(&AtomicBool::new(false)))
        .expect("spawn the job runner thread");
    control
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

/// Takes an exclusive lock on the data dir for the life of the process. Two copies
/// sharing one DB would have the second's stale-job reset requeue the first's running job.
fn hold_instance_lock(paths: &AppPaths) -> std::fs::File {
    let file = std::fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(false)
        .open(paths.root.join("audiobookify.lock"))
        .unwrap_or_else(|e| fail(&format!("Could not open the lock file in {}: {e}", paths.root.display())));
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        fail("Audiobookify is already running. Quit the other copy (or the `make dev` window), then try again.");
    }
    file
}

fn main() {
    let paths = AppPaths::new(data_dir());
    if let Err(e) = std::fs::create_dir_all(&paths.root) {
        fail(&format!("Could not create {}: {e}", paths.root.display()));
    }
    let _lock = hold_instance_lock(&paths);
    if !tauri::is_dev() {
        logging::stderr_to(&paths.log());
    }
    offer_import(&paths);
    let open = || {
        paths.open_db().unwrap_or_else(|e| fail(&format!("Could not open the database in {}: {e}", paths.root.display())))
    };
    let models = HubCache::new(hub::default_cache_dir());
    let runner = start_runner(open(), &paths, models.clone());
    let hub = Hub::new(models, python(), repo_root()).env("PATH", child_path(std::env::var_os("PATH")));
    let state = AppState { db: Mutex::new(open()), paths: RwLock::new(paths.clone()), hub, runner, recorder: Mutex::new(Recorder::new(system_capture())) };

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
            commands::create_voice_from_clip,
            commands::export_mono_clip,
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
            commands::list_models,
            commands::model_progress,
            commands::download_model,
            commands::cancel_model_download,
            commands::delete_model,
            commands::get_stats,
            commands::get_settings,
            commands::set_voices_dir,
            commands::start_recording,
            commands::stop_recording,
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

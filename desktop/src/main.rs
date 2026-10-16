mod sidecars;

use audiobookify_core::audio::child_path;
use audiobookify_core::db::Database;
use audiobookify_core::engine::PythonEngine;
use audiobookify_core::jobs::{Paths, Runner};
use sidecars::{audiobookify_commands, wait_port_free, Sidecars, API_ADDR};
use std::path::Path;
use std::sync::atomic::AtomicBool;
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

// ponytail: the repo path is baked in at compile time; this is a personal-Macs-only build
// (spec open question 1). A shareable app would bundle Python instead.
fn repo_root() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().expect("desktop/ has a parent")
}

fn start_sidecars() -> Result<Sidecars, String> {
    if !wait_port_free(API_ADDR, Duration::from_secs(6)) {
        return Err(format!(
            "{API_ADDR} is already in use. Run `make down` or quit the other Audiobookify, then relaunch."
        ));
    }
    let mut sc = Sidecars::start(audiobookify_commands(repo_root()))
        .map_err(|e| format!("Could not start the API from {}/.venv: {e}", repo_root().display()))?;
    sc.wait_ready(API_ADDR, Duration::from_secs(180))?; // on Err, `sc` drops here and stops the children
    Ok(sc)
}

/// Generation, on its own thread with its own DB connection (opened by the caller, so a
/// failure shows the startup alert). It owns the Python engine worker child, whose stdin
/// closes when this process exits, however it exits; the worker then finishes its chunk
/// and quits. A job cut off mid-chapter is requeued by `reset_stale_generating_jobs` on
/// the next launch, as before.
fn start_runner(db: Database) {
    let repo = repo_root().to_path_buf();
    std::thread::Builder::new()
        .name("job-runner".into())
        .spawn(move || {
            let engine = PythonEngine::new(repo.join(".venv/bin/python"), &repo)
                .env("HF_HUB_OFFLINE", "1")
                .env("PATH", child_path(std::env::var_os("PATH")));
            let paths = Paths {
                cache_root: repo.join(".cache/synth"),
                text_clip_dir: repo.join("text_clips"),
                backup_dir: repo.join("output"),
            };
            Runner::new(db, engine, paths).run_forever(&AtomicBool::new(false));
        })
        .expect("spawn the job runner thread");
}

/// Startup failures happen before Tauri runs: an error returned from Tauri's setup hook
/// aborts inside the macOS app delegate, which shows up as a crash report, not a message.
fn fail(msg: &str) -> ! {
    eprintln!("{msg}");
    // The message goes in as argv, so it needs no AppleScript escaping.
    let _ = std::process::Command::new("/usr/bin/osascript")
        .args([
            "-e", "on run argv",
            "-e", "display alert \"Audiobookify could not start\" message (item 1 of argv) as critical",
            "-e", "end run",
            msg,
        ])
        .status();
    std::process::exit(1);
}

fn main() {
    let sc = start_sidecars().unwrap_or_else(|e| fail(&e));
    let repo = repo_root();
    let db = Database::open(repo.join("audiobookify.db"), repo.join("voices"))
        .unwrap_or_else(|e| fail(&format!("Could not open the database in {}: {e}", repo.display())));
    start_runner(db);
    let app = tauri::Builder::default()
        .manage(Mutex::new(Some(sc)))
        .setup(|app| {
            // Dev: the Vite server (proxies /api). Release: FastAPI serves frontend/dist itself,
            // so the page stays same-origin with /api and the frontend needs no changes.
            let url = if tauri::is_dev() {
                WebviewUrl::App("index.html".into())
            } else {
                WebviewUrl::External(format!("http://{API_ADDR}").parse().unwrap())
            };
            WebviewWindowBuilder::new(app, "main", url)
                .title("Audiobookify")
                .inner_size(1280.0, 860.0)
                .min_inner_size(900.0, 600.0)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Audiobookify");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(sc) = handle.state::<Mutex<Option<Sidecars>>>().lock().unwrap().take() {
                sc.shutdown();
            }
        }
    });
}

mod sidecars;

use sidecars::{audiobookify_commands, wait_port_free, Sidecars, API_ADDR};
use std::path::Path;
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

// ponytail: the repo path is baked in at compile time; this is a personal-Macs-only build
// (spec open question 1). A shareable app would bundle Python instead.
fn repo_root() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().expect("desktop/ has a parent")
}

fn main() {
    let app = tauri::Builder::default()
        .setup(|app| {
            if !wait_port_free(API_ADDR, Duration::from_secs(6)) {
                return Err(format!(
                    "{API_ADDR} is already in use. Run `make down` or quit the other Audiobookify, then relaunch."
                )
                .into());
            }
            let mut sc = Sidecars::start(audiobookify_commands(repo_root()))?;
            sc.wait_ready(API_ADDR, Duration::from_secs(60))?;
            app.manage(Mutex::new(Some(sc)));

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

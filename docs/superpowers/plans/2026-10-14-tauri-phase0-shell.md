# Tauri Desktop Phase 0 (Shell) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A native macOS Audiobookify window, launched with `make app-dev` or as a built `.app`. It starts the existing FastAPI server and worker as child processes and kills them on quit. App behavior is identical to `make up` / `make dev`.

**Architecture:** A new Tauri 2 crate in `desktop/`. A small `sidecars` module spawns `uvicorn` and `audiobookify worker` from the repo's `.venv`, waits for port 8000, then opens the window. In dev the window loads the Vite dev server (`devUrl`, which proxies `/api`). In release it loads `http://127.0.0.1:8000` directly, where FastAPI already serves `frontend/dist`. The page stays same-origin with `/api`, so **no frontend changes** are needed in this phase. On `RunEvent::Exit` the children are killed. The worker already resets stale jobs on startup (`db.reset_stale_generating_jobs()` in `worker.py`), so killing it mid-job is safe.

**Tech Stack:** Rust (stable, cargo 1.98), Tauri 2 (`tauri-cli` 2.10.1 is installed), existing Python 3.12 `.venv`, Vite 5 frontend.

**Spec:** `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md`, phase 0.

## Global Constraints

- macOS on Apple Silicon only.
- Personal Macs only: unsigned `.app`, which uses the repo's `.venv` and Homebrew ffmpeg/espeak-ng (spec open question 1, default accepted).
- The Tauri crate lives in `desktop/`.
- No frontend changes in phase 0.
- No emojis anywhere in the UI.
- The API must stay bound to `127.0.0.1:8000`, matching the Vite proxy in `frontend/vite.config.js`.
- `HF_HUB_OFFLINE=1` for both children, as in the Makefile's `up`/`dev` targets.

## File structure

| File | Responsibility |
|---|---|
| `desktop/Cargo.toml` | Crate manifest (`tauri`, `tauri-build`). |
| `desktop/build.rs` | `tauri_build::build()`. |
| `desktop/tauri.conf.json` | App identity, dev/build hooks, bundle config. No windows are declared; the window is created in code after the API is up. |
| `desktop/icons/` | Default Tauri icons, copied from a scratch `cargo tauri init`. |
| `desktop/src/sidecars.rs` | Spawning, readiness wait, and shutdown of child processes. Pure std, unit-tested. |
| `desktop/src/main.rs` | Tauri builder: port check, sidecar start, window creation, kill on exit. |
| `Makefile` | `app-dev` and `app` targets. |
| `.gitignore` | `desktop/target/` and `desktop/gen/`. |

---

### Task 1: Scaffold the Tauri crate

**Files:**
- Create: `desktop/Cargo.toml`, `desktop/build.rs`, `desktop/tauri.conf.json`, `desktop/src/main.rs` (temporary stub), `desktop/icons/*`
- Modify: `.gitignore`

**Interfaces:**
- Produces: a crate named `audiobookify-desktop` that compiles with `cargo build` from `desktop/`.

- [ ] **Step 1: Generate default icons in a scratch directory**

Run:
```bash
SCRATCH=$(mktemp -d) && cargo tauri init --ci -d "$SCRATCH" -A audiobookify -W Audiobookify -D ../dist -P http://localhost:5173 && mkdir -p desktop && cp -R "$SCRATCH/src-tauri/icons" desktop/icons && ls desktop/icons
```
Expected: `desktop/icons` contains `icon.icns`, `icon.png`, `32x32.png`, `128x128.png`, `128x128@2x.png` (plus others).

- [ ] **Step 2: Write `desktop/Cargo.toml`**

```toml
[package]
name = "audiobookify-desktop"
version = "0.1.0"
edition = "2021"

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = [] }
```

- [ ] **Step 3: Write `desktop/build.rs`**

```rust
fn main() {
    tauri_build::build()
}
```

- [ ] **Step 4: Write `desktop/tauri.conf.json`**

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "Audiobookify",
  "version": "0.1.0",
  "identifier": "com.audiobookify.app",
  "build": {
    "devUrl": "http://localhost:5173",
    "frontendDist": "../frontend/dist",
    "beforeDevCommand": { "script": "npm run dev", "cwd": "../frontend", "wait": false },
    "beforeBuildCommand": { "script": "npm run build", "cwd": "../frontend" }
  },
  "app": {
    "windows": [],
    "security": { "csp": null }
  },
  "bundle": {
    "active": true,
    "targets": ["app"],
    "icon": ["icons/32x32.png", "icons/128x128.png", "icons/128x128@2x.png", "icons/icon.icns", "icons/icon.png"]
  }
}
```

`devUrl` uses `localhost`, not `127.0.0.1`, because Vite 5 binds to `localhost`, which can resolve to `::1` only.

- [ ] **Step 5: Write a stub `desktop/src/main.rs` so the crate builds**

```rust
fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running Audiobookify");
}
```

- [ ] **Step 6: Ignore build output**

Append to `.gitignore`:
```
desktop/target/
desktop/gen/
```

- [ ] **Step 7: Verify it compiles**

Run: `cd desktop && cargo build`
Expected: `Finished` with no errors. The first build downloads crates and takes a few minutes.

- [ ] **Step 8: Commit**

```bash
git add desktop .gitignore
git commit -m "Scaffold Tauri desktop crate"
```

---

### Task 2: Sidecar process management (TDD)

**Files:**
- Create: `desktop/src/sidecars.rs`
- Modify: `desktop/src/main.rs` (add `mod sidecars;` only)

**Interfaces:**
- Produces (used by Task 3):
  - `pub const API_ADDR: &str = "127.0.0.1:8000";`
  - `pub fn port_in_use(addr: &str) -> bool`
  - `pub fn audiobookify_commands(repo: &Path) -> Vec<Command>`
  - `pub struct Sidecars` with:
    - `pub fn start(cmds: Vec<Command>) -> std::io::Result<Sidecars>`
    - `pub fn wait_ready(&mut self, addr: &str, timeout: Duration) -> Result<(), String>`: Ok once `addr` accepts connections, and Err as soon as any child exits or the timeout passes.
    - `pub fn pids(&self) -> Vec<u32>`
    - `pub fn shutdown(self)`: kills and reaps every child. `Drop` does the same, so an early return can't leak processes.

- [ ] **Step 1: Write the failing tests** at the bottom of `desktop/src/sidecars.rs`, and add `mod sidecars;` to the top of `main.rs`.

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::time::Instant;

    fn free_addr() -> String {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        l.local_addr().unwrap().to_string() // listener drops here, port is free again
    }

    fn sleeper() -> Command {
        let mut c = Command::new("sleep");
        c.arg("30");
        c
    }

    fn alive(pid: u32) -> bool {
        Command::new("kill").args(["-0", &pid.to_string()]).status().unwrap().success()
    }

    #[test]
    fn port_in_use_detects_listener() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        assert!(port_in_use(&addr));
        drop(l);
        assert!(!port_in_use(&addr));
    }

    #[test]
    fn wait_ready_returns_once_port_listens() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        let mut sc = Sidecars::start(vec![sleeper()]).unwrap();
        assert_eq!(sc.wait_ready(&addr, Duration::from_secs(5)), Ok(()));
        sc.shutdown();
    }

    #[test]
    fn wait_ready_fails_fast_when_a_child_exits() {
        let mut sc = Sidecars::start(vec![Command::new("false")]).unwrap();
        let started = Instant::now();
        let err = sc.wait_ready(&free_addr(), Duration::from_secs(10)).unwrap_err();
        assert!(err.contains("exited"), "unexpected error: {err}");
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn wait_ready_times_out() {
        let mut sc = Sidecars::start(vec![sleeper()]).unwrap();
        let err = sc.wait_ready(&free_addr(), Duration::from_millis(300)).unwrap_err();
        assert!(err.contains("timed out"), "unexpected error: {err}");
    }

    #[test]
    fn shutdown_kills_children() {
        let sc = Sidecars::start(vec![sleeper(), sleeper()]).unwrap();
        let pids = sc.pids();
        assert!(pids.iter().all(|p| alive(*p)));
        sc.shutdown();
        assert!(pids.iter().all(|p| !alive(*p)));
    }

    #[test]
    fn drop_kills_children() {
        let sc = Sidecars::start(vec![sleeper()]).unwrap();
        let pid = sc.pids()[0];
        drop(sc);
        assert!(!alive(pid));
    }

    #[test]
    fn audiobookify_commands_use_repo_venv() {
        let repo = Path::new("/repo");
        let cmds = audiobookify_commands(repo);
        let programs: Vec<_> = cmds.iter().map(|c| c.get_program().to_owned()).collect();
        assert_eq!(programs, ["/repo/.venv/bin/uvicorn", "/repo/.venv/bin/audiobookify"]);
        let api_args: Vec<_> = cmds[0].get_args().map(|a| a.to_str().unwrap()).collect();
        assert_eq!(api_args, ["audiobookify.api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000"]);
        assert_eq!(cmds[1].get_args().collect::<Vec<_>>(), ["worker"]);
        for c in &cmds {
            assert_eq!(c.get_current_dir(), Some(repo));
            assert!(c.get_envs().any(|(k, v)| k == "HF_HUB_OFFLINE" && v == Some("1".as_ref())));
        }
    }
}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd desktop && cargo test sidecars`
Expected: compile errors (`cannot find function port_in_use`, `cannot find type Sidecars`, ...).

- [ ] **Step 3: Implement** above the test module in `desktop/src/sidecars.rs`

```rust
use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::process::{Child, Command};
use std::thread::sleep;
use std::time::{Duration, Instant};

pub const API_ADDR: &str = "127.0.0.1:8000";

pub fn port_in_use(addr: &str) -> bool {
    let addr: SocketAddr = addr.parse().expect("valid socket address");
    TcpStream::connect_timeout(&addr, Duration::from_millis(200)).is_ok()
}

/// The two processes `make up` starts: the FastAPI app and the generation worker.
pub fn audiobookify_commands(repo: &Path) -> Vec<Command> {
    let bin = repo.join(".venv/bin");
    let mut api = Command::new(bin.join("uvicorn"));
    api.args(["audiobookify.api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000"]);
    let mut worker = Command::new(bin.join("audiobookify"));
    worker.arg("worker");
    let mut cmds = vec![api, worker];
    for c in &mut cmds {
        // The repo root as cwd: create_app() and the worker default to ./audiobookify.db, ./uploads, ./voices.
        c.current_dir(repo).env("HF_HUB_OFFLINE", "1");
    }
    cmds
}

pub struct Sidecars(Vec<Child>);

impl Sidecars {
    pub fn start(cmds: Vec<Command>) -> std::io::Result<Sidecars> {
        let mut sc = Sidecars(Vec::new());
        for mut c in cmds {
            sc.0.push(c.spawn()?); // on error, Drop kills the ones already started
        }
        Ok(sc)
    }

    pub fn wait_ready(&mut self, addr: &str, timeout: Duration) -> Result<(), String> {
        let deadline = Instant::now() + timeout;
        loop {
            if port_in_use(addr) {
                return Ok(());
            }
            for child in &mut self.0 {
                if let Ok(Some(status)) = child.try_wait() {
                    return Err(format!("process {} exited before {addr} was ready ({status})", child.id()));
                }
            }
            if Instant::now() >= deadline {
                return Err(format!("timed out after {timeout:?} waiting for {addr}"));
            }
            sleep(Duration::from_millis(100));
        }
    }

    pub fn pids(&self) -> Vec<u32> {
        self.0.iter().map(Child::id).collect()
    }

    pub fn shutdown(self) {} // Drop does the work
}

impl Drop for Sidecars {
    fn drop(&mut self) {
        // ponytail: SIGKILL, not SIGTERM. Safe because the worker resets stale jobs on
        // startup and the chunk cache resumes. Add graceful shutdown if that ever changes.
        for child in &mut self.0 {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd desktop && cargo test sidecars`
Expected: `test result: ok. 7 passed`.

- [ ] **Step 5: Commit**

```bash
git add desktop/src
git commit -m "Add sidecar process management for the desktop shell"
```

---

### Task 3: Wire the sidecars into the Tauri app

**Files:**
- Modify: `desktop/src/main.rs` (replace the stub)

**Interfaces:**
- Consumes: `sidecars::{API_ADDR, port_in_use, audiobookify_commands, Sidecars}` from Task 2.
- Produces: a runnable app via `cargo tauri dev` / `cargo tauri build`.

This task is glue around tested pieces and the Tauri runtime, so it's verified by running the app (Step 2), not by unit tests.

- [ ] **Step 1: Replace `desktop/src/main.rs`**

```rust
mod sidecars;

use sidecars::{audiobookify_commands, port_in_use, Sidecars, API_ADDR};
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
            if port_in_use(API_ADDR) {
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
```

- [ ] **Step 2: Run it in dev**

Run: `make down; cd desktop && cargo tauri dev`
Expected:
- Vite starts.
- Terminal shows uvicorn's `Uvicorn running on http://127.0.0.1:8000` and the worker's startup lines.
- A native "Audiobookify" window opens on the home page with the existing library.

If Vite fails with an `ENOENT ... package.json` error, the `cwd` in `beforeDevCommand` resolved against a different base: change both `cwd` values in `tauri.conf.json` to `"frontend"` and retry.

- [ ] **Step 3: Exercise the app**

Do each of the following in the window:
- Open a book.
- Play a generated chapter in the Player.
- Open Voices and play a preview.
- Pause and resume the worker from the app shell.

Expected: everything behaves as it does under `make dev`.

- [ ] **Step 4: Confirm quitting cleans up**

Quit with Cmd+Q, then run: `lsof -ti:8000; pgrep -f "audiobookify worker"`
Expected: no output from either command.

Relaunch, close the window with the red button, and run the same command.
Expected: no output (on macOS, closing the last window exits the app).

- [ ] **Step 5: Confirm the port-conflict guard**

Run `make up`, then `cd desktop && cargo tauri dev`.
Expected: the app exits with the error `127.0.0.1:8000 is already in use...` in the terminal, and no second worker starts (`pgrep -f "audiobookify worker" | wc -l` prints `1`).
Then run `make down`.

- [ ] **Step 6: Commit**

```bash
git add desktop/src/main.rs desktop/tauri.conf.json
git commit -m "Launch the API and worker from the desktop shell"
```

---

### Task 4: Make targets and a built `.app`

**Files:**
- Modify: `Makefile` (the `.PHONY` line, plus two new targets after `dev`)
- Modify: `README.md` (one short section)

- [ ] **Step 1: Add the targets.** Add `app-dev app` to the `.PHONY` line, then add after the `dev` target:

```make
# Native window around the same API + worker. Quitting the app stops both.
app-dev: check-not-running
	cd desktop && cargo tauri dev

# Builds desktop/target/release/bundle/macos/Audiobookify.app. It runs this checkout's
# .venv, so keep the repo where it is.
app: check-not-running
	cd desktop && cargo tauri build
	@echo "built desktop/target/release/bundle/macos/Audiobookify.app"
```

`app` doesn't depend on `build`, because `beforeBuildCommand` already runs `npm run build`.

- [ ] **Step 2: Build the bundle**

Run: `make app`
Expected: the build ends with `built desktop/target/release/bundle/macos/Audiobookify.app`.

- [ ] **Step 3: Launch the built app from Finder**

Run: `open desktop/target/release/bundle/macos/Audiobookify.app`
Expected: within a few seconds a window shows the library, loaded from `http://127.0.0.1:8000`. The release build points at FastAPI, not Vite.
Quit the app, then run: `lsof -ti:8000`
Expected: no output.

- [ ] **Step 4: Document it.** Add to `README.md` after the `## Setup` section:

```markdown
## Desktop app

`make app-dev` opens Audiobookify in a native window (with hot reload). `make app` builds `desktop/target/release/bundle/macos/Audiobookify.app`. Both start the API and worker for you and stop them when you quit. The app runs this checkout's `.venv`, so build it after `make setup` and don't move the repo. The port to a full Rust backend is in progress; see `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md`.
```

- [ ] **Step 5: Commit**

```bash
git add Makefile README.md
git commit -m "Add make targets for the desktop app"
```

---

## Out of scope (later phases)

- A native error dialog when startup fails. Startup errors currently print to the terminal only. A dialog will come with `tauri-plugin-dialog` in phase 4, which needs that plugin anyway for file pickers.
- Bundling the frontend into the app (`tauri://` origin), IPC capabilities, and `invoke`: phase 4.
- Orphaned children if the app is force-killed (SIGKILL). The port-conflict guard catches the leftover server on next launch; `make down` cleans it up.

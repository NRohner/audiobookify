use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::thread::sleep;
use std::time::{Duration, Instant};

pub const API_ADDR: &str = "127.0.0.1:8000";

pub fn port_in_use(addr: &str) -> bool {
    let addr: SocketAddr = addr.parse().expect("valid socket address");
    // Connecting to a free ephemeral port can TCP self-connect (source port == destination port);
    // that is not a listener.
    TcpStream::connect_timeout(&addr, Duration::from_millis(200)).is_ok_and(|s| s.local_addr().ok() != Some(addr))
}

/// Waits for `addr` to stop accepting connections, e.g. an old instance's uvicorn still in graceful shutdown.
pub fn wait_port_free(addr: &str, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        if !port_in_use(addr) {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        sleep(Duration::from_millis(100));
    }
}

use audiobookify_core::audio::child_path;

/// The FastAPI app. Generation runs in-process (`jobs::Runner`), not as a sidecar.
pub fn audiobookify_commands(repo: &Path) -> Vec<Command> {
    let mut api = Command::new(repo.join(".venv/bin/uvicorn"));
    api.args(["audiobookify.api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000"]);
    // The repo root as cwd: create_app() defaults to ./audiobookify.db, ./uploads, ./voices.
    api.current_dir(repo).env("HF_HUB_OFFLINE", "1");
    api.env("PATH", child_path(std::env::var_os("PATH")));
    vec![api]
}

// Runs "$@" and ties it to stdin: when the app dies the kernel closes the pipe, `read` sees EOF,
// and the child gets SIGTERM, then SIGKILL after ~5 s. (Async lists get /dev/null as stdin, hence fd 3.)
// The child gets /dev/null as stdin (not a closed fd 0, which the next open() would take over) and no fd 3.
// A non-interactive sh starts async children with SIGINT/SIGQUIT ignored (inherited across exec), so
// Ctrl+C in the terminal no longer reaches the child directly; cleanup still happens through the pipe.
const WRAPPER: &str = r#"exec 3<&0
"$@" </dev/null 3<&- & c=$!
{ read _ <&3; kill -TERM $c 2>/dev/null; i=0; while kill -0 $c 2>/dev/null && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done; kill -KILL $c 2>/dev/null; } &
w=$!
wait $c; s=$?
kill $w 2>/dev/null
exit $s"#;

fn wrap(cmd: &Command) -> Command {
    let mut w = Command::new("/bin/sh");
    w.args(["-c", WRAPPER, "sh"]).arg(cmd.get_program()).args(cmd.get_args());
    for (k, v) in cmd.get_envs() {
        match v {
            Some(v) => w.env(k, v),
            None => w.env_remove(k),
        };
    }
    if let Some(dir) = cmd.get_current_dir() {
        w.current_dir(dir);
    }
    w.stdin(Stdio::piped());
    w
}

pub struct Sidecars(Vec<Child>);

impl Sidecars {
    pub fn start(cmds: Vec<Command>) -> std::io::Result<Sidecars> {
        let mut sc = Sidecars(Vec::new());
        for c in cmds {
            sc.0.push(wrap(&c).spawn()?); // on error, Drop kills the ones already started
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

    pub fn shutdown(self) {} // Drop does the work
}

impl Drop for Sidecars {
    fn drop(&mut self) {
        // ponytail: closing stdin makes each wrapper SIGTERM its child, then SIGKILL after ~5 s.
        // Do not kill the wrapper itself: that would orphan the real child.
        // Close every stdin first so the children shut down in parallel, then reap them.
        for child in &mut self.0 {
            drop(child.stdin.take());
        }
        for child in &mut self.0 {
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::time::Instant;

    // Tests that bind/probe ephemeral ports run one at a time: a port freed by one test can be
    // rebound by another mid-assertion (this made port_in_use_detects_listener flaky).
    static PORTS: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn ports() -> std::sync::MutexGuard<'static, ()> {
        PORTS.lock().unwrap_or_else(|e| e.into_inner())
    }

    // Port 1 is privileged and outside the ephemeral range, so nothing listens there and connects are
    // refused at once. Unlike a port picked by binding and dropping a listener, no other process can grab it.
    const NOTHING_LISTENS: &str = "127.0.0.1:1";

    fn sleeper() -> Command {
        let mut c = Command::new("sleep");
        c.arg("30");
        c
    }

    fn alive(pid: u32) -> bool {
        Command::new("kill").args(["-0", &pid.to_string()]).stderr(Stdio::null()).status().unwrap().success()
    }

    #[test]
    fn port_in_use_detects_listener() {
        let _g = ports();
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        assert!(port_in_use(&addr));
        drop(l);
        assert!(wait_port_free(&addr, Duration::from_secs(2)));
    }

    #[test]
    fn wait_ready_returns_once_port_listens() {
        let _g = ports();
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        let mut sc = Sidecars::start(vec![sleeper()]).unwrap();
        assert_eq!(sc.wait_ready(&addr, Duration::from_secs(5)), Ok(()));
        sc.shutdown();
    }

    #[test]
    fn wait_ready_fails_fast_when_a_child_exits() {
        let _g = ports();
        let mut sc = Sidecars::start(vec![Command::new("false")]).unwrap();
        let started = Instant::now();
        let err = sc.wait_ready(NOTHING_LISTENS, Duration::from_secs(10)).unwrap_err();
        assert!(err.contains("exited"), "unexpected error: {err}");
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn wait_ready_times_out() {
        let _g = ports();
        let mut sc = Sidecars::start(vec![sleeper()]).unwrap();
        let err = sc.wait_ready(NOTHING_LISTENS, Duration::from_millis(300)).unwrap_err();
        assert!(err.contains("timed out"), "unexpected error: {err}");
    }

    fn tmp_file(tag: &str) -> std::path::PathBuf {
        use std::sync::atomic::{AtomicU32, Ordering};
        static N: AtomicU32 = AtomicU32::new(0);
        let f = std::env::temp_dir().join(format!("sidecars-{tag}-{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
        let _ = std::fs::remove_file(&f);
        f
    }

    /// A command whose REAL process (not the wrapper) writes its pid to `f`, then runs `body`.
    fn pid_writer(f: &Path, body: &str) -> Command {
        let mut c = Command::new("sh");
        c.args(["-c", &format!("echo $$ > {}; {body}", f.display())]);
        c
    }

    fn read_pid(f: &Path) -> u32 {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Some(p) = std::fs::read_to_string(f).ok().and_then(|s| s.trim().parse().ok()) {
                return p;
            }
            assert!(Instant::now() < deadline, "no pid in {f:?}");
            sleep(Duration::from_millis(20));
        }
    }

    fn dies_within(pid: u32, limit: Duration) -> bool {
        let deadline = Instant::now() + limit;
        while alive(pid) {
            if Instant::now() >= deadline {
                return false;
            }
            sleep(Duration::from_millis(50));
        }
        true
    }

    fn real_sleeper(f: &Path) -> Command {
        pid_writer(f, "exec sleep 30")
    }

    #[test]
    fn shutdown_kills_the_real_children() {
        let (f1, f2) = (tmp_file("a"), tmp_file("b"));
        let sc = Sidecars::start(vec![real_sleeper(&f1), real_sleeper(&f2)]).unwrap();
        let pids = [read_pid(&f1), read_pid(&f2)];
        assert!(pids.iter().all(|p| alive(*p)));
        sc.shutdown();
        assert!(pids.iter().all(|p| !alive(*p)));
    }

    #[test]
    fn drop_kills_the_real_child() {
        let f = tmp_file("drop");
        let sc = Sidecars::start(vec![real_sleeper(&f)]).unwrap();
        let pid = read_pid(&f);
        drop(sc);
        assert!(!alive(pid));
    }

    #[test]
    fn child_dies_when_the_parent_pipe_closes() {
        // Simulates the app being SIGKILLed: the kernel closes the pipe, no Drop runs.
        let f = tmp_file("orphan");
        let mut sc = Sidecars::start(vec![real_sleeper(&f)]).unwrap();
        let pid = read_pid(&f);
        drop(sc.0[0].stdin.take().unwrap());
        assert!(dies_within(pid, Duration::from_secs(2)), "real child {pid} outlived the pipe");
    }

    #[test]
    fn drop_escalates_to_sigkill_for_a_child_that_ignores_sigterm() {
        let f = tmp_file("stubborn");
        let sc = Sidecars::start(vec![pid_writer(&f, "trap '' TERM; while :; do sleep 1; done")]).unwrap();
        let pid = read_pid(&f);
        sleep(Duration::from_millis(300)); // let the trap install
        let started = Instant::now();
        drop(sc);
        assert!(started.elapsed() < Duration::from_secs(7), "drop took {:?}", started.elapsed());
        assert!(dies_within(pid, Duration::from_secs(1)));
    }

    #[test]
    fn wrapping_preserves_env_and_cwd() {
        let dir = std::fs::canonicalize(std::env::temp_dir()).unwrap();
        let f = tmp_file("env");
        let mut c = Command::new("sh");
        c.args(["-c", &format!("echo \"$HF_HUB_OFFLINE $(pwd -P)\" > {}", f.display())]);
        c.env("HF_HUB_OFFLINE", "1").current_dir(&dir);
        let sc = Sidecars::start(vec![c]).unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while std::fs::read_to_string(&f).map_or(true, |s| s.is_empty()) {
            assert!(Instant::now() < deadline, "child never wrote {f:?}");
            sleep(Duration::from_millis(20));
        }
        assert_eq!(std::fs::read_to_string(&f).unwrap().trim(), format!("1 {}", dir.display()));
        sc.shutdown();
    }

    #[test]
    fn wrapped_child_has_dev_null_stdin_and_no_fd3() {
        let f = tmp_file("fds");
        let script = format!(
            "import os,stat\ntry:\n s=os.fstat(0); a='ok' if stat.S_ISCHR(s.st_mode) and s.st_rdev==os.stat('/dev/null').st_rdev else 'bad'\nexcept OSError: a='bad'\ntry:\n os.fstat(3); b='open'\nexcept OSError: b='closed'\nopen('{}','w').write(a+' '+b)",
            f.display()
        );
        let mut c = Command::new("python3");
        c.args(["-c", &script]);
        let sc = Sidecars::start(vec![c]).unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while std::fs::read_to_string(&f).map_or(true, |s| s.is_empty()) {
            assert!(Instant::now() < deadline, "child never wrote {f:?}");
            sleep(Duration::from_millis(20));
        }
        assert_eq!(std::fs::read_to_string(&f).unwrap(), "ok closed");
        sc.shutdown();
    }

    #[test]
    fn drop_shuts_children_down_in_parallel() {
        let (f1, f2) = (tmp_file("p1"), tmp_file("p2"));
        let stubborn = "trap '' TERM; while :; do sleep 1; done";
        let sc = Sidecars::start(vec![pid_writer(&f1, stubborn), pid_writer(&f2, stubborn)]).unwrap();
        let pids = [read_pid(&f1), read_pid(&f2)];
        sleep(Duration::from_millis(300)); // let the traps install
        let started = Instant::now();
        drop(sc);
        assert!(started.elapsed() < Duration::from_secs(7), "drop took {:?}", started.elapsed());
        assert!(pids.iter().all(|p| dies_within(*p, Duration::from_secs(1))));
    }

    #[test]
    fn wait_port_free_true_immediately_for_free_port() {
        let _g = ports();
        let started = Instant::now();
        assert!(wait_port_free(NOTHING_LISTENS, Duration::from_secs(2)));
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn wait_port_free_false_for_held_port() {
        let _g = ports();
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        let started = Instant::now();
        assert!(!wait_port_free(&addr, Duration::from_millis(500)));
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn wait_port_free_true_once_listener_drops() {
        let _g = ports();
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        let t = std::thread::spawn(move || {
            sleep(Duration::from_millis(300));
            drop(l);
        });
        assert!(wait_port_free(&addr, Duration::from_secs(3)));
        t.join().unwrap();
    }

    #[test]
    fn audiobookify_commands_start_only_the_api() {
        let repo = Path::new("/repo");
        let cmds = audiobookify_commands(repo);
        assert_eq!(cmds.len(), 1, "the job runner is Rust now; no Python worker sidecar");
        assert_eq!(cmds[0].get_program(), "/repo/.venv/bin/uvicorn");
        assert_eq!(cmds[0].get_current_dir(), Some(repo));
        let args: Vec<_> = cmds[0].get_args().map(|a| a.to_str().unwrap()).collect();
        assert_eq!(args, ["audiobookify.api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000"]);
        let env = |k: &str| cmds[0].get_envs().find(|(n, _)| *n == k).and_then(|(_, v)| v).map(|v| v.to_str().unwrap().to_string());
        assert_eq!(env("HF_HUB_OFFLINE").as_deref(), Some("1"));
        assert!(env("PATH").unwrap().starts_with("/opt/homebrew/bin:/usr/local/bin"));
    }
}

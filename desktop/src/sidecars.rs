use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::process::{Child, Command, Stdio};
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

// Runs "$@" and ties it to stdin: when the app dies the kernel closes the pipe, `read` sees EOF,
// and the child gets SIGTERM, then SIGKILL after ~5 s. (Async lists get /dev/null as stdin, hence fd 3.)
const WRAPPER: &str = r#"exec 3<&0
"$@" <&- & c=$!
{ read _ <&3; kill -TERM $c 2>/dev/null; i=0; while kill -0 $c 2>/dev/null && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done; kill -KILL $c 2>/dev/null; } &
w=$!
wait $c; s=$?
kill $w 2>/dev/null
exit $s"#;

fn wrap(cmd: &Command) -> Command {
    let mut w = Command::new("sh");
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
        // Do not kill the wrapper itself: that would orphan the real child. The worker resets
        // stale jobs on startup and the chunk cache resumes, so a forced kill is safe.
        for child in &mut self.0 {
            drop(child.stdin.take());
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
        Command::new("kill").args(["-0", &pid.to_string()]).stderr(Stdio::null()).status().unwrap().success()
    }

    #[test]
    fn port_in_use_detects_listener() {
        let _g = ports();
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        assert!(port_in_use(&addr));
        drop(l);
        assert!(!port_in_use(&addr));
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
        let err = sc.wait_ready(&free_addr(), Duration::from_secs(10)).unwrap_err();
        assert!(err.contains("exited"), "unexpected error: {err}");
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn wait_ready_times_out() {
        let _g = ports();
        let mut sc = Sidecars::start(vec![sleeper()]).unwrap();
        let err = sc.wait_ready(&free_addr(), Duration::from_millis(300)).unwrap_err();
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

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

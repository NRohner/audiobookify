"""Model files in the Hugging Face cache, for the desktop app's Settings page.

A one-shot process: `sizes REPO...`, `delete REPO...` or `download JSON`. Replies are
JSON lines on stdout (logging goes to stderr). The cache is shared with other projects,
so it only ever touches the repos it is given; the Rust registry decides which.
A download exits when stdin closes, so it dies with the app.
"""
import fnmatch
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path


def _cache_root() -> Path:
    from huggingface_hub import constants
    # constants is read at import; re-read the env so tests (and HF_HOME changes) apply.
    return Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME") or constants.HF_HOME) / "hub")


def _repo_dir(repo: str) -> Path:
    return _cache_root() / ("models--" + repo.replace("/", "--"))


def _scan():
    from huggingface_hub import scan_cache_dir
    from huggingface_hub.errors import CacheNotFound
    try:
        return scan_cache_dir(_cache_root())
    except CacheNotFound:
        return None


def sizes(repos: list[str]) -> dict[str, int]:
    info = _scan()
    cached = {r.repo_id: r.size_on_disk for r in info.repos if r.repo_type == "model"} if info else {}
    return {repo: cached.get(repo, 0) for repo in repos}


def delete(repos: list[str]) -> int:
    info = _scan()
    if info is None:
        return 0
    hashes = [rev.commit_hash for r in info.repos if r.repo_type == "model" and r.repo_id in repos
              for rev in r.revisions]
    if not hashes:
        return 0
    strategy = info.delete_revisions(*hashes)
    strategy.execute()
    return strategy.expected_freed_size


def blob_bytes(repo_dir: Path) -> int:
    """Bytes in a repo's blob store, partial (.incomplete) downloads included."""
    blobs = repo_dir / "blobs"
    return sum(f.stat().st_size for f in blobs.iterdir() if f.is_file()) if blobs.is_dir() else 0


def _reply(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _fake_download(items: list[dict]) -> None:
    if os.environ.get("AUDIOBOOKIFY_FAKE_DOWNLOAD") == "error":
        raise RuntimeError("fake download failed")
    delay = float(os.environ.get("AUDIOBOOKIFY_FAKE_DOWNLOAD_DELAY") or 0)
    total = 1024 * sum(len(i["required"]) for i in items)
    done = 0
    for item in items:
        root = _repo_dir(item["repo"])
        commit = hashlib.sha1(item["repo"].encode()).hexdigest()
        for name in item["required"]:
            time.sleep(delay)
            blob = root / "blobs" / hashlib.sha256(f"{item['repo']}/{name}".encode()).hexdigest()
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(b"x" * 1024)
            link = root / "snapshots" / commit / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.unlink(missing_ok=True)
            link.symlink_to(blob)
            done += 1024
            _reply({"downloaded": done, "total": total})
        (root / "refs").mkdir(parents=True, exist_ok=True)
        (root / "refs" / "main").write_text(commit)


def _real_download(items: list[dict]) -> None:
    from huggingface_hub import HfApi, snapshot_download
    api = HfApi()
    total = 0
    for item in items:
        siblings = api.model_info(item["repo"], files_metadata=True).siblings or []
        total += sum(s.size or 0 for s in siblings
                     if any(fnmatch.fnmatch(s.rfilename, p) for p in item["allow_patterns"]))
    dirs = [_repo_dir(i["repo"]) for i in items]
    stop = threading.Event()

    # ponytail: progress is the blob store's size on disk, polled; independent of HF's tqdm
    # internals. Coarse if a backend only writes each file at the end.
    def report():
        while not stop.wait(0.5):
            _reply({"downloaded": min(sum(blob_bytes(d) for d in dirs), total), "total": total})

    threading.Thread(target=report, daemon=True).start()
    try:
        for item in items:
            snapshot_download(item["repo"], allow_patterns=item["allow_patterns"])
    finally:
        stop.set()


def _exit_on_stdin_eof() -> None:
    def watch():
        sys.stdin.read()
        os._exit(1)
    threading.Thread(target=watch, daemon=True).start()


def main(argv: list[str]) -> int:
    command, args = argv[0], argv[1:]
    if command == "sizes":
        _reply({"sizes": sizes(args)})
        return 0
    if command == "delete":
        _reply({"freed": delete(args)})
        return 0
    if command == "download":
        _exit_on_stdin_eof()
        items = json.loads(args[0])
        try:
            (_fake_download if os.environ.get("AUDIOBOOKIFY_FAKE_DOWNLOAD") else _real_download)(items)
        except Exception as exc:
            _reply({"error": str(exc) or type(exc).__name__})
            return 1
        _reply({"done": True})
        return 0
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Model files in the Hugging Face cache, for the desktop app's Settings page.

A one-shot process: `sizes REPO...`, `delete REPO...` or `download JSON`. Replies are
JSON lines on stdout (logging goes to stderr). The cache is shared with other projects,
so it only ever touches the repos it is given; the Rust registry decides which.
A download exits when stdin closes, so it dies with the app.
"""
import dataclasses
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
    return Path(os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
                or Path(os.environ.get("HF_HOME") or constants.HF_HOME) / "hub")


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
    """Delete the named model repos; return the bytes actually freed.

    HF's delete_revisions maps a commit hash to the first scanned repo holding it, which
    could be a repo we were not asked to touch, so each strategy is built from a view of the
    cache holding only that one repo. The view keeps cache_dir, so execute() also sweeps the
    repo's payloads from the cache-wide shared blob store (Xet downloads), each only once no
    other repo still references it.
    """
    info = _scan()
    if info is None:
        return 0
    freed = 0
    for repo in info.repos:
        if repo.repo_type == "model" and repo.repo_id in repos:
            # Every revision goes, so the strategy removes the whole repo dir.
            strategy = dataclasses.replace(info, repos=frozenset({repo})).delete_revisions(
                *(rev.commit_hash for rev in repo.revisions))
            freed += strategy.expected_freed_size
            strategy.execute()
    return freed


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


def _byte_bar_class(bars: list):
    """A silent tqdm class for snapshot_download that collects its "Reconstructing" bars.

    That bar sums the bytes written to disk across the repo's files (snapshot_download feeds
    it from every per-file download, Xet or HTTP). tqdm only counts while enabled, so the bars
    stay enabled and draw to os.devnull: our stdout is the JSON protocol.
    """
    from tqdm.auto import tqdm

    class ByteBar(tqdm):
        monitor_interval = 0  # no tqdm monitor thread

        def __init__(self, *args, **kwargs):
            kwargs.update(disable=False, file=open(os.devnull, "w"))
            super().__init__(*args, **kwargs)
            if str(kwargs.get("desc") or "").startswith("Reconstructing"):
                bars.append(self)

        def close(self):
            file = self.fp
            super().close()
            file.close()

    return ByteBar


def _real_download(items: list[dict]) -> None:
    from huggingface_hub import HfApi, snapshot_download
    api = HfApi()
    totals = []
    for item in items:
        siblings = api.model_info(item["repo"], files_metadata=True).siblings or []
        totals.append(sum(s.size or 0 for s in siblings
                          if any(fnmatch.fnmatch(s.rfilename, p) for p in item["allow_patterns"])))
    total = sum(totals)
    done = 0  # bytes of the repos already finished
    bars: list = []  # this repo's "Reconstructing" bar(s)
    lock = threading.Lock()  # done and bars change together between repos
    stop = threading.Event()

    def report():
        last = None
        while not stop.wait(0.25):
            with lock:
                downloaded = min(done + sum(int(b.n) for b in bars), total)
            if downloaded != last:
                _reply({"downloaded": downloaded, "total": total})
                last = downloaded

    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    try:
        for item, repo_total in zip(items, totals):
            snapshot_download(item["repo"], allow_patterns=item["allow_patterns"], cache_dir=_cache_root(),
                              tqdm_class=_byte_bar_class(bars))
            with lock:
                done += repo_total
                bars.clear()
    finally:
        stop.set()
        reporter.join()


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

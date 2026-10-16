"""model_store: sizes, delete and the fake download, against a hand-built HF cache."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from audiobookify import model_store


def cache_repo(cache: Path, repo: str, files: dict[str, int], commit: str = "abc123") -> Path:
    """The HF cache layout: blobs, a snapshot of symlinks into them, refs/main."""
    root = cache / ("models--" + repo.replace("/", "--"))
    for i, (name, size) in enumerate(files.items()):
        blob = root / "blobs" / f"{commit}{i:04d}"
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(b"x" * size)
        link = root / "snapshots" / commit / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(blob)
    (root / "refs").mkdir(parents=True, exist_ok=True)
    (root / "refs" / "main").write_text(commit)
    return root


@pytest.fixture
def cache(tmp_path, monkeypatch):
    c = tmp_path / "hub"
    c.mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(c))
    return c


def test_sizes_reports_cached_repos_and_zero_for_missing(cache):
    cache_repo(cache, "org/a", {"config.json": 10, "model.safetensors": 1000})
    assert model_store.sizes(["org/a", "org/missing"]) == {"org/a": 1010, "org/missing": 0}


def test_sizes_with_no_cache_dir_is_all_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "nope"))
    assert model_store.sizes(["org/a"]) == {"org/a": 0}


def test_delete_removes_only_the_named_repos(cache):
    cache_repo(cache, "org/a", {"model.safetensors": 1000})
    other = cache_repo(cache, "someone/else", {"model.safetensors": 500}, commit="def456")
    assert model_store.delete(["org/a"]) == 1000
    assert model_store.sizes(["org/a", "someone/else"]) == {"org/a": 0, "someone/else": 500}
    assert (other / "snapshots" / "def456" / "model.safetensors").read_bytes() == b"x" * 500


def test_delete_spares_a_foreign_repo_sharing_the_commit_hash(cache):
    other = cache_repo(cache, "aaa/foreign", {"model.safetensors": 500})  # scanned first, same hash
    cache_repo(cache, "org/a", {"model.safetensors": 1000})
    assert model_store.delete(["org/a"]) == 1000
    assert model_store.sizes(["org/a", "aaa/foreign"]) == {"org/a": 0, "aaa/foreign": 500}
    assert (other / "snapshots" / "abc123" / "model.safetensors").read_bytes() == b"x" * 500


def test_delete_of_a_missing_repo_frees_nothing(cache):
    assert model_store.delete(["org/missing"]) == 0


XET_HASH = "ab" + "c" * 62
PAYLOAD = 3000


def cache_shared_repo(cache: Path, repo: str, commit: str, payload: bytes = b"w" * PAYLOAD) -> Path:
    """A repo as HF 2.2 leaves it after a Xet download: config.json a regular blob, the weights
    published to the cache-wide store at <cache>/blobs/ab/<xet hash> and symlinked from blobs/."""
    from huggingface_hub.utils._shared_blobs import publish_blob_to_shared_store

    root = cache_repo(cache, repo, {"config.json": 10}, commit=commit)
    blob = root / "blobs" / f"etag-{commit}"
    blob.write_bytes(payload)  # file_download writes the blob, then publishes it
    assert publish_blob_to_shared_store(blob_path=str(blob), xet_hash=XET_HASH, cache_dir=cache,
                                        expected_size=len(payload))
    assert blob.is_symlink()
    (root / "snapshots" / commit / "model.safetensors").symlink_to(blob)
    return root


def shared_payload(cache: Path) -> Path:
    return cache / "blobs" / XET_HASH[:2] / XET_HASH


def test_delete_frees_the_shared_store_payload(cache):
    root = cache_shared_repo(cache, "org/a", "abc123")
    assert shared_payload(cache).stat().st_size == PAYLOAD
    assert model_store.delete(["org/a"]) == PAYLOAD + 10
    assert not shared_payload(cache).exists()
    assert not root.exists()


def test_delete_keeps_a_shared_payload_a_foreign_repo_still_uses(cache):
    other = cache_shared_repo(cache, "aaa/foreign", "abc123")  # scanned first, same hash and payload
    root = cache_shared_repo(cache, "org/a", "abc123")
    assert model_store.delete(["org/a"]) == 10  # only our regular blob was freed
    assert not root.exists()
    assert shared_payload(cache).stat().st_size == PAYLOAD
    assert (other / "snapshots" / "abc123" / "model.safetensors").read_bytes() == b"w" * PAYLOAD
    assert model_store.sizes(["aaa/foreign"]) == {"aaa/foreign": PAYLOAD + 10}


def test_sizes_counts_a_shared_store_blob(cache):
    cache_shared_repo(cache, "org/a", "abc123")
    assert model_store.sizes(["org/a"]) == {"org/a": PAYLOAD + 10}


def run_cli(*args, env=None):
    # stdin stays open until the child exits: a download exits on stdin EOF by design.
    p = subprocess.Popen([sys.executable, "-m", "audiobookify.model_store", *args],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                         env={**os.environ, **(env or {})})
    out = p.stdout.read()
    p.wait(timeout=60)
    p.stdin.close()
    return subprocess.CompletedProcess(p.args, p.returncode, out, None)


def test_fake_download_writes_required_files_and_reports_done(cache):
    items = [{"repo": "org/a", "allow_patterns": ["*"], "required": ["config.json", "sub/model.safetensors"]}]
    out = run_cli("download", json.dumps(items), env={"AUDIOBOOKIFY_FAKE_DOWNLOAD": "1"})
    lines = [json.loads(line) for line in out.stdout.splitlines()]
    assert lines[-1] == {"done": True}
    assert {"downloaded": 2048, "total": 2048} in lines
    snap = cache / "models--org--a" / "snapshots"
    commit = (cache / "models--org--a" / "refs" / "main").read_text()
    assert (snap / commit / "sub" / "model.safetensors").stat().st_size == 1024


def test_fake_download_error(cache):
    items = [{"repo": "org/a", "allow_patterns": ["*"], "required": ["config.json"]}]
    out = run_cli("download", json.dumps(items), env={"AUDIOBOOKIFY_FAKE_DOWNLOAD": "error"})
    assert json.loads(out.stdout.splitlines()[-1]) == {"error": "fake download failed"}


def test_sizes_cli_prints_one_json_line(cache):
    cache_repo(cache, "org/a", {"model.safetensors": 7})
    out = run_cli("sizes", "org/a")
    assert json.loads(out.stdout) == {"sizes": {"org/a": 7}}


def test_download_exits_when_stdin_closes(cache):
    items = [{"repo": "org/a", "allow_patterns": ["*"], "required": ["a", "b", "c"]}]
    p = subprocess.Popen([sys.executable, "-m", "audiobookify.model_store", "download", json.dumps(items)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                         env={**os.environ, "AUDIOBOOKIFY_FAKE_DOWNLOAD": "1", "AUDIOBOOKIFY_FAKE_DOWNLOAD_DELAY": "30"})
    p.stdin.close()
    assert p.wait(timeout=10) != 0


def test_real_download_reports_reconstructed_bytes(cache, monkeypatch, capsys):
    import time
    from types import SimpleNamespace

    import huggingface_hub

    siblings = [SimpleNamespace(rfilename="config.json", size=10),
                SimpleNamespace(rfilename="model.safetensors", size=1000),
                SimpleNamespace(rfilename="readme.md", size=5)]
    calls = []

    class FakeApi:
        def model_info(self, repo, files_metadata=False):
            return SimpleNamespace(siblings=siblings)

    def fake_snapshot_download(repo_id, allow_patterns=None, cache_dir=None, tqdm_class=None):
        # Drive tqdm_class the way HF 2.2 does: a transfer bar, a reconstruct bar fed by every
        # file's bytes written, and the per-file counting bar of hf_thread_map.
        calls.append((repo_id, cache_dir))
        transfer = tqdm_class(desc="Downloading bytes", total=0, initial=0, unit="B", unit_scale=True)
        reconstruct = tqdm_class(desc="Reconstructing (incomplete total...)", total=0, initial=0,
                                 unit="B", unit_scale=True)
        with tqdm_class(desc="Fetching 2 files", total=2) as files:
            reconstruct.total = 1010
            for _ in range(10):
                time.sleep(0.1)
                transfer.update(50)
                reconstruct.update(100)
            reconstruct.update(10)
            files.update(2)
        transfer.close()
        reconstruct.close()
        return str(cache)

    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr(model_store, "_exit_on_stdin_eof", lambda: None)

    items = [{"repo": "org/a", "allow_patterns": ["*.json", "*.safetensors"], "required": ["config.json"]},
             {"repo": "org/b", "allow_patterns": ["*.json", "*.safetensors"], "required": ["config.json"]}]
    assert model_store.main(["download", json.dumps(items)]) == 0

    assert calls == [("org/a", cache), ("org/b", cache)]
    captured = capsys.readouterr()
    assert captured.err == ""  # the bars draw nowhere
    lines = [json.loads(line) for line in captured.out.splitlines()]
    assert lines[-1] == {"done": True}
    progress = [line for line in lines if "downloaded" in line]
    assert all(line["total"] == 2020 for line in progress)  # readme.md excluded, both repos
    done = [line["downloaded"] for line in progress]
    assert len(done) >= 4, done
    assert done == sorted(done) and len(set(done)) == len(done), done  # rising, no repeats
    assert any(0 < d < 1010 for d in done) and any(1010 < d < 2020 for d in done), done

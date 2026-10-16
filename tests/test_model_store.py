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


def test_delete_of_a_missing_repo_frees_nothing(cache):
    assert model_store.delete(["org/missing"]) == 0


def test_blob_bytes_counts_partial_downloads(cache):
    root = cache_repo(cache, "org/a", {"model.safetensors": 100})
    (root / "blobs" / "zzz.incomplete").write_bytes(b"x" * 50)
    assert model_store.blob_bytes(root) == 150


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

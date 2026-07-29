import shutil

import pytest
from fastapi.testclient import TestClient

from audiobookify.api.app import create_app
from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.models import JobStatus
from audiobookify.worker import run_worker_once

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"), upload_dir=str(tmp_path / "uploads"))
    return TestClient(app)


def test_create_book_ingests_uploaded_markdown_and_returns_chapters(tmp_path):
    client = _client(tmp_path)
    md_bytes = b"# Chapter One\nFirst chapter body.\n\n# Chapter Two\nSecond chapter body.\n"

    response = client.post(
        "/api/books",
        files={"file": ("book.md", md_bytes, "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "book"
    assert len(body["chapters"]) == 2
    assert body["chapters"][0]["title"] == "Chapter One"
    assert body["chapters"][0]["status"] == "pending"
    assert body["chapters"][0]["progress"] is None


def test_create_book_rejects_unsupported_format(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/api/books",
        files={"file": ("book.txt", b"hello", "text/plain")},
        data={"output_dir": str(tmp_path / "out")},
    )

    assert response.status_code == 400
    assert "unsupported input format" in response.json()["detail"]


def test_create_book_creates_a_new_book_row_for_each_upload_even_with_the_same_filename(tmp_path):
    client = _client(tmp_path)
    md_bytes = b"# Ch\nBody.\n"

    first = client.post("/api/books", files={"file": ("book.md", md_bytes, "text/markdown")},
                         data={"output_dir": str(tmp_path / "out")})
    second = client.post("/api/books", files={"file": ("book.md", md_bytes, "text/markdown")},
                          data={"output_dir": str(tmp_path / "out")})

    assert first.json()["id"] != second.json()["id"]


def test_list_books_returns_all_imported_books(tmp_path):
    client = _client(tmp_path)
    client.post("/api/books", files={"file": ("a.md", b"# Ch\nBody.\n", "text/markdown")},
                 data={"output_dir": str(tmp_path / "out")})
    client.post("/api/books", files={"file": ("b.md", b"# Ch\nBody.\n", "text/markdown")},
                 data={"output_dir": str(tmp_path / "out")})

    response = client.get("/api/books")

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_get_book_returns_404_for_unknown_id(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/books/999")

    assert response.status_code == 404


def test_generate_endpoint_creates_queued_jobs_for_all_chapters(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n\n# Two\nB.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()

    response = client.post(f"/api/books/{created['id']}/generate")

    assert response.status_code == 200
    assert len(response.json()["queued_job_ids"]) == 2

    db = Database(str(tmp_path / "test.db"))
    for chapter in db.list_chapters(created["id"]):
        assert db.latest_job_for_chapter(chapter.id).status == JobStatus.QUEUED
    db.close()


def test_generate_endpoint_does_not_duplicate_jobs_for_already_queued_chapters(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()

    client.post(f"/api/books/{created['id']}/generate")
    second = client.post(f"/api/books/{created['id']}/generate")

    assert second.json()["queued_job_ids"] == []


def test_generate_endpoint_returns_404_for_unknown_book(tmp_path):
    client = _client(tmp_path)

    response = client.post("/api/books/999/generate")

    assert response.status_code == 404


@requires_ffmpeg
def test_book_detail_reflects_job_progress_after_worker_processes_it(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()
    client.post(f"/api/books/{created['id']}/generate")

    db = Database(str(tmp_path / "test.db"))
    engine = FakeEngine()
    engine.load("cpu")
    run_worker_once(db, engine, {})
    db.close()

    detail = client.get(f"/api/books/{created['id']}").json()
    assert detail["chapters"][0]["status"] == "done"
    assert detail["chapters"][0]["progress"] == 1.0

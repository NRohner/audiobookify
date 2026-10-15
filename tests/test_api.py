import io
import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from audiobookify.api.app import create_app
from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, JobStatus, SourceFormat
from audiobookify.worker import EngineSlot, run_worker_once


def _slot(engine):
    """A worker engine slot that always hands out this engine."""
    return EngineSlot(lambda name: engine)

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"), upload_dir=str(tmp_path / "uploads"))
    return TestClient(app)


def _clean_tone_wav_bytes(total_duration_s=15.0, sample_rate=22050, amplitude=0.6):
    """A tone followed by a silent tail - passes validate_reference_audio cleanly."""
    n_total = int(total_duration_s * sample_rate)
    n_silence = int(n_total * 0.1)
    n_tone = n_total - n_silence
    t = np.linspace(0, n_tone / sample_rate, n_tone, endpoint=False)
    tone = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    silence = np.zeros(n_silence, dtype=np.float32)
    samples = np.concatenate([tone, silence])
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    buf.seek(0)
    return buf.read()


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


def test_create_book_strips_whitespace_from_output_dir(tmp_path):
    client = _client(tmp_path)
    target = tmp_path / "out"

    response = client.post(
        "/api/books",
        files={"file": ("book.md", b"# Ch\nBody.\n", "text/markdown")},
        data={"output_dir": f"  {target}  "},
    )

    assert response.status_code == 200
    assert response.json()["output_dir"] == str(target.resolve())


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


@requires_ffmpeg
def test_generate_endpoint_does_not_requeue_already_done_chapters(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()
    client.post(f"/api/books/{created['id']}/generate")

    db = Database(str(tmp_path / "test.db"))
    engine = FakeEngine()
    engine.load("cpu")
    run_worker_once(db, _slot(engine))
    db.close()

    second = client.post(f"/api/books/{created['id']}/generate")

    assert second.json()["queued_job_ids"] == []


def test_generate_endpoint_returns_404_for_unknown_book(tmp_path):
    client = _client(tmp_path)

    response = client.post("/api/books/999/generate")

    assert response.status_code == 404


def test_generate_chapter_endpoint_queues_only_that_chapter(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n\n# Two\nB.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()
    chapter_ids = [c["id"] for c in created["chapters"]]

    response = client.post(f"/api/books/{created['id']}/chapters/{chapter_ids[0]}/generate")

    assert response.status_code == 200
    assert response.json()["job_id"] is not None

    db = Database(str(tmp_path / "test.db"))
    assert db.latest_job_for_chapter(chapter_ids[0]).status == JobStatus.QUEUED
    assert db.latest_job_for_chapter(chapter_ids[1]) is None
    db.close()


def test_generate_chapter_endpoint_does_not_requeue_an_already_queued_chapter(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()
    chapter_id = created["chapters"][0]["id"]
    client.post(f"/api/books/{created['id']}/chapters/{chapter_id}/generate")

    second = client.post(f"/api/books/{created['id']}/chapters/{chapter_id}/generate")

    assert second.status_code == 200
    assert second.json()["job_id"] is None


@requires_ffmpeg
def test_generate_chapter_endpoint_does_not_requeue_an_already_done_chapter(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()
    chapter_id = created["chapters"][0]["id"]
    client.post(f"/api/books/{created['id']}/chapters/{chapter_id}/generate")

    db = Database(str(tmp_path / "test.db"))
    engine = FakeEngine()
    engine.load("cpu")
    run_worker_once(db, _slot(engine))
    db.close()

    response = client.post(f"/api/books/{created['id']}/chapters/{chapter_id}/generate")

    assert response.json()["job_id"] is None


def test_generate_chapter_endpoint_returns_404_for_unknown_book(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()
    chapter_id = created["chapters"][0]["id"]

    response = client.post(f"/api/books/999/chapters/{chapter_id}/generate")

    assert response.status_code == 404


def test_generate_chapter_endpoint_returns_404_for_unknown_chapter(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()

    response = client.post(f"/api/books/{created['id']}/chapters/999/generate")

    assert response.status_code == 404


def test_generate_chapter_endpoint_returns_404_when_chapter_belongs_to_a_different_book(tmp_path):
    client = _client(tmp_path)
    book_a = client.post(
        "/api/books", files={"file": ("a.md", b"# One\nA.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()
    book_b = client.post(
        "/api/books", files={"file": ("b.md", b"# One\nA.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()
    other_chapter_id = book_b["chapters"][0]["id"]

    response = client.post(f"/api/books/{book_a['id']}/chapters/{other_chapter_id}/generate")

    assert response.status_code == 404


def test_create_app_serves_built_frontend_index_when_dist_dir_provided(tmp_path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>Audiobookify</body></html>", encoding="utf-8")
    app = create_app(db_path=str(tmp_path / "test.db"), upload_dir=str(tmp_path / "uploads"),
                      frontend_dist=str(dist_dir))
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Audiobookify" in response.text


def test_create_app_returns_404_at_root_when_no_frontend_dist_configured(tmp_path):
    # Point frontend_dist at a directory that doesn't exist rather than relying on the
    # default (which resolves to the real repo's frontend/dist) - otherwise this test
    # would flake depending on whether `npm run build` has been run on this machine.
    app = create_app(db_path=str(tmp_path / "test.db"), upload_dir=str(tmp_path / "uploads"),
                      frontend_dist=str(tmp_path / "no-such-dist"))
    client = TestClient(app)

    response = client.get("/")

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
    run_worker_once(db, _slot(engine))
    db.close()

    detail = client.get(f"/api/books/{created['id']}").json()
    assert detail["chapters"][0]["status"] == "done"
    # progress is only surfaced for genuinely active (queued/generating) jobs; once a
    # chapter is done, `status` alone communicates completion and stale progress from
    # the finished job is no longer reported (see _book_to_dict in app.py).
    assert detail["chapters"][0]["progress"] is None


def test_create_voice_uploads_reference_audio_and_returns_profile(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/api/voices",
        files={"file": ("ref.wav", _clean_tone_wav_bytes(), "audio/wav")},
        data={"name": "narrator-1", "exaggeration": "0.5", "cfg_weight": "0.5", "temperature": "0.8"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "narrator-1"
    assert body["engine"] == "chatterbox-turbo"
    assert body["warnings"] == []


def test_create_voice_rejects_audio_too_short(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/api/voices",
        files={"file": ("ref.wav", _clean_tone_wav_bytes(total_duration_s=0.3), "audio/wav")},
        data={"name": "narrator-1"},
    )

    assert response.status_code == 400
    assert "too short" in response.json()["detail"]


def test_create_voice_rejects_duplicate_name(tmp_path):
    client = _client(tmp_path)
    client.post("/api/voices", files={"file": ("ref.wav", _clean_tone_wav_bytes(), "audio/wav")},
                data={"name": "dup"})

    response = client.post("/api/voices", files={"file": ("ref.wav", _clean_tone_wav_bytes(), "audio/wav")},
                            data={"name": "dup"})

    assert response.status_code == 400
    assert "dup" in response.json()["detail"]


def test_create_voice_rolls_back_the_profile_row_when_permanent_storage_fails(tmp_path, monkeypatch):
    import audiobookify.api.app as app_module

    def failing_store(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(app_module, "store_reference_audio", failing_store)
    client = _client(tmp_path)

    response = client.post(
        "/api/voices",
        files={"file": ("ref.wav", _clean_tone_wav_bytes(), "audio/wav")},
        data={"name": "narrator-1"},
    )

    assert response.status_code == 500

    # the row was rolled back - the name is free to retry, not stuck on a duplicate check
    db = Database(str(tmp_path / "test.db"))
    assert db.find_voice_profile_by_name("narrator-1") is None
    db.close()


def test_list_voices_returns_all_profiles(tmp_path):
    client = _client(tmp_path)
    client.post("/api/voices", files={"file": ("a.wav", _clean_tone_wav_bytes(), "audio/wav")},
                data={"name": "a"})
    client.post("/api/voices", files={"file": ("b.wav", _clean_tone_wav_bytes(), "audio/wav")},
                data={"name": "b"})

    response = client.get("/api/voices")

    assert response.status_code == 200
    assert [v["name"] for v in response.json()] == ["a", "b"]


def test_delete_voice_removes_profile(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/voices", files={"file": ("a.wav", _clean_tone_wav_bytes(), "audio/wav")},
                           data={"name": "a"}).json()

    response = client.delete(f"/api/voices/{created['id']}")

    assert response.status_code == 200
    assert client.get("/api/voices").json() == []


def test_delete_voice_returns_404_for_unknown_id(tmp_path):
    client = _client(tmp_path)

    response = client.delete("/api/voices/999")

    assert response.status_code == 404


def test_assign_voice_to_book_persists(tmp_path):
    client = _client(tmp_path)
    book = client.post("/api/books", files={"file": ("book.md", b"# One\nA.\n", "text/markdown")},
                        data={"output_dir": str(tmp_path / "out")}).json()
    voice = client.post("/api/voices", files={"file": ("a.wav", _clean_tone_wav_bytes(), "audio/wav")},
                         data={"name": "a"}).json()

    response = client.patch(f"/api/books/{book['id']}/voice", json={"voice_profile_id": voice["id"]})

    assert response.status_code == 200
    assert client.get(f"/api/books/{book['id']}").json()["voice_profile_id"] == voice["id"]


def test_assign_voice_to_book_returns_404_for_unknown_book(tmp_path):
    client = _client(tmp_path)

    response = client.patch("/api/books/999/voice", json={"voice_profile_id": None})

    assert response.status_code == 404


def test_list_pronunciations_returns_global_entries(tmp_path):
    client = _client(tmp_path)
    client.post("/api/pronunciations", json={"term": "Cadeyrn", "override_value": "KAH-dern"})

    response = client.get("/api/pronunciations")

    assert response.status_code == 200
    assert response.json()[0]["term"] == "Cadeyrn"


def test_create_pronunciation_rejects_duplicate_term(tmp_path):
    client = _client(tmp_path)
    client.post("/api/pronunciations", json={"term": "Cadeyrn", "override_value": "KAH-dern"})

    response = client.post("/api/pronunciations", json={"term": "Cadeyrn", "override_value": "kay-DERN"})

    assert response.status_code == 400
    assert "Cadeyrn" in response.json()["detail"]


def test_update_pronunciation_override_value(tmp_path):
    client = _client(tmp_path)
    client.post("/api/pronunciations", json={"term": "Cadeyrn", "override_value": "KAH-dern"})

    response = client.patch("/api/pronunciations/Cadeyrn", json={"override_value": "kay-DERN"})

    assert response.status_code == 200
    assert response.json()["override_value"] == "kay-DERN"


def test_update_pronunciation_returns_404_for_missing_term(tmp_path):
    client = _client(tmp_path)

    response = client.patch("/api/pronunciations/Nope", json={"override_value": "x"})

    assert response.status_code == 404


def test_delete_pronunciation_entry(tmp_path):
    client = _client(tmp_path)
    client.post("/api/pronunciations", json={"term": "Cadeyrn", "override_value": "KAH-dern"})

    response = client.delete("/api/pronunciations/Cadeyrn")

    assert response.status_code == 200
    assert client.get("/api/pronunciations").json() == []


def test_delete_pronunciation_returns_404_for_missing_term(tmp_path):
    client = _client(tmp_path)

    response = client.delete("/api/pronunciations/Nope")

    assert response.status_code == 404


def test_create_text_clip_queues_it_with_default_voice(tmp_path):
    client = _client(tmp_path)

    response = client.post("/api/text-clips", json={"text": "Hello there.", "voice_profile_id": None})

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Hello there."
    assert body["voice_profile_id"] is None
    assert body["status"] == "queued"
    assert body["progress"] == 0.0


def test_create_text_clip_rejects_empty_text(tmp_path):
    client = _client(tmp_path)

    response = client.post("/api/text-clips", json={"text": "   ", "voice_profile_id": None})

    assert response.status_code == 400
    assert "text must not be empty" in response.json()["detail"]


def test_create_text_clip_returns_404_for_unknown_voice_profile(tmp_path):
    client = _client(tmp_path)

    response = client.post("/api/text-clips", json={"text": "Hello there.", "voice_profile_id": 999})

    assert response.status_code == 404


def test_list_text_clips_returns_most_recent_first(tmp_path):
    client = _client(tmp_path)
    client.post("/api/text-clips", json={"text": "First", "voice_profile_id": None})
    client.post("/api/text-clips", json={"text": "Second", "voice_profile_id": None})

    response = client.get("/api/text-clips")

    assert response.status_code == 200
    assert [c["text"] for c in response.json()] == ["Second", "First"]


def test_get_text_clip_returns_404_for_unknown_id(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/text-clips/999")

    assert response.status_code == 404


def test_get_text_clip_audio_returns_404_before_generation_completes(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/text-clips", json={"text": "Hello", "voice_profile_id": None}).json()

    response = client.get(f"/api/text-clips/{created['id']}/audio")

    assert response.status_code == 404


@requires_ffmpeg
def test_worker_processes_queued_text_clip_and_audio_becomes_available(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/text-clips", json={"text": "Hello there.", "voice_profile_id": None}).json()

    db = Database(str(tmp_path / "test.db"))
    engine = FakeEngine()
    engine.load("cpu")
    run_worker_once(db, _slot(engine), text_clip_output_dir=str(tmp_path / "text_clips"))
    db.close()

    detail = client.get(f"/api/text-clips/{created['id']}").json()
    assert detail["status"] == "done"
    assert detail["progress"] == 1.0

    response = client.get(f"/api/text-clips/{created['id']}/audio")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"


def test_delete_text_clip_removes_it(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/text-clips", json={"text": "Hello", "voice_profile_id": None}).json()

    response = client.delete(f"/api/text-clips/{created['id']}")

    assert response.status_code == 200
    assert client.get("/api/text-clips").json() == []


def test_delete_text_clip_returns_404_for_unknown_id(tmp_path):
    client = _client(tmp_path)

    response = client.delete("/api/text-clips/999")

    assert response.status_code == 404


def test_browse_dir_lists_subdirectories_with_writability(tmp_path):
    client = _client(tmp_path)
    (tmp_path / "books").mkdir()
    (tmp_path / "locked").mkdir()
    os.chmod(tmp_path / "locked", 0o500)
    (tmp_path / "afile.txt").write_text("not a directory")

    try:
        response = client.get("/api/browse-dir", params={"path": str(tmp_path)})

        assert response.status_code == 200
        body = response.json()
        assert body["path"] == str(tmp_path.resolve())
        assert body["parent"] == str(tmp_path.resolve().parent)
        entries = {e["name"]: e["writable"] for e in body["entries"]}
        # Check that the key directories exist with correct writability
        assert entries["books"] is True
        assert entries["locked"] is False
        # Check that non-directory files are filtered out
        assert "afile.txt" not in entries
    finally:
        os.chmod(tmp_path / "locked", 0o700)


def test_browse_dir_returns_404_for_a_nonexistent_path(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/browse-dir", params={"path": str(tmp_path / "nope")})

    assert response.status_code == 404


def test_browse_dir_parent_is_none_at_filesystem_root(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/browse-dir", params={"path": "/"})

    assert response.status_code == 200
    assert response.json()["parent"] is None


def test_validate_output_dir_reports_writable_for_a_creatable_path(tmp_path):
    client = _client(tmp_path)
    new_dir = tmp_path / "brand-new-book-folder"

    response = client.get("/api/validate-output-dir", params={"path": str(new_dir)})

    assert response.status_code == 200
    body = response.json()
    assert body["writable"] is True
    assert body["error"] is None
    assert not new_dir.exists()  # validating must NOT create the directory


def test_validate_output_dir_does_not_create_intermediate_directories(tmp_path):
    client = _client(tmp_path)
    nested = tmp_path / "a" / "b" / "c"

    response = client.get("/api/validate-output-dir", params={"path": str(nested)})

    assert response.status_code == 200
    assert response.json()["writable"] is True
    assert not (tmp_path / "a").exists()


def test_validate_output_dir_reports_error_for_a_permission_denied_path(tmp_path):
    client = _client(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)

    try:
        response = client.get("/api/validate-output-dir", params={"path": str(locked)})

        assert response.status_code == 200
        body = response.json()
        assert body["writable"] is False
        assert body["error"] is not None
    finally:
        os.chmod(locked, 0o700)


def test_create_book_rejects_an_unwritable_output_dir(tmp_path):
    client = _client(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)

    try:
        response = client.post(
            "/api/books",
            files={"file": ("book.md", b"# Ch\nBody.\n", "text/markdown")},
            data={"output_dir": str(locked)},
        )

        assert response.status_code == 400
        assert "cannot write to" in response.json()["detail"]
    finally:
        os.chmod(locked, 0o700)

    db = Database(str(tmp_path / "test.db"))
    assert db.list_books() == []
    db.close()


def test_get_book_surfaces_used_fallback_per_chapter(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path="/backup/ch1.mp3",
                              used_fallback=True)
    db.close()

    response = client.get(f"/api/books/{book.id}")

    assert response.json()["chapters"][0]["used_fallback"] is True


def test_get_book_surfaces_started_at_for_a_generating_chapter(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    db.create_job(chapter.id)
    job = db.pop_next_queued_job()
    db.close()

    response = client.get(f"/api/books/{book.id}")

    assert response.json()["chapters"][0]["started_at"] == pytest.approx(job.started_at)


def test_get_text_clip_surfaces_used_fallback(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    clip = db.create_text_clip(text="Hi.", voice_profile_id=None)
    db.finish_text_clip(clip.id, JobStatus.DONE, output_path="/backup/clip.mp3", used_fallback=True)
    db.close()

    response = client.get(f"/api/text-clips/{clip.id}")

    assert response.json()["used_fallback"] is True


def test_get_text_clip_surfaces_started_at_while_generating(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    clip = db.create_text_clip(text="Hi.", voice_profile_id=None)
    clip = db.pop_next_queued_text_clip()
    db.close()

    response = client.get(f"/api/text-clips/{clip.id}")

    assert response.json()["started_at"] == pytest.approx(clip.started_at)


def test_worker_status_reports_not_paused_by_default(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/worker/status")

    assert response.status_code == 200
    assert response.json() == {"paused": False}


def test_worker_pause_then_status_reports_paused(tmp_path):
    client = _client(tmp_path)

    pause_response = client.post("/api/worker/pause")
    status_response = client.get("/api/worker/status")

    assert pause_response.json() == {"paused": True}
    assert status_response.json() == {"paused": True}


def test_worker_resume_after_pause_reports_not_paused(tmp_path):
    client = _client(tmp_path)
    client.post("/api/worker/pause")

    resume_response = client.post("/api/worker/resume")
    status_response = client.get("/api/worker/status")

    assert resume_response.json() == {"paused": False}
    assert status_response.json() == {"paused": False}


def test_cancel_chapter_with_a_queued_job_marks_it_cancelled_and_resets_the_chapter(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    job = db.create_job(chapter.id)
    db.close()

    response = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/cancel")

    assert response.status_code == 200
    db = Database(str(tmp_path / "test.db"))
    assert db.get_job(job.id).status == JobStatus.CANCELLED
    assert db.get_chapter(chapter.id).status == ChapterStatus.PENDING
    db.close()


def test_cancel_chapter_with_a_generating_job_sets_cancel_requested_without_changing_status_yet(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    job = db.create_job(chapter.id)
    db.pop_next_queued_job()  # -> generating, simulating a worker mid-chapter
    db.close()

    response = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/cancel")

    assert response.status_code == 200
    db = Database(str(tmp_path / "test.db"))
    assert db.is_job_cancel_requested(job.id) is True
    assert db.get_job(job.id).status == JobStatus.GENERATING  # not yet transitioned - worker's job
    db.close()


def test_cancel_chapter_with_no_active_job_is_a_noop(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    db.close()

    response = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/cancel")

    assert response.status_code == 200


def test_get_book_does_not_report_stale_progress_after_cancelling_a_queued_job(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    db.create_job(chapter.id)
    db.close()

    cancel_response = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/cancel")
    assert cancel_response.status_code == 200

    response = client.get(f"/api/books/{book.id}")

    chapter_dict = response.json()["chapters"][0]
    assert chapter_dict["status"] == "pending"
    assert chapter_dict["progress"] is None


def test_get_book_still_reports_progress_for_a_genuinely_queued_job(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    db.create_job(chapter.id)
    db.close()

    response = client.get(f"/api/books/{book.id}")

    chapter_dict = response.json()["chapters"][0]
    assert chapter_dict["status"] == "pending"
    assert chapter_dict["progress"] == 0.0


def test_cancel_chapter_clears_the_cache_for_a_paused_then_cancelled_job(tmp_path):
    import audiobookify.synth.chunk_cache as chunk_cache

    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    job = db.create_job(chapter.id)
    # Simulate a chunk already cached from a paused attempt (the reachable scenario:
    # the worker requeues a paused job to QUEUED while deliberately keeping its cache).
    chunk_cache.save_chunk(chapter.id, 0, __import__("numpy").zeros(10, dtype="float32"), sample_rate=22050)
    chunk_cache.write_manifest(chapter.id, "irrelevant-key-for-this-test")
    db.close()

    response = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/cancel")

    assert response.status_code == 200
    assert not chunk_cache.chapter_cache_dir(chapter.id).exists()


def test_cancel_chapter_is_a_noop_for_an_already_cancelled_job(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    job = db.create_job(chapter.id)
    db.finish_job(job.id, JobStatus.CANCELLED)
    db.close()

    response = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/cancel")

    assert response.status_code == 200
    db = Database(str(tmp_path / "test.db"))
    assert db.get_job(job.id).status == JobStatus.CANCELLED  # unchanged, not re-finished
    db.close()


def test_cancel_book_returns_404_for_an_unknown_book(tmp_path):
    client = _client(tmp_path)

    response = client.post("/api/books/999/cancel")

    assert response.status_code == 404


def test_cancel_chapter_returns_404_for_a_chapter_in_a_different_book(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book_a = db.create_book(
        title="A", author=None, source_path=str(tmp_path / "a.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    book_b = db.create_book(
        title="B", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter_b] = db.add_chapters(book_b.id, [RawChapter(title="Ch 1", text="Body.")])
    db.close()

    response = client.post(f"/api/books/{book_a.id}/chapters/{chapter_b.id}/cancel")

    assert response.status_code == 404


def test_cancel_book_cancels_every_chapter_with_an_active_job(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    chapters = db.add_chapters(book.id, [RawChapter(title=f"Ch {i}", text=f"Body {i}.")
                                          for i in range(2)])
    job0 = db.create_job(chapters[0].id)
    job1 = db.create_job(chapters[1].id)
    db.close()

    response = client.post(f"/api/books/{book.id}/cancel")

    assert response.status_code == 200
    db = Database(str(tmp_path / "test.db"))
    assert db.get_job(job0.id).status == JobStatus.CANCELLED
    assert db.get_job(job1.id).status == JobStatus.CANCELLED
    assert db.get_chapter(chapters[0].id).status == ChapterStatus.PENDING
    assert db.get_chapter(chapters[1].id).status == ChapterStatus.PENDING
    db.close()


def test_create_voice_stores_reference_audio_under_voices_dir_not_uploads(tmp_path):
    client = _client(tmp_path)

    created = client.post(
        "/api/voices",
        files={"file": ("ref.wav", _clean_tone_wav_bytes(), "audio/wav")},
        data={"name": "narrator-1"},
    ).json()

    db = Database(str(tmp_path / "test.db"))
    profile = db.get_voice_profile_by_id(created["id"])
    db.close()

    assert profile.reference_audio_path.startswith(str((tmp_path / "voices").resolve()))
    assert Path(profile.reference_audio_path).is_file()
    assert list((tmp_path / "uploads").iterdir()) == []  # staging dir cleaned up


def test_delete_voice_removes_the_permanent_voice_storage_folder(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/voices",
        files={"file": ("a.wav", _clean_tone_wav_bytes(), "audio/wav")},
        data={"name": "a"},
    ).json()
    db = Database(str(tmp_path / "test.db"))
    voice_dir = Path(db.get_voice_profile_by_id(created["id"]).reference_audio_path).parent
    db.close()
    assert voice_dir.is_dir()

    client.delete(f"/api/voices/{created['id']}")

    assert not voice_dir.exists()


def _upload_voice(client, name="narrator-1", **data):
    return client.post("/api/voices", files={"file": ("ref.wav", _clean_tone_wav_bytes(), "audio/wav")},
                       data={"name": name, **data}).json()


def test_a_new_voice_is_queued_for_transcription_and_reports_its_length(tmp_path):
    client = _client(tmp_path)

    body = _upload_voice(client)

    assert body["transcript_status"] == "pending" and body["reference_transcript"] is None
    assert body["reference_seconds"] == pytest.approx(15.0, abs=0.01)
    assert body["reference_trim_end"] is None and body["transcript_error"] is None


def test_a_voice_uploaded_with_a_typed_transcript_skips_whisper(tmp_path):
    client = _client(tmp_path)
    body = _upload_voice(client, reference_transcript="  I typed every word.  ")
    assert (body["transcript_status"], body["reference_transcript"]) == ("done", "I typed every word.")


def test_editing_a_voice_transcript_saves_it(tmp_path):
    client = _client(tmp_path)
    voice = _upload_voice(client)

    response = client.patch(f"/api/voices/{voice['id']}", json={"reference_transcript": "a small son called"})

    assert response.status_code == 200
    assert response.json()["reference_transcript"] == "a small son called"
    assert response.json()["transcript_status"] == "done"
    assert client.patch(f"/api/voices/{voice['id']}", json={"reference_transcript": "  "}).status_code == 400
    assert client.patch("/api/voices/999", json={"reference_transcript": "x"}).status_code == 404


def test_requesting_a_transcription_requeues_it(tmp_path):
    client = _client(tmp_path)
    voice = _upload_voice(client, reference_transcript="typed")

    response = client.post(f"/api/voices/{voice['id']}/transcribe")

    assert response.status_code == 200 and response.json()["transcript_status"] == "pending"
    assert client.post("/api/voices/999/transcribe").status_code == 404


def _book(client, tmp_path, db_voice=None):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(title="T", author=None, source_path=str(tmp_path / "b.md"),
                          source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"))
    chapters = db.add_chapters(book.id, [RawChapter(title="1", text="A."), RawChapter(title="2", text="B.")])
    if db_voice:
        db.update_book_voice_profile(book.id, db_voice)
    db.close()
    return book, chapters


def test_engines_endpoint_lists_both_engines_with_labels(tmp_path):
    body = _client(tmp_path).get("/api/engines").json()
    assert body["default"] == "chatterbox-turbo"
    by_id = {e["id"]: e for e in body["engines"]}
    assert by_id["qwen3"]["label"] == "Quality (Qwen3)" and by_id["qwen3"]["needs_voice"] is True
    assert by_id["chatterbox-turbo"]["label"] == "Speed (Turbo)" and by_id["chatterbox-turbo"]["needs_voice"] is False


def test_generating_a_book_stores_the_chosen_engine_for_every_later_job(tmp_path):
    client = _client(tmp_path)
    voice = _upload_voice(client)
    book, _ = _book(client, tmp_path, db_voice=voice["id"])

    response = client.post(f"/api/books/{book.id}/generate", json={"engine": "qwen3"})

    assert response.status_code == 200 and len(response.json()["queued_job_ids"]) == 2
    assert client.get(f"/api/books/{book.id}").json()["engine"] == "qwen3"
    client.post(f"/api/books/{book.id}/generate")  # no choice given: keeps the stored one
    assert client.get(f"/api/books/{book.id}").json()["engine"] == "qwen3"


def test_generate_rejects_unknown_engines_and_quality_without_a_voice(tmp_path):
    client = _client(tmp_path)
    book, [chapter, _] = _book(client, tmp_path)

    assert client.post(f"/api/books/{book.id}/generate", json={"engine": "nope"}).status_code == 400
    r = client.post(f"/api/books/{book.id}/generate", json={"engine": "qwen3"})
    assert r.status_code == 400 and "voice" in r.json()["detail"]
    r = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/generate", json={"engine": "qwen3"})
    assert r.status_code == 400
    assert client.get(f"/api/books/{book.id}").json()["engine"] == "chatterbox-turbo"  # nothing changed


def test_regenerating_one_chapter_with_another_engine_switches_the_book(tmp_path):
    client = _client(tmp_path)
    voice = _upload_voice(client)
    book, [chapter, _] = _book(client, tmp_path, db_voice=voice["id"])

    r = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/generate", json={"engine": "qwen3"})

    assert r.status_code == 200 and r.json()["job_id"] is not None
    assert client.get(f"/api/books/{book.id}").json()["engine"] == "qwen3"


def test_book_reports_which_engine_made_each_chapter_and_whether_they_are_mixed(tmp_path):
    client = _client(tmp_path)
    book, [a, b] = _book(client, tmp_path)
    db = Database(str(tmp_path / "test.db"))
    db.update_chapter_status(a.id, ChapterStatus.DONE, output_mp3_path="/a.mp3", engine="qwen3")
    db.close()

    body = client.get(f"/api/books/{book.id}").json()
    assert [c["engine"] for c in body["chapters"]] == ["qwen3", None]
    assert body["mixed_engines"] is False

    db = Database(str(tmp_path / "test.db"))
    db.update_chapter_status(b.id, ChapterStatus.DONE, output_mp3_path="/b.mp3", engine="chatterbox-turbo")
    db.close()
    assert client.get(f"/api/books/{book.id}").json()["mixed_engines"] is True


def test_text_clips_store_their_engine(tmp_path):
    client = _client(tmp_path)
    voice = _upload_voice(client)

    clip = client.post("/api/text-clips", json={"text": "Hi.", "voice_profile_id": voice["id"], "engine": "qwen3"})
    assert clip.status_code == 200 and clip.json()["engine"] == "qwen3"
    assert client.post("/api/text-clips", json={"text": "Hi."}).json()["engine"] == "chatterbox-turbo"
    assert client.post("/api/text-clips", json={"text": "Hi.", "engine": "qwen3"}).status_code == 400
    assert client.post("/api/text-clips", json={"text": "Hi.", "engine": "nope"}).status_code == 400


def test_voice_audio_serves_the_clip_the_engines_clone_from(tmp_path):
    client = _client(tmp_path)
    voice = _upload_voice(client)

    full = client.get(f"/api/voices/{voice['id']}/audio")
    assert full.status_code == 200
    assert sf.info(io.BytesIO(full.content)).duration == pytest.approx(15.0, abs=0.01)

    db = Database(str(tmp_path / "test.db"))
    db.save_voice_transcript(voice["id"], "Part.", words=[{"word": " Part.", "start": 0, "end": 4}], trim_end=4.0)
    db.close()
    trimmed = client.get(f"/api/voices/{voice['id']}/audio")
    assert sf.info(io.BytesIO(trimmed.content)).duration == pytest.approx(4.0, abs=0.01)
    assert client.get("/api/voices/999/audio").status_code == 404


def test_a_voice_reports_its_tempo_and_the_pace_of_the_clip_engines_hear(tmp_path):
    client = _client(tmp_path)
    voice = _upload_voice(client)
    assert voice["tempo"] == 1.0 and voice["pace"] is None  # not transcribed yet

    db = Database(str(tmp_path / "test.db"))
    words = [{"word": " One", "start": 0.0, "end": 0.5}, {"word": " two", "start": 0.5, "end": 1.0},
             {"word": " three", "start": 14.0, "end": 14.5}]
    db.save_voice_transcript(voice["id"], "One two", words=words, trim_end=1.1)
    db.close()

    pace = client.get("/api/voices").json()[0]["pace"]
    assert pace == {"speaking_wpm": pytest.approx(120.0), "overall_wpm": pytest.approx(120.0)}


def test_setting_a_voice_tempo_validates_the_range_and_keeps_the_transcript(tmp_path):
    client = _client(tmp_path)
    voice = _upload_voice(client, reference_transcript="typed")
    url = f"/api/voices/{voice['id']}"

    body = client.patch(url, json={"tempo": 0.884}).json()
    assert body["tempo"] == 0.88 and body["reference_transcript"] == "typed"
    for bad in (0.7, 1.3, "fast", None):
        assert client.patch(url, json={"tempo": bad}).status_code == 400
    assert client.patch(url, json={}).status_code == 400
    assert client.patch("/api/voices/999", json={"tempo": 0.9}).status_code == 404


def test_voice_audio_plays_the_saved_tempo_or_a_preview_tempo(tmp_path):
    client = _client(tmp_path)
    voice = _upload_voice(client)
    url = f"/api/voices/{voice['id']}/audio"

    preview = client.get(url, params={"tempo": 0.8})
    assert sf.info(io.BytesIO(preview.content)).duration == pytest.approx(15.0 / 0.8, abs=0.05)
    assert client.get(url).headers["content-length"] != preview.headers["content-length"]  # nothing saved

    client.patch(f"/api/voices/{voice['id']}", json={"tempo": 1.25})
    assert sf.info(io.BytesIO(client.get(url).content)).duration == pytest.approx(15.0 / 1.25, abs=0.05)
    assert client.get(url, params={"tempo": 2}).status_code == 400


def test_a_text_clip_can_carry_a_sample_tempo(tmp_path):
    client = _client(tmp_path)
    voice = _upload_voice(client)

    body = client.post("/api/text-clips", json={"text": "Hi.", "voice_profile_id": voice["id"],
                                                 "engine": "chatterbox-turbo", "tempo": 0.9}).json()
    assert body["tempo"] == 0.9
    assert client.post("/api/text-clips", json={"text": "Hi.", "voice_profile_id": None,
                                                "engine": "chatterbox-turbo"}).json()["tempo"] is None
    assert client.post("/api/text-clips", json={"text": "Hi.", "voice_profile_id": voice["id"],
                                                "tempo": 1.5}).status_code == 400
    assert client.post("/api/text-clips", json={"text": "Hi.", "voice_profile_id": None,
                                                "engine": "chatterbox-turbo", "tempo": 0.9}).status_code == 400


def _book_with_chapter(tmp_path, title="T"):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title=title, author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    return db, book, chapter


def test_chapter_audio_serves_the_finished_mp3(tmp_path):
    client = _client(tmp_path)
    db, book, chapter = _book_with_chapter(tmp_path)
    mp3 = tmp_path / "ch1.mp3"
    mp3.write_bytes(b"ID3fake")
    db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=str(mp3), duration=1.0)
    db.close()

    response = client.get(f"/api/books/{book.id}/chapters/{chapter.id}/audio")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"ID3fake"


def test_chapter_audio_404s_until_the_chapter_is_done(tmp_path):
    client = _client(tmp_path)
    db, book, chapter = _book_with_chapter(tmp_path)
    db.close()

    assert client.get(f"/api/books/{book.id}/chapters/{chapter.id}/audio").status_code == 404


def test_chapter_audio_404s_when_the_file_is_missing(tmp_path):
    client = _client(tmp_path)
    db, book, chapter = _book_with_chapter(tmp_path)
    db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=str(tmp_path / "gone.mp3"),
                              duration=1.0)
    db.close()

    assert client.get(f"/api/books/{book.id}/chapters/{chapter.id}/audio").status_code == 404


def test_chapter_audio_404s_for_unknown_book_or_a_chapter_of_another_book(tmp_path):
    client = _client(tmp_path)
    db, book, chapter = _book_with_chapter(tmp_path)
    other = db.create_book(
        title="Other", author=None, source_path=str(tmp_path / "o.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.close()

    assert client.get(f"/api/books/999/chapters/{chapter.id}/audio").status_code == 404
    assert client.get(f"/api/books/{other.id}/chapters/{chapter.id}/audio").status_code == 404
    assert client.get(f"/api/books/{book.id}/chapters/999/audio").status_code == 404


def test_book_reports_duration_only_for_done_chapters(tmp_path):
    client = _client(tmp_path)
    db, book, chapter = _book_with_chapter(tmp_path)
    db.close()

    assert client.get(f"/api/books/{book.id}").json()["chapters"][0]["duration"] is None


def test_book_measures_and_stores_duration_of_chapters_finished_before_durations_were_kept(tmp_path):
    client = _client(tmp_path)
    db, book, chapter = _book_with_chapter(tmp_path)
    mp3 = tmp_path / "ch1.mp3"
    sf.write(str(mp3), np.zeros(22050 * 2, dtype=np.float32), 22050, format="MP3")
    db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=str(mp3))

    duration = client.get(f"/api/books/{book.id}").json()["chapters"][0]["duration"]

    assert duration == pytest.approx(2.0, abs=0.1)
    assert db.get_chapter(chapter.id).duration == pytest.approx(duration)
    db.close()


@requires_ffmpeg
def test_worker_stores_the_chapter_duration_when_it_finishes(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/books", files={"file": ("book.md", b"# One\nA short line.\n", "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    ).json()
    client.post(f"/api/books/{created['id']}/generate")

    db = Database(str(tmp_path / "test.db"))
    engine = FakeEngine()
    engine.load("cpu")
    run_worker_once(db, _slot(engine))
    stored = db.list_chapters(created["id"])[0].duration
    db.close()

    assert stored and stored > 0
    assert client.get(f"/api/books/{created['id']}").json()["chapters"][0]["duration"] == stored


def _upload_book(client, tmp_path):
    out = tmp_path / "out"
    response = client.post(
        "/api/books",
        files={"file": ("tiny.md", b"# One\n\nHello.\n\n# Two\n\nWorld.\n", "text/markdown")},
        data={"output_dir": str(out)},
    )
    assert response.status_code == 200
    return response.json()


def test_delete_book_removes_book_cache_and_staged_upload_but_keeps_mp3s(tmp_path):
    import audiobookify.synth.chunk_cache as chunk_cache

    client = _client(tmp_path)
    created = _upload_book(client, tmp_path)
    db = Database(str(tmp_path / "test.db"))
    book = db.get_book(created["id"])
    staged_dir = Path(book.source_path).parent
    chapter = db.list_chapters(book.id)[0]
    mp3 = tmp_path / "out" / "one.mp3"
    mp3.write_bytes(b"mp3")
    db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=str(mp3))
    chunk_cache.save_chunk(chapter.id, 0, np.zeros(10, dtype="float32"), sample_rate=22050)
    assert staged_dir.is_dir() and chunk_cache.chapter_cache_dir(chapter.id).is_dir()

    response = client.delete(f"/api/books/{book.id}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert client.get("/api/books").json() == []
    assert not staged_dir.exists()
    assert not chunk_cache.chapter_cache_dir(chapter.id).exists()
    assert mp3.exists()


def test_delete_book_keeps_a_source_file_outside_the_upload_dir(tmp_path):
    client = _client(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    source = elsewhere / "b.md"
    source.write_text("# One\n\nHello.\n")
    book = db.create_book(title="B", author=None, source_path=str(source),
                          source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"))

    assert client.delete(f"/api/books/{book.id}").status_code == 200

    assert source.exists()
    assert client.get("/api/books").json() == []


def test_delete_book_returns_404_for_unknown_book(tmp_path):
    assert _client(tmp_path).delete("/api/books/999").status_code == 404


def test_delete_book_returns_409_while_a_job_is_queued(tmp_path):
    client = _client(tmp_path)
    created = _upload_book(client, tmp_path)
    db = Database(str(tmp_path / "test.db"))
    db.create_job(db.list_chapters(created["id"])[0].id)

    response = client.delete(f"/api/books/{created['id']}")

    assert response.status_code == 409
    assert len(client.get("/api/books").json()) == 1

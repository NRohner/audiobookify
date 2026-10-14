import shutil

import numpy as np
import pytest
from fastapi.testclient import TestClient
from mutagen.mp3 import MP3

from audiobookify.api.app import create_app
from audiobookify.db import Database, book_paragraphs
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, JobStatus, SourceFormat
from audiobookify.postprocess.audio import chapter_filename, export_chapter_mp3, relabel_chapter_mp3

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _book(tmp_path, chapters):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="B", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.add_chapters(book.id, [RawChapter(title=t, text=x) for t, x in chapters])
    return db, book


THREE = [("One", "a\n\nb"), ("Two", "c\n\nd\n\ne"), ("Three", "f")]


def test_book_paragraphs_flattens_chapters_and_reports_starts(tmp_path):
    db, book = _book(tmp_path, THREE)
    paragraphs, starts = book_paragraphs(db.list_chapters(book.id))
    assert paragraphs == ["a", "b", "c", "d", "e", "f"]
    assert starts == [0, 2, 5]


def test_replace_breaks_keeps_untouched_chapters_and_rebuilds_changed_ones(tmp_path):
    db, book = _book(tmp_path, THREE)
    one, two, three = db.list_chapters(book.id)
    db.update_chapter_status(one.id, ChapterStatus.DONE, output_mp3_path="/x/1.mp3")
    db.update_chapter_status(two.id, ChapterStatus.DONE, output_mp3_path="/x/2.mp3")
    db.create_job(two.id)

    # Split "Two" after "c"; rename "One" (same range, so its audio survives).
    removed = db.replace_chapter_breaks(book.id, [(0, "Prologue"), (2, "Two"), (3, "Two b"), (5, "Three")])

    chapters = db.list_chapters(book.id)
    assert [(c.order_index, c.title, c.text) for c in chapters] == [
        (0, "Prologue", "a\n\nb"), (1, "Two", "c"), (2, "Two b", "d\n\ne"), (3, "Three", "f"),
    ]
    assert chapters[0].id == one.id and chapters[0].status == ChapterStatus.DONE
    assert chapters[3].id == three.id
    assert chapters[1].id != two.id and chapters[1].status == ChapterStatus.PENDING
    assert chapters[1].output_mp3_path is None
    assert [c.id for c in removed] == [two.id]
    assert db.list_jobs_for_chapter(two.id) == []


def test_replace_breaks_can_merge_chapters(tmp_path):
    db, book = _book(tmp_path, THREE)
    db.replace_chapter_breaks(book.id, [(0, "All of it")])
    [only] = db.list_chapters(book.id)
    assert only.text == "a\n\nb\n\nc\n\nd\n\ne\n\nf"


@pytest.mark.parametrize("breaks", [
    [],                                  # no chapters
    [(1, "X")],                          # must start at the first paragraph
    [(0, "X"), (3, "Y"), (3, "Z")],      # not strictly increasing
    [(0, "X"), (6, "Y")],                # past the end
    [(0, "X"), (2, "  ")],               # blank title
])
def test_replace_breaks_rejects_invalid_breaks_without_changing_anything(tmp_path, breaks):
    db, book = _book(tmp_path, THREE)
    before = db.list_chapters(book.id)
    with pytest.raises(ValueError):
        db.replace_chapter_breaks(book.id, breaks)
    assert db.list_chapters(book.id) == before


def test_replace_breaks_rejects_a_chapter_with_no_words(tmp_path):
    db, book = _book(tmp_path, [("One", "a\n\n\n\nb")])  # paragraphs: a, "", b
    with pytest.raises(ValueError):
        db.replace_chapter_breaks(book.id, [(0, "X"), (1, "Empty"), (2, "B")])


@requires_ffmpeg
def test_relabel_chapter_mp3_renames_and_retags(tmp_path):
    audio = np.zeros(2205, dtype=np.float32)
    path, _ = export_chapter_mp3(audio, 22050, str(tmp_path), 0, "Old", "Book", "N")

    new_path = relabel_chapter_mp3(path, 4, "New")

    assert new_path == str(tmp_path / chapter_filename(4, "New"))
    assert not (tmp_path / chapter_filename(0, "Old")).exists()
    tags = MP3(new_path).tags
    assert str(tags["TIT2"]) == "New" and str(tags["TRCK"]) == "5"


def _client(tmp_path):
    return TestClient(create_app(db_path=str(tmp_path / "test.db"), upload_dir=str(tmp_path / "up")))


def test_text_endpoint_returns_paragraphs_and_chapter_starts(tmp_path):
    db, book = _book(tmp_path, THREE)
    db.close()
    body = _client(tmp_path).get(f"/api/books/{book.id}/text").json()
    assert body["paragraphs"] == ["a", "b", "c", "d", "e", "f"]
    assert [(c["title"], c["start"], c["status"]) for c in body["chapters"]] == [
        ("One", 0, "pending"), ("Two", 2, "pending"), ("Three", 5, "pending"),
    ]
    assert _client(tmp_path).get("/api/books/999/text").status_code == 404


@requires_ffmpeg
def test_put_chapters_renumbers_kept_audio_and_deletes_stale_audio(tmp_path):
    db, book = _book(tmp_path, THREE)
    one, two, three = db.list_chapters(book.id)
    out = tmp_path / "out"
    audio = np.zeros(2205, dtype=np.float32)
    paths = {}
    for ch in (two, three):
        paths[ch.id], _ = export_chapter_mp3(audio, 22050, str(out), ch.order_index, ch.title, "B", "N")
        db.update_chapter_status(ch.id, ChapterStatus.DONE, output_mp3_path=paths[ch.id])
    db.close()

    # Split "One" in two: "Two" stays intact but moves to position 2; "Three" too.
    response = _client(tmp_path).put(f"/api/books/{book.id}/chapters", json={"chapters": [
        {"start": 0, "title": "One"}, {"start": 1, "title": "One b"},
        {"start": 2, "title": "Two"}, {"start": 5, "title": "Three"},
    ]})

    assert response.status_code == 200
    chapters = response.json()["chapters"]
    assert [c["title"] for c in chapters] == ["One", "One b", "Two", "Three"]
    assert chapters[2]["status"] == "done"
    assert chapters[2]["output_mp3_path"] == str(out / chapter_filename(2, "Two"))
    assert chapters[3]["output_mp3_path"] == str(out / chapter_filename(3, "Three"))
    assert (out / chapter_filename(2, "Two")).is_file()
    assert sorted(p.name for p in out.glob("*.mp3")) == [chapter_filename(2, "Two"), chapter_filename(3, "Three")]


def test_put_chapters_deletes_audio_of_removed_chapters(tmp_path):
    db, book = _book(tmp_path, THREE)
    one, two, _ = db.list_chapters(book.id)
    stale = tmp_path / "stale.mp3"
    stale.write_bytes(b"ID3fake")
    db.update_chapter_status(two.id, ChapterStatus.DONE, output_mp3_path=str(stale))
    db.close()

    response = _client(tmp_path).put(f"/api/books/{book.id}/chapters", json={"chapters": [
        {"start": 0, "title": "One"}, {"start": 3, "title": "Two"},
    ]})

    assert response.status_code == 200
    assert not stale.exists()


def test_put_chapters_rejects_invalid_breaks_and_unknown_books(tmp_path):
    db, book = _book(tmp_path, THREE)
    db.close()
    client = _client(tmp_path)
    bad = client.put(f"/api/books/{book.id}/chapters", json={"chapters": [{"start": 2, "title": "X"}]})
    assert bad.status_code == 400
    assert client.put("/api/books/999/chapters", json={"chapters": [{"start": 0, "title": "X"}]}).status_code == 404


def test_put_chapters_refuses_while_the_book_is_rendering(tmp_path):
    db, book = _book(tmp_path, THREE)
    db.create_job(db.list_chapters(book.id)[2].id)
    db.close()
    response = _client(tmp_path).put(f"/api/books/{book.id}/chapters", json={"chapters": [{"start": 0, "title": "X"}]})
    assert response.status_code == 409


def test_put_chapters_allows_edits_once_jobs_have_finished(tmp_path):
    db, book = _book(tmp_path, THREE)
    job = db.create_job(db.list_chapters(book.id)[2].id)
    db.finish_job(job.id, JobStatus.DONE)
    db.close()
    response = _client(tmp_path).put(f"/api/books/{book.id}/chapters", json={"chapters": [{"start": 0, "title": "X"}]})
    assert response.status_code == 200

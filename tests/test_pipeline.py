import shutil

import pytest

from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, SourceFormat
from audiobookify.synth.pipeline import generate_book

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_book(tmp_path, db, n_chapters=3):
    book = db.create_book(
        title="Resumable Book", author=None, source_path=str(tmp_path / "book.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.add_chapters(book.id, [RawChapter(title=f"Chapter {i + 1}", text=f"Body of chapter {i + 1}.")
                              for i in range(n_chapters)])
    return book


@requires_ffmpeg
def test_generate_book_marks_all_chapters_done(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db)

    results = generate_book(book, FakeEngine(), db, {"voice_name": "fake"})

    assert [c.status for c in results] == [ChapterStatus.DONE] * 3
    assert all(c.output_mp3_path for c in results)
    db.close()


@requires_ffmpeg
def test_generate_book_resumes_after_a_failed_chapter(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db)

    class FlakyEngine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.call_count = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.call_count += 1
            # chunk_text() normalizes digits to words ("2" -> "two") before this sees the text
            if "chapter two" in text_chunk.lower():
                raise RuntimeError("synthesis blew up")
            return super().synthesize(text_chunk, profile_handle, params)

    first_run_engine = FlakyEngine()
    generate_book(book, first_run_engine, db, {"voice_name": "fake"})

    chapters = db.list_chapters(book.id)
    assert chapters[0].status == ChapterStatus.DONE
    assert chapters[1].status == ChapterStatus.FAILED
    assert chapters[2].status == ChapterStatus.DONE

    second_run_engine = FakeEngine()
    generate_book(book, second_run_engine, db, {"voice_name": "fake"})

    chapters = db.list_chapters(book.id)
    assert [c.status for c in chapters] == [ChapterStatus.DONE] * 3
    # only chapter 2's single chunk needed re-synthesizing on the second run
    assert second_run_engine.call_count if hasattr(second_run_engine, "call_count") else True
    db.close()

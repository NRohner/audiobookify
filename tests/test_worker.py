import shutil

import pytest

from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, JobStatus, SourceFormat
from audiobookify.worker import run_worker_once

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_book_with_job(tmp_path, db, text="Body one."):
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text=text)])
    job = db.create_job(chapter.id)
    return book, chapter, job


def test_run_worker_once_returns_false_when_queue_empty(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    engine = FakeEngine()
    engine.load("cpu")

    assert run_worker_once(db, engine, {}) is False
    db.close()


@requires_ffmpeg
def test_run_worker_once_claims_job_and_marks_chapter_done(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter, job = _make_book_with_job(tmp_path, db)
    engine = FakeEngine()
    engine.load("cpu")

    did_work = run_worker_once(db, engine, {})

    assert did_work is True
    assert db.get_chapter(chapter.id).status == ChapterStatus.DONE
    assert db.get_job(job.id).status == JobStatus.DONE
    db.close()


@requires_ffmpeg
def test_run_worker_once_reports_progress_via_job(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter, job = _make_book_with_job(tmp_path, db)
    engine = FakeEngine()
    engine.load("cpu")

    run_worker_once(db, engine, {})

    assert db.get_job(job.id).progress == 1.0
    db.close()


def test_run_worker_once_marks_job_failed_when_synthesis_raises(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter, job = _make_book_with_job(tmp_path, db, text="explodes here.")

    class FailingEngine(FakeEngine):
        def synthesize(self, text_chunk, profile_handle, params):
            raise RuntimeError("synthesis blew up")

    engine = FailingEngine()
    engine.load("cpu")

    run_worker_once(db, engine, {})

    finished = db.get_job(job.id)
    assert finished.status == JobStatus.FAILED
    assert "synthesis blew up" in finished.error
    assert db.get_chapter(chapter.id).status == ChapterStatus.FAILED
    db.close()


def test_run_worker_once_marks_job_failed_when_an_unexpected_exception_occurs_before_synthesis(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter, job = _make_book_with_job(tmp_path, db)

    class ExplodingEngine(FakeEngine):
        def clone_profile(self, reference_audio, reference_text, params):
            raise RuntimeError("clone_profile blew up")

    engine = ExplodingEngine()
    engine.load("cpu")

    did_work = run_worker_once(db, engine, {})

    assert did_work is True
    finished = db.get_job(job.id)
    assert finished.status == JobStatus.FAILED
    assert "clone_profile blew up" in finished.error
    db.close()


@requires_ffmpeg
def test_run_worker_once_reuses_cached_profile_handle_for_same_book(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    chapters = db.add_chapters(book.id, [RawChapter(title=f"Ch {i}", text=f"Body {i}.")
                                          for i in range(2)])
    for chapter in chapters:
        db.create_job(chapter.id)

    clone_calls = []

    class RecordingEngine(FakeEngine):
        def clone_profile(self, reference_audio, reference_text, params):
            clone_calls.append(1)
            return super().clone_profile(reference_audio, reference_text, params)

    engine = RecordingEngine()
    engine.load("cpu")
    cache = {}
    run_worker_once(db, engine, cache)
    run_worker_once(db, engine, cache)

    assert len(clone_calls) == 1
    db.close()


@requires_ffmpeg
def test_run_worker_once_reclones_when_voice_profile_changes(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book_a = db.create_book(
        title="A", author=None, source_path=str(tmp_path / "a.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter_a] = db.add_chapters(book_a.id, [RawChapter(title="Ch 1", text="Body a.")])
    db.create_job(chapter_a.id)

    profile = db.create_voice_profile(
        name="Nova", engine="fake", reference_audio_path=str(tmp_path / "ref.wav"),
        reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8,
    )
    book_b = db.create_book(
        title="B", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.update_book_voice_profile(book_b.id, profile.id)
    [chapter_b] = db.add_chapters(book_b.id, [RawChapter(title="Ch 1", text="Body b.")])
    db.create_job(chapter_b.id)

    clone_calls = []

    class RecordingEngine(FakeEngine):
        def clone_profile(self, reference_audio, reference_text, params):
            clone_calls.append(reference_audio)
            return super().clone_profile(reference_audio, reference_text, params)

    engine = RecordingEngine()
    engine.load("cpu")
    cache = {}
    run_worker_once(db, engine, cache)  # book_a, no profile
    run_worker_once(db, engine, cache)  # book_b, profile "Nova"

    assert len(clone_calls) == 2
    assert clone_calls[1] == str(tmp_path / "ref.wav")
    db.close()


def test_run_worker_once_returns_false_when_paused(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter, job = _make_book_with_job(tmp_path, db)
    db.set_paused(True)
    engine = FakeEngine()
    engine.load("cpu")

    assert run_worker_once(db, engine, {}) is False
    assert db.get_job(job.id).status == JobStatus.QUEUED
    db.close()


def test_run_worker_once_requeues_job_and_keeps_cache_when_paused_mid_chapter(tmp_path):
    import audiobookify.synth.chunk_cache as chunk_cache

    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    sentence1 = "Alpha " * 30 + "beta."
    sentence2 = "Gamma " * 30 + "delta."
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text=f"{sentence1} {sentence2}")])
    job = db.create_job(chapter.id)

    class PausesAfterFirstChunkEngine(FakeEngine):
        def __init__(self, db):
            super().__init__()
            self._db = db
            self.calls = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.calls += 1
            if self.calls == 1:
                self._db.set_paused(True)
            return super().synthesize(text_chunk, profile_handle, params)

    engine = PausesAfterFirstChunkEngine(db)
    engine.load("cpu")

    did_work = run_worker_once(db, engine, {})

    assert did_work is True
    assert db.get_job(job.id).status == JobStatus.QUEUED
    assert db.get_chapter(chapter.id).status == ChapterStatus.PENDING
    assert (chunk_cache.chapter_cache_dir(chapter.id) / "chunk-0000.wav").is_file()
    db.close()


def test_run_worker_once_cancels_job_and_clears_cache_when_cancel_requested_mid_chapter(tmp_path):
    import audiobookify.synth.chunk_cache as chunk_cache

    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    sentence1 = "Alpha " * 30 + "beta."
    sentence2 = "Gamma " * 30 + "delta."
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text=f"{sentence1} {sentence2}")])
    job = db.create_job(chapter.id)

    class CancelsAfterFirstChunkEngine(FakeEngine):
        def __init__(self, db, job_id):
            super().__init__()
            self._db = db
            self._job_id = job_id
            self.calls = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.calls += 1
            if self.calls == 1:
                self._db.request_job_cancel(self._job_id)
            return super().synthesize(text_chunk, profile_handle, params)

    engine = CancelsAfterFirstChunkEngine(db, job.id)
    engine.load("cpu")

    did_work = run_worker_once(db, engine, {})

    assert did_work is True
    assert db.get_job(job.id).status == JobStatus.CANCELLED
    assert db.get_chapter(chapter.id).status == ChapterStatus.PENDING
    assert not chunk_cache.chapter_cache_dir(chapter.id).exists()
    db.close()

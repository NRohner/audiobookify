import os
import shutil

import pytest

import audiobookify.synth.chunk_cache as chunk_cache
import audiobookify.synth.pipeline as pipeline
from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, JobStatus, SourceFormat
from audiobookify.synth.pipeline import generate_book, synthesize_chapter, synthesize_text_clip

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

    class CountingEngine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.call_count = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.call_count += 1
            return super().synthesize(text_chunk, profile_handle, params)

    second_run_engine = CountingEngine()
    generate_book(book, second_run_engine, db, {"voice_name": "fake"})

    chapters = db.list_chapters(book.id)
    assert [c.status for c in chapters] == [ChapterStatus.DONE] * 3
    # only chapter 2's single chunk ("Body of chapter two.", well under the
    # 300-char max_chunk_chars) needed re-synthesizing on the second run;
    # chapters 1 and 3 were already DONE and must be skipped, not redone.
    assert second_run_engine.call_count == 1
    db.close()


@requires_ffmpeg
def test_generate_book_applies_lexicon_substitution_before_synthesis(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    db.create_lexicon_entry(term="chapter", override_value="CHAP-ter", scope="global")

    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    generate_book(book, RecordingEngine(), db, {"voice_name": "fake"})

    assert any("CHAP-ter" in text for text in RecordingEngine.synthesized_texts)
    assert not any("Body of chapter" in text for text in RecordingEngine.synthesized_texts)
    db.close()


@requires_ffmpeg
def test_generate_book_works_normally_with_no_lexicon_entries(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)

    results = generate_book(book, FakeEngine(), db, {"voice_name": "fake"})

    assert results[0].status == ChapterStatus.DONE
    db.close()


@requires_ffmpeg
def test_generate_book_prefers_book_scoped_lexicon_entry_over_global(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    db.create_lexicon_entry(term="chapter", override_value="GLOBAL-version", scope="global")
    db.create_lexicon_entry(term="chapter", override_value="BOOK-version", scope="book", book_id=book.id)

    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    generate_book(book, RecordingEngine(), db, {"voice_name": "fake"})

    assert any("BOOK-version" in text for text in RecordingEngine.synthesized_texts)
    assert not any("GLOBAL-version" in text for text in RecordingEngine.synthesized_texts)
    db.close()


@requires_ffmpeg
def test_synthesize_chapter_reports_progress_per_chunk(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    [chapter] = db.list_chapters(book.id)
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    calls = []
    result = synthesize_chapter(
        chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
        on_chunk_done=lambda done, total: calls.append((done, total)),
    )

    assert result.status == ChapterStatus.DONE
    # "Body of chapter 1." is a single sentence, well under max_chunk_chars -> one chunk
    assert calls == [(1, 1)]
    db.close()


@requires_ffmpeg
def test_synthesize_chapter_records_used_fallback_when_output_dir_is_unwritable(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "test.db"))
    locked = tmp_path / "locked"
    locked.mkdir()
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(locked),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    os.chmod(locked, 0o500)
    monkeypatch.setattr(pipeline, "BACKUP_OUTPUT_DIR", str(tmp_path / "backup"))
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    try:
        result = synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"},
                                     [], db)
    finally:
        os.chmod(locked, 0o700)

    assert result.status == ChapterStatus.DONE
    assert result.used_fallback is True
    assert result.output_mp3_path.startswith(str(tmp_path / "backup"))
    db.close()


@requires_ffmpeg
def test_synthesize_text_clip_records_used_fallback_when_output_dir_is_unwritable(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "test.db"))
    clip = db.create_text_clip(text="Hello there.", voice_profile_id=None)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    monkeypatch.setattr(pipeline, "BACKUP_OUTPUT_DIR", str(tmp_path / "backup"))
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    try:
        result = synthesize_text_clip(clip, engine, profile_handle, {"voice_name": "fake"}, [], db,
                                       str(locked))
    finally:
        os.chmod(locked, 0o700)

    assert result.status == JobStatus.DONE
    assert result.used_fallback is True
    assert result.output_path.startswith(str(tmp_path / "backup"))
    db.close()


@requires_ffmpeg
def test_synthesize_chapter_writes_each_chunk_to_disk_as_it_is_synthesized(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    sentence1 = "Alpha " * 30 + "beta."
    sentence2 = "Gamma " * 30 + "delta."
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text=f"{sentence1} {sentence2}")])
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    seen = {}

    def check_disk_after_first_chunk(done, total):
        if done == 1:
            cache_dir = chunk_cache.chapter_cache_dir(chapter.id)
            seen["chunk_0_exists"] = (cache_dir / "chunk-0000.wav").is_file()
            seen["chunk_1_exists"] = (cache_dir / "chunk-0001.wav").is_file()

    result = synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
                                 on_chunk_done=check_disk_after_first_chunk)

    assert result.status == ChapterStatus.DONE
    assert seen == {"chunk_0_exists": True, "chunk_1_exists": False}
    db.close()


@requires_ffmpeg
def test_synthesize_chapter_resumes_from_cached_chunks_after_a_pause(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    sentence1 = "Alpha " * 30 + "beta."
    sentence2 = "Gamma " * 30 + "delta."
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text=f"{sentence1} {sentence2}")])
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    calls = []

    def pause_after_first_chunk(done, total):
        calls.append((done, total))
        if done == 1:
            raise pipeline.GenerationPaused()

    with pytest.raises(pipeline.GenerationPaused):
        synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
                            on_chunk_done=pause_after_first_chunk)

    assert calls == [(1, 2)]
    assert db.get_chapter(chapter.id).status == ChapterStatus.GENERATING

    class CountingEngine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.call_count = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.call_count += 1
            return super().synthesize(text_chunk, profile_handle, params)

    second_engine = CountingEngine()
    second_engine.load("cpu")
    second_profile_handle = second_engine.clone_profile(None, None, {})

    result = synthesize_chapter(chapter, book, second_engine, second_profile_handle,
                                 {"voice_name": "fake"}, [], db)

    assert result.status == ChapterStatus.DONE
    assert second_engine.call_count == 1  # only the second (uncached) chunk needed synthesis
    db.close()


@requires_ffmpeg
def test_synthesize_chapter_resumes_cleanly_when_the_cache_is_already_fully_populated(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="T", author=None, source_path=str(tmp_path / "b.md"),
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    sentence1 = "Alpha " * 30 + "beta."
    sentence2 = "Gamma " * 30 + "delta."
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text=f"{sentence1} {sentence2}")])
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    def pause_after_last_chunk(done, total):
        if done == total:
            raise pipeline.GenerationPaused()

    with pytest.raises(pipeline.GenerationPaused):
        synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
                            on_chunk_done=pause_after_last_chunk)

    class CountingEngine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.call_count = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.call_count += 1
            return super().synthesize(text_chunk, profile_handle, params)

    second_engine = CountingEngine()
    second_engine.load("cpu")
    second_profile_handle = second_engine.clone_profile(None, None, {})

    result = synthesize_chapter(chapter, book, second_engine, second_profile_handle,
                                 {"voice_name": "fake"}, [], db)

    assert result.status == ChapterStatus.DONE
    assert second_engine.call_count == 0  # every chunk was already cached, nothing to re-synthesize
    db.close()


@requires_ffmpeg
def test_synthesize_chapter_discards_stale_cache_when_the_lexicon_changes(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    [chapter] = db.list_chapters(book.id)
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    def raise_paused(done, total):
        raise pipeline.GenerationPaused()

    with pytest.raises(pipeline.GenerationPaused):
        synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
                            on_chunk_done=raise_paused)

    db.create_lexicon_entry(term="chapter", override_value="CHAP-ter", scope="global")
    lexicon_entries = db.list_lexicon_entries(book.id)

    class CountingEngine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.call_count = 0

        def synthesize(self, text_chunk, profile_handle, params):
            self.call_count += 1
            return super().synthesize(text_chunk, profile_handle, params)

    second_engine = CountingEngine()
    second_engine.load("cpu")
    second_profile_handle = second_engine.clone_profile(None, None, {})

    result = synthesize_chapter(chapter, book, second_engine, second_profile_handle,
                                 {"voice_name": "fake"}, lexicon_entries, db)

    assert result.status == ChapterStatus.DONE
    assert second_engine.call_count == 1  # the stale-key cache was discarded, not reused
    db.close()


@requires_ffmpeg
def test_synthesize_chapter_deletes_cache_dir_after_successful_export(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    [chapter] = db.list_chapters(book.id)
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    result = synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db)

    assert result.status == ChapterStatus.DONE
    assert not chunk_cache.chapter_cache_dir(chapter.id).exists()
    db.close()


def test_synthesize_chapter_reraises_generation_paused_instead_of_marking_failed(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    [chapter] = db.list_chapters(book.id)
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    def raise_paused(done, total):
        raise pipeline.GenerationPaused()

    with pytest.raises(pipeline.GenerationPaused):
        synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
                            on_chunk_done=raise_paused)

    assert db.get_chapter(chapter.id).status != ChapterStatus.FAILED
    db.close()


def test_synthesize_chapter_reraises_generation_cancelled_instead_of_marking_failed(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = _make_book(tmp_path, db, n_chapters=1)
    [chapter] = db.list_chapters(book.id)
    engine = FakeEngine()
    engine.load("cpu")
    profile_handle = engine.clone_profile(None, None, {})

    def raise_cancelled(done, total):
        raise pipeline.GenerationCancelled()

    with pytest.raises(pipeline.GenerationCancelled):
        synthesize_chapter(chapter, book, engine, profile_handle, {"voice_name": "fake"}, [], db,
                            on_chunk_done=raise_cancelled)

    assert db.get_chapter(chapter.id).status != ChapterStatus.FAILED
    db.close()

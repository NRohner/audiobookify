import shutil

import pytest

from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine
from audiobookify.ingest.base import RawChapter
from audiobookify.models import ChapterStatus, JobStatus, SourceFormat
from audiobookify.worker import EngineSlot, run_worker_once


def _slot(engine):
    """A worker engine slot that always hands out this engine."""
    return EngineSlot(lambda name: engine)

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

    assert run_worker_once(db, _slot(engine)) is False
    db.close()


@requires_ffmpeg
def test_run_worker_once_claims_job_and_marks_chapter_done(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter, job = _make_book_with_job(tmp_path, db)
    engine = FakeEngine()
    engine.load("cpu")

    did_work = run_worker_once(db, _slot(engine))

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

    run_worker_once(db, _slot(engine))

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

    run_worker_once(db, _slot(engine))

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

    did_work = run_worker_once(db, _slot(engine))

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
    slot = _slot(engine)
    run_worker_once(db, slot)
    run_worker_once(db, slot)

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
    slot = _slot(engine)
    run_worker_once(db, slot)  # book_a, no profile
    run_worker_once(db, slot)  # book_b, profile "Nova"

    assert len(clone_calls) == 2
    assert clone_calls[1] == str(tmp_path / "ref.wav")
    db.close()


def test_run_worker_once_returns_false_when_paused(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    _, chapter, job = _make_book_with_job(tmp_path, db)
    db.set_paused(True)
    engine = FakeEngine()
    engine.load("cpu")

    assert run_worker_once(db, _slot(engine)) is False
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

    did_work = run_worker_once(db, _slot(engine))

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

    did_work = run_worker_once(db, _slot(engine))

    assert did_work is True
    assert db.get_job(job.id).status == JobStatus.CANCELLED
    assert db.get_chapter(chapter.id).status == ChapterStatus.PENDING
    assert not chunk_cache.chapter_cache_dir(chapter.id).exists()
    db.close()


def _voice(db, tmp_path, **kw):
    import numpy as np
    import soundfile as sf

    ref = tmp_path / "ref.wav"
    sf.write(ref, np.zeros(24000 * 20, dtype=np.float32), 24000)
    return db.create_voice_profile(name="Nova", engine="chatterbox-turbo", reference_audio_path=str(ref),
                                   exaggeration=0.5, cfg_weight=0.5, temperature=0.8,
                                   **{"reference_transcript": None, **kw})


_WORDS = [{"word": " One", "start": 0.0, "end": 10.0}, {"word": " two.", "start": 10.1, "end": 14.0},
          {"word": " Three", "start": 14.2, "end": 18.0}]


def _fake_whisper(monkeypatch, result=None, error=None):
    from audiobookify.voice import analysis
    calls = []

    def transcribe(path):
        calls.append(path)
        if error:
            raise error
        return result or {"text": "One two. Three", "words": _WORDS}
    monkeypatch.setattr(analysis, "transcribe", transcribe)
    return calls


def test_worker_transcribes_pending_voices_and_trims_long_clips(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "test.db"))
    voice = _voice(db, tmp_path, transcript_status="pending")
    calls = _fake_whisper(monkeypatch)

    assert run_worker_once(db, _slot(FakeEngine())) is True

    saved = db.get_voice_profile_by_id(voice.id)
    assert calls == [voice.reference_audio_path]
    assert saved.transcript_status == "done" and saved.reference_words == _WORDS
    assert (saved.reference_transcript, saved.reference_trim_end) == ("One two.", 14.15)
    assert run_worker_once(db, _slot(FakeEngine())) is False
    db.close()


def test_worker_records_a_failed_transcription_instead_of_retrying_forever(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "test.db"))
    voice = _voice(db, tmp_path, transcript_status="pending")
    _fake_whisper(monkeypatch, error=RuntimeError("no speech found"))

    run_worker_once(db, _slot(FakeEngine()))

    saved = db.get_voice_profile_by_id(voice.id)
    assert (saved.transcript_status, saved.transcript_error) == ("failed", "no speech found")
    db.close()


class _TranscriptEngine(FakeEngine):
    needs_transcript = True

    def clone_profile(self, reference_audio, reference_text, params):
        self.cloned = (reference_audio, reference_text)
        return super().clone_profile(reference_audio, reference_text, params)


def test_worker_transcribes_a_voice_lazily_when_the_engine_needs_a_transcript(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "test.db"))
    voice = _voice(db, tmp_path)  # created before transcripts existed
    calls = _fake_whisper(monkeypatch)
    clip = db.create_text_clip("A sentence long enough to be spoken.", voice.id)
    engine = _TranscriptEngine()

    run_worker_once(db, _slot(engine), text_clip_output_dir=str(tmp_path / "clips"))

    assert calls == [voice.reference_audio_path]
    ref_audio, ref_text = engine.cloned
    assert ref_text == "One two." and ref_audio.endswith(".trim-14.15s.wav")  # trimmed clip + its words
    assert db.get_voice_profile_by_id(voice.id).transcript_status == "done"
    db.close()


@requires_ffmpeg
def test_a_text_clip_tempo_overrides_the_voice_tempo_for_that_clip_only(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    voice = _voice(db, tmp_path)
    db.set_voice_tempo(voice.id, 1.1)
    db.create_text_clip("A sentence long enough to be spoken.", voice.id, tempo=0.9)
    db.create_text_clip("A sentence long enough to be spoken.", voice.id)
    engine = _TranscriptEngine()
    engine.needs_transcript = False

    run_worker_once(db, _slot(engine), text_clip_output_dir=str(tmp_path / "clips"))
    assert engine.cloned[0].endswith(".tempo-0.90.wav")  # the sample's draft tempo
    run_worker_once(db, _slot(engine), text_clip_output_dir=str(tmp_path / "clips"))
    assert engine.cloned[0].endswith(".tempo-1.10.wav")  # the voice's saved tempo
    db.close()


def test_worker_does_not_transcribe_for_engines_that_clone_from_audio_alone(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "test.db"))
    voice = _voice(db, tmp_path)
    calls = _fake_whisper(monkeypatch)
    db.create_text_clip("A sentence long enough to be spoken.", voice.id)

    run_worker_once(db, _slot(FakeEngine()), text_clip_output_dir=str(tmp_path / "clips"))

    assert calls == []
    db.close()


class _Recording(FakeEngine):
    def __init__(self, name, log):
        super().__init__()
        self.name, self.log = name, log

    def load(self, device="auto"):
        self.log.append(("load", self.name))

    def teardown(self):
        self.log.append(("teardown", self.name))


def test_engine_slot_holds_one_engine_and_tears_it_down_before_loading_another():
    from audiobookify.worker import EngineSlot

    log = []
    slot = EngineSlot(lambda name: _Recording(name, log))

    a = slot.get("chatterbox-turbo")
    assert slot.get("chatterbox-turbo") is a  # reused, not reloaded
    slot.profile_cache["key"] = "stale"
    b = slot.get("qwen3")
    slot.teardown()

    assert b.name == "qwen3"
    assert log == [("load", "chatterbox-turbo"), ("teardown", "chatterbox-turbo"),
                   ("load", "qwen3"), ("teardown", "qwen3")]
    assert slot.profile_cache == {}  # handles belong to the engine that made them


def test_engine_slot_loads_nothing_until_a_job_needs_an_engine(tmp_path):
    from audiobookify.worker import EngineSlot

    log = []
    db = Database(str(tmp_path / "test.db"))
    assert run_worker_once(db, EngineSlot(lambda name: _Recording(name, log))) is False
    assert log == []
    db.close()


def test_worker_uses_each_jobs_stored_engine(tmp_path, monkeypatch):
    from audiobookify.worker import EngineSlot

    db = Database(str(tmp_path / "test.db"))
    book, chapter, _ = _make_book_with_job(tmp_path, db)
    db.update_book_engine(book.id, "qwen3")
    db.create_text_clip("A sentence long enough to be spoken.", None, engine="chatterbox-turbo")
    log = []
    slot = EngineSlot(lambda name: _Recording(name, log))
    monkeypatch.setattr("audiobookify.worker.synthesize_chapter", lambda chapter, book, engine, *a, **k: (
        log.append(("chapter", engine.name)) or db.get_chapter(chapter.id)))
    monkeypatch.setattr("audiobookify.worker.synthesize_text_clip", lambda clip, engine, *a, **k: (
        log.append(("clip", engine.name)) or db.finish_text_clip(clip.id, JobStatus.DONE, output_path="/x.mp3")))

    run_worker_once(db, slot, text_clip_output_dir=str(tmp_path / "clips"))
    run_worker_once(db, slot, text_clip_output_dir=str(tmp_path / "clips"))

    assert log == [("load", "qwen3"), ("chapter", "qwen3"),
                   ("teardown", "qwen3"), ("load", "chatterbox-turbo"), ("clip", "chatterbox-turbo")]
    db.close()

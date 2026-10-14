import sys
import time

from .db import Database
from .engines import ENGINES
from .models import ChapterStatus, JobStatus
from .synth.chunk_cache import clear_cache
from .synth.pipeline import (
    GenerationCancelled, GenerationPaused, synthesize_chapter, synthesize_text_clip,
)
from .voice import analysis
from .voice.validation import params_from_profile

DEFAULT_TEXT_CLIP_OUTPUT_DIR = "text_clips"


def _voice_params_for_profile(profile) -> dict:
    voice_params = {"voice_name": profile.name if profile else "default"}
    if profile:
        voice_params.update(params_from_profile(profile))
    return voice_params


class EngineSlot:
    """At most one loaded TTS engine: Qwen3 (~12 GB) and Turbo (~5 GB) together
    don't fit comfortably in 18 GB, and MLX reloads take only seconds."""

    def __init__(self, make=lambda name: ENGINES[name].cls()):
        self._make = make
        self.name = None
        self.engine = None
        self.profile_cache: dict = {}  # voice handles belong to the engine that made them

    def get(self, name: str):
        if name != self.name:
            self.teardown()
            print(f"Loading TTS engine '{name}'", file=sys.stderr)
            engine = self._make(name)
            engine.load()
            self.name, self.engine = name, engine
        return self.engine

    def teardown(self) -> None:
        engine = self.engine
        # Drop voice handles first: Turbo's hold GPU arrays the engine's teardown should free.
        self.name, self.engine, self.profile_cache = None, None, {}
        if engine is not None:
            engine.teardown()


def transcribe_voice(db: Database, profile) -> None:
    """One Whisper pass: transcript, word timings, and the reference trim."""
    try:
        result = analysis.transcribe(profile.reference_audio_path)
        cut = analysis.trim_point(result["words"])
        trim_end, text = cut if cut else (None, result["text"])
        db.save_voice_transcript(profile.id, text, words=result["words"], trim_end=trim_end)
    except Exception as exc:
        db.fail_voice_transcription(profile.id, str(exc))


def load_voice(db: Database, voice_profile_id, engine):
    """The voice profile, transcribed first if this engine needs a transcript and
    the voice has none yet (voices made before transcripts existed)."""
    profile = db.get_voice_profile_by_id(voice_profile_id) if voice_profile_id else None
    if profile and engine.needs_transcript and not (profile.reference_transcript or "").strip():
        transcribe_voice(db, profile)
        profile = db.get_voice_profile_by_id(voice_profile_id)
    return profile


def _ensure_profile_cached(engine, profile_cache: dict, voice_profile_id, voice_params: dict) -> None:
    # Keyed on the params too, so an edited transcript or trim takes effect.
    key = (voice_profile_id, voice_params.get("reference_audio"), voice_params.get("reference_text"))
    if profile_cache.get("key") != key:
        profile_cache["key"] = key
        profile_cache["handle"] = engine.clone_profile(
            voice_params.get("reference_audio"), voice_params.get("reference_text"), voice_params)


def _process_next_transcription(db: Database) -> bool:
    profile = db.next_pending_transcription()
    if profile is None:
        return False
    transcribe_voice(db, profile)
    return True


def _process_next_chapter_job(db: Database, engines: EngineSlot) -> bool:
    job = db.pop_next_queued_job()
    if job is None:
        return False

    try:
        chapter = db.get_chapter(job.chapter_id)
        book = db.get_book(chapter.book_id)
        engine, profile_cache = engines.get(book.engine), engines.profile_cache
        voice_params = _voice_params_for_profile(load_voice(db, book.voice_profile_id, engine))
        _ensure_profile_cached(engine, profile_cache, book.voice_profile_id, voice_params)

        lexicon_entries = db.list_lexicon_entries(book.id)

        def on_chunk_done(done: int, total: int) -> None:
            db.update_job_progress(job.id, done / total)
            if db.is_paused():
                raise GenerationPaused()
            if db.is_job_cancel_requested(job.id):
                raise GenerationCancelled()

        result = synthesize_chapter(chapter, book, engine, profile_cache["handle"], voice_params,
                                     lexicon_entries, db, on_chunk_done=on_chunk_done)

        status = JobStatus.DONE if result.status == ChapterStatus.DONE else JobStatus.FAILED
        db.finish_job(job.id, status, error=result.error)
    except GenerationPaused:
        db.update_chapter_status(job.chapter_id, ChapterStatus.PENDING)
        db.requeue_job(job.id)
    except GenerationCancelled:
        db.update_chapter_status(job.chapter_id, ChapterStatus.PENDING)
        db.finish_job(job.id, JobStatus.CANCELLED)
        clear_cache(job.chapter_id)
    except Exception as exc:
        db.finish_job(job.id, JobStatus.FAILED, error=str(exc))

    return True


def _process_next_text_clip(db: Database, engines: EngineSlot, output_dir: str) -> bool:
    clip = db.pop_next_queued_text_clip()
    if clip is None:
        return False

    try:
        engine, profile_cache = engines.get(clip.engine), engines.profile_cache
        voice_params = _voice_params_for_profile(load_voice(db, clip.voice_profile_id, engine))
        _ensure_profile_cached(engine, profile_cache, clip.voice_profile_id, voice_params)

        lexicon_entries = db.list_lexicon_entries()

        def on_chunk_done(done: int, total: int) -> None:
            db.update_text_clip_progress(clip.id, done / total)

        synthesize_text_clip(clip, engine, profile_cache["handle"], voice_params, lexicon_entries,
                              db, output_dir, on_chunk_done=on_chunk_done)
    except Exception as exc:
        db.finish_text_clip(clip.id, JobStatus.FAILED, error=str(exc))

    return True


def run_worker_once(db: Database, engines: EngineSlot,
                     text_clip_output_dir: str = DEFAULT_TEXT_CLIP_OUTPUT_DIR) -> bool:
    if db.is_paused():
        return False
    if _process_next_transcription(db):  # seconds each, and the Voices page is waiting on it
        return True
    if _process_next_chapter_job(db, engines):
        return True
    return _process_next_text_clip(db, engines, text_clip_output_dir)


def run_forever(db: Database, engines: EngineSlot, poll_interval: float = 2.0,
                 text_clip_output_dir: str = DEFAULT_TEXT_CLIP_OUTPUT_DIR) -> None:
    db.reset_stale_generating_jobs()
    db.reset_stale_generating_text_clips()
    while True:
        try:
            did_work = run_worker_once(db, engines, text_clip_output_dir)
        except Exception as exc:
            print(f"Error: worker loop iteration failed: {exc}", file=sys.stderr)
            did_work = False
        if not did_work:
            time.sleep(poll_interval)

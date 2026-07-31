import sys
import time

from .db import Database
from .models import ChapterStatus, JobStatus
from .synth.chunk_cache import clear_cache
from .synth.pipeline import (
    GenerationCancelled, GenerationPaused, synthesize_chapter, synthesize_text_clip,
)
from .voice.validation import params_from_profile

DEFAULT_TEXT_CLIP_OUTPUT_DIR = "text_clips"


def _voice_params_for_profile(profile) -> dict:
    voice_params = {"voice_name": profile.name if profile else "default"}
    if profile:
        voice_params.update(params_from_profile(profile))
    return voice_params


def _voice_params_for_book(db: Database, book) -> tuple:
    profile = db.get_voice_profile_by_id(book.voice_profile_id) if book.voice_profile_id else None
    return _voice_params_for_profile(profile), profile


def _ensure_profile_cached(engine, profile_cache: dict, voice_profile_id, profile, voice_params: dict) -> None:
    if "voice_profile_id" not in profile_cache or profile_cache["voice_profile_id"] != voice_profile_id:
        reference_audio = profile.reference_audio_path if profile else None
        reference_text = profile.reference_transcript if profile else None
        profile_cache["voice_profile_id"] = voice_profile_id
        profile_cache["handle"] = engine.clone_profile(reference_audio, reference_text, voice_params)


def _process_next_chapter_job(db: Database, engine, profile_cache: dict) -> bool:
    job = db.pop_next_queued_job()
    if job is None:
        return False

    try:
        chapter = db.get_chapter(job.chapter_id)
        book = db.get_book(chapter.book_id)
        voice_params, profile = _voice_params_for_book(db, book)
        _ensure_profile_cached(engine, profile_cache, book.voice_profile_id, profile, voice_params)

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


def _process_next_text_clip(db: Database, engine, profile_cache: dict, output_dir: str) -> bool:
    clip = db.pop_next_queued_text_clip()
    if clip is None:
        return False

    try:
        profile = db.get_voice_profile_by_id(clip.voice_profile_id) if clip.voice_profile_id else None
        voice_params = _voice_params_for_profile(profile)
        _ensure_profile_cached(engine, profile_cache, clip.voice_profile_id, profile, voice_params)

        lexicon_entries = db.list_lexicon_entries()

        def on_chunk_done(done: int, total: int) -> None:
            db.update_text_clip_progress(clip.id, done / total)

        synthesize_text_clip(clip, engine, profile_cache["handle"], voice_params, lexicon_entries,
                              db, output_dir, on_chunk_done=on_chunk_done)
    except Exception as exc:
        db.finish_text_clip(clip.id, JobStatus.FAILED, error=str(exc))

    return True


def run_worker_once(db: Database, engine, profile_cache: dict,
                     text_clip_output_dir: str = DEFAULT_TEXT_CLIP_OUTPUT_DIR) -> bool:
    if db.is_paused():
        return False
    if _process_next_chapter_job(db, engine, profile_cache):
        return True
    return _process_next_text_clip(db, engine, profile_cache, text_clip_output_dir)


def run_forever(db: Database, engine, poll_interval: float = 2.0,
                 text_clip_output_dir: str = DEFAULT_TEXT_CLIP_OUTPUT_DIR) -> None:
    db.reset_stale_generating_jobs()
    db.reset_stale_generating_text_clips()
    profile_cache: dict = {}
    while True:
        try:
            did_work = run_worker_once(db, engine, profile_cache, text_clip_output_dir)
        except Exception as exc:
            print(f"Error: worker loop iteration failed: {exc}", file=sys.stderr)
            did_work = False
        if not did_work:
            time.sleep(poll_interval)

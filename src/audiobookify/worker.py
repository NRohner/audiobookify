import time
from typing import Optional

from .db import Database
from .models import ChapterStatus, JobStatus
from .synth.pipeline import synthesize_chapter
from .voice.validation import params_from_profile


def _voice_params_for_book(db: Database, book) -> tuple:
    profile = db.get_voice_profile_by_id(book.voice_profile_id) if book.voice_profile_id else None
    voice_params = {"voice_name": profile.name if profile else "default"}
    if profile:
        voice_params.update(params_from_profile(profile))
    return voice_params, profile


def run_worker_once(db: Database, engine, profile_cache: dict) -> bool:
    job = db.pop_next_queued_job()
    if job is None:
        return False

    chapter = db.get_chapter(job.chapter_id)
    book = db.get_book(chapter.book_id)
    voice_params, profile = _voice_params_for_book(db, book)

    if "voice_profile_id" not in profile_cache or profile_cache["voice_profile_id"] != book.voice_profile_id:
        reference_audio = profile.reference_audio_path if profile else None
        reference_text = profile.reference_transcript if profile else None
        profile_cache["voice_profile_id"] = book.voice_profile_id
        profile_cache["handle"] = engine.clone_profile(reference_audio, reference_text, voice_params)

    lexicon_entries = db.list_lexicon_entries(book.id)

    def on_chunk_done(done: int, total: int) -> None:
        db.update_job_progress(job.id, done / total)

    result = synthesize_chapter(chapter, book, engine, profile_cache["handle"], voice_params,
                                 lexicon_entries, db, on_chunk_done=on_chunk_done)

    status = JobStatus.DONE if result.status == ChapterStatus.DONE else JobStatus.FAILED
    db.finish_job(job.id, status, error=result.error)
    return True


def run_forever(db: Database, engine, poll_interval: float = 2.0) -> None:
    db.reset_stale_generating_jobs()
    profile_cache: dict = {}
    while True:
        did_work = run_worker_once(db, engine, profile_cache)
        if not did_work:
            time.sleep(poll_interval)

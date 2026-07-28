from typing import List

from ..db import Database
from ..models import Book, Chapter, ChapterStatus
from ..normalize.text import chunk_text
from ..postprocess.audio import concatenate_chunks, export_chapter_mp3


def generate_book(book: Book, engine, db: Database, voice_params: dict, device: str = "auto") -> List[Chapter]:
    results = []
    try:
        engine.load(device)
        profile_handle = engine.clone_profile(
            voice_params.get("reference_audio"), voice_params.get("reference_text"), voice_params
        )

        for chapter in db.list_chapters(book.id):
            if chapter.status == ChapterStatus.DONE:
                results.append(chapter)
                continue

            db.update_chapter_status(chapter.id, ChapterStatus.GENERATING)
            try:
                chunks = chunk_text(chapter.text, engine.max_chunk_chars())
                audio_chunks = [engine.synthesize(c, profile_handle, voice_params) for c in chunks]
                combined = concatenate_chunks(audio_chunks, engine.sample_rate())
                mp3_path = export_chapter_mp3(
                    combined, engine.sample_rate(), book.output_dir, chapter.order_index,
                    chapter.title, book.title, voice_params.get("voice_name", engine.name),
                )
                db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=mp3_path)
            except Exception as exc:
                db.update_chapter_status(chapter.id, ChapterStatus.FAILED, error=str(exc))

            results.append(db.get_chapter(chapter.id))
    finally:
        engine.teardown()

    return results

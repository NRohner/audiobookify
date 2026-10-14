from pathlib import Path
from typing import Callable, List, Optional

from ..db import Database
from ..lexicon.substitution import apply_lexicon
from ..models import Book, Chapter, ChapterStatus, JobStatus, TextClip
from ..normalize.text import chunk_text
from ..postprocess.audio import BACKUP_OUTPUT_DIR, concatenate_chunks, export_chapter_mp3, export_mp3
from .chunk_cache import clear_cache, compute_cache_key, load_cached_chunks, save_chunk, write_manifest


class GenerationPaused(Exception):
    pass


class GenerationCancelled(Exception):
    pass


def synthesize_chapter(chapter: Chapter, book: Book, engine, profile_handle, voice_params: dict,
                        lexicon_entries: list, db: Database,
                        on_chunk_done: Optional[Callable[[int, int], None]] = None) -> Chapter:
    db.update_chapter_status(chapter.id, ChapterStatus.GENERATING)
    try:
        text = apply_lexicon(chapter.text, lexicon_entries)
        chunks = chunk_text(text, engine.max_chunk_chars(), engine.min_chunk_chars())
        total = len(chunks)

        # The engine is part of the key: same text and voice from another engine is different audio.
        cache_key = compute_cache_key([c.text for c in chunks], {**voice_params, "engine": engine.name})
        audio_chunks = load_cached_chunks(chapter.id, cache_key)
        if not audio_chunks:
            clear_cache(chapter.id)
            write_manifest(chapter.id, cache_key)

        for i in range(len(audio_chunks), total):
            chunk_audio = engine.synthesize(chunks[i].text, profile_handle, voice_params)
            save_chunk(chapter.id, i, chunk_audio, engine.sample_rate())
            audio_chunks.append(chunk_audio)
            if on_chunk_done is not None:
                on_chunk_done(i + 1, total)

        combined = concatenate_chunks(audio_chunks, [c.ends_with for c in chunks], engine.sample_rate())
        mp3_path, used_fallback = export_chapter_mp3(
            combined, engine.sample_rate(), book.output_dir, chapter.order_index,
            chapter.title, book.title, voice_params.get("voice_name", engine.name),
            fallback_dir=str(Path(BACKUP_OUTPUT_DIR) / str(book.id)),
        )
        db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path=mp3_path,
                                  used_fallback=used_fallback, engine=engine.name)
        clear_cache(chapter.id)
    except (GenerationPaused, GenerationCancelled):
        raise
    except Exception as exc:
        db.update_chapter_status(chapter.id, ChapterStatus.FAILED, error=str(exc))

    return db.get_chapter(chapter.id)


def synthesize_text_clip(clip: TextClip, engine, profile_handle, voice_params: dict,
                          lexicon_entries: list, db: Database, output_dir: str,
                          on_chunk_done: Optional[Callable[[int, int], None]] = None) -> TextClip:
    try:
        text = apply_lexicon(clip.text, lexicon_entries)
        chunks = chunk_text(text, engine.max_chunk_chars(), engine.min_chunk_chars())
        total = len(chunks)
        audio_chunks = []
        for i, c in enumerate(chunks):
            audio_chunks.append(engine.synthesize(c.text, profile_handle, voice_params))
            if on_chunk_done is not None:
                on_chunk_done(i + 1, total)
        combined = concatenate_chunks(audio_chunks, [c.ends_with for c in chunks], engine.sample_rate())
        output_path = str(Path(output_dir) / f"text-clip-{clip.id}.mp3")
        mp3_path, used_fallback = export_mp3(
            combined, engine.sample_rate(), output_path,
            title=f"Text clip #{clip.id}", artist=voice_params.get("voice_name", engine.name),
            fallback_dir=str(Path(BACKUP_OUTPUT_DIR) / "text-clips"),
        )
        db.finish_text_clip(clip.id, JobStatus.DONE, output_path=mp3_path, used_fallback=used_fallback)
    except Exception as exc:
        db.finish_text_clip(clip.id, JobStatus.FAILED, error=str(exc))

    return db.get_text_clip(clip.id)


def generate_book(book: Book, engine, db: Database, voice_params: dict, device: str = "auto") -> List[Chapter]:
    results = []
    try:
        engine.load(device)
        profile_handle = engine.clone_profile(
            voice_params.get("reference_audio"), voice_params.get("reference_text"), voice_params
        )
        lexicon_entries = db.list_lexicon_entries(book.id)

        for chapter in db.list_chapters(book.id):
            if chapter.status == ChapterStatus.DONE:
                results.append(chapter)
                continue
            results.append(
                synthesize_chapter(chapter, book, engine, profile_handle, voice_params,
                                    lexicon_entries, db)
            )
    finally:
        engine.teardown()

    return results

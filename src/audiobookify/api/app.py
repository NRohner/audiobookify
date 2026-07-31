import os
import shutil
import uuid
from pathlib import Path

from typing import Optional

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..db import Database
from ..ingest import ingest_by_path
from ..models import ChapterStatus, JobStatus
from ..synth.chunk_cache import clear_cache
from ..voice.storage import delete_voice_storage, store_reference_audio
from ..voice.validation import validate_reference_audio


def _check_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".audiobookify_write_test"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def _cancel_active_job_for_chapter(db: Database, chapter) -> None:
    latest = db.latest_job_for_chapter(chapter.id)
    if latest is None or latest.status not in (JobStatus.QUEUED, JobStatus.GENERATING):
        return
    if latest.status == JobStatus.QUEUED:
        db.finish_job(latest.id, JobStatus.CANCELLED)
        db.update_chapter_status(chapter.id, ChapterStatus.PENDING)
        clear_cache(chapter.id)
    else:
        db.request_job_cancel(latest.id)


def create_app(db_path: str = "audiobookify.db", upload_dir: str = "uploads",
                frontend_dist: Optional[str] = None) -> FastAPI:
    app = FastAPI()
    Path(upload_dir).mkdir(parents=True, exist_ok=True)

    def get_db():
        db = Database(db_path)
        try:
            yield db
        finally:
            db.close()

    def _voice_to_dict(profile, warnings: Optional[list] = None) -> dict:
        return {
            "id": profile.id,
            "name": profile.name,
            "engine": profile.engine,
            "reference_transcript": profile.reference_transcript,
            "exaggeration": profile.exaggeration,
            "cfg_weight": profile.cfg_weight,
            "temperature": profile.temperature,
            "warnings": warnings or [],
        }

    def _lexicon_to_dict(entry) -> dict:
        return {
            "term": entry.term,
            "override_value": entry.override_value,
            "notes": entry.notes,
        }

    def _text_clip_to_dict(clip) -> dict:
        return {
            "id": clip.id,
            "text": clip.text,
            "voice_profile_id": clip.voice_profile_id,
            "status": clip.status.value,
            "progress": clip.progress,
            "error": clip.error,
            "created_at": clip.created_at,
            "used_fallback": clip.used_fallback,
        }

    def _book_to_dict(db: Database, book) -> dict:
        chapters = []
        for chapter in db.list_chapters(book.id):
            job = db.latest_job_for_chapter(chapter.id)
            active_job = job if job and job.status in (JobStatus.QUEUED, JobStatus.GENERATING) else None
            chapters.append({
                "id": chapter.id,
                "order_index": chapter.order_index,
                "title": chapter.title,
                "status": chapter.status.value,
                "output_mp3_path": chapter.output_mp3_path,
                "error": chapter.error,
                "progress": active_job.progress if active_job else None,
                "used_fallback": chapter.used_fallback,
            })
        return {
            "id": book.id,
            "title": book.title,
            "author": book.author,
            "output_dir": book.output_dir,
            "voice_profile_id": book.voice_profile_id,
            "chapters": chapters,
        }

    @app.post("/api/books")
    def create_book(file: UploadFile = File(...), output_dir: str = Form(...),
                     db: Database = Depends(get_db)):
        filename = Path(file.filename).name
        suffix = Path(filename).suffix.lower()
        if suffix not in (".epub", ".md"):
            raise HTTPException(status_code=400, detail=f"unsupported input format: {suffix}")

        resolved_output_dir = Path(output_dir.strip()).resolve()
        if not _check_writable(resolved_output_dir):
            raise HTTPException(
                status_code=400,
                detail=f"cannot write to '{resolved_output_dir}' — check the folder exists "
                        "and you have permission to write to it",
            )

        staged_dir = Path(upload_dir) / uuid.uuid4().hex
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_path = staged_dir / filename
        staged_path.write_bytes(file.file.read())

        try:
            parsed, source_format = ingest_by_path(str(staged_path.resolve()))
        except ValueError as exc:
            shutil.rmtree(staged_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc))

        book = db.create_book(
            title=parsed.title, author=parsed.author, source_path=str(staged_path.resolve()),
            source_format=source_format, output_dir=str(resolved_output_dir),
        )
        db.add_chapters(book.id, parsed.chapters)

        return JSONResponse(_book_to_dict(db, book))

    @app.get("/api/books")
    def list_books(db: Database = Depends(get_db)):
        return [_book_to_dict(db, b) for b in db.list_books()]

    @app.get("/api/books/{book_id}")
    def get_book(book_id: int, db: Database = Depends(get_db)):
        try:
            book = db.get_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="book not found")
        return _book_to_dict(db, book)

    @app.get("/api/browse-dir")
    def browse_dir(path: Optional[str] = None):
        current = Path(path).expanduser().resolve() if path else Path.home().resolve()
        if not current.is_dir():
            raise HTTPException(status_code=404, detail=f"not a directory: {current}")

        try:
            children = sorted(current.iterdir())
        except PermissionError:
            children = []

        entries = [
            {"name": child.name, "writable": os.access(child, os.W_OK)}
            for child in children if child.is_dir()
        ]
        parent = str(current.parent) if current.parent != current else None

        return {
            "path": str(current),
            "parent": parent,
            "writable": _check_writable(current),
            "entries": entries,
        }

    @app.get("/api/validate-output-dir")
    def validate_output_dir(path: str):
        resolved = Path(path).expanduser().resolve()
        existing = resolved
        while not existing.exists():
            parent = existing.parent
            if parent == existing:
                break
            existing = parent
        writable = existing.is_dir() and os.access(existing, os.W_OK | os.X_OK)
        if writable:
            return {"path": str(resolved), "writable": True, "error": None}
        return {
            "path": str(resolved), "writable": False,
            "error": f"cannot write to '{resolved}' — check the folder exists and you have permission",
        }

    @app.post("/api/books/{book_id}/generate")
    def generate(book_id: int, db: Database = Depends(get_db)):
        try:
            book = db.get_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="book not found")

        queued_job_ids = []
        for chapter in db.list_chapters(book.id):
            if chapter.status == ChapterStatus.DONE:
                continue
            latest = db.latest_job_for_chapter(chapter.id)
            if latest is not None and latest.status in (JobStatus.QUEUED, JobStatus.GENERATING):
                continue
            job = db.create_job(chapter.id)
            queued_job_ids.append(job.id)

        return {"queued_job_ids": queued_job_ids}

    @app.post("/api/books/{book_id}/chapters/{chapter_id}/generate")
    def generate_chapter(book_id: int, chapter_id: int, db: Database = Depends(get_db)):
        try:
            db.get_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="book not found")

        try:
            chapter = db.get_chapter(chapter_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="chapter not found")
        if chapter.book_id != book_id:
            raise HTTPException(status_code=404, detail="chapter not found")

        if chapter.status == ChapterStatus.DONE:
            return {"job_id": None}
        latest = db.latest_job_for_chapter(chapter.id)
        if latest is not None and latest.status in (JobStatus.QUEUED, JobStatus.GENERATING):
            return {"job_id": None}

        job = db.create_job(chapter.id)
        return {"job_id": job.id}

    @app.get("/api/worker/status")
    def worker_status(db: Database = Depends(get_db)):
        return {"paused": db.is_paused()}

    @app.post("/api/worker/pause")
    def worker_pause(db: Database = Depends(get_db)):
        db.set_paused(True)
        return {"paused": True}

    @app.post("/api/worker/resume")
    def worker_resume(db: Database = Depends(get_db)):
        db.set_paused(False)
        return {"paused": False}

    @app.post("/api/books/{book_id}/chapters/{chapter_id}/cancel")
    def cancel_chapter(book_id: int, chapter_id: int, db: Database = Depends(get_db)):
        try:
            db.get_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="book not found")

        try:
            chapter = db.get_chapter(chapter_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="chapter not found")
        if chapter.book_id != book_id:
            raise HTTPException(status_code=404, detail="chapter not found")

        _cancel_active_job_for_chapter(db, chapter)
        return {"ok": True}

    @app.post("/api/books/{book_id}/cancel")
    def cancel_book(book_id: int, db: Database = Depends(get_db)):
        try:
            book = db.get_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="book not found")

        for chapter in db.list_chapters(book.id):
            _cancel_active_job_for_chapter(db, chapter)
        return {"ok": True}

    @app.patch("/api/books/{book_id}/voice")
    def assign_voice(book_id: int, payload: dict = Body(...), db: Database = Depends(get_db)):
        try:
            db.get_book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="book not found")
        db.update_book_voice_profile(book_id, payload.get("voice_profile_id"))
        return {"ok": True}

    @app.post("/api/voices")
    def create_voice(file: UploadFile = File(...), name: str = Form(...),
                      reference_transcript: Optional[str] = Form(None),
                      exaggeration: float = Form(0.5), cfg_weight: float = Form(0.5),
                      temperature: float = Form(0.8), db: Database = Depends(get_db)):
        staged_dir = Path(upload_dir) / uuid.uuid4().hex
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_path = staged_dir / Path(file.filename).name
        staged_path.write_bytes(file.file.read())

        try:
            info = validate_reference_audio(str(staged_path.resolve()))
        except ValueError as exc:
            shutil.rmtree(staged_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc))

        try:
            profile = db.create_voice_profile(
                name=name, engine="chatterbox", reference_audio_path=str(staged_path.resolve()),
                reference_transcript=reference_transcript, exaggeration=exaggeration,
                cfg_weight=cfg_weight, temperature=temperature,
            )
        except ValueError as exc:
            shutil.rmtree(staged_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc))

        permanent_path = store_reference_audio(str(staged_path.resolve()), profile.id, profile.name)
        db.update_voice_profile_reference_audio_path(profile.id, str(permanent_path))
        shutil.rmtree(staged_dir, ignore_errors=True)
        profile = db.get_voice_profile_by_id(profile.id)

        return _voice_to_dict(profile, warnings=info.warnings)

    @app.get("/api/voices")
    def list_voices(db: Database = Depends(get_db)):
        return [_voice_to_dict(p) for p in db.list_voice_profiles()]

    @app.delete("/api/voices/{voice_id}")
    def delete_voice(voice_id: int, db: Database = Depends(get_db)):
        profile = db.get_voice_profile_by_id(voice_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="voice profile not found")
        db.delete_voice_profile(voice_id)
        delete_voice_storage(voice_id, profile.name)
        return {"ok": True}

    @app.get("/api/pronunciations")
    def list_pronunciations(db: Database = Depends(get_db)):
        return [_lexicon_to_dict(e) for e in db.list_lexicon_entries()]

    @app.post("/api/pronunciations")
    def create_pronunciation(payload: dict = Body(...), db: Database = Depends(get_db)):
        try:
            entry = db.create_lexicon_entry(
                term=payload["term"], override_value=payload["override_value"], scope="global",
                notes=payload.get("notes"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return _lexicon_to_dict(entry)

    @app.patch("/api/pronunciations/{term}")
    def update_pronunciation(term: str, payload: dict = Body(...), db: Database = Depends(get_db)):
        try:
            entry = db.update_lexicon_entry_override(term, payload["override_value"])
        except KeyError:
            raise HTTPException(status_code=404, detail="pronunciation entry not found")
        return _lexicon_to_dict(entry)

    @app.delete("/api/pronunciations/{term}")
    def delete_pronunciation(term: str, db: Database = Depends(get_db)):
        try:
            db.delete_lexicon_entry(term)
        except KeyError:
            raise HTTPException(status_code=404, detail="pronunciation entry not found")
        return {"ok": True}

    @app.post("/api/text-clips")
    def create_text_clip(payload: dict = Body(...), db: Database = Depends(get_db)):
        text = (payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text must not be empty")

        voice_profile_id = payload.get("voice_profile_id")
        if voice_profile_id is not None and db.get_voice_profile_by_id(voice_profile_id) is None:
            raise HTTPException(status_code=404, detail="voice profile not found")

        clip = db.create_text_clip(text=text, voice_profile_id=voice_profile_id)
        return _text_clip_to_dict(clip)

    @app.get("/api/text-clips")
    def list_text_clips(db: Database = Depends(get_db)):
        return [_text_clip_to_dict(c) for c in db.list_text_clips()]

    @app.get("/api/text-clips/{clip_id}")
    def get_text_clip(clip_id: int, db: Database = Depends(get_db)):
        try:
            clip = db.get_text_clip(clip_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="text clip not found")
        return _text_clip_to_dict(clip)

    @app.get("/api/text-clips/{clip_id}/audio")
    def get_text_clip_audio(clip_id: int, db: Database = Depends(get_db)):
        try:
            clip = db.get_text_clip(clip_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="text clip not found")
        if clip.status != JobStatus.DONE or not clip.output_path:
            raise HTTPException(status_code=404, detail="audio not ready")
        return FileResponse(clip.output_path, media_type="audio/mpeg", filename=f"text-clip-{clip.id}.mp3")

    @app.delete("/api/text-clips/{clip_id}")
    def delete_text_clip(clip_id: int, db: Database = Depends(get_db)):
        try:
            clip = db.get_text_clip(clip_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="text clip not found")
        db.delete_text_clip(clip_id)
        if clip.output_path and Path(clip.output_path).is_file():
            Path(clip.output_path).unlink()
        return {"ok": True}

    resolved_dist = Path(frontend_dist) if frontend_dist else Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if resolved_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(resolved_dist), html=True), name="frontend")

    return app

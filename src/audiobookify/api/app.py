import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ..db import Database
from ..ingest import ingest_by_path


def create_app(db_path: str = "audiobookify.db", upload_dir: str = "uploads") -> FastAPI:
    app = FastAPI()
    Path(upload_dir).mkdir(parents=True, exist_ok=True)

    def get_db():
        db = Database(db_path)
        try:
            yield db
        finally:
            db.close()

    def _book_to_dict(db: Database, book) -> dict:
        chapters = []
        for chapter in db.list_chapters(book.id):
            job = db.latest_job_for_chapter(chapter.id)
            chapters.append({
                "id": chapter.id,
                "order_index": chapter.order_index,
                "title": chapter.title,
                "status": chapter.status.value,
                "output_mp3_path": chapter.output_mp3_path,
                "error": chapter.error,
                "progress": job.progress if job else None,
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

        staged_dir = Path(upload_dir) / uuid.uuid4().hex
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_path = staged_dir / filename
        staged_path.write_bytes(file.file.read())

        try:
            parsed, source_format = ingest_by_path(str(staged_path.resolve()))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        book = db.create_book(
            title=parsed.title, author=parsed.author, source_path=str(staged_path.resolve()),
            source_format=source_format, output_dir=str(Path(output_dir).resolve()),
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

    return app

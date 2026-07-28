import sqlite3
import time
from typing import List, Optional

from .models import Book, Chapter, ChapterStatus, SourceFormat
from .ingest.base import RawChapter

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT,
    source_path TEXT NOT NULL UNIQUE,
    source_format TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    created_at REAL NOT NULL,
    voice_profile_id INTEGER
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id),
    order_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    output_mp3_path TEXT,
    duration REAL,
    error TEXT,
    UNIQUE(book_id, order_index)
);
"""


def _row_to_book(row: sqlite3.Row) -> Book:
    return Book(
        id=row["id"], title=row["title"], author=row["author"],
        source_path=row["source_path"], source_format=SourceFormat(row["source_format"]),
        output_dir=row["output_dir"], created_at=row["created_at"],
        voice_profile_id=row["voice_profile_id"],
    )


def _row_to_chapter(row: sqlite3.Row) -> Chapter:
    return Chapter(
        id=row["id"], book_id=row["book_id"], order_index=row["order_index"],
        title=row["title"], text=row["text"], status=ChapterStatus(row["status"]),
        output_mp3_path=row["output_mp3_path"], duration=row["duration"], error=row["error"],
    )


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def create_book(self, title: str, author: Optional[str], source_path: str,
                     source_format: SourceFormat, output_dir: str) -> Book:
        cur = self.conn.execute(
            "INSERT INTO books (title, author, source_path, source_format, output_dir, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title, author, source_path, source_format.value, output_dir, time.time()),
        )
        self.conn.commit()
        return self.get_book(cur.lastrowid)

    def get_book(self, book_id: int) -> Book:
        row = self.conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return _row_to_book(row)

    def find_book_by_source(self, source_path: str) -> Optional[Book]:
        row = self.conn.execute("SELECT * FROM books WHERE source_path = ?", (source_path,)).fetchone()
        return _row_to_book(row) if row else None

    def add_chapters(self, book_id: int, raw_chapters: List[RawChapter]) -> List[Chapter]:
        for index, raw in enumerate(raw_chapters):
            self.conn.execute(
                "INSERT INTO chapters (book_id, order_index, title, text) VALUES (?, ?, ?, ?)",
                (book_id, index, raw.title, raw.text),
            )
        self.conn.commit()
        return self.list_chapters(book_id)

    def list_chapters(self, book_id: int) -> List[Chapter]:
        rows = self.conn.execute(
            "SELECT * FROM chapters WHERE book_id = ? ORDER BY order_index", (book_id,)
        ).fetchall()
        return [_row_to_chapter(row) for row in rows]

    def get_chapter(self, chapter_id: int) -> Chapter:
        row = self.conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
        return _row_to_chapter(row)

    def update_chapter_status(self, chapter_id: int, status: ChapterStatus,
                               output_mp3_path: Optional[str] = None,
                               duration: Optional[float] = None,
                               error: Optional[str] = None) -> None:
        self.conn.execute(
            "UPDATE chapters SET status = ?, output_mp3_path = COALESCE(?, output_mp3_path), "
            "duration = COALESCE(?, duration), error = ? WHERE id = ?",
            (status.value, output_mp3_path, duration, error, chapter_id),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

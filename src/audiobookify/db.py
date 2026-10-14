import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import List, Optional

from .models import (
    Book, Chapter, ChapterStatus, Job, JobStatus, LexiconEntry, SourceFormat, TextClip, VoiceProfile,
)
from .ingest.base import RawChapter
from .voice import storage as voice_storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT,
    source_path TEXT NOT NULL UNIQUE,
    source_format TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    created_at REAL NOT NULL,
    voice_profile_id INTEGER,
    engine TEXT NOT NULL DEFAULT 'chatterbox-turbo'
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
    used_fallback INTEGER NOT NULL DEFAULT 0,
    engine TEXT,
    UNIQUE(book_id, order_index)
);

CREATE TABLE IF NOT EXISTS voice_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    engine TEXT NOT NULL,
    reference_audio_path TEXT NOT NULL,
    reference_transcript TEXT,
    exaggeration REAL NOT NULL,
    cfg_weight REAL NOT NULL,
    temperature REAL NOT NULL,
    created_at REAL NOT NULL,
    transcript_status TEXT,
    transcript_error TEXT,
    reference_words TEXT,
    reference_trim_end REAL,
    tempo REAL NOT NULL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS lexicon_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL COLLATE NOCASE,
    override_value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    scope TEXT NOT NULL,
    book_id INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at REAL NOT NULL,
    phonemes TEXT,
    sample_audio_path TEXT,
    UNIQUE(book_id, term)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS worker_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    paused INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS text_clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    voice_profile_id INTEGER REFERENCES voice_profiles(id),
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL,
    output_path TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    error TEXT,
    used_fallback INTEGER NOT NULL DEFAULT 0,
    engine TEXT NOT NULL DEFAULT 'chatterbox-turbo',
    tempo REAL
);
"""


def _migrate_lexicon_entries_columns(conn: sqlite3.Connection) -> None:
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(lexicon_entries)")}
    if "phonemes" not in existing_cols:
        conn.execute("ALTER TABLE lexicon_entries ADD COLUMN phonemes TEXT")
    if "sample_audio_path" not in existing_cols:
        conn.execute("ALTER TABLE lexicon_entries ADD COLUMN sample_audio_path TEXT")


def _migrate_fallback_columns(conn: sqlite3.Connection) -> None:
    chapter_cols = {row["name"] for row in conn.execute("PRAGMA table_info(chapters)")}
    if "used_fallback" not in chapter_cols:
        conn.execute("ALTER TABLE chapters ADD COLUMN used_fallback INTEGER NOT NULL DEFAULT 0")
    clip_cols = {row["name"] for row in conn.execute("PRAGMA table_info(text_clips)")}
    if "used_fallback" not in clip_cols:
        conn.execute("ALTER TABLE text_clips ADD COLUMN used_fallback INTEGER NOT NULL DEFAULT 0")


def _migrate_cancel_requested_column(conn: sqlite3.Connection) -> None:
    job_cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "cancel_requested" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")


def _migrate_engine_and_transcript_columns(conn: sqlite3.Connection) -> None:
    def cols(table):
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

    for table in ("books", "text_clips"):
        if "engine" not in cols(table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN engine TEXT NOT NULL DEFAULT 'chatterbox-turbo'")
    if "engine" not in cols("chapters"):
        conn.execute("ALTER TABLE chapters ADD COLUMN engine TEXT")
        # Audio finished before engines were recorded came from PyTorch Chatterbox.
        conn.execute("UPDATE chapters SET engine = 'chatterbox' WHERE status = 'done'")
    voice_cols = cols("voice_profiles")
    for col, kind in [("transcript_status", "TEXT"), ("transcript_error", "TEXT"),
                      ("reference_words", "TEXT"), ("reference_trim_end", "REAL")]:
        if col not in voice_cols:
            conn.execute(f"ALTER TABLE voice_profiles ADD COLUMN {col} {kind}")
    if "tempo" not in voice_cols:
        conn.execute("ALTER TABLE voice_profiles ADD COLUMN tempo REAL NOT NULL DEFAULT 1.0")
    if "tempo" not in cols("text_clips"):
        conn.execute("ALTER TABLE text_clips ADD COLUMN tempo REAL")


_WARNED_VOICE_MIGRATION_IDS: set = set()


def _warn_voice_migration_skipped_once(profile_id: int, message: str) -> None:
    if profile_id in _WARNED_VOICE_MIGRATION_IDS:
        return
    _WARNED_VOICE_MIGRATION_IDS.add(profile_id)
    print(f"Warning: {message}", file=sys.stderr)


def _migrate_voice_reference_audio(conn: sqlite3.Connection) -> None:
    voices_root = str(Path(voice_storage.VOICES_DIR).resolve())
    rows = conn.execute("SELECT id, name, reference_audio_path FROM voice_profiles").fetchall()
    for row in rows:
        current_path = row["reference_audio_path"]
        if current_path.startswith(voices_root):
            continue
        if not Path(current_path).is_file():
            _warn_voice_migration_skipped_once(
                row["id"],
                f"voice profile {row['id']} ({row['name']})'s reference audio "
                f"'{current_path}' no longer exists - skipping migration for this profile",
            )
            continue
        try:
            new_path = voice_storage.store_reference_audio(current_path, row["id"], row["name"])
        except OSError as exc:
            _warn_voice_migration_skipped_once(
                row["id"],
                f"voice profile {row['id']} ({row['name']})'s reference audio could not be "
                f"migrated to permanent storage ({exc}) - skipping this profile",
            )
            continue
        conn.execute(
            "UPDATE voice_profiles SET reference_audio_path = ? WHERE id = ?",
            (str(new_path), row["id"]),
        )


def _row_to_book(row: sqlite3.Row) -> Book:
    return Book(
        id=row["id"], title=row["title"], author=row["author"],
        source_path=row["source_path"], source_format=SourceFormat(row["source_format"]),
        output_dir=row["output_dir"], created_at=row["created_at"],
        voice_profile_id=row["voice_profile_id"], engine=row["engine"],
    )


def _row_to_chapter(row: sqlite3.Row) -> Chapter:
    return Chapter(
        id=row["id"], book_id=row["book_id"], order_index=row["order_index"],
        title=row["title"], text=row["text"], status=ChapterStatus(row["status"]),
        output_mp3_path=row["output_mp3_path"], duration=row["duration"], error=row["error"],
        used_fallback=bool(row["used_fallback"]), engine=row["engine"],
    )


def _row_to_voice_profile(row: sqlite3.Row) -> VoiceProfile:
    return VoiceProfile(
        id=row["id"], name=row["name"], engine=row["engine"],
        reference_audio_path=row["reference_audio_path"],
        reference_transcript=row["reference_transcript"],
        exaggeration=row["exaggeration"], cfg_weight=row["cfg_weight"],
        temperature=row["temperature"], created_at=row["created_at"],
        transcript_status=row["transcript_status"], transcript_error=row["transcript_error"],
        reference_words=json.loads(row["reference_words"]) if row["reference_words"] else None,
        reference_trim_end=row["reference_trim_end"], tempo=row["tempo"],
    )


def _row_to_lexicon_entry(row: sqlite3.Row) -> LexiconEntry:
    return LexiconEntry(
        id=row["id"], term=row["term"], override_value=row["override_value"],
        source=row["source"], scope=row["scope"], book_id=row["book_id"],
        notes=row["notes"], created_at=row["created_at"],
        phonemes=row["phonemes"], sample_audio_path=row["sample_audio_path"],
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"], chapter_id=row["chapter_id"], status=JobStatus(row["status"]),
        progress=row["progress"], created_at=row["created_at"],
        started_at=row["started_at"], finished_at=row["finished_at"], error=row["error"],
    )


def _row_to_text_clip(row: sqlite3.Row) -> TextClip:
    return TextClip(
        id=row["id"], text=row["text"], voice_profile_id=row["voice_profile_id"],
        status=JobStatus(row["status"]), progress=row["progress"], output_path=row["output_path"],
        created_at=row["created_at"], started_at=row["started_at"], finished_at=row["finished_at"],
        error=row["error"], used_fallback=bool(row["used_fallback"]), engine=row["engine"],
        tempo=row["tempo"],
    )


def book_paragraphs(chapters: List[Chapter]):
    """The book as one paragraph list, plus the index each chapter starts at.

    Chapter text is paragraphs joined by a blank line, and split/join on it are exact
    inverses, so chapter breaks can move between paragraphs without losing a character.
    """
    paragraphs, starts = [], []
    for chapter in chapters:
        starts.append(len(paragraphs))
        paragraphs.extend(chapter.text.split("\n\n"))
    return paragraphs, starts


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Two OS processes (the API and the worker) now write to this file
        # concurrently - WAL + a busy timeout turns lock contention into a
        # brief wait/retry instead of "database is locked".
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        _migrate_lexicon_entries_columns(self.conn)
        _migrate_fallback_columns(self.conn)
        _migrate_cancel_requested_column(self.conn)
        _migrate_engine_and_transcript_columns(self.conn)
        _migrate_voice_reference_audio(self.conn)
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
        if row is None:
            raise KeyError(f"no book with id {book_id}")
        return _row_to_book(row)

    def list_books(self) -> List[Book]:
        rows = self.conn.execute("SELECT * FROM books ORDER BY id").fetchall()
        return [_row_to_book(row) for row in rows]

    def find_book_by_source(self, source_path: str) -> Optional[Book]:
        row = self.conn.execute("SELECT * FROM books WHERE source_path = ?", (source_path,)).fetchone()
        return _row_to_book(row) if row else None

    def add_chapters(self, book_id: int, raw_chapters: List[RawChapter]) -> List[Chapter]:
        # Find the next available order_index for this book
        max_order = self.conn.execute(
            "SELECT MAX(order_index) as max_idx FROM chapters WHERE book_id = ?", (book_id,)
        ).fetchone()
        start_index = (max_order["max_idx"] if max_order["max_idx"] is not None else -1) + 1

        for offset, raw in enumerate(raw_chapters):
            self.conn.execute(
                "INSERT INTO chapters (book_id, order_index, title, text) VALUES (?, ?, ?, ?)",
                (book_id, start_index + offset, raw.title, raw.text),
            )
        self.conn.commit()
        return self.list_chapters(book_id)

    def list_chapters(self, book_id: int) -> List[Chapter]:
        rows = self.conn.execute(
            "SELECT * FROM chapters WHERE book_id = ? ORDER BY order_index", (book_id,)
        ).fetchall()
        return [_row_to_chapter(row) for row in rows]

    def replace_chapter_breaks(self, book_id: int, breaks: List[tuple]) -> List[Chapter]:
        """Re-split the book's paragraphs at `breaks` [(start paragraph, title), ...].

        A chapter covering exactly the same paragraphs as before keeps its row (and so its
        audio and status); any other range becomes a new pending chapter. Returns the old
        chapters that no longer exist, so callers can clean up their files.
        """
        old = self.list_chapters(book_id)
        paragraphs, old_starts = book_paragraphs(old)
        starts = [start for start, _ in breaks]
        if not breaks or starts[0] != 0 or starts[-1] >= len(paragraphs) \
                or any(a >= b for a, b in zip(starts, starts[1:])):
            raise ValueError("chapter breaks must start at the first paragraph and go forward")
        ends = starts[1:] + [len(paragraphs)]
        texts = ["\n\n".join(paragraphs[s:e]) for s, e in zip(starts, ends)]
        titles = [title.strip() for _, title in breaks]
        if not all(titles):
            raise ValueError("every chapter needs a title")
        if not all(text.strip() for text in texts):
            raise ValueError("every chapter needs some words in it")

        old_ends = old_starts[1:] + [len(paragraphs)]
        by_range = {(s, e): c for s, e, c in zip(old_starts, old_ends, old)}
        kept = {}
        for i, (s, e) in enumerate(zip(starts, ends)):
            if (s, e) in by_range:
                kept[i] = by_range[(s, e)]
        kept_ids = {c.id for c in kept.values()}
        removed = [c for c in old if c.id not in kept_ids]

        with self.conn:
            # Park every index out of the way so the UNIQUE(book_id, order_index) holds mid-update.
            self.conn.execute("UPDATE chapters SET order_index = -1 - order_index WHERE book_id = ?", (book_id,))
            for chapter in removed:
                self.conn.execute("DELETE FROM jobs WHERE chapter_id = ?", (chapter.id,))
                self.conn.execute("DELETE FROM chapters WHERE id = ?", (chapter.id,))
            for i, (title, text) in enumerate(zip(titles, texts)):
                if i in kept:
                    self.conn.execute("UPDATE chapters SET order_index = ?, title = ? WHERE id = ?",
                                      (i, title, kept[i].id))
                else:
                    self.conn.execute(
                        "INSERT INTO chapters (book_id, order_index, title, text) VALUES (?, ?, ?, ?)",
                        (book_id, i, title, text),
                    )
        return removed

    def set_chapter_audio_path(self, chapter_id: int, path: str) -> None:
        self.conn.execute("UPDATE chapters SET output_mp3_path = ? WHERE id = ?", (path, chapter_id))
        self.conn.commit()

    def get_chapter(self, chapter_id: int) -> Chapter:
        row = self.conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
        if row is None:
            raise KeyError(f"no chapter with id {chapter_id}")
        return _row_to_chapter(row)

    def update_chapter_status(self, chapter_id: int, status: ChapterStatus,
                               output_mp3_path: Optional[str] = None,
                               duration: Optional[float] = None,
                               error: Optional[str] = None,
                               used_fallback: bool = False, engine: Optional[str] = None) -> None:
        cur = self.conn.execute(
            "UPDATE chapters SET status = ?, output_mp3_path = COALESCE(?, output_mp3_path), "
            "duration = COALESCE(?, duration), error = ?, used_fallback = ?, "
            "engine = COALESCE(?, engine) WHERE id = ?",
            (status.value, output_mp3_path, duration, error, int(used_fallback), engine, chapter_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"no chapter with id {chapter_id}")
        self.conn.commit()

    def set_chapter_duration(self, chapter_id: int, duration: float) -> None:
        self.conn.execute("UPDATE chapters SET duration = ? WHERE id = ?", (duration, chapter_id))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def create_voice_profile(self, name: str, engine: str, reference_audio_path: str,
                              reference_transcript: Optional[str], exaggeration: float,
                              cfg_weight: float, temperature: float,
                              transcript_status: Optional[str] = None) -> VoiceProfile:
        try:
            self.conn.execute(
                "INSERT INTO voice_profiles (name, engine, reference_audio_path, reference_transcript, "
                "exaggeration, cfg_weight, temperature, created_at, transcript_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, engine, reference_audio_path, reference_transcript, exaggeration, cfg_weight,
                 temperature, time.time(), transcript_status),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"a voice profile named '{name}' already exists")
        self.conn.commit()
        return self.find_voice_profile_by_name(name)

    def find_voice_profile_by_name(self, name: str) -> Optional[VoiceProfile]:
        row = self.conn.execute("SELECT * FROM voice_profiles WHERE name = ?", (name,)).fetchone()
        return _row_to_voice_profile(row) if row else None

    def list_voice_profiles(self) -> List[VoiceProfile]:
        rows = self.conn.execute("SELECT * FROM voice_profiles ORDER BY id").fetchall()
        return [_row_to_voice_profile(row) for row in rows]

    def update_book_engine(self, book_id: int, engine: str) -> None:
        self.conn.execute("UPDATE books SET engine = ? WHERE id = ?", (engine, book_id))
        self.conn.commit()

    def update_book_voice_profile(self, book_id: int, voice_profile_id: Optional[int]) -> None:
        self.conn.execute(
            "UPDATE books SET voice_profile_id = ? WHERE id = ?",
            (voice_profile_id, book_id),
        )
        self.conn.commit()

    def get_voice_profile_by_id(self, voice_profile_id: int) -> Optional[VoiceProfile]:
        row = self.conn.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (voice_profile_id,)
        ).fetchone()
        return _row_to_voice_profile(row) if row else None

    def update_voice_profile_reference_audio_path(self, profile_id: int,
                                                    reference_audio_path: str) -> None:
        cur = self.conn.execute(
            "UPDATE voice_profiles SET reference_audio_path = ? WHERE id = ?",
            (reference_audio_path, profile_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"no voice profile with id {profile_id}")
        self.conn.commit()

    def next_pending_transcription(self) -> Optional[VoiceProfile]:
        row = self.conn.execute(
            "SELECT * FROM voice_profiles WHERE transcript_status = 'pending' ORDER BY id LIMIT 1"
        ).fetchone()
        return _row_to_voice_profile(row) if row else None

    def request_voice_transcription(self, profile_id: int) -> None:
        self._update_voice(profile_id, "transcript_status = 'pending', transcript_error = NULL", ())

    def fail_voice_transcription(self, profile_id: int, error: str) -> None:
        self._update_voice(profile_id, "transcript_status = 'failed', transcript_error = ?", (error,))

    def save_voice_transcript(self, profile_id: int, text: str, words: Optional[list] = None,
                               trim_end: Optional[float] = None) -> None:
        """A Whisper result (words given: replaces timings and trim) or a user edit
        of the text alone (keeps them)."""
        if words is None:
            self._update_voice(profile_id, "reference_transcript = ?, transcript_status = 'done', "
                               "transcript_error = NULL", (text,))
        else:
            self._update_voice(profile_id, "reference_transcript = ?, transcript_status = 'done', "
                               "transcript_error = NULL, reference_words = ?, reference_trim_end = ?",
                               (text, json.dumps(words), trim_end))

    def set_voice_tempo(self, profile_id: int, tempo: float) -> None:
        self._update_voice(profile_id, "tempo = ?", (tempo,))

    def _update_voice(self, profile_id: int, assignments: str, params: tuple) -> None:
        cur = self.conn.execute(f"UPDATE voice_profiles SET {assignments} WHERE id = ?", (*params, profile_id))
        if cur.rowcount == 0:
            raise KeyError(f"no voice profile with id {profile_id}")
        self.conn.commit()

    def delete_voice_profile(self, voice_profile_id: int) -> None:
        cur = self.conn.execute(
            "DELETE FROM voice_profiles WHERE id = ?", (voice_profile_id,)
        )
        if cur.rowcount == 0:
            raise KeyError(f"no voice profile with id {voice_profile_id}")
        self.conn.commit()

    def create_lexicon_entry(self, term: str, override_value: str, scope: str,
                              book_id: int = 0, notes: Optional[str] = None,
                              source: str = "manual", phonemes: Optional[str] = None,
                              sample_audio_path: Optional[str] = None) -> LexiconEntry:
        try:
            self.conn.execute(
                "INSERT INTO lexicon_entries (term, override_value, source, scope, book_id, notes, "
                "created_at, phonemes, sample_audio_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (term, override_value, source, scope, book_id, notes, time.time(),
                 phonemes, sample_audio_path),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"a lexicon entry for '{term}' already exists in this scope")
        self.conn.commit()
        return self.find_lexicon_entry(term, book_id=book_id)

    def find_lexicon_entry(self, term: str, book_id: int = 0) -> Optional[LexiconEntry]:
        row = self.conn.execute(
            "SELECT * FROM lexicon_entries WHERE term = ? AND book_id = ?", (term, book_id)
        ).fetchone()
        return _row_to_lexicon_entry(row) if row else None

    def list_lexicon_entries(self, book_id: Optional[int] = None) -> List[LexiconEntry]:
        if book_id is None:
            rows = self.conn.execute(
                "SELECT * FROM lexicon_entries WHERE book_id = 0 ORDER BY id"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM lexicon_entries WHERE book_id = ? OR book_id = 0 "
                "ORDER BY (book_id = 0), id",
                (book_id,),
            ).fetchall()
        return [_row_to_lexicon_entry(row) for row in rows]

    def delete_lexicon_entry(self, term: str, book_id: int = 0) -> None:
        cur = self.conn.execute(
            "DELETE FROM lexicon_entries WHERE term = ? AND book_id = ?", (term, book_id)
        )
        if cur.rowcount == 0:
            raise KeyError(f"no lexicon entry for '{term}' in this scope")
        self.conn.commit()

    def update_lexicon_entry_override(self, term: str, override_value: str,
                                       book_id: int = 0) -> LexiconEntry:
        cur = self.conn.execute(
            "UPDATE lexicon_entries SET override_value = ? WHERE term = ? AND book_id = ?",
            (override_value, term, book_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"no lexicon entry for '{term}' in this scope")
        self.conn.commit()
        return self.find_lexicon_entry(term, book_id=book_id)

    def create_job(self, chapter_id: int) -> Job:
        cur = self.conn.execute(
            "INSERT INTO jobs (chapter_id, status, progress, created_at) VALUES (?, ?, ?, ?)",
            (chapter_id, JobStatus.QUEUED.value, 0.0, time.time()),
        )
        self.conn.commit()
        return self.get_job(cur.lastrowid)

    def get_job(self, job_id: int) -> Job:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"no job with id {job_id}")
        return _row_to_job(row)

    def list_jobs_for_chapter(self, chapter_id: int) -> List[Job]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE chapter_id = ? ORDER BY created_at DESC, id DESC",
            (chapter_id,),
        ).fetchall()
        return [_row_to_job(row) for row in rows]

    def latest_job_for_chapter(self, chapter_id: int) -> Optional[Job]:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE chapter_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (chapter_id,),
        ).fetchone()
        return _row_to_job(row) if row else None

    def pop_next_queued_job(self) -> Optional[Job]:
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE status = ? ORDER BY created_at, id LIMIT 1",
            (JobStatus.QUEUED.value,),
        ).fetchone()
        if row is None:
            return None
        job_id = row["id"]
        cur = self.conn.execute(
            "UPDATE jobs SET status = ?, started_at = ? WHERE id = ? AND status = ?",
            (JobStatus.GENERATING.value, time.time(), job_id, JobStatus.QUEUED.value),
        )
        self.conn.commit()
        if cur.rowcount == 0:
            return None  # claimed by another worker between the SELECT and UPDATE
        return self.get_job(job_id)

    def update_job_progress(self, job_id: int, progress: float) -> None:
        self.conn.execute("UPDATE jobs SET progress = ? WHERE id = ?", (progress, job_id))
        self.conn.commit()

    def finish_job(self, job_id: int, status: JobStatus, error: Optional[str] = None) -> None:
        if status == JobStatus.DONE:
            self.conn.execute(
                "UPDATE jobs SET status = ?, progress = 1.0, finished_at = ?, error = ? WHERE id = ?",
                (status.value, time.time(), error, job_id),
            )
        else:
            self.conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status.value, time.time(), error, job_id),
            )
        self.conn.commit()

    def reset_stale_generating_jobs(self) -> None:
        self.conn.execute(
            "UPDATE jobs SET status = ?, started_at = NULL WHERE status = ?",
            (JobStatus.QUEUED.value, JobStatus.GENERATING.value),
        )
        self.conn.commit()

    def create_text_clip(self, text: str, voice_profile_id: Optional[int],
                          engine: str = "chatterbox-turbo", tempo: Optional[float] = None) -> TextClip:
        cur = self.conn.execute(
            "INSERT INTO text_clips (text, voice_profile_id, status, progress, created_at, engine, tempo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (text, voice_profile_id, JobStatus.QUEUED.value, 0.0, time.time(), engine, tempo),
        )
        self.conn.commit()
        return self.get_text_clip(cur.lastrowid)

    def get_text_clip(self, clip_id: int) -> TextClip:
        row = self.conn.execute("SELECT * FROM text_clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            raise KeyError(f"no text clip with id {clip_id}")
        return _row_to_text_clip(row)

    def list_text_clips(self) -> List[TextClip]:
        rows = self.conn.execute("SELECT * FROM text_clips ORDER BY id DESC").fetchall()
        return [_row_to_text_clip(row) for row in rows]

    def delete_text_clip(self, clip_id: int) -> None:
        cur = self.conn.execute("DELETE FROM text_clips WHERE id = ?", (clip_id,))
        if cur.rowcount == 0:
            raise KeyError(f"no text clip with id {clip_id}")
        self.conn.commit()

    def pop_next_queued_text_clip(self) -> Optional[TextClip]:
        row = self.conn.execute(
            "SELECT id FROM text_clips WHERE status = ? ORDER BY created_at, id LIMIT 1",
            (JobStatus.QUEUED.value,),
        ).fetchone()
        if row is None:
            return None
        clip_id = row["id"]
        cur = self.conn.execute(
            "UPDATE text_clips SET status = ?, started_at = ? WHERE id = ? AND status = ?",
            (JobStatus.GENERATING.value, time.time(), clip_id, JobStatus.QUEUED.value),
        )
        self.conn.commit()
        if cur.rowcount == 0:
            return None  # claimed by another worker between the SELECT and UPDATE
        return self.get_text_clip(clip_id)

    def update_text_clip_progress(self, clip_id: int, progress: float) -> None:
        self.conn.execute("UPDATE text_clips SET progress = ? WHERE id = ?", (progress, clip_id))
        self.conn.commit()

    def finish_text_clip(self, clip_id: int, status: JobStatus, output_path: Optional[str] = None,
                          error: Optional[str] = None, used_fallback: bool = False) -> None:
        if status == JobStatus.DONE:
            self.conn.execute(
                "UPDATE text_clips SET status = ?, progress = 1.0, output_path = ?, "
                "finished_at = ?, error = ?, used_fallback = ? WHERE id = ?",
                (status.value, output_path, time.time(), error, int(used_fallback), clip_id),
            )
        else:
            self.conn.execute(
                "UPDATE text_clips SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status.value, time.time(), error, clip_id),
            )
        self.conn.commit()

    def reset_stale_generating_text_clips(self) -> None:
        self.conn.execute(
            "UPDATE text_clips SET status = ?, started_at = NULL WHERE status = ?",
            (JobStatus.QUEUED.value, JobStatus.GENERATING.value),
        )
        self.conn.commit()

    def is_paused(self) -> bool:
        row = self.conn.execute("SELECT paused FROM worker_state WHERE id = 1").fetchone()
        return bool(row["paused"]) if row else False

    def set_paused(self, paused: bool) -> None:
        self.conn.execute(
            "INSERT INTO worker_state (id, paused) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET paused = excluded.paused",
            (int(paused),),
        )
        self.conn.commit()

    def request_job_cancel(self, job_id: int) -> None:
        self.conn.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
        self.conn.commit()

    def is_job_cancel_requested(self, job_id: int) -> bool:
        row = self.conn.execute(
            "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no job with id {job_id}")
        return bool(row["cancel_requested"])

    def requeue_job(self, job_id: int) -> None:
        self.conn.execute(
            "UPDATE jobs SET status = ?, started_at = NULL WHERE id = ?",
            (JobStatus.QUEUED.value, job_id),
        )
        self.conn.commit()

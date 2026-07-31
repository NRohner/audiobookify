from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SourceFormat(str, Enum):
    EPUB = "epub"
    MARKDOWN = "md"


class ChapterStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Chapter:
    id: int
    book_id: int
    order_index: int
    title: str
    text: str
    status: ChapterStatus = ChapterStatus.PENDING
    output_mp3_path: Optional[str] = None
    duration: Optional[float] = None
    error: Optional[str] = None
    used_fallback: bool = False


@dataclass
class Book:
    id: int
    title: str
    author: Optional[str]
    source_path: str
    source_format: SourceFormat
    output_dir: str
    created_at: float
    voice_profile_id: Optional[int] = None
    chapters: list = field(default_factory=list)


@dataclass
class VoiceProfile:
    id: int
    name: str
    engine: str
    reference_audio_path: str
    reference_transcript: Optional[str]
    exaggeration: float
    cfg_weight: float
    temperature: float
    created_at: float


@dataclass
class LexiconEntry:
    id: int
    term: str
    override_value: str
    source: str
    scope: str
    book_id: int
    notes: Optional[str]
    created_at: float
    phonemes: Optional[str] = None
    sample_audio_path: Optional[str] = None


class JobStatus(str, Enum):
    QUEUED = "queued"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    id: int
    chapter_id: int
    status: JobStatus
    progress: Optional[float]
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None


@dataclass
class TextClip:
    id: int
    text: str
    voice_profile_id: Optional[int]
    status: JobStatus
    progress: Optional[float]
    output_path: Optional[str]
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    used_fallback: bool = False

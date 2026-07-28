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

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RawChapter:
    title: str
    text: str


@dataclass
class ParsedBook:
    title: str
    author: Optional[str]
    chapters: List[RawChapter] = field(default_factory=list)

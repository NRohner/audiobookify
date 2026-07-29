from pathlib import Path
from typing import Tuple

from ..models import SourceFormat
from . import epub, markdown
from .base import ParsedBook


def ingest_by_path(path: str, heading_level: int = 1) -> Tuple[ParsedBook, SourceFormat]:
    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        return epub.parse(path), SourceFormat.EPUB
    if suffix == ".md":
        return markdown.parse(path, heading_level=heading_level), SourceFormat.MARKDOWN
    raise ValueError(f"unsupported input format: {suffix}")

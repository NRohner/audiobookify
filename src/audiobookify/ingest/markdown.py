import re
from pathlib import Path

from .base import ParsedBook, RawChapter


def _strip_markdown(text: str) -> str:
    text = re.sub(r"`{1,3}[^`]*`{1,3}", lambda m: m.group(0).strip("`"), text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    return text.strip()


def parse(path: str, heading_level: int = 1) -> ParsedBook:
    raw = Path(path).read_text(encoding="utf-8")
    title = Path(path).stem

    pattern = re.compile(rf'^{"#" * heading_level}\s+(.+)$', re.MULTILINE)
    matches = list(pattern.finditer(raw))

    if not matches:
        return ParsedBook(title=title, author=None, chapters=[RawChapter(title=title, text=_strip_markdown(raw))])

    chapters = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        chapters.append(RawChapter(title=m.group(1).strip(), text=_strip_markdown(raw[start:end])))

    return ParsedBook(title=title, author=None, chapters=chapters)

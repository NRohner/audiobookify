from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import ITEM_DOCUMENT, epub as ebooklib_epub

from .base import ParsedBook, RawChapter

MIN_CHAPTER_CHARS = 200


def parse(path: str) -> ParsedBook:
    book = ebooklib_epub.read_epub(path)

    title_meta = book.get_metadata("DC", "title")
    title = title_meta[0][0] if title_meta else Path(path).stem

    author_meta = book.get_metadata("DC", "creator")
    author = author_meta[0][0] if author_meta else None

    chapters = []
    for item_id, _ in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue

        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["nav", "script", "style"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)

        if len(text) < MIN_CHAPTER_CHARS:
            continue  # skips cover pages, copyright blurbs, blank dividers

        heading = soup.find(["h1", "h2"])
        chapter_title = heading.get_text(strip=True) if heading else f"Chapter {len(chapters) + 1}"
        chapters.append(RawChapter(title=chapter_title, text=text))

    return ParsedBook(title=title, author=author, chapters=chapters)

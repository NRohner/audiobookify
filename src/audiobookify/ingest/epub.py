from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import PreformattedString
from ebooklib import ITEM_DOCUMENT, epub as ebooklib_epub

from .base import ParsedBook, RawChapter

MIN_CHAPTER_CHARS = 200
BLOCK_TAGS = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr", "td", "th"]


def _block_text(soup: BeautifulSoup) -> str:
    """Paragraphs (text under one block element) joined by blank lines.

    get_text("\\n") put a newline at every inline tag (<em>, <span>, <a>), so real
    paragraph breaks were indistinguishable. Here text nodes sharing a nearest block
    ancestor are joined as-is, and a blank line goes in only when that ancestor changes.

    ponytail: block ancestry is a fixed tag list; HTML5 blocks like <section> or
    <figure> bounding loose text won't split it. Add tags when a real book needs them.
    """
    for br in soup.find_all("br"):
        br.replace_with(" ")
    paragraphs, current, current_block = [], [], None
    for node in soup.find_all(string=True):
        if isinstance(node, PreformattedString):  # comments, doctype, CDATA
            continue
        block = node.find_parent(BLOCK_TAGS)
        if block is not current_block and current:
            paragraphs.append(" ".join("".join(current).split()))
            current = []
        current.append(str(node))
        current_block = block
    if current:
        paragraphs.append(" ".join("".join(current).split()))
    return "\n\n".join(p for p in paragraphs if p)


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
        for tag in soup(["head", "nav", "script", "style"]):
            tag.decompose()
        text = _block_text(soup)

        if len(text) < MIN_CHAPTER_CHARS:
            continue  # skips cover pages, copyright blurbs, blank dividers

        heading = soup.find(["h1", "h2"])
        chapter_title = heading.get_text(strip=True) if heading else f"Chapter {len(chapters) + 1}"
        chapters.append(RawChapter(title=chapter_title, text=text))

    return ParsedBook(title=title, author=author, chapters=chapters)

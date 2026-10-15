import copy
import posixpath
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup
from bs4.element import NavigableString, PreformattedString
from ebooklib import ITEM_DOCUMENT, epub as ebooklib_epub

from .base import ParsedBook, RawChapter

MIN_CHAPTER_CHARS = 200
SENTINEL_RANGE = range(0xE000, 0xF8FF + 1)  # private use area: candidates for the cut marker
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


def _load_soup(item) -> BeautifulSoup:
    soup = BeautifulSoup(item.get_content(), "html.parser")
    for tag in soup(["head", "nav", "script", "style"]):
        tag.decompose()
    return soup


def _toc_links(book) -> tuple:
    """(TOC file name, [(label, href) per entry]) in document order, nesting flattened.

    Prefers the EPUB 3 nav document, else the EPUB 2 NCX. Reads the raw bytes from the
    zip (item.content), not get_content(), which re-serializes nav through lxml.
    pageList / page-list / landmarks are never read.

    ponytail: ebooklib doesn't expose <spine toc="...">, so the NCX is the first
    manifest item with the NCX media type.
    """
    items = list(book.get_items())
    for item in items:
        if isinstance(item, ebooklib_epub.EpubNav):
            soup = BeautifulSoup(item.content, "html.parser")
            toc = next((n for n in soup.find_all("nav") if "toc" in n.get("epub:type", "").split()), None)
            if toc is not None:
                return item.file_name, [(a.get_text(), a["href"]) for a in toc.find_all("a", href=True)]
    for item in items:
        if item.media_type == "application/x-dtbncx+xml":
            nav_map = BeautifulSoup(item.content, "xml").find("navMap")
            links = []
            for point in nav_map.find_all("navPoint") if nav_map else []:
                label, content = point.find("navLabel", recursive=False), point.find("content", recursive=False)
                if content is not None and content.get("src"):
                    links.append((label.get_text() if label else "", content["src"]))
            return item.file_name, links
    return None, []


def _find_anchor(soup: BeautifulSoup, fragment: str):
    return soup.find(id=fragment) or soup.find("a", attrs={"name": fragment})


def _split_at_anchors(soup: BeautifulSoup, fragments: list) -> tuple:
    """(pieces, sentinel): text of a document cut before each anchor (in document order).

    len(fragments) + 1 pieces. The cut marker is the first private-use code point not in
    the document's text (books use them for ornaments), so it can't be mistaken for text;
    it is None when no anchor needs one. (None, None) when all 6,400 are taken: the caller
    gives up on the TOC. Rust mirrors this in `block_text_split`.
    """
    sentinel = None
    if fragments:
        text = _block_text(copy.copy(soup))  # a copy: _block_text replaces <br> tags, which may be anchors
        sentinel = next((chr(c) for c in SENTINEL_RANGE if chr(c) not in text), None)
        if sentinel is None:
            return None, None
        for fragment in fragments:
            _find_anchor(soup, fragment).insert_before(NavigableString(sentinel))
        text = _block_text(soup)
    else:
        text = _block_text(soup)
    pieces = []
    for piece in (text.split(sentinel) if sentinel else [text]):
        paragraphs = (p.strip() for p in piece.split("\n\n"))
        pieces.append("\n\n".join(p for p in paragraphs if p))
    return pieces, sentinel


def _toc_chapters(book, docs) -> list:
    """Chapters cut from the documents by the TOC; [] when fewer than 2 entries are usable."""
    toc_file, links = _toc_links(book)
    index = {item.file_name: i for i, item in enumerate(docs)}
    soups = {}  # document index -> soup, parsed on demand

    def soup_of(i):
        if i not in soups:
            soups[i] = _load_soup(docs[i])
        return soups[i]

    entries = []  # (label, document index, fragment or None)
    last = None
    for label, href in links:
        path, _, fragment = href.partition("#")
        path = posixpath.normpath(posixpath.join(posixpath.dirname(toc_file), unquote(path)))
        if path not in index:
            continue
        i = index[path]
        fragment = unquote(fragment)
        anchor = _find_anchor(soup_of(i), fragment) if fragment else None
        if anchor is None:
            fragment = None  # no fragment, or one that matches nothing: starts at the top
        # position in reading order; the top of a document sorts before every element in it
        order = -1
        if anchor:
            order = next(n for n, tag in enumerate(soup_of(i).find_all(True)) if tag is anchor)
        position = (i, order)
        if last is not None and position <= last:
            continue
        entries.append((label, i, fragment))
        last = position
    if len(entries) < 2:
        return []

    texts = [[] for _ in entries]  # pieces of each chapter's text
    sentinels = {}  # document index -> the cut marker inserted into its soup, if any
    current = None  # entry owning the text at the current point in the spine
    for i in range(len(docs)):
        here = [k for k, entry in enumerate(entries) if entry[1] == i]
        if not here and current is None:
            continue  # front matter
        cuts = [k for k in here if entries[k][2]]
        pieces, sentinels[i] = _split_at_anchors(soup_of(i), [entries[k][2] for k in cuts])
        if pieces is None:
            return []  # every private-use code point is taken: fall back to per-file chapters
        owners = [here[0] if here and not entries[here[0]][2] else current] + cuts
        for owner, piece in zip(owners, pieces):
            if owner is not None:
                texts[owner].append(piece)
        current = owners[-1]

    chapters = []
    for (label, i, _), pieces in zip(entries, texts):
        text = "\n\n".join(p for p in pieces if p)
        if not text:
            continue  # e.g. a cover image
        heading = soup_of(i).find(["h1", "h2"])
        title = (
            " ".join(label.split())
            or (heading.get_text(strip=True).replace(sentinels[i] or "", "") if heading else "")
            or f"Chapter {len(chapters) + 1}"
        )
        chapters.append(RawChapter(title=title, text=text))
    return chapters


def parse(path: str) -> ParsedBook:
    book = ebooklib_epub.read_epub(path)

    title_meta = book.get_metadata("DC", "title")
    title = title_meta[0][0] if title_meta else Path(path).stem

    author_meta = book.get_metadata("DC", "creator")
    author = author_meta[0][0] if author_meta else None

    docs = []
    for item_id, _ in book.spine:
        item = book.get_item_with_id(item_id)
        if item is not None and item.get_type() == ITEM_DOCUMENT:
            docs.append(item)

    chapters = _toc_chapters(book, docs)
    if chapters:
        return ParsedBook(title=title, author=author, chapters=chapters)

    # No usable TOC: one chapter per spine file.
    for item in docs:
        soup = _load_soup(item)
        text = _block_text(soup)

        if len(text) < MIN_CHAPTER_CHARS:
            continue  # skips cover pages, copyright blurbs, blank dividers

        heading = soup.find(["h1", "h2"])
        chapter_title = heading.get_text(strip=True) if heading else f"Chapter {len(chapters) + 1}"
        chapters.append(RawChapter(title=chapter_title, text=text))

    return ParsedBook(title=title, author=author, chapters=chapters)

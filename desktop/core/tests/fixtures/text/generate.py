"""Golden fixtures for the Rust text pipeline in desktop/core (Tauri port, phase 2).

Runs today's Python ingest -> lexicon -> normalize -> chunk_text and writes what it
produces as JSON. The Rust tests must reproduce every value byte for byte. Rerun this
only when the Python pipeline changes on purpose, and commit the result.

    .venv/bin/python desktop/core/tests/fixtures/text/generate.py
    .venv/bin/python desktop/core/tests/fixtures/text/generate.py --local BOOK.epub ...

--local writes goldens for books that must not be committed (uploads/ is
git-ignored and the books are copyrighted) into local/, which is git-ignored too.
"""
import argparse
import json
import random
import re
import sys
import unicodedata
import zipfile
from collections import namedtuple
from pathlib import Path

from ebooklib import epub as ebooklib_epub

from audiobookify.ingest import ingest_by_path
from audiobookify.lexicon.substitution import apply_lexicon
from audiobookify.normalize.text import chunk_text, expand_numbers, normalize_text, split_paragraphs, split_sentences

HERE = Path(__file__).resolve().parent
Entry = namedtuple("Entry", "term override_value")

# Applied before chunking, as synth/pipeline.py does. Terms a book lacks are no-ops.
# "Mr." never matches (\b after "." needs a word character next) and its value checks
# literal replacement; "the" -> "the" lowercases every "The", which moves sentence
# boundaries and stresses the splitter.
LEXICON = [
    ["Holmes", "HOHMZ"], ["Baker Street", "BAY-ker street"], ["Watson", "WOT-sun"],
    ["Abbess", "AB-ess"], ["Winterfell", "WIN-ter-fel"], ["Diaz", "DEE-ahs"],
    ["Mr.", "literal $1 and \\1"], ["the", "the"],
]
# (max_chars, min_chars, apply LEXICON first). 300/80 is what Turbo and Qwen3 ship
# with; 120/30 forces far more clause and word splits through the same code.
CHUNKINGS = [(300, 80, True), (120, 30, False)]

FILLER = "The rain kept on falling over the quiet town all through the night. " * 4
LONG = "The air was heavy over the marsh, and the reeds bent low under the wind from the sea. "


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def chunks_json(chunks):
    return [[c.text, c.ends_with] for c in chunks]


def book_golden(path, source, heading_level=1):
    parsed, source_format = ingest_by_path(str(path), heading_level)
    entries = [Entry(t, v) for t, v in LEXICON]
    return {
        "source": source,
        "heading_level": heading_level,
        "format": source_format.value,
        "title": parsed.title,
        "author": parsed.author,
        "chapters": [{"title": c.title, "text": c.text} for c in parsed.chapters],
        "lexicon": LEXICON,
        "chunkings": [
            {"max_chars": mx, "min_chars": mn, "lexicon": lex,
             "chapters": [chunks_json(chunk_text(apply_lexicon(c.text, entries) if lex else c.text, mx, mn))
                          for c in parsed.chapters]}
            for mx, mn, lex in CHUNKINGS
        ],
    }


# --- EPUBs written by ebooklib, the same way the Python tests build theirs ---

EBOOKLIB_BOOKS = {
    "basic": ("My Test Book", "Jane Author", [
        ("Cover", "<p>(c) 2026</p>"),
        ("Chapter One", "<h1>Chapter One</h1><p>" + "Once upon a time. " * 30 + "</p>"),
        ("Chapter Two", "<h1>Chapter Two</h1><p>" + "The story continues. " * 30 + "</p>"),
    ]),
    "blocks": ("Blocks", None, [("Chapter One", f"<h1>Chapter One</h1><p>{FILLER}</p><p>Second paragraph.</p>")]),
    "inline": ("Inline", None, [("Chapter One",
        f"<p>{FILLER}</p><p>He was <em>very</em> tired,\n   <span>so</span> he <a href='#n1'>slept</a>.</p>")]),
    "br": ("Br", None, [("Chapter One", f"<p>{FILLER}</p><p>Roses are red<br/>violets are blue.</p>")]),
    "scene": ("Scene", None, [("Chapter One",
        "<h1>Chapter One</h1>" f"<p>{FILLER}</p>" "<p>“<i>No.</i>”</p>" "<p class='break'>* * *</p>"
        "<p>Morning came, and with it a <em>terrible</em> silence that settled over the whole valley below.</p>")]),
    "dashes": ("Dashes", None, [("Chapter One",
        f"<p>{LONG}“The air is bad there, and if we were to—”</p>"
        f"<p>{LONG}They could build a city, or an aqueduct, or a—</p>"
        f"<p>{LONG}“But, Cosimo, only look–’</p>"
        f"<p>{LONG}He stopped short--</p>"
        f"<p>{LONG}The end came quickly.</p>")]),
    "dashes_inside": ("Dashes", None, [("Chapter One",
        f"<p>{LONG}She paused—only for a moment—and went on.</p><p>{LONG * 2}</p>")]),
}


def write_ebooklib_epub(path, title, author, docs):
    book = ebooklib_epub.EpubBook()
    book.set_identifier(f"id-{path.stem}")
    book.set_title(title)
    if author:
        book.add_author(author)
    items = []
    for i, (doc_title, body) in enumerate(docs):
        item = ebooklib_epub.EpubHtml(title=doc_title, file_name=f"ch{i}.xhtml")
        item.content = f"<html><body>{body}</body></html>"
        book.add_item(item)
        items.append(item)
    book.spine = items
    book.add_item(ebooklib_epub.EpubNcx())
    book.add_item(ebooklib_epub.EpubNav())
    path.parent.mkdir(parents=True, exist_ok=True)
    ebooklib_epub.write_epub(str(path), book)


# --- A hand-written EPUB with the markup ebooklib won't produce (it re-serializes
# content): Calibre's self-closing tags, odd manifests, entities, BOM, CRLF. ---

XHTML = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
         '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title><style>p {{}}</style></head>\n'
         '<body>{}</body></html>')
HANDMADE_DOCS = {
    # Calibre writes empty headings as <h1 .../>: the title is "", not the page text.
    "Text/calibre one.xhtml": XHTML.format(
        '<h1 id="h" title="Title Page" class="calibre1"/>\n'
        '<p class="c"><span class="x"/>First&nbsp;line &amp; more&#8212;and &#x2014; <a id="p1"/>text.</p>\n'
        '<div>Loose <b>div</b> text<p>nested para</p>tail of div</div><p>A<p>B</p>C</p>'
        f'<p>{FILLER}</p>'),
    "Text/two.xhtml": XHTML.format(
        '<h2>Part<!-- x --> <em>Two</em><br/>Again<![CDATA[!]]></h2>'
        '<section>Loose section text<figure>fig text</figure></section>'
        '<table><tr><td>cell1</td><td>cell2</td></tr></table>'
        '<p>ruby <ruby>漢<rt>kan</rt></ruby> and <template>tmpl</template> end</p>'
        '<script>if (a<b) {}</script><nav><p>nav text</p></nav>'
        '<p>Price: 1,000.50 dollars, ISBN 9780593420317, code 0801, 27.50, 80,000.3, COVID-19, pages 12-34b, the 1980-90s, 3-2.</p>'
        '<p>Dr. Smith vs. Mr. Jones met Mrs. Ng at St. Paul’s, etc. FIRST. The 5-year-old said 7 more.</p>'
        '<p>“Wait—”</p><p>* * *</p><p>The End.</p>'
        f'<p>{FILLER}</p>'),
    "Text/short.xhtml": XHTML.format('<p>(c) 2026</p>'),
    "Text/plain.html": XHTML.format(f'<h1>Plain HTML</h1><p>{FILLER}</p>'),
    "Text/aside.xhtml": XHTML.format(f'<h2>Aside</h2><p>one<br>two</br>three</p><p>{FILLER}</p>'),
    # No whitespace between tags: lxml's pretty-printer adds it, so "<span>a</span><span>b</span>"
    # reads "a b", and text before <body>'s first child is dropped.
    "Text/minified.xhtml": '<html><body>Lead text<div><p><span>Small</span><span>Caps</span> stay glued.</p>'
                           '<p><span>a</span><span>b</span></p><h1><span>Chapter</span><span>Nine</span></h1>'
                           f'<p><em>x</em><!-- c --><img src="i.png"/><em>y</em></p><p>{FILLER}</p></div></body></html>'
                           '<p>after body</p>',
    "Text/bytes.xhtml": XHTML.format(f'<h1>Bad\x00bytes</h1><p>caf\xe9 and \xff end</p><p>{FILLER}</p>'),
    "Text/bom.xhtml": "﻿" + XHTML.format(f'<h1>Byte\r\nOrder</h1>\r\n<p>{FILLER}</p>\r\n').replace("\n", "\r\n"),
    # The nav document is skipped even though it is in the spine (ebooklib: ITEM_NAVIGATION).
    "nav.xhtml": XHTML.format('<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc"><ol>'
                              f'<li><a href="Text/two.xhtml">Two</a></li></ol></nav><p>{FILLER}</p>'),
}
HANDMADE_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">handmade-1</dc:identifier>
    <dc:title>Handmade Book</dc:title>
    <dc:title>Ignored Second Title</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="one" href="Text/calibre%20one.xhtml" media-type="application/xhtml+xml"/>
    <item id="two" href="Text/../Text/two.xhtml" media-type="application/xhtml+xml"/>
    <item id="short" href="Text/short.xhtml" media-type="application/xhtml+xml"/>
    <item id="plain" href="Text/plain.html" media-type="text/html"/>
    <item id="aside" href="Text/aside.xhtml" media-type="application/xhtml+xml"/>
    <item id="bom" href="Text/bom.xhtml" media-type="application/xhtml+xml"/>
    <item id="minified" href="Text/minified.xhtml" media-type="application/xhtml+xml"/>
    <item id="bytes" href="Text/bytes.xhtml" media-type="application/xhtml+xml"/>
    <item id="coverpage" href="Text/plain.html" media-type="application/xhtml+xml" properties="cover"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="nav"/>
    <itemref idref="one"/>
    <itemref idref="short"/>
    <itemref idref="plain"/>
    <itemref idref="missing"/>
    <itemref idref="two"/>
    <itemref idref="aside" linear="no"/>
    <itemref idref="bom"/>
    <itemref idref="minified"/>
    <itemref idref="bytes"/>
    <itemref idref="coverpage"/>
  </spine>
</package>
"""
CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""
NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/><docTitle><text>Handmade Book</text></docTitle><navMap/></ncx>
"""


def write_handmade_epub(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", HANDMADE_OPF, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", NCX, compress_type=zipfile.ZIP_DEFLATED)
        for name, body in HANDMADE_DOCS.items():
            # latin-1 for the invalid-UTF-8 page: its \xe9 and \xff must stay single bytes.
            data = body.encode("latin-1") if name == "Text/bytes.xhtml" else body.encode("utf-8")
            z.writestr(f"OEBPS/{name}", data, compress_type=zipfile.ZIP_DEFLATED)

# --- Hand-written EPUBs whose chapters come from the table of contents. Built with
# zipfile (ebooklib rewrites content); fixed zip timestamps keep the binaries stable. ---

def _para(topic, n=6):
    return "".join(f"<p>{topic} {i}. {LONG}{FILLER.strip()}</p>" for i in range(n))


def write_zip_epub(path, title, docs, spine, ncx=None, nav=None, nav_path="nav.xhtml"):
    """docs: {path under OEBPS/: body html}; spine: doc paths in reading order."""
    ids = {name: f"d{i}" for i, name in enumerate(docs)}
    items = [f'<item id="{ids[n]}" href="{n}" media-type="application/xhtml+xml"/>' for n in docs]
    if ncx is not None:
        items.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    if nav is not None:
        items.append(f'<item id="nav" href="{nav_path}" media-type="application/xhtml+xml" properties="nav"/>')
    toc_attr = ' toc="ncx"' if ncx is not None else ""
    refs = "".join(f'<itemref idref="{ids[n]}"/>' for n in spine)
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
           'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="uid">{path.stem}</dc:identifier><dc:title>{title}</dc:title>'
           '<dc:creator>Test Author</dc:creator><dc:language>en</dc:language></metadata>'
           f'<manifest>{"".join(items)}</manifest><spine{toc_attr}>{refs}</spine></package>')
    page = ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>t</title></head><body>{}</body></html>')

    def put(z, name, data, stored=False):
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
        z.writestr(info, data.encode("utf-8"))

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        put(z, "mimetype", "application/epub+zip", stored=True)
        put(z, "META-INF/container.xml", CONTAINER)
        put(z, "OEBPS/content.opf", opf)
        for name, body in docs.items():
            put(z, f"OEBPS/{name}", page.format(body))
        if ncx is not None:
            points = "".join(
                f'<navPoint id="n{i}"><navLabel><text>{label}</text></navLabel><content src="{href}"/></navPoint>'
                for i, (label, href) in enumerate(ncx))
            put(z, "OEBPS/toc.ncx", '<?xml version="1.0" encoding="utf-8"?>\n'
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                f"<head/><docTitle><text>{title}</text></docTitle><navMap>{points}</navMap></ncx>")
        if nav is not None:
            put(z, f"OEBPS/{nav_path}", page.format(nav))


def write_calibre_split_epub(path):
    """Calibre-style: heading-only files, front matter before the first entry, back matter after the last."""
    docs = {
        "Text/titlepage.xhtml": "<h1>Split Book</h1><p>Copyright page text that is not part of any chapter.</p>" + _para("Front"),
        "Text/part1.xhtml": "<h1>BOOK I</h1>",
        "Text/ch1.xhtml": "<h2>CHAPTER ONE</h2>",
        "Text/ch1_1.xhtml": _para("First"),
        "Text/ch1_2.xhtml": _para("Second"),
        "Text/ch2.xhtml": "<h2>CHAPTER TWO</h2>",
        "Text/ch2_1.xhtml": _para("Third"),
        "Text/part2.xhtml": "<h1>BOOK II</h1>",
        "Text/ch3.xhtml": "<h2>CHAPTER THREE</h2>" + _para("Fourth"),
        "Text/about.xhtml": "<h2>About the Author</h2>" + _para("Back", 2),
    }
    ncx = [("BOOK I", "Text/part1.xhtml"), ("CHAPTER ONE", "Text/ch1.xhtml"), ("  CHAPTER\n TWO ", "Text/ch2.xhtml"),
           ("BOOK II", "Text/part2.xhtml"), ("CHAPTER THREE", "Text/ch3.xhtml")]
    write_zip_epub(path, "Calibre Split", docs, list(docs), ncx=ncx)


def write_anchors_epub(path):
    """EPUB 3 nav in another directory, nested entries, chapters by #id, <a name>, percent-encoded and
    unknown fragments, an out-of-order entry, and page-list and landmarks navs that must be ignored."""
    docs = {
        "text/a.xhtml": ("<h1>Part One</h1>" + _para("Opening", 2)
                         + '<h2 id="c1">Chapter 1</h2>' + _para("One") + '<h2 id="c2">Chapter 2</h2>' + _para("Two")
                         + '<h2><a name="c3"></a>Chapter 3</h2>' + _para("Three")),
        "text/b.xhtml": ('<h1>Part Two</h1>' + _para("Mid", 2) + '<h2 id="caf\xe9">Chapter 4</h2>' + _para("Four")
                         + '<h2 id="c5">Chapter 5</h2>' + _para("Five")),
        "text/c.xhtml": "<h2>Epilogue</h2>" + _para("Last", 3),
    }
    nav = ('<nav epub:type="landmarks"><ol><li><a href="../text/a.xhtml">Start</a></li>'
           '<li><a href="../text/c.xhtml">Epilogue landmark</a></li></ol></nav>'
           '<nav epub:type="page-list"><ol><li><a href="../text/a.xhtml#c2">1</a></li>'
           '<li><a href="../text/b.xhtml#c5">2</a></li><li><a href="../text/c.xhtml">3</a></li></ol></nav>'
           '<nav epub:type="toc"><ol>'
           '<li><a href="../text/a.xhtml">Part One</a><ol>'
           '<li><a href="../text/a.xhtml#c1">Chapter 1</a></li>'
           '<li><a href="../text/a.xhtml#c2">Chapter 2</a></li>'
           '<li><a href="../text/a.xhtml#c3">Chapter 3</a></li>'
           '<li><a href="../text/a.xhtml#c1">Out of order</a></li></ol></li>'
           '<li><a href="../text/b.xhtml#missing">Part Two</a><ol>'
           '<li><a href="../text/b.xhtml#caf%C3%A9">Chapter 4</a></li>'
           '<li><a href="../text/b.xhtml#c5">Chapter 5</a></li></ol></li>'
           '<li><a href="../text/c.xhtml">Epilogue</a></li></ol></nav>')
    write_zip_epub(path, "Anchors", docs, list(docs), nav=nav, nav_path="nav/toc.xhtml")


MARKDOWN = {
    "basic.md": "# Chapter One: A Beginning\n\nThis is **bold** and _italic_ text with a [link](https://example.com).\n\n"
                "# Chapter Two\n\nMore `code` and content here.\n",
    "flat.md": "Just plain text, no headings.",
    "nested.md": "## Part A\nfirst\n## Part B\nsecond\n",
    "messy.md": "﻿Preamble before any heading.\r\n\r\n# One  \r\nSee ![a map](map.png) and [the docs](http://x.y).\r\n"
                "```\r\ncode block\r\n```\r\n***Bold italic*** and __under__ and *a*b*.\r\n\r\n#NoSpace heading\r\n"
                "#   Two\rOld Mac line\r\n\r\n### deep heading\r\n\r\nDr. Who has 3 cats.\r\n\r\n* * *\r\n\r\nEnd.\r\n",
}
MARKDOWN_LEVELS = {"nested.md": 2}


# --- Unit-level cases ---

def number_inputs():
    rng = random.Random(2026)
    raws = [str(n) for n in range(0, 1101)]
    raws += [str(rng.randrange(10 ** k)) for k in range(4, 40)] + [str(rng.randrange(10 ** k)) for k in range(40, 307, 11)]
    raws += [str(10 ** k) for k in range(0, 306, 3)] + [str(10 ** 306 - 1), str(10 ** 306)]
    raws += ["0801", "06", "007", "10,000", "30,000,000", "1,,2", "9780593420317", "2021018182"]
    raws += ["1.5", "27.50", "80,000.3", "19.15", "0.1", "1.05", "3.14159", "2.675", "0.00001", "0.0000001",
             "1.10", "5.0", "10.00", "12345678901234567.0", "9007199254740993.00", "0.999999999999999999",
             "123456789.123", "1." + "0" * 30 + "1", "0." + "0" * 40 + "7"]
    for _ in range(500):
        whole = rng.randrange(10 ** rng.randint(1, 15))
        frac = "".join(rng.choice("0123456789") for _ in range(rng.randint(1, 18)))
        raws.append(f"{whole}.{frac}")
    texts = [f"Take {r} now." for r in raws]
    texts += ["She had COVID-19 last year.", "Room 7 - the attic.", "A 5-year-old, a 10-minute break.",
              "See pages 12-34.", "They won 3-2.", "See pages 12-34b for details.", "The 1980-90s recession.",
              "Table 3-2a shows it.", "Version 1.2.3 shipped.", "a1b2 c3", "the 1,000,000th visitor",
              "x-7 and -7 and 7-", "Chapters 1-3 cover it.", "1,000.5,000", "4.5.6.7", "12,", "_7_", "7_000",
              "3½ cups", "10x faster", "Q3 2026", "007: 0.5% of 1,234.5"]
    return texts


def number_cases():
    cases, skipped = [], 0
    for text in number_inputs():
        try:
            cases.append([text, expand_numbers(text)])
        except OverflowError:  # num2words above 10^306; Rust leaves those as written
            skipped += 1
    return cases, skipped


def random_text(rng):
    words = ["the", "a", "river", "old", "mill", "she", "said", "quietly", "darkness", "No", "Yes",
             "Dr.", "7", "1,200", "“Go!”", "(it", "was)", "—", "half-", "3.5", "St."]
    punct = [".", ".", "!", "?", ",", ";", ":", "", "", "—", "…", "”", "'"]
    paragraphs = []
    for _ in range(rng.randint(1, 8)):
        sentences = []
        for _ in range(rng.randint(1, 6)):
            body = " ".join(rng.choice(words) + rng.choice(punct) for _ in range(rng.randint(1, 40)))
            sentences.append(body.rstrip(".,;!?") + rng.choice([".", "?", "!", "—", ".”"]))
        paragraphs.append(" ".join(sentences))
        if rng.random() < 0.1:
            paragraphs.append(rng.choice(["* * *", "#", "⁂", "~~~"]))
    return rng.choice(["\n\n", "\n \n", "\n\n\n"]).join(paragraphs)


def chunk_cases():
    fixed = [
        ("Just one short sentence.", 300, 0), ("Six went home. 7 more came.", 20, 0),
        ("First paragraph here.\n\nSecond paragraph here.", 300, 0),
        ("Aaaa aaaa aaaa. Bbbb bbbb bbbb. Cccc cccc cccc.", 32, 0),
        ("He walked to the old mill, slowly; the river ran past it, cold and grey.", 50, 0),
        ("He walked to the old mill by the river, and the water ran past it cold and grey.", 50, 0),
        ("Yes; he walked to the old mill by the river and the water ran past it cold and grey all day.", 60, 20),
        ('"No."\n\nShe looked at him for a long moment before answering.', 300, 20),
        ("She looked at him for a long moment.\n\nNo.", 300, 20),
        ("Chapter One\n\nIt was a dark and stormy night.", 300, 20),
        ("No.\n\nYes.\n\nMaybe.\n\nThat settles it then, I suppose.", 300, 15),
        ("Hi.", 300, 80), ("* * *", 300, 0),
        ("The door closed behind him for good.\n\n* * *\n\nMorning came slowly over the hills.", 300, 10),
        ("The door closed behind him for good.\n\nThe end.\n\n#\n\nMorning came slowly over the hills.", 300, 20),
        ("The door closed behind him for good.\n\n* * *\n\nThe End.", 300, 20),
        (" ".join(["word"] * 200), 100, 0), ("x" * 500 + " tail", 100, 20), ("", 300, 80),
        (" ".join(f"Sentence number {i}." for i in range(20)), 50, 0),
    ]
    cases = [[t, mx, mn, chunks_json(chunk_text(t, mx, mn))] for t, mx, mn in fixed]
    rng = random.Random(1234)
    for mx, mn in [(120, 30)] * 200 + [(300, 80)] * 100 + [(50, 10)] * 50 + [(80, 0)] * 50:
        text = random_text(rng)
        cases.append([text, mx, mn, chunks_json(chunk_text(text, mx, mn))])
    return cases


def lexicon_cases():
    cases = [
        ("Cadeyrn walked into the hall.", [["cadeyrn", "KAH-dern"]]),
        ("Anne-Marie waved at Ann.", [["Ann", "ANN-override"]]),
        ("She flew to New York yesterday.", [["New York", "Noo Yawk"]]),
        ("Nothing to substitute here.", [["Cadeyrn", "KAH-dern"]]),
        ("Cadeyrn spoke.", [["Cadeyrn", "book-specific-version"], ["Cadeyrn", "global-version"]]),
        ("Cadeyrn spoke.", [["Cadeyrn", "KAH\\dern"]]),
        ("Cadeyrn spoke.", [["Cadeyrn", "$1 ${0} \\g<0>"]]),
        ("She flew to New York yesterday.", [["York", "Yorrk"], ["New York", "Noo Yawk"]]),
        ("She flew to New York yesterday.", [["New York", "Noo Yawk"], ["York", "Yorrk"]]),
        ("ÉLODIE and élodie and Élodie.", [["élodie", "ay-LO-dee"]]),
        ("Mr. Smith and C++ and a.b.", [["Mr.", "Mister"], ["C++", "see plus plus"], ["a.b", "A B"]]),
        ("Straße and STRASSE.", [["straße", "STRAH-seh"]]),
        ("Ab ab abab", [["ab", "x"], ["b", "y"]]),
        ("one two", [["", "_"]]),
        ("Zoë's café.", [["Zoë", "ZOH-ee"], ["café", "ka-FAY"]]),
    ]
    out = []
    for text, entries in cases:
        out.append([text, entries, apply_lexicon(text, [Entry(t, v) for t, v in entries])])
    return out


def char_ranges(pred):
    """[[first, last], ...] code point ranges where pred holds."""
    ranges = []
    for c in range(0x110000):
        if pred(chr(c)):
            if ranges and ranges[-1][1] == c - 1:
                ranges[-1][1] = c
            else:
                ranges.append([c, c])
    return ranges


def cases():
    numbers, skipped = number_cases()
    print(f"numbers: {len(numbers)} cases ({skipped} skipped: Python raises above 10^306)")
    paragraph_inputs = ["One\nstill one.\n\n  Two.  \n \n\nThree.", "Line one.\nLine two.", "\n\n", "",
                        "a\n\u001c\nb", "a\n \nb", "a\r\n\r\nb", "x   y\n\t\nz"]
    sentence_inputs = ["First sentence. Second sentence! Third one?", '"I\'m leaving." She turned away.',
                       "“Go now!” He ran. (It was late.) Then dawn came.", 'He waited. "Hello?" No answer.',
                       "'Why?' he asked. She shrugged.", "Wait… 3 more. ‘Yes.’ [Note.] Fine.", "e.g. this. no split",
                       "A. B. C.\u001cD.", "End?!” “Start.", "Ellipsis... Then."]
    normalize_inputs = ["Dr. Smith arrived.", "I have 42 apples.", "Mrs. and Mr. and Ms. and Prof.",
                        "FIRST. Fast. St. devs. vs. etc. etc.", "Prof.Dr. 3 cats", "Mr.Mrs."]
    return {
        "python_whitespace": [c for c in range(0x110000) if chr(c).isspace()],
        # re's \w, and the code points this Python's Unicode tables leave unassigned (a
        # newer Unicode in Rust may call some of those letters).
        "python_word": char_ranges(lambda ch: re.match(r"\w", ch) is not None),
        "python_unassigned": char_ranges(lambda ch: unicodedata.category(ch) == "Cn"),
        "numbers": numbers,
        "normalize": [[t, normalize_text(t)] for t in normalize_inputs],
        "sentences": [[t, split_sentences(t)] for t in sentence_inputs],
        "paragraphs": [[t, split_paragraphs(t)] for t in paragraph_inputs],
        "chunks": chunk_cases(),
        "lexicon": lexicon_cases(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local", nargs="+", metavar="BOOK", help="books whose goldens go to local/ (not committed)")
    args = parser.parse_args()

    if args.local:
        for book in args.local:
            path = Path(book).resolve()
            write(HERE / "local" / f"{path.name}.json", book_golden(path, str(path)))
            print(f"local/{path.name}.json")
        return

    for name, (title, author, docs) in EBOOKLIB_BOOKS.items():
        write_ebooklib_epub(HERE / "epub" / f"{name}.epub", title, author, docs)
    write_handmade_epub(HERE / "epub" / "handmade.epub")
    write_calibre_split_epub(HERE / "epub" / "calibre_split.epub")
    write_anchors_epub(HERE / "epub" / "anchors.epub")
    for name, text in MARKDOWN.items():
        (HERE / "md").mkdir(exist_ok=True)
        (HERE / "md" / name).write_bytes(text.encode("utf-8"))

    sources = sorted((HERE / "epub").glob("*.epub")) + sorted((HERE / "md").glob("*.md"))
    for path in sources:
        source = path.relative_to(HERE).as_posix()
        write(HERE / "golden" / f"{path.name}.json", book_golden(path, source, MARKDOWN_LEVELS.get(path.name, 1)))
        print(f"golden/{path.name}.json")
    write(HERE / "cases.json", cases())
    print("cases.json")


if __name__ == "__main__":
    main()

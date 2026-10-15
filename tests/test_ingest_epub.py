import zipfile
from urllib.parse import quote
from pathlib import Path

import pytest
from ebooklib import epub as ebooklib_epub
from audiobookify.ingest import epub


def _build_epub(path):
    book = ebooklib_epub.EpubBook()
    book.set_identifier("id123")
    book.set_title("My Test Book")
    book.add_author("Jane Author")

    cover = ebooklib_epub.EpubHtml(title="Cover", file_name="cover.xhtml")
    cover.content = "<html><body><p>(c) 2026</p></body></html>"  # short boilerplate

    ch1 = ebooklib_epub.EpubHtml(title="Chapter One", file_name="ch1.xhtml")
    ch1.content = (
        "<html><body><h1>Chapter One</h1><p>" + ("Once upon a time. " * 30) + "</p></body></html>"
    )

    ch2 = ebooklib_epub.EpubHtml(title="Chapter Two", file_name="ch2.xhtml")
    ch2.content = (
        "<html><body><h1>Chapter Two</h1><p>" + ("The story continues. " * 30) + "</p></body></html>"
    )

    for item in (cover, ch1, ch2):
        book.add_item(item)
    book.spine = [cover, ch1, ch2]
    book.add_item(ebooklib_epub.EpubNcx())
    book.add_item(ebooklib_epub.EpubNav())

    ebooklib_epub.write_epub(str(path), book)


def test_parse_epub_extracts_title_author_and_skips_short_boilerplate(tmp_path):
    epub_path = tmp_path / "book.epub"
    _build_epub(epub_path)

    parsed = epub.parse(str(epub_path))

    assert parsed.title == "My Test Book"
    assert parsed.author == "Jane Author"
    assert [c.title for c in parsed.chapters] == ["Chapter One", "Chapter Two"]
    assert "Once upon a time." in parsed.chapters[0].text


def _epub_with_body(path, body_html):
    book = ebooklib_epub.EpubBook()
    book.set_identifier("id-blocks")
    book.set_title("Blocks")
    ch = ebooklib_epub.EpubHtml(title="Chapter One", file_name="ch1.xhtml")
    ch.content = f"<html><body>{body_html}</body></html>"
    book.add_item(ch)
    book.spine = [ch]
    book.add_item(ebooklib_epub.EpubNcx())
    book.add_item(ebooklib_epub.EpubNav())
    ebooklib_epub.write_epub(str(path), book)
    return epub.parse(str(path)).chapters[0].text


FILLER = "The rain kept on falling over the quiet town all through the night. " * 4


def test_parse_epub_separates_block_elements_with_blank_lines(tmp_path):
    text = _epub_with_body(tmp_path / "b.epub", f"<h1>Chapter One</h1><p>{FILLER}</p><p>Second paragraph.</p>")
    assert text.split("\n\n") == ["Chapter One", FILLER.strip(), "Second paragraph."]


def test_parse_epub_keeps_inline_markup_and_source_wrapping_inside_one_paragraph(tmp_path):
    text = _epub_with_body(
        tmp_path / "b.epub",
        f"<p>{FILLER}</p><p>He was <em>very</em> tired,\n   <span>so</span> he <a href='#n1'>slept</a>.</p>",
    )
    assert text.split("\n\n")[1] == "He was very tired, so he slept."


def test_parse_epub_treats_br_as_a_space(tmp_path):
    text = _epub_with_body(tmp_path / "b.epub", f"<p>{FILLER}</p><p>Roses are red<br/>violets are blue.</p>")
    assert text.split("\n\n")[1] == "Roses are red violets are blue."


def test_real_epub_output_chunks_into_paragraph_aware_chunks(tmp_path):
    """End to end: real ingest output (not a hand-built string) through the chunker."""
    from audiobookify.normalize.text import Chunk, chunk_text

    text = _epub_with_body(
        tmp_path / "b.epub",
        "<h1>Chapter One</h1>"
        f"<p>{FILLER}</p>"
        "<p>“<i>No.</i>”</p>"
        "<p class='break'>* * *</p>"
        "<p>Morning came, and with it a <em>terrible</em> silence that settled over the whole valley below.</p>",
    )
    chunks = chunk_text(text, max_chars=300, min_chars=80)

    assert chunks == [
        Chunk("Chapter One. " + FILLER.strip() + " “No.”", "scene"),
        Chunk("Morning came, and with it a terrible silence that settled over the whole valley below.", "paragraph"),
    ]


# --- chapters from the table of contents -------------------------------------
# Built with zipfile, not ebooklib, which rewrites the content it writes.

def _zip_epub(path, docs, ncx=None, nav=None, spine=None, nav_path="nav.xhtml"):
    """docs: {file name under OEBPS/: body html}. ncx: [(label, href)]. nav: raw nav html."""
    items = [
        f'<item id="d{i}" href="{quote(name)}" media-type="application/xhtml+xml"/>'
        for i, name in enumerate(docs)
    ]
    ids = {name: f"d{i}" for i, name in enumerate(docs)}
    if ncx is not None:
        items.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    if nav is not None:
        items.append(f'<item id="nav" href="{nav_path}" media-type="application/xhtml+xml" properties="nav"/>')
    spine = spine or list(docs)
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">x</dc:identifier><dc:title>Zip Book</dc:title>'
        '<dc:creator>Zed</dc:creator></metadata>'
        f'<manifest>{"".join(items)}</manifest>'
        f'<spine{" toc=\"ncx\"" if ncx is not None else ""}>{"".join(f"<itemref idref=\"{ids[n]}\"/>" for n in spine)}</spine></package>'
    )
    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        '</rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        for name, body in docs.items():
            z.writestr(
                f"OEBPS/{name}",
                f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>'
                f"<body>{body}</body></html>",
            )
        if ncx is not None:
            points = "".join(
                f'<navPoint id="n{i}"><navLabel><text>{label}</text></navLabel>'
                f'<content src="{href}"/></navPoint>'
                for i, (label, href) in enumerate(ncx)
            )
            z.writestr(
                "OEBPS/toc.ncx",
                '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                f"<navMap>{points}</navMap></ncx>",
            )
        if nav is not None:
            z.writestr(
                f"OEBPS/{nav_path}",
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                f"<body>{nav}</body></html>",
            )


def _parse(tmp_path, *args, **kwargs):
    path = tmp_path / "zip.epub"
    _zip_epub(path, *args, **kwargs)
    return epub.parse(str(path)).chapters


def _titles(chapters):
    return [c.title for c in chapters]


def test_toc_chapters_join_split_files_and_keep_heading_files(tmp_path):
    docs = {
        "h1.xhtml": "<h1>CHAPTER ONE</h1>",
        "a.xhtml": "<p>First part.</p>",
        "b.xhtml": "<p>Second part.</p>",
        "book1.xhtml": "<h1>BOOK I</h1>",
        "h2.xhtml": "<h1>CHAPTER TWO</h1>",
        "c.xhtml": "<p>Third part.</p>",
    }
    chapters = _parse(
        tmp_path,
        docs,
        ncx=[("CHAPTER ONE", "h1.xhtml"), ("BOOK I", "book1.xhtml"), ("CHAPTER TWO", "h2.xhtml")],
    )
    assert _titles(chapters) == ["CHAPTER ONE", "BOOK I", "CHAPTER TWO"]
    assert chapters[0].text == "CHAPTER ONE\n\nFirst part.\n\nSecond part."
    assert chapters[1].text == "BOOK I"
    assert chapters[2].text == "CHAPTER TWO\n\nThird part."


def test_pages_before_the_first_toc_entry_are_dropped(tmp_path):
    docs = {"front.xhtml": "<p>Acclaim.</p>", "a.xhtml": "<p>One.</p>", "b.xhtml": "<p>Two.</p>"}
    chapters = _parse(tmp_path, docs, ncx=[("A", "a.xhtml"), ("B", "b.xhtml")])
    assert [c.text for c in chapters] == ["One.", "Two."]


def test_back_matter_stays_in_the_last_chapter(tmp_path):
    docs = {"a.xhtml": "<p>One.</p>", "b.xhtml": "<p>Two.</p>", "notes.xhtml": "<p>Notes.</p>"}
    chapters = _parse(tmp_path, docs, ncx=[("A", "a.xhtml"), ("B", "b.xhtml")])
    assert chapters[1].text == "Two.\n\nNotes."


def test_anchor_entries_split_one_file_into_chapters(tmp_path):
    docs = {
        "all.xhtml": (
            "<p>Preface.</p>"
            '<h2 id="c1">One</h2><p>Alpha beta <a id="mid"></a>gamma delta.</p>'
            '<h2><a name="c2"></a>Two</h2><p>Epsilon.</p>'
        ),
    }
    ncx = [("One", "all.xhtml#c1"), ("Mid", "all.xhtml#mid"), ("Two", "all.xhtml#c2")]
    chapters = _parse(tmp_path, docs, ncx=ncx)
    assert _titles(chapters) == ["One", "Mid", "Two"]
    assert chapters[0].text == "One\n\nAlpha beta"
    assert chapters[1].text == "gamma delta."
    assert chapters[2].text == "Two\n\nEpsilon."


def test_nav_document_wins_over_ncx_and_nesting_is_flattened(tmp_path):
    docs = {"a.xhtml": "<p>A.</p>", "b.xhtml": "<p>B.</p>", "c.xhtml": "<p>C.</p>"}
    nav = (
        '<nav epub:type="toc"><ol>'
        '<li><a href="a.xhtml">Part  One</a><ol><li><a href="b.xhtml">Nested</a></li></ol></li>'
        '<li><a href="c.xhtml">Last</a></li></ol></nav>'
    )
    chapters = _parse(tmp_path, docs, ncx=[("X", "a.xhtml"), ("Y", "c.xhtml")], nav=nav)
    assert _titles(chapters) == ["Part One", "Nested", "Last"]


def test_page_list_and_landmarks_are_ignored(tmp_path):
    docs = {"a.xhtml": "<p>A.</p>", "b.xhtml": "<p>B.</p>", "c.xhtml": "<p>C.</p>"}
    nav = (
        '<nav epub:type="landmarks"><ol><li><a href="c.xhtml">Start</a></li></ol></nav>'
        '<nav epub:type="toc"><ol><li><a href="a.xhtml">A</a></li>'
        '<li><a href="b.xhtml">B</a></li></ol></nav>'
        '<nav epub:type="page-list"><ol><li><a href="c.xhtml">3</a></li></ol></nav>'
    )
    assert _titles(_parse(tmp_path, docs, nav=nav)) == ["A", "B"]


def test_ncx_page_list_is_ignored(tmp_path):
    docs = {"a.xhtml": "<p>A.</p>", "b.xhtml": "<p>B.</p>", "c.xhtml": "<p>C.</p>"}
    path = tmp_path / "pl.epub"
    _zip_epub(path, docs, ncx=[("A", "a.xhtml"), ("B", "b.xhtml")])
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    files["OEBPS/toc.ncx"] = files["OEBPS/toc.ncx"].replace(
        b"</ncx>",
        b'<pageList><pageTarget><navLabel><text>3</text></navLabel>'
        b'<content src="c.xhtml"/></pageTarget></pageList></ncx>',
    )
    with zipfile.ZipFile(path, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    assert _titles(epub.parse(str(path)).chapters) == ["A", "B"]


def test_private_use_char_already_in_the_text_is_kept_when_an_anchor_splits(tmp_path):
    docs = {"all.xhtml": '<p>x\ue000y</p><p id="c">C</p>'}
    chapters = _parse(tmp_path, docs, ncx=[("A", "all.xhtml"), ("C", "all.xhtml#c")])
    assert _titles(chapters) == ["A", "C"]
    assert [c.text for c in chapters] == ["x\ue000y", "C"]


def test_anchors_in_one_file_listed_out_of_order_skip_the_second(tmp_path):
    docs = {"all.xhtml": '<p id="a">A.</p><p id="b">B.</p><p id="c">C.</p>'}
    ncx = [("A", "all.xhtml#a"), ("C", "all.xhtml#c"), ("B late", "all.xhtml#b")]
    chapters = _parse(tmp_path, docs, ncx=ncx)
    assert _titles(chapters) == ["A", "C"]
    assert [c.text for c in chapters] == ["A.\n\nB.", "C."]


def test_top_of_file_entry_and_anchor_entries_share_a_file(tmp_path):
    docs = {"all.xhtml": '<p>Intro.</p><h2 id="c1">One</h2><p>x</p>'}
    chapters = _parse(tmp_path, docs, ncx=[("Top", "all.xhtml"), ("One", "all.xhtml#c1")])
    assert _titles(chapters) == ["Top", "One"]
    assert [c.text for c in chapters] == ["Intro.", "One\n\nx"]


def test_anchor_on_a_br_splits_the_paragraph(tmp_path):
    docs = {"all.xhtml": '<p>one<br id="b"/>two</p>'}
    chapters = _parse(tmp_path, docs, ncx=[("A", "all.xhtml"), ("B", "all.xhtml#b")])
    assert [c.text for c in chapters] == ["one", "two"]


def test_unknown_fragment_starts_at_top_of_file(tmp_path):
    docs = {"a.xhtml": "<p>A.</p>", "b.xhtml": "<p>B.</p>"}
    chapters = _parse(tmp_path, docs, ncx=[("A", "a.xhtml#nope"), ("B", "b.xhtml")])
    assert [c.text for c in chapters] == ["A.", "B."]


def test_out_of_order_and_duplicate_entries_are_skipped(tmp_path):
    docs = {"a.xhtml": "<p>A.</p>", "b.xhtml": "<p>B.</p>", "c.xhtml": "<p>C.</p>"}
    ncx = [
        ("A", "a.xhtml"),
        ("C", "c.xhtml"),
        ("B late", "b.xhtml"),  # before the previous kept entry
        ("C again", "c.xhtml"),  # same position as the previous kept entry
        ("Outside", "elsewhere.xhtml"),  # not in the spine
    ]
    chapters = _parse(tmp_path, docs, ncx=ncx)
    assert _titles(chapters) == ["A", "C"]
    assert chapters[0].text == "A.\n\nB."


def test_entry_with_no_text_is_dropped(tmp_path):
    docs = {"cover.xhtml": '<img src="c.jpg"/>', "a.xhtml": "<p>A.</p>", "b.xhtml": "<p>B.</p>"}
    ncx = [("Cover", "cover.xhtml"), ("A", "a.xhtml"), ("B", "b.xhtml")]
    assert _titles(_parse(tmp_path, docs, ncx=ncx)) == ["A", "B"]


def test_empty_label_falls_back_to_heading_then_chapter_number(tmp_path):
    docs = {"a.xhtml": "<h1>Heading</h1><p>A.</p>", "b.xhtml": "<p>B.</p>"}
    chapters = _parse(tmp_path, docs, ncx=[("  ", "a.xhtml"), ("", "b.xhtml")])
    assert _titles(chapters) == ["Heading", "Chapter 2"]


def test_toc_hrefs_resolve_against_the_toc_files_directory(tmp_path):
    docs = {"my a.xhtml": "<p>A.</p>", "b.xhtml": "<p>B.</p>"}
    nav = (
        '<nav epub:type="toc"><ol><li><a href="my%20a.xhtml">A</a></li>'
        '<li><a href="./sub/../b.xhtml#">B</a></li></ol></nav>'
    )
    assert _titles(_parse(tmp_path, docs, nav=nav)) == ["A", "B"]


def test_fragment_is_percent_decoded(tmp_path):
    docs = {"a.xhtml": '<p>Top.</p><h2 id="\u00e9t\u00e9">Summer</h2><p>Sun.</p>'}
    chapters = _parse(tmp_path, docs, ncx=[("Top", "a.xhtml"), ("Summer", "a.xhtml#%C3%A9t%C3%A9")])
    assert [c.text for c in chapters] == ["Top.", "Summer\n\nSun."]


def test_anchor_in_a_later_file_leaves_its_top_in_the_previous_chapter(tmp_path):
    docs = {
        "a.xhtml": "<p>A.</p>",
        "b.xhtml": '<p>B top.</p><h2 id="x">Two</h2><p>B rest.</p>',
        "c.xhtml": "<p>C.</p>",
    }
    chapters = _parse(tmp_path, docs, ncx=[("A", "a.xhtml"), ("Two", "b.xhtml#x")])
    assert [c.text for c in chapters] == ["A.\n\nB top.", "Two\n\nB rest.\n\nC."]


def test_toc_in_another_directory_resolves_relative_to_itself(tmp_path):
    docs = {"text/a.xhtml": "<p>A.</p>", "text/b.xhtml": "<p>B.</p>"}
    nav = (
        '<nav epub:type="toc"><ol><li><a href="../text/a.xhtml">A</a></li>'
        '<li><a href="../text/b.xhtml">B</a></li></ol></nav>'
    )
    assert _titles(_parse(tmp_path, docs, nav=nav, nav_path="nav/nav.xhtml")) == ["A", "B"]


def test_nav_with_one_entry_falls_back_to_per_file_chapters(tmp_path):
    long = "<p>" + "Once upon a time. " * 20 + "</p>"
    docs = {"a.xhtml": long, "b.xhtml": long}
    nav = '<nav epub:type="toc"><ol><li><a href="a.xhtml">A</a></li></ol></nav>'
    assert _titles(_parse(tmp_path, docs, nav=nav)) == ["Chapter 1", "Chapter 2"]


def test_books_without_a_usable_toc_keep_per_file_chapters(tmp_path):
    long = "<p>" + "Once upon a time. " * 20 + "</p>"
    docs = {"a.xhtml": long, "short.xhtml": "<p>tiny</p>", "b.xhtml": long}
    for ncx in (None, [], [("A", "a.xhtml")]):  # no TOC, empty TOC, one entry
        chapters = _parse(tmp_path, docs, ncx=ncx)
        assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]


AUGUSTUS = Path("/Volumes/Untitled/Augustus - Williams, John.epub")


@pytest.mark.skipif(not AUGUSTUS.exists(), reason="local-only book")
def test_augustus_shape():
    chapters = epub.parse(str(AUGUSTUS)).chapters
    assert len(chapters) == 17
    assert chapters[0].title == "PROLOGUE"
    assert chapters[2].title == "CHAPTER ONE"
    assert chapters[2].text.startswith(
        "CHAPTER ONE\n\nI. The Memoirs of Marcus Agrippa: Fragments (13 B.c.)\n\n"
    )
    assert "I was with him at Actium" in chapters[2].text

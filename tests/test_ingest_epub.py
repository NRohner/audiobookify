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

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

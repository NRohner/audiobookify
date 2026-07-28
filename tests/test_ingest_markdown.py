from audiobookify.ingest import markdown

SAMPLE_MD = """# Chapter One: A Beginning

This is **bold** and _italic_ text with a [link](https://example.com).

# Chapter Two

More `code` and content here.
"""


def test_parse_markdown_splits_on_top_level_headings(tmp_path):
    md_path = tmp_path / "book.md"
    md_path.write_text(SAMPLE_MD, encoding="utf-8")

    parsed = markdown.parse(str(md_path))

    assert [c.title for c in parsed.chapters] == ["Chapter One: A Beginning", "Chapter Two"]
    assert "bold" in parsed.chapters[0].text
    assert "**" not in parsed.chapters[0].text
    assert "link" in parsed.chapters[0].text
    assert "https://example.com" not in parsed.chapters[0].text


def test_parse_markdown_with_no_headings_returns_single_chapter(tmp_path):
    md_path = tmp_path / "flat.md"
    md_path.write_text("Just plain text, no headings.", encoding="utf-8")

    parsed = markdown.parse(str(md_path))

    assert len(parsed.chapters) == 1
    assert parsed.chapters[0].title == "flat"


def test_parse_markdown_respects_configurable_heading_level(tmp_path):
    md_path = tmp_path / "nested.md"
    md_path.write_text("## Part A\nfirst\n## Part B\nsecond\n", encoding="utf-8")

    parsed = markdown.parse(str(md_path), heading_level=2)

    assert [c.title for c in parsed.chapters] == ["Part A", "Part B"]

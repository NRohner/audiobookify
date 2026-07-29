import pytest

from audiobookify.ingest import ingest_by_path
from audiobookify.models import SourceFormat


def test_ingest_by_path_parses_markdown(tmp_path):
    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody.\n", encoding="utf-8")

    parsed, source_format = ingest_by_path(str(md_path))

    assert source_format == SourceFormat.MARKDOWN
    assert parsed.chapters[0].title == "Chapter One"


def test_ingest_by_path_rejects_unsupported_suffix(tmp_path):
    bad_path = tmp_path / "book.txt"
    bad_path.write_text("hello", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported input format"):
        ingest_by_path(str(bad_path))

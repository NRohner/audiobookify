//! Port of tests/test_ingest_markdown.py.

use audiobookify_core::ingest::markdown;

const SAMPLE_MD: &str = "# Chapter One: A Beginning

This is **bold** and _italic_ text with a [link](https://example.com).

# Chapter Two

More `code` and content here.
";

fn write(name: &str, text: &str) -> (tempfile::TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join(name);
    std::fs::write(&path, text).unwrap();
    (dir, path)
}

#[test]
fn parse_markdown_splits_on_top_level_headings() {
    let (_dir, path) = write("book.md", SAMPLE_MD);

    let parsed = markdown::parse(&path, 1).unwrap();

    let titles: Vec<&str> = parsed.chapters.iter().map(|c| c.title.as_str()).collect();
    assert_eq!(titles, ["Chapter One: A Beginning", "Chapter Two"]);
    let text = &parsed.chapters[0].text;
    assert!(text.contains("bold"));
    assert!(!text.contains("**"));
    assert!(text.contains("link"));
    assert!(!text.contains("https://example.com"));
}

#[test]
fn parse_markdown_with_no_headings_returns_single_chapter() {
    let (_dir, path) = write("flat.md", "Just plain text, no headings.");

    let parsed = markdown::parse(&path, 1).unwrap();

    assert_eq!(parsed.chapters.len(), 1);
    assert_eq!(parsed.chapters[0].title, "flat");
}

#[test]
fn parse_markdown_respects_configurable_heading_level() {
    let (_dir, path) = write("nested.md", "## Part A\nfirst\n## Part B\nsecond\n");

    let parsed = markdown::parse(&path, 2).unwrap();

    let titles: Vec<&str> = parsed.chapters.iter().map(|c| c.title.as_str()).collect();
    assert_eq!(titles, ["Part A", "Part B"]);
}

#[test]
fn parse_markdown_reads_crlf_and_cr_as_newlines() {
    let (_dir, path) = write("crlf.md", "# One\r\nfirst\r# Two\r\nsecond");

    let parsed = markdown::parse(&path, 1).unwrap();

    let chapters: Vec<(&str, &str)> = parsed.chapters.iter().map(|c| (c.title.as_str(), c.text.as_str())).collect();
    assert_eq!(chapters, [("One", "first"), ("Two", "second")]);
}

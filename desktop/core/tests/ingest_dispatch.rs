//! Port of tests/test_ingest_dispatch.py.

use audiobookify_core::ingest::{ingest_by_path, IngestError};
use audiobookify_core::models::SourceFormat;

#[test]
fn ingest_by_path_parses_markdown() {
    let dir = tempfile::tempdir().unwrap();
    let md_path = dir.path().join("book.md");
    std::fs::write(&md_path, "# Chapter One\nBody.\n").unwrap();

    let (parsed, source_format) = ingest_by_path(&md_path, 1).unwrap();

    assert_eq!(source_format, SourceFormat::Markdown);
    assert_eq!(parsed.chapters[0].title, "Chapter One");
}

#[test]
fn ingest_by_path_rejects_unsupported_suffix() {
    let dir = tempfile::tempdir().unwrap();
    let bad_path = dir.path().join("book.txt");
    std::fs::write(&bad_path, "hello").unwrap();

    let err = ingest_by_path(&bad_path, 1).unwrap_err();

    assert!(matches!(err, IngestError::UnsupportedFormat(_)));
    assert_eq!(err.to_string(), "unsupported input format: .txt");
}

#[test]
fn ingest_by_path_matches_the_suffix_case_insensitively() {
    let dir = tempfile::tempdir().unwrap();
    let md_path = dir.path().join("BOOK.MD");
    std::fs::write(&md_path, "Body.").unwrap();
    assert_eq!(ingest_by_path(&md_path, 1).unwrap().1, SourceFormat::Markdown);
}

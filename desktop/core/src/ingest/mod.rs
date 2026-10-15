//! Books in, chapters out: `src/audiobookify/ingest/` in Rust.

#[allow(dead_code)] // epub.rs (Task 8) is its only caller
mod html;
pub mod markdown;

use crate::models::RawChapter;

#[derive(Debug, Clone, PartialEq)]
pub struct ParsedBook {
    pub title: String,
    pub author: Option<String>,
    pub chapters: Vec<RawChapter>,
}

#[derive(Debug, thiserror::Error)]
pub enum IngestError {
    #[error("unsupported input format: {0}")]
    UnsupportedFormat(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error("bad EPUB: {0}")]
    Epub(String),
}

//! Books in, chapters out: `src/audiobookify/ingest/` in Rust.

pub mod epub;
mod html;
pub mod markdown;

use std::path::Path;

use crate::models::{RawChapter, SourceFormat};

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

/// `ingest_by_path`: pick the parser from the file suffix.
pub fn ingest_by_path(path: impl AsRef<Path>, heading_level: usize) -> Result<(ParsedBook, SourceFormat), IngestError> {
    let path = path.as_ref();
    // Python's Path.suffix: "" for "book." and for ".epub" alone.
    let suffix = match path.extension().map(|e| e.to_string_lossy().to_lowercase()) {
        Some(ext) if !ext.is_empty() => format!(".{ext}"),
        _ => String::new(),
    };
    match suffix.as_str() {
        ".epub" => Ok((epub::parse(path)?, SourceFormat::Epub)),
        ".md" => Ok((markdown::parse(path, heading_level)?, SourceFormat::Markdown)),
        _ => Err(IngestError::UnsupportedFormat(suffix)),
    }
}

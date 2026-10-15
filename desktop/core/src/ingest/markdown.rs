use std::path::Path;
use std::sync::LazyLock;

use regex::{Captures, Regex};

use super::{IngestError, ParsedBook};
use crate::models::RawChapter;
use crate::pystr::{self, WS};

static CODE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"`{1,3}[^`]*`{1,3}").unwrap());
static IMAGE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"!\[([^\]]*)\]\([^)]*\)").unwrap());
static LINK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\[([^\]]*)\]\([^)]*\)").unwrap());
static EMPHASIS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[*_]{1,3}([^*_]+)[*_]{1,3}").unwrap());
static HEADING_MARK: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!(r"(?m)^#{{1,6}}[{WS}]*")).unwrap());

fn strip_markdown(text: &str) -> String {
    let text = CODE.replace_all(text, |c: &Captures| c[0].trim_matches('`').to_string());
    let text = IMAGE.replace_all(&text, "${1}");
    let text = LINK.replace_all(&text, "${1}");
    let text = EMPHASIS.replace_all(&text, "${1}");
    let text = HEADING_MARK.replace_all(&text, "");
    pystr::strip(&text).to_string()
}

pub fn parse(path: impl AsRef<Path>, heading_level: usize) -> Result<ParsedBook, IngestError> {
    let path = path.as_ref();
    // Path.read_text() decodes with universal newlines: "\r\n" and "\r" become "\n".
    let raw = std::fs::read_to_string(path)?.replace("\r\n", "\n").replace('\r', "\n");
    let title = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();

    let pattern = Regex::new(&format!(r"(?m)^{}[{WS}]+(.+)$", "#".repeat(heading_level))).expect("valid heading regex");
    let matches: Vec<Captures> = pattern.captures_iter(&raw).collect();
    if matches.is_empty() {
        let chapters = vec![RawChapter { title: title.clone(), text: strip_markdown(&raw) }];
        return Ok(ParsedBook { title, author: None, chapters });
    }

    let chapters = matches
        .iter()
        .enumerate()
        .map(|(i, m)| {
            let start = m.get(0).expect("group 0").end();
            let end = matches.get(i + 1).map_or(raw.len(), |next| next.get(0).expect("group 0").start());
            RawChapter { title: pystr::strip(&m[1]).to_string(), text: strip_markdown(&raw[start..end]) }
        })
        .collect();
    Ok(ParsedBook { title, author: None, chapters })
}

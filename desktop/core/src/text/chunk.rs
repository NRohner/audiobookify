use std::sync::LazyLock;

use regex::Regex;

use super::normalize::{expand_abbreviations, expand_numbers, split_paragraphs, split_sentences, CLOSERS};
use crate::pystr::{self, WORD, WS};

/// Why a chunk ended, which sets the pause after it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EndsWith {
    Word,
    Clause,
    Sentence,
    Paragraph,
    Scene,
}

impl EndsWith {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Word => "word",
            Self::Clause => "clause",
            Self::Sentence => "sentence",
            Self::Paragraph => "paragraph",
            Self::Scene => "scene",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Chunk {
    pub text: String,
    pub ends_with: EndsWith,
}

impl Chunk {
    pub fn new(text: impl Into<String>, ends_with: EndsWith) -> Self {
        Self { text: text.into(), ends_with }
    }
}

// Chunk text never contains a newline (split_paragraphs collapsed it), so `$` here
// behaves like Python's `$`, which also matches before a trailing newline.
static ENDS_WITH_PUNCT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!(r"[.!?…,;:—–-][{CLOSERS}]*$")).unwrap());
// A chunk ending in an interrupted line ("if we were to—”", "or a—", "short--").
static TRAILING_DASH_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!(r"[{WS}]*(?:[—–]|-+)([{CLOSERS}]*)$")).unwrap());
// Clause split points, strongest first; a cut goes right after the match.
static CLAUSE_LEVELS: LazyLock<[Regex; 3]> = LazyLock::new(|| {
    [
        Regex::new(&format!(r"[;:][{WS}]+")).unwrap(),
        Regex::new(&format!(r"[{WS}][—–][{WS}]*|—[{WS}]*")).unwrap(),
        Regex::new(&format!(r",[{WS}]+")).unwrap(),
    ]
});
static WORD_GAP: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!(r"[{WS}]+")).unwrap());
static WORD_CHAR: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!("[{WORD}]")).unwrap());

fn is_scene_break(paragraph: &str) -> bool {
    !WORD_CHAR.is_match(paragraph) // "* * *", "#", "~~~", "⁂"
}

/// Largest cut position after a `pattern` match leaving both sides within bounds.
fn best_cut(text: &str, max_chars: usize, min_chars: usize, pattern: &Regex) -> Option<usize> {
    let mut best = None;
    for m in pattern.find_iter(text) {
        let left = pystr::len(pystr::strip(&text[..m.end()]));
        let right = pystr::len(pystr::strip(&text[m.end()..]));
        if min_chars <= left && left <= max_chars && right >= min_chars {
            best = Some(m.end());
        }
    }
    best
}

/// Split a sentence longer than max_chars at the strongest clause punctuation that
/// fits, falling back to a word boundary. The last piece's ends_with is a placeholder
/// the caller overwrites. Words are never broken, so one giant word stays oversized.
fn split_long_sentence(sentence: &str, max_chars: usize, min_chars: usize) -> Vec<Chunk> {
    let word_gap = std::slice::from_ref(&*WORD_GAP);
    let mut pieces = Vec::new();
    let mut rest = sentence.to_string();
    while pystr::len(&rest) > max_chars {
        let found = [(EndsWith::Clause, &CLAUSE_LEVELS[..]), (EndsWith::Word, word_gap)]
            .into_iter()
            .find_map(|(kind, patterns)| patterns.iter().find_map(|p| best_cut(&rest, max_chars, min_chars, p)).map(|cut| (cut, kind)))
            // No cut keeps both sides >= min_chars; respect max_chars alone.
            .or_else(|| best_cut(&rest, max_chars, 0, &WORD_GAP).map(|cut| (cut, EndsWith::Word)));
        let Some((cut, kind)) = found else { break }; // one giant word
        pieces.push(Chunk::new(pystr::strip(&rest[..cut]), kind));
        rest = pystr::strip(&rest[cut..]).to_string();
    }
    pieces.push(Chunk::new(rest, EndsWith::Sentence));
    pieces
}

fn chunk_paragraph(paragraph: &str, max_chars: usize, min_chars: usize) -> Vec<Chunk> {
    // Abbreviations are expanded before splitting so "Dr. Smith" isn't a sentence
    // break; numbers after, because "7 more" -> "seven more" would hide the boundary.
    let sentences: Vec<String> = split_sentences(&expand_abbreviations(paragraph)).iter().map(|s| expand_numbers(s)).collect();
    let mut packed = Vec::new();
    let mut current = String::new();
    for sentence in sentences {
        let candidate = pystr::strip(&format!("{current} {sentence}")).to_string();
        if pystr::len(&candidate) > max_chars && !current.is_empty() {
            packed.push(std::mem::replace(&mut current, sentence));
        } else {
            current = candidate;
        }
    }
    if !current.is_empty() {
        packed.push(current);
    }

    // A packed chunk only exceeds max_chars when it is a single sentence.
    let mut chunks = Vec::new();
    for text in packed {
        if pystr::len(&text) <= max_chars {
            chunks.push(Chunk::new(text, EndsWith::Sentence));
        } else {
            chunks.extend(split_long_sentence(&text, max_chars, min_chars));
        }
    }
    if let Some(last) = chunks.last_mut() {
        last.ends_with = EndsWith::Paragraph;
    }
    chunks
}

fn join(a: &Chunk, b: &Chunk) -> Chunk {
    // A merged paragraph with no closing punctuation (a heading like "Chapter One")
    // gets a period so the model pauses instead of running into the next sentence.
    let first = if ENDS_WITH_PUNCT_RE.is_match(&a.text) { a.text.clone() } else { format!("{}.", a.text) };
    Chunk::new(format!("{first} {}", b.text), b.ends_with)
}

/// A chunk under min_chars merges into the next one, or into the previous one when it
/// ends a scene or the text. It only crosses a scene break when the whole scene is
/// shorter than min_chars: a lost pause beats a garbled chunk.
///
/// ponytail: merging can push a chunk past max_chars by up to min_chars (2x for a
/// short final paragraph); max_chars is a quality limit, not the model's hard limit.
fn merge_short(chunks: Vec<Chunk>, min_chars: usize) -> Vec<Chunk> {
    let mut out: Vec<Chunk> = Vec::new();
    let mut pending: Option<Chunk> = None;
    for mut chunk in chunks {
        if let Some(p) = pending.take() {
            chunk = join(&p, &chunk);
        }
        if pystr::len(&chunk.text) >= min_chars {
            out.push(chunk);
        } else if chunk.ends_with == EndsWith::Scene && !out.is_empty() {
            let last = out.last_mut().expect("checked non-empty");
            *last = join(last, &chunk);
        } else {
            pending = Some(chunk);
        }
    }
    if let Some(p) = pending {
        match out.last_mut() {
            Some(last) => *last = join(last, &p),
            None => out.push(p),
        }
    }
    out
}

/// Give a chunk that ends in a dash a clean stop ("to—”" -> "to.”"). Turbo sometimes
/// babbled after a chunk-final dash. The pause after the chunk is unchanged.
fn close_interrupted(mut chunk: Chunk) -> Chunk {
    chunk.text = TRAILING_DASH_RE.replace(&chunk.text, ".${1}").into_owned();
    chunk
}

pub fn chunk_text(text: &str, max_chars: usize, min_chars: usize) -> Vec<Chunk> {
    let mut chunks: Vec<Chunk> = Vec::new();
    for paragraph in split_paragraphs(text) {
        if is_scene_break(&paragraph) {
            if let Some(last) = chunks.last_mut() {
                last.ends_with = EndsWith::Scene;
            }
            continue;
        }
        chunks.extend(chunk_paragraph(&paragraph, max_chars, min_chars));
    }
    merge_short(chunks, min_chars).into_iter().map(close_interrupted).collect()
}

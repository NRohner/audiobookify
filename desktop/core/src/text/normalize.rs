use std::sync::LazyLock;

use fancy_regex::{Captures, Regex as FancyRegex, RegexBuilder as FancyRegexBuilder};
use regex::Regex;

use super::num2words;
use crate::pystr::{self, BOUNDARY, WS};

/// Applied in this order with plain substring replacement, exactly like the Python
/// loop of `re.sub(re.escape(abbr), ...)`, so "St." inside "FIRST." is replaced too.
const ABBREVIATIONS: [(&str, &str); 8] = [
    ("Dr.", "Doctor"), ("Mr.", "Mister"), ("Mrs.", "Missus"), ("Ms.", "Miz"),
    ("Prof.", "Professor"), ("St.", "Saint"), ("vs.", "versus"), ("etc.", "et cetera"),
];

/// Closing quotes and brackets that may follow sentence-ending punctuation.
pub(super) const CLOSERS: &str = r#""'”’)\]"#;

const NUMBER: &str = r"\d[\d,]*(?:\.\d+)?";
// A digit run, optionally followed by more hyphen-joined digit runs, so a range
// ("12-34") is one match that is left alone. The lookbehind keeps "COVID-19" out.
// BOUNDARY is Python's \b: "4½" has none after the 4, so it stays as written.
static NUMBER_RE: LazyLock<FancyRegex> =
    LazyLock::new(|| {
        // Unlimited backtracking: the search is linear in the text, but fancy-regex's
        // default cap (~1M backtracks per search) would panic on a long chapter.
        FancyRegexBuilder::new(&format!(r"(?<!-){BOUNDARY}{NUMBER}(?:-{NUMBER})*{BOUNDARY}"))
            .backtrack_limit(usize::MAX)
            .build()
            .expect("number pattern is valid, and unlimited backtracking cannot fail")
    });
// A range whose right operand is glued to a word character ("12-34b", "1980-90s")
// can't close its \b, so NUMBER_RE matches only the left operand; this spots that.
static FOLLOWED_BY_RANGE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\A-\d").unwrap());
// A sentence ends at .!?… plus up to two closers; the next may open with a quote or bracket.
static SENTENCE_SPLIT_RE: LazyLock<FancyRegex> = LazyLock::new(|| {
    // Unlimited backtracking, as for NUMBER_RE.
    FancyRegexBuilder::new(&format!(
        r#"(?:(?<=[.!?…])|(?<=[.!?…][{c}])|(?<=[.!?…][{c}][{c}]))[{WS}]+(?=["'“‘(\[]?[A-Z0-9])"#,
        c = CLOSERS
    ))
    .backtrack_limit(usize::MAX)
    .build()
    .expect("sentence pattern is valid, and unlimited backtracking cannot fail")
});
static PARAGRAPH_SPLIT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!(r"\n[{WS}]*\n")).unwrap());

pub fn expand_abbreviations(text: &str) -> String {
    ABBREVIATIONS.iter().fold(text.to_string(), |text, (abbr, expansion)| text.replace(abbr, expansion))
}

pub fn expand_numbers(text: &str) -> String {
    NUMBER_RE
        .replace_all(text, |caps: &Captures<str>| {
            let m = caps.get(0).expect("group 0 always exists");
            let raw = m.as_str();
            if raw.contains('-') || FOLLOWED_BY_RANGE_RE.is_match(&text[m.end()..]) {
                return raw.to_string(); // a numeric range: leave both operands alone
            }
            num2words::expand(&raw.replace(',', "")).unwrap_or_else(|| raw.to_string())
        })
        .into_owned()
}

pub fn normalize_text(text: &str) -> String {
    expand_numbers(&expand_abbreviations(text))
}

pub fn split_sentences(text: &str) -> Vec<String> {
    let mut pieces = Vec::new();
    let mut last = 0;
    for m in SENTENCE_SPLIT_RE.find_iter(text) {
        let m = m.expect("unlimited backtracking cannot fail");
        pieces.push(&text[last..m.start()]);
        last = m.end();
    }
    pieces.push(&text[last..]);
    pieces.into_iter().map(pystr::strip).filter(|s| !s.is_empty()).map(String::from).collect()
}

/// Blank lines separate paragraphs; whitespace inside a paragraph collapses to one space.
pub fn split_paragraphs(text: &str) -> Vec<String> {
    PARAGRAPH_SPLIT_RE.split(text).map(pystr::collapse).filter(|p| !p.is_empty()).collect()
}

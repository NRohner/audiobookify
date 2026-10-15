//! Python `str` and `re` semantics the ported text code relies on.
//!
//! - Python's `str.split()`, `str.strip()` and regex `\s` treat U+001C..U+001F as
//!   whitespace; Rust's `char::is_whitespace` and regex `\s` do not.
//! - Python's regex `\w` is `str.isalnum()` or "_" (letters and all numbers, so "½"
//!   is a word character); Rust's `\w` leaves out "½" and adds combining marks. `\b`
//!   follows `\w`, so "4½" has no boundary after the 4 in Python but has one in Rust.
//! - `len()` counts code points, not bytes.

/// Python regex `\s`, for use inside a character class: `[{WS}]`.
pub const WS: &str = r"\s\x1C-\x1F";
/// Python regex `\w`, for use inside a character class: `[{WORD}]`.
pub const WORD: &str = r"\p{L}\p{N}_";
/// Python regex `\b` (needs fancy-regex for the lookarounds).
pub const BOUNDARY: &str = r"(?:(?<=[\p{L}\p{N}_])(?![\p{L}\p{N}_])|(?<![\p{L}\p{N}_])(?=[\p{L}\p{N}_]))";

pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `s.strip()`
pub fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// `" ".join(s.split())`
pub fn collapse(s: &str) -> String {
    s.split(is_space).filter(|w| !w.is_empty()).collect::<Vec<_>>().join(" ")
}

/// `len(s)`
pub fn len(s: &str) -> usize {
    s.chars().count()
}

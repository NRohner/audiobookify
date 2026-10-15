#![allow(dead_code)] // each test binary uses a different subset

//! Helpers for the text pipeline tests and their fixtures in tests/fixtures/text.

use std::path::PathBuf;

use serde_json::Value;

/// `desktop/core/tests/fixtures/text`
pub fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/text")
}

pub fn load(path: impl Into<PathBuf>) -> Value {
    let path = path.into();
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

pub fn str_of(v: &Value) -> &str {
    v.as_str().unwrap_or_else(|| panic!("expected a string, got {v}"))
}

pub fn usize_of(v: &Value) -> usize {
    v.as_u64().unwrap_or_else(|| panic!("expected a number, got {v}")) as usize
}

/// assert_eq! for long strings: on a mismatch, show where they first differ instead
/// of printing two whole chapters.
#[track_caller]
pub fn assert_same(label: &str, expected: &str, actual: &str) {
    if expected == actual {
        return;
    }
    let at = expected.chars().zip(actual.chars()).take_while(|(a, b)| a == b).count();
    let window = |s: &str| s.chars().skip(at.saturating_sub(60)).take(120).collect::<String>();
    panic!(
        "{label}: first difference at char {at}\n  expected: {:?}\n  actual:   {:?}\n  (lengths {} vs {})",
        window(expected), window(actual), expected.chars().count(), actual.chars().count()
    );
}

//! Unit-level parity with the Python text functions. Each case in
//! tests/fixtures/text/cases.json is an input and what Python returned for it.

mod fixture;

use std::sync::LazyLock;

use audiobookify_core::pystr;
use fixture::{fixtures, load, str_of};
use serde_json::Value;

static CASES: LazyLock<Value> = LazyLock::new(|| load(fixtures().join("cases.json")));

fn cases(key: &str) -> &'static Vec<Value> {
    CASES[key].as_array().unwrap_or_else(|| panic!("cases.json has no {key}"))
}

fn in_ranges(ranges: &[Value], c: u32) -> bool {
    ranges.iter().any(|r| r[0].as_u64().unwrap() as u32 <= c && c <= r[1].as_u64().unwrap() as u32)
}

#[test]
fn whitespace_is_pythons() {
    let python: Vec<u32> = cases("python_whitespace").iter().map(|c| c.as_u64().unwrap() as u32).collect();
    let rust: Vec<u32> = (0..0x110000).filter(|&c| char::from_u32(c).is_some_and(pystr::is_space)).collect();
    assert_eq!(rust, python);
}

#[test]
fn word_class_is_pythons() {
    let word = regex::Regex::new(&format!("^[{}]$", pystr::WORD)).unwrap();
    let (python, unassigned) = (cases("python_word"), cases("python_unassigned"));
    for c in (0..0x110000).filter_map(char::from_u32) {
        let (py, rs) = (in_ranges(python, c as u32), word.is_match(c.encode_utf8(&mut [0; 4])));
        assert!(py == rs || in_ranges(unassigned, c as u32), "U+{:04X}: Python \\w {py}, Rust {rs}", c as u32);
    }
}

#[test]
fn lexicon() {
    for case in cases("lexicon") {
        let entries: Vec<(&str, &str)> = case[1].as_array().unwrap().iter().map(|e| (str_of(&e[0]), str_of(&e[1]))).collect();
        let actual = audiobookify_core::text::apply_lexicon(str_of(&case[0]), entries);
        assert_eq!(actual, str_of(&case[2]), "{}", case[1]);
    }
}

#[test]
fn numbers() {
    for case in cases("numbers") {
        let actual = audiobookify_core::text::expand_numbers(str_of(&case[0]));
        fixture::assert_same(str_of(&case[0]), str_of(&case[1]), &actual);
    }
}

#[test]
fn normalize() {
    for case in cases("normalize") {
        let actual = audiobookify_core::text::normalize_text(str_of(&case[0]));
        assert_eq!(actual, str_of(&case[1]), "{}", case[0]);
    }
}

#[test]
fn sentences() {
    for case in cases("sentences") {
        let expected: Vec<&str> = case[1].as_array().unwrap().iter().map(str_of).collect();
        assert_eq!(audiobookify_core::text::split_sentences(str_of(&case[0])), expected, "{}", case[0]);
    }
}

#[test]
fn paragraphs() {
    for case in cases("paragraphs") {
        let expected: Vec<&str> = case[1].as_array().unwrap().iter().map(str_of).collect();
        assert_eq!(audiobookify_core::text::split_paragraphs(str_of(&case[0])), expected, "{}", case[0]);
    }
}

#[test]
fn chunks() {
    for (i, case) in cases("chunks").iter().enumerate() {
        let (text, max, min) = (str_of(&case[0]), fixture::usize_of(&case[1]), fixture::usize_of(&case[2]));
        let chunks = audiobookify_core::text::chunk_text(text, max, min);
        let actual: Vec<(&str, &str)> = chunks.iter().map(|c| (c.text.as_str(), c.ends_with.as_str())).collect();
        let expected: Vec<(&str, &str)> = case[3].as_array().unwrap().iter().map(|c| (str_of(&c[0]), str_of(&c[1]))).collect();
        assert_eq!(actual, expected, "case {i}: {max}/{min} {text:?}");
    }
}

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
fn respelling() {
    for case in cases("respelling") {
        let actual = audiobookify_core::text::phonemes_to_respelling(str_of(&case[0]));
        assert_eq!(actual, str_of(&case[1]), "{:?}", case[0]);
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

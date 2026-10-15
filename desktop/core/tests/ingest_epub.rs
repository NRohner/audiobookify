//! Port of tests/test_ingest_epub.py. The Python tests build their EPUBs with
//! ebooklib at test time; here the same books are committed fixtures written by
//! tests/fixtures/text/generate.py (epub/basic.epub, blocks.epub, ...).

mod fixture;

use audiobookify_core::ingest::epub;
use audiobookify_core::text::{chunk_text, Chunk, EndsWith};
use fixture::fixtures;

const FILLER: &str = "The rain kept on falling over the quiet town all through the night. The rain kept on falling over the quiet town all through the night. The rain kept on falling over the quiet town all through the night. The rain kept on falling over the quiet town all through the night.";

fn chapter_text(name: &str) -> String {
    epub::parse(fixtures().join("epub").join(name)).unwrap().chapters.remove(0).text
}

#[test]
fn parse_epub_extracts_title_author_and_skips_short_boilerplate() {
    let parsed = epub::parse(fixtures().join("epub/basic.epub")).unwrap();

    assert_eq!(parsed.title, "My Test Book");
    assert_eq!(parsed.author.as_deref(), Some("Jane Author"));
    let titles: Vec<&str> = parsed.chapters.iter().map(|c| c.title.as_str()).collect();
    assert_eq!(titles, ["Chapter One", "Chapter Two"]);
    assert!(parsed.chapters[0].text.contains("Once upon a time."));
}

#[test]
fn parse_epub_separates_block_elements_with_blank_lines() {
    let text = chapter_text("blocks.epub");
    assert_eq!(text.split("\n\n").collect::<Vec<_>>(), ["Chapter One", FILLER, "Second paragraph."]);
}

#[test]
fn parse_epub_keeps_inline_markup_and_source_wrapping_inside_one_paragraph() {
    assert_eq!(chapter_text("inline.epub").split("\n\n").nth(1), Some("He was very tired, so he slept."));
}

#[test]
fn parse_epub_treats_br_as_a_space() {
    assert_eq!(chapter_text("br.epub").split("\n\n").nth(1), Some("Roses are red violets are blue."));
}

#[test]
fn real_epub_output_chunks_into_paragraph_aware_chunks() {
    let chunks = chunk_text(&chapter_text("scene.epub"), 300, 80);
    assert_eq!(chunks, [
        Chunk::new(format!("Chapter One. {FILLER} “No.”"), EndsWith::Scene),
        Chunk::new("Morning came, and with it a terrible silence that settled over the whole valley below.", EndsWith::Paragraph),
    ]);
}

#[test]
fn spine_rules_follow_ebooklib() {
    let parsed = epub::parse(fixtures().join("epub/handmade.epub")).unwrap();

    assert_eq!(parsed.title, "Handmade Book"); // the first of two dc:title elements
    assert_eq!(parsed.author, None);
    // nav is a chapter; the short page, the text/html page, the dangling idref and the
    // properties="cover" page are not; Calibre's empty <h1/> gives an empty title.
    let titles: Vec<&str> = parsed.chapters.iter().map(|c| c.title.as_str()).collect();
    assert_eq!(titles, ["Chapter 1", "", "PartTwoAgain", "Aside", "Byte\n\nOrder", "ChapterNine", "Bad\u{fffd}bytes"]);
}

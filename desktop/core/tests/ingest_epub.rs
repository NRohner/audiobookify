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

// --- chapters from the table of contents -------------------------------------
// Ports of the TOC tests in tests/test_ingest_epub.py. Like the Python tests, each
// book is written with a zip writer at test time (see `zip_epub`).

use std::io::Write;

use audiobookify_core::models::RawChapter;

#[derive(Default)]
struct ZipBook<'a> {
    /// (file name under OEBPS/, body html), manifest and spine order.
    docs: Vec<(&'a str, String)>,
    ncx: Option<Vec<(&'a str, &'a str)>>,
    nav: Option<&'a str>,
    nav_path: Option<&'a str>,
    /// Raw XML put after the NCX's navMap.
    ncx_tail: &'a str,
}

/// `_zip_epub` in tests/test_ingest_epub.py.
fn zip_epub(path: &std::path::Path, book: &ZipBook) {
    // urllib.parse.quote for the names used here: only " " needs escaping.
    let quote = |s: &str| s.replace(' ', "%20");
    let nav_path = book.nav_path.unwrap_or("nav.xhtml");
    let mut items: Vec<String> = book
        .docs
        .iter()
        .enumerate()
        .map(|(i, (name, _))| format!(r#"<item id="d{i}" href="{}" media-type="application/xhtml+xml"/>"#, quote(name)))
        .collect();
    if book.ncx.is_some() {
        items.push(r#"<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>"#.to_string());
    }
    if book.nav.is_some() {
        items.push(format!(r#"<item id="nav" href="{nav_path}" media-type="application/xhtml+xml" properties="nav"/>"#));
    }
    let toc_attr = if book.ncx.is_some() { r#" toc="ncx""# } else { "" };
    let refs: String = (0..book.docs.len()).map(|i| format!(r#"<itemref idref="d{i}"/>"#)).collect();
    let opf = format!(
        r#"<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">x</dc:identifier><dc:title>Zip Book</dc:title><dc:creator>Zed</dc:creator></metadata><manifest>{}</manifest><spine{toc_attr}>{refs}</spine></package>"#,
        items.concat()
    );
    let container = r#"<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>"#;

    let mut zip = zip::ZipWriter::new(std::fs::File::create(path).unwrap());
    let mut put = |name: &str, data: &str| {
        zip.start_file(name, zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored)).unwrap();
        zip.write_all(data.as_bytes()).unwrap();
    };
    put("mimetype", "application/epub+zip");
    put("META-INF/container.xml", container);
    put("OEBPS/content.opf", &opf);
    for (name, body) in &book.docs {
        put(
            &format!("OEBPS/{name}"),
            &format!(r#"<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body>{body}</body></html>"#),
        );
    }
    if let Some(ncx) = &book.ncx {
        let points: String = ncx
            .iter()
            .enumerate()
            .map(|(i, (label, href))| format!(r#"<navPoint id="n{i}"><navLabel><text>{label}</text></navLabel><content src="{href}"/></navPoint>"#))
            .collect();
        put(
            "OEBPS/toc.ncx",
            &format!(r#"<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>{points}</navMap>{}</ncx>"#, book.ncx_tail),
        );
    }
    if let Some(nav) = book.nav {
        put(
            &format!("OEBPS/{nav_path}"),
            &format!(r#"<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>{nav}</body></html>"#),
        );
    }
    zip.finish().unwrap();
}

fn parse_zip(book: &ZipBook) -> Vec<RawChapter> {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("zip.epub");
    zip_epub(&path, book);
    epub::parse(&path).unwrap().chapters
}

fn docs<'a>(pairs: &[(&'a str, &str)]) -> Vec<(&'a str, String)> {
    pairs.iter().map(|(n, b)| (*n, b.to_string())).collect()
}

fn titles(chapters: &[RawChapter]) -> Vec<&str> {
    chapters.iter().map(|c| c.title.as_str()).collect()
}

fn texts(chapters: &[RawChapter]) -> Vec<&str> {
    chapters.iter().map(|c| c.text.as_str()).collect()
}

const ABC: [(&str, &str); 3] = [("a.xhtml", "<p>A.</p>"), ("b.xhtml", "<p>B.</p>"), ("c.xhtml", "<p>C.</p>")];

#[test]
fn toc_chapters_join_split_files_and_keep_heading_files() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&[
            ("h1.xhtml", "<h1>CHAPTER ONE</h1>"),
            ("a.xhtml", "<p>First part.</p>"),
            ("b.xhtml", "<p>Second part.</p>"),
            ("book1.xhtml", "<h1>BOOK I</h1>"),
            ("h2.xhtml", "<h1>CHAPTER TWO</h1>"),
            ("c.xhtml", "<p>Third part.</p>"),
        ]),
        ncx: Some(vec![("CHAPTER ONE", "h1.xhtml"), ("BOOK I", "book1.xhtml"), ("CHAPTER TWO", "h2.xhtml")]),
        ..Default::default()
    });
    assert_eq!(titles(&chapters), ["CHAPTER ONE", "BOOK I", "CHAPTER TWO"]);
    assert_eq!(texts(&chapters), ["CHAPTER ONE\n\nFirst part.\n\nSecond part.", "BOOK I", "CHAPTER TWO\n\nThird part."]);
}

#[test]
fn pages_before_the_first_toc_entry_are_dropped() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&[("front.xhtml", "<p>Acclaim.</p>"), ("a.xhtml", "<p>One.</p>"), ("b.xhtml", "<p>Two.</p>")]),
        ncx: Some(vec![("A", "a.xhtml"), ("B", "b.xhtml")]),
        ..Default::default()
    });
    assert_eq!(texts(&chapters), ["One.", "Two."]);
}

#[test]
fn back_matter_stays_in_the_last_chapter() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&[("a.xhtml", "<p>One.</p>"), ("b.xhtml", "<p>Two.</p>"), ("notes.xhtml", "<p>Notes.</p>")]),
        ncx: Some(vec![("A", "a.xhtml"), ("B", "b.xhtml")]),
        ..Default::default()
    });
    assert_eq!(chapters[1].text, "Two.\n\nNotes.");
}

#[test]
fn anchor_entries_split_one_file_into_chapters() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&[(
            "all.xhtml",
            r#"<p>Preface.</p><h2 id="c1">One</h2><p>Alpha beta <a id="mid"></a>gamma delta.</p><h2><a name="c2"></a>Two</h2><p>Epsilon.</p>"#,
        )]),
        ncx: Some(vec![("One", "all.xhtml#c1"), ("Mid", "all.xhtml#mid"), ("Two", "all.xhtml#c2")]),
        ..Default::default()
    });
    assert_eq!(titles(&chapters), ["One", "Mid", "Two"]);
    assert_eq!(texts(&chapters), ["One\n\nAlpha beta", "gamma delta.", "Two\n\nEpsilon."]);
}

#[test]
fn nav_document_wins_over_ncx_and_nesting_is_flattened() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&ABC),
        ncx: Some(vec![("X", "a.xhtml"), ("Y", "c.xhtml")]),
        nav: Some(
            r#"<nav epub:type="toc"><ol><li><a href="a.xhtml">Part  One</a><ol><li><a href="b.xhtml">Nested</a></li></ol></li><li><a href="c.xhtml">Last</a></li></ol></nav>"#,
        ),
        ..Default::default()
    });
    assert_eq!(titles(&chapters), ["Part One", "Nested", "Last"]);
}

#[test]
fn page_list_and_landmarks_are_ignored() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&ABC),
        nav: Some(
            r#"<nav epub:type="landmarks"><ol><li><a href="c.xhtml">Start</a></li></ol></nav><nav epub:type="toc"><ol><li><a href="a.xhtml">A</a></li><li><a href="b.xhtml">B</a></li></ol></nav><nav epub:type="page-list"><ol><li><a href="c.xhtml">3</a></li></ol></nav>"#,
        ),
        ..Default::default()
    });
    assert_eq!(titles(&chapters), ["A", "B"]);
}

#[test]
fn ncx_page_list_is_ignored() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&ABC),
        ncx: Some(vec![("A", "a.xhtml"), ("B", "b.xhtml")]),
        ncx_tail: r#"<pageList><pageTarget><navLabel><text>3</text></navLabel><content src="c.xhtml"/></pageTarget></pageList>"#,
        ..Default::default()
    });
    assert_eq!(titles(&chapters), ["A", "B"]);
}

#[test]
fn unknown_fragment_starts_at_top_of_file() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&ABC[..2]),
        ncx: Some(vec![("A", "a.xhtml#nope"), ("B", "b.xhtml")]),
        ..Default::default()
    });
    assert_eq!(texts(&chapters), ["A.", "B."]);
}

#[test]
fn out_of_order_and_duplicate_entries_are_skipped() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&ABC),
        ncx: Some(vec![
            ("A", "a.xhtml"),
            ("C", "c.xhtml"),
            ("B late", "b.xhtml"),         // before the previous kept entry
            ("C again", "c.xhtml"),        // same position as the previous kept entry
            ("Outside", "elsewhere.xhtml"), // not in the spine
        ]),
        ..Default::default()
    });
    assert_eq!(titles(&chapters), ["A", "C"]);
    assert_eq!(chapters[0].text, "A.\n\nB.");
}

#[test]
fn entry_with_no_text_is_dropped() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&[("cover.xhtml", r#"<img src="c.jpg"/>"#), ABC[0], ABC[1]]),
        ncx: Some(vec![("Cover", "cover.xhtml"), ("A", "a.xhtml"), ("B", "b.xhtml")]),
        ..Default::default()
    });
    assert_eq!(titles(&chapters), ["A", "B"]);
}

#[test]
fn empty_label_falls_back_to_heading_then_chapter_number() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&[("a.xhtml", "<h1>Heading</h1><p>A.</p>"), ABC[1]]),
        ncx: Some(vec![("  ", "a.xhtml"), ("", "b.xhtml")]),
        ..Default::default()
    });
    assert_eq!(titles(&chapters), ["Heading", "Chapter 2"]);
}

#[test]
fn toc_hrefs_resolve_against_the_toc_files_directory() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&[("my a.xhtml", "<p>A.</p>"), ABC[1]]),
        nav: Some(r#"<nav epub:type="toc"><ol><li><a href="my%20a.xhtml">A</a></li><li><a href="./sub/../b.xhtml#">B</a></li></ol></nav>"#),
        ..Default::default()
    });
    assert_eq!(titles(&chapters), ["A", "B"]);
}

#[test]
fn fragment_is_percent_decoded() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&[("a.xhtml", "<p>Top.</p><h2 id=\"\u{e9}t\u{e9}\">Summer</h2><p>Sun.</p>")]),
        ncx: Some(vec![("Top", "a.xhtml"), ("Summer", "a.xhtml#%C3%A9t%C3%A9")]),
        ..Default::default()
    });
    assert_eq!(texts(&chapters), ["Top.", "Summer\n\nSun."]);
}

#[test]
fn anchor_in_a_later_file_leaves_its_top_in_the_previous_chapter() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&[("a.xhtml", "<p>A.</p>"), ("b.xhtml", r#"<p>B top.</p><h2 id="x">Two</h2><p>B rest.</p>"#), ("c.xhtml", "<p>C.</p>")]),
        ncx: Some(vec![("A", "a.xhtml"), ("Two", "b.xhtml#x")]),
        ..Default::default()
    });
    assert_eq!(texts(&chapters), ["A.\n\nB top.", "Two\n\nB rest.\n\nC."]);
}

#[test]
fn toc_in_another_directory_resolves_relative_to_itself() {
    let chapters = parse_zip(&ZipBook {
        docs: docs(&[("text/a.xhtml", "<p>A.</p>"), ("text/b.xhtml", "<p>B.</p>")]),
        nav: Some(r#"<nav epub:type="toc"><ol><li><a href="../text/a.xhtml">A</a></li><li><a href="../text/b.xhtml">B</a></li></ol></nav>"#),
        nav_path: Some("nav/nav.xhtml"),
        ..Default::default()
    });
    assert_eq!(titles(&chapters), ["A", "B"]);
}

fn long_page() -> String {
    format!("<p>{}</p>", "Once upon a time. ".repeat(20))
}

#[test]
fn nav_with_one_entry_falls_back_to_per_file_chapters() {
    let long = long_page();
    let chapters = parse_zip(&ZipBook {
        docs: docs(&[("a.xhtml", &long), ("b.xhtml", &long)]),
        // A one-entry nav does not fall through to the NCX.
        ncx: Some(vec![("A", "a.xhtml"), ("B", "b.xhtml")]),
        nav: Some(r#"<nav epub:type="toc"><ol><li><a href="a.xhtml">A</a></li></ol></nav>"#),
        ..Default::default()
    });
    assert_eq!(titles(&chapters), ["Chapter 1", "Chapter 2"]);
}

#[test]
fn books_without_a_usable_toc_keep_per_file_chapters() {
    let long = long_page();
    let pages = docs(&[("a.xhtml", &long), ("short.xhtml", "<p>tiny</p>"), ("b.xhtml", &long)]);
    for ncx in [None, Some(vec![]), Some(vec![("A", "a.xhtml")])] {
        let chapters = parse_zip(&ZipBook { docs: pages.clone(), ncx: ncx.clone(), ..Default::default() });
        assert_eq!(titles(&chapters), ["Chapter 1", "Chapter 2"], "ncx {ncx:?}");
    }
}

#[test]
fn calibre_split_fixture_follows_the_ncx() {
    let parsed = epub::parse(fixtures().join("epub/calibre_split.epub")).unwrap();
    // Front matter dropped, heading-only files kept, the label's whitespace collapsed,
    // the About page left in the last chapter.
    assert_eq!(titles(&parsed.chapters), ["BOOK I", "CHAPTER ONE", "CHAPTER TWO", "BOOK II", "CHAPTER THREE"]);
    assert_eq!(parsed.chapters[0].text, "BOOK I");
    assert!(parsed.chapters[1].text.starts_with("CHAPTER ONE\n\nFirst 0. "));
    assert!(parsed.chapters[4].text.contains("\n\nAbout the Author\n\nBack 0. "));
}

#[test]
fn anchors_fixture_follows_the_nav() {
    let parsed = epub::parse(fixtures().join("epub/anchors.epub")).unwrap();
    let titles = titles(&parsed.chapters);
    assert_eq!(titles, ["Part One", "Chapter 1", "Chapter 2", "Chapter 3", "Part Two", "Chapter 4", "Chapter 5", "Epilogue"]);
    // Part Two's fragment is unknown, so it starts at the top of b.xhtml.
    assert!(parsed.chapters[4].text.starts_with("Part Two\n\nMid 0. "));
    assert!(parsed.chapters[3].text.starts_with("Chapter 3\n\nThree 0. "));
}

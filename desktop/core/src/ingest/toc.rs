//! Chapters from the book's table of contents: `_toc_links` and `_toc_chapters` in
//! `ingest/epub.py`, in the same order of operations.
//!
//! - The TOC is the first EPUB 3 nav document (an XHTML manifest item with property
//!   "nav") that has a `<nav epub:type="toc">`, read from its raw zip bytes; else the
//!   first manifest item with the NCX media type (ebooklib doesn't expose
//!   `<spine toc>`). A nav with a toc is final even when its entries are unusable.
//! - An entry's href is resolved against the TOC file's directory and must name a
//!   spine document; its fragment is percent-decoded and found by `find_anchor`, and an
//!   unknown one means the top of the document. Entries at or before the previous kept
//!   one, compared as (spine index, anchor in document order), are skipped.
//! - Fewer than 2 entries: no chapters, and the caller keeps per-file chapters.

use std::collections::HashMap;
use std::fs::File;

use roxmltree::{Document as Xml, ParsingOptions};
use zip::ZipArchive;

use super::epub::{read, unquote, Item, Spine};
use super::IngestError;
use crate::models::RawChapter;
use crate::pystr;

const NCX_MEDIA_TYPE: &str = "application/x-dtbncx+xml";

/// (label, href) pairs.
type Links = Vec<(String, String)>;

fn decode(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(bytes)).into_owned()
}

/// `_toc_links`: the TOC's file name (ebooklib's, relative to the OPF) and its entries
/// in document order with nesting flattened. pageList and the other navs are never read.
pub(super) fn links(zip: &mut ZipArchive<File>, items: &[Item]) -> Result<Option<(String, Links)>, IngestError> {
    for item in items.iter().filter(|i| i.is_nav) {
        let page = super::html::Document::parse(&decode(&read(zip, &item.path)?));
        if let Some(links) = page.toc_links() {
            return Ok(Some((item.file_name.clone(), links)));
        }
    }
    let Some(item) = items.iter().find(|i| i.media_type == NCX_MEDIA_TYPE) else { return Ok(None) };
    let xml = decode(&read(zip, &item.path)?);
    Ok(Some((item.file_name.clone(), ncx_links(&xml))))
}

/// The NCX's `navMap` `navPoint`s: label = all text under the direct `navLabel` child,
/// href = the direct `content` child's non-empty src.
///
/// ponytail: Python reads the NCX with lxml in recover mode; an NCX that isn't
/// well-formed XML gives no entries here (so per-file chapters) instead.
fn ncx_links(xml: &str) -> Links {
    let options = ParsingOptions { allow_dtd: true, ..ParsingOptions::default() };
    let Ok(doc) = Xml::parse_with_options(xml, options) else { return Vec::new() };
    let named = |n: &roxmltree::Node, name: &str| n.is_element() && n.tag_name().name() == name;
    let Some(nav_map) = doc.descendants().find(|n| named(n, "navMap")) else { return Vec::new() };
    nav_map
        .descendants()
        .filter(|n| named(n, "navPoint"))
        .filter_map(|point| {
            let src = point.children().find(|c| named(c, "content"))?.attribute("src").filter(|s| !s.is_empty())?;
            let label = point
                .children()
                .find(|c| named(c, "navLabel"))
                .map(|l| l.descendants().filter(|d| d.is_text()).filter_map(|d| d.text()).collect())
                .unwrap_or_default();
            Some((label, src.to_string()))
        })
        .collect()
}

/// `posixpath.dirname`
fn dirname(path: &str) -> &str {
    let head = &path[..path.rfind('/').map_or(0, |i| i + 1)];
    let trimmed = head.trim_end_matches('/');
    if trimmed.is_empty() { head } else { trimmed }
}

/// `posixpath.join` for two parts.
fn join(dir: &str, path: &str) -> String {
    if path.starts_with('/') || dir.is_empty() {
        path.to_string()
    } else if dir.ends_with('/') {
        format!("{dir}{path}")
    } else {
        format!("{dir}/{path}")
    }
}

/// `posixpath.normpath`, leading slashes included, so an absolute href stays absolute
/// and matches nothing, as in Python.
fn normpath(path: &str) -> String {
    if path.is_empty() {
        return ".".to_string();
    }
    let slashes = if path.starts_with("//") && !path.starts_with("///") { 2 } else { usize::from(path.starts_with('/')) };
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." if (slashes == 0 && parts.is_empty()) || parts.last() == Some(&"..") => parts.push(part),
            ".." => {
                parts.pop();
            }
            _ => parts.push(part),
        }
    }
    let out = format!("{}{}", "/".repeat(slashes), parts.join("/"));
    if out.is_empty() { ".".to_string() } else { out }
}

struct Entry<'a> {
    label: &'a str,
    doc: usize,
    /// The anchor's node id; None for the top of the document.
    anchor: Option<usize>,
}

/// `_toc_chapters`: chapters cut from the spine documents by the TOC; empty when fewer
/// than 2 entries are usable.
pub(super) fn chapters(toc_file: &str, links: &[(String, String)], spine: &Spine) -> Vec<RawChapter> {
    let index: HashMap<&str, usize> = spine.names.iter().enumerate().map(|(i, n)| (n.as_str(), i)).collect(); // last wins
    let dir = dirname(toc_file);

    let mut entries: Vec<Entry> = Vec::new();
    let mut last: Option<(usize, Option<usize>)> = None;
    for (label, href) in links {
        let (path, fragment) = href.split_once('#').unwrap_or((href, ""));
        let Some(&doc) = index.get(normpath(&join(dir, &unquote(path))).as_str()) else { continue };
        let fragment = unquote(fragment);
        let anchor = if fragment.is_empty() { None } else { spine.doc(doc).find_anchor(&fragment) };
        // Reading order; None (the top of a document) sorts before every element in it.
        let position = (doc, anchor);
        if last.is_some_and(|l| position <= l) {
            continue;
        }
        entries.push(Entry { label, doc, anchor });
        last = Some(position);
    }
    if entries.len() < 2 {
        return Vec::new();
    }

    let mut texts: Vec<Vec<String>> = entries.iter().map(|_| Vec::new()).collect();
    let mut current: Option<usize> = None; // entry owning the text at this point in the spine
    for i in 0..spine.len() {
        let here: Vec<usize> = (0..entries.len()).filter(|&k| entries[k].doc == i).collect();
        if here.is_empty() && current.is_none() {
            continue; // front matter
        }
        let cuts: Vec<usize> = here.iter().copied().filter(|&k| entries[k].anchor.is_some()).collect();
        let anchors: Vec<usize> = cuts.iter().filter_map(|&k| entries[k].anchor).collect();
        let pieces = spine.doc(i).block_text_split(&anchors);
        let top = match here.first() {
            Some(&k) if entries[k].anchor.is_none() => Some(k),
            _ => current,
        };
        let owners: Vec<Option<usize>> = std::iter::once(top).chain(cuts.into_iter().map(Some)).collect();
        // zip(): a U+E000 already in the text makes extra pieces, which Python drops too.
        for (owner, piece) in owners.iter().zip(pieces) {
            if let Some(k) = owner {
                texts[*k].push(piece);
            }
        }
        current = *owners.last().expect("owners starts with the top piece");
    }

    let mut chapters = Vec::new();
    for (entry, pieces) in entries.iter().zip(texts) {
        let text = pieces.into_iter().filter(|p| !p.is_empty()).collect::<Vec<_>>().join("\n\n");
        if text.is_empty() {
            continue; // e.g. a cover image
        }
        let mut title = pystr::collapse(entry.label);
        if title.is_empty() {
            title = spine.doc(entry.doc).first_heading().map(|h| h.replace('\u{e000}', "")).unwrap_or_default();
        }
        if title.is_empty() {
            title = format!("Chapter {}", chapters.len() + 1);
        }
        chapters.push(RawChapter { title, text });
    }
    chapters
}

#[cfg(test)]
mod tests {
    use super::{dirname, join, normpath};

    #[test]
    fn paths_follow_posixpath() {
        // (input, posixpath.normpath)
        for (path, want) in [
            ("", "."),
            ("a/./b/../c", "a/c"),
            ("../a", "../a"),
            ("a/../../b", "../b"),
            ("/../a", "/a"),
            ("//a//b/", "//a/b"),
            ("///a", "/a"),
            ("a/..", "."),
            ("nav/", "nav"),
        ] {
            assert_eq!(normpath(path), want, "normpath({path:?})");
        }
        assert_eq!([dirname("toc.ncx"), dirname("nav/toc.xhtml"), dirname("/x"), dirname("a//b")], ["", "nav", "/", "a"]);
        assert_eq!([join("", "a"), join("nav", "../a"), join("nav/", "a"), join("nav", "/a"), join("nav", "")], ["a", "nav/../a", "nav/a", "/a", "nav/"]);
    }
}

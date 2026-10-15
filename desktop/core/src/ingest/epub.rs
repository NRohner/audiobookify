//! EPUB -> chapters, matching `ingest/epub.py` on top of ebooklib 0.20.
//!
//! ebooklib's rules that decide which spine entries become chapters:
//! - The OPF is the last `rootfile` in META-INF/container.xml with media type
//!   application/oebps-package+xml; hrefs are percent-decoded and resolved against
//!   its directory.
//! - A spine `itemref` names the first manifest item with that id. It is a document
//!   only when its media type is exactly application/xhtml+xml and its properties
//!   don't include "cover" (ebooklib renders its own cover page for those). The nav
//!   document counts as a document.
//! - The title is the first `dc:title` directly under `<metadata>`, else the file
//!   stem; the author is the first `dc:creator`.

use std::fs::File;
use std::io::Read;
use std::path::Path;

use roxmltree::{Document as Xml, Node};
use zip::ZipArchive;

use super::html::Document;
use super::{IngestError, ParsedBook};
use crate::models::RawChapter;
use crate::pystr;

const MIN_CHAPTER_CHARS: usize = 200;
const CONTAINER_NS: &str = "urn:oasis:names:tc:opendocument:xmlns:container";
const OPF_NS: &str = "http://www.idpf.org/2007/opf";
const DC_NS: &str = "http://purl.org/dc/elements/1.1/";

fn bad(message: impl Into<String>) -> IngestError {
    IngestError::Epub(message.into())
}

fn read(zip: &mut ZipArchive<File>, name: &str) -> Result<Vec<u8>, IngestError> {
    let mut file = zip.by_name(name).map_err(|e| bad(format!("{name}: {e}")))?;
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    Ok(bytes)
}

fn xml_text(bytes: &[u8], name: &str) -> Result<String, IngestError> {
    let text = String::from_utf8(bytes.to_vec()).map_err(|_| bad(format!("{name} is not UTF-8")))?;
    Ok(text.strip_prefix('\u{feff}').map(str::to_string).unwrap_or(text))
}

fn child<'a, 'i>(node: Node<'a, 'i>, ns: &str, name: &str) -> Option<Node<'a, 'i>> {
    node.children().find(|c| c.tag_name().namespace() == Some(ns) && c.tag_name().name() == name)
}

/// `urllib.parse.unquote`
fn unquote(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let hex = |b: u8| (b as char).to_digit(16);
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let (Some(h), Some(l)) = (hex(bytes[i + 1]), hex(bytes[i + 2])) {
                out.push((h * 16 + l) as u8);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `posixpath.normpath` for the relative paths inside a zip.
fn normpath(path: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." if parts.last().is_some_and(|p| *p != "..") => {
                parts.pop();
            }
            _ => parts.push(part),
        }
    }
    parts.join("/")
}

struct Item {
    id: Option<String>,
    path: String,
    is_document: bool,
}

pub fn parse(path: impl AsRef<Path>) -> Result<ParsedBook, IngestError> {
    let path = path.as_ref();
    let mut zip = ZipArchive::new(File::open(path)?).map_err(|e| bad(e.to_string()))?;

    let container = xml_text(&read(&mut zip, "META-INF/container.xml")?, "META-INF/container.xml")?;
    let container = Xml::parse(&container).map_err(|e| bad(format!("container.xml: {e}")))?;
    let opf_path = container
        .descendants()
        .filter(|n| n.tag_name().namespace() == Some(CONTAINER_NS) && n.tag_name().name() == "rootfile")
        .filter(|n| n.attribute("media-type") == Some("application/oebps-package+xml"))
        .filter_map(|n| n.attribute("full-path"))
        .next_back() // ebooklib keeps the last one
        .ok_or_else(|| bad("container.xml names no OPF file"))?
        .to_string();
    let opf_dir = opf_path.rsplit_once('/').map_or("", |(dir, _)| dir).to_string();

    let opf = xml_text(&read(&mut zip, &normpath(&opf_path))?, &opf_path)?;
    let opf = Xml::parse(&opf).map_err(|e| bad(format!("{opf_path}: {e}")))?;
    let package = opf.root_element();

    let metadata = child(package, OPF_NS, "metadata");
    let first_dc = |name: &str| -> Option<String> {
        metadata?.children().find(|c| c.tag_name().namespace() == Some(DC_NS) && c.tag_name().name() == name).map(|c| c.text().unwrap_or("").to_string())
    };
    let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    // ponytail: an empty <dc:title/> gives Python a None title (and a failed import); fall back to the stem.
    let title = first_dc("title").filter(|t| !t.is_empty()).unwrap_or(stem);
    let author = first_dc("creator").filter(|a| !a.is_empty());

    let items: Vec<Item> = child(package, OPF_NS, "manifest")
        .map(|manifest| {
            manifest
                .children()
                .filter(|n| n.tag_name().namespace() == Some(OPF_NS) && n.tag_name().name() == "item")
                .map(|n| {
                    let properties: Vec<&str> = n.attribute("properties").unwrap_or("").split(' ').collect();
                    let href = unquote(n.attribute("href").unwrap_or(""));
                    Item {
                        id: n.attribute("id").map(str::to_string),
                        path: normpath(&format!("{opf_dir}/{href}")),
                        is_document: n.attribute("media-type") == Some("application/xhtml+xml") && !properties.contains(&"cover"),
                    }
                })
                .collect()
        })
        .unwrap_or_default();

    let spine: Vec<String> = child(package, OPF_NS, "spine")
        .map(|spine| spine.children().filter(|n| n.is_element()).filter_map(|n| n.attribute("idref")).map(str::to_string).collect())
        .unwrap_or_default();

    let mut chapters = Vec::new();
    for idref in &spine {
        let Some(item) = items.iter().find(|i| i.id.as_deref() == Some(idref.as_str())) else { continue };
        if !item.is_document {
            continue;
        }
        let bytes = read(&mut zip, &item.path)?;
        // lxml is told the page is UTF-8 whatever it declares; bad bytes become U+FFFD.
        let html = String::from_utf8_lossy(bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes)).into_owned();
        let doc = Document::parse(&html);
        let text = doc.block_text();
        if pystr::len(&text) < MIN_CHAPTER_CHARS {
            continue; // skips cover pages, copyright blurbs, blank dividers
        }
        let title = doc.first_heading().unwrap_or_else(|| format!("Chapter {}", chapters.len() + 1));
        chapters.push(RawChapter { title, text });
    }
    Ok(ParsedBook { title, author, chapters })
}

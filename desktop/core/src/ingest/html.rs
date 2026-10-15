//! One EPUB spine document -> (paragraph text, first h1/h2 text), matching what
//! `ingest/epub.py` gets today. That is not BeautifulSoup over the file: ebooklib's
//! `EpubHtml.get_content()` first parses the page with libxml2 (lxml), keeps only the
//! children of `<body>` and re-serializes with `pretty_print=True`, and BeautifulSoup
//! (html.parser) reads that. This module reproduces the effects that change the text:
//!
//! - Tokens come from html5ever's tokenizer, which follows the HTML5 tokenizing rules
//!   libxml2 2.14 uses (entities, CR/LF, `<![CDATA[` as a comment, NUL as U+FFFD).
//! - The tree follows html.parser's rules, not HTML5 tree construction: `<tag/>` closes
//!   any tag, nothing closes implicitly, and an end tag pops to the most recent open
//!   tag of that name. Calibre writes empty headings as `<h1 id=".."/>`; an HTML5 tree
//!   builder would leave that h1 open around the whole page.
//! - Only `<body>`'s subtree counts, and text before its first child is dropped.
//! - Pretty-printing: an element whose children include no text (and whose ancestors
//!   are the same) gets whitespace around each child, so `<p><span>a</span><span>b</span></p>`
//!   reads "a b".
//!
//! For chapters from the table of contents it also finds a TOC anchor (`find_anchor`),
//! cuts the text there (`block_text_split`), and reads the links of a nav document
//! (`toc_links`), which Python parses from the raw file with html.parser, not via lxml.

use std::cell::RefCell;

use html5ever::tendril::StrTendril;
use html5ever::tokenizer::states::RawKind;
use html5ever::tokenizer::{BufferQueue, TagKind, Token, TokenSink, TokenSinkResult, Tokenizer};

use crate::pystr;

const BLOCK_TAGS: [&str; 13] = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr", "td", "th"];
const SKIPPED_TAGS: [&str; 4] = ["head", "nav", "script", "style"]; // decomposed before reading
/// html.parser closes these at once (BeautifulSoup's HTMLTreeBuilder empty-element tags).
const VOID_TAGS: [&str; 24] = [
    "area", "base", "basefont", "bgsound", "br", "col", "command", "embed", "frame", "hr", "image", "img",
    "input", "isindex", "keygen", "link", "menuitem", "meta", "nextid", "param", "source", "spacer", "track", "wbr",
];
/// BeautifulSoup gives strings under these their own class, which get_text() skips.
const STRING_CONTAINERS: [&str; 3] = ["template", "rt", "rp"];
/// The attributes anything here reads; the rest are dropped at parse time.
const KEPT_ATTRS: [&str; 4] = ["id", "name", "href", "epub:type"];
/// Private use area: candidates for the marker where a TOC anchor cuts a document
/// (`SENTINEL_RANGE` in `ingest/epub.py`).
const SENTINEL_RANGE: std::ops::RangeInclusive<u32> = 0xE000..=0xF8FF;
/// Passed where there are no cuts, so the marker is never used.
const SENTINEL_FILLER: char = '\u{e000}';

enum Tok {
    Start(String, bool, Vec<(String, String)>),
    End(String),
    Text(String),
    Comment,
}

struct Sink(RefCell<Vec<Tok>>);

impl TokenSink for Sink {
    type Handle = ();

    fn process_token(&self, token: Token, _line: u64) -> TokenSinkResult<()> {
        let mut toks = self.0.borrow_mut();
        match token {
            Token::TagToken(tag) if tag.kind == TagKind::EndTag => toks.push(Tok::End(tag.name.to_string())),
            Token::TagToken(tag) => {
                let name = tag.name.to_string();
                // html.parser's raw-text elements (Python 3.12), unless written as <tag/>.
                let raw = match name.as_str() {
                    "script" | "style" | "xmp" | "iframe" | "noembed" | "noframes" => Some(RawKind::Rawtext),
                    "textarea" | "title" => Some(RawKind::Rcdata),
                    _ => None,
                };
                let self_closing = tag.self_closing;
                // The tokenizer drops a repeated attribute, keeping the first, as libxml2 does.
                let attrs = tag
                    .attrs
                    .iter()
                    .filter(|a| KEPT_ATTRS.contains(&&*a.name.local))
                    .map(|a| (a.name.local.to_string(), a.value.to_string()))
                    .collect();
                toks.push(Tok::Start(name, self_closing, attrs));
                if let (Some(kind), false) = (raw, self_closing) {
                    return TokenSinkResult::RawData(kind);
                }
            }
            Token::CharacterTokens(text) => toks.push(Tok::Text(text.to_string())),
            Token::NullCharacterToken => toks.push(Tok::Text("\u{fffd}".to_string())),
            Token::CommentToken(_) | Token::DoctypeToken(_) => toks.push(Tok::Comment),
            Token::EOFToken | Token::ParseError(_) => {}
        }
        TokenSinkResult::Continue
    }
}

fn tokenize(html: &str) -> Vec<Tok> {
    let input = BufferQueue::default();
    input.push_back(StrTendril::from(html));
    let tokenizer = Tokenizer::new(Sink(RefCell::new(Vec::new())), Default::default());
    let _ = tokenizer.feed(&input);
    tokenizer.end();
    tokenizer.sink.0.take()
}

enum Kind {
    Element(String),
    /// `in_heading` is false for strings under template/rt/rp (BeautifulSoup's
    /// TemplateString/RubyTextString), which _block_text keeps but get_text() skips.
    Text { text: String, in_heading: bool },
    Comment,
}

struct Node {
    kind: Kind,
    children: Vec<usize>,
    /// `KEPT_ATTRS` only; empty for text and comments.
    attrs: Vec<(String, String)>,
}

/// A parsed spine document. `nodes[0]` is the root.
pub struct Document {
    nodes: Vec<Node>,
}

impl Document {
    pub fn parse(html: &str) -> Self {
        let mut doc = Document { nodes: vec![Node { kind: Kind::Element(String::new()), children: Vec::new(), attrs: Vec::new() }] };
        let mut stack = vec![0usize];
        let mut names: Vec<String> = vec![String::new()]; // tag names along `stack`
        let mut already_closed: Vec<String> = Vec::new(); // void tags whose end tag we ignore once
        let mut text = String::new();
        let is_container = |n: &String| STRING_CONTAINERS.contains(&n.as_str());
        let mut open_containers = 0usize; // how many of `names` are STRING_CONTAINERS

        let flush = |doc: &mut Document, stack: &[usize], open_containers: usize, text: &mut String| {
            if text.is_empty() {
                return;
            }
            let in_heading = open_containers == 0;
            let parent = *stack.last().expect("root never pops");
            doc.add(parent, Kind::Text { text: std::mem::take(text), in_heading });
        };

        for tok in tokenize(html) {
            if let Tok::Text(t) = &tok {
                text.push_str(t);
                continue;
            }
            flush(&mut doc, &stack, open_containers, &mut text);
            match tok {
                Tok::Start(name, self_closing, attrs) => {
                    let id = doc.add(*stack.last().expect("root never pops"), Kind::Element(name.clone()));
                    doc.nodes[id].attrs = attrs;
                    if self_closing {
                        continue; // opened and closed at once
                    }
                    if VOID_TAGS.contains(&name.as_str()) {
                        already_closed.push(name);
                        continue;
                    }
                    stack.push(id);
                    open_containers += usize::from(is_container(&name));
                    names.push(name);
                }
                Tok::End(name) => {
                    if let Some(i) = already_closed.iter().position(|n| *n == name) {
                        already_closed.remove(i);
                    } else if let Some(depth) = names.iter().rposition(|n| *n == name).filter(|d| *d > 0) {
                        stack.truncate(depth);
                        open_containers -= names[depth..].iter().filter(|n| is_container(n)).count();
                        names.truncate(depth);
                    }
                }
                Tok::Comment => {
                    doc.add(*stack.last().expect("root never pops"), Kind::Comment);
                }
                Tok::Text(_) => unreachable!("handled above"),
            }
        }
        flush(&mut doc, &stack, open_containers, &mut text);
        doc
    }

    fn add(&mut self, parent: usize, kind: Kind) -> usize {
        self.nodes.push(Node { kind, children: Vec::new(), attrs: Vec::new() });
        let id = self.nodes.len() - 1;
        self.nodes[parent].children.push(id);
        id
    }

    fn name(&self, id: usize) -> Option<&str> {
        match &self.nodes[id].kind {
            Kind::Element(name) => Some(name),
            _ => None,
        }
    }

    fn attr(&self, id: usize, name: &str) -> Option<&str> {
        self.nodes[id].attrs.iter().find(|(n, _)| n == name).map(|(_, v)| v.as_str())
    }

    fn is_text(&self, id: usize) -> bool {
        matches!(self.nodes[id].kind, Kind::Text { .. })
    }

    /// The body ebooklib keeps: the first `<body>` (or the whole document when there is
    /// none), minus the text before its first child element or comment.
    fn body(&self) -> (usize, Vec<usize>) {
        let body = (0..self.nodes.len()).find(|&id| self.name(id) == Some("body")).unwrap_or(0);
        let children = self.nodes[body].children.iter().copied().skip_while(|&c| self.is_text(c)).collect();
        (body, children)
    }

    /// Strings in document order as (text, nearest block ancestor, counts for headings),
    /// with head/nav/script/style removed, `<br>` read as " ", and pretty-print whitespace.
    /// A `sentinel` string goes just before each element in `cuts` (sorted node ids), as
    /// `anchor.insert_before(NavigableString(sentinel))` puts it after the padding.
    fn strings<'a>(
        &'a self,
        children: &'a [usize],
        formatted: bool,
        block: Option<usize>,
        cuts: &[usize],
        sentinel: char,
        out: &mut Vec<(String, Option<usize>, bool)>,
    ) {
        // Explicit stack instead of recursion: nothing closes implicitly, so unclosed tags
        // nest as deep as the input is long. A frame is a child list being walked.
        struct Frame<'a> {
            children: &'a [usize],
            next: usize,
            formatted: bool,
            block: Option<usize>,
        }
        let frame = |children: &'a [usize], formatted: bool, block| Frame {
            children,
            next: 0,
            formatted: formatted && !children.iter().any(|&c| self.is_text(c)),
            block,
        };
        let mut stack = vec![frame(children, formatted, block)];
        while let Some(top) = stack.last_mut() {
            let pad = |out: &mut Vec<(String, Option<usize>, bool)>, f: bool, b| {
                if f {
                    out.push((" ".to_string(), b, true));
                }
            };
            let Some(&child) = top.children.get(top.next) else {
                if !top.children.is_empty() {
                    pad(out, top.formatted, top.block);
                }
                stack.pop();
                continue;
            };
            top.next += 1;
            let (formatted, block) = (top.formatted, top.block);
            pad(out, formatted, block);
            if cuts.binary_search(&child).is_ok() {
                out.push((sentinel.to_string(), block, true));
            }
            match &self.nodes[child].kind {
                Kind::Text { text, in_heading } => out.push((text.clone(), block, *in_heading)),
                Kind::Comment => {}
                Kind::Element(name) if SKIPPED_TAGS.contains(&name.as_str()) => {}
                Kind::Element(name) if name == "br" => out.push((" ".to_string(), block, true)),
                Kind::Element(name) => {
                    let inner = if BLOCK_TAGS.contains(&name.as_str()) { Some(child) } else { block };
                    stack.push(frame(&self.nodes[child].children, formatted, inner));
                }
            }
        }
    }

    /// `_block_text`: text under one block element joined as-is, paragraphs joined by
    /// blank lines, whitespace inside a paragraph collapsed.
    pub fn block_text(&self) -> String {
        self.block_text_with_cuts(&[], SENTINEL_FILLER)
    }

    /// `_split_at_anchors`: the text cut just before each anchor (node ids from
    /// `find_anchor`), each piece cleaned paragraph by paragraph. The cut marker is the
    /// first private-use code point not in the page's text (books use them for
    /// ornaments), so it can't be mistaken for text; with no anchors none is needed.
    /// None when all 6,400 are taken: the caller gives up on the TOC.
    pub fn block_text_split(&self, anchors: &[usize]) -> Option<Vec<String>> {
        let text = self.block_text();
        let clean = |piece: &str| piece.split("\n\n").map(pystr::strip).filter(|p| !p.is_empty()).collect::<Vec<_>>().join("\n\n");
        if anchors.is_empty() {
            return Some(vec![clean(&text)]);
        }
        let sentinel = SENTINEL_RANGE.filter_map(char::from_u32).find(|&c| !text.contains(c))?;
        let mut cuts = anchors.to_vec();
        cuts.sort_unstable();
        Some(self.block_text_with_cuts(&cuts, sentinel).split(sentinel).map(clean).collect())
    }

    fn block_text_with_cuts(&self, cuts: &[usize], sentinel: char) -> String {
        let (body, children) = self.body();
        let mut strings = Vec::new();
        let block = if BLOCK_TAGS.contains(&self.name(body).unwrap_or("")) { Some(body) } else { None };
        self.strings(&children, true, block, cuts, sentinel, &mut strings);

        let mut paragraphs = Vec::new();
        let mut current: Vec<&str> = Vec::new();
        let mut current_block = None;
        for (text, block, _) in &strings {
            if *block != current_block && !current.is_empty() {
                paragraphs.push(pystr::collapse(&current.concat()));
                current.clear();
            }
            current.push(text);
            current_block = *block;
        }
        if !current.is_empty() {
            paragraphs.push(pystr::collapse(&current.concat()));
        }
        paragraphs.into_iter().filter(|p| !p.is_empty()).collect::<Vec<_>>().join("\n\n")
    }

    /// `soup.find(["h1", "h2"]).get_text(strip=True)`: each string stripped, empty ones
    /// dropped, the rest glued together with nothing between them.
    pub fn first_heading(&self) -> Option<String> {
        let (_, children) = self.body();
        let heading = self.find_heading(&children)?;
        let mut strings = Vec::new();
        self.strings(&self.nodes[heading].children, false, None, &[], SENTINEL_FILLER, &mut strings); // padding is stripped anyway
        Some(strings.iter().filter(|(_, _, in_heading)| *in_heading).map(|(t, _, _)| pystr::strip(t)).collect())
    }

    /// `_find_anchor`: the first element whose id is `fragment`, else the first `<a>`
    /// whose name is, among what BeautifulSoup keeps (body, minus head/nav/script/style).
    /// Node ids follow document order, so the result also orders anchors.
    pub fn find_anchor(&self, fragment: &str) -> Option<usize> {
        let (_, children) = self.body();
        let elements = self.elements(&children, true);
        elements
            .iter()
            .copied()
            .find(|&e| self.attr(e, "id") == Some(fragment))
            .or_else(|| elements.iter().copied().find(|&e| self.name(e) == Some("a") && self.attr(e, "name") == Some(fragment)))
    }

    /// `_toc_links` on a nav document: (label, href) of every `<a href>` in the first
    /// `<nav>` whose epub:type includes "toc", in document order; None without one.
    pub fn toc_links(&self) -> Option<Vec<(String, String)>> {
        let all = self.elements(&self.nodes[0].children, false);
        let toc = all.iter().copied().find(|&e| {
            self.name(e) == Some("nav") && self.attr(e, "epub:type").unwrap_or("").split(pystr::is_space).any(|t| t == "toc")
        })?;
        let links = self
            .elements(&self.nodes[toc].children, false)
            .into_iter()
            .filter(|&e| self.name(e) == Some("a"))
            .filter_map(|a| Some((self.get_text(a), self.attr(a, "href")?.to_string())))
            .collect();
        Some(links)
    }

    /// Elements under `roots` in document order, optionally leaving out the subtrees
    /// `_load_soup` decomposes. Iterative, see `strings`.
    fn elements(&self, roots: &[usize], skip_removed: bool) -> Vec<usize> {
        let mut out = Vec::new();
        let mut stack: Vec<usize> = roots.iter().rev().copied().collect();
        while let Some(c) = stack.pop() {
            match self.name(c) {
                Some(n) if skip_removed && SKIPPED_TAGS.contains(&n) => {}
                Some(_) => {
                    out.push(c);
                    stack.extend(self.nodes[c].children.iter().rev());
                }
                None => {}
            }
        }
        out
    }

    /// BeautifulSoup's `get_text()` on the raw (html.parser) tree: every string below,
    /// as written, except those under script/style/template/rt/rp.
    fn get_text(&self, id: usize) -> String {
        let mut text = String::new();
        let mut stack: Vec<usize> = self.nodes[id].children.iter().rev().copied().collect();
        while let Some(c) = stack.pop() {
            match &self.nodes[c].kind {
                Kind::Text { text: t, in_heading: true } => text.push_str(t),
                Kind::Element(n) if n == "script" || n == "style" => {}
                Kind::Element(_) => stack.extend(self.nodes[c].children.iter().rev()),
                _ => {}
            }
        }
        text
    }

    fn find_heading(&self, children: &[usize]) -> Option<usize> {
        // Document-order search with an explicit stack (reverse-pushed), see `strings`.
        let mut stack: Vec<usize> = children.iter().rev().copied().collect();
        while let Some(c) = stack.pop() {
            match self.name(c) {
                Some(n) if SKIPPED_TAGS.contains(&n) => {}
                Some("h1" | "h2") => return Some(c),
                Some(_) => stack.extend(self.nodes[c].children.iter().rev()),
                None => {}
            }
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::Document;

    /// (name, page, block text, first heading). Expected values are what
    /// `ingest/epub.py` produces for each page through ebooklib + BeautifulSoup.
    const CASES: &[(&str, &str, &str, Option<&str>)] = &[
        ("calibre_self_closing_heading", "<body><h1 id=\"h\" class=\"c\"/><p>Text after.</p></body>", "Text after.", Some("")),
        ("nested_p", "<body>\n<p>a<p>b</p>c</p>\n</body>", "a\n\nb\n\nc", None),
        ("unclosed_div", "<body>\n<div><p>a</div>b\n</body>", "a\n\nb", None),
        ("inline_markup_and_wrapping", "<body>\n<p>He was <em>very</em> tired,\n   <span>so</span> he <a href='#n1'>slept</a>.</p>\n</body>", "He was very tired, so he slept.", None),
        ("br_is_a_space", "<body>\n<p>Roses are red<br/>violets are blue.</p>\n</body>", "Roses are red violets are blue.", None),
        ("br_without_slash_and_stray_end_tag", "<body>\n<p>one<br>two</br>three</p>\n</body>", "one twothree", None),
        ("heading_strings_are_glued", "<body>\n<h1>Chapter <em>One</em> x</h1>\n</body>", "Chapter One x", Some("ChapterOnex")),
        ("heading_skips_comments_and_cdata", "<body>\n<h2>Part<!-- x --> <em>Two</em><br/>Again<![CDATA[!]]></h2><p>p</p>\n</body>", "Part Two Again\n\np", Some("PartTwoAgain")),
        ("head_nav_script_style_are_dropped", "<html><head><title>T</title><style>p{}</style></head><body>\n<nav><p>nav</p></nav><script>if (a<b) {}</script><p>kept</p>\n</body></html>", "kept", None),
        ("loose_text_between_blocks", "<body>\n<div>Loose <b>div</b> text<p>nested para</p>tail of div</div>\n</body>", "Loose div text\n\nnested para\n\ntail of div", None),
        ("entities", "<body>\n<p>First&nbsp;line &amp; more&#8212;and &#x2014; &copy &#151; &foo; &lt;tag&gt;</p>\n</body>", "First line & more—and — © — &foo; <tag>", None),
        ("ruby_and_template_text", "<body>\n<p>ruby <ruby>漢<rt>kan</rt></ruby> and <template>tmpl</template> end</p>\n</body>", "ruby 漢kan and tmpl end", None),
        ("calibre_self_closing_span_and_anchor", "<body>\n<p><span class=\"x\"/>One <a id=\"p1\"/>two.</p><p>Three.</p>\n</body>", "One two.\n\nThree.", None),
        ("table_cells", "<body>\n<table><tr><td>cell1</td><td>cell2</td></tr></table>\n</body>", "cell1\n\ncell2", None),
        ("first_heading_is_h1_or_h2_in_document_order", "<body>\n<h3>Third</h3><h2>Second</h2><h1>First</h1>\n</body>", "Third\n\nSecond\n\nFirst", Some("Second")),
        ("cr_and_crlf_become_newlines", "<body>\n<h1>Byte\r\nOrder</h1>\r\n<p>x\ry</p>\n</body>", "Byte Order\n\nx y", Some("Byte\nOrder")),
        ("nul_becomes_replacement_char", "<body>\n<p>a\0b</p>\n</body>", "a�b", None),
        ("minified_inline_siblings_get_a_space", "<body><p><span>a</span><span>b</span></p></body>", "a b", None),
        ("minified_text_child_turns_padding_off", "<body><p><span>Small</span><span>Caps</span> glued.</p></body>", "SmallCaps glued.", None),
        ("leading_body_text_is_dropped", "<body>lead<p>para</p>tail</body>", "para\n\ntail", None),
        ("content_after_body_is_dropped", "<body>\n<p>in</p>\n</body><p>after</p>", "in", None),
        ("h1_inside_nav_is_skipped", "<body>\n<nav><h1>Contents</h1></nav><p>text</p>\n</body>", "text", None),
        ("no_body_element", "<p>just a fragment</p>", "just a fragment", None),
    ];

    #[test]
    fn pages_read_like_the_python_pipeline() {
        for (name, page, text, heading) in CASES {
            let doc = Document::parse(page);
            assert_eq!(doc.block_text(), *text, "{name}: block text");
            assert_eq!(doc.first_heading().as_deref(), *heading, "{name}: heading");
        }
    }

    /// (name, page, fragments, pieces): `_split_at_anchors` in `ingest/epub.py` on the
    /// page as ebooklib + BeautifulSoup read it.
    const SPLIT_CASES: &[(&str, &str, &[&str], &[&str])] = &[
        ("mid_paragraph_anchor", "<body>\n<p>Alpha beta <a id=\"mid\"></a>gamma.</p>\n</body>", &["mid"], &["Alpha beta", "gamma."]),
        ("anchor_on_heading_minified", "<body><p>Pre.</p><h2 id=\"c1\">One</h2><p>x</p></body>", &["c1"], &["Pre.", "One\n\nx"]),
        ("a_name_inside_heading", "<body>\n<p>Pre.</p><h2><a name=\"c2\"></a>Two</h2><p>x</p>\n</body>", &["c2"], &["Pre.", "Two\n\nx"]),
        ("id_wins_over_earlier_a_name", "<body>\n<p><a name=\"x\">first</a></p><p id=\"x\">second</p>\n</body>", &["x"], &["first", "second"]),
        ("anchor_in_nav_is_ignored", "<body>\n<nav><p id=\"x\">n</p></nav><p>a</p><p id=\"x\">b</p>\n</body>", &["x"], &["a", "b"]),
        ("br_anchor", "<body>\n<p>one<br id=\"b\"/>two</p>\n</body>", &["b"], &["one", "two"]),
        ("two_anchors", "<body>\n<p id=\"a\">A.</p><div><p>B.</p><p id=\"c\">C.</p></div>\n</body>", &["a", "c"], &["", "A.\n\nB.", "C."]),
        ("minified_inline_siblings", "<body><p><span>a</span><span id=\"s\">b</span></p></body>", &["s"], &["a", "b"]),
        ("anchor_after_dropped_leading_text", "<body>lead<p id=\"x\">para</p>tail</body>", &["x"], &["", "para\n\ntail"]),
        ("private_use_char_in_the_text_without_anchors", "<body>\n<p>x\u{e000}y</p>\n</body>", &[], &["x\u{e000}y"]),
        ("private_use_char_in_the_text_with_an_anchor", "<body><p>x\u{e000}y</p><p id=\"c\">C</p></body>", &["c"], &["x\u{e000}y", "C"]),
        ("anchor_in_template", "<body>\n<p>a <template><b id=\"t\">t</b></template> b</p>\n</body>", &["t"], &["a", "t b"]),
    ];

    #[test]
    fn pages_split_at_anchors_like_the_python_pipeline() {
        for (name, page, fragments, pieces) in SPLIT_CASES {
            let doc = Document::parse(page);
            let anchors: Vec<usize> = fragments.iter().map(|f| doc.find_anchor(f).unwrap_or_else(|| panic!("{name}: no #{f}"))).collect();
            assert_eq!(doc.block_text_split(&anchors).unwrap(), *pieces, "{name}");
        }
        assert_eq!(Document::parse("<body><p id=\"a\">x</p></body>").find_anchor("b"), None);
    }

    /// (name, nav document, links): `_toc_links` in `ingest/epub.py` reading the raw page
    /// with html.parser; None when no `<nav>` has epub:type "toc".
    type Links = Option<&'static [(&'static str, &'static str)]>;
    const NAV_CASES: &[(&str, &str, Links)] = &[
        (
            "first_toc_nav_nesting_flattened",
            "<?xml version=\"1.0\"?><html><head><title>Contents</title></head><body><nav epub:type=\"landmarks\"><a href=\"x.xhtml\">L</a></nav><nav epub:type=\" toc\n\"><ol><li><a href=\"a.xhtml\">Part <em>One</em></a><ol><li><a href=\"b.xhtml#c\">Nested</a></li></ol></li><li><a>no href</a><a href=\"\">empty</a></li></ol></nav><nav epub:type=\"toc\"><a href=\"z.xhtml\">second toc</a></nav></body></html>",
            Some(&[("Part One", "a.xhtml"), ("Nested", "b.xhtml#c"), ("empty", "")]),
        ),
        (
            "label_skips_script_rt_comments",
            "<nav epub:type=\"toc\"><a href=\"a.xhtml\">A<script>s</script><rt>r</rt><!--c--><br>B<style>t</style><template>u</template></a><a href=\"b.xhtml\">&amp; Co</a></nav>",
            Some(&[("AB", "a.xhtml"), ("& Co", "b.xhtml")]),
        ),
        ("no_toc_nav", "<nav epub:type=\"page-list\"><a href=\"a.xhtml\">1</a></nav><nav><a href=\"b.xhtml\">2</a></nav>", None),
        ("toc_in_head", "<html><head><nav epub:type=\"toc\"><a href=\"h.xhtml\">H</a></nav></head><body></body></html>", Some(&[("H", "h.xhtml")])),
    ];

    #[test]
    fn nav_links_read_like_the_python_pipeline() {
        for (name, page, links) in NAV_CASES {
            let got = Document::parse(page).toc_links();
            let want = links.map(|l| l.iter().map(|(a, b)| (a.to_string(), b.to_string())).collect::<Vec<_>>());
            assert_eq!(got, want, "{name}");
        }
    }

    #[test]
    fn deeply_nested_unclosed_paragraphs_do_not_overflow_the_stack() {
        let page = format!("<body>{}</body>", "<p>x".repeat(50_000));
        std::thread::Builder::new()
            .stack_size(2 * 1024 * 1024)
            .spawn(move || {
                let doc = Document::parse(&page);
                let text = doc.block_text();
                assert_eq!(text.split("\n\n").filter(|p| *p == "x").count(), 50_000);
                assert_eq!(doc.first_heading(), None);
                assert_eq!(doc.find_anchor("nope"), None);
                assert_eq!(doc.block_text_split(&[]).unwrap().len(), 1);
                let nav = format!("<nav epub:type=\"toc\">{}<a href=\"a\">deep</a>", "<li>".repeat(50_000));
                assert_eq!(Document::parse(&nav).toc_links(), Some(vec![("deep".to_string(), "a".to_string())]));
            })
            .unwrap()
            .join()
            .expect("walks must not overflow a 2 MB stack");
    }
}

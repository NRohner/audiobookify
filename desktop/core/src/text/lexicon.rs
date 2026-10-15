use fancy_regex::{NoExpand, RegexBuilder};

use crate::pystr::{self, BOUNDARY};

/// Replace each `(term, override)` pair as a whole word, case-insensitively. Longer
/// terms go first ("New York" before "York"); equal lengths keep the given order, so
/// a book-scoped entry listed before a global one wins. The override is literal text.
pub fn apply_lexicon<'a>(text: &str, entries: impl IntoIterator<Item = (&'a str, &'a str)>) -> String {
    let mut entries: Vec<_> = entries.into_iter().collect();
    entries.sort_by_key(|(term, _)| std::cmp::Reverse(pystr::len(term))); // stable, like sorted()
    let mut text = text.to_string();
    for (term, value) in entries {
        // BOUNDARY's lookarounds run on fancy-regex's slow path; most terms aren't in a
        // given chapter, and the plain search proves that cheaply.
        let present = regex::RegexBuilder::new(&regex::escape(term)).case_insensitive(true).build().expect("escaped term");
        if !present.is_match(&text) {
            continue;
        }
        // fancy-regex counts backtracks across a whole unanchored search (about one per byte
        // scanned), so the default cap would panic on a long chapter. This pattern has no
        // catastrophic case: the work is linear in the text, so the limit is lifted.
        let pattern = RegexBuilder::new(&format!("(?i){BOUNDARY}{}{BOUNDARY}", fancy_regex::escape(term)))
            .backtrack_limit(usize::MAX)
            .build()
            .expect("escaped term is a valid regex, and unlimited backtracking cannot fail");
        text = pattern.replace_all(&text, NoExpand(value)).into_owned();
    }
    text
}

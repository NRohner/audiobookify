use fancy_regex::{NoExpand, Regex};

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
        let pattern = Regex::new(&format!("(?i){BOUNDARY}{}{BOUNDARY}", fancy_regex::escape(term)))
            .expect("escaped term is a valid regex");
        text = pattern.replace_all(&text, NoExpand(value)).into_owned();
    }
    text
}

mod common;

use audiobookify_core::models::NewLexiconEntry;
use common::*;
use tempfile::TempDir;

fn global<'a>(term: &'a str, value: &'a str) -> NewLexiconEntry<'a> {
    NewLexiconEntry::new(term, value, "global")
}

fn for_book<'a>(term: &'a str, value: &'a str, book_id: i64) -> NewLexiconEntry<'a> {
    NewLexiconEntry { book_id, ..NewLexiconEntry::new(term, value, "book") }
}

fn from_audio<'a>() -> NewLexiconEntry<'a> {
    NewLexiconEntry {
        source: "from_audio", phonemes: Some("k ə d eɪ ɹ n"), sample_audio_path: Some("/clips/cadeyrn.wav"),
        ..global("Cadeyrn", "kuhdayrn")
    }
}

#[test]
fn create_and_find_global_lexicon_entry() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let entry = db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    assert_eq!(entry.book_id, 0);
    let found = db.find_lexicon_entry("Cadeyrn", 0).unwrap().unwrap();
    assert_eq!(found.override_value, "KAH-dern");
    assert_eq!(found.scope, "global");
    assert_eq!(found.source, "manual");
}

#[test]
fn create_and_find_book_scoped_lexicon_entry() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    let entry = db.create_lexicon_entry(&NewLexiconEntry {
        notes: Some("book-specific take"), ..for_book("Cadeyrn", "kah-DAIRN", book.id)
    }).unwrap();
    assert_eq!(entry.book_id, book.id);

    let found = db.find_lexicon_entry("Cadeyrn", book.id).unwrap().unwrap();
    assert_eq!(found.override_value, "kah-DAIRN");
    assert_eq!(found.notes.as_deref(), Some("book-specific take"));
    // the global lookup must not see the book-scoped entry
    assert!(db.find_lexicon_entry("Cadeyrn", 0).unwrap().is_none());
}

#[test]
fn create_lexicon_entry_rejects_duplicate_term_in_same_scope() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    assert_eq!(invalid(db.create_lexicon_entry(&global("Cadeyrn", "something-else"))),
               "a lexicon entry for 'Cadeyrn' already exists in this scope");
}

#[test]
fn create_lexicon_entry_allows_same_term_at_different_scopes() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    // a book-scoped override of a globally-defined term is legitimate
    db.create_lexicon_entry(&for_book("Cadeyrn", "kah-DAIRN", book.id)).unwrap();
}

#[test]
fn list_lexicon_entries_without_book_id_returns_global_only() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    db.create_lexicon_entry(&global("Global One", "g-one")).unwrap();
    db.create_lexicon_entry(&for_book("Book One", "b-one", book.id)).unwrap();
    let terms: Vec<String> = db.list_lexicon_entries(None).unwrap().into_iter().map(|e| e.term).collect();
    assert_eq!(terms, vec!["Global One"]);
}

#[test]
fn list_lexicon_entries_with_book_id_returns_book_scoped_first_then_global() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    db.create_lexicon_entry(&global("Global One", "g-one")).unwrap();
    db.create_lexicon_entry(&for_book("Book One", "b-one", book.id)).unwrap();
    let terms: Vec<String> =
        db.list_lexicon_entries(Some(book.id)).unwrap().into_iter().map(|e| e.term).collect();
    assert_eq!(terms, vec!["Book One", "Global One"]);
}

#[test]
fn delete_lexicon_entry_removes_it() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    db.delete_lexicon_entry("Cadeyrn", 0).unwrap();
    assert!(db.find_lexicon_entry("Cadeyrn", 0).unwrap().is_none());
}

#[test]
fn delete_lexicon_entry_raises_not_found_for_missing_entry() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert_eq!(not_found(db.delete_lexicon_entry("Cadeyrn", 0)), "no lexicon entry for 'Cadeyrn' in this scope");
}

#[test]
fn create_lexicon_entry_stores_phonemes_and_sample_audio_path() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let entry = db.create_lexicon_entry(&from_audio()).unwrap();
    assert_eq!(entry.source, "from_audio");
    assert_eq!(entry.phonemes.as_deref(), Some("k ə d eɪ ɹ n"));
    assert_eq!(entry.sample_audio_path.as_deref(), Some("/clips/cadeyrn.wav"));
    let found = db.find_lexicon_entry("Cadeyrn", 0).unwrap().unwrap();
    assert_eq!(found.phonemes.as_deref(), Some("k ə d eɪ ɹ n"));
    assert_eq!(found.sample_audio_path.as_deref(), Some("/clips/cadeyrn.wav"));
}

#[test]
fn create_lexicon_entry_defaults_phonemes_and_sample_audio_path_to_none() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let entry = db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    assert_eq!(entry.phonemes, None);
    assert_eq!(entry.sample_audio_path, None);
}

#[test]
fn update_lexicon_entry_override_changes_value_and_keeps_metadata() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    db.create_lexicon_entry(&NewLexiconEntry { notes: Some("derived from audio"), ..from_audio() }).unwrap();

    let updated = db.update_lexicon_entry_override("Cadeyrn", "kah-DAIRN", 0).unwrap();

    assert_eq!(updated.override_value, "kah-DAIRN");
    assert_eq!(updated.source, "from_audio");
    assert_eq!(updated.phonemes.as_deref(), Some("k ə d eɪ ɹ n"));
    assert_eq!(updated.sample_audio_path.as_deref(), Some("/clips/cadeyrn.wav"));
    assert_eq!(updated.notes.as_deref(), Some("derived from audio"));
}

#[test]
fn update_lexicon_entry_override_raises_not_found_for_missing_entry() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    assert!(not_found(db.update_lexicon_entry_override("Cadeyrn", "kah-DAIRN", 0)).contains("Cadeyrn"));
}

#[test]
fn update_lexicon_entry_override_respects_book_scope() {
    let dir = TempDir::new().unwrap();
    let db = open(dir.path());
    let book = md_book(&db, dir.path(), "/books/test.md");
    db.create_lexicon_entry(&global("Cadeyrn", "KAH-dern")).unwrap();
    db.create_lexicon_entry(&for_book("Cadeyrn", "kah-DAIRN", book.id)).unwrap();

    db.update_lexicon_entry_override("Cadeyrn", "NEW-book-version", book.id).unwrap();

    assert_eq!(db.find_lexicon_entry("Cadeyrn", book.id).unwrap().unwrap().override_value, "NEW-book-version");
    assert_eq!(db.find_lexicon_entry("Cadeyrn", 0).unwrap().unwrap().override_value, "KAH-dern"); // global untouched
}

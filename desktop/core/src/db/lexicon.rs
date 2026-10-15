use rusqlite::{params, Row};

use super::{is_constraint, now, Database, DbError, Result};
use crate::models::{LexiconEntry, NewLexiconEntry};

impl Database {
    pub fn create_lexicon_entry(&self, e: &NewLexiconEntry<'_>) -> Result<LexiconEntry> {
        let inserted = self.conn.execute(
            "INSERT INTO lexicon_entries (term, override_value, source, scope, book_id, notes, \
             created_at, phonemes, sample_audio_path) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![e.term, e.override_value, e.source, e.scope, e.book_id, e.notes, now(),
                    e.phonemes, e.sample_audio_path],
        );
        match inserted {
            Err(err) if is_constraint(&err) => Err(DbError::Invalid(format!(
                "a lexicon entry for '{}' already exists in this scope", e.term
            ))),
            Err(err) => Err(err.into()),
            Ok(_) => Ok(self.conn.query_row("SELECT * FROM lexicon_entries WHERE id = ?1",
                                            [self.conn.last_insert_rowid()], lexicon_from_row)?),
        }
    }

    pub fn find_lexicon_entry(&self, term: &str, book_id: i64) -> Result<Option<LexiconEntry>> {
        self.query_opt("SELECT * FROM lexicon_entries WHERE term = ?1 AND book_id = ?2",
                       params![term, book_id], lexicon_from_row)
    }

    /// Global entries only, or a book's entries first and then the global ones.
    pub fn list_lexicon_entries(&self, book_id: Option<i64>) -> Result<Vec<LexiconEntry>> {
        match book_id {
            None => self.query_all("SELECT * FROM lexicon_entries WHERE book_id = 0 ORDER BY id", [],
                                   lexicon_from_row),
            Some(book_id) => self.query_all(
                "SELECT * FROM lexicon_entries WHERE book_id = ?1 OR book_id = 0 ORDER BY (book_id = 0), id",
                [book_id], lexicon_from_row,
            ),
        }
    }

    pub fn delete_lexicon_entry(&self, term: &str, book_id: i64) -> Result<()> {
        let changed = self.conn.execute("DELETE FROM lexicon_entries WHERE term = ?1 AND book_id = ?2",
                                        params![term, book_id])?;
        if changed == 0 {
            return Err(missing_entry(term));
        }
        Ok(())
    }

    pub fn update_lexicon_entry_override(&self, term: &str, override_value: &str,
                                         book_id: i64) -> Result<LexiconEntry> {
        let changed = self.conn.execute(
            "UPDATE lexicon_entries SET override_value = ?1 WHERE term = ?2 AND book_id = ?3",
            params![override_value, term, book_id],
        )?;
        if changed == 0 {
            return Err(missing_entry(term));
        }
        self.find_lexicon_entry(term, book_id)?.ok_or_else(|| missing_entry(term))
    }
}

fn missing_entry(term: &str) -> DbError {
    DbError::NotFound(format!("no lexicon entry for '{term}' in this scope"))
}

fn lexicon_from_row(r: &Row<'_>) -> rusqlite::Result<LexiconEntry> {
    Ok(LexiconEntry {
        id: r.get("id")?, term: r.get("term")?, override_value: r.get("override_value")?,
        source: r.get("source")?, scope: r.get("scope")?, book_id: r.get("book_id")?,
        notes: r.get("notes")?, created_at: r.get("created_at")?,
        phonemes: r.get("phonemes")?, sample_audio_path: r.get("sample_audio_path")?,
    })
}

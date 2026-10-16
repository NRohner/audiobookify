//! Pronunciation endpoints: global entries only, as in the browser app.

use serde::Serialize;

use super::{found, ApiResult};
use crate::db::Database;
use crate::models::{LexiconEntry, NewLexiconEntry};

#[derive(Debug, Serialize)]
pub struct LexiconDto {
    pub term: String,
    pub override_value: String,
    pub notes: Option<String>,
}

fn dto(e: LexiconEntry) -> LexiconDto {
    LexiconDto { term: e.term, override_value: e.override_value, notes: e.notes }
}

pub fn list_pronunciations(db: &Database) -> ApiResult<Vec<LexiconDto>> {
    Ok(db.list_lexicon_entries(None)?.into_iter().map(dto).collect())
}

pub fn create_pronunciation(db: &Database, term: &str, override_value: &str, notes: Option<&str>) -> ApiResult<LexiconDto> {
    let mut entry = NewLexiconEntry::new(term, override_value, "global");
    entry.notes = notes;
    Ok(dto(db.create_lexicon_entry(&entry)?))
}

pub fn update_pronunciation(db: &Database, term: &str, override_value: &str) -> ApiResult<LexiconDto> {
    Ok(dto(found(db.update_lexicon_entry_override(term, override_value, 0), "pronunciation entry")?))
}

pub fn delete_pronunciation(db: &Database, term: &str) -> ApiResult<()> {
    found(db.delete_lexicon_entry(term, 0), "pronunciation entry")
}

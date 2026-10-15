//! Schema changes for database files created by older versions, ported from db.py.
//! Each one checks first, so running them on an up-to-date file does nothing.

use std::collections::HashSet;

use rusqlite::Connection;

/// (table, column, declaration), in the order db.py adds them.
const ADDED_COLUMNS: &[(&str, &str, &str)] = &[
    ("lexicon_entries", "phonemes", "TEXT"),
    ("lexicon_entries", "sample_audio_path", "TEXT"),
    ("chapters", "used_fallback", "INTEGER NOT NULL DEFAULT 0"),
    ("text_clips", "used_fallback", "INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
    ("books", "engine", "TEXT NOT NULL DEFAULT 'chatterbox-turbo'"),
    ("text_clips", "engine", "TEXT NOT NULL DEFAULT 'chatterbox-turbo'"),
    ("chapters", "engine", "TEXT"),
    ("voice_profiles", "transcript_status", "TEXT"),
    ("voice_profiles", "transcript_error", "TEXT"),
    ("voice_profiles", "reference_words", "TEXT"),
    ("voice_profiles", "reference_trim_end", "REAL"),
    ("voice_profiles", "tempo", "REAL NOT NULL DEFAULT 1.0"),
    ("text_clips", "tempo", "REAL"),
];

pub fn migrate_columns(conn: &Connection) -> rusqlite::Result<()> {
    for &(table, column, declaration) in ADDED_COLUMNS {
        if columns(conn, table)?.contains(column) {
            continue;
        }
        conn.execute(&format!("ALTER TABLE {table} ADD COLUMN {column} {declaration}"), [])?;
        if (table, column) == ("chapters", "engine") {
            // Audio finished before engines were recorded came from PyTorch Chatterbox.
            conn.execute("UPDATE chapters SET engine = 'chatterbox' WHERE status = 'done'", [])?;
        }
    }
    Ok(())
}

fn columns(conn: &Connection, table: &str) -> rusqlite::Result<HashSet<String>> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let names = stmt.query_map([], |row| row.get::<_, String>("name"))?.collect();
    names
}

//! Schema changes for database files created by older versions, ported from db.py.
//! Each one checks first, so running them on an up-to-date file does nothing.

use std::collections::HashSet;
use std::fs;
use std::path::Path;

use rusqlite::Connection;

use crate::db::Result;
use crate::voice_storage;

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

/// Copies reference clips that live outside `voices_dir` (old uploads) into it and
/// points the profile at the copy. A profile that can't be migrated is left as it
/// is and reported in the returned warnings, so one bad file can't block startup.
pub fn migrate_voice_reference_audio(conn: &Connection, voices_dir: &Path) -> Result<Vec<String>> {
    // Resolve symlinks like Python's Path.resolve(); the dir may not exist yet.
    let voices_root = fs::canonicalize(voices_dir).or_else(|_| std::path::absolute(voices_dir))?;
    let profiles: Vec<(i64, String, String)> = {
        let mut stmt = conn.prepare("SELECT id, name, reference_audio_path FROM voice_profiles")?;
        let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
        rows.collect::<rusqlite::Result<_>>()?
    };
    let mut warnings = Vec::new();
    for (id, name, current) in profiles {
        let current_path = Path::new(&current);
        let resolved = fs::canonicalize(current_path).unwrap_or_else(|_| current_path.to_path_buf());
        if current_path.starts_with(&voices_root) || resolved.starts_with(&voices_root) {
            continue;
        }
        if !current_path.is_file() {
            warnings.push(format!(
                "voice profile {id} ({name})'s reference audio '{current}' no longer exists - \
                 skipping migration for this profile"
            ));
            continue;
        }
        match voice_storage::store_reference_audio(&voices_root, current_path, id, &name) {
            Ok(new_path) => {
                conn.execute("UPDATE voice_profiles SET reference_audio_path = ?1 WHERE id = ?2",
                             rusqlite::params![new_path.to_string_lossy(), id])?;
            }
            Err(e) => warnings.push(format!(
                "voice profile {id} ({name})'s reference audio could not be migrated to permanent \
                 storage ({e}) - skipping this profile"
            )),
        }
    }
    Ok(warnings)
}

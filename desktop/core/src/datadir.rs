//! First launch of the desktop app: copy the browser app's library out of the repo into the
//! app data dir, and point the stored paths at the copies. The repo is left as it was.

use std::fs;
use std::io;
use std::path::Path;

use rusqlite::{Connection, OpenFlags};

use crate::app::AppPaths;
use crate::db;

fn copy_dir(from: &Path, to: &Path) -> io::Result<()> {
    fs::create_dir_all(to)?;
    for entry in fs::read_dir(from)? {
        let entry = entry?;
        let dest = to.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_dir(&entry.path(), &dest)?;
        } else {
            fs::copy(entry.path(), dest)?;
        }
    }
    Ok(())
}

/// Copies `<repo>/audiobookify.db`, `uploads/`, `voices/` and `text_clips/` into `paths`.
/// The DB gets its real name last, so a failed import leaves nothing that looks like a library.
pub fn import_repo_data(repo: &Path, paths: &AppPaths) -> db::Result<()> {
    let repo = fs::canonicalize(repo)?;
    fs::create_dir_all(&paths.root)?;
    let tmp = paths.root.join("audiobookify.db.importing");
    let _ = fs::remove_file(&tmp);
    // Read-only, so a missing DB is an error instead of a new empty one. VACUUM INTO takes a
    // consistent copy even while another process has the DB open.
    Connection::open_with_flags(repo.join("audiobookify.db"), OpenFlags::SQLITE_OPEN_READ_ONLY)?
        .execute("VACUUM INTO ?1", [tmp.to_string_lossy()])?;

    let conn = Connection::open(&tmp)?;
    for (dir, new_dir, table, column) in [
        ("uploads", paths.uploads(), "books", "source_path"),
        ("voices", paths.voices(), "voice_profiles", "reference_audio_path"),
        ("text_clips", paths.text_clips(), "text_clips", "output_path"),
    ] {
        let old = repo.join(dir);
        if old.is_dir() {
            copy_dir(&old, &new_dir)?;
        }
        let new_prefix = format!("{}/", new_dir.display());
        // Absolute paths, plus the relative ones the Python worker wrote for text clips.
        for old_prefix in [format!("{}/", old.display()), format!("{dir}/")] {
            conn.execute(
                &format!("UPDATE {table} SET {column} = ?2 || substr({column}, length(?1) + 1) \
                          WHERE substr({column}, 1, length(?1)) = ?1"),
                [&old_prefix, &new_prefix],
            )?;
        }
    }
    drop(conn);
    fs::rename(&tmp, paths.db())?;
    Ok(())
}

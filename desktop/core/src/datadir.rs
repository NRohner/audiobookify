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
/// Refuses if a library already exists there. The DB gets its real name last, and the temp
/// DB is removed on failure, so a failed import leaves nothing that looks like a library.
pub fn import_repo_data(repo: &Path, paths: &AppPaths) -> db::Result<()> {
    if paths.db().exists() {
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            format!("{} already exists", paths.db().display()),
        )
        .into());
    }
    // Rows may hold the repo path as given (e.g. through a symlink) or the resolved one.
    let given = std::path::absolute(repo)?;
    let repo = fs::canonicalize(repo)?;
    fs::create_dir_all(&paths.root)?;
    let tmp = paths.root.join("audiobookify.db.importing");
    let _ = fs::remove_file(&tmp);
    let result = import_into(&repo, &given, &tmp, paths);
    if result.is_err() {
        // Copied folders stay; a later import overwrites them.
        let _ = fs::remove_file(&tmp);
    }
    result
}

fn import_into(repo: &Path, given: &Path, tmp: &Path, paths: &AppPaths) -> db::Result<()> {
    // Read-only, so a missing DB is an error instead of a new empty one. VACUUM INTO takes a
    // consistent copy even while another process has the DB open.
    Connection::open_with_flags(repo.join("audiobookify.db"), OpenFlags::SQLITE_OPEN_READ_ONLY)?
        .execute("VACUUM INTO ?1", [tmp.to_string_lossy()])?;

    let conn = Connection::open(tmp)?;
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
        let mut old_prefixes = vec![format!("{}/", old.display())];
        if given != repo {
            old_prefixes.push(format!("{}/", given.join(dir).display()));
        }
        // The relative form is what the Python worker wrote for text clips.
        old_prefixes.push(format!("{dir}/"));
        for old_prefix in old_prefixes {
            conn.execute(
                &format!("UPDATE {table} SET {column} = ?2 || substr({column}, length(?1) + 1) \
                          WHERE substr({column}, 1, length(?1)) = ?1"),
                [&old_prefix, &new_prefix],
            )?;
        }
    }
    drop(conn);
    fs::rename(tmp, paths.db())?;
    Ok(())
}

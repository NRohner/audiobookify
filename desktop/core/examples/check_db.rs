//! Phase 1 exit check: open a real audiobookify.db, decode every row, write, and
//! confirm a second open changes nothing. Run it on a copy, never the live file.

use std::error::Error;
use std::path::Path;

use audiobookify_core::db::Database;
use rusqlite::Connection;

fn schema(db_path: &Path) -> rusqlite::Result<Vec<(String, String)>> {
    let conn = Connection::open(db_path)?;
    let mut stmt = conn.prepare("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")?;
    let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?;
    rows.collect()
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [db_path, voices_dir] = args.as_slice() else {
        eprintln!("usage: check_db <path/to/audiobookify.db> <path/to/voices>");
        std::process::exit(2);
    };
    let db_path = Path::new(db_path);

    let db = Database::open(db_path, voices_dir)?;
    let books = db.list_books()?;
    let (mut chapters, mut jobs, mut lexicon) = (0, 0, db.list_lexicon_entries(None)?.len());
    for book in &books {
        let book_chapters = db.list_chapters(book.id)?;
        for chapter in &book_chapters {
            jobs += db.list_jobs_for_chapter(chapter.id)?.len();
        }
        chapters += book_chapters.len();
        lexicon += db.list_lexicon_entries(Some(book.id))?.iter().filter(|e| e.book_id == book.id).count();
    }
    let voices = db.list_voice_profiles()?.len();
    let clips = db.list_text_clips()?.len();

    let paused = db.is_paused()?;
    db.set_paused(!paused)?;
    assert_eq!(db.is_paused()?, !paused, "write did not stick");
    db.set_paused(paused)?;
    drop(db);

    let before = schema(db_path)?;
    drop(Database::open(db_path, voices_dir)?);
    assert_eq!(schema(db_path)?, before, "second open changed the schema");

    println!("ok: {} books, {chapters} chapters, {jobs} jobs, {voices} voices, {lexicon} lexicon entries, \
              {clips} text clips; write ok; reopen is a no-op", books.len());
    Ok(())
}

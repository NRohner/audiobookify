mod common;

use std::fs;
use std::path::Path;

use audiobookify_core::app::AppPaths;
use audiobookify_core::datadir::import_repo_data;
use audiobookify_core::db::Database;
use audiobookify_core::models::{JobStatus, SourceFormat};
use common::*;
use tempfile::TempDir;

#[test]
fn imports_the_db_and_files_and_points_the_rows_at_the_copies() {
    let dir = TempDir::new().unwrap();
    let repo = dir.path().join("repo");
    for sub in ["uploads/abc", "voices/1_Nova", "text_clips"] {
        fs::create_dir_all(repo.join(sub)).unwrap();
    }
    let repo = fs::canonicalize(repo).unwrap();
    fs::write(repo.join("uploads/abc/book.md"), "# One\nHi.\n").unwrap();
    fs::write(repo.join("voices/1_Nova/reference.wav"), "wav").unwrap();
    fs::write(repo.join("text_clips/text-clip-1.mp3"), "mp3").unwrap();
    {
        let db = Database::open(repo.join("audiobookify.db"), repo.join("voices")).unwrap();
        db.create_book("B", None, &path(&repo, "uploads/abc/book.md"), SourceFormat::Markdown, "/out").unwrap();
        voice_at(&db, "Nova", &path(&repo, "voices/1_Nova/reference.wav"));
        let clip = db.create_text_clip("Hi", None, "chatterbox-turbo", None).unwrap();
        // Relative, as the Python worker wrote it.
        db.finish_text_clip(clip.id, JobStatus::Done, Some("text_clips/text-clip-1.mp3"), None, false).unwrap();
    }
    let paths = AppPaths::new(dir.path().join("data"));

    import_repo_data(&repo, &paths).unwrap();

    let db = paths.open_db().unwrap();
    let book = db.list_books().unwrap().remove(0);
    assert_eq!(book.source_path, path(&paths.uploads(), "abc/book.md"));
    let voice = db.list_voice_profiles().unwrap().remove(0);
    assert_eq!(voice.reference_audio_path, path(&paths.voices(), "1_Nova/reference.wav"));
    let clip = db.list_text_clips().unwrap().remove(0);
    assert_eq!(clip.output_path.unwrap(), path(&paths.text_clips(), "text-clip-1.mp3"));
    assert_eq!(fs::read_to_string(&book.source_path).unwrap(), "# One\nHi.\n");
    assert!(Path::new(&voice.reference_audio_path).is_file());
    assert!(repo.join("audiobookify.db").is_file() && repo.join("uploads/abc/book.md").is_file());
    assert!(!paths.root.join("audiobookify.db.importing").exists());
}

#[test]
fn a_failed_import_leaves_no_db_behind() {
    let dir = TempDir::new().unwrap();
    let repo = dir.path().join("repo");
    fs::create_dir_all(&repo).unwrap();
    let paths = AppPaths::new(dir.path().join("data"));

    assert!(import_repo_data(&repo, &paths).is_err());
    assert!(!paths.db().exists());
}

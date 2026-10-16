//! End-to-end parity with the Python pipeline: ingest -> lexicon -> chunk_text over
//! real ingest output. The goldens were generated from the Python pipeline, which is gone; they are frozen.

mod fixture;

use std::path::Path;

use audiobookify_core::ingest::ingest_by_path;
use audiobookify_core::text::{apply_lexicon, chunk_text};
use fixture::{assert_same, fixtures, load, str_of, usize_of};

/// Checks one golden file and returns how many chapters it covered.
fn check(golden_path: &Path) -> usize {
    let golden = load(golden_path);
    let name = golden_path.file_name().unwrap().to_string_lossy().into_owned();
    let source = Path::new(str_of(&golden["source"]));
    let source = if source.is_absolute() { source.to_path_buf() } else { fixtures().join(source) };

    let (book, format) = ingest_by_path(&source, usize_of(&golden["heading_level"]))
        .unwrap_or_else(|e| panic!("{name}: ingest failed: {e}"));
    assert_eq!(format.as_str(), str_of(&golden["format"]), "{name}: format");
    assert_eq!(book.title, str_of(&golden["title"]), "{name}: title");
    assert_eq!(book.author.as_deref(), golden["author"].as_str(), "{name}: author");

    let chapters = golden["chapters"].as_array().unwrap();
    let titles: Vec<&str> = chapters.iter().map(|c| str_of(&c["title"])).collect();
    assert_eq!(book.chapters.iter().map(|c| c.title.as_str()).collect::<Vec<_>>(), titles, "{name}: chapter titles");
    for (i, (chapter, expected)) in book.chapters.iter().zip(chapters).enumerate() {
        assert_same(&format!("{name}: chapter {i} text"), str_of(&expected["text"]), &chapter.text);
    }

    let lexicon: Vec<(&str, &str)> =
        golden["lexicon"].as_array().unwrap().iter().map(|e| (str_of(&e[0]), str_of(&e[1]))).collect();
    for chunking in golden["chunkings"].as_array().unwrap() {
        let (max, min) = (usize_of(&chunking["max_chars"]), usize_of(&chunking["min_chars"]));
        assert_eq!(
            book.chapters.len(),
            chunking["chapters"].as_array().unwrap().len(),
            "{name}: chapter count vs chunking at {max}/{min}"
        );
        for (i, (chapter, expected)) in book.chapters.iter().zip(chunking["chapters"].as_array().unwrap()).enumerate() {
            let text = if chunking["lexicon"].as_bool().unwrap() { apply_lexicon(&chapter.text, lexicon.iter().copied()) } else { chapter.text.clone() };
            let chunks = chunk_text(&text, max, min);
            let expected = expected.as_array().unwrap();
            let label = format!("{name}: chapter {i} at {max}/{min}");
            for (j, (chunk, want)) in chunks.iter().zip(expected).enumerate() {
                assert_same(&format!("{label}, chunk {j}"), str_of(&want[0]), &chunk.text);
                assert_eq!(chunk.ends_with.as_str(), str_of(&want[1]), "{label}, chunk {j} ends_with");
            }
            assert_eq!(chunks.len(), expected.len(), "{label}: chunk count");
        }
    }
    book.chapters.len()
}

fn goldens_in(dir: &Path) -> Vec<std::path::PathBuf> {
    let mut paths: Vec<_> = std::fs::read_dir(dir)
        .map(|entries| entries.map(|e| e.unwrap().path()).filter(|p| p.extension().is_some_and(|x| x == "json")).collect())
        .unwrap_or_default();
    paths.sort();
    paths
}

#[test]
fn committed_fixtures_match_python() {
    let paths = goldens_in(&fixtures().join("golden"));
    assert!(!paths.is_empty(), "no goldens in tests/fixtures/text/golden");
    for path in paths {
        check(&path);
    }
}

/// Copyrighted books from uploads/ (generated before the Python pipeline was deleted). Their goldens are
/// git-ignored, so this passes with a note when there are none.
#[test]
fn local_books_match_python() {
    let paths = goldens_in(&fixtures().join("local"));
    if paths.is_empty() && std::env::var("REQUIRE_LOCAL_GOLDENS").is_err() {
        eprintln!("no local book goldens in tests/fixtures/text/local; skipped");
        return;
    }
    for path in &paths {
        let chapters = check(path);
        eprintln!("ok {} ({chapters} chapters)", path.file_name().unwrap().to_string_lossy());
    }
    eprintln!("checked {} local books", paths.len());
    require_local_goldens(paths.len());
}

fn require_local_goldens(checked: usize) {
    if let Ok(v) = std::env::var("REQUIRE_LOCAL_GOLDENS") {
        let want: usize = v.parse().expect("REQUIRE_LOCAL_GOLDENS must be a number");
        assert_eq!(checked, want, "REQUIRE_LOCAL_GOLDENS={want} but checked {checked} local goldens");
    }
}

import pytest

from audiobookify.db import Database
from audiobookify.models import SourceFormat, ChapterStatus
from audiobookify.ingest.base import RawChapter


def test_create_book_and_add_chapters(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author="Jane Author", source_path="/books/test.epub",
        source_format=SourceFormat.EPUB, output_dir=str(tmp_path / "out"),
    )
    db.add_chapters(book.id, [RawChapter(title="Chapter One", text="Once upon a time.")])

    chapters = db.list_chapters(book.id)
    assert len(chapters) == 1
    assert chapters[0].order_index == 0
    assert chapters[0].title == "Chapter One"
    assert chapters[0].status == ChapterStatus.PENDING
    db.close()


def test_update_chapter_status_persists_across_reopen(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    [chapter] = db.add_chapters(book.id, [RawChapter(title="Ch 1", text="Body.")])
    db.update_chapter_status(chapter.id, ChapterStatus.DONE, output_mp3_path="/out/01 - Ch 1.mp3")
    db.close()

    reopened = Database(db_path)
    [reloaded] = reopened.list_chapters(book.id)
    assert reloaded.status == ChapterStatus.DONE
    assert reloaded.output_mp3_path == "/out/01 - Ch 1.mp3"
    reopened.close()


def test_find_book_by_source(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_book(
        title="Test Book", author=None, source_path="/books/test.epub",
        source_format=SourceFormat.EPUB, output_dir=str(tmp_path / "out"),
    )
    found = db.find_book_by_source("/books/test.epub")
    assert found is not None
    assert found.title == "Test Book"
    assert db.find_book_by_source("/nope.epub") is None
    db.close()


def test_get_chapter_raises_keyerror_for_missing_id(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    with pytest.raises(KeyError, match="no chapter with id 999"):
        db.get_chapter(999)
    db.close()


def test_update_chapter_status_raises_keyerror_for_missing_id(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    with pytest.raises(KeyError, match="no chapter with id 999"):
        db.update_chapter_status(999, ChapterStatus.DONE)
    db.close()


def test_create_and_find_voice_profile_by_name(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    profile = db.create_voice_profile(
        name="narrator-1", engine="chatterbox", reference_audio_path="/refs/clip.wav",
        reference_transcript="Hello there.", exaggeration=0.6, cfg_weight=0.4, temperature=0.75,
    )
    assert profile.id is not None
    assert profile.name == "narrator-1"

    found = db.find_voice_profile_by_name("narrator-1")
    assert found is not None
    assert found.reference_audio_path == "/refs/clip.wav"
    assert found.reference_transcript == "Hello there."
    assert found.exaggeration == 0.6
    assert found.cfg_weight == 0.4
    assert found.temperature == 0.75
    assert db.find_voice_profile_by_name("nope") is None
    db.close()


def test_list_voice_profiles_returns_all_in_creation_order(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_voice_profile(name="a", engine="chatterbox", reference_audio_path="/a.wav",
                             reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8)
    db.create_voice_profile(name="b", engine="chatterbox", reference_audio_path="/b.wav",
                             reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8)

    profiles = db.list_voice_profiles()
    assert [p.name for p in profiles] == ["a", "b"]
    db.close()


def test_create_voice_profile_rejects_duplicate_name(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_voice_profile(name="dup", engine="chatterbox", reference_audio_path="/a.wav",
                             reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8)

    with pytest.raises(ValueError, match="dup"):
        db.create_voice_profile(name="dup", engine="chatterbox", reference_audio_path="/b.wav",
                                 reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8)
    db.close()


def test_update_book_voice_profile_persists(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    profile = db.create_voice_profile(
        name="narrator-1", engine="chatterbox", reference_audio_path="/refs/clip.wav",
        reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8,
    )

    db.update_book_voice_profile(book.id, profile.id)

    reloaded = db.get_book(book.id)
    assert reloaded.voice_profile_id == profile.id
    db.close()


def test_get_voice_profile_by_id(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    profile = db.create_voice_profile(
        name="narrator-1", engine="chatterbox", reference_audio_path="/refs/clip.wav",
        reference_transcript=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8,
    )

    found = db.get_voice_profile_by_id(profile.id)
    assert found is not None
    assert found.name == "narrator-1"
    assert db.get_voice_profile_by_id(999999) is None
    db.close()


def test_create_and_find_global_lexicon_entry(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    entry = db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")

    assert entry.id is not None
    assert entry.book_id == 0

    found = db.find_lexicon_entry("Cadeyrn")
    assert found is not None
    assert found.override_value == "KAH-dern"
    assert found.scope == "global"
    assert found.source == "manual"
    db.close()


def test_create_and_find_book_scoped_lexicon_entry(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    entry = db.create_lexicon_entry(
        term="Cadeyrn", override_value="kah-DAIRN", scope="book", book_id=book.id, notes="book-specific take"
    )

    assert entry.book_id == book.id

    found = db.find_lexicon_entry("Cadeyrn", book_id=book.id)
    assert found is not None
    assert found.override_value == "kah-DAIRN"
    assert found.notes == "book-specific take"

    # the global lookup (book_id=0, the default) must not see the book-scoped entry
    assert db.find_lexicon_entry("Cadeyrn") is None
    db.close()


def test_create_lexicon_entry_rejects_duplicate_term_in_same_scope(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")

    with pytest.raises(ValueError, match="Cadeyrn"):
        db.create_lexicon_entry(term="Cadeyrn", override_value="something-else", scope="global")
    db.close()


def test_create_lexicon_entry_allows_same_term_at_different_scopes(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")
    # must not raise - book-scoped override of a globally-defined term is legitimate
    db.create_lexicon_entry(term="Cadeyrn", override_value="kah-DAIRN", scope="book", book_id=book.id)
    db.close()


def test_list_lexicon_entries_without_book_id_returns_global_only(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.create_lexicon_entry(term="Global One", override_value="g-one", scope="global")
    db.create_lexicon_entry(term="Book One", override_value="b-one", scope="book", book_id=book.id)

    entries = db.list_lexicon_entries()
    assert [e.term for e in entries] == ["Global One"]
    db.close()


def test_list_lexicon_entries_with_book_id_returns_book_scoped_first_then_global(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.create_lexicon_entry(term="Global One", override_value="g-one", scope="global")
    db.create_lexicon_entry(term="Book One", override_value="b-one", scope="book", book_id=book.id)

    entries = db.list_lexicon_entries(book_id=book.id)
    assert [e.term for e in entries] == ["Book One", "Global One"]
    db.close()


def test_delete_lexicon_entry_removes_it(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")

    db.delete_lexicon_entry("Cadeyrn")

    assert db.find_lexicon_entry("Cadeyrn") is None
    db.close()


def test_delete_lexicon_entry_raises_keyerror_for_missing_entry(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    with pytest.raises(KeyError, match="Cadeyrn"):
        db.delete_lexicon_entry("Cadeyrn")
    db.close()


def test_create_lexicon_entry_stores_phonemes_and_sample_audio_path(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    entry = db.create_lexicon_entry(
        term="Cadeyrn", override_value="kuhdayrn", scope="global",
        source="from_audio", phonemes="k ə d eɪ ɹ n", sample_audio_path="/clips/cadeyrn.wav",
    )

    assert entry.source == "from_audio"
    assert entry.phonemes == "k ə d eɪ ɹ n"
    assert entry.sample_audio_path == "/clips/cadeyrn.wav"

    found = db.find_lexicon_entry("Cadeyrn")
    assert found.phonemes == "k ə d eɪ ɹ n"
    assert found.sample_audio_path == "/clips/cadeyrn.wav"
    db.close()


def test_create_lexicon_entry_defaults_phonemes_and_sample_audio_path_to_none(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    entry = db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")

    assert entry.phonemes is None
    assert entry.sample_audio_path is None
    db.close()


def test_existing_m2_database_gains_new_lexicon_columns_via_migration(tmp_path):
    # Simulates opening a database file created before M3 (no phonemes/sample_audio_path
    # columns) - the migration in Database.__init__ must add them without raising.
    import sqlite3

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE lexicon_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL COLLATE NOCASE,
            override_value TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            scope TEXT NOT NULL,
            book_id INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at REAL NOT NULL,
            UNIQUE(book_id, term)
        )
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)  # must not raise
    entry = db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")
    assert entry.phonemes is None
    db.close()


def test_update_lexicon_entry_override_changes_value_and_keeps_metadata(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.create_lexicon_entry(
        term="Cadeyrn", override_value="kuhdayrn", scope="global",
        source="from_audio", phonemes="k ə d eɪ ɹ n", sample_audio_path="/clips/cadeyrn.wav",
        notes="derived from audio",
    )

    updated = db.update_lexicon_entry_override("Cadeyrn", "kah-DAIRN")

    assert updated.override_value == "kah-DAIRN"
    assert updated.source == "from_audio"
    assert updated.phonemes == "k ə d eɪ ɹ n"
    assert updated.sample_audio_path == "/clips/cadeyrn.wav"
    assert updated.notes == "derived from audio"
    db.close()


def test_update_lexicon_entry_override_raises_keyerror_for_missing_entry(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    with pytest.raises(KeyError, match="Cadeyrn"):
        db.update_lexicon_entry_override("Cadeyrn", "kah-DAIRN")
    db.close()


def test_update_lexicon_entry_override_respects_book_scope(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    book = db.create_book(
        title="Test Book", author=None, source_path="/books/test.md",
        source_format=SourceFormat.MARKDOWN, output_dir=str(tmp_path / "out"),
    )
    db.create_lexicon_entry(term="Cadeyrn", override_value="KAH-dern", scope="global")
    db.create_lexicon_entry(term="Cadeyrn", override_value="kah-DAIRN", scope="book", book_id=book.id)

    db.update_lexicon_entry_override("Cadeyrn", "NEW-book-version", book_id=book.id)

    assert db.find_lexicon_entry("Cadeyrn", book_id=book.id).override_value == "NEW-book-version"
    assert db.find_lexicon_entry("Cadeyrn").override_value == "KAH-dern"  # global untouched
    db.close()

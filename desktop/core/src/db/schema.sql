
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT,
    source_path TEXT NOT NULL UNIQUE,
    source_format TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    created_at REAL NOT NULL,
    voice_profile_id INTEGER,
    engine TEXT NOT NULL DEFAULT 'chatterbox-turbo'
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id),
    order_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    output_mp3_path TEXT,
    duration REAL,
    error TEXT,
    used_fallback INTEGER NOT NULL DEFAULT 0,
    engine TEXT,
    UNIQUE(book_id, order_index)
);

CREATE TABLE IF NOT EXISTS voice_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    engine TEXT NOT NULL,
    reference_audio_path TEXT NOT NULL,
    reference_transcript TEXT,
    exaggeration REAL NOT NULL,
    cfg_weight REAL NOT NULL,
    temperature REAL NOT NULL,
    created_at REAL NOT NULL,
    transcript_status TEXT,
    transcript_error TEXT,
    reference_words TEXT,
    reference_trim_end REAL,
    tempo REAL NOT NULL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS lexicon_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL COLLATE NOCASE,
    override_value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    scope TEXT NOT NULL,
    book_id INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at REAL NOT NULL,
    phonemes TEXT,
    sample_audio_path TEXT,
    UNIQUE(book_id, term)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS worker_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    paused INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS text_clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    voice_profile_id INTEGER REFERENCES voice_profiles(id),
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL,
    output_path TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    error TEXT,
    used_fallback INTEGER NOT NULL DEFAULT 0,
    engine TEXT NOT NULL DEFAULT 'chatterbox-turbo',
    tempo REAL
);

//! Rows of the audiobookify database, mirroring `src/audiobookify/models.py`.

use rusqlite::types::{FromSql, FromSqlError, FromSqlResult, ToSql, ToSqlOutput, ValueRef};

/// The engine a new book or clip gets, matching the column defaults in schema.sql.
pub const DEFAULT_ENGINE: &str = "chatterbox-turbo";

/// A status/format enum that is stored as its lowercase text.
macro_rules! text_enum {
    ($name:ident { $($variant:ident => $text:literal),+ $(,)? }) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum $name { $($variant),+ }

        impl $name {
            pub fn as_str(self) -> &'static str {
                match self { $(Self::$variant => $text),+ }
            }
        }

        impl ToSql for $name {
            fn to_sql(&self) -> rusqlite::Result<ToSqlOutput<'_>> {
                Ok(self.as_str().into())
            }
        }

        impl FromSql for $name {
            fn column_result(value: ValueRef<'_>) -> FromSqlResult<Self> {
                match value.as_str()? {
                    $($text => Ok(Self::$variant),)+
                    other => Err(FromSqlError::Other(
                        format!("unknown {} {other:?}", stringify!($name)).into(),
                    )),
                }
            }
        }
    };
}

text_enum!(SourceFormat { Epub => "epub", Markdown => "md" });
text_enum!(ChapterStatus { Pending => "pending", Generating => "generating", Done => "done", Failed => "failed" });
text_enum!(JobStatus {
    Queued => "queued", Generating => "generating", Done => "done", Failed => "failed", Cancelled => "cancelled",
});

#[derive(Debug, Clone, PartialEq)]
pub struct RawChapter {
    pub title: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Book {
    pub id: i64,
    pub title: String,
    pub author: Option<String>,
    pub source_path: String,
    pub source_format: SourceFormat,
    pub output_dir: String,
    pub created_at: f64,
    pub voice_profile_id: Option<i64>,
    /// Used for every (re)generated chapter.
    pub engine: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Chapter {
    pub id: i64,
    pub book_id: i64,
    pub order_index: i64,
    pub title: String,
    pub text: String,
    pub status: ChapterStatus,
    pub output_mp3_path: Option<String>,
    pub duration: Option<f64>,
    pub error: Option<String>,
    pub used_fallback: bool,
    /// What produced the audio; None until generated.
    pub engine: Option<String>,
}

/// The optional fields of `Database::update_chapter_status`.
#[derive(Debug, Clone, Copy, Default)]
pub struct ChapterOutcome<'a> {
    pub output_mp3_path: Option<&'a str>,
    pub duration: Option<f64>,
    pub error: Option<&'a str>,
    pub used_fallback: bool,
    pub engine: Option<&'a str>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct VoiceProfile {
    pub id: i64,
    pub name: String,
    /// Legacy: voices work with any engine; the book or clip picks one.
    pub engine: String,
    pub reference_audio_path: String,
    pub reference_transcript: Option<String>,
    pub exaggeration: f64,
    pub cfg_weight: f64,
    pub temperature: f64,
    pub created_at: f64,
    /// None (never asked), "pending", "done", "failed".
    pub transcript_status: Option<String>,
    pub transcript_error: Option<String>,
    /// Whisper word timings of the whole clip.
    pub reference_words: Option<serde_json::Value>,
    /// Engines clone from the first N seconds; None = whole clip.
    pub reference_trim_end: Option<f64>,
    /// The reference is played at this speed (atempo) before cloning.
    pub tempo: f64,
}

#[derive(Debug, Clone, Copy)]
pub struct NewVoiceProfile<'a> {
    pub name: &'a str,
    pub engine: &'a str,
    pub reference_audio_path: &'a str,
    pub reference_transcript: Option<&'a str>,
    pub exaggeration: f64,
    pub cfg_weight: f64,
    pub temperature: f64,
    pub transcript_status: Option<&'a str>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LexiconEntry {
    pub id: i64,
    pub term: String,
    pub override_value: String,
    pub source: String,
    pub scope: String,
    /// 0 = global.
    pub book_id: i64,
    pub notes: Option<String>,
    pub created_at: f64,
    pub phonemes: Option<String>,
    pub sample_audio_path: Option<String>,
}

#[derive(Debug, Clone, Copy)]
pub struct NewLexiconEntry<'a> {
    pub term: &'a str,
    pub override_value: &'a str,
    pub scope: &'a str,
    pub book_id: i64,
    pub notes: Option<&'a str>,
    pub source: &'a str,
    pub phonemes: Option<&'a str>,
    pub sample_audio_path: Option<&'a str>,
}

impl<'a> NewLexiconEntry<'a> {
    /// A global, manual entry; set the other fields directly.
    pub fn new(term: &'a str, override_value: &'a str, scope: &'a str) -> Self {
        Self {
            term, override_value, scope, book_id: 0, notes: None, source: "manual",
            phonemes: None, sample_audio_path: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Job {
    pub id: i64,
    pub chapter_id: i64,
    pub status: JobStatus,
    pub progress: Option<f64>,
    pub created_at: f64,
    pub started_at: Option<f64>,
    pub finished_at: Option<f64>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TextClip {
    pub id: i64,
    pub text: String,
    pub voice_profile_id: Option<i64>,
    pub status: JobStatus,
    pub progress: Option<f64>,
    pub output_path: Option<String>,
    pub created_at: f64,
    pub started_at: Option<f64>,
    pub finished_at: Option<f64>,
    pub error: Option<String>,
    pub used_fallback: bool,
    pub engine: String,
    /// A sample at an unsaved tempo; None = the voice's tempo.
    pub tempo: Option<f64>,
}

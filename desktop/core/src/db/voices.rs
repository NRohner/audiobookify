use rusqlite::types::Type;
use rusqlite::{params, Row, ToSql};

use super::{is_constraint, now, Database, DbError, Result};
use crate::models::{NewVoiceProfile, VoiceProfile};

impl Database {
    pub fn create_voice_profile(&self, v: &NewVoiceProfile<'_>) -> Result<VoiceProfile> {
        let inserted = self.conn.execute(
            "INSERT INTO voice_profiles (name, engine, reference_audio_path, reference_transcript, \
             exaggeration, cfg_weight, temperature, created_at, transcript_status) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![v.name, v.engine, v.reference_audio_path, v.reference_transcript, v.exaggeration,
                    v.cfg_weight, v.temperature, now(), v.transcript_status],
        );
        match inserted {
            Err(e) if is_constraint(&e) => {
                Err(DbError::Invalid(format!("a voice profile named '{}' already exists", v.name)))
            }
            Err(e) => Err(e.into()),
            Ok(_) => Ok(self.conn.query_row("SELECT * FROM voice_profiles WHERE id = ?1",
                                            [self.conn.last_insert_rowid()], voice_from_row)?),
        }
    }

    pub fn find_voice_profile_by_name(&self, name: &str) -> Result<Option<VoiceProfile>> {
        self.query_opt("SELECT * FROM voice_profiles WHERE name = ?1", [name], voice_from_row)
    }

    pub fn list_voice_profiles(&self) -> Result<Vec<VoiceProfile>> {
        self.query_all("SELECT * FROM voice_profiles ORDER BY id", [], voice_from_row)
    }

    pub fn get_voice_profile_by_id(&self, id: i64) -> Result<Option<VoiceProfile>> {
        self.query_opt("SELECT * FROM voice_profiles WHERE id = ?1", [id], voice_from_row)
    }

    pub fn update_voice_profile_reference_audio_path(&self, id: i64, reference_audio_path: &str) -> Result<()> {
        self.update_voice(id, "reference_audio_path = ?", &[&reference_audio_path])
    }

    pub fn next_pending_transcription(&self) -> Result<Option<VoiceProfile>> {
        self.query_opt(
            "SELECT * FROM voice_profiles WHERE transcript_status = 'pending' ORDER BY id LIMIT 1",
            [], voice_from_row,
        )
    }

    pub fn request_voice_transcription(&self, id: i64) -> Result<()> {
        self.update_voice(id, "transcript_status = 'pending', transcript_error = NULL", &[])
    }

    pub fn fail_voice_transcription(&self, id: i64, error: &str) -> Result<()> {
        self.update_voice(id, "transcript_status = 'failed', transcript_error = ?", &[&error])
    }

    /// A Whisper result (words given: replaces timings and trim) or a user edit
    /// of the text alone (keeps them).
    pub fn save_voice_transcript(&self, id: i64, text: &str, words: Option<&serde_json::Value>,
                                 trim_end: Option<f64>) -> Result<()> {
        match words {
            None => self.update_voice(
                id, "reference_transcript = ?, transcript_status = 'done', transcript_error = NULL", &[&text],
            ),
            Some(words) => {
                let words = serde_json::to_string(words)?;
                self.update_voice(
                    id,
                    "reference_transcript = ?, transcript_status = 'done', transcript_error = NULL, \
                     reference_words = ?, reference_trim_end = ?",
                    &[&text, &words, &trim_end],
                )
            }
        }
    }

    pub fn set_voice_tempo(&self, id: i64, tempo: f64) -> Result<()> {
        self.update_voice(id, "tempo = ?", &[&tempo])
    }

    pub fn delete_voice_profile(&self, id: i64) -> Result<()> {
        if self.conn.execute("DELETE FROM voice_profiles WHERE id = ?1", [id])? == 0 {
            return Err(DbError::NotFound(format!("no voice profile with id {id}")));
        }
        Ok(())
    }

    /// `assignments` uses plain `?` placeholders; the id is bound last.
    fn update_voice(&self, id: i64, assignments: &str, values: &[&dyn ToSql]) -> Result<()> {
        let mut bound = values.to_vec();
        bound.push(&id);
        let sql = format!("UPDATE voice_profiles SET {assignments} WHERE id = ?");
        if self.conn.execute(&sql, bound.as_slice())? == 0 {
            return Err(DbError::NotFound(format!("no voice profile with id {id}")));
        }
        Ok(())
    }
}

fn voice_from_row(r: &Row<'_>) -> rusqlite::Result<VoiceProfile> {
    Ok(VoiceProfile {
        id: r.get("id")?, name: r.get("name")?, engine: r.get("engine")?,
        reference_audio_path: r.get("reference_audio_path")?,
        reference_transcript: r.get("reference_transcript")?,
        exaggeration: r.get("exaggeration")?, cfg_weight: r.get("cfg_weight")?,
        temperature: r.get("temperature")?, created_at: r.get("created_at")?,
        transcript_status: r.get("transcript_status")?, transcript_error: r.get("transcript_error")?,
        reference_words: json_column(r, "reference_words")?,
        reference_trim_end: r.get("reference_trim_end")?, tempo: r.get("tempo")?,
    })
}

/// A JSON text column; NULL or empty is None, as in db.py.
fn json_column(r: &Row<'_>, name: &str) -> rusqlite::Result<Option<serde_json::Value>> {
    let text: Option<String> = r.get(name)?;
    match text.as_deref() {
        None | Some("") => Ok(None),
        Some(text) => serde_json::from_str(text).map(Some).map_err(|e| {
            let index = r.as_ref().column_index(name).unwrap_or(0);
            rusqlite::Error::FromSqlConversionFailure(index, Type::Text, Box::new(e))
        }),
    }
}

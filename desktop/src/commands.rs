//! The Tauri commands: lock the DB, call `audiobookify_core::app`, and let the webview play
//! the audio files they return. The logic and its tests live in the core crate.

use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};

use audiobookify_core::app::books::{self, BookDto, BookText, ChapterBreak, DirCheck};
use audiobookify_core::app::clips::{self, TextClipDto};
use audiobookify_core::app::jobs::{self, Queued, QueuedOne, WorkerStatus};
use audiobookify_core::app::lexicon::{self, LexiconDto};
use audiobookify_core::app::models::{self, Deleted, ModelProgress, ModelRow};
use audiobookify_core::app::stats::{self, Stats};
use audiobookify_core::app::voices::{self, VoiceDto};
use audiobookify_core::app::{self, ApiError, ApiResult, AppPaths, Engines};
use audiobookify_core::db::Database;
use audiobookify_core::hub::store::Hub;
use audiobookify_core::jobs::RunnerControl;
use tauri::{AppHandle, Manager, State};

/// The commands' connection; the job runner has its own. WAL mode and the busy timeout let
/// them share the file, as FastAPI and the Python worker did.
// ponytail: one connection behind one Mutex; an import holds it for a second or two and the
// 2 s polls wait. A small pool if that ever shows.
pub struct AppState {
    pub db: Mutex<Database>,
    pub paths: AppPaths,
    pub hub: Hub,
    pub runner: RunnerControl,
}

impl AppState {
    fn db(&self) -> MutexGuard<'_, Database> {
        self.db.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

/// Lets the webview load `path` over the asset protocol, then returns it for `convertFileSrc`.
fn playable(app: &AppHandle, path: PathBuf) -> ApiResult<PathBuf> {
    app.asset_protocol_scope().allow_file(&path).map_err(|e| ApiError(e.to_string()))?;
    Ok(path)
}

#[tauri::command]
pub fn list_engines(state: State<'_, AppState>) -> Engines {
    app::list_engines(state.hub.cache())
}

#[tauri::command]
pub fn validate_output_dir(path: String) -> DirCheck {
    books::validate_output_dir(&path)
}

#[tauri::command(async)]
pub fn list_books(state: State<'_, AppState>) -> ApiResult<Vec<BookDto>> {
    books::list_books(&state.db())
}

#[tauri::command(async)]
pub fn get_book(state: State<'_, AppState>, book_id: i64) -> ApiResult<BookDto> {
    books::get_book(&state.db(), book_id)
}

#[tauri::command(async)]
pub fn create_book(state: State<'_, AppState>, path: PathBuf, output_dir: String) -> ApiResult<BookDto> {
    books::create_book(&state.db(), &state.paths, &path, &output_dir)
}

#[tauri::command(async)]
pub fn delete_book(state: State<'_, AppState>, book_id: i64) -> ApiResult<()> {
    books::delete_book(&state.db(), &state.paths, book_id)
}

#[tauri::command(async)]
pub fn get_book_text(state: State<'_, AppState>, book_id: i64) -> ApiResult<BookText> {
    books::book_text(&state.db(), book_id)
}

#[tauri::command(async)]
pub fn save_chapter_breaks(state: State<'_, AppState>, book_id: i64, chapters: Vec<ChapterBreak>) -> ApiResult<BookDto> {
    books::save_chapter_breaks(&mut state.db(), &state.paths, book_id, &chapters)
}

#[tauri::command(async)]
pub fn assign_voice(state: State<'_, AppState>, book_id: i64, voice_profile_id: Option<i64>) -> ApiResult<()> {
    books::assign_voice(&state.db(), book_id, voice_profile_id)
}

#[tauri::command(async)]
pub fn generate_book(state: State<'_, AppState>, book_id: i64, engine: Option<String>) -> ApiResult<Queued> {
    jobs::generate_book(&state.db(), state.hub.cache(), book_id, engine.as_deref())
}

#[tauri::command(async)]
pub fn generate_chapter(state: State<'_, AppState>, book_id: i64, chapter_id: i64, engine: Option<String>)
                        -> ApiResult<QueuedOne> {
    jobs::generate_chapter(&state.db(), state.hub.cache(), book_id, chapter_id, engine.as_deref())
}

#[tauri::command(async)]
pub fn cancel_chapter(state: State<'_, AppState>, book_id: i64, chapter_id: i64) -> ApiResult<()> {
    jobs::cancel_chapter(&state.db(), &state.paths, book_id, chapter_id)
}

#[tauri::command(async)]
pub fn cancel_book(state: State<'_, AppState>, book_id: i64) -> ApiResult<()> {
    jobs::cancel_book(&state.db(), &state.paths, book_id)
}

#[tauri::command(async)]
pub fn worker_status(state: State<'_, AppState>) -> ApiResult<WorkerStatus> {
    jobs::worker_status(&state.db())
}

#[tauri::command(async)]
pub fn pause_worker(state: State<'_, AppState>) -> ApiResult<WorkerStatus> {
    jobs::set_paused(&state.db(), true)
}

#[tauri::command(async)]
pub fn resume_worker(state: State<'_, AppState>) -> ApiResult<WorkerStatus> {
    jobs::set_paused(&state.db(), false)
}

#[tauri::command(async)]
pub fn chapter_audio_path(app: AppHandle, state: State<'_, AppState>, book_id: i64, chapter_id: i64)
                          -> ApiResult<PathBuf> {
    let path = jobs::chapter_audio_path(&state.db(), book_id, chapter_id)?;
    playable(&app, path)
}

#[tauri::command(async)]
pub fn list_voices(state: State<'_, AppState>) -> ApiResult<Vec<VoiceDto>> {
    voices::list_voices(&state.db())
}

#[tauri::command(async)]
pub fn create_voice(state: State<'_, AppState>, path: PathBuf, name: String, reference_transcript: Option<String>)
                    -> ApiResult<VoiceDto> {
    voices::create_voice(&state.db(), &state.paths, &path, &name, reference_transcript.as_deref())
}

#[tauri::command(async)]
pub fn set_voice_transcript(state: State<'_, AppState>, voice_id: i64, transcript: String) -> ApiResult<VoiceDto> {
    voices::set_voice_transcript(&state.db(), voice_id, &transcript)
}

#[tauri::command(async)]
pub fn set_voice_tempo(state: State<'_, AppState>, voice_id: i64, tempo: f64) -> ApiResult<VoiceDto> {
    voices::set_voice_tempo(&state.db(), voice_id, tempo)
}

#[tauri::command(async)]
pub fn transcribe_voice(state: State<'_, AppState>, voice_id: i64) -> ApiResult<VoiceDto> {
    voices::transcribe_voice(&state.db(), state.hub.cache(), voice_id)
}

#[tauri::command(async)]
pub fn delete_voice(state: State<'_, AppState>, voice_id: i64) -> ApiResult<()> {
    voices::delete_voice(&state.db(), &state.paths, voice_id)
}

#[tauri::command(async)]
pub fn voice_audio_path(app: AppHandle, state: State<'_, AppState>, voice_id: i64, tempo: Option<f64>)
                        -> ApiResult<PathBuf> {
    let path = voices::voice_audio_path(&state.db(), voice_id, tempo)?;
    playable(&app, path)
}

#[tauri::command(async)]
pub fn list_pronunciations(state: State<'_, AppState>) -> ApiResult<Vec<LexiconDto>> {
    lexicon::list_pronunciations(&state.db())
}

#[tauri::command(async)]
pub fn create_pronunciation(state: State<'_, AppState>, term: String, override_value: String, notes: Option<String>)
                            -> ApiResult<LexiconDto> {
    lexicon::create_pronunciation(&state.db(), &term, &override_value, notes.as_deref())
}

#[tauri::command(async)]
pub fn update_pronunciation(state: State<'_, AppState>, term: String, override_value: String) -> ApiResult<LexiconDto> {
    lexicon::update_pronunciation(&state.db(), &term, &override_value)
}

#[tauri::command(async)]
pub fn delete_pronunciation(state: State<'_, AppState>, term: String) -> ApiResult<()> {
    lexicon::delete_pronunciation(&state.db(), &term)
}

#[tauri::command(async)]
pub fn list_text_clips(state: State<'_, AppState>) -> ApiResult<Vec<TextClipDto>> {
    clips::list_text_clips(&state.db())
}

#[tauri::command(async)]
pub fn create_text_clip(state: State<'_, AppState>, text: String, voice_profile_id: Option<i64>,
                        engine: Option<String>, tempo: Option<f64>) -> ApiResult<TextClipDto> {
    clips::create_text_clip(&state.db(), state.hub.cache(), &text, voice_profile_id, engine.as_deref(), tempo)
}

#[tauri::command(async)]
pub fn get_text_clip(state: State<'_, AppState>, clip_id: i64) -> ApiResult<TextClipDto> {
    clips::get_text_clip(&state.db(), clip_id)
}

#[tauri::command(async)]
pub fn delete_text_clip(state: State<'_, AppState>, clip_id: i64) -> ApiResult<()> {
    clips::delete_text_clip(&state.db(), clip_id)
}

#[tauri::command(async)]
pub fn text_clip_audio_path(app: AppHandle, state: State<'_, AppState>, clip_id: i64) -> ApiResult<PathBuf> {
    let path = clips::text_clip_audio_path(&state.db(), clip_id)?;
    playable(&app, path)
}

#[tauri::command(async)]
pub fn save_text_clip(state: State<'_, AppState>, clip_id: i64, dest: PathBuf) -> ApiResult<()> {
    clips::save_text_clip(&state.db(), clip_id, &dest)
}

#[tauri::command(async)]
pub fn list_models(state: State<'_, AppState>) -> ApiResult<Vec<ModelRow>> {
    models::list_models(&state.hub)
}

#[tauri::command]
pub fn model_progress(state: State<'_, AppState>) -> Vec<ModelProgress> {
    models::model_progress(&state.hub)
}

#[tauri::command]
pub fn download_model(state: State<'_, AppState>, model: String) -> ApiResult<()> {
    models::download_model(&state.hub, &model)
}

#[tauri::command]
pub fn cancel_model_download(state: State<'_, AppState>, model: String) {
    models::cancel_model_download(&state.hub, &model)
}

/// Doesn't hold the DB lock while it waits for the runner or runs Python.
#[tauri::command(async)]
pub fn delete_model(state: State<'_, AppState>, model: String) -> ApiResult<Deleted> {
    let work_db = state.paths.open_db()?;
    models::delete_model(&work_db, &state.hub, &state.runner, &model)
}

#[tauri::command(async)]
pub fn get_stats(state: State<'_, AppState>) -> ApiResult<Stats> {
    stats::stats(&state.db())
}

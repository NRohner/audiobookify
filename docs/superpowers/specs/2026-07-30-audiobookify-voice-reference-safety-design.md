# Audiobookify — Voice Reference Audio Safety & Organization: Design

**Status:** Approved by user (2026-07-30), ready for implementation planning.

## Goal

Voice profiles depend permanently on a reference audio file that today lives only in the
transient upload-staging area (`uploads/<uuid>/<filename>`), with no folder-per-voice
organization. Nothing distinguishes "a file safe to keep forever because a voice profile
depends on it" from "a file safe to delete because it was already ingested/processed" —
so a reasonable cleanup of `uploads/` can silently break voice cloning for any profile
whose source file gets removed. This sub-project gives cloned voices a permanent,
organized home and removes that latent data-loss trap.

This is one of 3 sub-projects from this brainstorming session (see also: output path
safety, generation control/pause-cancel-resume — each gets its own spec/plan/build
cycle).

## Background

- `POST /api/voices` (`src/audiobookify/api/app.py`) saves the uploaded reference audio
  to `uploads/<uuid>/<filename>`, validates it (`validate_reference_audio`), and stores
  that staging path directly as `VoiceProfile.reference_audio_path` — the profile's only
  reference to its source audio is a path inside a directory meant for transient staging.
- `worker.py`'s `_ensure_profile_cached` reads `reference_audio_path` every time the
  in-process profile cache misses (e.g. after a worker restart, or when a different
  book's voice is used) — so this isn't a one-time read at creation, it's a standing
  dependency for the lifetime of the profile.
- Confirmed while inspecting local dev data: one existing profile ("Jim Dale") has a
  `reference_audio_path` recorded from a different machine/environment than the current
  one, though the file happens to still exist locally — illustrating that stored paths
  can already drift from reality, which the migration below needs to tolerate rather
  than assume.

## Decisions (confirmed with the user during brainstorming)

- **Copy, don't just reference:** the validated upload is copied into a permanent,
  app-owned location immediately after the profile is created, and the transient
  staging copy is deleted — there is never a lingering copy in `uploads/` for a voice
  profile to depend on.
- **One folder per voice** under a new project-root `voices/` directory — satisfies both
  "keep cloned voices organized" and "give the reference clip a secure home" with a
  single structure.
- **Existing profiles get migrated too**, not just new ones — otherwise this fix only
  protects voices created after today, leaving already-fragile profiles (like the local
  "Jim Dale" one) exactly as exposed as before.

## Architecture

```
POST /api/voices
  │ 1. save upload → uploads/<uuid>/<filename>  (staging, as today)
  │ 2. validate_reference_audio(staged_path)
  │ 3. db.create_voice_profile(...)              → profile gets an id
  │ 4. copy staged file → voices/<id>_<name>/reference<ext>
  │ 5. db: update reference_audio_path to the permanent path
  │ 6. delete uploads/<uuid>/ (staging no longer needed)
  ▼
VoiceProfile.reference_audio_path always points under voices/, never under uploads/
```

## Directory layout

- `voices/<profile-id>_<sanitized-name>/reference<ext>` — `<ext>` preserves the original
  upload's suffix (`.mp3`, `.wav`, etc.), `<sanitized-name>` reuses the existing
  `sanitize_filename` helper from `postprocess/audio.py` (already used for chapter
  filenames, no new sanitization logic needed). The id prefix guarantees uniqueness even
  if a profile is deleted and a new one created with the same name later.
- Deliberately leaves room for future per-voice artifacts (e.g. sample clips from the
  TextPage voice-testing feature) to live in the same folder later — not building that
  now, just not boxing it out structurally.

## Backend changes

- `create_voice` (`app.py`): reordered as above — profile creation now happens *before*
  the permanent copy, since the folder name needs the assigned id. `Database` gains
  `update_voice_profile_reference_audio_path(profile_id, path) -> None` (same shape as
  the existing `update_book_voice_profile`).
- `delete_voice`: after `db.delete_voice_profile(voice_id)`, also removes that profile's
  entire `voices/<id>_<name>/` folder — mirrors the existing text-clip
  delete-row-then-delete-file pattern already in `delete_text_clip`.
- New helper `src/audiobookify/voice/storage.py` (or added to `voice/validation.py` if
  small enough — implementer's call): `permanent_voice_dir(profile_id, name) -> Path`,
  `store_reference_audio(staged_path, profile_id, name) -> Path` (copy + return new
  path), `delete_voice_storage(profile_id, name) -> None`.

## One-time migration for existing profiles

- Runs at `Database.__init__` alongside the existing `_migrate_lexicon_entries_columns`
  call (same additive-migration convention, not a separate command the user has to
  remember to run).
- For every `VoiceProfile` whose `reference_audio_path` doesn't already start with the
  resolved `voices/` directory: attempt the same copy-and-repoint used for new profiles.
- If the recorded source file doesn't exist (the stale-path case observed in local dev
  data), skip that profile with a logged warning rather than raising — the migration
  can't recover a file that's already gone, and one missing source file must not block
  every other profile from migrating or crash app startup.

## Data model changes

- No new columns — `reference_audio_path` is reused as-is, just guaranteed to always
  point under `voices/` going forward (for both new and migrated profiles).

## Testing strategy

- `store_reference_audio`/`permanent_voice_dir`/`delete_voice_storage`: unit tests
  against `tmp_path` — copy creates the expected folder/filename, delete removes the
  whole folder, folder naming is stable and collision-free across two profiles with the
  same name but different ids.
- `create_voice` (API): extend existing tests to assert the DB row's
  `reference_audio_path` ends up under `voices/` (not `uploads/`) after the request, and
  that the `uploads/<uuid>/` staging directory no longer exists afterward.
- `delete_voice` (API): assert the profile's `voices/<id>_<name>/` folder is gone after
  deletion.
- Migration: unit test with a hand-seeded `tmp_path` database containing a profile
  pointing at a real file outside `voices/` (asserts it gets migrated) and one pointing
  at a nonexistent path (asserts it's skipped without raising, and other profiles still
  migrate normally).

## Alternatives considered

**Leave `reference_audio_path` pointing at `uploads/`, just stop deleting those
folders.** Rejected — doesn't address the "keep cloned voices organized in folders" half
of the ask, and leaves the reference audio mixed in with book-import staging data
indefinitely, which is exactly the ambiguity ("safe to delete or not?") causing the
original concern.

**Symlink into `voices/` instead of copying.** Rejected — doesn't protect against the
`uploads/` source file being deleted (the actual risk being addressed), and adds
cross-platform symlink-permission complexity for no benefit over a plain copy of what
are small audio reference clips.

## Out of scope for this sub-project

Per-voice generated sample/test clips (from the TextPage voice-testing feature) living
in the same folder — structurally possible later, not built now; output path safety and
generation control (pause/cancel/resume) — separate specs from this session.

## Open questions

None outstanding — all decisions in this document were confirmed with the user during
brainstorming on 2026-07-30.

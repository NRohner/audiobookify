import shutil
from pathlib import Path

from ..postprocess.audio import sanitize_filename

VOICES_DIR = str(Path(__file__).resolve().parents[3] / "voices")


def permanent_voice_dir(profile_id: int, name: str) -> Path:
    return Path(VOICES_DIR) / f"{profile_id}_{sanitize_filename(name)}"


def store_reference_audio(staged_path: str, profile_id: int, name: str) -> Path:
    dest_dir = permanent_voice_dir(profile_id, name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(staged_path).suffix
    dest_path = dest_dir / f"reference{suffix}"
    shutil.copy2(staged_path, dest_path)
    return dest_path


def delete_voice_storage(profile_id: int, name: str) -> None:
    shutil.rmtree(permanent_voice_dir(profile_id, name), ignore_errors=True)

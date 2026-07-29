import shutil

import numpy as np
import pytest
import soundfile as sf

from audiobookify import cli
from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@requires_ffmpeg
def test_generate_command_produces_mp3s_per_chapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nFirst chapter body.\n\n# Chapter Two\nSecond chapter body.\n",
                        encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "done:" in captured.out
    assert (out_dir / "01 - Chapter One.mp3").exists()
    assert (out_dir / "02 - Chapter Two.mp3").exists()


@requires_ffmpeg
def test_generate_command_is_resumable_across_invocations(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    first_mtime = (out_dir / "01 - Chapter One.mp3").stat().st_mtime

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    second_mtime = (out_dir / "01 - Chapter One.mp3").stat().st_mtime

    assert first_mtime == second_mtime  # already-DONE chapter was not regenerated


@requires_ffmpeg
def test_generate_command_exits_nonzero_and_reports_failed_chapters(tmp_path, monkeypatch, capsys):
    class FailingEngine(FakeEngine):
        def synthesize(self, text_chunk, profile_handle, params):
            if "explodes" in text_chunk.lower():
                raise RuntimeError("synthesis blew up")
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "ChatterboxEngine", FailingEngine)

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nFine body.\n\n# Chapter Two\nThis one explodes.\n",
                        encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "done: Chapter One" in captured.out  # successful chapters still reported
    assert "Chapter Two" in captured.err
    assert "synthesis blew up" in captured.err


@requires_ffmpeg
def test_generate_command_resumes_when_the_input_path_is_spelled_differently(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    monkeypatch.chdir(tmp_path)
    cli.main(["generate", "--input", "./book.md", "--out", str(out_dir), "--db", str(db_path)])
    first_mtime = (out_dir / "01 - Chapter One.mp3").stat().st_mtime

    # same file, absolute spelling: must resolve to the same book, not a second one
    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])

    assert (out_dir / "01 - Chapter One.mp3").stat().st_mtime == first_mtime
    db = Database(str(db_path))
    assert db.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    db.close()


@requires_ffmpeg
def test_generate_command_warns_when_out_dir_differs_on_resume(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    original_out = tmp_path / "out-a"
    new_out = tmp_path / "out-b"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(original_out), "--db", str(db_path)])
    capsys.readouterr()

    cli.main(["generate", "--input", str(md_path), "--out", str(new_out), "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert str(original_out) in captured.err
    assert not new_out.exists()  # the stored output_dir wins; nothing is orphaned


def test_generate_command_rejects_unsupported_format(tmp_path, capsys):
    txt_path = tmp_path / "book.txt"
    txt_path.write_text("This is a text file.", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["generate", "--input", str(txt_path), "--out", str(out_dir), "--db", str(db_path)])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "unsupported input format" in captured.err
    assert ".txt" in captured.err


def _write_reference_wav(path, duration_s=15.0, sample_rate=22050):
    n_total = int(duration_s * sample_rate)
    n_silence = int(n_total * 0.1)
    n_tone = n_total - n_silence
    t = np.linspace(0, n_tone / sample_rate, n_tone, endpoint=False)
    tone = (0.6 * np.sin(2 * np.pi * 220 * t)).astype("float32")
    silence = np.zeros(n_silence, dtype="float32")
    sf.write(str(path), np.concatenate([tone, silence]), sample_rate)


def test_voice_add_creates_profile_and_reports_reference_info(tmp_path, capsys):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Created voice profile 'narrator-1'" in captured.out

    db = Database(str(db_path))
    profile = db.find_voice_profile_by_name("narrator-1")
    assert profile is not None
    assert profile.reference_audio_path == str(ref_path.resolve())
    assert profile.engine == "chatterbox"
    db.close()


def test_voice_add_rejects_too_short_reference(tmp_path, capsys):
    ref_path = tmp_path / "tiny.wav"
    sf.write(str(ref_path), np.zeros(1000, dtype="float32"), 22050)
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["voice", "add", "--name", "tiny", "--reference", str(ref_path), "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "too short" in capsys.readouterr().err


def test_voice_add_rejects_duplicate_name(tmp_path, capsys):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "already exists" in capsys.readouterr().err


def test_voice_list_shows_added_profiles(tmp_path, capsys):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    capsys.readouterr()

    cli.main(["voice", "list", "--db", str(db_path)])

    assert "narrator-1" in capsys.readouterr().out


def test_voice_list_reports_when_empty(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    cli.main(["voice", "list", "--db", str(db_path)])

    assert "No voice profiles yet" in capsys.readouterr().out


def test_voice_preview_writes_audio_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"
    out_path = tmp_path / "preview.wav"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    cli.main([
        "voice", "preview", "--name", "narrator-1", "--text", "Hello there, listener.",
        "--out", str(out_path), "--db", str(db_path),
    ])

    assert out_path.exists()
    data, _sr = sf.read(str(out_path))
    assert len(data) > 0


def test_voice_preview_defaults_output_filename_to_profile_name(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatterboxEngine", FakeEngine)
    monkeypatch.chdir(tmp_path)

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    cli.main(["voice", "preview", "--name", "narrator-1", "--text", "Hi.", "--db", str(db_path)])

    assert (tmp_path / "narrator-1-preview.wav").exists()


def test_voice_preview_reports_missing_profile(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["voice", "preview", "--name", "ghost", "--text", "hi", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "no voice profile named 'ghost'" in capsys.readouterr().err


def test_voice_preview_tears_down_engine_even_if_load_fails(tmp_path, monkeypatch):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    class FailingLoadEngine(FakeEngine):
        teardown_called = False

        def load(self, device):
            raise RuntimeError("device unavailable")

        def teardown(self):
            type(self).teardown_called = True

    monkeypatch.setattr(cli, "ChatterboxEngine", FailingLoadEngine)

    with pytest.raises(RuntimeError, match="device unavailable"):
        cli.main(["voice", "preview", "--name", "narrator-1", "--text", "Hi.", "--db", str(db_path)])

    assert FailingLoadEngine.teardown_called is True

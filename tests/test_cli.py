import shutil

import pytest

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

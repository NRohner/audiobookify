import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from audiobookify import cli
from audiobookify.db import Database
from audiobookify.engines.fake import FakeEngine

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@requires_ffmpeg
def test_generate_command_produces_mp3s_per_chapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

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
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

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

    monkeypatch.setattr(cli, "_make_engine", lambda name: FailingEngine())

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
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

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
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

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
    # The migration copies the reference audio into VOICES_DIR on Database.__init__, so
    # the path is now VOICES_DIR/{id}_{name}/reference.{suffix}
    assert profile.reference_audio_path.endswith(f"{profile.id}_narrator-1/reference.wav")
    assert Path(profile.reference_audio_path).is_file()
    assert profile.engine == "chatterbox-turbo"
    db.close()


def test_voice_add_reference_audio_survives_deletion_of_the_original_file(tmp_path):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main([
        "voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path),
    ])

    ref_path.unlink()  # simulate the user cleaning up their original download

    db = Database(str(db_path))
    profile = db.find_voice_profile_by_name("narrator-1")
    assert Path(profile.reference_audio_path).is_file()
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
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

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
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())
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

        def load(self, device="auto"):
            raise RuntimeError("device unavailable")

        def teardown(self):
            type(self).teardown_called = True

    monkeypatch.setattr(cli, "_make_engine", lambda name: FailingLoadEngine())

    with pytest.raises(RuntimeError, match="device unavailable"):
        cli.main(["voice", "preview", "--name", "narrator-1", "--text", "Hi.", "--db", str(db_path)])

    assert FailingLoadEngine.teardown_called is True


@requires_ffmpeg
def test_generate_command_uses_voice_profile_reference_and_params(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main([
        "voice", "add", "--name", "narrator-1", "--reference", str(ref_path),
        "--exaggeration", "0.9", "--db", str(db_path),
    ])

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    cli.main([
        "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
        "--voice-profile", "narrator-1",
    ])

    from mutagen.mp3 import MP3
    tags = MP3(str(out_dir / "01 - Chapter One.mp3"))
    assert str(tags.get("TPE1")) == "narrator-1"  # narrator tag defaults to the profile name


@requires_ffmpeg
def test_generate_command_passes_voice_profile_reference_and_params_to_engine(tmp_path, monkeypatch):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main([
        "voice", "add", "--name", "narrator-1", "--reference", str(ref_path),
        "--transcript", "Hello there.", "--exaggeration", "0.9", "--cfg", "0.2",
        "--temperature", "0.95", "--db", str(db_path),
    ])

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    class RecordingEngine(FakeEngine):
        clone_calls = []
        synthesize_params = []

        def clone_profile(self, reference_audio, reference_text, params):
            type(self).clone_calls.append((reference_audio, reference_text))
            return super().clone_profile(reference_audio, reference_text, params)

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesize_params.append(params)
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "_make_engine", lambda name: RecordingEngine())

    cli.main([
        "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
        "--voice-profile", "narrator-1",
    ])

    # The migration copies the reference audio into VOICES_DIR on Database.__init__, so
    # the path is now VOICES_DIR/{id}_{name}/reference.{suffix}
    clone_call_ref_audio, clone_call_ref_text = RecordingEngine.clone_calls[0]
    assert clone_call_ref_text == "Hello there."
    assert clone_call_ref_audio.endswith("_narrator-1/reference.wav")
    assert Path(clone_call_ref_audio).is_file()

    synth_params = RecordingEngine.synthesize_params[0]
    assert synth_params["voice_name"] == "narrator-1"
    assert synth_params["reference_audio"].endswith("_narrator-1/reference.wav")
    assert Path(synth_params["reference_audio"]).is_file()
    assert synth_params["reference_text"] == "Hello there."
    assert synth_params["exaggeration"] == 0.9
    assert synth_params["cfg"] == 0.2
    assert synth_params["temperature"] == 0.95


@requires_ffmpeg
def test_generate_command_voice_flag_overrides_profile_name_as_narrator_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    cli.main([
        "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
        "--voice-profile", "narrator-1", "--voice", "Custom Narrator Name",
    ])

    from mutagen.mp3 import MP3
    tags = MP3(str(out_dir / "01 - Chapter One.mp3"))
    assert str(tags.get("TPE1")) == "Custom Narrator Name"


@requires_ffmpeg
def test_generate_command_reports_missing_voice_profile(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
            "--voice-profile", "ghost",
        ])

    assert exc_info.value.code == 1
    assert "no voice profile named 'ghost'" in capsys.readouterr().err


@requires_ffmpeg
def test_generate_command_persists_voice_profile_association_on_first_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    cli.main([
        "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
        "--voice-profile", "narrator-1",
    ])

    db = Database(str(db_path))
    book = db.find_book_by_source(str(md_path.resolve()))
    profile = db.find_voice_profile_by_name("narrator-1")
    assert book.voice_profile_id == profile.id
    db.close()


@requires_ffmpeg
def test_generate_command_uses_original_voice_for_new_chapters_after_profile_mismatch(
    tmp_path, monkeypatch, capsys
):
    ref_path_a = tmp_path / "narrator-a.wav"
    _write_reference_wav(ref_path_a)
    ref_path_b = tmp_path / "narrator-b.wav"
    _write_reference_wav(ref_path_b)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-a", "--reference", str(ref_path_a), "--db", str(db_path)])
    cli.main(["voice", "add", "--name", "narrator-b", "--reference", str(ref_path_b), "--db", str(db_path)])

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n\n# Chapter Two\nBody two.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    class FlakyOnce(FakeEngine):
        def synthesize(self, text_chunk, profile_handle, params):
            # Markdown ingest strips the heading out of chapter.text (it's
            # tracked separately as chapter.title), so the pipeline only ever
            # passes body text to synthesize() - match on "body two", not the
            # chapter title, or this never fires and the chapter never fails.
            if "body two" in text_chunk.lower():
                raise RuntimeError("pause before chapter two")
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "_make_engine", lambda name: FlakyOnce())
    with pytest.raises(SystemExit):
        cli.main([
            "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
            "--voice-profile", "narrator-a",
        ])
    capsys.readouterr()

    class RecordingEngine(FakeEngine):
        clone_calls = []

        def clone_profile(self, reference_audio, reference_text, params):
            type(self).clone_calls.append(reference_audio)
            return super().clone_profile(reference_audio, reference_text, params)

    monkeypatch.setattr(cli, "_make_engine", lambda name: RecordingEngine())
    cli.main([
        "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
        "--voice-profile", "narrator-b",
    ])

    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert "narrator-a" in captured.err
    # chapter two (not yet done at the time of the mismatch) was synthesized
    # with the originally-associated voice (narrator-a), not the requested one
    # The migration copies the reference audio into VOICES_DIR on Database.__init__, so
    # the path is now VOICES_DIR/{id}_{name}/reference.{suffix}
    assert len(RecordingEngine.clone_calls) == 1
    assert RecordingEngine.clone_calls[0].endswith("_narrator-a/reference.wav")
    assert Path(RecordingEngine.clone_calls[0]).is_file()


@requires_ffmpeg
def test_generate_command_keeps_default_voice_for_new_chapters_after_later_profile_request(
    tmp_path, monkeypatch, capsys
):
    ref_path = tmp_path / "narrator-a.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-a", "--reference", str(ref_path), "--db", str(db_path)])

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n\n# Chapter Two\nBody two.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    class FlakyOnce(FakeEngine):
        def synthesize(self, text_chunk, profile_handle, params):
            if "body two" in text_chunk.lower():
                raise RuntimeError("pause before chapter two")
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "_make_engine", lambda name: FlakyOnce())
    with pytest.raises(SystemExit):
        cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    capsys.readouterr()

    class RecordingEngine(FakeEngine):
        clone_calls = []

        def clone_profile(self, reference_audio, reference_text, params):
            type(self).clone_calls.append(reference_audio)
            return super().clone_profile(reference_audio, reference_text, params)

    monkeypatch.setattr(cli, "_make_engine", lambda name: RecordingEngine())
    cli.main([
        "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
        "--voice-profile", "narrator-a",
    ])

    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert "default voice" in captured.err
    # chapter two (not yet done at the time of the mismatch) stayed on the
    # default voice - it must NOT have been cloned with narrator-a
    assert RecordingEngine.clone_calls == [None]


def test_voice_add_strips_whitespace_from_name(tmp_path):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "  narrator-1  ", "--reference", str(ref_path), "--db", str(db_path)])

    db = Database(str(db_path))
    assert db.find_voice_profile_by_name("narrator-1") is not None
    db.close()


def test_voice_add_rejects_empty_name(tmp_path, capsys):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["voice", "add", "--name", "  ", "--reference", str(ref_path), "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "must not be empty" in capsys.readouterr().err


def test_voice_add_does_not_print_warnings_on_duplicate_name_rejection(tmp_path, capsys):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path, duration_s=3.0)  # short enough to trigger a real warning

    db_path = tmp_path / "test.db"
    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    capsys.readouterr()

    with pytest.raises(SystemExit):
        cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Warning" not in captured.err
    assert "already exists" in captured.err


def test_voice_preview_creates_output_directory_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"
    out_path = tmp_path / "nested" / "dir" / "preview.wav"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    cli.main([
        "voice", "preview", "--name", "narrator-1", "--text", "Hi.",
        "--out", str(out_path), "--db", str(db_path),
    ])

    assert out_path.exists()


@requires_ffmpeg
def test_generate_command_reports_missing_reference_audio(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])

    # `voice add` now copies the reference clip into permanent storage immediately, so
    # deleting the original upload (ref_path) no longer breaks the profile - that's the
    # fix this project exists to deliver. To exercise "reference audio genuinely missing"
    # we instead delete the permanent copy itself, e.g. simulating manual tampering with
    # the voices/ directory.
    db = Database(str(db_path))
    profile = db.find_voice_profile_by_name("narrator-1")
    Path(profile.reference_audio_path).unlink()
    db.close()

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path),
            "--voice-profile", "narrator-1",
        ])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "reference audio" in captured.err
    assert "missing" in captured.err


def test_lexicon_add_creates_global_entry(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Added pronunciation for 'Cadeyrn'" in captured.out

    db = Database(str(db_path))
    entry = db.find_lexicon_entry("Cadeyrn")
    assert entry is not None
    assert entry.override_value == "KAH-dern"
    assert entry.scope == "global"
    db.close()


def test_lexicon_add_creates_book_scoped_entry(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])

    cli.main([
        "lexicon", "add", "--term", "Cadeyrn", "--respell", "kah-DAIRN",
        "--book", str(md_path), "--db", str(db_path),
    ])

    db = Database(str(db_path))
    book = db.find_book_by_source(str(md_path.resolve()))
    entry = db.find_lexicon_entry("Cadeyrn", book_id=book.id)
    assert entry is not None
    assert entry.override_value == "kah-DAIRN"
    assert entry.scope == "book"
    db.close()


def test_lexicon_add_reports_unknown_book(tmp_path, capsys):
    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern",
            "--book", str(md_path), "--db", str(db_path),
        ])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "no book found" in captured.err
    assert "generate" in captured.err


def test_lexicon_add_rejects_empty_term(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "add", "--term", "  ", "--respell", "KAH-dern", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "must not be empty" in capsys.readouterr().err


def test_lexicon_add_rejects_duplicate_term_in_same_scope(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "different", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "already exists" in capsys.readouterr().err


def test_lexicon_list_shows_global_entries(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])
    capsys.readouterr()

    cli.main(["lexicon", "list", "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Cadeyrn" in captured.out
    assert "KAH-dern" in captured.out


def test_lexicon_list_shows_book_scoped_and_global_entries(tmp_path, capsys, monkeypatch):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    cli.main(["lexicon", "add", "--term", "Global One", "--respell", "g-one", "--db", str(db_path)])
    cli.main([
        "lexicon", "add", "--term", "Book One", "--respell", "b-one",
        "--book", str(md_path), "--db", str(db_path),
    ])
    capsys.readouterr()

    cli.main(["lexicon", "list", "--book", str(md_path), "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Global One" in captured.out
    assert "Book One" in captured.out


def test_lexicon_list_reports_when_empty(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    cli.main(["lexicon", "list", "--db", str(db_path)])

    assert "No lexicon entries yet" in capsys.readouterr().out


def test_lexicon_remove_deletes_global_entry(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])
    capsys.readouterr()

    cli.main(["lexicon", "remove", "--term", "Cadeyrn", "--db", str(db_path)])

    assert "Removed" in capsys.readouterr().out
    db = Database(str(db_path))
    assert db.find_lexicon_entry("Cadeyrn") is None
    db.close()


def test_lexicon_remove_reports_missing_entry(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "remove", "--term", "Ghost", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "no lexicon entry" in capsys.readouterr().err


def test_lexicon_preview_writes_audio_with_substitution_applied(tmp_path, monkeypatch):
    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "_make_engine", lambda name: RecordingEngine())

    db_path = tmp_path / "test.db"
    out_path = tmp_path / "preview.wav"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])

    cli.main([
        "lexicon", "preview", "--term", "Cadeyrn", "--out", str(out_path), "--db", str(db_path),
    ])

    assert out_path.exists()
    assert any("KAH-dern" in text for text in RecordingEngine.synthesized_texts)


def test_lexicon_preview_uses_custom_sentence_when_given(tmp_path, monkeypatch):
    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "_make_engine", lambda name: RecordingEngine())
    monkeypatch.chdir(tmp_path)

    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])

    cli.main([
        "lexicon", "preview", "--term", "Cadeyrn", "--sentence", "Cadeyrn walked home alone.",
        "--db", str(db_path),
    ])

    assert any("KAH-dern walked home alone." in text for text in RecordingEngine.synthesized_texts)


def test_lexicon_preview_supports_voice_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"
    out_path = tmp_path / "preview.wav"

    cli.main(["voice", "add", "--name", "narrator-1", "--reference", str(ref_path), "--db", str(db_path)])
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])

    cli.main([
        "lexicon", "preview", "--term", "Cadeyrn", "--voice-profile", "narrator-1",
        "--out", str(out_path), "--db", str(db_path),
    ])

    assert out_path.exists()


def test_lexicon_preview_reports_missing_entry(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "preview", "--term", "Ghost", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "no lexicon entry" in capsys.readouterr().err


def test_lexicon_add_rejects_empty_respelling(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "add", "--term", "Ghost", "--respell", "   ", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "must not be empty" in capsys.readouterr().err


def test_lexicon_add_warns_when_term_cannot_match_a_word_boundary(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    cli.main(["lexicon", "add", "--term", "Dr.", "--respell", "Doctor", "--db", str(db_path)])

    assert "word boundary" in capsys.readouterr().err


def test_lexicon_remove_strips_whitespace_from_term(tmp_path):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])

    cli.main(["lexicon", "remove", "--term", "  Cadeyrn  ", "--db", str(db_path)])

    db = Database(str(db_path))
    assert db.find_lexicon_entry("Cadeyrn") is None
    db.close()


def test_lexicon_preview_sanitizes_term_for_default_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())
    monkeypatch.chdir(tmp_path)

    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "AC/DC", "--respell", "AY-SEE-DEE-SEE", "--db", str(db_path)])

    cli.main(["lexicon", "preview", "--term", "AC/DC", "--db", str(db_path)])

    assert not (tmp_path / "AC").exists()  # no unwanted subdirectory created
    assert any(p.name.endswith("-pronunciation-preview.wav") for p in tmp_path.iterdir())


@requires_ffmpeg
def test_lexicon_preview_prefers_book_scoped_entry_over_global(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "_make_engine", lambda name: RecordingEngine())

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "GLOBAL-version", "--db", str(db_path)])
    cli.main([
        "lexicon", "add", "--term", "Cadeyrn", "--respell", "BOOK-version",
        "--book", str(md_path), "--db", str(db_path),
    ])

    cli.main(["lexicon", "preview", "--term", "Cadeyrn", "--book", str(md_path), "--db", str(db_path)])

    assert any("BOOK-version" in text for text in RecordingEngine.synthesized_texts)


@requires_ffmpeg
def test_lexicon_preview_falls_back_to_global_entry_when_book_scope_has_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class RecordingEngine(FakeEngine):
        synthesized_texts = []

        def synthesize(self, text_chunk, profile_handle, params):
            type(self).synthesized_texts.append(text_chunk)
            return super().synthesize(text_chunk, profile_handle, params)

    monkeypatch.setattr(cli, "_make_engine", lambda name: RecordingEngine())

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "GLOBAL-version", "--db", str(db_path)])

    cli.main(["lexicon", "preview", "--term", "Cadeyrn", "--book", str(md_path), "--db", str(db_path)])

    assert any("GLOBAL-version" in text for text in RecordingEngine.synthesized_texts)


def test_lexicon_list_still_shows_manual_entries_without_phonemes_column_crashing(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])
    capsys.readouterr()

    cli.main(["lexicon", "list", "--db", str(db_path)])

    captured = capsys.readouterr()
    assert "Cadeyrn" in captured.out
    assert "manual" in captured.out


def test_lexicon_edit_reports_missing_entry(tmp_path, capsys):
    db_path = tmp_path / "test.db"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "edit", "--term", "Ghost", "--respell", "GOST", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "no lexicon entry" in capsys.readouterr().err


def test_lexicon_edit_rejects_empty_respelling(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "KAH-dern", "--db", str(db_path)])
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["lexicon", "edit", "--term", "Cadeyrn", "--respell", "   ", "--db", str(db_path)])

    assert exc_info.value.code == 1
    assert "must not be empty" in capsys.readouterr().err


def test_lexicon_edit_respects_book_scope(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())

    md_path = tmp_path / "book.md"
    md_path.write_text("# Chapter One\nBody one.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    db_path = tmp_path / "test.db"

    cli.main(["generate", "--input", str(md_path), "--out", str(out_dir), "--db", str(db_path)])
    cli.main(["lexicon", "add", "--term", "Cadeyrn", "--respell", "GLOBAL-version", "--db", str(db_path)])
    cli.main([
        "lexicon", "add", "--term", "Cadeyrn", "--respell", "BOOK-version",
        "--book", str(md_path), "--db", str(db_path),
    ])

    cli.main([
        "lexicon", "edit", "--term", "Cadeyrn", "--respell", "BOOK-EDITED",
        "--book", str(md_path), "--db", str(db_path),
    ])

    db = Database(str(db_path))
    book = db.find_book_by_source(str(md_path.resolve()))
    assert db.find_lexicon_entry("Cadeyrn", book_id=book.id).override_value == "BOOK-EDITED"
    assert db.find_lexicon_entry("Cadeyrn").override_value == "GLOBAL-version"  # untouched
    db.close()


def test_worker_command_runs_forever_loading_engines_on_demand(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_engine", lambda name: FakeEngine())
    calls = []

    def fake_run_forever(db, engines, poll_interval, text_clip_output_dir):
        calls.append((db, engines, poll_interval))
        engines.get("chatterbox-turbo")

    monkeypatch.setattr(cli.worker, "run_forever", fake_run_forever)

    db_path = tmp_path / "test.db"
    cli.main(["worker", "--db", str(db_path), "--poll-interval", "5"])

    assert len(calls) == 1
    db, engines, poll_interval = calls[0]
    assert engines.engine is None  # torn down on exit; nothing was loaded up front
    assert poll_interval == 5.0


class _NamedFake(FakeEngine):
    """Fake standing in for whichever engine was asked for, by name."""
    made = []

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.needs_transcript = name == "qwen3"
        _NamedFake.made.append(name)


def _named_engines(monkeypatch):
    _NamedFake.made = []
    monkeypatch.setattr(cli, "_make_engine", _NamedFake)
    from audiobookify.voice import analysis
    monkeypatch.setattr(analysis, "transcribe", lambda path: {
        "text": "Words.", "words": [{"word": " Words.", "start": 0.0, "end": 1.0}]})


def test_voice_add_queues_whisper_unless_a_transcript_is_typed(tmp_path):
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"

    cli.main(["voice", "add", "--name", "a", "--reference", str(ref_path), "--db", str(db_path)])
    cli.main(["voice", "add", "--name", "b", "--reference", str(ref_path), "--db", str(db_path),
              "--transcript", "Typed words."])

    db = Database(str(db_path))
    assert db.find_voice_profile_by_name("a").transcript_status == "pending"
    assert db.find_voice_profile_by_name("b").transcript_status == "done"
    db.close()


def test_voice_preview_uses_the_chosen_engine_and_transcribes_for_qwen3(tmp_path, monkeypatch):
    _named_engines(monkeypatch)
    ref_path = tmp_path / "narrator.wav"
    _write_reference_wav(ref_path)
    db_path = tmp_path / "test.db"
    cli.main(["voice", "add", "--name", "n", "--reference", str(ref_path), "--db", str(db_path)])

    cli.main(["voice", "preview", "--name", "n", "--text", "Hello.", "--engine", "qwen3",
              "--out", str(tmp_path / "p.wav"), "--db", str(db_path)])

    assert _NamedFake.made == ["qwen3"]
    db = Database(str(db_path))
    assert db.find_voice_profile_by_name("n").reference_transcript == "Words."
    db.close()


def test_generate_rejects_qwen3_without_a_voice_profile(tmp_path, monkeypatch, capsys):
    _named_engines(monkeypatch)
    book_path = tmp_path / "book.md"
    book_path.write_text("# Ch 1\n\nSome words here.\n")

    with pytest.raises(SystemExit):
        cli.main(["generate", "--input", str(book_path), "--out", str(tmp_path / "out"),
                  "--db", str(tmp_path / "test.db"), "--engine", "qwen3"])

    assert "needs a voice" in capsys.readouterr().err
    assert _NamedFake.made == []

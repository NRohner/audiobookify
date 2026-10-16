import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

from audiobookify.engine_worker import FAKE_TRANSCRIPT
from audiobookify.engines.fake import FakeEngine

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _run(requests, env=None, code=None):
    """Feed request lines to a fresh worker; returns (process, parsed response lines)."""
    lines = "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in requests)
    args = [sys.executable, "-c", code] if code else [sys.executable, "-m", "audiobookify.engine_worker"]
    proc = subprocess.run(args, input=lines, capture_output=True, text=True, timeout=120,
                          env={**os.environ, **(env or {})})
    return proc, [json.loads(line) for line in proc.stdout.splitlines()]


def test_load_and_engine_info_for_the_fake_engine_then_exit_on_eof():
    proc, out = _run([{"op": "load", "engine": "fake"}, {"op": "engine_info"}])
    assert proc.returncode == 0
    assert out == [{"ok": {}}, {"ok": {"name": "fake", "max_chunk_chars": 300, "min_chunk_chars": 20,
                                       "sample_rate": 24000, "needs_transcript": False}}]


def test_a_fake_prefixed_name_is_a_fake_engine_with_that_name():
    _, out = _run([{"op": "load", "engine": "fake-b"}, {"op": "engine_info"}])
    assert out[1]["ok"]["name"] == "fake-b"


def test_synthesize_writes_a_float_wav_and_reports_its_length(tmp_path):
    wav = tmp_path / "out.wav"
    _, out = _run([{"op": "load", "engine": "fake"},
                   {"op": "synthesize", "text": "Hello there.", "voice": {}, "out_wav": str(wav)}])
    data, sr = sf.read(wav, dtype="float32")
    assert sf.info(wav).subtype == "FLOAT"
    assert out[1] == {"ok": {"samples": len(data), "sample_rate": 24000}} and sr == 24000


def test_errors_get_an_error_line_and_the_worker_keeps_going(tmp_path):
    _, out = _run(["not json", {"op": "nope"},
                   {"op": "synthesize", "text": "x", "voice": {}, "out_wav": str(tmp_path / "a.wav")},
                   {"op": "load", "engine": "fake"}])
    assert [("error" in r) for r in out] == [True, True, True, False]
    assert out[2]["error"] == "no engine loaded"


def test_stdout_carries_only_protocol_lines_even_when_an_engine_prints(tmp_path):
    code = ("import audiobookify.engines.fake as f, audiobookify.engine_worker as w\n"
            "orig = f.FakeEngine.synthesize\n"
            "def noisy(self, *a):\n    print('noise from the model')\n    return orig(self, *a)\n"
            "f.FakeEngine.synthesize = noisy\nw.main()\n")
    proc, out = _run([{"op": "load", "engine": "fake"},
                      {"op": "synthesize", "text": "Hi.", "voice": {}, "out_wav": str(tmp_path / "a.wav")}],
                     code=code)
    assert len(out) == 2 and "ok" in out[1]  # every stdout line parsed as JSON
    assert "noise from the model" in proc.stderr


def test_shutdown_replies_and_exits_without_reading_further():
    proc, out = _run([{"op": "shutdown"}, {"op": "load", "engine": "fake"}])
    assert proc.returncode == 0 and out == [{"ok": {}}]


def test_transcribe_uses_the_fake_transcript_when_asked(tmp_path):
    _, out = _run([{"op": "transcribe", "path": str(tmp_path / "x.wav")}], env={"AUDIOBOOKIFY_FAKE_STT": "1"})
    assert out == [{"ok": FAKE_TRANSCRIPT}]
    _, out = _run([{"op": "transcribe", "path": str(tmp_path / "x.wav")}], env={"AUDIOBOOKIFY_FAKE_STT": "error"})
    assert out == [{"error": "no speech found"}]


@requires_ffmpeg
def test_synthesize_clones_from_the_trimmed_clip_at_the_voice_tempo(tmp_path):
    ref = tmp_path / "ref.wav"
    sf.write(ref, np.zeros(24000 * 20, dtype=np.float32), 24000)
    voice = {"reference_path": str(ref), "reference_text": "One two.", "trim_end": 14.15, "tempo": 0.9}
    _, out = _run([{"op": "load", "engine": "fake"},
                   {"op": "synthesize", "text": "Hello.", "voice": voice, "out_wav": str(tmp_path / "a.wav")}])
    assert "ok" in out[1]
    assert (tmp_path / "ref.trim-14.15s.wav").is_file()
    assert (tmp_path / "ref.trim-14.15s.tempo-0.90.wav").is_file()


def test_fake_engine_knobs(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("AUDIOBOOKIFY_FAKE_LOG", str(log))
    monkeypatch.setenv("AUDIOBOOKIFY_FAKE_FAIL_ON", "boom")
    monkeypatch.setenv("AUDIOBOOKIFY_FAKE_NEEDS_TRANSCRIPT", "1")
    engine = FakeEngine()
    engine.synthesize("Fine.", None, {})
    with pytest.raises(RuntimeError, match="synthesis blew up"):
        engine.synthesize("Then BOOM.", None, {})
    assert [json.loads(line) for line in log.read_text().splitlines()] == ["Fine.", "Then BOOM."]
    assert engine.needs_transcript is True
    assert FakeEngine.needs_transcript is False  # the class default is untouched

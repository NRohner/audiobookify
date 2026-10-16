from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from audiobookify.voice import analysis
from audiobookify.voice.analysis import reference_clip, transcribe


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_transcribe_returns_text_and_word_timings_from_one_whisper_pass(monkeypatch):
    calls = []

    class FakeWhisper:
        def generate(self, path, word_timestamps):
            calls.append((path, word_timestamps))
            return SimpleNamespace(text="  Hello there. ", segments=[
                {"words": [_w(" Hello", 0.0, 0.4)]}, {"words": [_w(" there.", 0.5, 0.9)]}])

    monkeypatch.setattr(analysis, "_load_whisper", FakeWhisper)
    result = transcribe("/refs/a.wav")

    assert calls == [("/refs/a.wav", True)]
    assert result == {"text": "Hello there.", "words": [_w(" Hello", 0.0, 0.4), _w(" there.", 0.5, 0.9)]}


def _whisper_returning(*results):
    """Fake Whisper: one (text, words) result per generate() call; records prompts."""
    calls = []

    class FakeWhisper:
        def generate(self, path, word_timestamps, **kw):
            calls.append(kw.get("initial_prompt"))
            text = results[len(calls) - 1]
            words = [_w(" " + t, i, i + 0.5) for i, t in enumerate(text.split())]
            return SimpleNamespace(text=text, segments=[{"words": words}])
    return FakeWhisper, calls


def test_transcribe_retries_with_a_punctuation_prompt_when_whisper_drops_punctuation(monkeypatch):
    fake, calls = _whisper_returning("the white wolf raced to a black wood",
                                     "The white wolf raced to a black wood.")
    monkeypatch.setattr(analysis, "_load_whisper", fake)

    result = transcribe("/refs/roy.mp3")

    assert calls == [None, analysis.PUNCTUATION_PROMPT]
    assert result["text"] == "The white wolf raced to a black wood."
    assert result["words"][-1]["word"] == " wood."


def test_transcribe_keeps_the_plain_pass_when_the_prompted_one_loses_words(monkeypatch):
    # Prompts can make Whisper skip most of a clip; never trade words for punctuation.
    fake, _ = _whisper_returning("the white wolf raced to a black wood beneath a pale cliff", "It was.")
    monkeypatch.setattr(analysis, "_load_whisper", fake)
    assert transcribe("/refs/roy.mp3")["text"] == "the white wolf raced to a black wood beneath a pale cliff"


def test_transcribe_runs_once_when_the_text_is_already_punctuated(monkeypatch):
    fake, calls = _whisper_returning("Mrs. Dursley was thin.")
    monkeypatch.setattr(analysis, "_load_whisper", fake)
    transcribe("/refs/jim.wav")
    assert calls == [None]


def test_reference_clip_is_the_original_when_untrimmed(tmp_path):
    ref = tmp_path / "reference.mp3"
    ref.write_bytes(b"x")
    assert reference_clip(str(ref), None) == str(ref)


def test_reference_clip_writes_the_trimmed_clip_once_next_to_the_reference(tmp_path):
    ref = tmp_path / "reference.wav"
    sf.write(ref, np.arange(24000 * 3, dtype=np.float32) / 1e6, 24000)

    trimmed = reference_clip(str(ref), 1.5)
    data, sr = sf.read(trimmed, dtype="float32")
    assert trimmed.startswith(str(tmp_path)) and trimmed != str(ref)
    assert sr == 24000 and len(data) == 36000

    mtime = (tmp_path / trimmed).stat().st_mtime_ns
    assert reference_clip(str(ref), 1.5) == trimmed
    assert (tmp_path / trimmed).stat().st_mtime_ns == mtime  # cached, not rewritten


def test_reference_clip_at_tempo_one_is_the_plain_clip(tmp_path):
    ref = tmp_path / "reference.mp3"
    ref.write_bytes(b"x")
    assert reference_clip(str(ref), None, 1.0) == str(ref)


def _tone(path, seconds, sr=24000):
    t = np.arange(int(sr * seconds)) / sr
    sf.write(path, (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr)


def test_reference_clip_slows_the_trimmed_clip_with_atempo_and_caches_it(tmp_path):
    ref = tmp_path / "reference.wav"
    _tone(ref, 3.0)

    slowed = reference_clip(str(ref), 1.5, 0.8)
    info = sf.info(slowed)
    assert slowed.startswith(str(tmp_path)) and "tempo-0.80" in slowed and "trim-1.50s" in slowed
    assert info.samplerate == 24000 and info.duration == pytest.approx(1.5 / 0.8, abs=0.05)

    mtime = (tmp_path / slowed).stat().st_mtime_ns
    assert reference_clip(str(ref), 1.5, 0.8) == slowed
    assert (tmp_path / slowed).stat().st_mtime_ns == mtime  # cached, not rewritten


def test_reference_clip_speeds_up_an_untrimmed_clip(tmp_path):
    ref = tmp_path / "reference.wav"
    _tone(ref, 3.0)
    faster = reference_clip(str(ref), None, 1.2)
    assert faster != str(ref) and sf.info(faster).duration == pytest.approx(3.0 / 1.2, abs=0.05)


@pytest.mark.integration
def test_transcribe_runs_real_whisper_on_a_reference_clip():
    from pathlib import Path

    clip = next(Path("voices").glob("*/reference.*"), None)
    if clip is None:
        pytest.skip("no stored voice clip to transcribe")
    result = transcribe(str(clip))
    assert len(result["text"].split()) > 5
    assert result["words"] and all(w["end"] >= w["start"] for w in result["words"])

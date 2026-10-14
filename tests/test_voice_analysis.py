from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from audiobookify.voice import analysis
from audiobookify.voice.analysis import reference_clip, transcribe, trim_point


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


def test_trim_point_keeps_a_short_clip_whole():
    words = [_w(" One", 0.0, 5.0), _w(" two.", 5.2, 14.9)]
    assert trim_point(words, max_seconds=15.0) is None


def test_trim_point_cuts_after_the_last_whole_word_before_the_limit():
    words = [_w(" The", 0.0, 0.3), _w(" quick", 0.4, 7.0), _w(" fox", 7.1, 14.8), _w(" ran.", 15.0, 15.6)]
    end, text = trim_point(words, max_seconds=15.0)
    assert end == pytest.approx(14.8 + 0.15)  # a little air after the last word
    assert text == "The quick fox"


def test_trim_point_never_cuts_into_the_next_word():
    words = [_w(" a", 0.0, 14.0), _w(" b", 14.05, 16.0)]
    end, _ = trim_point(words, max_seconds=15.0)
    assert end == pytest.approx(14.05)


def test_trim_point_prefers_a_sentence_end_when_one_is_close_to_the_limit():
    words = [_w(" One", 0.0, 1.0), _w(" two.", 1.1, 11.5), _w(" Three", 11.7, 13.0), _w(" four", 13.1, 14.9),
             _w(" five", 15.0, 16.0)]
    end, text = trim_point(words, max_seconds=15.0)
    assert text == "One two."
    assert end == pytest.approx(11.65)


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


def test_word_error_rate():
    from audiobookify.voice.analysis import word_error_rate

    assert word_error_rate("The cat sat.", "the cat sat") == 0.0
    # the model is sent spelled-out numbers; Whisper may write digits back
    assert word_error_rate("Eleonora’s map, in one thousand, five hundred and forty-four!",
                           "eleonora's map in 1544") == 0.0
    assert word_error_rate("one two three four", "one three four") == 0.25  # deletion
    assert word_error_rate("one two", "one two two two") == 1.0  # repeated words count
    assert word_error_rate("well-ventilated room", "well ventilated room") == 0.0

def test_cadence_stats_from_word_timestamps():
    from audiobookify.voice.analysis import cadence_stats

    w = lambda word, start, end: {"word": word, "start": start, "end": end}
    words = [
        w(" One", 0.0, 0.3), w(" two.", 0.3, 0.6),        # sentence end, then 0.8 s pause
        w(" Three,", 1.4, 1.7),                            # comma, then 0.3 s pause
        w(" four", 2.0, 2.3), w(" five", 2.4, 2.7),        # 0.1 s gap: within speech
        w(" six!”", 2.7, 3.0),                             # sentence end before closing quote, 0.4 s pause
        w(" Seven.", 3.4, 3.7),
    ]
    stats = cadence_stats(words)

    assert stats["sentence_pauses"] == [0.8, 0.4]
    assert stats["comma_pauses"] == [0.3]
    assert stats["sentence_pause_median"] == pytest.approx(0.6)
    # 7 words over 3.7 s minus the 0.8 + 0.3 + 0.4 s pauses (gaps >= 0.15 s) = 2.2 s speaking
    assert stats["articulation_wpm"] == pytest.approx(7 / 2.2 * 60)
    assert stats["overall_wpm"] == pytest.approx(7 / 3.7 * 60)


def test_reference_pace_counts_only_the_words_engines_hear():
    words = [_w(" One", 0.0, 0.5), _w(" two.", 0.5, 1.0), _w(" Three", 1.5, 2.0),  # 0.5 s pause
             _w(" four", 20.0, 20.5)]  # past the trim
    pace = analysis.reference_pace(words, trim_end=2.1)
    assert pace["overall_wpm"] == pytest.approx(3 / 2.0 * 60)
    assert pace["speaking_wpm"] == pytest.approx(3 / 1.5 * 60)
    assert analysis.reference_pace(words, None)["overall_wpm"] == pytest.approx(4 / 20.5 * 60)


def test_reference_pace_is_none_without_enough_words():
    assert analysis.reference_pace(None, None) is None
    assert analysis.reference_pace([_w(" One", 0.0, 0.5)], None) is None


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

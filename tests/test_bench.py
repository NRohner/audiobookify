import json

import pytest
import soundfile as sf

from audiobookify.bench import leading_paragraphs, run_variant
from audiobookify.engines.fake import FakeEngine


def test_leading_paragraphs_stops_at_the_first_paragraph_end_past_the_limit():
    text = "One one one.\n\n\t\tTwo two two.\n\nThree three three."
    assert leading_paragraphs(text, 15) == "One one one.\n\nTwo two two."
    assert leading_paragraphs(text, 5) == "One one one."
    assert leading_paragraphs(text, None) == "One one one.\n\nTwo two two.\n\nThree three three."


def test_run_variant_writes_audio_and_per_chunk_timings(tmp_path):
    text = "\n\n".join(f"Paragraph number {i} is long enough to stand on its own as a chunk." for i in range(4))

    result = run_variant("fake", FakeEngine(), text, reference_audio=None, out_dir=tmp_path)

    assert result["n_chunks"] == 4 == len(result["chunks"])
    assert [c["index"] for c in result["chunks"]] == [0, 1, 2, 3]
    assert all(c["gen_seconds"] >= 0 and c["audio_seconds"] > 0 for c in result["chunks"])
    assert result["audio_seconds"] == sum(c["audio_seconds"] for c in result["chunks"])
    assert result["rtf"] == result["gen_seconds"] / result["audio_seconds"]
    assert result["load_seconds"] >= 0 and result["peak_rss_mb"] > 0

    saved = json.loads((tmp_path / "fake.json").read_text())
    assert saved["n_chunks"] == 4
    audio, sr = sf.read(tmp_path / "fake.wav")
    assert sr == 24000 and len(audio) > 0


def test_run_variant_honours_a_chunk_size_override(tmp_path):
    text = " ".join(f"Sentence {i} goes here." for i in range(40))
    result = run_variant("fake", FakeEngine(), text, reference_audio=None, out_dir=tmp_path, max_chars=100)
    assert result["max_chars"] == 100
    assert all(len(c["text"]) <= 100 + FakeEngine().min_chunk_chars() for c in result["chunks"])


def test_results_record_which_runtime_packages_produced_them(tmp_path):
    result = run_variant("fake", FakeEngine(), "A sentence long enough to be one chunk.", None, tmp_path)
    # results stay comparable across runtime upgrades
    assert "soundfile" in result["packages"]
    assert all(isinstance(v, str) for v in result["packages"].values())


def _fake_mlx_model(segments):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    model = MagicMock(sample_rate=24000)
    model.generate.side_effect = lambda text, **kw: iter([SimpleNamespace(audio=s) for s in segments])
    return model


def test_mlx_audio_engine_joins_every_segment_and_passes_reference_and_settings():
    import numpy as np

    from audiobookify.bench import MLXAudioEngine

    class Demo(MLXAudioEngine):
        REPO = "demo/repo"
        GEN_KWARGS = {"temperature": 0.7}

    engine = Demo()
    engine.model = _fake_mlx_model([[0.1, 0.2], [0.3]])  # model split the text itself
    audio = engine.synthesize("Hello. World.", engine.clone_profile("ref.wav", "ref words", {}), {})

    assert audio.dtype == np.float32 and audio.tolist() == np.float32([0.1, 0.2, 0.3]).tolist()
    engine.model.generate.assert_called_once_with("Hello. World.", ref_audio="ref.wav", temperature=0.7)
    assert (engine.max_chunk_chars(), engine.min_chunk_chars()) == (300, 80)


def test_mlx_audio_engine_passes_reference_transcript_when_the_model_needs_it():
    from audiobookify.bench import MLXAudioEngine

    class NeedsText(MLXAudioEngine):
        NEEDS_REF_TEXT = True

    engine = NeedsText()
    engine.model = _fake_mlx_model([[0.1]])
    engine.synthesize("Hi.", engine.clone_profile("ref.wav", "ref words", {}), {})
    assert engine.model.generate.call_args.kwargs["ref_text"] == "ref words"


def test_turbo_variant_benchmarks_the_apps_engine():
    from audiobookify.bench import VARIANTS
    from audiobookify.engines.turbo import ChatterboxTurboEngine

    assert VARIANTS["chatterbox-turbo"] is ChatterboxTurboEngine


def test_reference_transcript_is_transcribed_once_and_cached(tmp_path):
    from audiobookify.bench import reference_transcript

    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"pretend audio")
    calls = []
    transcribe = lambda path: calls.append(path) or "  Hello there, narrator.  "

    assert reference_transcript(str(ref), tmp_path / "cache", transcribe) == "Hello there, narrator."
    assert reference_transcript(str(ref), tmp_path / "cache", transcribe) == "Hello there, narrator."
    assert calls == [str(ref)]

    ref.write_bytes(b"a different recording")  # replaced file -> new transcript
    reference_transcript(str(ref), tmp_path / "cache", transcribe)
    assert len(calls) == 2


def test_word_error_rate():
    from audiobookify.bench import word_error_rate

    assert word_error_rate("The cat sat.", "the cat sat") == 0.0
    # the model is sent spelled-out numbers; Whisper may write digits back
    assert word_error_rate("Eleonora’s map, in one thousand, five hundred and forty-four!",
                           "eleonora's map in 1544") == 0.0
    assert word_error_rate("one two three four", "one three four") == 0.25  # deletion
    assert word_error_rate("one two", "one two two two") == 1.0  # repeated words count
    assert word_error_rate("well-ventilated room", "well ventilated room") == 0.0


def test_run_variant_hands_the_reference_transcript_to_the_engine(tmp_path):
    seen = {}

    class Recording(FakeEngine):
        def clone_profile(self, reference_audio, reference_text, params):
            seen["text"] = reference_text
            return super().clone_profile(reference_audio, reference_text, params)

    run_variant("fake", Recording(), "A sentence long enough to be one chunk.", None, tmp_path,
                reference_text="ref words")
    assert seen["text"] == "ref words"


def test_qwen3_variant_needs_the_reference_transcript():
    from audiobookify.bench import VARIANTS, MLXAudioEngine

    qwen = VARIANTS["qwen3-tts-1.7b"]
    assert issubclass(qwen, MLXAudioEngine) and qwen.NEEDS_REF_TEXT
    assert qwen.REPO == "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"


def test_score_run_adds_word_error_rate_to_each_result(tmp_path):
    from audiobookify.bench import score_run

    text = "\n\n".join(f"Paragraph number {i} is long enough to stand on its own as a chunk." for i in range(2))
    run_variant("fake", FakeEngine(), text, None, tmp_path)
    spoken = " ".join(f"paragraph number {i} is long enough to stand on its own as a chunk" for i in range(2))

    scores = score_run(tmp_path, transcribe=lambda path: spoken.replace("number 1", "number one one"))

    saved = json.loads((tmp_path / "fake.json").read_text())
    assert scores == {"fake": saved["wer"]}
    assert saved["wer"] == 1 / 28  # one inserted word out of 2 x 14
    assert saved["transcript"].startswith("paragraph number")


def test_breeze_variant_needs_the_reference_transcript():
    from audiobookify.bench import VARIANTS, MLXAudioEngine

    breeze = VARIANTS["breeze-tts-2"]
    assert issubclass(breeze, MLXAudioEngine) and breeze.NEEDS_REF_TEXT
    assert breeze.REPO == "mlx-community/Breeze-TTS-2-mlx"


def test_cadence_stats_from_word_timestamps():
    from audiobookify.bench import cadence_stats

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

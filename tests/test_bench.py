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


def test_app_engine_variants_benchmark_the_apps_engines():
    from audiobookify.bench import VARIANTS
    from audiobookify.engines.turbo import ChatterboxTurboEngine

    from audiobookify.engines.qwen3 import Qwen3Engine

    assert VARIANTS["chatterbox-turbo"] is ChatterboxTurboEngine
    assert VARIANTS["qwen3-tts-1.7b"] is Qwen3Engine


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




def test_run_variant_hands_the_reference_transcript_to_the_engine(tmp_path):
    seen = {}

    class Recording(FakeEngine):
        def clone_profile(self, reference_audio, reference_text, params):
            seen["text"] = reference_text
            return super().clone_profile(reference_audio, reference_text, params)

    run_variant("fake", Recording(), "A sentence long enough to be one chunk.", None, tmp_path,
                reference_text="ref words")
    assert seen["text"] == "ref words"


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


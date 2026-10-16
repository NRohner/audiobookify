"""The desktop app's ML process: one TTS engine behind JSON lines on stdin/stdout.

One request per line ({"op": ..., ...}), exactly one response line each:
{"ok": {...}} or {"error": "..."}. Exits on stdin EOF, so it dies with the app.
No DB access and no pipeline logic; the Rust runner owns those.
"""
import contextlib
import json
import os
import sys

import numpy as np
import soundfile as sf

from .engines import ENGINES
from .engines.fake import FakeEngine
from .voice import analysis

# What AUDIOBOOKIFY_FAKE_STT=1 returns: 18 s of words, so trim_point cuts at "two." (14.15 s).
FAKE_TRANSCRIPT = {"text": "One two. Three", "words": [
    {"word": " One", "start": 0.0, "end": 10.0},
    {"word": " two.", "start": 10.1, "end": 14.0},
    {"word": " Three", "start": 14.2, "end": 18.0},
]}


class EngineSlot:
    """At most one loaded TTS engine: Qwen3 (~12 GB) and Turbo (~5 GB) together
    don't fit comfortably in 18 GB, and MLX reloads take only seconds."""

    def __init__(self, make=lambda name: ENGINES[name].cls()):
        self._make = make
        self.name = None
        self.engine = None
        self.profile_cache: dict = {}  # voice handles belong to the engine that made them

    def get(self, name: str):
        if name != self.name:
            self.teardown()
            print(f"Loading TTS engine '{name}'", file=sys.stderr)
            engine = self._make(name)
            engine.load()
            self.name, self.engine = name, engine
        return self.engine

    def teardown(self) -> None:
        engine = self.engine
        # Drop voice handles first: Turbo's hold GPU arrays the engine's teardown should free.
        self.name, self.engine, self.profile_cache = None, None, {}
        if engine is not None:
            engine.teardown()


def _make(name: str):
    if name.startswith("fake"):
        engine = FakeEngine()
        engine.name = name  # ponytail: "fake-b" lets tests switch engines without a second fake class
        return engine
    if name not in ENGINES:
        raise ValueError(f"unknown engine {name!r}")
    return ENGINES[name].cls()


def _params(voice: dict) -> dict:
    """Engine params from the protocol's voice fields (worker.params_from_profile's shape)."""
    ref = voice.get("reference_path")
    tempo = voice.get("tempo") or 1.0
    return {
        "reference_audio": analysis.reference_clip(ref, voice.get("trim_end"), tempo) if ref else None,
        "reference_text": voice.get("reference_text"),
        "reference_trim_end": voice.get("trim_end"),
        "exaggeration": voice.get("exaggeration"),
        "cfg": voice.get("cfg"),
        "temperature": voice.get("temperature"),
        "tempo": tempo,
    }


class Handler:
    def __init__(self, slot: EngineSlot = None):
        self.slot = slot or EngineSlot(_make)

    def _engine(self):
        if self.slot.engine is None:
            raise RuntimeError("no engine loaded")
        return self.slot.engine

    def load(self, engine: str) -> dict:
        self.slot.get(engine)
        return {}

    def engine_info(self) -> dict:
        e = self._engine()
        return {"name": e.name, "max_chunk_chars": e.max_chunk_chars(), "min_chunk_chars": e.min_chunk_chars(),
                "sample_rate": e.sample_rate(), "needs_transcript": e.needs_transcript}

    def synthesize(self, text: str, voice: dict, out_wav: str) -> dict:
        e = self._engine()
        params = _params(voice)
        # Keyed on the clip and transcript, so an edited transcript, trim or tempo takes effect.
        key = (params["reference_audio"], params["reference_text"])
        cache = self.slot.profile_cache
        if cache.get("key") != key:
            cache["key"] = key
            cache["handle"] = e.clone_profile(params["reference_audio"], params["reference_text"], params)
        audio = np.asarray(e.synthesize(text, cache["handle"], params), dtype=np.float32).reshape(-1)
        # Atomic: libsndfile writes a zero data length and patches it on close, so a kill
        # mid-write would leave a WAV that reads as valid and empty.
        tmp = os.path.join(os.path.dirname(out_wav) or ".", f".{os.path.basename(out_wav)}.{os.getpid()}.tmp")
        try:
            sf.write(tmp, audio, e.sample_rate(), format="WAV", subtype="FLOAT")
            os.replace(tmp, out_wav)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
        return {"samples": len(audio), "sample_rate": e.sample_rate()}

    def transcribe(self, path: str) -> dict:
        fake = os.environ.get("AUDIOBOOKIFY_FAKE_STT")
        if fake == "error":
            raise RuntimeError("no speech found")
        if fake:
            return FAKE_TRANSCRIPT
        return analysis.transcribe(path)


OPS = {"load", "engine_info", "synthesize", "transcribe"}


def _reply(out, message: dict) -> None:
    out.write(json.dumps(message) + "\n")
    out.flush()


def serve(lines, out, handler: Handler) -> None:
    for line in lines:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            op = request.pop("op")
            if op == "shutdown":
                _reply(out, {"ok": {}})
                return
            if op not in OPS:
                raise ValueError(f"unknown op {op!r}")
            _reply(out, {"ok": getattr(handler, op)(**request)})
        except Exception as exc:
            _reply(out, {"error": str(exc) or type(exc).__name__})


def main() -> None:
    # Keep the real stdout for the protocol and point fd 1 at stderr, so a library
    # print (Python or C level) can never corrupt a response line.
    protocol = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    serve(sys.stdin, protocol, Handler())


if __name__ == "__main__":
    main()

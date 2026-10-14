from typing import NamedTuple

from .qwen3 import Qwen3Engine
from .turbo import ChatterboxTurboEngine


class EngineSpec(NamedTuple):
    cls: type
    label: str
    description: str


ENGINES = {
    Qwen3Engine.name: EngineSpec(Qwen3Engine, "Quality (Qwen3)",
                                 "Most natural; needs a voice with a transcript. About 0.85 s per second of audio."),
    ChatterboxTurboEngine.name: EngineSpec(ChatterboxTurboEngine, "Speed (Turbo)",
                                           "About twice as fast, slightly less natural."),
}
DEFAULT_ENGINE = ChatterboxTurboEngine.name

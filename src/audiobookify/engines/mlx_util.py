"""Shared bits for the mlx-audio TTS engines."""
import numpy as np


def load_model(repo: str):
    from mlx_audio.tts.utils import load_model as _load
    return _load(repo)


def join_segments(segments) -> np.ndarray:
    # The ports may split text themselves and yield several segments; keep them all.
    return np.concatenate([np.asarray(s.audio, dtype=np.float32).reshape(-1) for s in segments])


def free_memory() -> None:
    """Return cached GPU memory, so the next engine fits."""
    import gc
    gc.collect()  # model objects can sit in reference cycles
    try:
        import mlx.core as mx
        mx.clear_cache()
    except ImportError:
        pass

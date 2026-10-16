"""Writes golden.json: inputs and Python's trim/level/concatenate outputs.
Run from the repo root: .venv/bin/python desktop/core/tests/fixtures/audio/generate.py"""
import json
from pathlib import Path

import numpy as np

from audiobookify.postprocess.audio import concatenate_chunks, level_chunks, trim_silence

SR = 1000


def speech(n, amp=0.5):
    return (amp * np.sin(np.arange(n) * 0.3)).astype(np.float32)


def padded(n_speech, n_pad, amp=0.5):
    pad = np.zeros(n_pad, dtype=np.float32)
    return np.concatenate([pad, speech(n_speech, amp), pad])


def spiky():
    s = speech(500, 0.05)
    s[250] = 0.95
    return s


def ramp_up():  # a fade-in that crosses the -40 dB threshold mid-chunk
    return (np.linspace(0, 1, 800, dtype=np.float32) ** 3 * np.sin(np.arange(800) * 0.2)).astype(np.float32)


trim_cases = [padded(500, 400), speech(500), np.zeros(300, dtype=np.float32), ramp_up(), speech(5, 0.3)]
level_cases = [
    [speech(500), speech(500), speech(500, 0.05), speech(500)],
    [speech(500, 0.5), speech(500, 0.4)],
    [speech(500), speech(500), spiky()],
    [np.zeros(100, dtype=np.float32), speech(300, 0.1), speech(300, 0.6)],
]
concat_cases = [
    ([padded(200, 300), padded(200, 300, 0.1), speech(200)], ["scene", "paragraph", "sentence"]),
    ([speech(200), speech(200), speech(200)], ["word", "clause", "paragraph"]),
]

out = {
    "sample_rate": SR,
    "trim": [{"input": c.tolist(), "output": trim_silence(c, SR).tolist()} for c in trim_cases],
    "level": [{"input": [c.tolist() for c in cs], "output": [o.tolist() for o in level_chunks(cs)]}
              for cs in level_cases],
    "concat": [{"input": [c.tolist() for c in cs], "ends_with": ends,
                "output": concatenate_chunks(cs, ends, SR).tolist()} for cs, ends in concat_cases],
}
Path(__file__).with_name("golden.json").write_text(json.dumps(out))

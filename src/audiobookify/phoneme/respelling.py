# Static espeak-IPA-symbol -> ASCII mapping. Deliberately simple: no syllable
# boundaries or stress marking. The derived respelling is a starting point the
# user corrects with `lexicon edit` before it's used, not a phonetically
# precise transcription.
_IPA_TO_ASCII = {
    "p": "p", "b": "b", "t": "t", "d": "d", "k": "k", "ɡ": "g", "g": "g",
    "tʃ": "ch", "dʒ": "j", "f": "f", "v": "v", "θ": "th", "ð": "th",
    "s": "s", "z": "z", "ʃ": "sh", "ʒ": "zh", "h": "h", "m": "m", "n": "n",
    "ŋ": "ng", "l": "l", "ɹ": "r", "r": "r", "w": "w", "j": "y",
    "i": "ee", "iː": "ee", "ɪ": "ih", "ɛ": "eh", "e": "eh", "æ": "a",
    "ɑ": "ah", "ɑː": "ah", "ɔ": "aw", "ɔː": "aw", "ʊ": "uu", "u": "oo",
    "uː": "oo", "ʌ": "uh", "ə": "uh", "ɚ": "er", "ɝ": "er",
    "eɪ": "ay", "aɪ": "eye", "ɔɪ": "oy", "aʊ": "ow", "oʊ": "oh",
}


def phonemes_to_respelling(phonemes: str) -> str:
    tokens = phonemes.split()
    return "".join(_IPA_TO_ASCII.get(token, token) for token in tokens)

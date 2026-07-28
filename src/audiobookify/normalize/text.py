import re

from num2words import num2words

ABBREVIATIONS = {
    "Dr.": "Doctor", "Mr.": "Mister", "Mrs.": "Missus", "Ms.": "Miz",
    "Prof.": "Professor", "St.": "Saint", "vs.": "versus", "etc.": "et cetera",
}

_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'])')


def expand_abbreviations(text: str) -> str:
    for abbr, expansion in ABBREVIATIONS.items():
        text = re.sub(re.escape(abbr), expansion, text)
    return text


def expand_numbers(text: str) -> str:
    def repl(match: re.Match) -> str:
        raw = match.group(0).replace(",", "")
        try:
            return num2words(float(raw)) if "." in raw else num2words(int(raw))
        except ValueError:
            return match.group(0)

    return _NUMBER_RE.sub(repl, text)


def normalize_text(text: str) -> str:
    return expand_numbers(expand_abbreviations(text))


def split_sentences(text: str) -> list:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def chunk_text(text: str, max_chars: int) -> list:
    sentences = split_sentences(normalize_text(text))
    chunks = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks

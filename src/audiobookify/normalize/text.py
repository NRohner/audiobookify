import re

from num2words import num2words

ABBREVIATIONS = {
    "Dr.": "Doctor", "Mr.": "Mister", "Mrs.": "Missus", "Ms.": "Miz",
    "Prof.": "Professor", "St.": "Saint", "vs.": "versus", "etc.": "et cetera",
}

_NUMBER = r'\d[\d,]*(?:\.\d+)?'
# Matches a digit run, optionally followed by further hyphen-joined digit runs.
# The trailing `(?:-<number>)*` makes a numeric range ("12-34", "3-2") a single
# atomic match so neither operand can be half-expanded; the `(?<!-)` lookbehind
# keeps hyphen-suffixed identifiers ("COVID-19") out of the match entirely.
_NUMBER_RE = re.compile(rf'(?<!-)\b{_NUMBER}(?:-{_NUMBER})*\b')
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'])')


def expand_abbreviations(text: str) -> str:
    for abbr, expansion in ABBREVIATIONS.items():
        text = re.sub(re.escape(abbr), expansion, text)
    return text


def expand_numbers(text: str) -> str:
    def repl(match: re.Match) -> str:
        raw = match.group(0)
        if "-" in raw:
            return raw  # numeric range ("12-34", "3-2"): leave both operands alone
        raw = raw.replace(",", "")
        try:
            return num2words(float(raw)) if "." in raw else num2words(int(raw))
        except ValueError:
            return match.group(0)

    return _NUMBER_RE.sub(repl, text)


def normalize_text(text: str) -> str:
    return expand_numbers(expand_abbreviations(text))


def split_sentences(text: str) -> list:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _split_on_word_boundaries(text: str, max_chars: int) -> list:
    """Greedily pack whitespace-separated words into pieces of at most max_chars.

    Words are never broken, so a single word longer than max_chars still yields an
    oversized piece — that is preferred over emitting a mid-word fragment.
    """
    pieces = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            pieces.append(current)
            current = word
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def chunk_text(text: str, max_chars: int) -> list:
    # Abbreviations are expanded before splitting so "Dr. Smith" isn't seen as a
    # sentence break; numbers are expanded *after* splitting, because turning a
    # leading digit into a lowercase word ("7 more" -> "seven more") would hide
    # the sentence boundary from _SENTENCE_SPLIT_RE.
    sentences = [expand_numbers(s) for s in split_sentences(expand_abbreviations(text))]
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

    # A chunk can still exceed max_chars when a single sentence does (long
    # unpunctuated front matter, verse, list runs); hard-split those on words.
    result = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
        else:
            result.extend(_split_on_word_boundaries(chunk, max_chars))
    return result

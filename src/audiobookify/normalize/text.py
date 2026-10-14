import re
from typing import List, NamedTuple, Optional, Tuple

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
# A sentence ends at .!?… optionally followed by up to two closing quotes/brackets
# ('."', '?\'', '!”', '.)'); the next one may open with a quote or bracket. Without
# the closers, dialogue like '"I'm leaving." She turned' never split, producing
# run-on "sentences" that later had to be hard-split mid-sentence.
_CLOSERS = "\"'”’)\\]"
_SENTENCE_SPLIT_RE = re.compile(
    rf'(?:(?<=[.!?…])|(?<=[.!?…][{_CLOSERS}])|(?<=[.!?…][{_CLOSERS}][{_CLOSERS}]))'
    r'\s+(?=["\'“‘(\[]?[A-Z0-9])'
)
_PARAGRAPH_SPLIT_RE = re.compile(r'\n\s*\n')
_ENDS_WITH_PUNCT_RE = re.compile(rf'[.!?…,;:—–-][{_CLOSERS}]*$')
# A chunk ending in an interrupted line ("if we were to—”", "or a—", "short--").
_TRAILING_DASH_RE = re.compile(rf'\s*(?:[—–]|-+)([{_CLOSERS}]*)$')
# Clause split points, strongest first; a cut goes right after the match.
_CLAUSE_LEVELS = [re.compile(r'[;:]\s+'), re.compile(r'\s[—–]\s*|—\s*'), re.compile(r',\s+')]
# Matches a trailing hyphen-digit right after a would-be match, e.g. the "-34"
# in "12-34b" or the "-90" in "1980-90s". When the range's right operand is
# glued to a following word character (no closing \b), _NUMBER_RE above can't
# match the range as a whole, backtracks to zero range iterations, and matches
# only the left operand. This is used in the repl() callback below to detect
# that case and suppress expansion of the stranded left operand too.
_FOLLOWED_BY_RANGE_RE = re.compile(r'-\d')


def expand_abbreviations(text: str) -> str:
    for abbr, expansion in ABBREVIATIONS.items():
        text = re.sub(re.escape(abbr), expansion, text)
    return text


def expand_numbers(text: str) -> str:
    def repl(match: re.Match) -> str:
        raw = match.group(0)
        if "-" in raw:
            return raw  # numeric range ("12-34", "3-2"): leave both operands alone
        if _FOLLOWED_BY_RANGE_RE.match(match.string, match.end()):
            # The right operand of a range is glued to a trailing word character
            # ("12-34b", "1980-90s", "3-2a"), so _NUMBER_RE only matched the left
            # operand ("12", "1980", "3"). Leave it alone rather than half-expand
            # what is really still a range.
            return raw
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


def split_paragraphs(text: str) -> List[str]:
    """Blank lines separate paragraphs; whitespace inside a paragraph collapses to one space."""
    return [p for p in (" ".join(raw.split()) for raw in _PARAGRAPH_SPLIT_RE.split(text)) if p]


class Chunk(NamedTuple):
    text: str
    # Why the chunk ended, which sets the pause after it:
    # "word" | "clause" | "sentence" | "paragraph" | "scene"
    ends_with: str


def _is_scene_break(paragraph: str) -> bool:
    return not re.search(r'\w', paragraph)  # "* * *", "#", "~~~", "⁂"


def _best_cut(text: str, max_chars: int, min_chars: int, pattern: re.Pattern) -> Optional[int]:
    """Largest cut position after a `pattern` match leaving both sides within bounds."""
    best = None
    for m in pattern.finditer(text):
        left, right = text[:m.end()].strip(), text[m.end():].strip()
        if min_chars <= len(left) <= max_chars and len(right) >= min_chars:
            best = m.end()
    return best


def _split_long_sentence(sentence: str, max_chars: int, min_chars: int) -> List[Tuple[str, str]]:
    """Split a sentence longer than max_chars at the strongest clause punctuation
    that fits, falling back to a word boundary. Returns (text, ends_with) pieces;
    the last piece's ends_with is a placeholder the caller overwrites.

    Words are never broken, so a single word longer than max_chars still yields an
    oversized piece - that is preferred over emitting a mid-word fragment.
    """
    pieces = []
    rest = sentence
    while len(rest) > max_chars:
        for kind, patterns in (("clause", _CLAUSE_LEVELS), ("word", [re.compile(r'\s+')])):
            cut = next((c for c in (_best_cut(rest, max_chars, min_chars, p) for p in patterns)
                        if c is not None), None)
            if cut is not None:
                break
        if cut is None:
            # No cut keeps both sides >= min_chars; respect max_chars alone.
            cut = _best_cut(rest, max_chars, 0, re.compile(r'\s+'))
            kind = "word"
        if cut is None:
            break  # one giant word
        pieces.append((rest[:cut].strip(), kind))
        rest = rest[cut:].strip()
    pieces.append((rest, "sentence"))
    return pieces


def _chunk_paragraph(paragraph: str, max_chars: int, min_chars: int) -> List[Chunk]:
    # Abbreviations are expanded before splitting so "Dr. Smith" isn't seen as a
    # sentence break; numbers are expanded *after* splitting, because turning a
    # leading digit into a lowercase word ("7 more" -> "seven more") would hide
    # the sentence boundary from _SENTENCE_SPLIT_RE.
    sentences = [expand_numbers(s) for s in split_sentences(expand_abbreviations(paragraph))]
    packed = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > max_chars and current:
            packed.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        packed.append(current)

    # A packed chunk only exceeds max_chars when it is a single sentence (long
    # unpunctuated front matter, verse, list runs); split those at clauses/words.
    chunks = []
    for text in packed:
        if len(text) <= max_chars:
            chunks.append(Chunk(text, "sentence"))
        else:
            chunks.extend(Chunk(t, k) for t, k in _split_long_sentence(text, max_chars, min_chars))
    chunks[-1] = chunks[-1]._replace(ends_with="paragraph")
    return chunks


def _join(a: Chunk, b: Chunk) -> Chunk:
    # A merged paragraph with no closing punctuation (a heading like "Chapter One")
    # gets a period so the model pauses instead of running into the next sentence.
    first = a.text if _ENDS_WITH_PUNCT_RE.search(a.text) else a.text + "."
    return Chunk(f"{first} {b.text}", b.ends_with)


def _merge_short(chunks: List[Chunk], min_chars: int) -> List[Chunk]:
    """Very short inputs sound bad (and waste a model call), so a chunk under
    min_chars merges into the next one - or into the previous one when it ends a
    scene or the text. It only crosses a scene break when the whole scene is
    shorter than min_chars ("The End."): a lost pause beats a garbled chunk.

    ponytail: merging can push a chunk past max_chars by up to min_chars (2x for
    a short final paragraph); max_chars is a conservative quality limit, not the
    model's hard limit.
    """
    out: List[Chunk] = []
    pending: Optional[Chunk] = None
    for chunk in chunks:
        if pending is not None:
            chunk, pending = _join(pending, chunk), None
        if len(chunk.text) >= min_chars:
            out.append(chunk)
        elif chunk.ends_with == "scene" and out:
            out[-1] = _join(out[-1], chunk)
        else:
            pending = chunk
    if pending is not None:
        if out:
            out[-1] = _join(out[-1], pending)
        else:
            out.append(pending)
    return out


def _close_interrupted(chunk: Chunk) -> Chunk:
    """Give a chunk that ends in a dash a clean stop ("to—”" -> "to.”"). Turbo
    sometimes babbled or repeated words after a chunk-final dash (1 in 20 runs;
    0 in 20 with a period). The pause after the chunk (ends_with) is unchanged;
    dashes inside a chunk are left alone.

    ponytail: also applies where the clause splitter cut a long sentence at a
    dash, which then reads as a full stop; a comma there if that ever sounds wrong.
    """
    return chunk._replace(text=_TRAILING_DASH_RE.sub(r".\1", chunk.text))


def chunk_text(text: str, max_chars: int, min_chars: int = 0) -> List[Chunk]:
    chunks: List[Chunk] = []
    for paragraph in split_paragraphs(text):
        if _is_scene_break(paragraph):
            if chunks:
                chunks[-1] = chunks[-1]._replace(ends_with="scene")
            continue
        chunks.extend(_chunk_paragraph(paragraph, max_chars, min_chars))
    return [_close_interrupted(c) for c in _merge_short(chunks, min_chars)]

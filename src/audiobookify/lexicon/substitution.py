import re
from typing import List


def apply_lexicon(text: str, entries: List) -> str:
    for entry in sorted(entries, key=lambda e: -len(e.term)):
        pattern = r"\b" + re.escape(entry.term) + r"\b"
        text = re.sub(pattern, lambda m: entry.override_value, text, flags=re.IGNORECASE)
    return text

import re
from typing import List


def apply_lexicon(text: str, entries: List) -> str:
    for entry in entries:
        pattern = r"\b" + re.escape(entry.term) + r"\b"
        text = re.sub(pattern, entry.override_value, text, flags=re.IGNORECASE)
    return text

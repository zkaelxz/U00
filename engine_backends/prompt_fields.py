"""Small helpers for the LLM review passes: the language named in a prompt and
reading the line id the model echoes back."""
import re

from core import LANGUAGE_NAMES


def source_language_phrase(source_language=None) -> str:
    return LANGUAGE_NAMES.get(source_language, "source-language")


def parse_line_id(value):
    """2, "2", " 2 " and "[2]" (the prompt's own format) all give 2; else None."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    m = re.fullmatch(r"\s*\[?\s*(-?\d+)\s*\]?\s*", value) if isinstance(value, str) else None
    return int(m.group(1)) if m else None

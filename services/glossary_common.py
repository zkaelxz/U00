"""
services/glossary_common.py -- helpers shared by glossary_service and
glossary_extract_service.

They live below both so the two modules never import each other: a
cycle there made the import order matter. No FastAPI import.
"""
from typing import Optional

from services.service_errors import InvalidInputError, UnsupportedOperationError
from services.ownership_service import require_drama as _drama  # noqa: F401  (the glossary services import it from here)

MAX_TERM_LEN = 200
MAX_NOTES_LEN = 1000

# Term conventions live as a trailing section of the project instructions text,
# so the translate prompt carries them with no second channel, and backup and
# import already copy them. frontend/.../NovelInstructionsTab.tsx splits on the same heading.
TERM_CONVENTIONS_HEADING = "Term conventions:"


def split_term_conventions(instructions: str) -> tuple:
    """(prose, conventions) from a project instructions text; the last heading wins
    so prose that quotes the heading earlier isn't cut."""
    text = instructions or ""
    marker = "\n" + TERM_CONVENTIONS_HEADING + "\n"
    at = ("\n" + text).rfind(marker)
    if at < 0:
        return text.strip(), ""
    return text[:max(at - 1, 0)].strip(), text[at + len(marker) - 1:].strip()


def _series_id(drama: dict, *, required: bool) -> Optional[int]:
    sid = drama.get("series_id")
    if not sid and required:
        raise UnsupportedOperationError(
            "This title isn't part of a series; add it to a series first.")
    return sid or None


def _text(value, field: str, max_len: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise InvalidInputError(f"{field} must be text.")
    value = value.strip()
    if required and not value:
        raise InvalidInputError(f"{field} is required.")
    if len(value) > max_len:
        raise InvalidInputError(f"{field} is too long (max {max_len} characters).")
    return value

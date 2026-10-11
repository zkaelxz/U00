"""
services/glossary_common.py -- helpers shared by glossary_service and
glossary_extract_service.

They live below both so the two modules never import each other: a
cycle there made the import order matter. No FastAPI import.
"""
from typing import Optional

import db
from services.service_errors import InvalidInputError, NotFoundError, UnsupportedOperationError

MAX_TERM_LEN = 200
MAX_NOTES_LEN = 1000


def _drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError("Title not found.")
    return drama


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

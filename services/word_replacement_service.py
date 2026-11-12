"""services/word_replacement_service.py -- the series' word replacements: list, save, delete.

Owned by the drama's series like the glossary terms; a title with no series gets one of its
own on its first save (see glossary_common._series_id)."""
import db
from lib.errors import InvalidInputError, NotFoundError
from services.glossary_common import MAX_TERM_LEN, _drama, _series_id

MAX_REPLACEMENTS = 500


def list_word_replacements(drama_id: int) -> list:
    sid = _series_id(_drama(drama_id), required=False)
    return db.list_word_replacements(sid) if sid else []


def save_word_replacement(drama_id: int, find_text: str, replace_text: str) -> dict:
    if not isinstance(find_text, str) or not isinstance(replace_text, str):
        raise InvalidInputError("find_text and replace_text must be text.")
    find_text, replace_text = find_text.strip(), replace_text.strip()
    if not find_text:
        raise InvalidInputError("Say which word to replace.")
    if len(find_text) > MAX_TERM_LEN or len(replace_text) > MAX_TERM_LEN:
        raise InvalidInputError(f"Keep each side under {MAX_TERM_LEN} characters.")
    if find_text.casefold() == replace_text.casefold():
        raise InvalidInputError("The replacement is the same word.")
    sid = _series_id(_drama(drama_id), required=True)
    existing = db.list_word_replacements(sid)
    if len(existing) >= MAX_REPLACEMENTS and all(r["find_text"] != find_text for r in existing):
        raise InvalidInputError(f"A series can have at most {MAX_REPLACEMENTS} word replacements.")
    return db.save_word_replacement(sid, find_text, replace_text)


def delete_word_replacement(drama_id: int, replacement_id: int) -> dict:
    sid = _series_id(_drama(drama_id), required=False)
    if not sid or not db.delete_word_replacement(sid, replacement_id):
        raise NotFoundError("Word replacement not found.")
    return {"deleted": [replacement_id]}

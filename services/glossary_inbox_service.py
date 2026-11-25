"""
services/glossary_inbox_service.py -- names the app finds by itself after a translate run,
kept per title for the user to Add (with an English spelling) or Ignore.

The finder is glossary_variants_service.discover_names: offline and free, so refreshing
costs nothing and runs without being asked. Nothing is ever added to the glossary here
without the user's Add. Ignored names are remembered per title so they never come back.

Stored in dramas.glossary_inbox as {"items": [cluster...], "rejected": [term...]}.
No FastAPI import.
"""
import json
import logging
import threading

import db
from lib.errors import ConflictError, NotFoundError
from services import glossary_service, glossary_variants_service
from services.glossary_common import _drama

log = logging.getLogger(__name__)

# One process owns the library, and every write here is a read-modify-write of one column.
_lock = threading.Lock()

MAX_REJECTED = 1000   # an ignore list that only grows must not slow every refresh


def _load(drama: dict) -> dict:
    try:
        data = json.loads(drama.get("glossary_inbox") or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    items, rejected = data.get("items"), data.get("rejected")
    # A hand-edited or foreign backup could carry any shape; keep only entries the routes can serve.
    items = [c for c in items if isinstance(c, dict) and isinstance(c.get("suggested_original"), str)
             and isinstance(c.get("spellings"), list)
             and all(isinstance(x, dict) and isinstance(x.get("text"), str) for x in c["spellings"])] \
        if isinstance(items, list) else []
    return {"items": items,
            "rejected": [t for t in rejected if isinstance(t, str)] if isinstance(rejected, list) else []}


def _save(drama_id: int, data: dict) -> None:
    db.update_drama(drama_id, glossary_inbox=json.dumps(data, ensure_ascii=False))


def _view(data: dict) -> dict:
    return {"items": data["items"], "count": len(data["items"])}


def get_inbox(drama_id: int) -> dict:
    return _view(_load(_drama(drama_id)))


def _key(text: str) -> str:
    return glossary_variants_service._norm(text)


def _known_spellings(drama_id: int) -> set:
    return {_key(t) for term in glossary_service.list_glossary_terms(drama_id)
            for t in [term["term_original"], *term["aliases"]]}


def refresh(drama_id: int) -> dict:
    """Rescans the title's source lines and replaces the waiting names with what is still
    unknown and not ignored. A cluster with any spelling already a term or alias is left out."""
    _drama(drama_id)
    # The scan takes seconds and runs outside the lock; rejections are read again under it.
    found = glossary_variants_service.discover_names(drama_id)
    with _lock:
        data = _load(_drama(drama_id))
        rejected = {_key(t) for t in data["rejected"]}
        data["items"] = [
            c for c in found.get("clusters", [])
            if not c.get("existing_term") and not rejected & {_key(s["text"]) for s in c["spellings"]}]
        _save(drama_id, data)
    return _view(data)


def refresh_quietly(drama_id: int) -> None:
    """For a job's last step: a failed scan must never fail the translation that triggered it."""
    try:
        refresh(drama_id)
    except Exception as exc:  # noqa: BLE001 - best effort by design
        log.warning("Glossary inbox refresh failed for title %s: %s", drama_id, type(exc).__name__)


def _take(data: dict, term: str) -> dict:
    item = next((c for c in data["items"] if c["suggested_original"] == term), None)
    if item is None:
        raise NotFoundError("That name is no longer in the inbox.")
    return item


def accept(drama_id: int, term: str, translation: str) -> dict:
    """Saves the name as a glossary term with the English the user typed; the other
    spellings in its cluster become aliases so every one of them matches. A name the glossary
    learned since the scan is refused, so Add never overwrites an existing term."""
    with _lock:
        data = _load(_drama(drama_id))
        item = _take(data, term)
        known = _known_spellings(drama_id)
        if known & {_key(s["text"]) for s in item["spellings"]}:
            data["items"] = [c for c in data["items"] if c is not item]
            _save(drama_id, data)
            raise ConflictError("The glossary already has that name. Edit it in Terms instead.")
        aliases = [s["text"] for s in item["spellings"] if s["text"] != term]
        glossary_service.upsert_glossary_term(
            drama_id, {"term_original": term, "term_translation": translation, "aliases": aliases})
        data["items"] = [c for c in data["items"] if c is not item]
        _save(drama_id, data)
        return _view(data)


def reject(drama_id: int, term: str) -> dict:
    with _lock:
        data = _load(_drama(drama_id))
        item = _take(data, term)
        data["items"] = [c for c in data["items"] if c is not item]
        data["rejected"] = list(dict.fromkeys([*data["rejected"], *(s["text"] for s in item["spellings"])]))[-MAX_REJECTED:]
        _save(drama_id, data)
        return _view(data)

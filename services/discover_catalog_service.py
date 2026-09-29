"""
services/discover_catalog_service.py -- the Discover "Known titles" catalog
without the Streamlit tab (Migration Slice 55, spec slice D-1).

UI-free, plain dicts, errors from `service_errors`. No network and no LLM:
platform listing and search links are pure string building
(`known_sites`). Writes use whitelisted fields only, and the id is
verified to exist before every write or delete. Responses never carry a
path or a key.
"""

import db
import known_sites
import title_library
from services import drama_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

LANGUAGES = ("zh", "ja", "ko")
# The catalog also accepts "game" (schema comment) on top of the drama types.
TITLE_MEDIA_TYPES = tuple(drama_service.MEDIA_TYPE_OPTIONS) + ("game",)
SEARCH_LINK_FORMATS = ("audio_drama", "novel", "manhua", "manhwa", "manga")
MAX_ID = drama_service.MAX_ID
MAX_QUERY_LEN = 200

# field -> max length. Anything else in a create is rejected.
_TITLE_CAPS = {"title_original": 300, "title_en": 300, "author": 300, "tags": 500,
               "summary_en": 5000, "summary_original": 5000, "source_name": 100,
               "source_url": 2000}
_TITLE_FIELDS = tuple(_TITLE_CAPS) + ("language", "media_type")


def _check_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > MAX_ID:
        raise InvalidInputError("title id is out of range.")


def _check_query(name, value):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > MAX_QUERY_LEN:
        raise InvalidInputError(f"{name} must be text of at most {MAX_QUERY_LEN} characters.")
    return value.strip()


def _check_language(value, allow_blank=True):
    if (value == "" or value is None) and allow_blank:
        return ""
    if value not in LANGUAGES:
        raise InvalidInputError("language must be one of zh, ja, ko.",
                                details={"allowed": list(LANGUAGES)})
    return value


def _check_media_type(value, allow_blank=True):
    if (value == "" or value is None) and allow_blank:
        return ""
    if value not in TITLE_MEDIA_TYPES:
        raise InvalidInputError("Unknown media_type.", details={"allowed": list(TITLE_MEDIA_TYPES)})
    return value


def _public(row: dict) -> dict:
    return {k: row.get(k) for k in ("id",) + _TITLE_FIELDS + ("created_at",)}


def _find(title_id) -> dict:
    _check_id(title_id)
    for row in db.list_known_titles():
        if row["id"] == title_id:
            return row
    raise NotFoundError("No known title with that id.")


def list_titles(q="", language="", media_type="") -> dict:
    q = _check_query("q", q)
    language = _check_language(language)
    media_type = _check_media_type(media_type)
    total = len(db.list_known_titles())
    rows = db.list_known_titles(search=q, language=language, media_type=media_type)
    return {"titles": [_public(r) for r in rows], "total": total}


def create_title(fields: dict) -> dict:
    """Manual add. `title_original` is required; unknown keys are rejected."""
    if not isinstance(fields, dict):
        raise InvalidInputError("Expected an object of title fields.")
    unknown = sorted(set(fields) - set(_TITLE_FIELDS))
    if unknown:
        raise InvalidInputError("Unknown field(s): " + ", ".join(unknown[:5]) + ".",
                                details={"allowed": list(_TITLE_FIELDS)})
    clean = {}
    for name, cap in _TITLE_CAPS.items():
        value = fields.get(name, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise InvalidInputError(f"{name} must be text.")
        if len(value) > cap:
            raise InvalidInputError(f"{name} is too long (max {cap} characters).")
        clean[name] = value
    if not clean["title_original"].strip():
        raise InvalidInputError("title_original is required.")
    if clean["source_url"] and not clean["source_url"].startswith(("http://", "https://")):
        raise InvalidInputError("source_url must be empty or start with http:// or https://.")
    clean["source_name"] = clean["source_name"] or "manual"
    clean["language"] = _check_language(fields.get("language", ""), allow_blank=False)
    clean["media_type"] = _check_media_type(fields.get("media_type", ""), allow_blank=False)
    new_id = db.create_known_title(**clean)
    return _public(_find(new_id))


def delete_title(title_id, confirm=False) -> dict:
    if confirm is not True:
        raise InvalidInputError("Deleting a title needs confirm=true.")
    _find(title_id)
    db.delete_known_title(title_id)
    return {"deleted": True, "id": title_id}


def seed_titles() -> dict:
    """Idempotent: title_library skips starters already present."""
    return {"added": title_library.seed_known_titles(db), "total": len(db.list_known_titles())}


def _already_imported(t: dict):
    """Id of the existing drama matching this title (the tab's dup rule:
    same title_en, or same title_zh as the original), else None."""
    for d in db.list_dramas(search=t["title_en"] or t["title_original"]):
        if (t["title_en"] and d.get("title_en") == t["title_en"]) or \
                (t["title_original"] and d.get("title_zh") == t["title_original"]):
            return d["id"]
    return None


def import_to_library(title_id, principal=None) -> dict:
    """Creates a drama from a known title, owned by `principal` (auth B2)."""
    t = _find(title_id)
    existing = _already_imported(t)
    if existing is not None:
        raise ConflictError("This title is already in your Library.",
                            details={"drama_id": existing})
    media_type = t["media_type"] if t["media_type"] in drama_service.MEDIA_TYPE_OPTIONS else "other"
    return drama_service.create_drama(
        source_language=t["language"] or "",
        title_en=t["title_en"] or "",
        title_zh=(t["title_original"] or "") if t["language"] == "zh" else "",
        author=t["author"] or "", summary=t["summary_en"] or "",
        media_type=media_type, principal=principal)


def list_platforms(language="", content_type="") -> dict:
    language = _check_language(language)
    content_type = _check_query("content_type", content_type)
    sites = known_sites.list_sites(content_type=content_type or None, language=language or None)
    return {"platforms": [dict(s) for s in sites]}


def search_links(q, fmt="") -> dict:
    q = _check_query("q", q)
    if not q:
        raise InvalidInputError("q is required.")
    fmt = _check_query("format", fmt)
    if fmt and fmt not in SEARCH_LINK_FORMATS:
        raise InvalidInputError("Unknown format.", details={"allowed": list(SEARCH_LINK_FORMATS)})
    return {"links": known_sites.build_search_links(q, content_type=fmt or None)}

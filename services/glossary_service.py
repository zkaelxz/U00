"""
services/glossary_service.py -- series glossary, project/series
instructions, and the read-only option catalogues for the Translate
stage's config (`tabs/workspace_tab.py`, Translate config's "Project
instructions" box and "Series glossary & term handling" expander).

Glossary terms belong to a SERIES (`drama.series_id`), never a drama: a
drama with no series reads as an empty glossary and refuses term writes
and series instructions (UnsupportedOperationError). Every term write or
delete verifies the term belongs to the drama's own series first, since
the id-only db helpers (`db.delete_glossary_term`, `db.update_glossary_term`)
have no ownership check; a term of another series reads as NotFoundError.

aliases / banned_translations are lists of strings in this API and are
stored pipe-separated (db convention), so a "|" inside one is rejected.

Deliberately NOT here:
  - LLM glossary extraction (`tguide.extract_glossary_from_novel`,
    `extract_terms_llm`) -- a paid call needing engine/key resolution;
    a later slice.
  - Presets CRUD (`db.save_preset` etc.), series/drama creation,
    characters (services/characters_service.py), and the pre-translate
    glossary review gate.

No Streamlit or FastAPI import; no LLM calls.
"""
from typing import Optional

import db
import translation_guide as tguide
from translate_engines import WORKFLOW_TIERS
from services.service_errors import (
    ConflictError, InvalidInputError, NotFoundError, UnsupportedOperationError,
)

MAX_TERM_LEN = 200
MAX_NOTES_LEN = 1000
MAX_LIST_ITEMS = 50
MAX_ITEM_LEN = 200
MAX_INSTRUCTIONS_LEN = 5000


def _drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError("Drama not found.")
    return drama


def _series_id(drama: dict, *, required: bool) -> Optional[int]:
    sid = drama.get("series_id")
    if not sid and required:
        raise UnsupportedOperationError(
            "This drama isn't part of a series; add it to a series first.")
    return sid or None


def _split(value) -> list:
    return [p.strip() for p in (value or "").split("|") if p.strip()]


def _serialize(row: dict) -> dict:
    return {
        "id": row["id"],
        "term_original": row.get("term_original") or "",
        "term_translation": row.get("term_translation") or "",
        "notes": row.get("notes") or "",
        "category": row.get("category") or None,
        "policy": row.get("policy") or None,
        "enforce_exact": bool(row.get("enforce_exact")),
        "aliases": _split(row.get("aliases")),
        "banned_translations": _split(row.get("banned_translations")),
    }


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


def _str_list(value, field: str) -> str:
    """Validate a list of non-empty strings; return the pipe-joined form."""
    if not isinstance(value, (list, tuple)):
        raise InvalidInputError(f"{field} must be a list of strings.")
    if len(value) > MAX_LIST_ITEMS:
        raise InvalidInputError(f"{field} has too many entries (max {MAX_LIST_ITEMS}).")
    out = []
    for item in value:
        if not isinstance(item, str):
            raise InvalidInputError(f"{field} must be a list of strings.")
        item = item.strip()
        if not item:
            raise InvalidInputError(f"{field} entries must not be empty.")
        if len(item) > MAX_ITEM_LEN:
            raise InvalidInputError(f"A {field} entry is too long (max {MAX_ITEM_LEN} characters).")
        if "|" in item:
            raise InvalidInputError(f"{field} entries must not contain '|'.")
        out.append(item)
    return "|".join(out)


def _owned_term(series_id: int, term_id) -> dict:
    if isinstance(term_id, bool) or not isinstance(term_id, int):
        raise NotFoundError("Glossary term not found.")
    for row in db.list_glossary_terms(series_id):
        if row["id"] == term_id:
            return row
    raise NotFoundError("Glossary term not found.")


def list_glossary_terms(drama_id: int) -> list:
    drama = _drama(drama_id)
    sid = _series_id(drama, required=False)
    if sid is None:
        return []
    return [_serialize(r) for r in db.list_glossary_terms(sid)]


def upsert_glossary_term(drama_id: int, term_fields: dict) -> dict:
    """Create or update a term in the drama's series glossary.

    With an "id" the term is updated in place (its original text may be
    corrected, like the tab's edit form); without one it is keyed on
    (series, term_original) exactly like the tab's add form. Omitted
    optional fields keep their stored value on update. Returns the saved
    term."""
    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    if not isinstance(term_fields, dict):
        raise InvalidInputError("Term fields must be an object.")

    existing = _owned_term(sid, term_fields["id"]) if term_fields.get("id") is not None else None
    base = _serialize(existing) if existing else {}

    def pick(key, default=None):
        return term_fields[key] if key in term_fields else base.get(key, default)

    original = _text(pick("term_original"), "term_original", MAX_TERM_LEN, required=True)
    translation = _text(pick("term_translation"), "term_translation", MAX_TERM_LEN, required=True)
    notes = _text(pick("notes"), "notes", MAX_NOTES_LEN)

    category = pick("category")
    if category is not None and category not in tguide.TERM_CATEGORIES:
        raise InvalidInputError("Unknown category.")
    policy = pick("policy")
    if policy is not None and policy not in tguide.TERM_POLICIES:
        raise InvalidInputError("Unknown policy.")
    enforce = pick("enforce_exact", False)
    if not isinstance(enforce, bool):
        raise InvalidInputError("enforce_exact must be true or false.")
    aliases = _str_list(pick("aliases", []), "aliases")
    banned = _str_list(pick("banned_translations", []), "banned_translations")

    if existing:
        clash = next((r for r in db.list_glossary_terms(sid)
                      if r["term_original"] == original and r["id"] != existing["id"]), None)
        if clash:
            raise ConflictError("Another term already uses that original text.")
        db.update_glossary_term(existing["id"], original, translation, notes, category, policy,
                                enforce, aliases, banned)
        saved_id = existing["id"]
    else:
        db.upsert_glossary_term(sid, original, translation, notes, category, policy, enforce,
                                aliases, banned)
        saved_id = next(r["id"] for r in db.list_glossary_terms(sid)
                        if r["term_original"] == original)
    return _serialize(_owned_term(sid, saved_id))


def delete_glossary_term(drama_id: int, term_id: int, confirm: bool = False) -> None:
    """Delete one term. Mirrors the tab's confirm-before-delete checkbox
    (Step 71): confirm must be True."""
    drama = _drama(drama_id)
    sid = _series_id(drama, required=False)
    if sid is None:
        raise NotFoundError("Glossary term not found.")
    term = _owned_term(sid, term_id)
    if confirm is not True:
        raise InvalidInputError("Deleting a glossary term needs confirm=true.")
    db.delete_glossary_term(term["id"])


def get_instructions(drama_id: int) -> dict:
    drama = _drama(drama_id)
    return {
        "project_instructions": drama.get("project_instructions") or "",
        "series_instructions": drama.get("series_instructions") or "",
    }


def set_project_instructions(drama_id: int, text: str) -> dict:
    _drama(drama_id)
    text = _text(text, "project_instructions", MAX_INSTRUCTIONS_LEN)
    db.update_drama(drama_id, project_instructions=text)
    return get_instructions(drama_id)


def set_series_instructions(drama_id: int, text: str) -> dict:
    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    text = _text(text, "series_instructions", MAX_INSTRUCTIONS_LEN)
    db.update_series_instructions(sid, text)
    return get_instructions(drama_id)


def get_catalogues() -> dict:
    """Static option lists for the Translate config UI (no secrets: the
    workflow tiers expose only key/label/engine/model/flags)."""
    return {
        "style_presets": [{"key": k, "label": v["label"]}
                          for k, v in tguide.STYLE_PRESETS.items()],
        "term_categories": [{"key": k, "label": v} for k, v in tguide.TERM_CATEGORIES.items()],
        "term_policies": [{"key": k, "label": v["label"], "example": v.get("example", "")}
                          for k, v in tguide.TERM_POLICIES.items()],
        "workflow_tiers": [{"key": k, "label": v["label"],
                            "translation_engine": v["translation_engine"],
                            "engine_model": v["engine_model"],
                            "reflect": bool(v["reflect"]), "auto_qc": bool(v["auto_qc"])}
                           for k, v in WORKFLOW_TIERS.items()],
    }

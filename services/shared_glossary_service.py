"""
services/shared_glossary_service.py -- shared glossary groups: a named
glossary (e.g. one creator's slang and ASR mishearings) that several series
inherit.

A series joins at most one group. Its effective glossary is the group's terms
overlaid by its own, the series winning on the same source text
(db.list_effective_glossary_terms, which every consumer reads). Leaving a group
or deleting it never touches a series' own terms. Groups are visible by name to
every signed-in reader; only the creator, or an admin at the PC, changes a
group's terms (ownership_service.can_edit_shared_group); its creator or any
admin, away too, may delete it. Deactivating a member unlinks their groups from
every series (db.detach_shared_glossary_owner_groups). Joining and leaving need
the series to be editable.

No FastAPI import.
"""
import sqlite3

import db
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services import glossary_service, ownership_service
from services.series_service import MAX_NAME_LEN, term_key

MAX_GROUP_TERMS = 5000


def _view(principal, group: dict) -> dict:
    return {"id": group["id"], "name": group["name"], "term_count": group["term_count"],
            "series_count": group["series_count"],
            "can_edit": ownership_service.can_edit_shared_group(principal, group)}


def _group(group_id) -> dict:
    group = db.get_shared_glossary_group(group_id) if isinstance(group_id, int) else None
    if group is None:
        raise NotFoundError("Shared glossary not found.")
    return group


def _editable_group(principal, group_id) -> dict:
    group = _group(group_id)
    if not ownership_service.can_edit_shared_group(principal, group):
        raise NotFoundError("Shared glossary not found.")
    return group


def list_groups(principal) -> dict:
    return {"items": [_view(principal, g) for g in db.list_shared_glossary_groups()]}


def create_group(principal, name) -> dict:
    if not isinstance(name, str) or not name.strip():
        raise InvalidInputError("A name is required.")
    name = name.strip()
    if len(name) > MAX_NAME_LEN:
        raise InvalidInputError(f"A name is at most {MAX_NAME_LEN} characters.")
    wanted = term_key(name)
    if any(term_key(g["name"]) == wanted for g in db.list_shared_glossary_groups()):
        raise ConflictError("A shared glossary with that name already exists.")
    try:
        group_id = db.create_shared_glossary_group(name, ownership_service.user_id(principal))
    except sqlite3.IntegrityError:
        raise ConflictError("A shared glossary with that name already exists.") from None
    return _view(principal, _group(group_id))


def delete_group(principal, group_id) -> dict:
    """Its creator or any admin (at the PC or away) deletes a group; an admin
    needs no admin_override here because the group is household-wide, not one
    member's private work."""
    group = _group(group_id)
    if not (principal is None or principal.get("is_admin") or principal.get("is_local_owner")
            or ownership_service.can_edit_shared_group(principal, group)):
        raise NotFoundError("Shared glossary not found.")
    db.delete_shared_glossary_group(group["id"])
    return {"deleted": True, "group_id": group["id"], "series_unlinked": group["series_count"]}


def list_terms(principal, group_id) -> list:
    group = _group(group_id)
    return [glossary_service._serialize(r) for r in db.list_shared_glossary_terms(group["id"])]


def save_term(principal, group_id, term_fields: dict) -> dict:
    """Create or update a group term, with the same field rules as a series
    term. With "id" it is updated in place; without, it is keyed on the original
    text and an existing term of that text keeps the fields not sent."""
    group = _editable_group(principal, group_id)
    if not isinstance(term_fields, dict):
        raise InvalidInputError("Term fields must be an object.")
    terms = db.list_shared_glossary_terms(group["id"])
    existing = None
    if term_fields.get("id") is not None:
        existing = next((t for t in terms if t["id"] == term_fields["id"]), None)
        if existing is None:
            raise NotFoundError("Glossary term not found.")
    elif isinstance(term_fields.get("term_original"), str):
        existing = next((t for t in terms if t["term_original"] == term_fields["term_original"].strip()), None)
    fields = glossary_service.clean_term_fields(
        term_fields, glossary_service._serialize(existing) if existing else {})
    if existing is not None and term_fields.get("id") is not None and any(
            t["term_original"] == fields[0] and t["id"] != existing["id"] for t in terms):
        raise ConflictError("Another term already uses that original text.")
    if existing is None and len(terms) >= MAX_GROUP_TERMS:
        raise InvalidInputError(f"A shared glossary holds at most {MAX_GROUP_TERMS} terms.")
    db.save_shared_glossary_term(group["id"], existing["id"] if existing else None, *fields)
    saved = next(t for t in db.list_shared_glossary_terms(group["id"]) if t["term_original"] == fields[0])
    return glossary_service._serialize(saved)


def delete_term(principal, group_id, term_id) -> dict:
    group = _editable_group(principal, group_id)
    if isinstance(term_id, bool) or not isinstance(term_id, int) \
            or not db.delete_shared_glossary_term(group["id"], term_id):
        raise NotFoundError("Glossary term not found.")
    return {"deleted": True}


def series_view(principal, series_id) -> dict:
    """The series' group (if any) and its effective glossary with each term's origin."""
    ownership_service.require_visible(principal, "series", series_id)
    group_id = db.get_series_shared_group_id(series_id)
    group = db.get_shared_glossary_group(group_id) if group_id else None
    terms = [{**glossary_service._serialize(r), "origin": r["origin"], "origin_name": r["origin_name"]}
             for r in db.list_effective_glossary_terms(series_id)]
    return {"series_id": series_id, "group": _view(principal, group) if group else None,
            "terms": terms, "can_edit": ownership_service.can_edit(principal, "series", series_id)}


def set_series_group(principal, series_id, group_id) -> dict:
    """Join `group_id`, or leave the current group when None. Joining replaces
    any group the series had; the series' own terms are never changed."""
    ownership_service.require_editable(principal, "series", series_id)
    if group_id is not None:
        _group(group_id)
    db.set_series_shared_group(series_id, group_id)
    return series_view(principal, series_id)

"""
services/language_pack_service.py -- which language packs a title uses, and
the prompt text they add.

Choices live in app_settings (no schema change): one JSON per title under
`language_packs.title.<id>`, and one per source language under
`language_packs.default.<lang>` that a title without its own choice follows.
Both are {"packs": {pack id: style}}. Packs are off until someone turns them
on. The app, the CLI and every job reach the prompt through
`block_for`, so they cannot differ.
"""

import db
import language_packs
from services import ownership_service
from services.service_errors import InvalidInputError, NotFoundError

_TITLE_KEY = "language_packs.title.{}"
_DEFAULT_KEY = "language_packs.default.{}"


def _drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError("Drama not found.")
    return drama


def _language(drama: dict) -> str:
    return drama.get("source_language") or "zh"


def _summary(pack: dict) -> dict:
    return {"id": pack["id"], "version": pack["version"], "language": pack["language"],
            "title": pack["title"], "description": pack["description"],
            "entry_count": len(pack["entries"]), "styles": pack["styles"]}


def list_packs(language: str = "") -> list:
    packs = language_packs.packs_for_language(language) if language else language_packs.all_packs().values()
    return [_summary(p) for p in packs]


def get_pack(pack_id: str) -> dict:
    pack = language_packs.all_packs().get(pack_id)
    if pack is None:
        raise NotFoundError("Language pack not found.")
    return {**_summary(pack), "entries": pack["entries"]}


def _clean_choice(value, language: str) -> dict:
    """{"packs": {id: style}} from a client body; unknown packs, packs for
    another language and unknown styles are refused, not dropped silently."""
    chosen = (value or {}).get("packs") if isinstance(value, dict) else None
    if not isinstance(chosen, dict):
        raise InvalidInputError("Say which packs to use.")
    allowed = {p["id"]: p for p in language_packs.packs_for_language(language)}
    clean = {}
    for pack_id, style in chosen.items():
        pack = allowed.get(pack_id)
        if pack is None:
            raise InvalidInputError(f"'{pack_id}' is not a language pack for this language.")
        if style in (None, ""):
            style = pack["styles"]["default"]
        if pack["styles"]["options"] and style not in pack["styles"]["options"]:
            raise InvalidInputError(f"'{style}' is not a style of the pack '{pack_id}'.")
        clean[pack_id] = style or None
    return {"packs": clean}


def _stored_choice(drama_id: int, language: str):
    """(choice, uses_default): the title's own choice, else the language default, else nothing."""
    own = db.get_app_setting(_TITLE_KEY.format(drama_id))
    if isinstance(own, dict):
        return own, False
    default = db.get_app_setting(_DEFAULT_KEY.format(language))
    return (default if isinstance(default, dict) else {"packs": {}}), True


def get_title_packs(drama_id: int, principal=None) -> dict:
    ownership_service.require_visible(principal, "drama", drama_id)
    drama = _drama(drama_id)
    language = _language(drama)
    choice, uses_default = _stored_choice(drama_id, language)
    on = choice.get("packs") or {}
    return {"source_language": language, "uses_default": uses_default,
            "packs": [{**_summary(p), "enabled": p["id"] in on,
                       "style": on.get(p["id"]) or p["styles"]["default"] or None}
                      for p in language_packs.packs_for_language(language)]}


def set_title_packs(drama_id: int, body: dict, principal=None) -> dict:
    ownership_service.require_editable(principal, "drama", drama_id)
    language = _language(_drama(drama_id))
    db.set_app_setting(_TITLE_KEY.format(drama_id), _clean_choice(body, language))
    return get_title_packs(drama_id, principal)


def set_language_default(language: str, body: dict) -> dict:
    if language not in ("zh", "ja", "ko"):
        raise InvalidInputError("The language must be zh, ja or ko.")
    choice = _clean_choice(body, language)
    db.set_app_setting(_DEFAULT_KEY.format(language), choice)
    return {"language": language, "packs": sorted(choice["packs"])}


def entries_for_text(drama: dict, text: str, user_terms=None) -> list:
    """The matching pack entries for any text of this title. For code that
    builds its own prompt (a Live path, a one-line helper)."""
    choice, _ = _stored_choice(drama["id"], _language(drama))
    # A choice saved before the source language changed may name packs of the
    # old language; the UI only lists the new language's, so those could never
    # be switched off.
    offered = {p["id"] for p in language_packs.packs_for_language(_language(drama))}
    packs = {k: v for k, v in (choice.get("packs") or {}).items() if k in offered}
    return language_packs.matching_entries(packs, text, user_terms, language=_language(drama)) if packs else []


def block_for(drama: dict, lines, user_terms=None) -> str:
    """The pack section of a run's prompt ("" or a blank line then the block,
    ready to append to the guidelines), chosen from every line the run
    translates so all of its batches share one identical (cacheable) prompt."""
    if not drama or drama.get("id") is None:
        return ""
    text = "\n".join(getattr(ln, "zh", "") or "" for ln in lines)
    block = language_packs.build_pack_block(entries_for_text(drama, text, user_terms))
    return "\n\n" + block if block else ""

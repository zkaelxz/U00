"""
services/series_service.py -- the Series page: a series' titles and counts,
rename, delete, and copying glossary terms in from another series.

Ownership (services/ownership_service.py): reading needs the series to be
visible; rename needs it editable (a private series is only ever editable
by its owner or an admin at the PC); delete needs its owner or an admin at
the PC (require_series_owner); a copy needs the destination editable and the
source visible and, if private, the caller's own (require_copy_source).

Delete keeps the titles (they become "No series") and drops the series'
glossary, people, translation memory, dismissals and instructions; it is
refused while a job runs for any of its titles. Copying never overwrites
or deletes a term: a source term is skipped when the destination already
has one with the same original text after trim + NFKC + case folding.

No FastAPI import.
"""
import sqlite3
import unicodedata

import db
from services import auth_service, drama_service, ownership_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

MAX_NAME_LEN = 300           # drama_service.MAX_NAME_LEN, the "New series" limit
PREVIEW_TERMS = 50
_DELETE_WORD = "DELETE"
_JOB_RUNNING = ("A job is running for a title in this series. Wait for it to finish or "
                "cancel it before deleting the series.")
_COUNT_KEYS = (("titles", "title_count"), ("glossary_terms", "glossary_term_count"),
               ("characters", "character_count"),
               ("translation_memory", "translation_memory_count"),
               ("dismissals", "dismissal_count"))


def term_key(text) -> str:
    """How two original texts are compared for "already there"."""
    return unicodedata.normalize("NFKC", str(text or "")).strip().casefold()


def _clean_name(name) -> str:
    if not isinstance(name, str):
        raise InvalidInputError("A series name must be text.")
    name = name.strip()
    if not name:
        raise InvalidInputError("A series name is required.")
    if len(name) > MAX_NAME_LEN:
        raise InvalidInputError(f"A series name is at most {MAX_NAME_LEN} characters.")
    if any(unicodedata.category(ch) == "Cc" for ch in name):
        raise InvalidInputError("A series name can't contain control characters.")
    return name


def _series_row(principal, series_id) -> dict:
    ownership_service.require_visible(principal, "series", series_id)
    row = next((s for s in db.list_series() if s["id"] == series_id), None)
    if row is None:  # deleted between the check and the read
        raise NotFoundError("Series not found.")
    return row


def _counts(series_id) -> dict:
    counts = db.series_content_counts(series_id)
    if counts is None:
        raise NotFoundError("Series not found.")
    return counts


def list_series(principal=None) -> list:
    """Every series the principal may see, with its title and term counts
    (unlike library_service.list_series_with_dramas, which keeps only series
    with two or more titles)."""
    uid = ownership_service.user_id(principal)
    out = []
    for s in db.list_series(visible_to=ownership_service.visible_to_filter(principal)):
        counts = db.series_content_counts(s["id"])
        if counts is None:
            continue
        out.append({"id": s["id"], "name": s["name"], "is_private": bool(s.get("is_private")),
                    "owned_by_me": uid is not None and s.get("owner_user_id") == uid,
                    "title_count": counts["titles"],
                    "glossary_term_count": counts["glossary_terms"]})
    return out


def get_series_page(principal, series_id) -> dict:
    row = _series_row(principal, series_id)
    counts = _counts(series_id)
    uid = ownership_service.user_id(principal)
    titles = [{"id": d["id"], "title_en": d.get("title_en"), "title_zh": d.get("title_zh"),
               "media_type": d.get("media_type"), "status": d.get("status"),
               "episode_number": d.get("episode_number"),
               "line_count": counts["title_lines"].get(d["id"], 0)}
              for d in db.list_dramas_by_series(series_id)]
    return {"id": row["id"], "name": row["name"], "is_private": bool(row.get("is_private")),
            "owned_by_me": uid is not None and row.get("owner_user_id") == uid,
            "can_edit": ownership_service.can_edit(principal, "series", series_id),
            "has_instructions": counts["has_instructions"],
            "glossary_term_count": counts["glossary_terms"],
            "character_count": counts["characters"],
            "titles": titles}


def rename_series(principal, series_id, name) -> dict:
    """Trimmed, at most MAX_NAME_LEN characters. ConflictError when another
    series the caller can see has the same name ignoring case and width, or
    when one they can't see has exactly this name (series.name is unique
    across the library)."""
    ownership_service.require_editable(principal, "series", series_id)
    name = _clean_name(name)
    key = term_key(name)
    for s in db.list_series(visible_to=ownership_service.visible_to_filter(principal)):
        if s["id"] != series_id and term_key(s["name"]) == key:
            raise ConflictError(f"There is already a series called “{s['name']}”.")
    try:
        renamed = db.rename_series(series_id, name)
    except sqlite3.IntegrityError:
        raise ConflictError("That series name is taken.") from None
    if not renamed:
        raise NotFoundError("Series not found.")
    return {"id": series_id, "name": name}


def _job_running(series_id) -> bool:
    return any(drama_service.job_running_for_drama(d["id"])
               for d in db.list_dramas_by_series(series_id))


def _holds_anything(counts) -> bool:
    return counts["has_instructions"] or any(counts[k] for k, _ in _COUNT_KEYS)


def delete_preview(principal, series_id) -> dict:
    """What deleting this series would do, for the confirm prompt."""
    ownership_service.require_series_owner(principal, series_id)
    row = _series_row(principal, series_id)
    counts = _counts(series_id)
    out = {"series_id": series_id, "name": row["name"],
           "has_instructions": counts["has_instructions"],
           "needs_typed_confirm": _holds_anything(counts),
           "job_running": _job_running(series_id)}
    out.update({public: counts[key] for key, public in _COUNT_KEYS})
    return out


def delete_series(principal, series_id, confirm=False, confirm_text="") -> dict:
    """Order: unknown, invisible or not-owned -> 404/403; then a series that
    holds anything needs confirm=True and confirm_text "DELETE" (an empty one
    only confirm=True) -> InvalidInputError; then a job on any of its titles
    -> ConflictError. Writes an audit entry with counts only (no names)."""
    ownership_service.require_series_owner(principal, series_id)
    counts = _counts(series_id)
    if confirm is not True:
        raise InvalidInputError("Deleting a series needs confirm=true.")
    if _holds_anything(counts) and confirm_text != _DELETE_WORD:
        raise InvalidInputError("This series holds titles or glossary data: deleting it needs "
                                "confirm_text set to the word DELETE, in capitals.")
    if _job_running(series_id):
        raise ConflictError(_JOB_RUNNING)
    deleted = db.delete_series(series_id)
    if deleted is None:
        raise NotFoundError("Series not found.")
    auth_service.write_audit(
        ownership_service.user_id(principal), "series.delete",
        f"series {series_id}: titles={deleted['titles']} terms={deleted['glossary_terms']} "
        f"characters={deleted['characters']} memory={deleted['translation_memory']} "
        f"dismissals={deleted['dismissals']} instructions={int(deleted['has_instructions'])}")
    return {"deleted": True, "series_id": series_id, "titles_kept": deleted["titles"],
            "glossary_terms_deleted": deleted["glossary_terms"],
            "characters_deleted": deleted["characters"]}


def _plan_copy(principal, series_id, source_series_id):
    """(new source terms, skipped source terms), both in source order."""
    ownership_service.require_editable(principal, "series", series_id)
    ownership_service.require_copy_source(principal, source_series_id)
    if series_id == source_series_id:
        raise InvalidInputError("Pick a different series to copy from.")
    taken = {term_key(t["term_original"]) for t in db.list_glossary_terms(series_id)}
    new, skipped = [], []
    for t in db.list_glossary_terms(source_series_id):
        key = term_key(t["term_original"])
        if not key or key in taken:
            skipped.append(t)
            continue
        taken.add(key)
        new.append(t)
    return new, skipped


def copy_terms_preview(principal, series_id, source_series_id) -> dict:
    new, skipped = _plan_copy(principal, series_id, source_series_id)
    return {"series_id": series_id, "source_series_id": source_series_id,
            "new_count": len(new), "skipped_count": len(skipped),
            "new_terms": [t["term_original"] for t in new[:PREVIEW_TERMS]],
            "skipped_terms": [t["term_original"] for t in skipped[:PREVIEW_TERMS]]}


def copy_terms(principal, series_id, source_series_id) -> dict:
    """Adds the source's terms the destination lacks. The plan is made again
    here, so a term added since the preview is skipped, and the insert itself
    never overwrites (insert_glossary_term_if_absent)."""
    new, skipped = _plan_copy(principal, series_id, source_series_id)
    added = 0
    for t in new:
        if not db.insert_glossary_term_if_absent(
                series_id, t["term_original"], t.get("term_translation") or "",
                notes=t.get("notes") or "", category=t.get("category"), policy=t.get("policy"),
                enforce_exact=bool(t.get("enforce_exact"))):
            skipped.append(t)
            continue
        added += 1
        if t.get("aliases") or t.get("banned_translations"):
            _copy_extras(series_id, t)
    return {"series_id": series_id, "source_series_id": source_series_id,
            "added": added, "skipped": len(skipped)}


def _copy_extras(series_id, t) -> None:
    # insert_glossary_term_if_absent has no aliases/banned columns; fill
    # them on the row just added, rather than lose them.
    row = next((r for r in db.list_glossary_terms(series_id)
                if r["term_original"] == t["term_original"]), None)
    if row is None:
        return
    db.update_glossary_term(row["id"], row["term_original"], row["term_translation"],
                            notes=row.get("notes") or "", category=row.get("category"),
                            policy=row.get("policy"), enforce_exact=bool(row.get("enforce_exact")),
                            aliases=t.get("aliases"),
                            banned_translations=t.get("banned_translations"))

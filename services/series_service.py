"""
services/series_service.py -- the Series page: a series' titles and counts,
rename, delete, and copying glossary terms in from another series.

Ownership (services/ownership_service.py): reading needs the series to be
visible, and lists only the titles the viewer may see; rename needs it
editable (a private series is only ever editable by its owner or an admin
at the PC); delete needs its owner or an admin at the PC
(require_series_owner); a copy needs the destination editable and the
source visible and, if private, the caller's own (require_copy_source).

Delete keeps the titles (they become "No series") and drops the series'
glossary, people, translation memory, dismissals, learned style profile and
instructions; it is refused while a job runs for any of its titles, and the
database refuses it if the series changed after the counts were read.
Copying never overwrites or deletes a term: a source term is skipped when
the destination already has one with the same original text after trim +
NFKC + case folding; the copy is one transaction.

No FastAPI import.
"""
import sqlite3
import unicodedata

import db
from services import auth_service, drama_service, ownership_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

MAX_NAME_LEN = 300           # drama_service.MAX_NAME_LEN, the "New series" limit
MAX_COPY_TERMS = 2000        # glossary_service.MAX_IMPORT_TERMS: one bulk add per request
PREVIEW_TERMS = 50
_DELETE_WORD = "DELETE"
_JOB_RUNNING = ("A job is running for a title in this series. Wait for it to finish or "
                "cancel it before deleting the series.")
_CHANGED = ("This series changed while the delete was being confirmed. Open Delete series "
            "again to see what it holds now.")
_TOO_MANY_TERMS = (f"That series has more than {MAX_COPY_TERMS} glossary terms, the most "
                   "that can be copied at once.")
_COUNT_KEYS = (("titles", "title_count"), ("glossary_terms", "glossary_term_count"),
               ("characters", "character_count"),
               ("translation_memory", "translation_memory_count"),
               ("dismissals", "dismissal_count"), ("style_profiles", "style_profile_count"))
# Invisible or line-breaking characters (zero-width space, bidi overrides,
# line/paragraph separators) would let two names that look the same both pass
# the "already a series called" check.
_BAD_NAME_CATEGORIES = {"Cc", "Cf", "Zl", "Zp"}


def term_key(text) -> str:
    """How two original texts (and two series names) are compared."""
    return unicodedata.normalize("NFKC", str(text or "")).strip().casefold()


def _clean_name(name) -> str:
    if not isinstance(name, str):
        raise InvalidInputError("A series name must be text.")
    name = name.strip()
    if not name:
        raise InvalidInputError("A series name is required.")
    if len(name) > MAX_NAME_LEN:
        raise InvalidInputError(f"A series name is at most {MAX_NAME_LEN} characters.")
    if any(unicodedata.category(ch) in _BAD_NAME_CATEGORIES for ch in name):
        raise InvalidInputError("A series name can't contain control or invisible characters.")
    return name


def _series_row(principal, series_id) -> dict:
    ownership_service.require_visible(principal, "series", series_id)
    row = next((s for s in db.list_series() if s["id"] == series_id), None)
    if row is None:  # deleted between the check and the read
        raise NotFoundError("Series not found.")
    return row


def _counts(series_id, visible_to=None) -> dict:
    counts = db.series_content_counts(series_id, visible_to=visible_to)
    if counts is None:
        raise NotFoundError("Series not found.")
    return counts


def list_series(principal=None) -> list:
    """Every series the principal may see, with the number of its titles
    they may see and its term count (unlike
    library_service.list_series_with_dramas, which keeps only series with
    two or more titles)."""
    uid = ownership_service.user_id(principal)
    visible_to = ownership_service.visible_to_filter(principal)
    out = []
    for s in db.list_series(visible_to=visible_to):
        counts = db.series_content_counts(s["id"], visible_to=visible_to)
        if counts is None:
            continue
        out.append({"id": s["id"], "name": s["name"], "is_private": bool(s.get("is_private")),
                    "owned_by_me": uid is not None and s.get("owner_user_id") == uid,
                    "title_count": counts["visible_titles"],
                    "glossary_term_count": counts["glossary_terms"]})
    return out


def get_series_page(principal, series_id) -> dict:
    """A title the viewer can't see (a private title left in a shared series
    from before a title's own flag was cleared on joining one) is left out."""
    row = _series_row(principal, series_id)
    counts = _counts(series_id, ownership_service.visible_to_filter(principal))
    uid = ownership_service.user_id(principal)
    lines = counts["title_lines"]
    titles = [{"id": d["id"], "title_en": d.get("title_en"), "title_zh": d.get("title_zh"),
               "media_type": d.get("media_type"), "status": d.get("status"),
               "episode_number": d.get("episode_number"),
               "release_date": d.get("release_date") or None,
               "line_count": lines[d["id"]]}
              for d in db.list_dramas_by_series(series_id) if d["id"] in lines]
    return {"id": row["id"], "name": row["name"], "is_private": bool(row.get("is_private")),
            "owned_by_me": uid is not None and row.get("owner_user_id") == uid,
            "can_edit": ownership_service.can_edit(principal, "series", series_id),
            "has_instructions": counts["has_instructions"],
            "glossary_term_count": counts["glossary_terms"],
            "character_count": counts["characters"],
            "titles": titles}


_NAME_UNUSABLE = "That name can't be used. Pick another name for the series."


def rename_series(principal, series_id, name) -> dict:
    """Trimmed, at most MAX_NAME_LEN characters, no control or invisible
    characters. ConflictError when another series the caller can see has the
    same name ignoring case and width (checked in the same transaction as the
    write), or when one they can't see has exactly this name (series.name is
    unique across the library). Both get the same message, so the answer
    doesn't tell a visible clash from another user's private series."""
    ownership_service.require_editable(principal, "series", series_id)
    name = _clean_name(name)
    try:
        result = db.rename_series(series_id, name, term_key,
                                  visible_to=ownership_service.visible_to_filter(principal))
    except sqlite3.IntegrityError:
        raise ConflictError(_NAME_UNUSABLE) from None
    if result["clash"] is not None:
        raise ConflictError(_NAME_UNUSABLE)
    if not result["renamed"]:
        raise NotFoundError("Series not found.")
    return {"id": series_id, "name": name}


def job_running_for_series(series_id) -> bool:
    return any(drama_service.job_running_for_drama(d["id"])
               for d in db.list_dramas_by_series(series_id))


def _holds_anything(counts) -> bool:
    return counts["has_instructions"] or any(counts[k] for k, _ in _COUNT_KEYS)


def delete_preview(principal, series_id) -> dict:
    """What deleting this series would do, for the confirm prompt. Counts
    every title: only the series' owner or an admin gets here, and both see
    every title in it."""
    ownership_service.require_series_owner(principal, series_id)
    row = _series_row(principal, series_id)
    counts = _counts(series_id)
    out = {"series_id": series_id, "name": row["name"],
           "has_instructions": counts["has_instructions"],
           "needs_typed_confirm": _holds_anything(counts),
           "job_running": job_running_for_series(series_id)}
    out.update({public: counts[key] for key, public in _COUNT_KEYS})
    return out


def delete_series(principal, series_id, confirm=False, confirm_text="") -> dict:
    """Order: unknown, invisible or not-owned -> 404/403; then a series that
    holds anything needs confirm=True and confirm_text "DELETE" (an empty one
    only confirm=True) -> InvalidInputError; then a job on any of its titles
    -> ConflictError; then db.delete_series re-reads the counts in its
    transaction and refuses (ConflictError) if anything was added or removed
    since they were read here. Writes an audit entry with counts only."""
    ownership_service.require_series_owner(principal, series_id)
    counts = _counts(series_id)
    if confirm is not True:
        raise InvalidInputError("Deleting a series needs confirm=true.")
    if _holds_anything(counts) and confirm_text != _DELETE_WORD:
        raise InvalidInputError("This series holds titles or glossary data: deleting it needs "
                                "confirm_text set to the word DELETE, in capitals.")
    if job_running_for_series(series_id):
        raise ConflictError(_JOB_RUNNING)
    result = db.delete_series(series_id, expected=counts)
    if result is None:
        raise NotFoundError("Series not found.")
    if not result["deleted"]:
        raise ConflictError(_CHANGED)
    done = result["counts"]
    auth_service.write_audit(
        ownership_service.user_id(principal), "series.delete",
        f"series {series_id}: titles={done['titles']} terms={done['glossary_terms']} "
        f"characters={done['characters']} memory={done['translation_memory']} "
        f"dismissals={done['dismissals']} style_profiles={done['style_profiles']} "
        f"instructions={int(done['has_instructions'])}")
    return {"deleted": True, "series_id": series_id, "titles_kept": done["titles"],
            "glossary_terms_deleted": done["glossary_terms"],
            "characters_deleted": done["characters"]}


def _source_terms(principal, series_id, source_series_id) -> list:
    ownership_service.require_editable(principal, "series", series_id)
    ownership_service.require_copy_source(principal, source_series_id)
    if series_id == source_series_id:
        raise InvalidInputError("Pick a different series to copy from.")
    terms = db.list_glossary_terms(source_series_id)
    if len(terms) > MAX_COPY_TERMS:
        raise InvalidInputError(_TOO_MANY_TERMS)
    return terms


def copy_terms_preview(principal, series_id, source_series_id) -> dict:
    terms = _source_terms(principal, series_id, source_series_id)
    taken = {term_key(t["term_original"]) for t in db.list_glossary_terms(series_id)}
    new, skipped = [], []
    for t in terms:
        key = term_key(t["term_original"])
        (skipped if not key or key in taken else new).append(t)
        taken.add(key)
    return {"series_id": series_id, "source_series_id": source_series_id,
            "new_count": len(new), "skipped_count": len(skipped),
            "new_terms": [t["term_original"] for t in new[:PREVIEW_TERMS]],
            "skipped_terms": [t["term_original"] for t in skipped[:PREVIEW_TERMS]]}


def copy_terms(principal, series_id, source_series_id) -> dict:
    """Adds the source's terms the destination lacks, every column included,
    in one transaction (db.copy_series_terms), which decides "already there"
    against the destination as it is then, so a term added since the preview
    is skipped and a failure part-way adds nothing."""
    terms = _source_terms(principal, series_id, source_series_id)
    result = db.copy_series_terms(series_id, terms, term_key)
    return {"series_id": series_id, "source_series_id": source_series_id, **result}

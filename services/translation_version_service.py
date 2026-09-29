"""
services/translation_version_service.py -- make a saved translation version
the drama's current English (parity item R39). Mirrors the Review tab's
"Translation versions" -> Activate button in `tabs/workspace_tab.py`
(`_restore_saved_lines(..., translation_only=True)` then
`db.set_active_translation_version`).

What it writes: a "before switching version" line-history snapshot (so the
switch can be undone from Version history), then ONLY the `en` column of the
drama's existing lines, matched by permanent line id
(`db.save_lines(..., fields=("en",))`), then the version's active mark. It
never does a full line sync, so it can't insert, delete or reorder a line.

Deliberate difference from the tab: when the version was saved over a
different set of lines (merged, split or re-segmented since, or saved before
permanent line ids existed), the tab restores the version's own lines whole
(timing and speakers too) with a full sync. That is a structural rewrite,
so here it is refused with a ConflictError instead; nothing is written.

Refused (409) while any job is running on the drama, and needs
`confirm=True` (422 otherwise) because it overwrites the current English.
Ownership: `db.get_translation_version` has no drama check, so another
drama's version is the same NotFoundError as a missing one.

No Streamlit or FastAPI import; plain dicts in and out. Messages never echo
line text.
"""
from dataclasses import replace

import core as core_module
import db
from services import drama_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

SNAPSHOT_LABEL = "before switching version"


def _require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _owned_version(drama_id: int, version_id: int) -> dict:
    v = db.get_translation_version(version_id)
    if v is None or v.get("drama_id") != drama_id:
        raise NotFoundError(f"No translation version {version_id} for drama {drama_id}.")
    return v


def activate_version(drama_id: int, version_id: int, confirm: bool = False) -> dict:
    """Sets every current line's `en` to the version's translation for the
    same permanent line id, and marks the version active. Returns
    {drama_id, version_id, label, activated, lines_changed}."""
    _require_drama(drama_id)
    version = _owned_version(drama_id, version_id)
    if confirm is not True:
        raise InvalidInputError("Using a saved version replaces the current English; "
                                "send confirm=true.")
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A background job is still running for this drama -- wait for it "
                            "to finish or cancel it before switching versions.")
    rows = version.get("lines") or []
    current = db.load_line_objects(drama_id)
    if not core_module.saved_matches_lines(rows, current):
        raise ConflictError("This version was saved over a different set of lines (merged, "
                            "split or re-segmented since), so its translations can't be "
                            "matched to the current lines. Nothing was changed.")
    en_by_id = {r["id"]: r.get("en") or "" for r in rows}
    changed = [replace(ln, en=en_by_id[ln.id]) for ln in current if ln.en != en_by_id[ln.id]]
    db.save_line_history_snapshot(drama_id, current, SNAPSHOT_LABEL)
    if changed:
        db.save_lines(drama_id, changed, fields=("en",))
    db.set_active_translation_version(drama_id, version_id)
    return {"drama_id": drama_id, "version_id": version_id, "label": version.get("label") or "",
            "activated": True, "lines_changed": len(changed)}

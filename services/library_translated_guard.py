"""A title only counts as translated, and only goes into an export, when at
least one of its lines has English text; a status alone is not proof."""

import db

NO_TRANSLATED_LINES = "No line in this title has a translation yet."


def _refusal(drama_id: int) -> dict:
    return {"drama_id": drama_id, "ok": False, "error": "no_translated_lines",
            "message": NO_TRANSLATED_LINES}


def set_status(bulk_apply, drama_ids, status: str) -> dict:
    """bulk_apply is library_admin_service._bulk_apply. "translated" is
    refused per id for a title without a translated line."""
    refused = set()

    def apply(did):
        if status == "translated" and not db.drama_has_translated_lines(did):
            refused.add(did)
            return
        db.update_drama(did, status=status)

    out = bulk_apply(drama_ids, apply)
    out["results"] = [_refusal(r["drama_id"]) if r["drama_id"] in refused else r
                      for r in out["results"]]
    out["updated"] = sum(r["ok"] for r in out["results"])
    return out


def pick_exportable(drama_ids, check_ids, exportable_statuses) -> tuple:
    """(ids to export, per-id results or None). With no explicit ids the
    results list only titles skipped for having no translated line."""
    if drama_ids is None:
        ids, results = [], None
        for d in db.list_dramas():
            if d.get("status") not in exportable_statuses:
                continue
            if db.drama_has_translated_lines(d["id"]):
                ids.append(d["id"])
            else:
                results = (results or []) + [_refusal(d["id"])]
        return ids, results
    ids, results = [], []
    for did in check_ids(drama_ids):
        drama = db.get_drama(did)
        if drama is None:
            results.append({"drama_id": did, "ok": False, "error": "not_found"})
        elif drama.get("status") not in exportable_statuses:
            results.append({"drama_id": did, "ok": False, "error": "not_translated"})
        elif not db.drama_has_translated_lines(did):
            results.append(_refusal(did))
        else:
            ids.append(did)
            results.append({"drama_id": did, "ok": True})
    return ids, results

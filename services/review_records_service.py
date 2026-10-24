"""
services/review_records_service.py -- READ-ONLY Review-stage records for one
drama (the Review read-only records slice, "R1b"): line history, translation
versions (list/compare), translation notes (list/markdown), consistency
issues, emotion summary, stored edit tendencies, and translation-memory
suggestions.

Nothing here writes to the database.

Ownership: several id-only db reads (`db.get_line_history_snapshot`,
`db.get_translation_version`) do not check which drama a record belongs to,
so this module verifies ownership itself and raises NotFoundError -- the
same error as a missing record, so the existence of another drama's record
is never revealed.

No FastAPI import; plain dicts in and out, JSON-serialisable.
"""
import os
from typing import Optional

import core as core_module
import db
import emotion
import adaptive_style
import translation_guide
import translation_memory
from services import ownership_service
from services.service_errors import NotFoundError

MAX_TM_LINES_SCANNED = 2000  # cap on lines fed to the TM scan (roughly lines x entries)


def _newest_first(rows: list) -> list:
    return sorted(rows, key=lambda r: (r.get("created_at") or "", r.get("id") or 0), reverse=True)


# ---------------------------------------------------------------------------
# Line history
# ---------------------------------------------------------------------------

def list_line_history(drama_id: int) -> list:
    """Snapshot metadata (id, label, created_at), newest first in the order
    written (db.list_line_history), not by created_at, which a clock set
    back reorders. No lines payload -- use get_line_history_snapshot for that."""
    ownership_service.require_drama(drama_id)
    return [{"id": h["id"], "drama_id": h["drama_id"], "label": h["label"],
             "created_at": h["created_at"]}
            for h in db.list_line_history(drama_id)]


def get_line_history_snapshot(drama_id: int, history_id: int) -> dict:
    """One snapshot's lines. NotFoundError if the snapshot doesn't exist OR
    belongs to another drama (db.get_line_history_snapshot has no ownership
    check, so it is enforced via this drama's own history list)."""
    ownership_service.require_drama(drama_id)
    meta = next((h for h in db.list_line_history(drama_id) if h["id"] == history_id), None)
    if meta is None:
        raise NotFoundError(f"No history snapshot {history_id} for title {drama_id}.")
    raw = db.get_line_history_snapshot(history_id)
    if raw is None:
        raise NotFoundError(f"No history snapshot {history_id} for title {drama_id}.")
    # flag/flag_note/sfx are left out: snapshots saved before they were
    # recorded don't have them, so this read shape doesn't promise them.
    lines = [{"id": r.get("id"), "idx": r.get("idx"), "start": r.get("start"),
              "end": r.get("end"), "zh": r.get("zh") or "", "en": r.get("en") or "",
              "speaker": r.get("speaker"),
              "speaker_manual": bool(r.get("speaker_manual")),
              # bare filename only, never a filesystem path
              "dub_filename": os.path.basename(r["dub_filename"]) if r.get("dub_filename") else None}
             for r in raw]
    current = db.load_line_objects(drama_id)
    return {"id": meta["id"], "drama_id": drama_id, "label": meta["label"],
            "created_at": meta["created_at"], "lines": lines,
            "lines_with_notes_removed": len(lines_losing_notes(drama_id, raw, current)),
            "marks_changed_line_ids": marks_changed(raw, current)}


def marks_changed(rows, current) -> list:
    """Ids of current lines whose flag, flag note, SFX mark or language the
    restore changes: the snapshot's lines above leave those out, so a
    preview built from them alone would say nothing changes."""
    now = {ln.id: ln for ln in current}
    out = []
    for ln in core_module.restore_saved_lines(rows, current):
        was = now.get(ln.id)
        if was is not None and ((was.flag or None, was.flag_note or "", bool(was.sfx), was.lang)
                                != (ln.flag or None, ln.flag_note or "", bool(ln.sfx), ln.lang)):
            out.append(ln.id)
    return out


def lines_losing_notes(drama_id: int, rows, current) -> set:
    """Ids of `current` lines that restoring snapshot `rows` would remove
    while they have a translation note or emotion tag (the full sync deletes
    those with the line)."""
    kept = {ln.id for ln in core_module.restore_saved_lines(rows, current) if ln.id is not None}
    return db.line_ids_with_refs(drama_id, {ln.id for ln in current} - kept)


# ---------------------------------------------------------------------------
# Translation versions
# ---------------------------------------------------------------------------

def list_translation_versions(drama_id: int) -> list:
    """Version metadata, newest first (no lines payload)."""
    ownership_service.require_drama(drama_id)
    return [{"id": v["id"], "drama_id": v["drama_id"], "label": v["label"],
             "engine": v["engine"] or "", "model": v["model"] or "",
             "is_active": bool(v["is_active"]), "created_at": v["created_at"]}
            for v in _newest_first(db.list_translation_versions(drama_id))]


def _owned_version(drama_id: int, version_id: int) -> dict:
    v = db.get_translation_version(version_id)
    if v is None or v.get("drama_id") != drama_id:
        raise NotFoundError(f"No translation version {version_id} for title {drama_id}.")
    return v


def compare_versions(drama_id: int, left_id: int, right_id: int) -> dict:
    """The "Show differences" view: lines of the left version
    whose English differs from the right version's line at the same idx.
    Both versions must belong to this drama."""
    ownership_service.require_drama(drama_id)
    left = _owned_version(drama_id, left_id)
    right = _owned_version(drama_id, right_id)
    rmap = {r["idx"]: r.get("en") or "" for r in right["lines"] if r.get("idx") is not None}
    diffs = [{"idx": r["idx"], "zh": r.get("zh") or "", "left_en": r.get("en") or "",
              "right_en": rmap.get(r["idx"], "")}
             for r in left["lines"]
             if r.get("idx") is not None and rmap.get(r["idx"], "") != (r.get("en") or "")]
    return {"drama_id": drama_id,
            "left": {"id": left["id"], "label": left["label"]},
            "right": {"id": right["id"], "label": right["label"]},
            "left_line_count": len(left["lines"]),
            "diff_count": len(diffs),
            "diffs": diffs}


# ---------------------------------------------------------------------------
# Translation notes
# ---------------------------------------------------------------------------

def list_translation_notes(drama_id: int) -> list:
    """Notes ordered by the line's current position; line_idx follows the
    line through merges/splits (see db.list_translation_notes)."""
    ownership_service.require_drama(drama_id)
    return [{"id": n["id"], "drama_id": n["drama_id"], "line_id": n["line_id"],
             "line_idx": n["line_idx"], "term": n["term"], "note_type": n["note_type"],
             "note": n["note"], "created_at": n["created_at"]}
            for n in db.list_translation_notes(drama_id)]


def get_notes_markdown(drama_id: int) -> str:
    """The text of "Download notes as Markdown"."""
    drama = ownership_service.require_drama(drama_id)
    notes = db.list_translation_notes(drama_id)
    # db's COALESCE can yield a NULL line_idx, which the formatter's sort
    # can't compare; pass copies with None -> 0 (rows themselves untouched).
    notes = [dict(n, line_idx=n["line_idx"] if n.get("line_idx") is not None else 0)
             for n in notes]
    return translation_guide.format_notes_as_markdown(
        notes, drama.get("title_en") or drama.get("title_zh") or "")


# ---------------------------------------------------------------------------
# Consistency, emotion, edit tendencies
# ---------------------------------------------------------------------------

def get_consistency_issues(drama_id: int) -> list:
    """Stored results of the last consistency check (not re-run here)."""
    ownership_service.require_drama(drama_id)
    return [{"id": i["id"], "term": i.get("term") or "",
             "variants": list(i.get("variants") or []), "note": i.get("note") or "",
             "created_at": i.get("created_at")}
            for i in db.load_consistency_issues(drama_id)]


def get_emotion_summary(drama_id: int) -> dict:
    """Local counts over stored emotion tags plus the per-line tags."""
    ownership_service.require_drama(drama_id)
    emap = db.load_emotions(drama_id)
    summary = emotion.emotion_summary(emap)
    tags = [{"line_idx": int(idx), "emotion": t["emotion"], "intensity": t["intensity"],
             "note": t.get("note") or ""}
            for idx, t in sorted(emap.items(), key=lambda kv: int(kv[0]))]
    return {"drama_id": drama_id, "total": summary["total"],
            "by_emotion": summary["by_emotion"], "high_risk": summary["high_risk"],
            "lines": tags}


def get_edit_tendencies(drama_id: int, principal=None) -> dict:
    """This drama's recorded-edit statistics plus the STORED learned style
    profile (None when nothing has been learned, or when the principal may
    not see it: ownership_service.sees_learned_style). Never runs the LLM
    analysis."""
    drama = ownership_service.require_drama(drama_id)
    tend = adaptive_style.summarize_edit_tendencies(db.list_edit_samples(drama_id))
    scope = f"series:{drama['series_id']}" if drama.get("series_id") else "global"
    stored = db.get_style_profile(scope)
    profile = None
    prefs = ((stored or {}).get("profile") or {}).get("preferences")
    if prefs and ownership_service.sees_learned_style(principal, stored["profile"]):
        pr = stored["profile"]
        profile = {"summary": pr.get("summary") or "", "confidence": pr.get("confidence"),
                   "preferences": list(prefs), "sample_count": stored.get("sample_count") or 0,
                   "updated_at": stored.get("updated_at")}
    return {"drama_id": drama_id, "scope": scope,
            "tendencies": {"total": tend.get("total", 0),
                           "shortened": tend.get("shortened", 0),
                           "expanded": tend.get("expanded", 0),
                           "rephrased": tend.get("rephrased", 0),
                           "avg_word_delta": tend.get("avg_word_delta", 0.0)},
            "profile": profile}


# ---------------------------------------------------------------------------
# Translation-memory suggestions
# ---------------------------------------------------------------------------

def list_tm_suggestions(drama_id: int, line_ids: Optional[list] = None) -> list:
    """Suggestions only (nothing is applied or bumped). Empty when the drama
    has no series. line_ids limits the lines considered (permanent ids).
    At most MAX_TM_LINES_SCANNED lines (the first ones, in line order) are
    scanned, since the TM match costs roughly lines x entries."""
    drama = ownership_service.require_drama(drama_id)
    series_id = drama.get("series_id")
    if not series_id:
        return []
    entries = db.list_translation_memory(series_id)
    lines = db.load_line_objects(drama_id)
    if line_ids is not None:
        wanted = set(line_ids)
        lines = [ln for ln in lines if ln.id in wanted]
    lines = lines[:MAX_TM_LINES_SCANNED]
    by_idx = translation_memory.suggest_for_lines(lines, entries)
    out = []
    for ln in lines:
        m = by_idx.get(ln.idx)
        if not m:
            continue
        out.append({"line_id": ln.id, "line_idx": ln.idx, "zh": ln.zh, "en": ln.en or "",
                    "suggestion": m["entry"]["translation"],
                    "similarity": round(m["similarity"], 4), "exact": bool(m["exact"]),
                    "entry_id": m["entry"]["id"]})
    return out

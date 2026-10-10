"""
services/subtitle_import_service.py -- bring a timed subtitle or lyric file
(SRT, VTT, ASS/SSA, LRC) into a title, and rank sidecar file names.

Two ways in, chosen by the user (default "source"):
- "source": the cues become the title's lines, the file's times authoritative.
  Replacing lines that already exist needs `confirm_replace_lines`.
- "translation": cue text goes onto the existing lines whose time it overlaps
  by more than half; nothing is matched by position. Overwriting a
  translation that is already there needs `confirm_overwrite`.

`preview_import` and `import_subtitle` share one plan, so what the preview
counts is what the import writes. The whole file is parsed and checked before
anything is written, so a file with blocking problems changes nothing, and
every write takes a history snapshot first (undo via the returned handle).
Re-aligning the new lines against the audio is the existing Re-time job, which
the caller starts from the returned `line_ids`.
"""

from typing import Optional

import core as core_module
import db
import subtitle_parse
import subtitle_sidecar
from services import restructure_service
from services.lines_service import MAX_LINE_TEXT_CHARS
from services.service_errors import InvalidInputError, NotFoundError

MODES = ("source", "translation")
SAMPLE_CUES = 5
SNAPSHOT_LABEL = "before subtitle import"


def _drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    return drama


def _parse(drama: dict, data: bytes, filename: str, encoding: Optional[str]):
    try:
        return subtitle_parse.parse_subtitle(
            data, filename or "", encoding=encoding or None,
            language=drama.get("source_language"))
    except subtitle_parse.SubtitleParseError as e:
        raise InvalidInputError(str(e)) from None


def _plan(parsed, mode: str, split_bilingual: bool, translation_first: bool, lines: list) -> dict:
    """What an import would do, without writing. `lines` are the title's
    current Line objects."""
    if mode not in MODES:
        raise InvalidInputError(f"mode must be one of: {', '.join(MODES)}.")
    cues, translations, unsplit = parsed.cues, None, 0
    if split_bilingual:
        cues, translations, unsplit = subtitle_parse.split_bilingual(cues, translation_first)
    plan = {"mode": mode, "unsplit_cues": unsplit, "existing_line_count": len(lines)}
    if mode == "source":
        plan["new_lines"] = [
            core_module.Line(idx=i, start=c.start, end=c.end, zh=c.text,
                             en=translations[i].text if translations else "")
            for i, c in enumerate(cues)]
        plan["replaces_lines"] = len(lines)
        return plan
    if not lines:
        raise InvalidInputError("This title has no lines yet. Import the file as source lines first.")
    texts = translations if translations is not None else cues
    texts = [c for c in texts if c.text.strip()]
    ordered = sorted(lines, key=lambda ln: ln.start)
    matched = subtitle_parse.match_cues_to_lines(texts, ordered)
    updates, overwrites = {}, 0
    for k, group in matched.items():
        line = ordered[k]
        # Lines hold single text; several cues over one line read as one sentence.
        new_en = " ".join(c.text.replace("\n", " ") for c in group)[:MAX_LINE_TEXT_CHARS]
        if new_en != (line.en or ""):
            updates[line.id] = new_en
            if (line.en or "").strip():
                overwrites += 1
    plan.update(updates=updates, matched_lines=len(matched), overwrites=overwrites,
                unmatched_cues=len(texts) - sum(len(g) for g in matched.values()))
    return plan


def _describe(parsed) -> dict:
    cues = parsed.cues
    return {
        "format": parsed.format,
        "encoding": parsed.encoding,
        "encoding_guessed": parsed.encoding_guessed,
        "cue_count": len(cues),
        "duration_seconds": round(max((c.end for c in cues), default=0.0), 3),
        "detected_language": subtitle_sidecar.detect_language("\n".join(c.text for c in cues)),
        "bilingual_suspected": subtitle_parse.looks_bilingual(cues),
        "problems": [{"code": p.code, "severity": p.severity, "message": p.message, "count": p.count}
                     for p in parsed.problems],
        "blocking": parsed.blocking,
        "sample": [{"start": c.start, "end": c.end, "text": c.text[:200]} for c in cues[:SAMPLE_CUES]],
    }


def preview_import(drama_id: int, data: bytes, filename: str = "", *, encoding: Optional[str] = None,
                   mode: str = "source", split_bilingual: bool = False,
                   translation_first: bool = False) -> dict:
    """Parse and check a file and say what importing it would do. Writes
    nothing. Raises InvalidInputError for an unusable file."""
    drama = _drama(drama_id)
    parsed = _parse(drama, data, filename, encoding)
    out = _describe(parsed)
    out["mode"] = mode
    lines = db.load_line_objects(drama_id)
    try:
        plan = _plan(parsed, mode, split_bilingual, translation_first, lines)
    except InvalidInputError as e:
        # "No lines to put a translation on" is an answer for the preview, not a failure.
        if mode != "translation" or lines:
            raise
        out.update(existing_line_count=0, replaces_lines=0, matched_lines=0, unmatched_cues=0,
                   overwrites=0, unsplit_cues=0, blocked_reason=e.message)
        return out
    out.update(existing_line_count=plan["existing_line_count"], unsplit_cues=plan["unsplit_cues"],
               replaces_lines=plan.get("replaces_lines", 0), matched_lines=plan.get("matched_lines", 0),
               unmatched_cues=plan.get("unmatched_cues", 0), overwrites=plan.get("overwrites", 0),
               blocked_reason=None)
    return out


def import_subtitle(drama_id: int, data: bytes, filename: str = "", *, encoding: Optional[str] = None,
                    mode: str = "source", split_bilingual: bool = False,
                    translation_first: bool = False, confirm_replace_lines: bool = False,
                    confirm_overwrite: bool = False) -> dict:
    """Write the file into the title. Returns the counts, the ids of the
    lines written or changed and an undo handle. Raises InvalidInputError for
    an unusable file or a missing confirmation (details.reason names which),
    ConflictError while a job is changing the title's lines."""
    drama = _drama(drama_id)
    parsed = _parse(drama, data, filename, encoding)
    if parsed.blocking:
        raise InvalidInputError("This file has problems that stop the import: "
                                + " ".join(p.message for p in parsed.problems if p.severity == "error"))
    with restructure_service.exclusive_write(drama_id):
        current = db.load_line_objects(drama_id)
        plan = _plan(parsed, mode, split_bilingual, translation_first, current)
        if mode == "source":
            if current and not confirm_replace_lines:
                raise InvalidInputError(
                    f"This title already has {len(current)} line(s); importing replaces them. "
                    "Confirm to continue (the old lines are saved to history first).",
                    details={"reason": "confirm_replace_lines", "existing_line_count": len(current)})
            written = plan["new_lines"]
            history_id = db.save_line_history_snapshot(drama_id, current, SNAPSHOT_LABEL) if current else None
            db.save_lines(drama_id, written)
            db.update_drama(drama_id, status="aligned")
        else:
            if plan["overwrites"] and not confirm_overwrite:
                raise InvalidInputError(
                    f"{plan['overwrites']} line(s) already have a translation that this file would "
                    "replace. Confirm to continue (the old text is saved to history first).",
                    details={"reason": "confirm_overwrite", "overwrites": plan["overwrites"]})
            written = [ln for ln in current if ln.id in plan["updates"]]
            history_id = None
            if written:
                history_id = db.save_line_history_snapshot(drama_id, current, SNAPSHOT_LABEL)
                for ln in written:
                    ln.en = plan["updates"][ln.id]
                # Field-limited and compare-and-set so a translation edited since the load is kept.
                kept = set(db.save_lines(drama_id, written, fields=("en",), only_if_unchanged=True))
                written = [ln for ln in written if ln.id not in kept]
        handle = restructure_service.undo_handle(drama_id, history_id) if history_id else None
    return {"mode": mode, "format": parsed.format, "encoding": parsed.encoding,
            "lines_written": len(written), "line_ids": [ln.id for ln in written],
            "replaced_lines": plan.get("replaces_lines", 0),
            "matched_lines": plan.get("matched_lines", 0),
            "unmatched_cues": plan.get("unmatched_cues", 0),
            "undo": handle}


def rank_sidecar_names(drama_id: int, media_name: str, names: list) -> dict:
    """Subtitle file names (picked in the browser) that belong to `media_name`,
    best first. Names only, never paths; nothing is bound for the caller."""
    drama = _drama(drama_id)
    media_name = (media_name or "").strip()
    if not media_name or "/" in media_name or "\\" in media_name:
        raise InvalidInputError("media_name must be a file name.")
    if len(names) > 500 or any((not isinstance(n, str)) or len(n) > 255 or "/" in n or "\\" in n
                              for n in names):
        raise InvalidInputError("names must be at most 500 file names, without folders.")
    ranked = subtitle_sidecar.rank_sidecars(media_name, names, drama.get("source_language"))
    return {"candidates": [{"name": c.name, "format": c.format, "language_token": c.language_token,
                            "language": c.language, "exact": c.exact} for c in ranked],
            "ambiguous": len(ranked) > 1}

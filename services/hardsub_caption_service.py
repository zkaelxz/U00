"""
services/hardsub_caption_service.py -- offers a title's burned-in caption text where the transcript's line is
close to it but not the same (typically a misheard name), and writes the caption into the lines the person
picks. Never changes a line unasked: captions have typos of their own. UI-free.
"""
import db
from services import hardsub_speaker_apply_service as apply_svc, hardsub_speaker_read_service as reading
from services import drama_service, lines_service, ownership_service
from services.benchmark_scoring_service import script_normaliser
from services.hardsub_speaker_compare import _chars, _overlap
from lib.errors import ConflictError, InvalidInputError, NotFoundError

# Below this share of shared characters the caption is probably another utterance (or a misread band), not
# the same words spelled differently, and offering it would replace a good line.
MIN_SIMILARITY = 0.5


def _caption_for(line: dict, groups: list):
    """(group key, caption text) of the group covering the line, in time order, else None."""
    probe = type("Span", (), {"start": line["start"], "end": line["end"]})
    key = apply_svc.label_for(probe, groups)
    if key is None:
        return None
    group = next(g for g in groups if g["key"] == key)
    parts = [c["text"] for c in sorted(group["lines"], key=lambda c: c["start"])
             if _overlap(line["start"], line["end"], c["start"], c["end"]) > 0]
    return key, "".join(parts)


def _finished_groups(principal, drama_id: int) -> tuple:
    got = reading.result(principal, drama_id)
    if got["status"] != "done" or not got.get("result") or not got["result"]["groups"]:
        raise NotFoundError("This title has no finished hard-sub reading to take captions from.")
    drama = db.get_drama(drama_id)
    return got["result"]["groups"], (drama or {}).get("source_language") or "zh"


def suggestions(principal, drama_id: int) -> dict:
    """{"suggestions": [{"line_id", "start", "end", "current", "caption", "group", "person", "similarity"}]}
    for lines whose covering caption has most of the same characters but not all."""
    ownership_service.require_visible(principal, "drama", drama_id)
    groups, language = _finished_groups(principal, drama_id)
    normaliser = script_normaliser("ocr", language)
    people = apply_svc.colour_names(drama_id)
    out = []
    for line in db.load_lines(drama_id):
        found = _caption_for(line, groups)
        current = (line.get("zh") or "").strip()
        if not found or not found[1] or not current:
            continue
        key, caption = found
        ours, theirs = _chars(current, normaliser), _chars(caption, normaliser)
        longest = max(sum(ours.values()), sum(theirs.values()))
        if not longest or ours == theirs:
            continue
        similarity = sum((ours & theirs).values()) / longest
        if similarity < MIN_SIMILARITY:
            continue
        out.append({"line_id": line["id"], "start": line["start"], "end": line["end"], "current": current,
                    "caption": caption, "group": key, "person": people.get(key), "similarity": round(similarity, 4)})
    return {"suggestions": out}


def accept(principal, drama_id: int, line_ids: list) -> dict:
    """Writes the offered caption into each chosen line (re-derived now, so a line edited since is skipped).
    Returns {"changed", "skipped"}."""
    ownership_service.require_editable(principal, "drama", drama_id)
    if not line_ids or len(set(line_ids)) != len(line_ids):
        raise InvalidInputError("Pass the lines to change, each once.")
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A background job is still running for this title -- wait for it "
                            "to finish or cancel it first.")
    offered = {s["line_id"]: s for s in suggestions(principal, drama_id)["suggestions"]}
    changed = skipped = 0
    for line_id in line_ids:
        item = offered.get(line_id)
        if item is None:
            skipped += 1
            continue
        try:
            lines_service.patch_line(drama_id, line_id, zh=item["caption"], expected={"zh": item["current"]})
            changed += 1
        except ConflictError:
            skipped += 1
    return {"changed": changed, "skipped": skipped}

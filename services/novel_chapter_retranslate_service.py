"""services/novel_chapter_retranslate_service.py -- "Translate this chapter again": the normal translate run limited to one chapter's lines, previewed first."""

from lib.errors import InvalidInputError, UnsupportedOperationError
from services import (line_provenance_service, novel_line_chapters_service, ownership_service,
                      translate_run_service)


def _chapter(drama_id: int, number: int) -> list:
    """The chapter's lines that have a source text, each with a hand_edited flag."""
    ownership_service.require_drama(drama_id)
    rows = [r for r in novel_line_chapters_service.chapter_lines(drama_id, number)
            if (r.get("zh") or "").strip()]
    if not rows:
        raise InvalidInputError("No such chapter in the translation lines.")
    # Nothing marks a person's edit, so a line the last translate run did not
    # produce counts as hand-edited, as in the glossary re-translate.
    machine = line_provenance_service.machine_made_ids(
        drama_id, {r["id"]: r.get("en") or "" for r in rows if (r.get("en") or "").strip()})
    return [{**r, "hand_edited": bool((r.get("en") or "").strip()) and r["id"] not in machine}
            for r in rows]


def preview(drama_id: int, number: int, engine_name: str | None = None, model: str | None = None,
            reflect: bool = False, gemini_free_tier: bool | None = None,
            job_cost_cap_usd: float | None = None) -> dict:
    """Line counts and the cost estimate (no engine call) for translating the
    chapter again: without and with the lines a person edited."""
    rows = _chapter(drama_id, number)
    machine = {r["id"] for r in rows if not r["hand_edited"]}

    def estimate(ids):
        return translate_run_service.estimate_translate_cost(
            drama_id, engine_name, model, reflect=reflect, force_retranslate=True,
            gemini_free_tier=gemini_free_tier, job_cost_cap_usd=job_cost_cap_usd, line_ids=ids)

    return {"drama_id": drama_id, "number": number, "line_count": len(rows),
            "hand_edited_count": len(rows) - len(machine),
            "estimate": estimate(machine) if machine else None,
            "estimate_with_hand_edited": estimate({r["id"] for r in rows})}


def start(drama_id: int, number: int, include_hand_edited: bool = False, **run_options) -> dict:
    """Starts the translate run for the chapter's lines, writing only `en`
    and never over an edit saved while it runs. The pre-run snapshot keeps
    the old text. UnsupportedOperationError when only hand-edited lines are
    left and they were not included."""
    rows = _chapter(drama_id, number)
    keep = [r for r in rows if include_hand_edited or not r["hand_edited"]]
    if not keep:
        raise UnsupportedOperationError(
            "Every line in this chapter was edited by hand. Tick 'Include hand-edited lines' to replace them.")
    started = translate_run_service.start_translate_run(
        drama_id, force_retranslate=True, line_ids=[r["id"] for r in keep],
        own_lines_only=True, expected_en={r["id"]: r.get("en") or "" for r in keep}, **run_options)
    ran = started.get("line_ids", [r["id"] for r in keep])
    return {**started, "line_ids": ran, "skipped_hand_edited_count": len(rows) - len(ran)}

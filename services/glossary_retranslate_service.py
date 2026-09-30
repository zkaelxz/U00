"""
services/glossary_retranslate_service.py -- after glossary terms change,
re-translate only the lines those terms affect.

`find_glossary_affected_lines` lists the translated lines whose source
text holds a glossary term or one of its aliases (the same test as
translate_engines.matching_glossary_terms), plus the lines whose current
English uses one of a term's banned translations (Auto QC's test). The
glossary keeps no changed-at time, so the caller narrows it by picking
terms (`term_ids`); none picked means every term.

A line counts as hand-edited unless its English is exactly what the last
recorded translate run produced (line_provenance_service.machine_made_ids).
Nothing marks a person's edit as such, so every line without that record --
a Review edit, find & replace, an import, a restore, an older line from
before provenance was kept, or a machine path that records none -- is
treated as hand-edited and left out unless the caller includes them.

`start_affected_retranslate` recomputes the affected set, refuses a stale
preview (ConflictError) and ids outside the set, drops hand-edited ids
unless asked, then starts the normal translate job for just those ids
(translate_run_service.start_translate_run: same engine, glossary, style,
characters, caps, cancel and pre-run snapshot), writing only `en` and
never over an edit saved while it runs.
"""

import hashlib
import json
import re

import auto_qc
import db
import translate_engines
from services import line_provenance_service, translate_run_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                      UnsupportedOperationError)


def _require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if not drama:
        raise NotFoundError(f"Drama {drama_id} not found.")
    return drama


def _selected_terms(drama: dict, term_ids) -> list:
    series_id = drama.get("series_id")
    terms = db.list_glossary_terms(series_id) if series_id else []
    if term_ids is None:
        return terms
    wanted = set(term_ids)
    if not wanted <= {t["id"] for t in terms}:
        raise InvalidInputError("term_ids must be terms in this drama's glossary.")
    return [t for t in terms if t["id"] in wanted]


def _banned_forms(term) -> tuple:
    return tuple(b.strip() for b in re.split(r"[|,，、]", term.get("banned_translations") or "")
                 if b.strip())


_TERM_FIELDS = ("term_original", "term_translation", "aliases", "banned_translations",
                "enforce_exact", "notes")


def _preview_hash(terms, lines) -> str:
    # The terms' own text too: a preview showing an old rendering is stale.
    blob = json.dumps({"terms": sorted([t["id"]] + [str(t.get(k) or "") for k in _TERM_FIELDS]
                                       for t in terms),
                       "lines": [[ln["id"], ln["zh"], ln["en"], ln["hand_edited"],
                                  [m["term_id"] for m in ln["matched_terms"]]] for ln in lines]},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _affected(drama_id: int, drama: dict, term_ids) -> tuple:
    """(terms used, affected lines, preview hash)."""
    terms = _selected_terms(drama, term_ids)
    banned = [(t, _banned_forms(t)) for t in terms]
    rows = db.load_lines(drama_id)
    candidates = []
    for row in rows:
        zh, en = row.get("zh") or "", row.get("en") or ""
        if not zh.strip() or not en.strip():
            continue
        matched = {t["id"]: "source" for t in translate_engines.matching_glossary_terms(zh, terms)}
        en_lower = auto_qc._normalize(en).lower()
        for t, forms in banned:
            if forms and auto_qc._banned_hit(forms, en_lower):
                matched[t["id"]] = "banned"
        if matched:
            candidates.append((row, matched))
    machine = line_provenance_service.machine_made_ids(
        drama_id, {row["id"]: row.get("en") or "" for row, _ in candidates})
    by_id = {t["id"]: t for t in terms}
    lines = [{
        "id": row["id"], "idx": row["idx"], "start": row.get("start"), "end": row.get("end"),
        "zh": row.get("zh") or "", "en": row.get("en") or "",
        "hand_edited": row["id"] not in machine,
        "matched_terms": [{"term_id": tid, "term_original": by_id[tid].get("term_original") or "",
                           "term_translation": by_id[tid].get("term_translation") or "",
                           "reason": reason}
                          for tid, reason in sorted(matched.items())],
    } for row, matched in candidates]
    return terms, lines, _preview_hash(terms, lines)


def find_glossary_affected_lines(drama_id: int, term_ids=None, engine_name: str = None,
                                 model: str = None, reflect: bool = False,
                                 gemini_free_tier: bool = None,
                                 job_cost_cap_usd: float = None) -> dict:
    """The preview: affected lines, the glossary to pick terms from, and
    cost estimates (no engine call) for the machine lines alone and for
    every affected line. term_ids None = every term."""
    drama = _require_drama(drama_id)
    series_id = drama.get("series_id")
    all_terms = db.list_glossary_terms(series_id) if series_id else []
    terms, lines, preview_hash = _affected(drama_id, drama, term_ids)
    machine_ids = {ln["id"] for ln in lines if not ln["hand_edited"]}

    def _estimate(ids):
        return translate_run_service.estimate_translate_cost(
            drama_id, engine_name, model, reflect=reflect, force_retranslate=True,
            gemini_free_tier=gemini_free_tier, job_cost_cap_usd=job_cost_cap_usd, line_ids=ids)

    return {
        "drama_id": drama_id,
        "has_glossary": bool(all_terms),
        "terms": [{"id": t["id"], "term_original": t.get("term_original") or "",
                   "term_translation": t.get("term_translation") or ""} for t in all_terms],
        "selected_term_ids": sorted(t["id"] for t in terms),
        "lines": lines,
        "hand_edited_count": len(lines) - len(machine_ids),
        "preview_hash": preview_hash,
        "estimate": _estimate(machine_ids),
        "estimate_with_hand_edited": _estimate({ln["id"] for ln in lines}),
    }


def affected_line_ids(drama_id: int, include_hand_edited: bool = False, term_ids=None) -> list:
    """The ids a run would re-translate with no preview (`cli.py translate
    --glossary-affected`): hand-edited lines only with include_hand_edited."""
    drama = _require_drama(drama_id)
    _terms, lines, _hash = _affected(drama_id, drama, term_ids)
    return [ln["id"] for ln in lines if include_hand_edited or not ln["hand_edited"]]


def start_affected_retranslate(drama_id: int, line_ids, preview_hash: str,
                               include_hand_edited: bool = False, term_ids=None,
                               engine_name: str = None, model: str = None,
                               style_preset: str = None, style_note: str = "",
                               locale: str = "en-US", context_window: int = None,
                               context_window_ahead: int = None, batch_size: int = None,
                               gemini_free_tier: bool = None, job_cost_cap_usd: float = None,
                               fallback_chain: list = None, reflect: bool = False,
                               default_female_pronouns: bool = None,
                               include_genre_notes: bool = None,
                               allow_paid_summary: bool = True) -> dict:
    """Starts the re-translation of the chosen affected lines. The affected
    set is recomputed here; the client's list is only a selection from it.
    InvalidInputError for ids not in this drama or not affected,
    ConflictError when the lines or glossary changed since the preview,
    UnsupportedOperationError when only hand-edited lines were chosen and
    include_hand_edited is off; otherwise start_translate_run's own errors."""
    drama = _require_drama(drama_id)
    wanted = set(line_ids or ())
    if not wanted:
        raise InvalidInputError("Choose at least one line to re-translate.")
    drama_line_ids = {row["id"] for row in db.load_lines(drama_id)}
    if not wanted <= drama_line_ids:
        raise InvalidInputError("line_ids must be this drama's lines.")
    try:
        _terms, lines, current_hash = _affected(drama_id, drama, term_ids)
    except InvalidInputError:
        # A term the preview was built from has been deleted since.
        raise ConflictError("The glossary changed since this preview. Open the preview again.",
                            details={"reason": "stale_preview"}) from None
    if current_hash != preview_hash:
        raise ConflictError("The lines or the glossary changed since this preview. "
                            "Open the preview again.", details={"reason": "stale_preview"})
    affected = {ln["id"]: ln for ln in lines}
    if not wanted <= set(affected):
        raise InvalidInputError("line_ids must be lines the glossary affects.")
    keep = sorted(i for i in wanted if include_hand_edited or not affected[i]["hand_edited"])
    if not keep:
        raise UnsupportedOperationError(
            "Every chosen line is hand-edited. Tick 'Include hand-edited lines' to replace them.")
    started = translate_run_service.start_translate_run(
        drama_id, engine_name=engine_name, model=model, style_preset=style_preset,
        style_note=style_note, locale=locale, force_retranslate=True,
        context_window=context_window, context_window_ahead=context_window_ahead,
        batch_size=batch_size, line_ids=keep, gemini_free_tier=gemini_free_tier,
        job_cost_cap_usd=job_cost_cap_usd, fallback_chain=fallback_chain, reflect=reflect,
        default_female_pronouns=default_female_pronouns,
        include_genre_notes=include_genre_notes, allow_paid_summary=allow_paid_summary,
        own_lines_only=True, expected_en={i: affected[i]["en"] for i in keep})
    # A line edited after the recompute above is dropped by the run and is
    # hand-edited now, so it counts as skipped.
    ran = started.get("line_ids", keep)
    return {**started, "line_ids": ran, "skipped_hand_edited_count": len(wanted) - len(ran)}

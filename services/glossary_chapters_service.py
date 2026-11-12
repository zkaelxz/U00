"""
services/glossary_chapters_service.py -- scan every saved chapter of a title in order and add the
terms found to the series glossary, without overwriting one the person already has.

Each chapter is scanned with the glossary as it stands after the chapter before, so a name found
early is "known" to the later scans. Nothing is wired into the translation job: run this before
translating. Paid-engine spend: one call per chapter, unless the engine is in FREE_ENGINES.
"""
from typing import Optional

import background_jobs
import db
import translation_guide as tguide
from engine_backends import paid_calls
from engine_backends import shared as engine_shared
from engine_backends.shared import TranslationCancelled
from lib.errors import ConflictError, UnsupportedOperationError
from services import glossary_extract_service as extract
from services import novel_line_chapters_service, paid_job_service
from services.glossary_common import _drama, _series_id

# A term seen once in a chapter is more often a one-off phrase than a name worth fixing; the later
# chapters that repeat it will reach this bar.
MIN_OCCURRENCES = 2


def chapters_glossary_job_id(drama_id: int) -> str:
    return f"chapters_glossary_{drama_id}"


def _chapter_texts(drama_id: int) -> list:
    """[(chapter number, [source lines])] in order, empty chapters left out."""
    rows = db.load_lines(drama_id)
    out = []
    for c in novel_line_chapters_service.line_chapters(drama_id)["chapters"]:
        part = [r["zh"] for r in rows if r["idx"] >= c["first_idx"]][:c["line_count"]]
        part = [t for t in part if (t or "").strip()]
        if part:
            out.append((c["number"], part))
    return out


def _add_new_terms(drama, proposals: list) -> list:
    """Adds the proposals worth keeping; the terms added, in order."""
    sid = _series_id(drama, required=False)
    fresh = [p for p in proposals
             if not p["already_in_glossary"] and p["suggested_translation"]
             and p["occurrences"] >= MIN_OCCURRENCES]
    if sid:
        ignored = db.list_dismissed_glossary_terms(sid, [p["term"] for p in fresh])
        fresh = [p for p in fresh if p["term"] not in ignored]
    if not fresh:
        return []
    sid = _series_id(drama, required=True)
    for p in fresh:
        db.upsert_glossary_term(sid, p["term"], p["suggested_translation"], notes=p["reason"],
                                category=p["category"], policy=p["policy"], enforce_exact=False)
    return [p["term"] for p in fresh]


def _scan_one(drama, engine, engine_name, lines) -> list:
    sid = _series_id(drama, required=False)
    known = db.list_effective_glossary_terms(sid) if sid else []
    proposals = tguide.extract_terms_llm(
        lines, engine, source_language=drama.get("source_language") or "zh", known_terms=known,
        usage_cb=paid_calls.usage_logger(drama["id"], engine_name, engine, "extract_terms"))
    proposals = [{k: v for k, v in p.items() if k != "renderings"} for p in proposals if isinstance(p, dict)]
    return extract._normalize_proposals(proposals, known, "\n".join(lines))


def _run_job(job_id, run_id, drama_id, engine, engine_name, chapters):
    drama = _drama(drama_id)
    added, done = [], 0
    try:
        with extract._bounded_calls(job_id, drama_id, engine, engine_name, "extract_terms") as limits:
            for number, lines in chapters:
                if background_jobs.is_cancel_requested(job_id):
                    raise background_jobs.JobCancelled()
                background_jobs.update_progress(
                    job_id, done / len(chapters), f"Scanning chapter {number} ({done + 1} of {len(chapters)})...")
                added += _add_new_terms(drama, _scan_one(drama, engine, engine_name, lines))
                done += 1
                drama = _drama(drama_id)
    except (TranslationCancelled, background_jobs.JobCancelled):
        raise background_jobs.JobCancelled() from None
    except Exception as exc:
        raise RuntimeError(extract._extract_failed(engine_name) + " "
                           + engine_shared.redact_secrets(str(exc))) from None
    summary = f"Scanned {done} chapter{'' if done == 1 else 's'}; added {len(added)} term{'' if len(added) == 1 else 's'}."
    background_jobs.update_progress(job_id, 1.0, summary)
    background_jobs.set_result(job_id, {"proposals": [], "run_id": run_id, "added": added,
                                        **paid_job_service.stop_fields(limits)})


def start_chapters_glossary_run(drama_id: int, engine_name: Optional[str] = None) -> dict:
    """Starts scanning each saved chapter in turn. UnsupportedOperationError when the title has no
    chapters or the engine can't do this, ConflictError when a glossary scan is already running
    (the engine rule and the spending cap are start_lines_glossary_run's)."""
    from services import settings_service, translate_run_service

    drama = _drama(drama_id)
    chapters = _chapter_texts(drama_id)
    if len(chapters) < 2:
        raise UnsupportedOperationError("This title has no saved chapters to scan one by one.")
    engine_name, engine = extract._glossary_engine(drama, engine_name)
    translate_run_service.refuse_when_cap_spent(engine_name, bool(settings_service.get("gemini_free_tier")))
    job_id = chapters_glossary_job_id(drama_id)
    started = extract._start_extraction_job(
        job_id, _run_job, drama_id, engine, engine_name, chapters, gpu_touching=engine_name == "ollama",
        description=f"Glossary from every chapter (title #{drama_id})")
    if not started:
        raise ConflictError("A glossary extraction is already running for this title.")
    return {"job_id": job_id, "engine": engine_name, "chapter_count": len(chapters)}


def get_chapters_glossary_status(drama_id: int) -> dict:
    return extract._extraction_status(drama_id, chapters_glossary_job_id(drama_id))


def cancel_chapters_glossary_run(drama_id: int, run_id: str, principal) -> dict:
    return extract._cancel_extraction(drama_id, chapters_glossary_job_id(drama_id), run_id, principal)

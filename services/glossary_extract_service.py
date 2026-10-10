"""
services/glossary_extract_service.py -- the glossary extraction jobs split
out of glossary_service: the novel and source-lines extraction runs, their
status, cancel, dismissals and apply. The module docstring of
glossary_service describes the contract; import these names from there.

No FastAPI import.
"""
import os
import threading
import uuid
from typing import Optional

import background_jobs
import db
import translate_engines
import translation_guide as tguide
from engine_backends import llm_tasks
from engine_backends.shared import TranslationCancelled
from glossary_io import TERM_CATEGORIES, TERM_POLICIES
from services import job_checkpoint_service
from services.glossary_common import MAX_NOTES_LEN, MAX_TERM_LEN, _drama, _series_id, _text
from services.service_errors import (
    ConflictError,
    InvalidInputError,
    MissingKeyError,
    UnsupportedOperationError,
)


# ---------------------------------------------------------------------------
# Glossary from the attached novel
# ---------------------------------------------------------------------------

RAW_NOVEL_FILENAME = "raw_novel_context.txt"
_EXTRACT_FAILED = "Glossary extraction failed."


def novel_glossary_job_id(drama_id: int) -> str:
    return f"novel_glossary_{drama_id}"


def _read_drama_file(drama_id: int, filename: Optional[str]) -> str:
    if not filename:
        return ""
    path = os.path.join(db.drama_dir(drama_id), filename)
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _default_engine() -> str:
    from services import settings_service
    return settings_service.get_default_engine()


def novel_glossary_engine(drama_id: int) -> str:
    """The engine a run would use (the drama's translation_engine, else
    the Settings default engine) -- for the router's engines.paid gate."""
    return _drama(drama_id).get("translation_engine") or _default_engine()


# A proposal is High confidence when the term turns up at least this often
# in the source and every window that proposed it agreed on one rendering;
# anything else is Low. Deliberately simple so a reviewer can predict it.
HIGH_CONFIDENCE_MIN_OCCURRENCES = 3
MAX_ALTERNATIVES = 5


def _confidence(occurrences: int, alternatives: list, translation: str) -> str:
    consistent = bool(translation) and not alternatives
    return "high" if consistent and occurrences >= HIGH_CONFIDENCE_MIN_OCCURRENCES else "low"


def _normalize_proposals(proposals, known_terms, source_text: str = "") -> list:
    """Allowlisted proposal dicts keyed by term text (a repeated term keeps
    the last one); already_in_glossary is the snapshot at extraction time.
    occurrences counts the term in source_text (the text the model read);
    alternatives are the other renderings earlier windows proposed."""
    known = {t["term_original"] for t in known_terms}
    out = {}
    for p in proposals or []:
        term = p.get("term") if isinstance(p, dict) else None
        term = term.strip() if isinstance(term, str) else ""
        if not term or len(term) > MAX_TERM_LEN:
            continue
        translation = str(p.get("suggested_translation") or "").strip()[:MAX_TERM_LEN]
        renderings = p.get("renderings") if isinstance(p.get("renderings"), list) else []
        alternatives = [r.strip()[:MAX_TERM_LEN] for r in renderings
                        if isinstance(r, str) and r.strip() and r.strip() != translation]
        alternatives = list(dict.fromkeys(alternatives))[:MAX_ALTERNATIVES]
        occurrences = source_text.count(term) if source_text else 0
        out[term] = {
            "term": term,
            "suggested_translation": translation,
            "occurrences": occurrences,
            "alternatives": alternatives,
            "confidence": _confidence(occurrences, alternatives, translation),
            "category": p.get("category") if p.get("category") in TERM_CATEGORIES else None,
            "policy": p.get("policy") if p.get("policy") in TERM_POLICIES else None,
            "reason": str(p.get("reason") or "")[:MAX_NOTES_LEN],
            "already_in_glossary": term in known,
        }
    return list(out.values())


def _novel_glossary_cache(engine, engine_name, fresh=False):
    """Each passage's reply is cached on (prompt, engine and
    model, max_tokens), so re-running an interrupted extraction only pays
    for the passages it never finished. fresh: skip the cached replies (new
    ones still replace them)."""
    model = f"{engine_name}:{getattr(engine, 'model', '') or ''}"
    settings = {"max_tokens": 4000}

    # A cache failure (a locked or full disk) must never fail a paid run.
    def get(prompt):
        if fresh:
            return None
        try:
            return job_checkpoint_service.cache_get(
                "glossary_from_novel", job_checkpoint_service.hash_text(prompt), model,
                settings)
        except Exception:
            return None

    def put(prompt, text):
        try:
            job_checkpoint_service.cache_put(
                "glossary_from_novel", job_checkpoint_service.hash_text(prompt), model,
                settings, text)
        except Exception:
            pass
    return get, put


def _bounded_calls(job_id):
    """Deadline and Cancel for the job's LLM calls, with retry notices in its
    message so a wait never looks like a hang."""
    def on_wait(delay, next_attempt, max_retries):
        # Keep the bar where it is: this notice can arrive mid-run.
        current = (background_jobs.get_status(job_id) or {}).get("progress") or 0.0
        background_jobs.update_progress(
            job_id, current, f"The AI engine is slow or busy; retrying (attempt {next_attempt} of {max_retries})...")
    return llm_tasks.bounded_llm_calls(
        job_id, lambda: background_jobs.is_cancel_requested(job_id), on_wait, no_thinking=True)


def _run_novel_glossary_job(job_id, run_id, drama_id, engine, engine_name, src_text, en_text,
                            source_language, known_terms, fresh=False):
    try:
        with _bounded_calls(job_id):
            proposals = tguide.extract_glossary_from_novel(
                src_text, engine, source_language=source_language, english_translation=en_text,
                known_terms=known_terms,
                response_cache=_novel_glossary_cache(engine, engine_name, fresh),
                progress_cb=lambda f: background_jobs.update_progress(
                    job_id, f, f"Reading... {f * 100:.0f}%"),
                usage_cb=lambda inp, out: db.log_usage(
                    drama_id, engine_name, getattr(engine, "model", engine_name),
                    "glossary_from_novel", inp, out,
                    translate_engines.estimate_cost_for_engine(engine, inp, out)))
    except TranslationCancelled:
        raise background_jobs.JobCancelled() from None
    except Exception as exc:
        # Engine errors can echo request details; never surface them raw.
        raise RuntimeError(
            _EXTRACT_FAILED + " " + translate_engines.redact_secrets(str(exc))) from None
    background_jobs.set_result(job_id, {"proposals": _normalize_proposals(proposals, known_terms, src_text),
                                        "run_id": run_id})


# The run_id of each extraction job id's latest started run, for the status
# of a run that isn't done yet (a done run's own result carries its run_id,
# which is what an apply is checked against).
_run_ids: dict = {}
_run_ids_lock = threading.Lock()


def _start_extraction_job(job_id: str, target, *args, **kwargs) -> bool:
    run_id = uuid.uuid4().hex
    # Start and record the run_id under one lock, so a run-scoped cancel
    # (_cancel_extraction) never sees the new job with the old run_id.
    with _run_ids_lock:
        started = background_jobs.start_job(job_id, target, job_id, run_id, *args, **kwargs)
        if started:
            _run_ids[job_id] = run_id
    return started


def _cancel_extraction(drama_id: int, job_id: str, run_id) -> dict:
    """Cancels this drama's extraction only when the held run is `run_id`
    (the one the caller is looking at), so a stale Cancel can't stop a newer
    run. ConflictError when another run is held or the run has finished."""
    _drama(drama_id)
    if not isinstance(run_id, str) or not run_id or len(run_id) > 64:
        raise InvalidInputError("run_id must be the run_id from the extraction's status.")
    from services import jobs_service
    with _run_ids_lock:
        job = background_jobs.get_status(job_id)
        if _current_run_id_locked(job_id, job) != run_id:
            raise ConflictError("That extraction was replaced by a newer run; nothing was "
                                "cancelled.")
        if not job or job.get("status") not in ("running", "queued"):
            raise ConflictError("That extraction has already finished.")
        return jobs_service.cancel_job(job_id)


def cancel_novel_glossary_run(drama_id: int, run_id: str) -> dict:
    """Cancel this drama's glossary-from-novel run `run_id` (see
    _cancel_extraction). Returns jobs_service.cancel_job's result."""
    return _cancel_extraction(drama_id, novel_glossary_job_id(drama_id), run_id)


def cancel_lines_glossary_run(drama_id: int, run_id: str) -> dict:
    """Cancel this drama's glossary-from-lines run `run_id`."""
    return _cancel_extraction(drama_id, lines_glossary_job_id(drama_id), run_id)


def _current_run_id_locked(job_id: str, job: Optional[dict]) -> Optional[str]:
    """_current_run_id for a caller already holding _run_ids_lock."""
    if not job:
        return None
    if job.get("status") == "done":
        return (job.get("result") or {}).get("run_id") or None
    return _run_ids.get(job_id)


def _current_run_id(job_id: str, job: Optional[dict]) -> Optional[str]:
    with _run_ids_lock:
        return _current_run_id_locked(job_id, job)


def _glossary_engine(drama: dict, engine_name: Optional[str]):
    """(engine_name, engine) for an extraction run on the drama's stored
    translation_engine. engine_name is the one the caller's gate checked;
    ConflictError if the stored engine changed since."""
    from services import settings_service, translate_service

    stored_engine = drama.get("translation_engine") or settings_service.get_default_engine()
    if engine_name is not None and engine_name != stored_engine:
        raise ConflictError("This title's engine changed; check it and start again.")
    engine_name = stored_engine
    cls = translate_engines.ENGINES.get(engine_name)
    if cls is None or not getattr(cls, "supports_reference", False):
        raise UnsupportedOperationError("This title's engine can't extract a glossary.")
    api_key = translate_service.resolve_api_key(engine_name)
    if not api_key:
        raise MissingKeyError(engine_name)
    engine = translate_engines.get_engine(
        engine_name, api_key,
        free_tier=engine_name == "gemini" and settings_service.get_gemini_free_tier(),
        base_url=(settings_service.resolve_key("ollama_url") or None)
        if engine_name == "ollama" else None)
    return engine_name, engine


def start_novel_glossary_run(drama_id: int, engine_name: Optional[str] = None,
                             fresh: bool = False) -> dict:
    """Starts proposing glossary terms from this drama's saved novel text.
    Uses the saved original-language novel (raw_novel_context.txt) as the
    source and the saved novel translation as the paired rendering when
    both exist; either alone is used as the source.
    Paid-engine spend: yes, unless the engine is in FREE_ENGINES.

    engine_name: the engine a caller already authorized (the API route's
    engines.paid gate). When given and the drama's stored engine no longer
    matches it (changed in between), ConflictError -- the run, and the key
    resolved for it, never use an engine the caller didn't check.

    Poll get_novel_glossary_status(drama_id) (GET /api/glossary/dramas/{id}/
    from-novel); its "done" result is {"proposals": [{term,
    suggested_translation, category, policy, reason, already_in_glossary}]}.

    NotFoundError, UnsupportedOperationError (no series / no novel saved /
    engine can't do this / monthly cap used up, capped engines only, as
    start_lines_glossary_run), DependencyUnavailableError (no key),
    ConflictError (already running).

    fresh: ignore replies cached by an earlier run over the same
    text and engine and ask the model again (the new replies replace them)."""
    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    orig = _read_drama_file(drama_id, RAW_NOVEL_FILENAME)
    novel = _read_drama_file(drama_id, drama.get("novel_reference_filename"))
    if not (orig.strip() or novel.strip()):
        raise UnsupportedOperationError("Attach a novel to this title first.")
    src_text = orig if orig.strip() else novel
    en_text = novel if orig.strip() else ""

    engine_name, engine = _glossary_engine(drama, engine_name)
    from services import settings_service, translate_run_service
    translate_run_service.refuse_when_cap_spent(engine_name,
                                                settings_service.get_gemini_free_tier())
    job_id = novel_glossary_job_id(drama_id)
    started = _start_extraction_job(
        job_id, _run_novel_glossary_job, drama_id, engine, engine_name, src_text,
        en_text, drama.get("source_language") or "zh", db.list_glossary_terms(sid),
        gpu_touching=engine_name == "ollama",
        description=f"Glossary from novel (title #{drama_id})", fresh=bool(fresh))
    if not started:
        raise ConflictError("A glossary extraction is already running for this title.")
    return {"job_id": job_id, "engine": engine_name, "paired": bool(en_text)}


_PROPOSAL_FIELDS = ("term", "suggested_translation", "category", "policy", "reason",
                    "already_in_glossary", "occurrences", "alternatives", "confidence")


# Bounds the ignore list a shared-series editor can grow, since every status
# poll and list call reads it.
MAX_DISMISSALS_PER_SERIES = 5000


def _dismissals(drama: dict) -> list:
    sid = drama.get("series_id")
    return db.list_glossary_dismissals(sid) if sid else []


def _extraction_status(drama_id: int, job_id: str) -> dict:
    drama = _drama(drama_id)
    job = background_jobs.get_status(job_id)
    if not job:
        return {"job_id": "", "status": "idle", "progress": 0.0, "message": "",
                "result": None, "run_id": None}
    status = job.get("status")
    result = None
    if status == "done":
        held = [p.get("term") for p in (job.get("result") or {}).get("proposals") or []
                if isinstance(p, dict) and isinstance(p.get("term"), str)]
        sid = drama.get("series_id")
        ignored = db.list_dismissed_glossary_terms(sid, held) if sid else set()
        result = {"proposals": [{**{k: p.get(k) for k in _PROPOSAL_FIELDS},
                                 "occurrences": p.get("occurrences") or 0,
                                 "alternatives": p.get("alternatives") or [],
                                 "confidence": p.get("confidence") or "low"}
                                for p in (job.get("result") or {}).get("proposals") or []
                                if isinstance(p, dict) and p.get("term") not in ignored]}
    message = job.get("error") if status == "error" else job.get("message")
    return {"job_id": job_id, "status": status, "progress": job.get("progress"),
            "message": translate_engines.redact_secrets(str(message)) if message else "",
            "result": result, "run_id": _current_run_id(job_id, job)}


def get_novel_glossary_status(drama_id: int) -> dict:
    """{job_id, status, progress, message, result, run_id} for this drama's
    glossary-from-novel job; result is {"proposals": [...]} only when done
    (else None). run_id names this run (None if unknown); pass it back to
    the apply. The message (or a failed job's error) is redacted; no key
    is ever in a job result. NotFoundError when the drama doesn't exist. No
    such job resident in this process (results live only in background_jobs
    memory) is the normal first answer: status "idle", job_id ""."""
    return _extraction_status(drama_id, novel_glossary_job_id(drama_id))


_OVERRIDE_KEYS = ("translation", "category", "policy")


def _clean_overrides(overrides) -> dict:
    """{term: {translation?, category?, policy?}} -> validated copy. A key
    present means "use this value"; an absent key keeps the proposal's."""
    if overrides is None:
        return {}
    if not isinstance(overrides, dict) or len(overrides) > 1000:
        raise InvalidInputError("overrides must be an object keyed by term.")
    out = {}
    for term, edit in overrides.items():
        if not isinstance(term, str) or not isinstance(edit, dict) \
                or not set(edit) <= set(_OVERRIDE_KEYS):
            raise InvalidInputError("Each override must be {translation, category, policy}.")
        clean = {}
        if "translation" in edit:
            clean["translation"] = _text(edit["translation"], "translation", MAX_TERM_LEN,
                                         required=True)
        if "category" in edit:
            if edit["category"] is not None and edit["category"] not in TERM_CATEGORIES:
                raise InvalidInputError("Unknown category.")
            clean["category"] = edit["category"]
        if "policy" in edit:
            if edit["policy"] is not None and edit["policy"] not in TERM_POLICIES:
                raise InvalidInputError("Unknown policy.")
            clean["policy"] = edit["policy"]
        out[term.strip()] = clean
    return out


PROPOSALS_CHANGED = "The proposals changed since you reviewed them — review again."


def _apply_extraction(drama_id: int, job_id: str, terms: list, overwrite_existing: bool,
                      overrides, run_id: Optional[str], *, run_id_required: bool) -> dict:
    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    if not isinstance(overwrite_existing, bool):
        raise InvalidInputError("overwrite_existing must be true or false.")
    if (not isinstance(terms, (list, tuple)) or not terms or len(terms) > 1000
            or not all(isinstance(t, str) for t in terms)):
        raise InvalidInputError("terms must be a non-empty list of term strings.")
    if run_id is None and run_id_required:
        raise InvalidInputError("run_id is required.")
    if run_id is not None and (not isinstance(run_id, str) or not run_id or len(run_id) > 64):
        raise InvalidInputError("run_id must be the run_id from the extraction's status.")
    edits = _clean_overrides(overrides)
    job = background_jobs.get_status(job_id)
    # A different run than the one reviewed (or none held any more): its
    # proposals would fill fields the user never saw.
    if run_id is not None and _current_run_id(job_id, job) != run_id:
        raise ConflictError(PROPOSALS_CHANGED)
    if not job or job.get("status") != "done":
        raise UnsupportedOperationError("No finished glossary extraction for this title.")
    by_term = {p["term"]: p for p in (job.get("result") or {}).get("proposals") or []}
    existing = {r["term_original"]: r for r in db.list_glossary_terms(sid)}

    report = {"added": [], "overwritten": [], "skipped_existing": [], "unknown": []}
    for term in dict.fromkeys(t.strip() for t in terms):
        p = by_term.get(term)
        edit = edits.get(term, {})
        translation = edit.get("translation", p["suggested_translation"] if p else "")
        if p is None or not translation:
            report["unknown"].append(term)
            continue
        current = existing.get(term)
        if current is not None and not overwrite_existing:
            report["skipped_existing"].append(term)
            continue
        db.upsert_glossary_term(
            sid, term, translation, notes=p["reason"],
            category=edit.get("category", p["category"]),
            policy=edit.get("policy", p["policy"]),
            enforce_exact=bool(current.get("enforce_exact")) if current else False)
        report["overwritten" if current is not None else "added"].append(term)
    return report


def list_glossary_dismissals(drama_id: int) -> dict:
    """{"dismissals": [{term, created_at}]}: the proposals ignored for this
    drama's series (empty for a drama with no series)."""
    return {"dismissals": [{"term": d["term_original"], "created_at": d["created_at"]}
                           for d in _dismissals(_drama(drama_id))]}


def _clean_dismiss_terms(terms) -> list:
    if (not isinstance(terms, (list, tuple)) or not terms or len(terms) > 1000
            or not all(isinstance(t, str) and t.strip() and len(t.strip()) <= MAX_TERM_LEN
                       for t in terms)):
        raise InvalidInputError("terms must be a non-empty list of term strings.")
    return list(dict.fromkeys(t.strip() for t in terms))


def dismiss_glossary_proposals(drama_id: int, terms: list) -> dict:
    """Ignores these terms for the drama's series: the extractions' statuses
    stop listing them. Any text is accepted (a term from a later run needn't
    be in the held one); the series must exist (UnsupportedOperationError).
    {"changed": n} new ignores."""
    sid = _series_id(_drama(drama_id), required=True)
    clean = _clean_dismiss_terms(terms)
    before = {d["term_original"] for d in db.list_glossary_dismissals(sid)}
    if len(before | set(clean)) > MAX_DISMISSALS_PER_SERIES:
        raise InvalidInputError(
            f"A series can ignore at most {MAX_DISMISSALS_PER_SERIES} glossary terms; "
            "restore some before ignoring more.")
    db.add_glossary_dismissals(sid, clean)
    return {"changed": len([t for t in clean if t not in before])}


def restore_glossary_proposals(drama_id: int, terms: list) -> dict:
    """Takes terms off the ignore list; {"changed": n} removed."""
    sid = _series_id(_drama(drama_id), required=True)
    return {"changed": db.remove_glossary_dismissals(sid, _clean_dismiss_terms(terms))}


def apply_novel_glossary(drama_id: int, terms: list, overwrite_existing: bool = False,
                         overrides: Optional[dict] = None, run_id: Optional[str] = None) -> dict:
    """Adds the named proposals (by term text, never list position) from
    this drama's finished extraction to the series glossary. A term already
    in the glossary is left untouched and reported in "skipped_existing"
    unless overwrite_existing is True (then its translation/notes/category/
    policy are replaced; its enforce_exact, aliases and banned list are
    kept). Terms not among the proposals (or proposed with no translation
    and none given in overrides) are reported in "unknown". overrides:
    optional {term: {translation?, category?, policy?}} the user edited in
    review; keys for terms not in `terms` are ignored. run_id: optional,
    the run_id of the status the user reviewed; when given and the held run
    is another one (or none), ConflictError and nothing is written. Returns
    {"added", "overwritten", "skipped_existing", "unknown"} lists of terms."""
    return _apply_extraction(drama_id, novel_glossary_job_id(drama_id), terms,
                             overwrite_existing, overrides, run_id, run_id_required=False)


# ---------------------------------------------------------------------------
# Glossary from the drama's source lines (parity X10)
# ---------------------------------------------------------------------------

def lines_glossary_job_id(drama_id: int) -> str:
    return f"lines_glossary_{drama_id}"


def lines_glossary_engine(drama_id: int) -> str:
    """The engine a lines extraction would use (the same rule as the novel
    one: the drama's translation_engine, default claude)."""
    return novel_glossary_engine(drama_id)


def _run_lines_glossary_job(job_id, run_id, drama_id, engine, engine_name, source_lines,
                            source_language, known_terms):
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled()
    background_jobs.update_progress(job_id, 0.1, f"Scanning {len(source_lines)} lines...")
    try:
        with _bounded_calls(job_id):
            proposals = tguide.extract_terms_llm(
                source_lines, engine, source_language=source_language, known_terms=known_terms,
                usage_cb=lambda inp, out: db.log_usage(
                    drama_id, engine_name, getattr(engine, "model", engine_name),
                    "extract_terms", inp, out,
                    translate_engines.estimate_cost_for_engine(engine, inp, out)))
    except TranslationCancelled:
        raise background_jobs.JobCancelled() from None
    except Exception as exc:
        raise RuntimeError(
            _EXTRACT_FAILED + " " + translate_engines.redact_secrets(str(exc))) from None
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled()
    # `renderings` is the novel path's cross-window tally; a single call has no
    # windows, so a model-supplied one would be an unvetted alternatives source.
    proposals = [{k: v for k, v in p.items() if k != "renderings"}
                 for p in proposals if isinstance(p, dict)]
    background_jobs.set_result(job_id, {"proposals": _normalize_proposals(proposals, known_terms,
                                                                  "\n".join(source_lines)),
                                        "run_id": run_id})


def start_lines_glossary_run(drama_id: int, engine_name: Optional[str] = None) -> dict:
    """Starts proposing glossary terms from this drama's saved source lines
    (`tguide.extract_terms_llm`, which samples up to 400 lines across the
    whole drama). One LLM call on the drama's translation_engine; the
    engine_name rule is start_novel_glossary_run's. Refused before starting
    when this month's spending cap is already used up (capped engines only,
    as translate_run_service). Paid-engine spend: yes, unless the engine is
    in FREE_ENGINES.

    Poll get_lines_glossary_status; apply with apply_lines_glossary.
    NotFoundError, UnsupportedOperationError (no series / no source lines /
    engine can't do this / monthly cap used up), DependencyUnavailableError
    (no key), ConflictError (already running, or the engine changed)."""
    from services import settings_service, translate_run_service

    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    source_lines = [r["zh"] for r in db.load_lines(drama_id) if (r.get("zh") or "").strip()]
    if not source_lines:
        raise UnsupportedOperationError(
            "This title has no source lines yet; transcribe or import them first.")
    engine_name, engine = _glossary_engine(drama, engine_name)
    translate_run_service.refuse_when_cap_spent(engine_name,
                                                settings_service.get_gemini_free_tier())

    job_id = lines_glossary_job_id(drama_id)
    started = _start_extraction_job(
        job_id, _run_lines_glossary_job, drama_id, engine, engine_name, source_lines,
        drama.get("source_language") or "zh", db.list_glossary_terms(sid),
        gpu_touching=engine_name == "ollama",
        description=f"Glossary from lines (title #{drama_id})")
    if not started:
        raise ConflictError("A glossary extraction is already running for this title.")
    return {"job_id": job_id, "engine": engine_name, "line_count": len(source_lines)}


def get_lines_glossary_status(drama_id: int) -> dict:
    """get_novel_glossary_status's contract, for the lines extraction."""
    return _extraction_status(drama_id, lines_glossary_job_id(drama_id))


def apply_lines_glossary(drama_id: int, terms: list, overwrite_existing: bool = False,
                         overrides: Optional[dict] = None, run_id: Optional[str] = None) -> dict:
    """apply_novel_glossary's contract, for the lines extraction's proposals,
    except that run_id is required (InvalidInputError when missing)."""
    return _apply_extraction(drama_id, lines_glossary_job_id(drama_id), terms,
                             overwrite_existing, overrides, run_id, run_id_required=True)

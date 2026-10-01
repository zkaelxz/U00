"""
debug_view.py -- Step 58's "What happened here?" per-line and per-job
debugging view.

Per the roadmap's own instruction: this is a PRESENTATION layer over data
the app already records, not a second data-collection effort. Step 41's
wider reproducibility metadata (per-line prompt/glossary/model version,
per-stage job timing) hasn't been built yet -- every field that would
depend on it is returned with an explicit "not recorded" note instead of
being guessed at, matched positionally, or otherwise fabricated.

  - explain_line():      real, currently-recorded history for one line.
  - explain_job():       a background job's real timing/status.
"""

import db
import background_jobs
import translate_engines

CONTEXT_WINDOW_NOTE = (
    "The actual preceding/upcoming lines shown to the model when this line was "
    "translated aren't recorded -- that context is built fresh per batch and "
    "discarded once translated (needs Step 41's reproducibility metadata). Shown "
    "instead: this line's CURRENT neighbors, which may differ from what was "
    "actually in the prompt at translation time.")

GLOSSARY_MATCH_NOTE = (
    "Best-effort: glossary terms whose source text (or a recorded alias) appears "
    "in this line. Every translation prompt sends the whole glossary, not a "
    "per-line filtered subset, and Baihe doesn't yet record which entries the "
    "model actually used for a specific line (needs Step 41).")

PER_STAGE_TIMING_NOTE = (
    "Per-stage timing (Step 41 item 5) is recorded for jobs run since it was "
    "added; a job that marks no stages shows one \"Whole job\" row. Spend is the "
    "estimate logged while each stage ran.")


def explain_line(drama_id: int, line, all_lines=None, glossary_terms=None,
                  context_before: int = 3, context_after: int = 3) -> dict:
    """Everything Baihe currently knows about how one line got its
    translation. `line` and `all_lines` are core.Line objects (e.g. from
    db.load_line_objects) -- all_lines lets this show the line's current
    neighbors; pass None to skip that section entirely."""
    drama = db.get_drama(drama_id) or {}
    if glossary_terms is None and drama.get("series_id"):
        glossary_terms = db.list_glossary_terms(drama["series_id"])

    notes = [n for n in db.list_translation_notes(drama_id) if n["line_idx"] == line.idx]
    emotion = db.load_emotions(drama_id).get(line.idx)
    edit_samples = [e for e in db.list_edit_samples(drama_id) if e["zh"] == line.zh]
    consistency_issues = [
        c for c in db.load_consistency_issues(drama_id)
        if c.get("term") and (c["term"] in line.zh or c["term"] in (line.en or ""))]

    neighbors_before, neighbors_after = [], []
    if all_lines:
        pos = next((i for i, ln in enumerate(all_lines) if ln.idx == line.idx), None)
        if pos is not None:
            neighbors_before = [(n.idx, n.zh, n.en)
                                for n in all_lines[max(0, pos - context_before):pos]]
            neighbors_after = [(n.idx, n.zh, n.en)
                               for n in all_lines[pos + 1:pos + 1 + context_after]]

    from services import line_provenance_service
    provenance = (line_provenance_service.get(drama_id, line.id, current_en=line.en or "")
                  if getattr(line, "id", None) is not None else None)
    versions = db.list_translation_versions(drama_id)
    active_version = next((v for v in versions if v["is_active"]), None)
    if provenance:
        engine, model = provenance["engine"], provenance["model"]
        engine_source = "recorded when this line was last translated"
    elif active_version:
        engine, model = active_version["engine"], active_version["model"]
        engine_source = "this drama's active saved translation version"
    else:
        engine, model = drama.get("translation_engine"), None
        engine_source = ("this drama's currently configured engine -- not necessarily what "
                         "produced this line's current text, if the engine has changed since")

    return {
        "line_id": line.id,
        "line_idx": line.idx,
        "zh": line.zh,
        "en": line.en,
        "speaker": line.speaker,
        "speaker_manual": bool(line.speaker_manual),
        "flag": line.flag,
        "flag_reason": translate_engines.flag_reason_label(line.flag) if line.flag else None,
        "flag_note": line.flag_note,
        "translation_notes": notes,
        "emotion": emotion,
        "edit_samples": edit_samples,
        "consistency_issues": consistency_issues,
        "glossary_matches": translate_engines.matching_glossary_terms(line.zh, glossary_terms),
        "glossary_matches_note": GLOSSARY_MATCH_NOTE,
        "context_window_used": None,
        "context_window_note": CONTEXT_WINDOW_NOTE,
        "current_neighbors_before": neighbors_before,
        "current_neighbors_after": neighbors_after,
        "engine": engine,
        "model": model,
        "engine_source": engine_source,
        "provenance": provenance,
        "prompt_version_note": line_provenance_service.describe(provenance),
    }


def explain_job(job_id: str) -> dict:
    """A background job's real recorded status/timing. Jobs live only in
    background_jobs' in-memory tracker -- a job from a previous process
    (an app restart) has nothing here at all."""
    job = background_jobs.get_status(job_id)
    if not job:
        return {"job_id": job_id, "found": False}
    started, finished = job.get("started_at"), job.get("finished_at")
    duration = (finished - started) if started and finished else None
    try:
        from services import job_timing_service
        runs = job_timing_service.list_runs(job_id, limit=1)
    except Exception:
        runs = []
    return {
        "job_id": job_id,
        "found": True,
        "status": job.get("status"),
        "description": job.get("description"),
        "message": job.get("message"),
        "error": job.get("error"),
        "gpu_touching": bool(job.get("gpu_touching")),
        "started_at": started,
        "finished_at": finished,
        "duration_seconds": duration,
        "per_stage_breakdown": runs[0]["stages"] if runs else None,
        "per_stage_breakdown_note": PER_STAGE_TIMING_NOTE,
    }

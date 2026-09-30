"""
debug_view.py -- Step 58's "What happened here?" per-line and per-job
debugging view, plus bug record-and-replay.

Per the roadmap's own instruction: this is a PRESENTATION layer over data
the app already records, not a second data-collection effort. Step 41's
wider reproducibility metadata (per-line prompt/glossary/model version,
per-stage job timing) hasn't been built yet -- every field that would
depend on it is returned with an explicit "not recorded" note instead of
being guessed at, matched positionally, or otherwise fabricated.

  - explain_line():      real, currently-recorded history for one line.
  - explain_job():       a background job's real timing/status.
  - save_bug_bundle():   freezes one line's current input as a re-runnable
                          reproduction case.
  - replay_bug_bundle(): re-runs a saved bundle and checks whether it
                          still reproduces the same output.
"""

import json

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
    provenance = (line_provenance_service.get(drama_id, line.id)
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


def save_bug_bundle(drama_id: int, line, all_lines, engine_name: str, model: str,
                     glossary_terms=None, style_guidelines: str = "", locale: str = "en-US",
                     label: str = None, context_window: int = 6,
                     context_window_ahead: int = 3) -> int:
    """Freezes exactly what a replay needs: the same recent/upcoming
    context translate_lines_with_engine would build for this line right
    now, plus the settings and the (bad/flagged) output -- a saved,
    re-runnable case rather than only a description in words."""
    drama = db.get_drama(drama_id) or {}
    recent_context, upcoming_lines = [], []
    pos = next((i for i, ln in enumerate(all_lines) if ln.idx == line.idx), None)
    if pos is not None:
        if context_window > 0:
            preceding = all_lines[max(0, pos - context_window):pos]
            recent_context = [(ln.zh, ln.en) for ln in preceding if ln.en.strip()]
        if context_window_ahead > 0:
            upcoming = all_lines[pos + 1:pos + 1 + context_window_ahead]
            upcoming_lines = [ln.zh for ln in upcoming if ln.zh.strip()]

    input_snapshot = {
        "zh": line.zh,
        "speaker": line.speaker,
        "recent_context": recent_context,
        "upcoming_lines": upcoming_lines,
        "glossary_terms": glossary_terms or [],
        "style_guidelines": style_guidelines or "",
        "locale": locale,
        "drama_meta": {
            "source_language": drama.get("source_language", "zh"),
            "title_en": drama.get("title_en"),
            "title_zh": drama.get("title_zh"),
        },
    }
    return db.save_bug_report(
        drama_id=drama_id, line_id=line.id, label=label or f"Line #{line.idx + 1}",
        input_json=json.dumps(input_snapshot, ensure_ascii=False),
        engine=engine_name, model=model or "", produced_output=line.en,
        flag=line.flag, flag_note=line.flag_note)


def replay_bug_bundle(report_id: int, engine) -> dict:
    """Re-runs a saved bundle's exact recorded input through `engine` and
    checks whether it still produces the same output. Pass a fresh
    instance of the bundle's own recorded engine to check "does this
    still fail", or a different engine to check whether the failure is
    engine-specific."""
    report = db.get_bug_report(report_id)
    if not report:
        return None
    snapshot = json.loads(report["input_json"])
    context = translate_engines.build_translation_context(
        engine, snapshot.get("drama_meta"), locale=snapshot.get("locale", "en-US"),
        glossary_terms=snapshot.get("glossary_terms"),
        style_guidelines=snapshot.get("style_guidelines", ""))
    context["recent_context"] = [tuple(p) for p in snapshot.get("recent_context", [])]
    context["upcoming_lines"] = snapshot.get("upcoming_lines", [])
    context["speaker_labels"] = [snapshot.get("speaker")]
    context["line_ids"] = [report["line_id"]]

    replay_output = engine.translate_batch([snapshot["zh"]], context)[0]
    reproduced = replay_output == report["produced_output"]
    db.update_bug_report_replay(report_id, replay_output, reproduced)
    return {
        "report_id": report_id,
        "reproduced": reproduced,
        "original_output": report["produced_output"],
        "replay_output": replay_output,
    }

"""
services/benchmark_end_to_end_service.py -- Benchmark Lab end-to-end arms: a translation arm with
a `transcribe` recogniser translates what that recogniser hears in each clip, not the reference
transcript, so a transcription setting is judged by what reaches the viewer. The run loop, scoring
and persistence stay in benchmark_lab_service.
"""

import json

import background_jobs
import db
from translate_engines import redact_secrets
from services import transcribe_options_service
from services.benchmark_case_service import (LAB_BACKENDS, ignored_options, model_identity, require_backend_fits_cases,
                                             require_backend_ready, require_whisper_fits_cases,
                                             require_separation_ready, run_transcription, run_translation)
from lib.errors import UnsupportedOperationError


def arm_key(arm):
    """What makes two recogniser arms different: options the recogniser does not read cannot."""
    if not arm:
        return None
    return (arm["engine"], arm["model"], json.dumps(
        {k: v for k, v in arm["options"].items() if k not in ignored_options(arm["engine"])}, sort_keys=True))


def require_recogniser_fits(cfg: dict, cases: list):
    if cfg["engine"] in LAB_BACKENDS:
        require_backend_fits_cases(cfg["engine"], cases)
        require_backend_ready(cfg["engine"], cases, cfg["options"]["refine_timing"])
    else:
        require_whisper_fits_cases("transcription", [cfg], cases)


def audio_cases(cases: list) -> tuple:
    """({translation case id: the audio case of the same clip}, the cases with none). A clip import makes both
    cases from one clip_key; an audio case that was switched off counts as missing, as it is out of scoring."""
    found, missing = {}, []
    for case in cases:
        siblings = db.list_benchmark_cases_by_clip(case["clip_key"]) if case.get("clip_key") else []
        audio = next((c for c in siblings if c.get("stage") == "transcription" and c.get("input_filename")
                      and not c.get("excluded")), None)
        if audio is None:
            missing.append(case)
        else:
            found[case["id"]] = audio
    return found, missing


def require_audio_for(arms: list, cases: list):
    """Refuses before anything runs when a case has no clip to recognise or a recogniser can't take it."""
    if not arms:
        return
    missing = audio_cases(cases)[1]
    if missing:
        raise UnsupportedOperationError(
            f"{len(missing)} of these cases have no usable audio clip, so there is nothing to recognise. "
            "End-to-end runs need cases made by importing clips.")
    for arm in arms:
        require_recogniser_fits(arm, cases)


def _not_heard(error: str, seconds, text: str = "") -> dict:
    return {"output_text": "", "error": error, "cost_usd": 0.0, "usage": {}, "recognition_failed": True,
            "duration_seconds": seconds or 0.0, "recognised_text": text}


def translate_recognised(engine, arm: dict, case: dict, api_key, use_gpu, folder, job_id) -> dict:
    """One end-to-end case: the clip is recognised at the arm's settings and what was heard is translated;
    the caller scores it against the reference translation."""
    clip = audio_cases([case])[0].get(case["id"])
    if clip is None:
        return _not_heard("The clip audio is missing.", 0.0)
    heard = run_transcription(arm, clip, use_gpu, folder, lambda: background_jobs.is_cancel_requested(job_id))
    text = (heard.get("output_text") or "").strip()
    failure = heard.get("error") or (None if text else "Nothing was recognised in this clip.")
    # A late cancel must not bill a translation call for a result the run drops.
    if failure or background_jobs.is_cancel_requested(job_id):
        return _not_heard(redact_secrets(failure or "Cancelled."), heard.get("duration_seconds"), text)
    r = run_translation(engine, dict(case, source_text=text), api_key)
    r["recognised_text"] = text
    r["duration_seconds"] = (r.get("duration_seconds") or 0.0) + (heard.get("duration_seconds") or 0.0)
    return r


def run_context(arm: dict, use_gpu) -> dict:
    """What a session records about its recogniser, beside the translation engine's own entries."""
    defaults = transcribe_options_service.lab_option_defaults()
    return {"use_gpu": use_gpu, "end_to_end": {
        "engine": arm["engine"], "model": arm["model"], "options": arm["options"],
        "changes": {k: v for k, v in arm["options"].items() if v != defaults[k]},
        "options_ignored": ignored_options(arm["engine"]), "model_identity": model_identity("transcription", arm)}}


def check_start(stage: str, checked: list, judge, use_gpu):
    """Refusals a run makes once, before it starts: the judge scores a translation of the reference transcript,
    and a recogniser that separates vocals first needs that model already downloaded."""
    arms = [c["transcribe"] for c in checked if c.get("transcribe")]
    if judge and arms:
        raise UnsupportedOperationError("The judge can't score an end-to-end arm.")
    require_separation_ready(checked if stage == "transcription" else arms, use_gpu)

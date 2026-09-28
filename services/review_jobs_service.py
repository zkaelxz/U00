"""
services/review_jobs_service.py -- Migration Slice 44: the Review stage's
AI checks as background jobs that do everything themselves (DB write
included): consistency check, emotion tagging, translation notes, the
"needs a second look" flag pass, and bulk fix-flagged-lines. Streamlit-free.

Each start function validates, builds the engine from settings-resolved keys
(never a client-supplied key or URL; D2), copies the drama's lines from the
DB (with their permanent ids and `orig` baseline) and hands them to the same
runner the Workspace buttons use (services/workspace_job_service.py), so the
writes stay field-scoped: flag/flag_note (flag), the consistency-issues /
emotions / translation-notes tables (keyed by permanent line id), and
zh/en/flag/flag_note for fix-flagged. Never `fields=None`, and a field the
user changed in the meantime is not overwritten (db.save_lines diffs
against `orig`). AI results are matched by id inside the engine helpers,
never by list position.

Out of scope: the Claude/Gemini bulk (batch API) variants, Auto QC and the
pacing auto-shorten (not jobs), and the restructure tools.
"""
import os
from typing import Optional

import background_jobs
import core
import db
import translate_engines
from services import (settings_service, translate_run_service, translate_service,
                      workspace_job_service)
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, UnsupportedOperationError)

# kind -> (job id prefix, human label)
_KINDS = {
    "consistency": ("consistency_", "consistency check"),
    "emotion": ("emotion_", "emotion tagging"),
    "notes": ("notes_", "translation notes"),
    "flag": ("flag_", "review flagging"),
    "fix-flagged": ("fixflag_", "fix flagged lines"),
}


def _start(kind: str, drama_id: int, engine_name: Optional[str], model: Optional[str],
           gemini_free_tier: bool, runner, make_args, allow_translation_only: bool = False,
           precheck=None) -> dict:
    """make_args(drama, lines, engine, engine_name) -> (args, extra kwargs)
    for start_job after the job id."""
    prefix, label = _KINDS[kind]
    drama = translate_run_service._require_drama(drama_id)
    lines = core.lines_from_rows(db.load_lines(drama_id))
    if not lines:
        raise UnsupportedOperationError("This drama has no lines yet.")
    if precheck:
        precheck(lines)
    engine_name = engine_name or drama.get("translation_engine") or "claude"
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError("Unknown engine.")
    if not allow_translation_only and engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(
            f"{engine_name} is a translation-only engine and can't run this check.")
    if (gemini_free_tier and engine_name == "gemini"
            and model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS):
        raise UnsupportedOperationError("That model isn't available on Gemini's free tier.")
    api_key = translate_service._resolve_api_key(engine_name)
    if api_key is None and engine_name != "nllb":
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")
    job_id = f"{prefix}{drama_id}"
    if background_jobs.is_running(job_id):
        raise ConflictError(f"A {label} is already running for this drama.")
    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=engine_name == "gemini" and gemini_free_tier,
        base_url=(settings_service.resolve_key("ollama_url") or None)
        if engine_name == "ollama" else None)
    args, kwargs = make_args(drama, lines, engine, engine_name)
    kwargs.setdefault("gpu_touching", engine_name == "ollama")
    started = background_jobs.start_job(
        job_id, runner, job_id, drama_id, *args,
        description=f"{label.capitalize()} (drama #{drama_id})", **kwargs)
    if not started:
        raise ConflictError(f"A {label} is already running for this drama.")
    return {"job_id": job_id, "drama_id": drama_id, "kind": kind, "engine": engine_name,
            "model": getattr(engine, "model", model), "line_count": len(lines)}


def start_consistency_check(drama_id: int, engine_name: str = None, model: str = None,
                            gemini_free_tier: bool = False) -> dict:
    """Same-term-translated-differently check; saves the consistency issues."""
    return _start("consistency", drama_id, engine_name, model, gemini_free_tier,
                  workspace_job_service.run_consistency_job,
                  lambda d, lines, eng, name: ((lines, eng, name), {}))


def start_emotion_tagging(drama_id: int, engine_name: str = None, model: str = None,
                          gemini_free_tier: bool = False,
                          use_audio_cues: bool = None) -> dict:
    """Emotional register per line, saved by permanent line id. use_audio_cues
    defaults on when the drama has audio, like the tab."""
    def make_args(drama, lines, eng, name):
        cues = bool(drama.get("audio_filename")) if use_audio_cues is None else use_audio_cues
        return (lines, eng, cues, name), {}
    return _start("emotion", drama_id, engine_name, model, gemini_free_tier,
                  workspace_job_service.run_emotion_job, make_args)


def start_translation_notes(drama_id: int, engine_name: str = None, model: str = None,
                            gemini_free_tier: bool = False) -> dict:
    """Notes for readers, saved to the notes table by permanent line id."""
    return _start("notes", drama_id, engine_name, model, gemini_free_tier,
                  workspace_job_service.run_translation_notes_job,
                  lambda d, lines, eng, name: ((lines, eng, name), {}))


def start_flag_review(drama_id: int, engine_name: str = None, model: str = None,
                      gemini_free_tier: bool = False) -> dict:
    """LLM 'needs a second look' pass; writes only flag/flag_note by line id."""
    def precheck(lines):
        if not any((ln.en or "").strip() for ln in lines):
            raise UnsupportedOperationError("There are no translated lines to review yet.")
    return _start("flag", drama_id, engine_name, model, gemini_free_tier,
                  workspace_job_service.run_flag_job,
                  lambda d, lines, eng, name: ((lines, eng, name), {}), precheck=precheck)


def start_fix_flagged(drama_id: int, engine_name: str = None, model: str = None,
                      gemini_free_tier: bool = False,
                      job_cost_cap_usd: float = None) -> dict:
    """Re-transcribes (when the drama has audio) and re-translates every
    currently flagged line, clearing the flag on lines it changed. Stops at
    the spending cap, keeping what was fixed."""
    if job_cost_cap_usd is not None and job_cost_cap_usd < 0:
        raise InvalidInputError("job_cost_cap_usd can't be negative.")

    def precheck(lines):
        if not any(ln.flag for ln in lines):
            raise UnsupportedOperationError("No lines are flagged.")

    def make_args(drama, lines, eng, name):
        cap = None
        if translate_run_service._cap_applies(name, gemini_free_tier):
            monthly = translate_run_service._monthly_cap()
            cap, refusal = translate_engines.resolve_cost_cap(
                job_cost_cap_usd, monthly, db.get_month_spend() if monthly else 0.0)
            if refusal:
                raise UnsupportedOperationError(refusal)
        audio = drama.get("audio_filename")
        audio_path = os.path.join(db.drama_dir(drama["id"]), audio) if audio else None
        args = (lines, audio_path, drama.get("whisper_size") or core.DEFAULT_WHISPER_SIZE,
                settings_service.get_use_gpu(), drama.get("source_language") or "zh", eng, name)
        return args, {"cost_cap_usd": cap,
                      "gpu_touching": bool(audio_path) or name == "ollama"}
    return _start("fix-flagged", drama_id, engine_name, model, gemini_free_tier,
                  workspace_job_service.run_fix_flagged_lines_job, make_args,
                  allow_translation_only=True, precheck=precheck)

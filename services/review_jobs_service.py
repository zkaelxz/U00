"""
services/review_jobs_service.py -- the Review stage's
AI checks as background jobs that do everything themselves (DB write
included): consistency check, emotion tagging, translation notes, the
"needs a second look" flag pass, and bulk fix-flagged-lines.

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

bulk=True (parity R49) submits consistency / emotion / notes / flag through
Claude's or Gemini's batch API at half price instead (bulk_translate's
submit_bulk_* functions; submit_bulk_review below is the glue). The job
`bulk_<kind>_<drama_id>` submits, then polls until the batch is applied
(translate_run_service.run_bulk_translate_job). Results are applied by
bulk_translate itself, matched to lines by the request's custom_id and the
permanent line ids recorded with it, never by position, with field-scoped
writes (flag/flag_note; the emotions/notes/consistency tables). The batch
shows up in the drama's bulk job list and is cancelled like any other one.

Out of scope: Auto QC and the pacing auto-shorten (not jobs), and the
restructure tools.
"""
import os
from typing import Optional

import background_jobs
import bulk_translate
import core
import db
import translate_engines
from services import (settings_service, transcribe_service, translate_run_service,
                      translate_service, workspace_job_service)
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

# bulk_translate kind -> its submit function. The API's "notes" is the
# batch kind "translation_notes".
_BULK_SUBMIT = {
    "flag": bulk_translate.submit_bulk_flag,
    "consistency": bulk_translate.submit_bulk_consistency,
    "emotion": bulk_translate.submit_bulk_emotion,
    "translation_notes": bulk_translate.submit_bulk_translation_notes,
}
_BULK_KIND = {"consistency": "consistency", "emotion": "emotion",
              "notes": "translation_notes", "flag": "flag"}


def bulk_job_id(kind: str, drama_id: int) -> str:
    return f"bulk_{kind}_{drama_id}"


def submit_bulk_review(kind: str, drama_id: int, engine, engine_choice: str,
                       **submit_kwargs):
    """Submits a flag/consistency/emotion/translation_notes batch (kind is
    the bulk_translate kind) with the drama's lines fresh from the database,
    so each carries its permanent id. Returns (bulk_job_id, provider)."""
    lines = db.load_line_objects(drama_id)
    provider = bulk_translate.make_provider(engine_choice, engine)
    bulk_id = _BULK_SUBMIT[kind](drama_id, lines, engine, engine_choice,
                                 provider=provider, **submit_kwargs)
    return bulk_id, provider


def _start_bulk(kind: str, drama_id: int, engine, engine_name: str, line_count: int,
                submit_kwargs: dict) -> dict:
    """The bulk half of _start: one background job that submits the batch
    and polls it until bulk_translate has applied the results."""
    bulk_kind = _BULK_KIND[kind]
    job_id = bulk_job_id(kind, drama_id)
    pending = db.BULK_PENDING_STATUSES + ("submitting", "running")
    if background_jobs.is_running(job_id) or any(
            (j.get("kind") or "translate") == bulk_kind
            for j in db.list_bulk_jobs(drama_id, statuses=pending)):
        raise ConflictError(f"A bulk {_KINDS[kind][1]} is already pending for this drama.")

    def submit():
        return submit_bulk_review(bulk_kind, drama_id, engine, engine_name, **submit_kwargs)[0]
    started = background_jobs.start_job(
        job_id, translate_run_service.run_bulk_translate_job, job_id, engine, engine_name, submit,
        translate_run_service.month_cap_usd() or None,
        description=f"Bulk {_KINDS[kind][1]} (drama #{drama_id})")
    if not started:
        raise ConflictError(f"A bulk {_KINDS[kind][1]} is already pending for this drama.")
    return {"job_id": job_id, "drama_id": drama_id, "kind": kind, "engine": engine_name,
            "model": getattr(engine, "model", None), "line_count": line_count, "bulk": True}


def _start(kind: str, drama_id: int, engine_name: Optional[str], model: Optional[str],
           gemini_free_tier: bool, runner, make_args, allow_translation_only: bool = False,
           precheck=None, bulk: bool = False, bulk_kwargs=None) -> dict:
    """make_args(drama, lines, engine, engine_name) -> (args, extra kwargs)
    for start_job after the job id. bulk_kwargs(drama) -> extra kwargs for
    the kind's bulk_translate submit function."""
    prefix, label = _KINDS[kind]
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    drama = translate_run_service.require_drama(drama_id)
    lines = core.lines_from_rows(db.load_lines(drama_id))
    if not lines:
        raise UnsupportedOperationError("This drama has no lines yet.")
    if precheck:
        precheck(lines)
    engine_name = engine_name or drama.get("translation_engine") or settings_service.get_default_engine()
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    if not allow_translation_only and engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(
            f"{engine_name} is a translation-only engine and can't run this check.")
    if (gemini_free_tier and engine_name == "gemini"
            and model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS):
        raise UnsupportedOperationError("That model isn't available on Gemini's free tier.")
    if bulk and (engine_name not in ("claude", "gemini")
                 or (engine_name == "gemini" and gemini_free_tier)):
        raise UnsupportedOperationError("Bulk mode needs Claude or Gemini (paid) batch APIs.")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None and engine_name != "nllb":
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")
    if bulk:
        translate_run_service.refuse_when_cap_spent(engine_name, gemini_free_tier)
        engine = translate_engines.get_engine(engine_name, api_key, model)
        return _start_bulk(kind, drama_id, engine, engine_name, len(lines),
                           bulk_kwargs(drama) if bulk_kwargs else {})
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
            "model": getattr(engine, "model", model), "line_count": len(lines), "bulk": False}


def start_consistency_check(drama_id: int, engine_name: str = None, model: str = None,
                            gemini_free_tier: bool = None, bulk: bool = False) -> dict:
    """Same-term-translated-differently check; saves the consistency issues."""
    return _start("consistency", drama_id, engine_name, model, gemini_free_tier,
                  workspace_job_service.run_consistency_job,
                  lambda d, lines, eng, name: ((lines, eng, name), {}), bulk=bulk)


def start_emotion_tagging(drama_id: int, engine_name: str = None, model: str = None,
                          gemini_free_tier: bool = None,
                          use_audio_cues: bool = None, bulk: bool = False) -> dict:
    """Emotional register per line, saved by permanent line id. use_audio_cues
    defaults on when the drama has audio."""
    def cues(drama):
        return bool(drama.get("audio_filename")) if use_audio_cues is None else use_audio_cues

    def make_args(drama, lines, eng, name):
        return (lines, eng, cues(drama), name), {}
    return _start("emotion", drama_id, engine_name, model, gemini_free_tier,
                  workspace_job_service.run_emotion_job, make_args, bulk=bulk,
                  bulk_kwargs=lambda drama: {"use_audio_cues": cues(drama)})


def start_translation_notes(drama_id: int, engine_name: str = None, model: str = None,
                            gemini_free_tier: bool = None, bulk: bool = False) -> dict:
    """Notes for readers, saved to the notes table by permanent line id."""
    return _start("notes", drama_id, engine_name, model, gemini_free_tier,
                  workspace_job_service.run_translation_notes_job,
                  lambda d, lines, eng, name: ((lines, eng, name), {}), bulk=bulk)


def start_flag_review(drama_id: int, engine_name: str = None, model: str = None,
                      gemini_free_tier: bool = None, bulk: bool = False) -> dict:
    """LLM 'needs a second look' pass; writes only flag/flag_note by line id."""
    def precheck(lines):
        if not any((ln.en or "").strip() for ln in lines):
            raise UnsupportedOperationError("There are no translated lines to review yet.")
    return _start("flag", drama_id, engine_name, model, gemini_free_tier,
                  workspace_job_service.run_flag_job,
                  lambda d, lines, eng, name: ((lines, eng, name), {}), precheck=precheck,
                  bulk=bulk)


def start_fix_flagged(drama_id: int, engine_name: str = None, model: str = None,
                      gemini_free_tier: bool = None,
                      job_cost_cap_usd: float = None, include_genre_notes: bool = True,
                      default_female_pronouns: bool = False) -> dict:
    """Re-transcribes (when the drama has audio) and re-translates every
    currently flagged line, clearing the flag on lines it changed. Stops at
    the spending cap, keeping what was fixed."""
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    if job_cost_cap_usd is not None and job_cost_cap_usd < 0:
        raise InvalidInputError("job_cost_cap_usd can't be negative.")

    def precheck(lines):
        if not any(ln.flag for ln in lines):
            raise UnsupportedOperationError("No lines are flagged.")

    def make_args(drama, lines, eng, name):
        cap = None
        if translate_run_service.engine_cap_applies(name, gemini_free_tier):
            monthly = translate_run_service.month_cap_usd()
            cap, refusal = translate_engines.resolve_cost_cap(
                job_cost_cap_usd, monthly, db.get_month_spend() if monthly else 0.0)
            if refusal:
                raise UnsupportedOperationError(refusal)
        audio = drama.get("audio_filename")
        audio_path = os.path.join(db.drama_dir(drama["id"]), audio) if audio else None
        args = (lines, audio_path, transcribe_service.stored_whisper_size(drama),
                settings_service.get_use_gpu(), drama.get("source_language") or "zh", eng, name)
        # Settings' defaults, as `cli.py translate` resolves them.
        return args, {"cost_cap_usd": cap,
                      "locale": settings_service.get_preference("default_locale"),
                      "style_note": settings_service.get_preference("default_style_note") or "",
                      "include_genre_notes": include_genre_notes,
                      "default_female_pronouns": default_female_pronouns,
                      "gpu_touching": bool(audio_path) or name == "ollama"}
    return _start("fix-flagged", drama_id, engine_name, model, gemini_free_tier,
                  workspace_job_service.run_fix_flagged_lines_job, make_args,
                  allow_translation_only=True, precheck=precheck)

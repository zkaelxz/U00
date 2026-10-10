"""
services/narration_service.py -- the novel-narration "Chunk & tag speakers"
action for one drama (CLI: `cli.cmd_narrate_prep`).

The `chunk_and_tag` path runs as a background job that does the WHOLE
pipeline (chunk, LLM speaker tagging, history snapshot, DB write, status),
the same "job does everything" decision as the other narration jobs. Poll GET /api/jobs/{id};
background_jobs' own runner redacts secrets from a failed job's error.

Input is the drama's attached novel text (`dub_narration.NOVEL_SOURCE_FILENAME` in
its folder). The LLM engine is chosen per request; its key is resolved
server-side and never returned (D2). Speakers come from
`tag_speakers_by_id`, which returns {chunk idx: label}; the job looks each
chunk's label up by its idx (missing -> "Narrator"), never by list position.

Writes replace the drama's lines wholesale, exactly as the CLI does (the
lines are brand new), but only after a "before chunk & tag
speakers" history snapshot of whatever lines existed.

No FastAPI import.
"""
import os
from typing import Optional

import background_jobs
import core as core_module
import db
import dub_narration
import translate_engines
from core import Line
from services import job_checkpoint_service, job_timing_service, settings_service
from services.service_errors import (
    ConflictError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
)

DEFAULT_ENGINE = "claude"
# LLM-capable engines that can tag speakers (pure MT engines cannot).
TAG_ENGINES = ["claude", "deepseek", "gemini", "openai", "ollama"]
MAX_CHUNK_CHARS = 200  # core.chunk_novel_text's own default


def _require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _read_novel(drama_id: int) -> str:
    # Not db.drama_dir: that creates the folder, and config is a read.
    path = os.path.join(db.DRAMAS_DIR, str(drama_id), dub_narration.NOVEL_SOURCE_FILENAME)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _api_key(engine_name: str) -> Optional[str]:
    if engine_name == "ollama":
        return "local"  # a locally-run server; nothing secret to pass
    return settings_service.resolve_key(engine_name)


def get_narration_config(drama_id: int) -> dict:
    """Booleans/enums only: no keys, paths or novel text. Raises
    NotFoundError for an unknown drama."""
    drama = _require_drama(drama_id)
    status = background_jobs.get_status(f"narration_{drama_id}")
    return {
        "drama_id": drama_id,
        "is_narration": drama.get("content_mode") == "novel_narration",
        "has_novel_source": bool(_read_novel(drama_id).strip()),
        "engines": [{"key": e, "key_configured": bool(_api_key(e))} for e in TAG_ENGINES],
        "default_engine": DEFAULT_ENGINE,
        "max_chunk_chars": MAX_CHUNK_CHARS,
        "existing_line_count": len(db.load_line_ids(drama_id)),
        "replaces_existing_lines": True,
        "job_running": bool(status and status["status"] in ("running", "queued")),
    }


def start_narration_run(drama_id: int, engine_name: Optional[str] = None,
                        model: Optional[str] = None, fresh: bool = False) -> dict:
    """Starts the chunk-and-tag job; returns {"job_id"}. NotFoundError for
    an unknown drama, InvalidInputError for no novel text or an engine that
    cannot tag speakers, DependencyUnavailableError if the engine's key is
    not configured, ConflictError if a run is already active.

    A re-run over the same text, engine, model and known characters
    resumes after the batches an interrupted run already tagged;
    fresh=True drops those and tags everything again."""
    _require_drama(drama_id)
    engine_name = engine_name or DEFAULT_ENGINE
    if engine_name not in TAG_ENGINES:
        raise InvalidInputError(f"Engine {engine_name!r} cannot tag speakers; "
                                f"use one of {', '.join(TAG_ENGINES)}.")
    text = _read_novel(drama_id)
    if not text.strip():
        raise InvalidInputError("This drama has no novel text attached yet.")
    api_key = _api_key(engine_name)
    if not api_key:
        raise MissingKeyError(engine_name)

    job_id = f"narration_{drama_id}"
    started = background_jobs.start_job(
        job_id, _run_narration_job, job_id, drama_id, text, engine_name, api_key, model,
        gpu_touching=translate_engines.ollama_touches_local_gpu(engine_name, model),
        description=f"Chunk & tag (drama {drama_id})", fresh=bool(fresh))
    if not started:
        raise ConflictError("A narration chunk & tag run is already active for this drama.")
    return {"job_id": job_id}


def tagging_checkpoint(drama_id, text, engine_name, model, known, fresh=False,
                       max_chunk_chars=MAX_CHUNK_CHARS):
    """(done {idx: label}, on_batch) for tag_speakers_by_id, shared
    with `cli.py narrate-prep`. A re-run over the same text, engine, model,
    known characters and prompt version skips the batches an interrupted
    run already tagged (and paid for); fresh=True drops them first.
    Checkpoint failures never fail the run."""
    scope = job_checkpoint_service.checkpoint_scope(
        "narration_tag", drama_id, job_checkpoint_service.hash_text(text),
        f"{engine_name}:{model or ''}",
        {"max_chunk_chars": max_chunk_chars, "known": sorted(known),
         "prompt_version": translate_engines.TAG_SPEAKERS_PROMPT_VERSION})
    try:
        if fresh:
            job_checkpoint_service.clear_prefix("narration_tag", drama_id)
        done = {int(k): v for k, v in job_checkpoint_service.done_units(scope).items()
                if isinstance(v, str) and k.lstrip("-").isdigit()}
    except Exception:
        done = {}

    def on_batch(batch_labels):
        try:
            for idx, label in batch_labels.items():
                job_checkpoint_service.record_unit(scope, idx, label)
        except Exception:
            pass
    return done, on_batch


def finish_tagging_checkpoint(drama_id):
    """Call once the tagged lines are saved: the next run starts fresh."""
    try:
        job_checkpoint_service.clear_prefix("narration_tag", drama_id)
    except Exception:
        pass


def _raise_if_cancelled(job_id):
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)


def _run_narration_job(job_id, drama_id, text, engine_name, api_key, model, fresh=False):
    job_timing_service.mark_stage(job_id, "Chunk")
    background_jobs.update_progress(job_id, 0.05, "Chunking novel text...")
    chunks = core_module.chunk_novel_text(text, MAX_CHUNK_CHARS)
    if not chunks:
        background_jobs.set_result(job_id, {"failed_reason": "empty"})
        return
    lines = [Line(idx=i, start=float(i), end=float(i) + 1.0, zh=c) for i, c in enumerate(chunks)]

    job_timing_service.mark_stage(job_id, "Tag speakers")
    background_jobs.update_progress(job_id, 0.2, "Tagging speakers with the LLM...")
    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=settings_service.get_gemini_free_tier(),
        base_url=(settings_service.resolve_key("ollama_url") if engine_name == "ollama" else None))
    known = [c["character_name"] for c in db.list_characters(drama_id) if c["character_name"]]
    done, _checkpoint = tagging_checkpoint(
        drama_id, text, engine_name, getattr(engine, "model", model), known, fresh=fresh)
    if done:
        background_jobs.update_progress(
            job_id, 0.2, f"Resuming: {len(done)} of {len(lines)} chunks already tagged...")

    by_idx = translate_engines.tag_speakers_by_id(
        {ln.idx: ln.zh for ln in lines}, engine, known,
        usage_cb=lambda inp, out: db.log_usage(
            drama_id, engine_name, getattr(engine, "model", engine_name), "tag_speakers",
            inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)),
        done=done, on_batch=_checkpoint,
        cancel_check=lambda: _raise_if_cancelled(job_id))
    _raise_if_cancelled(job_id)
    for ln in lines:
        ln.speaker = (by_idx.get(ln.idx) or "").strip() or "Narrator"

    job_timing_service.mark_stage(job_id, "Save")
    background_jobs.update_progress(job_id, 0.9, "Saving lines...")
    for label in sorted({ln.speaker for ln in lines}):
        db.upsert_character(drama_id, label, character_name=label)
    background_jobs.cancel_line_jobs(drama_id)
    existing = db.load_line_objects(drama_id)
    if existing:
        db.save_line_history_snapshot(drama_id, existing, "before chunk & tag speakers")
    # Full sync on purpose: these brand-new lines replace the drama's lines.
    # Line jobs were cancelled and the old lines snapshotted just above.
    db.save_lines(drama_id, lines)
    db.update_drama(drama_id, status="aligned")
    finish_tagging_checkpoint(drama_id)
    background_jobs.set_result(job_id, {"line_count": len(lines)})

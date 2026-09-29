"""
services/narration_service.py -- the novel-narration "Chunk & tag speakers"
action for one drama (Streamlit: `tabs/workspace_tab.py`'s `run_prep and
content_mode == "novel_narration"` branch; CLI: `cli.cmd_narrate_prep`).

Migration Slice 33, the `chunk_and_tag` path Slice 20 deferred. Today it
is fully synchronous; here it becomes a background job that does the WHOLE
pipeline (chunk, LLM speaker tagging, history snapshot, DB write, status),
same "job does everything" decision as Slice 20. Poll GET /api/jobs/{id};
background_jobs' own runner redacts secrets from a failed job's error.

Input is the drama's attached novel text (`dub.NOVEL_SOURCE_FILENAME` in
its folder). The LLM engine is chosen per request; its key is resolved
server-side and never returned (D2). Speakers come from
`tag_speakers_by_id`, which returns {chunk idx: label}; the job looks each
chunk's label up by its idx (missing -> "Narrator"), never by list position.

Writes replace the drama's lines wholesale, exactly as Streamlit and the
CLI do (the lines are brand new), but only after a "before chunk & tag
speakers" history snapshot of whatever lines existed.

No Streamlit or FastAPI import.
"""
import os
from typing import Optional

import background_jobs
import core as core_module
import db
import dub
import translate_engines
from core import Line
from services import settings_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)

DEFAULT_ENGINE = "claude"
# LLM-capable engines that can tag speakers (pure MT engines cannot).
TAG_ENGINES = ["claude", "deepseek", "gemini", "ollama"]
MAX_CHUNK_CHARS = 200  # core.chunk_novel_text's own default


def _require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _read_novel(drama_id: int) -> str:
    # Not db.drama_dir: that creates the folder, and config is a read.
    path = os.path.join(db.DRAMAS_DIR, str(drama_id), dub.NOVEL_SOURCE_FILENAME)
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
                        model: Optional[str] = None) -> dict:
    """Starts the chunk-and-tag job; returns {"job_id"}. NotFoundError for
    an unknown drama, InvalidInputError for no novel text or an engine that
    cannot tag speakers, DependencyUnavailableError if the engine's key is
    not configured, ConflictError if a run is already active."""
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
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")

    job_id = f"narration_{drama_id}"
    started = background_jobs.start_job(
        job_id, _run_narration_job, job_id, drama_id, text, engine_name, api_key, model,
        description=f"Chunk & tag (drama {drama_id})")
    if not started:
        raise ConflictError("A narration chunk & tag run is already active for this drama.")
    return {"job_id": job_id}


def _run_narration_job(job_id, drama_id, text, engine_name, api_key, model):
    background_jobs.update_progress(job_id, 0.05, "Chunking novel text...")
    chunks = core_module.chunk_novel_text(text, MAX_CHUNK_CHARS)
    if not chunks:
        background_jobs.set_result(job_id, {"failed_reason": "empty"})
        return
    lines = [Line(idx=i, start=float(i), end=float(i) + 1.0, zh=c) for i, c in enumerate(chunks)]

    background_jobs.update_progress(job_id, 0.2, "Tagging speakers with the LLM...")
    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=settings_service.get_gemini_free_tier(),
        base_url=(settings_service.resolve_key("ollama_url") if engine_name == "ollama" else None))
    known = [c["character_name"] for c in db.list_characters(drama_id) if c["character_name"]]
    by_idx = translate_engines.tag_speakers_by_id(
        {ln.idx: ln.zh for ln in lines}, engine, known,
        usage_cb=lambda inp, out: db.log_usage(
            drama_id, engine_name, getattr(engine, "model", engine_name), "tag_speakers",
            inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out)))
    for ln in lines:
        ln.speaker = (by_idx.get(ln.idx) or "").strip() or "Narrator"

    background_jobs.update_progress(job_id, 0.9, "Saving lines...")
    for label in sorted({ln.speaker for ln in lines}):
        db.upsert_character(drama_id, label, character_name=label)
    background_jobs.cancel_line_jobs(drama_id)
    existing = db.load_line_objects(drama_id)
    if existing:
        db.save_line_history_snapshot(drama_id, existing, "before chunk & tag speakers")
    db.save_lines(drama_id, lines)
    db.update_drama(drama_id, status="aligned")
    background_jobs.set_result(job_id, {"line_count": len(lines)})

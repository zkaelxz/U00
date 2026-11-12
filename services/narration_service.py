"""
services/narration_service.py -- the novel-narration "Chunk & tag speakers"
action for one drama.

The `chunk_and_tag` path runs as a background job that does the WHOLE
pipeline (chunk, LLM speaker tagging, history snapshot, DB write, status),
the same "job does everything" decision as the other narration jobs. Poll GET /api/jobs/{id};
background_jobs' own runner redacts secrets from a failed job's error.

Input is the drama's attached novel text (`novel_text.NOVEL_SOURCE_FILENAME` in
its folder). The LLM engine is chosen per request; its key is resolved
server-side and never returned (D2). Speakers come from
`tag_speakers_by_id`, which returns {chunk idx: label}; the job looks each
chunk's label up by its idx (missing -> "Narrator"), never by list position.

Writes replace the drama's lines wholesale, (the
lines are brand new), but only after a "before chunk & tag
speakers" history snapshot of whatever lines existed.

No FastAPI import.
"""
import os
from typing import Optional

import background_jobs
from jobs import job_launch
import novel_text
import db
from engine_backends import llm_tasks, engine_registry
from engine_backends import local as engine_local
from engine_backends import paid_calls
from core import Line
from engine_backends.run_limits import TranslateRunStopped
from services import (
    job_checkpoint_service,
    job_timing_service,
    ownership_service,
    paid_job_service,
    settings_service,
)
from lib.errors import (
    ConflictError,
    InvalidInputError,
)

DEFAULT_ENGINE = "claude"
# LLM-capable engines that can tag speakers (pure MT engines cannot).
TAG_ENGINES = ["claude", "deepseek", "gemini", "openai", "ollama"]
MAX_CHUNK_CHARS = 200  # core.chunk_novel_text's own default
NO_KEY_NOTICE = ("No AI key set, so every line is marked Narrator. "
                 "Add a key in Settings to tag speakers.")


def _read_novel(drama_id: int) -> str:
    # Not db.drama_dir: that creates the folder, and config is a read.
    path = os.path.join(db.DRAMAS_DIR, str(drama_id), novel_text.NOVEL_SOURCE_FILENAME)
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
    drama = ownership_service.require_drama(drama_id)
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
                        model: Optional[str] = None, fresh: bool = False,
                        append: bool = False) -> dict:
    """Starts the chunk-and-tag job; returns {"job_id"}. NotFoundError for
    an unknown drama, InvalidInputError for no novel text or an engine that
    cannot tag speakers, ConflictError if a run is already active.
    With no key for the engine the job still cuts the text and marks every
    chunk "Narrator" (chunking needs no AI), so a pasted novel opens
    without any account.

    A re-run over the same text, engine, model and known characters
    resumes after the batches an interrupted run already tagged;
    fresh=True drops those and tags everything again.

    append (adding chapters to a title that has lines): only the text an add left
    without lines (novel_text.read_pending_lines, held by the server, never the
    client) is cut and tagged, and its lines are appended after the saved ones.
    The saved lines, whatever was edited, merged or split in Review, are not
    touched. InvalidInputError when nothing is waiting, ConflictError when the
    text changed since."""
    ownership_service.require_drama(drama_id)
    engine_name = engine_name or DEFAULT_ENGINE
    if engine_name not in TAG_ENGINES:
        raise InvalidInputError(f"Engine {engine_name!r} cannot tag speakers; "
                                f"use one of {', '.join(TAG_ENGINES)}.")
    text = _read_novel(drama_id)
    if not text.strip():
        raise InvalidInputError("This title has no novel text attached yet.")
    if append:
        text = _pending_tail(drama_id, text)
    api_key = _api_key(engine_name)

    job_id = f"narration_{drama_id}"
    started = job_launch.start_job(
        job_id, _run_narration_job, job_id, drama_id, text, engine_name, api_key, model,
        gpu_touching=engine_local.ollama_touches_local_gpu(engine_name, model),
        description=f"Chunk & tag (title {drama_id})", fresh=bool(fresh),
        append=bool(append), line_fields=db.FULL_SYNC)
    if not started:
        raise ConflictError("A narration chunk & tag run is already active for this title.")
    return {"job_id": job_id}


def _pending_tail(drama_id: int, text: str) -> str:
    """The end of `text` that an add left without lines, checked against what the add recorded."""
    waiting = novel_text.read_pending_lines(os.path.join(db.DRAMAS_DIR, str(drama_id)))
    if waiting is None:
        raise InvalidInputError("No added chapters are waiting for lines.")
    chars, length = waiting.chars, waiting.length
    if length != len(text):
        raise ConflictError("The translation text changed after the chapters were added, so their lines "
                            "can't be cut on their own. Run chunk & tag again from the Source stage.")
    saved = db.load_lines(drama_id)
    if not saved:
        raise InvalidInputError("This title has no lines to add to; run chunk & tag first.")
    if lines_already_added(waiting, text, [ln["zh"] if isinstance(ln, dict) else ln.zh for ln in saved]):
        # A stop between saving the lines and clearing the marker: adding them again would repeat them.
        novel_text.clear_pending_lines(os.path.join(db.DRAMAS_DIR, str(drama_id)))
        raise InvalidInputError("These chapters already have their lines.")
    return text[-chars:]


def lines_already_added(waiting, text: str, line_texts: list) -> bool:
    """Whether the lines a stopped append was adding are already saved: the marker says how many lines
    the title had and how many were being added, and those last lines hold the waiting text."""
    return (waiting.base is not None and waiting.added is not None
            and len(line_texts) == waiting.base + waiting.added
            and novel_text.lines_hold_tail(line_texts, text[-waiting.chars:], waiting.added))


def tagging_checkpoint(drama_id, text, engine_name, model, known, fresh=False,
                       max_chunk_chars=MAX_CHUNK_CHARS):
    """(done {idx: label}, on_batch) for tag_speakers_by_id, used by
    the narration job. A re-run over the same text, engine, model,
    known characters and prompt version skips the batches an interrupted
    run already tagged (and paid for); fresh=True drops them first.
    Checkpoint failures never fail the run."""
    scope = job_checkpoint_service.checkpoint_scope(
        "narration_tag", drama_id, job_checkpoint_service.hash_text(text),
        f"{engine_name}:{model or ''}",
        {"max_chunk_chars": max_chunk_chars, "known": sorted(known),
         "prompt_version": llm_tasks.TAG_SPEAKERS_PROMPT_VERSION})
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


def _run_narration_job(job_id, drama_id, text, engine_name, api_key, model, fresh=False, append=False):
    job_timing_service.mark_stage(job_id, "Chunk")
    background_jobs.update_progress(job_id, 0.05, "Chunking novel text...")
    chunks = novel_text.chunk_novel_text(text, MAX_CHUNK_CHARS)
    if not chunks:
        background_jobs.set_result(job_id, {"failed_reason": "empty"})
        return
    lines = [Line(idx=i, start=float(i), end=float(i) + 1.0, zh=c) for i, c in enumerate(chunks)]

    if not api_key:
        for ln in lines:
            ln.speaker = "Narrator"
        (_append_lines if append else _save_lines)(job_id, drama_id, lines, notice=NO_KEY_NOTICE)
        return

    job_timing_service.mark_stage(job_id, "Tag speakers")
    background_jobs.update_progress(job_id, 0.2, "Tagging speakers with the LLM...")
    engine = engine_registry.get_engine(
        engine_name, api_key, model,
        free_tier=bool(settings_service.get("gemini_free_tier")),
        base_url=(settings_service.ollama_url() if engine_name == "ollama" else None))
    known = [c["character_name"] for c in db.list_characters(drama_id) if c["character_name"]]
    done, _checkpoint = tagging_checkpoint(
        drama_id, text, engine_name, getattr(engine, "model", model), known, fresh=fresh)
    if done:
        background_jobs.update_progress(
            job_id, 0.2, f"Resuming: {len(done)} of {len(lines)} chunks already tagged...")

    try:
        with paid_job_service.job_calls(job_id, drama_id, engine, engine_name, "tag_speakers"):
            by_idx = llm_tasks.tag_speakers_by_id(
                {ln.idx: ln.zh for ln in lines}, engine, known,
                usage_cb=paid_calls.usage_logger(drama_id, engine_name, engine, "tag_speakers"),
                done=done, on_batch=_checkpoint,
                cancel_check=lambda: _raise_if_cancelled(job_id))
    except TranslateRunStopped as stop:
        # Half-tagged chunks must not be saved as Narrator; the checkpoint
        # keeps the finished batches for the next run.
        raise RuntimeError(f"{str(stop).rstrip('.')}. Chunks already tagged are kept; run it again to continue.") from None
    _raise_if_cancelled(job_id)
    for ln in lines:
        ln.speaker = (by_idx.get(ln.idx) or "").strip() or "Narrator"

    (_append_lines if append else _save_lines)(job_id, drama_id, lines)


def _carry_over_existing(lines, existing):
    """Gives each new line the id, translation and review state of the saved
    line with the same source text. Chunking is deterministic per paragraph, so
    after chapters are added the old text re-chunks identically and keeps its
    English, notes and checked marks; only text that changed or is new starts
    empty. Matching is by text in order (duplicate lines pair off one by one),
    never by position, since appended chapters shift every later index."""
    pool = {}
    for old in existing:
        pool.setdefault(old.zh, []).append(old)
    for ln in lines:
        queue = pool.get(ln.zh)
        if not queue:
            continue
        old = queue.pop(0)
        ln.id, ln.en, ln.reviewed = old.id, old.en, old.reviewed
        ln.flag, ln.flag_note = old.flag, old.flag_note
        if old.speaker_manual:
            ln.speaker, ln.speaker_manual = old.speaker, True


def _append_lines(job_id, drama_id, lines, notice=None):
    """Adds the new chunks after the saved lines, which are written back exactly as read, so edits
    made in Review survive. Refuses (ConflictError) if the lines changed while the job ran."""
    job_timing_service.mark_stage(job_id, "Save")
    background_jobs.update_progress(job_id, 0.9, notice or "Saving lines...")
    background_jobs.cancel_line_jobs(drama_id)
    existing = db.load_line_objects(drama_id)
    first = max((ln.idx for ln in existing), default=-1) + 1
    for n, ln in enumerate(lines):
        ln.idx = first + n
        ln.start, ln.end = float(ln.idx), float(ln.idx) + 1.0
    for label in sorted({ln.speaker or "Narrator" for ln in lines}):
        db.upsert_character(drama_id, label, character_name=label)
    db.save_line_history_snapshot(drama_id, existing, "before adding chapters")
    drama_dir = os.path.join(db.DRAMAS_DIR, str(drama_id))
    waiting = novel_text.read_pending_lines(drama_dir)
    if waiting:
        # Written before the lines are, so a stop after they are saved and before the marker is cleared
        # is recognised by the next retry instead of appending them a second time.
        novel_text.write_pending_lines(drama_dir, waiting.chars, waiting.length, len(existing), len(lines))
    db.save_lines(drama_id, existing + lines, expect_unchanged=existing)
    finish_tagging_checkpoint(drama_id)
    novel_text.clear_pending_lines(os.path.join(db.DRAMAS_DIR, str(drama_id)))
    result = {"line_count": len(existing) + len(lines), "added_lines": len(lines)}
    if notice:
        result["notice"] = notice
    background_jobs.set_result(job_id, result)


def _save_lines(job_id, drama_id, lines, notice=None):
    job_timing_service.mark_stage(job_id, "Save")
    background_jobs.update_progress(job_id, 0.9, notice or "Saving lines...")
    for label in sorted({ln.speaker or "Narrator" for ln in lines}):
        db.upsert_character(drama_id, label, character_name=label)
    background_jobs.cancel_line_jobs(drama_id)
    existing = db.load_line_objects(drama_id)
    if existing:
        db.save_line_history_snapshot(drama_id, existing, "before chunk & tag speakers")
    _carry_over_existing(lines, existing)
    # Full sync on purpose: the list is the drama's lines now; text that is
    # gone loses its row, unchanged text keeps its row through the carry-over.
    # expect_unchanged: cancel_line_jobs doesn't wait, and a Review edit saved
    # after the read above would otherwise be overwritten by the carried-over copy.
    db.save_lines(drama_id, lines, expect_unchanged=existing)
    db.update_drama(drama_id, status="aligned")
    finish_tagging_checkpoint(drama_id)
    novel_text.clear_pending_lines(os.path.join(db.DRAMAS_DIR, str(drama_id)))   # the whole text was cut
    result = {"line_count": len(lines)}
    if notice:
        result["notice"] = notice
    background_jobs.set_result(job_id, result)

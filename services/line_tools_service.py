"""
services/line_tools_service.py -- two Review per-line tools that aren't
plain read-only LLM text (review parity R19, R28). Streamlit-free; plain
dicts/bytes in and out. The LLM text tools (improve, explain, alternatives,
grammar) live in services/line_ai_service.py.

  - pronounce_line(): an edge-tts clip of one line's SOURCE text in the
    source language (`line_tools.SOURCE_LANG_VOICES`), for hearing how a
    name or phrase is said. Bounded: at most MAX_PRONOUNCE_CHARS of text,
    PRONOUNCE_TIMEOUT_S for the whole synthesis, MAX_AUDIO_BYTES of audio.
    The clip is made in a temporary folder that is removed before return;
    nothing is written to the drama or the database. The text is the
    stored line's, never client-supplied.
  - shorten_overlong(): the tab's "Auto-shorten overlong lines with LLM":
    lines the pacing check calls too long for their time slot are
    rewritten more concisely by `translate_engines.rewrite_for_pacing_llm`
    (id-keyed, never matched back by position). Writes ONLY `en`, one
    compare-and-set UPDATE per line against the text the model was shown,
    so a line edited while the model ran is skipped as stale rather than
    overwritten. A line_history snapshot of the whole drama is saved before
    the first overwrite, so the change can be undone from History -- none
    when no line will be written, and none when the newest snapshot is the
    same shortening pass's (only overlong lines' English changed since). A paid
    engine is refused once the monthly spending cap is used up.
"""
import asyncio
import os
import tempfile

import db
import dub
import line_tools
import translate_engines
from core import Line
from services import drama_service, line_ai_service, lines_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError,
                                      NotFoundError, ServiceError,
                                      UnsupportedOperationError)

MAX_PRONOUNCE_CHARS = 200
PRONOUNCE_TIMEOUT_S = 30
MAX_AUDIO_BYTES = 2_000_000
MAX_SHORTEN_LINES = 60
MAX_SHORTEN_IDS = 1000
SHORTEN_SNAPSHOT_LABEL = "before auto-shorten"


def pronounce_line(drama_id: int, line_id: int) -> bytes:
    """MP3 bytes of the line's source text read aloud in its language."""
    drama, _, ln = lines_service._load(drama_id, line_id)
    text = (ln.zh or "").strip()
    if not text:
        raise UnsupportedOperationError("This line has no source text to pronounce.")
    if len(text) > MAX_PRONOUNCE_CHARS:
        raise UnsupportedOperationError(
            f"This line is too long to pronounce (max {MAX_PRONOUNCE_CHARS} characters).")
    try:
        import edge_tts  # noqa: F401  (optional dependency)
    except ImportError:
        raise DependencyUnavailableError(
            "Pronouncing needs edge-tts (pip install edge-tts).") from None
    lang = drama.get("source_language") or "zh"
    voice = line_tools.SOURCE_LANG_VOICES.get(lang, line_tools.SOURCE_LANG_VOICES["zh"])
    with tempfile.TemporaryDirectory(prefix="baihe_pronounce_") as tmp:
        out = os.path.join(tmp, "pronounce.mp3")
        try:
            asyncio.run(asyncio.wait_for(dub._edge_tts_synthesize(text, voice, out),
                                         PRONOUNCE_TIMEOUT_S))
        except asyncio.TimeoutError:
            raise ServiceError("The pronunciation service took too long. Try again.") from None
        except dub.EdgeTTSBlockedError:
            raise DependencyUnavailableError(
                "Microsoft blocked the request; update edge-tts (pip install -U edge-tts).") from None
        except Exception as e:
            raise ServiceError("Couldn't make the audio: "
                               + translate_engines.redact_secrets(str(e))[:200]) from None
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            raise ServiceError("Couldn't make the audio. Try again.")
        if os.path.getsize(out) > MAX_AUDIO_BYTES:
            raise ServiceError("The audio came back too large.")
        with open(out, "rb") as f:
            return f.read()


def _too_long_lines(lines) -> list:
    too_long = {f["idx"] for f in translate_engines.smart_segment_lines(lines)
                if f["issue"] == "too_long_for_slot"}
    return [ln for ln in lines if ln.idx in too_long and (ln.en or "").strip()]


def _snapshot_row(ln) -> dict:
    return {"id": ln.id, "idx": ln.idx, "start": ln.start, "end": ln.end, "zh": ln.zh,
            "speaker": getattr(ln, "speaker", None),
            "dub_filename": getattr(ln, "dub_filename", None),
            "speaker_manual": bool(getattr(ln, "speaker_manual", False)),
            "flag": getattr(ln, "flag", None), "flag_note": getattr(ln, "flag_note", "") or "",
            "sfx": bool(getattr(ln, "sfx", False))}


def _shorten_pass_key(drama_id: int) -> str:
    return f"shorten_pass:{drama_id}"


def _continues_last_shorten(drama_id: int, current) -> bool:
    """True when the drama's newest line_history snapshot is the auto-shorten
    one the last shorten pass took and nothing has changed since except the
    English that pass (and its continuations) wrote -- i.e. this run
    continues the same shortening pass (the "N more are left" re-run), so
    that snapshot already holds the lines before it and a second one would
    only push older history out of the 10 kept. A line whose English differs
    from the snapshot but isn't exactly what the pass wrote (a manual edit
    between runs) means a new snapshot, so that edit stays undoable."""
    hist = db.list_line_history(drama_id)
    if not hist or hist[0]["label"] != SHORTEN_SNAPSHOT_LABEL:
        return False
    pass_info = db.get_app_setting(_shorten_pass_key(drama_id)) or {}
    if pass_info.get("snapshot_id") != hist[0]["id"]:
        return False
    written = pass_info.get("written") or {}
    snap = db.get_line_history_snapshot(hist[0]["id"]) or []
    if len(snap) != len(current):
        return False
    for row, ln in zip(snap, current):
        if {k: row.get(k) for k in _snapshot_row(ln)} != _snapshot_row(ln):
            return False
        if (row.get("en") or "") != (ln.en or "") and written.get(str(ln.id)) != ln.en:
            return False
    return True


def shorten_overlong(drama_id: int, line_ids=None, engine_name: str = None,
                     model: str = None, gemini_free_tier: bool = None,
                     confirm: bool = False) -> dict:
    """Rewrites the drama's too-long-for-slot lines (or just those of
    `line_ids` that are too long) more concisely. At most MAX_SHORTEN_LINES
    per call, in line order; `remaining` says how many were left for
    another call. Needs confirm=True (it overwrites English) and is refused
    while a background job runs for the drama. Returns {shortened, unchanged, stale, remaining,
    snapshot_saved, lines: [{id, idx, before, after}]}."""
    if line_ids is not None:
        if (not isinstance(line_ids, (list, tuple)) or len(line_ids) > MAX_SHORTEN_IDS
                or any(not isinstance(i, int) or isinstance(i, bool) for i in line_ids)):
            raise InvalidInputError(f"line_ids must be a list of at most {MAX_SHORTEN_IDS} ids.")
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    if confirm is not True:
        raise InvalidInputError("Shortening overwrites the English of the overlong lines; "
                                "send confirm=true.")
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A background job is still running for this drama -- wait for it "
                            "to finish or cancel it before shortening lines.")
    targets = _too_long_lines(db.load_line_objects(drama_id))
    if line_ids is not None:
        wanted = set(line_ids)
        targets = [ln for ln in targets if ln.id in wanted]
    empty = {"shortened": 0, "unchanged": 0, "stale": 0, "remaining": 0,
             "snapshot_saved": False, "lines": []}
    if not targets:
        return empty
    remaining = max(0, len(targets) - MAX_SHORTEN_LINES)
    targets = targets[:MAX_SHORTEN_LINES]
    engine, name = line_ai_service._engine_for(drama, engine_name, model, gemini_free_tier,
                                               check_cap=True)
    model_name = getattr(engine, "model", model) or name

    def usage(inp, out):
        db.log_usage(drama_id, name, model_name, "pacing_shorten", inp, out,
                     translate_engines.estimate_cost_for_engine(engine, inp, out))

    before = {ln.id: ln.en for ln in targets}
    work = [Line(idx=ln.idx, start=ln.start, end=ln.end, zh=ln.zh, en=ln.en, id=ln.id)
            for ln in targets]
    line_ai_service._run(lambda: translate_engines.rewrite_for_pacing_llm(
        work, engine, usage_cb=usage))

    changed = [w for w in work if (w.en or "").strip() and w.en.strip() != before[w.id].strip()]
    out = dict(empty, remaining=remaining, unchanged=len(work) - len(changed))
    current = db.load_line_objects(drama_id)
    current_en = {ln.id: ln.en for ln in current}
    # A line edited (or removed) while the model ran would be skipped by the
    # compare-and-set below anyway; count it now so a run that can't write
    # anything saves no snapshot.
    writable = [w for w in changed if current_en.get(w.id) == before[w.id]]
    out["stale"] = len(changed) - len(writable)
    if not writable:
        return out
    if _continues_last_shorten(drama_id, current):
        pass_info = db.get_app_setting(_shorten_pass_key(drama_id))
    else:
        db.save_line_history_snapshot(drama_id, current, SHORTEN_SNAPSHOT_LABEL)
        out["snapshot_saved"] = True
        pass_info = {"snapshot_id": db.list_line_history(drama_id)[0]["id"], "written": {}}
    for w in writable:
        new_en = w.en.strip()[:lines_service.MAX_LINE_TEXT_CHARS]
        if db.update_line_fields_if(drama_id, w.id, {"en": new_en}, {"en": before[w.id]}):
            out["shortened"] += 1
            pass_info["written"][str(w.id)] = new_en
            out["lines"].append({"id": w.id, "idx": w.idx, "before": before[w.id],
                                 "after": new_en})
        else:
            out["stale"] += 1
    db.set_app_setting(_shorten_pass_key(drama_id), pass_info)
    return out

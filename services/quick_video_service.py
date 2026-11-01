"""
services/quick_video_service.py -- Quick video translate: a link and a saved translation
profile in, one title out, run through the Queue (download, transcribe, translate, export).

The run is a chain of ordinary queue items for one new title, so GPU slots, the exclusive
hold, ownership and the spending caps apply exactly as for a click. The profile is the
saved preset (profile_service) plus its quick_* options: the source language the title is
created in, the export it ends with and the language packs.

A "watch once" title is recorded in `RUNS_KEY` and can be removed later, by the person or
by the startup sweep, through `drama_service.delete_drama`. Nothing else is ever deleted
here: the id must be in that list.
"""
import logging
import threading
import time
import uuid
from typing import Optional

import db
from engine_backends import engine_registry, shared
from lib.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from services import drama_service, language_pack_service, profile_service, url_media_service

_log = logging.getLogger(__name__)

PROFILE_KEY = "quick_video.profile_id"
RUNS_KEY = "quick_video.runs"
MAX_RUNS = 30
# A watch-once title nobody removed (the person closed the page, the PC was off) goes after this.
TEMPORARY_MAX_AGE_SECONDS = 48 * 3600
_lock = threading.Lock()

_NO_PROFILE = "Choose a translation profile for Quick video translate in Settings."
_NO_LANGUAGE = "This profile has no source language. Set one in the profile's Quick video translate section."
_NOT_TEMPORARY = "This title was not made as a watch-once title, so it is not removed here."
_PAID = "This profile translates with a paid engine, which your account can't use."
_UNFINISHED = "An earlier step of this run did not finish."
_RUN_STEPS = ("download", "transcribe", "translate", "export")


def _presets() -> list:
    return db.list_presets()


def _preset(profile_id) -> Optional[dict]:
    return next((p for p in _presets() if p["id"] == profile_id), None)


def get_settings() -> dict:
    """The saved profile choice and the profiles to pick from. A profile that was deleted reads as no choice."""
    chosen = db.get_app_setting(PROFILE_KEY)
    return {"profile_id": chosen if _preset(chosen) else None,
            "profiles": [{"id": p["id"], "name": p["name"],
                          "ready": bool(profile_service.stored_options(p).get("quick_source_language"))}
                         for p in _presets()]}


def set_profile(profile_id) -> dict:
    if profile_id is not None and _preset(profile_id) is None:
        raise NotFoundError("No profile with that id.")
    db.set_app_setting(PROFILE_KEY, profile_id)
    return get_settings()


def _read_runs() -> list:
    raw = db.get_app_setting(RUNS_KEY, [])
    return [r for r in raw if isinstance(r, dict) and isinstance(r.get("drama_id"), int)] \
        if isinstance(raw, list) else []


def _write_runs(runs: list) -> None:
    # The newest MAX_RUNS runs stay; a watch-once title is kept until it is removed or swept.
    keep = [r for r in runs if r.get("watch_once")] + [r for r in runs if not r.get("watch_once")][-MAX_RUNS:]
    db.set_app_setting(RUNS_KEY, sorted(keep, key=lambda r: r.get("created_at") or 0))


def _remember(run: dict) -> None:
    with _lock:
        _write_runs(_read_runs() + [run])


def _forget(drama_id: int) -> None:
    with _lock:
        _write_runs([r for r in _read_runs() if r["drama_id"] != drama_id])


def require_chain_finished(item: dict, items: list) -> None:
    """Raises unless every step before this item's step has a done item in its chain, whatever their
    positions in the queue: moving or removing an item must not run a step on a title that did not get
    its media or lines."""
    done = {other["step"] for other in items if other.get("chain") == item["chain"] and other["status"] == "done"}
    if not set(_RUN_STEPS[:_RUN_STEPS.index(item["step"])]) <= done:
        raise ConflictError(_UNFINISHED)


def start_download(drama: dict, url) -> str:
    """Starts the chain's download item as the video download of the title (the video is kept for watching)."""
    if not isinstance(url, str) or not url:
        raise InvalidInputError("This item has no link.")
    return url_media_service.start_url_download(drama["id"], url, False)["job_id"]


def _export_settings(options: dict) -> dict:
    return {"format": options.get("quick_export_format", "srt"),
            "language": options.get("quick_export_language", "en")}


def start(principal, url, profile_id, watch_once, *, holds_paid: bool, at_pc: bool) -> dict:
    """Creates the title and queues Download, Transcribe, Translate and Export for it, then starts the
    queue. A refused request creates nothing. Returns {"drama_id", "watch_once"}."""
    from services import queue_service
    if not isinstance(watch_once, bool):
        raise InvalidInputError("watch_once must be true or false.")
    url = url_media_service.check_link(url)
    if profile_id is None:
        profile_id = get_settings()["profile_id"]
    preset = _preset(profile_id) if profile_id is not None else None
    if preset is None:
        raise InvalidInputError(_NO_PROFILE)
    options = profile_service.stored_options(preset)
    language = options.get("quick_source_language")
    if not language:
        raise InvalidInputError(_NO_LANGUAGE)
    engine = preset.get("translation_engine")
    if engine and engine not in engine_registry.FREE_ENGINES and not holds_paid:
        raise ForbiddenError(_PAID)

    created = drama_service.create_drama(source_language=language, media_type="video_drama",
                                         preset_id=preset["id"], principal=principal)
    drama_id = created["id"]
    chain = uuid.uuid4().hex
    try:
        # The new title has no transcript to paste, so it transcribes the downloaded audio.
        if not db.get_drama(drama_id).get("transcript_mode"):
            db.update_drama(drama_id, transcript_mode="whisper")
        packs = options.get("quick_language_packs") or []
        if packs:
            language_pack_service.set_title_packs(drama_id, {"packs": {p: None for p in packs}}, principal)
        steps = [("download", {"url": url}), ("transcribe", {}), ("translate", {}),
                 ("export", _export_settings(options))]
        for step, settings in steps[1:]:   # the download item is checked by check_link and at its start
            queue_service._check_addable(principal, drama_id, step, holds_paid, at_pc,
                                         settings or None, True)
        queue_service._append_items(principal, drama_id, steps, at_pc, chain=chain)
    except BaseException:
        drama_service.hard_delete_drama(drama_id)
        raise
    _remember({"drama_id": drama_id, "chain": chain, "watch_once": watch_once, "created_at": time.time()})
    queue_service.start(principal)
    return {"drama_id": drama_id, "watch_once": watch_once}


def runs(principal) -> list:
    """The recent runs the caller can see, newest first, with how far each got. The link is never
    included."""
    from services import queue_service
    items = queue_service.get_queue(principal)["items"]
    out = []
    for run in reversed(_read_runs()):
        drama = db.get_drama(run["drama_id"])
        mine = [i for i in items if i["drama_id"] == run["drama_id"] and i["step"] in _RUN_STEPS]
        if drama is None or not mine:
            continue
        mine.sort(key=lambda i: _RUN_STEPS.index(i["step"]))
        bad = next((i for i in mine if i["status"] in ("failed", "skipped", "interrupted", "cancelled")), None)
        active = next((i for i in mine if i["status"] in ("running", "waiting")), None)
        if bad is not None:
            state, step, reason = "stopped", bad["step"], bad["reason"]
        elif active is not None:
            state, step, reason = "running", active["step"], None
        else:
            state, step, reason = "done", mine[-1]["step"], None
        out.append({"drama_id": run["drama_id"], "title": mine[0]["title"], "watch_once": bool(run.get("watch_once")),
                    "state": state, "step": step, "reason": reason})
    return out


def delete_temporary(principal, drama_id: int, confirm=False) -> dict:
    """Removes a watch-once title: only one recorded as such, and only the caller's own."""
    from services import ownership_service
    if confirm is not True:
        raise InvalidInputError("Confirm removing the title first.")
    if not any(r["drama_id"] == drama_id and r.get("watch_once") for r in _read_runs()):
        raise InvalidInputError(_NOT_TEMPORARY)
    ownership_service.require_editable(principal, "drama", drama_id)
    result = drama_service.delete_drama(drama_id, confirm=True, confirm_text="DELETE")
    _forget(drama_id)
    return result


def sweep_temporary(now: float = None) -> int:
    """Removes watch-once titles older than TEMPORARY_MAX_AGE_SECONDS that are not running; returns how
    many. A run whose title is already gone is just forgotten. Never raises."""
    now = time.time() if now is None else now
    removed = 0
    for run in _read_runs():
        try:
            if db.get_drama(run["drama_id"]) is None:
                _forget(run["drama_id"])
            elif run.get("watch_once") and now - float(run.get("created_at") or now) > TEMPORARY_MAX_AGE_SECONDS:
                drama_service.delete_drama(run["drama_id"], confirm=True, confirm_text="DELETE")
                _forget(run["drama_id"])
                removed += 1
        except Exception as exc:
            _log.warning("A leftover watch-once title was not removed: %s",
                         shared.redact_secrets(str(exc)))
    return removed

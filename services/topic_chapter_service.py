"""Timed topic chapters of one title: making them with the summary engine, keeping them, and the export forms."""
import json

import db
from engine_backends import llm_tasks, paid_calls, topic_chapters
from lib.errors import DependencyUnavailableError, InvalidInputError, ServiceError, UnsupportedOperationError
from services import (engine_failure_service, episode_summary_service, line_ai_service, ownership_service,
                      translate_run_service)


def _read(drama: dict) -> list[dict]:
    try:
        stored = json.loads(drama.get("topic_chapters") or "[]")
    except ValueError:
        return []
    return [c for c in stored if isinstance(c, dict) and isinstance(c.get("title"), str)
            and isinstance(c.get("start"), (int, float))] if isinstance(stored, list) else []


def chapters_for(drama_id: int) -> list[dict]:
    """The saved chapters, [] when none (also for a title the caller already checked)."""
    return _read(db.get_drama(drama_id) or {})


def get(drama_id: int) -> dict:
    """The saved chapters plus what Details offers to make them: same engines as the summary."""
    drama = ownership_service.require_drama(drama_id)
    return {**episode_summary_service.options(drama_id), "chapters": _read(drama)}


def generate(drama_id: int, engine_name: str, model: str | None = None) -> dict:
    """Makes the chapters from the title's lines and saves them over any earlier ones.
    Unlike the summary this saves at once: the list has no text box to review it in."""
    drama = ownership_service.require_drama(drama_id)
    engine, name = line_ai_service.engine_for(drama, engine_name, model, None, check_cap=True)
    lines = db.load_line_objects(drama_id)
    if not any((ln.en or ln.zh or "").strip() for ln in lines):
        raise UnsupportedOperationError("This title has no lines yet; there is nothing to make chapters from.")
    duration = max(ln.end for ln in lines)
    try:
        with paid_calls.logged_calls(engine, "topic_chapters", drama_id=drama_id):
            found = llm_tasks.generate_topic_chapters(lines, engine, duration)
    except ServiceError:
        raise
    except Exception as exc:
        raise engine_failure_service.engine_failure(exc, name, engine_failure_service.ENGINE_FAILED) from None
    if not found:
        if name == "ollama" and not translate_run_service.ollama_reachable():
            raise DependencyUnavailableError(f"Ollama not reachable; {episode_summary_service.CHOOSE}.")
        raise ServiceError(f"The {name} engine returned no chapters.")
    db.update_drama(drama_id, topic_chapters=json.dumps(found))
    return {"drama_id": drama_id, "chapters": found, "engine": name}


def save(drama_id: int, chapters: list[dict]) -> list[dict]:
    """Replaces the chapters with the person's list ([] clears them), cleaned the way a reply is."""
    ownership_service.require_drama(drama_id)
    lines = db.load_line_objects(drama_id)
    duration = max((ln.end for ln in lines), default=0.0)
    clean = topic_chapters.parse_reply(json.dumps({"chapters": chapters}), duration) if chapters else []
    if chapters and not clean:
        raise InvalidInputError("None of those chapters has a usable start time and title.")
    db.update_drama(drama_id, topic_chapters=json.dumps(clean) if clean else None)
    return clean


def timestamp_text(drama_id: int) -> str:
    """The YouTube-style description list; refuses a title with none, since an empty file helps nobody."""
    drama = ownership_service.require_drama(drama_id)
    chapters = _read(drama)
    if not chapters:
        raise InvalidInputError("This title has no topic chapters yet.")
    return topic_chapters.timestamp_list(chapters)

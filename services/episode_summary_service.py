"""Why a translate run wrote no episode summary, and writing one on demand.

The run writes the summary last, with the Settings "episode summary engine"
(default Claude), and never fails the translation over it. That used to
leave an empty summary with no hint why; skip_reason() and
missing_after_run_reason() give the plain-words reason for the job result, and
generate() fills the summary later with an engine the person picks.
"""
import db
from engine_backends import engine_registry
from engine_backends import llm_tasks, paid_calls
from services import (engine_failure_service, line_ai_service, ownership_service, settings_service, translate_run_service,
                      workspace_job_service,
                      translate_service)
from lib.errors import DependencyUnavailableError, ServiceError, UnsupportedOperationError

CHOOSE = "choose an engine in Settings"


def _label(choice: str) -> str:
    return "Ollama" if choice == "ollama" else choice


def no_engine_reason(allow_paid: bool) -> str:
    """Why translate_run_service.pick_summary_engine gave no engine. Never raises:
    a bad Settings value gives a reason, not a failed run start."""
    choice = None
    try:
        choice = settings_service.get("episode_summary_engine")
        if not allow_paid and not engine_registry.open_to_all_accounts(choice):
            return (f"Summary skipped: {choice} is a paid engine and this account can't use paid "
                    f"engines; {CHOOSE} that every account may use (Ollama).")
        if choice != "ollama" and not translate_service.resolve_api_key(str(choice)):
            return f"Summary skipped: no {choice} key is saved; {CHOOSE} or add the key."
    except Exception:
        pass
    return f"Summary skipped: the {_label(str(choice or 'chosen'))} engine could not be started; {CHOOSE}."


def missing_after_run_reason(drama_id: int, choice: str, cancelled: bool) -> str:
    """"" when the title has a summary; otherwise why the run left it empty."""
    drama = db.get_drama(drama_id) or {}
    if (drama.get("episode_summary") or "").strip():
        return ""
    if cancelled:
        return "Summary skipped: the run was cancelled."
    if drama.get("status") != "translated":
        return "Summary skipped: some lines are still untranslated."
    if choice == "ollama" and not translate_run_service.ollama_reachable():
        return f"Summary skipped: Ollama not reachable; {CHOOSE}."
    return (f"Summary skipped: the {_label(choice or '')} engine returned no summary "
            f"(or the monthly cap is used up); {CHOOSE}.")


def options(drama_id: int) -> dict:
    """What Details offers: the Settings pick and the engines that can summarize."""
    ownership_service.require_drama(drama_id)
    engines = [e for e in engine_registry.ENGINES
               if e not in engine_registry.TRANSLATION_ONLY_ENGINES]
    return {"engine": settings_service.get("episode_summary_engine"), "engines": engines}


def generate(drama_id: int, engine_name: str, model: str | None = None) -> dict:
    """The summary text for the title's current lines. Writes nothing: the
    person keeps it by saving Details. The engine is built here from the saved
    key, refused once the monthly cap is used up, and the call is logged."""
    drama = ownership_service.require_drama(drama_id)
    engine, name = line_ai_service.engine_for(drama, engine_name, model, None, check_cap=True)
    lines = db.load_line_objects(drama_id)
    if not any((ln.en or "").strip() for ln in lines):
        raise UnsupportedOperationError("Translate some lines first; there is nothing to summarize yet.")
    try:
        with paid_calls.logged_calls(engine, "episode_summary", drama_id=drama_id):
            text = llm_tasks.generate_episode_summary(
                lines, engine, character_names=workspace_job_service.speaker_names(drama_id))
    except ServiceError:
        raise
    except Exception as exc:
        raise engine_failure_service.engine_failure(exc, name, engine_failure_service.ENGINE_FAILED) from None
    if not text:
        if name == "ollama" and not translate_run_service.ollama_reachable():
            raise DependencyUnavailableError(f"Ollama not reachable; {CHOOSE}.")
        raise ServiceError(f"The {name} engine returned no summary.")
    return {"drama_id": drama_id, "summary": text, "engine": name}

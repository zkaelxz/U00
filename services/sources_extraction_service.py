"""
services/sources_extraction_service.py -- the pasted-URL extraction extras
for the API (Streamlit Sources parity SO09): the optional AI-assisted
fallback engine.

SO09, the AI fallback. The extraction ladder (sources/adaptive.py) tries a
saved site profile, then deterministic extraction, and asks an LLM only
when those come back empty or ambiguous: one call per page, cached. The
API makes it opt-in per request (`use_ai`), with the engine picked from
the reference-capable ones (`ai_engines`), defaulting to the saved default
engine (Settings > Defaults, settings_service.get_default_engine) when that
is one of them. The key is resolved here, on the PC, from .env; it is never
taken from, or returned to, the client, and a failure to build the engine
is reported without it (translate_engines.redact_secrets). The route checks
`engines.paid` for the named engine first (api.auth.require_engines_allowed).
"""

from typing import Optional

import translate_engines
from services import settings_service
from services.service_errors import DependencyUnavailableError, InvalidInputError

# The offline test engine answers every prompt with canned text; it is not
# offered (the Streamlit picker left it out too).
_HIDDEN_ENGINES = ("test_offline",)
_NO_ENGINE = "Pick an AI engine for the fallback."
_ENGINE_FAILED = "The AI engine could not be set up."


def ai_engines() -> list:
    """Engine names the AI fallback may use (those that take a reference
    prompt, like the Streamlit picker)."""
    return [e for e, cls in translate_engines.ENGINES.items()
            if getattr(cls, "supports_reference", False) and e not in _HIDDEN_ENGINES]


def default_ai_engine() -> Optional[str]:
    """The saved default engine when the fallback can use it, else None."""
    name = settings_service.get_default_engine()
    return name if name in ai_engines() else None


def engines_view() -> dict:
    """{engines, default} for the picker. Names only: no key, no key status."""
    return {"engines": ai_engines(), "default": default_ai_engine()}


def resolve_ai_engine_name(use_ai, engine) -> Optional[str]:
    """The engine name a request asks for, or None when the fallback is off.
    422 for an engine the fallback can't use, or none given and no usable
    saved default."""
    if not use_ai:
        return None
    if engine is None:
        engine = default_ai_engine()
        if engine is None:
            raise InvalidInputError(_NO_ENGINE, details={"allowed": ai_engines()})
    if not isinstance(engine, str) or engine not in ai_engines():
        raise InvalidInputError("That engine can't be used for the AI fallback.",
                                details={"allowed": ai_engines()})
    return engine


def build_ai_engine(name: Optional[str]):
    """The engine object for `name` (None -> None, the fallback stays off).
    The key is resolved on this PC and stays inside the engine object.
    503 when no key is configured or the engine isn't installed."""
    if name is None:
        return None
    if name == "ollama":
        key, base_url = "local", settings_service.resolve_key("ollama_url") or None
    else:
        key, base_url = settings_service.resolve_key(name), None
        if not key:
            raise DependencyUnavailableError(
                f"No {name} key is configured. Set one in Settings first.")
    try:
        return translate_engines.get_engine(
            name, key, base_url=base_url,
            free_tier=name == "gemini" and settings_service.get_gemini_free_tier())
    except ImportError:
        raise DependencyUnavailableError(
            f"The {name} engine isn't installed on this PC.") from None
    except Exception as e:
        # Never the key: the message is redacted and the cause dropped.
        raise DependencyUnavailableError(
            translate_engines.redact_secrets(f"{_ENGINE_FAILED} ({type(e).__name__})")) from None

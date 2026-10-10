"""
services/engine_routing_service.py -- capability-based AI task
routing ("which engine does what").

A task asks for a capability (`resolve_capability("translation.cheap")`)
instead of naming an engine. Each capability maps to the engine the user
chose for it in Settings; unset, it falls back to a fixed default (usually
Settings' default engine, so nothing changes until the user picks one).
This is an additive layer over translate_engines.ENGINES/get_engine, not a
second engine registry, and it never switches engines on its own: resolving
returns the configured choice, whether or not its key works (settled
decision: no automatic model switching).

Two capabilities are views of settings that already existed, so there is one
source of truth for each: `translation.cheap` IS the "default engine for new
dramas" preference and `summary.episode` IS the episode-summary engine
preference. The others are stored in db.app_settings under
"capability.<id>".

Also here: the per-engine status for Settings
(not configured / untested / working / failed) and the "Test" action, which
makes one short real call through diagnostics_report.check_engine_reachable with
the key resolved on the PC. Keys are never accepted or returned; a failure
message goes through translate_engines.redact_secrets.
"""
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone

import db
import diagnostics_report
import translate_engines
from services import settings_service, translate_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, NotFoundError,
                                      UnsupportedOperationError)

_STORE_PREFIX = "capability."
_DEFAULT_ENGINE = "default_engine"  # sentinel: Settings' default engine

# id -> definition. "requires": the engine tag an engine must carry to be
# offered; "pref": an existing settings_service preference this capability
# reads and writes instead of its own app setting; "default": the engine used
# while unset (_DEFAULT_ENGINE = Settings' default engine); "choices": an
# extra allow-list (or a function returning one) on top of "requires";
# "unset_label": the capability is OFF while unset (its unset option is labelled with this, and only an explicit
# choice counts as set, even one equal to the default).
CAPABILITIES = {
    "translation.cheap": {
        "label": "Everyday translation",
        "help": "Used for each new drama, and for a drama that has no engine of its own.",
        "requires": translate_engines.CAP_TRANSLATE,
        "pref": "default_engine",
        "choices": settings_service.engine_preference_choices,
    },
    "translation.high_quality": {
        "label": "Stronger translation for hard lines",
        "help": ("Suggested in Review for a line with a QC flag or a glossary conflict. "
                 "Off until you pick an engine here. Only a suggestion: nothing runs until "
                 "you choose it."),
        "requires": translate_engines.CAP_TRANSLATE,
        "default": _DEFAULT_ENGINE,
        "unset_label": "Off (no suggestions)",  # no suggestions are offered while unset
    },
    "llm.instructions": {
        "label": "Line helpers for translation-only engines",
        "help": ("Improve, Why this?, Alternatives and Grammar use the drama's own engine. "
                 "For a drama translated with an engine that "
                 "can't follow instructions, they use this engine instead."),
        "requires": translate_engines.CAP_INSTRUCTIONS,
        "default": _DEFAULT_ENGINE,
    },
    "summary.episode": {
        "label": "Episode summaries",
        "help": "Writes the short summary that gives the next episode its context.",
        "requires": translate_engines.CAP_INSTRUCTIONS,
        "pref": "episode_summary_engine",
        "choices": lambda: settings_service.SUMMARY_ENGINE_CHOICES,
    },
    "research.grounded_search": {
        "label": "Web-grounded research",
        "help": "Looks up titles and names with web search results (metadata research).",
        "requires": translate_engines.CAP_GROUNDED_SEARCH,
        "default": "gemini",
    },
}

ENGINE_TEST_PREFIX = settings_service.ENGINE_TEST_PREFIX
_MAX_TEST_ERROR = 300
# The request waits this long for a Test; a slower engine is reported as a
# failure (its call finishes in the background and is ignored). SDK clients
# can otherwise wait minutes.
TEST_TIMEOUT_S = 45
_testing = set()
_testing_lock = threading.Lock()
# Engine id -> why its Test isn't offered (e.g. a large first-use download).
# Empty while every engine is quick to test; the API and Settings still show a reason.
_NO_TEST: dict = {}


def _definition(capability: str) -> dict:
    if capability not in CAPABILITIES:
        raise NotFoundError("Unknown capability.")
    return CAPABILITIES[capability]


def engine_choices(capability: str) -> list:
    """Engines that may back `capability`, in ENGINES order."""
    d = _definition(capability)
    names = translate_engines.engines_with_capability(d["requires"])
    if d.get("choices"):
        allowed = d["choices"]()
        names = [n for n in names if n in allowed]
    return names


def _stored(capability: str):
    """The user's saved engine for `capability`, or None when unset or no
    longer valid (an engine that was removed or lost the capability)."""
    d = _definition(capability)
    if d.get("pref") == "default_engine":
        raw = settings_service.get_default_engine()  # the one public getter for it
    elif d.get("pref"):
        raw = settings_service.get_preference(d["pref"])
    else:
        raw = db.get_app_setting(_STORE_PREFIX + capability, None)
    return raw if isinstance(raw, str) and raw in engine_choices(capability) else None


def default_engine_for(capability: str) -> str:
    d = _definition(capability)
    if d.get("pref"):
        return settings_service.preference_default(d["pref"])
    default = d.get("default", _DEFAULT_ENGINE)
    return settings_service.get_default_engine() if default == _DEFAULT_ENGINE else default


def is_configured(capability: str) -> bool:
    """True once the user picked an engine for `capability` in Settings."""
    return _stored(capability) is not None


def resolve_capability(capability: str) -> str:
    """The engine name configured for `capability`, else its default. Pure
    configuration lookup: never checks keys, never tries another engine."""
    return _stored(capability) or default_engine_for(capability)


def set_capability_engine(capability: str, engine) -> dict:
    """Saves the engine for `capability`; None clears it back to the
    default. Returns the capability's refreshed entry."""
    d = _definition(capability)
    if engine is not None and engine not in engine_choices(capability):
        raise InvalidInputError("That engine can't do this task.")
    if d.get("pref"):
        value = engine if engine is not None else settings_service.preference_default(d["pref"])
        settings_service.set_settings({d["pref"]: value})
    else:
        db.set_app_setting(_STORE_PREFIX + capability, engine)
    return _capability_entry(capability)


def _capability_entry(capability: str) -> dict:
    d = _definition(capability)
    stored = _stored(capability)
    default = default_engine_for(capability)
    engine = stored or default
    return {
        "id": capability,
        "label": d["label"],
        "help": d["help"],
        "requires": d["requires"],
        "engine": engine,
        "default_engine": default,
        # A pref-backed capability always has a saved value's worth of
        # meaning: it equals the preference, set or not.
        "is_default": (stored is None if d.get("unset_label")
                       else stored is None or stored == default),
        "unset_label": d.get("unset_label"),
        "engine_supported": engine in engine_choices(capability),
        "choices": engine_choices(capability),
    }


def _needs_key(engine: str) -> bool:
    return engine in settings_service.KEY_WRITE_ENGINES


def _last_test(engine: str):
    raw = db.get_app_setting(ENGINE_TEST_PREFIX + engine, None)
    if not isinstance(raw, dict) or not isinstance(raw.get("ok"), bool):
        return None
    return {"ok": raw["ok"], "tested_at": str(raw.get("tested_at") or ""),
            "error": raw.get("error") if not raw["ok"] else None}


def engine_status(engine: str, key_status: dict = None) -> dict:
    """{engine, tags, needs_key, key_configured, status, last_test} for one
    engine. status: "not_configured" (needs a key, none set), "untested",
    "working" or "failed" (the last Test). Booleans and short redacted text
    only; never the key or a URL."""
    if engine not in translate_engines.ENGINES:
        raise NotFoundError(translate_engines.unknown_engine_message(engine))
    keys = key_status if key_status is not None else settings_service.key_status()
    needs_key = _needs_key(engine)
    configured = bool(keys.get(engine)) if needs_key else True
    last = _last_test(engine)
    if not configured:
        status = "not_configured"
    elif last is None:
        status = "untested"
    else:
        status = "working" if last["ok"] else "failed"
    return {"engine": engine,
            "tags": sorted(translate_engines.engine_capabilities(engine)),
            "test_blocked": _NO_TEST.get(engine),
            "needs_key": needs_key, "key_configured": configured,
            "status": status, "last_test": last}


def get_routing() -> dict:
    """What the Settings "Which engine does what" section shows."""
    keys = settings_service.key_status()
    return {"capabilities": [_capability_entry(c) for c in CAPABILITIES],
            "engines": [engine_status(e, keys) for e in translate_engines.ENGINES]}


def test_engine(engine: str, model: str = None) -> dict:
    """One short real translate call with the key saved on
    this PC. Spends a tiny amount of quota on a paid engine, so it runs only
    from an explicit button. Records the outcome for the status badge and
    returns the engine's refreshed status."""
    if engine not in translate_engines.ENGINES:
        raise NotFoundError(translate_engines.unknown_engine_message(engine))
    api_key = translate_service.resolve_api_key(engine)
    if api_key is None and _needs_key(engine):
        raise DependencyUnavailableError(f"No {engine} key is configured. Add one first.")
    if engine in _NO_TEST:
        raise UnsupportedOperationError(_NO_TEST[engine])
    base_url = (settings_service.resolve_key("ollama_url") or None) if engine == "ollama" else None
    generation = settings_service.engine_test_generation(engine)
    with _testing_lock:
        if engine in _testing:
            raise ConflictError("A test of this engine is already running.")
        _testing.add(engine)

    def _release(_future=None):
        with _testing_lock:
            _testing.discard(engine)

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(diagnostics_report.check_engine_reachable, engine, api_key, model,
                             base_url=base_url)
    except Exception:
        _release()
        raise
    # The engine stays "being tested" until its call really ends, so a click
    # after a timeout can't stack a second real call on a hung one.
    future.add_done_callback(_release)
    pool.shutdown(wait=False)
    try:
        result = future.result(timeout=TEST_TIMEOUT_S)
    except FutureTimeout:
        result = {"ok": False, "error": f"No answer within {TEST_TIMEOUT_S} seconds."}
    error = None
    if not result.get("ok"):
        # Keys, then paths and URLs-with-paths (a local server's address)
        # come out: this text is shown later by an admin.settings read.
        error = diagnostics_report.redact_for_support(str(result.get("error") or "The test failed."))
        error = error[:_MAX_TEST_ERROR]
    # A key or endpoint saved while the test ran makes this result stale:
    # it tested the old one, so it isn't recorded.
    if settings_service.engine_test_generation(engine) == generation:
        db.set_app_setting(ENGINE_TEST_PREFIX + engine, {
            "ok": bool(result.get("ok")),
            "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": error})
    return engine_status(engine)

"""
services/assistant_settings_service.py -- the maintenance assistant's saved
settings: Developer Mode, default and review engine, per-provider consent to
send code and logs, and the escalation ladder of engines. UI-free.
"""

import re

import db
from engine_backends import echo
from lib.errors import ConflictError, InvalidInputError


MAX_MODEL_CHARS = 100

_SETTINGS_PREFIX = "assistant."



def _llm_engine_choices() -> list:
    from engine_backends import engine_registry
    return sorted(n for n in engine_registry.ENGINES
                  if n not in engine_registry.TRANSLATION_ONLY_ENGINES)


def _get(key: str, default=None):
    try:
        return db.get_app_setting(_SETTINGS_PREFIX + key, default)
    except Exception:
        return default


DEFAULT_ENGINE = "ollama"


def _is_local_engine(name: str, model=None) -> bool:
    """Loopback Ollama with a non-hosted model (None: the saved one)."""
    from engine_backends.local import is_ollama_cloud_model
    if name != "ollama" or not _ollama_is_local():
        return False
    if model is None and (_get("engine") or DEFAULT_ENGINE) == name:
        model = _get("model")
    return not is_ollama_cloud_model(model)


def _cloud_consent() -> dict:
    raw = _get("cloud_consent", {})
    return {k: True for k, v in raw.items() if v is True} if isinstance(raw, dict) else {}


def _ollama_is_local() -> bool:
    """Ollama is local only when its endpoint is this PC (loopback); a
    remote or LAN Ollama server needs consent like a cloud engine."""
    from services import settings_service
    return settings_service.ollama_endpoint_is_loopback()


def cloud_consent_given(engine_name: str, model=None) -> bool:
    """A local engine needs no consent; a cloud one (or Ollama on another
    machine) needs the owner's saved, per-provider "allow sending code
    and logs" consent."""
    return _is_local_engine(engine_name, model) or _cloud_consent().get(engine_name) is True


def require_cloud_consent(engine_name: str, model=None):
    if not cloud_consent_given(engine_name, model):
        raise ConflictError(
            f"Sending this app's code and logs to {engine_name} isn't allowed yet. Allow it "
            "for that engine in the assistant's settings, or use Ollama to keep everything "
            "on this PC.", details={"reason": "cloud_consent_required", "engine": engine_name})


def developer_mode_enabled() -> bool:
    """Off by default; a DB hiccup reads as off."""
    return _get("developer_mode", False) is True


MAX_TIERS = 3
# Paid hosted engines, in the order the default ladder picks its last tier.
_HOSTED_ENGINES = ("claude", "deepseek")



def _key_set(name: str) -> bool:
    """True when the engine can be built: a local server needs no key.
    Only ever a boolean; the key itself stays in the key store."""
    from services import translate_service
    try:
        return translate_service.resolve_api_key(name) is not None
    except Exception:
        return False


def _default_tier_order() -> list:
    """Ollama on this PC, then Gemini if a key is set, then one paid
    hosted engine: the saved default engine if it is one, else the first
    with a key."""
    order = ["ollama"]
    if _key_set("gemini"):
        order.append("gemini")
    saved = _get("engine")
    hosted = [saved] if saved in _HOSTED_ENGINES else []
    hosted += [e for e in _HOSTED_ENGINES if e not in hosted]
    order += [e for e in hosted if _key_set(e)][:1]
    return order


def _saved_tier_order():
    raw = _get("tiers")
    if isinstance(raw, list) and raw and all(isinstance(e, str) for e in raw):
        return raw[:MAX_TIERS]
    return None


def tier_ladder(choices=None) -> list:
    """The tiers actually offered, in order: the saved order (or the
    default), skipping engines that aren't chat-capable here or have no
    key. Each entry says whether it stays on this PC and whether its
    provider's send-code-and-logs consent is saved; no key values."""
    choices = choices if choices is not None else _llm_engine_choices()
    out = []
    for name in _saved_tier_order() or _default_tier_order():
        if name not in choices or any(t["engine"] == name for t in out) or not _key_set(name):
            continue
        out.append({"tier": len(out) + 1, "engine": name, "local": _is_local_engine(name),
                    "consent": cloud_consent_given(name)})
    return out


def _tier_of(engine_name: str, ladder: list):
    """(tier number or None, next engine or None) for an engine."""
    for i, t in enumerate(ladder):
        if t["engine"] == engine_name:
            return t["tier"], (ladder[i + 1]["engine"] if i + 1 < len(ladder) else None)
    return None, None


def get_settings() -> dict:
    engine = _get("engine")
    model = _get("model")
    review_engine = _get("review_engine")
    review_model = _get("review_model")
    choices = _llm_engine_choices()
    local = {c for c in choices if _is_local_engine(c)}
    return {
        # The escalation ladder: the saved order (None = the default) and
        # the tiers it gives right now. Keys only as booleans.
        "tier_order": _saved_tier_order(),
        "tiers": tier_ladder(choices),
        "engine_keys": {c: _key_set(c) for c in choices},
        "developer_mode": developer_mode_enabled(),
        "engine": engine if engine in choices else None,
        "model": model if isinstance(model, str) and model else None,
        "engine_choices": choices,
        # Implement -> independent review, off by default.
        "roles_enabled": _get("roles_enabled", False) is True,
        "review_engine": review_engine if review_engine in choices else None,
        "review_model": review_model if isinstance(review_model, str) and review_model else None,
        "default_engine": DEFAULT_ENGINE,
        "local_engines": sorted(local),
        # Per cloud engine (and Ollama on another machine): may the
        # assistant send code and logs to it?
        "cloud_consent": {c: cloud_consent_given(c) for c in choices if c not in local},
    }


def _check_engine_name(name, field="engine"):
    if name is None:
        return None
    choices = _llm_engine_choices()
    if not isinstance(name, str) or name not in choices:
        raise InvalidInputError(f"{field} must be one of the chat-capable engines.")
    return name


def _check_model(model, field="model"):
    if model is None or model == "":
        return None
    if (not isinstance(model, str) or len(model) > MAX_MODEL_CHARS
            or re.search(r"[\s\x00-\x1f\x7f]", model)):
        raise InvalidInputError(
            f"{field} must be at most {MAX_MODEL_CHARS} characters with no spaces or control characters.")
    return model


def set_settings(updates: dict) -> dict:
    """Validates everything before writing anything."""
    if not isinstance(updates, dict):
        raise InvalidInputError("Settings must be an object.")
    cleaned = {}
    for key, value in updates.items():
        if key in ("developer_mode", "roles_enabled"):
            if not isinstance(value, bool):
                raise InvalidInputError(f"{key} must be true or false.")
            cleaned[key] = value
        elif key in ("engine", "review_engine"):
            cleaned[key] = _check_engine_name(value, key)
        elif key in ("model", "review_model"):
            cleaned[key] = _check_model(value, key)
        elif key == "cloud_consent":
            if not isinstance(value, dict) or len(value) > 20:
                raise InvalidInputError("cloud_consent must map engine names to true/false.")
            merged = _cloud_consent()
            for eng, allowed in value.items():
                _check_engine_name(eng, "cloud_consent engine")
                # Ollama may be on another machine.
                if not isinstance(allowed, bool):
                    raise InvalidInputError("cloud_consent is for cloud engines, as true/false.")
                merged[eng] = allowed
            cleaned[key] = {k: v for k, v in merged.items() if v is True}
        elif key == "tiers":
            # None or [] goes back to the default ladder.
            if value is None or value == []:
                cleaned[key] = None
                continue
            if (not isinstance(value, list) or len(value) > MAX_TIERS
                    or not all(isinstance(e, str) for e in value)):
                raise InvalidInputError(f"tiers must be a list of at most {MAX_TIERS} engines.")
            names = [_check_engine_name(e, "tiers engine") for e in value]
            if len(set(names)) != len(names):
                raise InvalidInputError("Each engine can be one tier only.")
            cleaned[key] = names
        else:
            raise InvalidInputError("Unknown assistant setting.")
    current = get_settings()
    for eng, mod in (("engine", "model"), ("review_engine", "review_model")):
        if eng in cleaned and mod not in cleaned and cleaned[eng] != current[eng]:
            cleaned[mod] = None  # a model belongs to its engine
    for key, value in cleaned.items():
        db.set_app_setting(_SETTINGS_PREFIX + key, value)
    echo.set_enabled(developer_mode_enabled())
    return get_settings()


def _require_developer_mode():
    if not developer_mode_enabled():
        raise ConflictError("Developer Mode is off. Turn it on in Settings to use the assistant.")

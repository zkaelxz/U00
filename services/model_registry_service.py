"""
services/model_registry_service.py -- Step 40: model deprecation /
migration assistant. UI-free.

Answers "is a model this app is set up to use deprecated, retired, or no
longer offered by its provider?" and helps the user switch -- never
automatically (a settled decision: no automatic model switching).

- The registry (services/model_registry.json) is shipped data: per
  engine/model a status (current / legacy / deprecated / retired), dates,
  a suggested replacement and a note. Only sourced facts go in it.
- The provider check is manual (one click, at most once a minute, never
  two at once): each configured cloud engine's own model-list endpoint on
  a fixed host, with its key in a header and a timeout. The answer is
  cached so the status view never calls out by itself. Engines without a
  key, or without a list endpoint, are reported as not checked.
- "Configured" models are every place a model string is set in this app:
  each engine's built-in default, the workflow tiers and saved presets.
  (Step 36's capability routing, when it lands, can add its own.)
- The guided switch changes one saved preset's model to the replacement,
  only when the user confirms, and only if the preset still has the model
  the user saw (409 otherwise). Built-in defaults and workflow tiers are
  code: the answer for those is "update the app", never a silent change.

Keys never leave this module: not returned, not logged, not stored. Error
text from a provider is passed through translate_engines.redact_secrets.
"""
import datetime
import inspect
import json
import os
import re
import threading
import time

import db
import translate_engines
from services import translate_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     RateLimitedError)

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_registry.json")
STATUSES = ("current", "legacy", "deprecated", "retired")
CHECK_CACHE_KEY = "model_registry_provider_check"
CHECK_MIN_INTERVAL_SECONDS = 60
HTTP_TIMEOUT = 15
MAX_MODELS_PER_ENGINE = 2000

# Fixed model-list endpoints. The key always goes in a header.
_PROVIDER_LISTS = {
    "claude": {"url": "https://api.anthropic.com/v1/models?limit=1000",
               "headers": lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"},
               "extract": lambda body: [m.get("id") for m in body.get("data", [])],
               "more": lambda body: bool(body.get("has_more"))},
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
               "headers": lambda key: {"x-goog-api-key": key},
               "extract": lambda body: [str(m.get("name", "")).split("/", 1)[-1]
                                        for m in body.get("models", [])],
               "more": lambda body: bool(body.get("nextPageToken"))},
    "deepseek": {"url": "https://api.deepseek.com/models",
                 "headers": lambda key: {"Authorization": f"Bearer {key}"},
                 "extract": lambda body: [m.get("id") for m in body.get("data", [])],
                 "more": lambda body: False},
}

_check_lock = threading.Lock()
_last_check_started = 0.0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def load_registry(path: str = None) -> dict:
    """{(engine, model): entry}. A missing or unreadable file is an empty
    registry (the provider check still works without it)."""
    try:
        with open(path or REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for e in data.get("models", []):
        if not isinstance(e, dict) or e.get("status") not in STATUSES:
            continue
        if isinstance(e.get("engine"), str) and isinstance(e.get("model"), str):
            out[(e["engine"], e["model"])] = e
    return out


def _default_model(engine: str):
    cls = translate_engines.ENGINES.get(engine)
    if cls is None:
        return None
    param = inspect.signature(cls.__init__).parameters.get("model")
    return param.default if param is not None and param.default is not inspect.Parameter.empty else None


def configured_models() -> list:
    """Every place a model string is set: [{engine, model, where, kind,
    preset_id?}] (kind: "default", "tier" or "preset")."""
    out = []
    for e in translate_service.list_engines():
        if e["name"] not in _PROVIDER_LISTS and e["models"] is None:
            continue
        model = _default_model(e["name"])
        if model:
            out.append({"engine": e["name"], "model": model, "kind": "default",
                        "where": f"{e['name']} built-in default"})
    for key, tier in translate_engines.WORKFLOW_TIERS.items():
        if tier.get("engine_model"):
            out.append({"engine": tier["translation_engine"], "model": tier["engine_model"],
                        "kind": "tier", "where": f"Workflow tier: {tier.get('label') or key}"})
    try:
        from services import extension_service
        ext = db.get_app_setting(extension_service.ENGINE_SETTING)
    except Exception:
        ext = None
    if isinstance(ext, dict) and ext.get("engine") and ext.get("model"):
        out.append({"engine": ext["engine"], "model": ext["model"], "kind": "extension",
                    "where": "Browser extension translation"})
    for p in db.list_presets():
        if p.get("engine_model") and p.get("translation_engine"):
            out.append({"engine": p["translation_engine"], "model": p["engine_model"],
                        "kind": "preset", "preset_id": p["id"],
                        "where": f"Preset: {p.get('name') or p['id']}"})
    return out


def _cached_check() -> dict:
    raw = db.get_app_setting(CHECK_CACHE_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _offered(engine: str) -> list:
    """Models this app offers for `engine`; an engine without a model picker
    (DeepSeek, DeepL, ...) offers only its built-in default, as
    translate_run_service._require_offered_model treats it."""
    entry = next((e for e in translate_service.list_engines() if e["name"] == engine), None)
    if entry is None:
        return []
    if entry["models"] is not None:
        return list(entry["models"])
    default = _default_model(engine)
    return [default] if default else []


# A dated snapshot suffix: "-20260101" (Anthropic), "-2026-01-01", "-09-2025",
# or a numbered version such as "-001" (Google).
_DATED_SUFFIX = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2}|\d{2}-\d{4}|\d{3})$")


def _listed(engine: str, model: str, ids) -> "bool | None":
    """True when the provider lists `model`, or (for an alias) a dated
    snapshot of it; False when it clearly doesn't; None when `model` is an
    alias the list can't confirm either way.

    Aliases: any "-latest" id, and an undated Claude id (Anthropic's list may
    return only dated snapshots, e.g. "claude-sonnet-5-20260101" for
    "claude-sonnet-5"). An undated Claude id that isn't matched is only
    "no longer listed" when the same list also shows undated ids, i.e. the
    provider does list ids in that form."""
    ids = set(ids)
    if model in ids:
        return True
    latest = model.endswith("-latest")
    base = model[:-len("-latest")] if latest else model
    if latest and base in ids:
        return True
    if any(i.startswith(base + "-") and _DATED_SUFFIX.fullmatch(i[len(base):]) for i in ids):
        return True
    if latest:
        return None
    if engine == "claude" and not _DATED_SUFFIX.search(model):
        claude_ids = [i for i in ids if i.startswith("claude-")]
        if claude_ids and all(_DATED_SUFFIX.search(i) for i in claude_ids):
            return None
    return False


def _assess(engine: str, model: str, registry: dict, check: dict) -> dict:
    """status: retired / deprecated / not_listed / legacy / current / unknown,
    with a plain message naming the model and engine."""
    entry = registry.get((engine, model))
    engines_checked = (check.get("engines") or {})
    provider = engines_checked.get(engine) or {}
    listed = None
    alias_unconfirmed = False
    if provider.get("ok"):
        listed = _listed(engine, model, provider.get("models") or [])
        alias_unconfirmed = listed is None
    replacement = entry.get("replacement") if entry else None
    if entry and entry["status"] == "retired":
        status = "retired"
        msg = f"{model} ({engine}) has been retired by the provider"
        msg += f" ({entry['retired_on']})." if entry.get("retired_on") else "."
    elif entry and entry["status"] == "deprecated":
        status = "deprecated"
        msg = f"{model} ({engine}) is deprecated"
        msg += f" and retires on {entry['retires_on']}." if entry.get("retires_on") else "."
    elif alias_unconfirmed and model in _offered(engine):
        # An alias the app offers may not appear in a provider's list (which
        # can show only dated snapshots); not proof it is gone.
        status = "unknown"
        msg = (f"{model} ({engine}) is an alias the provider's list doesn't show, so it "
               "can't be confirmed; it may still work.")
    elif listed is False:
        status = "not_listed"
        msg = (f"{model} is no longer in {engine}'s model list (checked "
               f"{(check.get('checked_at') or '')[:10]}). Calls to it will likely fail.")
    elif _offered(engine) and model not in _offered(engine) and engine != "ollama":
        status = "not_offered"
        msg = (f"{model} ({engine}) isn't a model this app offers any more, so a run with it "
               "is refused.")
        replacement = replacement or (_default_model(engine) if engine in translate_engines.ENGINES
                                      else None)
    elif entry and entry["status"] == "legacy":
        status = "legacy"
        msg = f"{model} ({engine}) is an older model that is still offered."
    elif listed is True or model in _offered(engine):
        status = "current"
        msg = f"{model} ({engine}) is current."
    else:
        status = "unknown"
        msg = f"{model} ({engine}) isn't in the registry or a provider check yet."
    if replacement:
        msg += f" Suggested replacement: {replacement}."
    return {"status": status, "message": msg, "replacement": replacement,
            "note": entry.get("note") if entry else None,
            "listed_by_provider": listed}


_SEVERITY = {"retired": 3, "not_listed": 3, "not_offered": 2, "deprecated": 2, "legacy": 1,
             "current": 0, "unknown": 0}


def get_status() -> dict:
    """Every configured model with its status. Reads the registry and the
    cached provider check only -- never calls out."""
    registry = load_registry()
    check = _cached_check()
    items = []
    for c in configured_models():
        a = _assess(c["engine"], c["model"], registry, check)
        replacement_offered = bool(a["replacement"]) and a["replacement"] in _offered(c["engine"])
        items.append({**c, **a, "severity": _SEVERITY[a["status"]],
                      "can_switch": c["kind"] == "preset" and replacement_offered
                      and a["status"] in ("retired", "not_listed", "not_offered", "deprecated",
                                          "legacy")})
    engines_checked = {
        name: {"ok": bool(v.get("ok")), "model_count": len(v.get("models") or []),
               "error": v.get("error")}
        for name, v in (check.get("engines") or {}).items()}
    return {"items": items,
            "warnings": sum(1 for i in items if i["severity"] >= 2),
            "checked_at": check.get("checked_at"),
            "engines_checked": engines_checked,
            "registry_updated": _registry_updated()}


def _registry_updated():
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("updated")
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Manual provider check
# ---------------------------------------------------------------------------

def _fetch_models(engine: str, key: str) -> list:
    import requests
    spec = _PROVIDER_LISTS[engine]
    # No redirects: a custom key header (x-api-key, x-goog-api-key) would
    # otherwise follow one to another host.
    resp = requests.get(spec["url"], headers=spec["headers"](key), timeout=HTTP_TIMEOUT,
                        allow_redirects=False)
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError("unexpected response shape")
    models = [m for m in spec["extract"](body) if isinstance(m, str) and m]
    # An empty or paginated answer is not a complete list: treating it as one
    # would mark every configured model "no longer listed".
    if not models:
        raise ValueError("the provider returned an empty model list")
    if spec["more"](body) or len(models) > MAX_MODELS_PER_ENGINE:
        raise ValueError("the provider's model list was incomplete")
    return models


def check_providers(now: float = None) -> dict:
    """Asks each configured cloud engine for its current model list and
    caches the answer. Manual only; RateLimitedError within a minute of the
    last check or while one is running."""
    global _last_check_started
    now = time.time() if now is None else now
    if not _check_lock.acquire(blocking=False):
        raise RateLimitedError("A model check is already running.")
    try:
        if now - _last_check_started < CHECK_MIN_INTERVAL_SECONDS:
            raise RateLimitedError("Models were checked less than a minute ago.")
        _last_check_started = now
        results = {}
        for engine in _PROVIDER_LISTS:
            key = translate_service.resolve_api_key(engine)
            if not key:
                continue
            try:
                results[engine] = {"ok": True, "models": _fetch_models(engine, key)}
            except Exception as exc:
                msg = translate_engines.redact_secrets(f"{type(exc).__name__}: {exc}")
                if key and key in msg:
                    msg = msg.replace(key, "[redacted]")
                results[engine] = {"ok": False, "models": [], "error": msg[:300]}
        db.set_app_setting(CHECK_CACHE_KEY, json.dumps({
            "checked_at": datetime.datetime.utcnow().isoformat(), "engines": results}))
    finally:
        _check_lock.release()
    return get_status()


# ---------------------------------------------------------------------------
# Guided switch (user-confirmed)
# ---------------------------------------------------------------------------

def switch_preset_model(preset_id: int, from_model: str, to_model: str) -> dict:
    """Changes one saved preset's model, only from the model the user saw
    to one its engine offers. Nothing else about the preset changes."""
    preset = next((p for p in db.list_presets() if p["id"] == preset_id), None)
    if preset is None:
        raise NotFoundError("Preset not found.")
    engine = preset.get("translation_engine")
    if preset.get("engine_model") != from_model:
        raise ConflictError("The preset's model changed since you looked; refresh and try again.")
    if not isinstance(to_model, str) or to_model not in _offered(engine or ""):
        raise InvalidInputError("That model isn't offered for this preset's engine.")
    if to_model == from_model:
        raise InvalidInputError("The preset already uses that model.")
    if not db.set_preset_engine_model(preset_id, to_model, expected_model=from_model):
        raise ConflictError("The preset's model changed since you looked; refresh and try again.")
    return {"preset_id": preset_id, "engine": engine, "from_model": from_model,
            "to_model": to_model}

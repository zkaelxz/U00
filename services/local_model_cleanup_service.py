"""services/local_model_cleanup_service.py -- the one-time 0.2.0 startup pass that clears saved local Ollama models."""
import contextlib
import json

import db
from engine_backends import engine_registry

MARKER = "migrations.local_ollama_models_cleared"

# Cleared to "no model" rather than rewritten to the hosted tag: an unset model is
# resolved at run time through effective_default_model and the hosted-default
# opt-in, whereas a stored hosted tag would read as the owner's explicit choice.
_OVERRIDES_DEFAULTS = engine_registry.MODEL_OVERRIDE_DEFAULTS_KEY
_OVERRIDES_TIERS = engine_registry.MODEL_OVERRIDE_TIERS_KEY
_EXTENSION = "extension_translation_engine"
_QUEUE = "job_queue"
_REEVAL_PRODUCTION = "model_reeval_production"
_ASSISTANT_ENGINE, _ASSISTANT_MODEL = "assistant.engine", "assistant.model"
_ASSISTANT_REVIEW_ENGINE, _ASSISTANT_REVIEW_MODEL = "assistant.review_engine", "assistant.review_model"
# The assistant runs on Ollama when no engine is saved (assistant_settings_service.DEFAULT_ENGINE).
_ASSISTANT_DEFAULT_ENGINE = "ollama"


def _is_local(engine, model) -> bool:
    return (isinstance(model, str) and bool(model)
            and engine_registry.drop_saved_local_model(engine, model) is None)


def _setting(conn, key):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return None


def _put(conn, key, value):
    conn.execute("UPDATE app_settings SET value = ? WHERE key = ?", (json.dumps(value), key))


def _clear_presets(conn) -> int:
    rows = conn.execute("SELECT id, engine_model FROM presets WHERE translation_engine = 'ollama' "
                        "AND engine_model IS NOT NULL").fetchall()
    ids = [r["id"] for r in rows if _is_local("ollama", r["engine_model"])]
    conn.executemany("UPDATE presets SET engine_model = NULL WHERE id = ?", [(i,) for i in ids])
    return len(ids)


def _title_engines(conn, unset_engine: str) -> dict:
    # A title with no engine translates with the routed default, as queue_service._translate_engine does.
    return {r["id"]: r["translation_engine"] or unset_engine
            for r in conn.execute("SELECT id, translation_engine FROM dramas")}


def _clear_title_starts(conn, engines: dict) -> int:
    changed = 0
    for row in conn.execute("SELECT id, translate_start_json FROM dramas "
                            "WHERE translate_start_json LIKE '%engine_model%'").fetchall():
        try:
            start = json.loads(row["translate_start_json"])
        except (TypeError, ValueError):
            continue
        # The stored model is the title's own engine's (profile_service.remember_run).
        if isinstance(start, dict) and _is_local(engines.get(row["id"]), start.get("engine_model")):
            start.pop("engine_model")
            conn.execute("UPDATE dramas SET translate_start_json = ? WHERE id = ?",
                         (json.dumps(start, sort_keys=True), row["id"]))
            changed += 1
    return changed


def _clear_overrides(conn, key: str, engine_of) -> int:
    stored = _setting(conn, key)
    if not isinstance(stored, dict):
        return 0
    local = [k for k, v in stored.items() if _is_local(engine_of(k), v)]
    if local:
        _put(conn, key, {k: v for k, v in stored.items() if k not in local})
    return len(local)


def _clear_extension(conn) -> int:
    saved = _setting(conn, _EXTENSION)
    if isinstance(saved, dict) and _is_local(saved.get("engine"), saved.get("model")):
        _put(conn, _EXTENSION, {**saved, "model": None})
        return 1
    return 0


def _clear_queue(conn, engines: dict) -> int:
    doc = _setting(conn, _QUEUE)
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        return 0
    changed = 0
    for item in doc["items"]:
        settings = item.get("settings") if isinstance(item, dict) else None
        if not isinstance(settings, dict) or "model" not in settings:
            continue
        # A Translate step runs on its title's engine; every other step names its own.
        engine = engines.get(item.get("drama_id")) if item.get("step") == "translate" else settings.get("engine")
        if _is_local(engine, settings["model"]):
            del settings["model"]
            changed += 1
    if changed:
        _put(conn, _QUEUE, doc)
    return changed


def _clear_reeval_production(conn) -> int:
    # model_reeval_service saves this record as JSON text inside the JSON value.
    raw = _setting(conn, _REEVAL_PRODUCTION)
    try:
        prod = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return 0
    if not isinstance(prod, dict):
        return 0
    changed = 0
    for record in prod.values():
        if isinstance(record, dict) and _is_local(record.get("engine"), record.get("model")):
            record["model"] = None
            changed += 1
    if changed:
        _put(conn, _REEVAL_PRODUCTION, json.dumps(prod))
    return changed


def _clear_assistant(conn) -> int:
    changed = 0
    for engine_key, model_key, unset in ((_ASSISTANT_ENGINE, _ASSISTANT_MODEL, _ASSISTANT_DEFAULT_ENGINE),
                                         (_ASSISTANT_REVIEW_ENGINE, _ASSISTANT_REVIEW_MODEL, None)):
        if _is_local(_setting(conn, engine_key) or unset, _setting(conn, model_key)):
            _put(conn, model_key, None)
            changed += 1
    return changed


def run_once() -> dict:
    """Clears every saved local Ollama model once and returns {storage name: values cleared},
    only the ones that changed. Hosted (-cloud) tags and other engines' models are left as they are."""
    from services import engine_routing_service
    unset_engine = engine_routing_service.resolve_capability("translation.cheap")
    tiers = engine_registry.WORKFLOW_TIERS
    with contextlib.closing(db.get_conn()) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Checked under the write lock: another process may have run it while this one waited.
            if conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (MARKER,)).fetchone():
                conn.execute("ROLLBACK")
                return {}
            engines = _title_engines(conn, unset_engine)
            counts = {
                "presets": _clear_presets(conn),
                "title start values": _clear_title_starts(conn, engines),
                _OVERRIDES_DEFAULTS: _clear_overrides(conn, _OVERRIDES_DEFAULTS, lambda k: k),
                _OVERRIDES_TIERS: _clear_overrides(
                    conn, _OVERRIDES_TIERS, lambda k: (tiers.get(k) or {}).get("translation_engine")),
                _EXTENSION: _clear_extension(conn),
                _QUEUE: _clear_queue(conn, engines),
                _REEVAL_PRODUCTION: _clear_reeval_production(conn),
                "assistant": _clear_assistant(conn),
            }
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, 'true')", (MARKER,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    changed = {name: n for name, n in counts.items() if n}
    _log(changed)
    return changed


def _log(changed: dict) -> None:
    try:
        import applog
        if changed:
            applog.get_logger().info(
                "Cleared %d saved local Ollama model choice(s); they now follow the default Ollama model (%s): %s",
                sum(changed.values()), engine_registry.builtin_default_model("ollama"),
                ", ".join(f"{name} {n}" for name, n in sorted(changed.items())))
        else:
            applog.get_logger().info("No saved local Ollama model choices to clear.")
    except OSError:
        pass   # no log folder is no reason to stop starting

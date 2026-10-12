"""
services/translate_service.py -- metadata and translate action for the
standalone translate tool.
The read-only "list engines" / "list history" half (docs/archive/migration-review.md
section 3.7) is paired with translate() itself, resolving a server-side key per engine (D2 --
docs/archive/migration-review.md section 6) rather than accepting one from the
caller.

clear_history() is the one piece deliberately
added separately from the read-only half and translate().
"""
import threading
import types
from typing import Optional

import bulk_translate
import db
import translate_engines
from services import ownership_service, settings_service, translate_run_service
from services.service_errors import (
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    UnsupportedOperationError,
)

# translate_engines.ENGINES keys whose engine class needs no API key to run
# (see translate_engines.py's own ENGINES / FREE_ENGINES):
#   - ollama: its key is optional, defaulting to the literal "local" when
#     nothing is configured (resolve_api_key below) -- it points
#     at a locally-run server, not a hosted API that requires an account
#     key. There's nothing meaningful to "configure" in the same sense as an
#     API key, so it's reported as configured too.
# Everything else (claude/deepseek/gemini/openai) maps directly onto
# services.settings_service.key_status(), which is keyed by the same engine
# name for these three.
_NO_KEY_REQUIRED_ENGINES = translate_engines.KEYLESS_ENGINES

# Engine name -> the model dict (if any) the user picks a model from for
# that engine.
ENGINE_MODEL_DICTS = {
    "claude": translate_engines.CLAUDE_MODELS,
    "gemini": translate_engines.GEMINI_MODELS,
    "openai": translate_engines.OPENAI_MODELS,
    # Local tags only: this dict is also what Diagnostics lets the owner
    # pick a new default from, and a hosted tag must never become one.
    "ollama": translate_engines.OLLAMA_MODELS,
}


def _saved_ollama_models() -> list:
    """Ollama model tags saved in presets, so a preset's hosted tag that is
    not built in is still offered (and flagged) rather than silently dropped."""
    tags = []
    for preset in db.list_presets():
        tag = preset.get("engine_model")
        if (preset.get("translation_engine") == "ollama" and isinstance(tag, str)
                and translate_engines.MODEL_ID_RE.fullmatch(tag) and ".." not in tag):
            tags.append(tag)
    return list(dict.fromkeys(tags))


def list_engines(env_path: Optional[str] = None) -> list:
    """One entry per translate_engines.ENGINES key: name, display label,
    whether it's free (translate_engines.FREE_ENGINES), its selectable
    model keys (or None if the engine has no model picker), and whether a
    key/endpoint is configured for it (never the key value itself -- see
    services.settings_service.key_status). The Gemini label reflects the
    persisted "Gemini free tier" setting (translate_engines.engine_picker_label)."""
    from services import model_registry_service  # imports this module at load time
    key_status = settings_service.key_status(env_path)
    gemini_free_tier = settings_service.get("gemini_free_tier")
    engines = []
    for name in translate_engines.ENGINES:
        model_dict = ENGINE_MODEL_DICTS.get(name)
        if name in _NO_KEY_REQUIRED_ENGINES:
            key_configured = True
        else:
            key_configured = bool(key_status.get(name, False))
        models = list(model_dict.keys()) if model_dict is not None else None
        extras = model_registry_service.extra_models(name)
        chosen = translate_engines.override_models(name)
        if extras or chosen:
            # DeepSeek has no built-in picker: its built-in default plus the
            # extras and the models the user chose in Diagnostics.
            builtin = translate_engines.builtin_default_model(name)
            base = models if models is not None else ([builtin] if builtin else [])
            models = list(dict.fromkeys(base + extras + chosen))
        if name == "ollama":
            # Offered per run only; saved presets' tags are included so one
            # saved with a hosted tag outside the built-in list is still flagged.
            models = list(dict.fromkeys(
                (models or []) + list(translate_engines.OLLAMA_CLOUD_MODELS)
                + _saved_ollama_models()))
        cloud = [m for m in models or [] if name == "ollama" and translate_engines.is_ollama_cloud_model(m)]
        labels = {m: model_registry_service.extra_model_label(name, m) for m in extras}
        # Without this the picker would show a hosted tag as plain text,
        # indistinguishable from a local model.
        labels.update({m: f"{m} -- CLOUD: sends text off this PC" for m in cloud})
        engines.append({
            "name": name,
            "label": translate_engines.engine_picker_label(name, gemini_free_tier),
            "free": name in translate_engines.FREE_ENGINES,
            "models": models,
            "model_labels": labels,
            "cloud_models": cloud,
            "key_configured": key_configured,
        })
    return engines


def list_history(limit: int = 50, principal=None) -> list:
    """Thin wrapper over db.list_translate_history -- most recent first.
    A household user (auth B2) sees only their own rows."""
    return db.list_translate_history(
        limit=limit, visible_to=ownership_service.visible_to_filter(principal))


def resolve_api_key(engine_name: str, env_path: Optional[str] = None) -> Optional[str]:
    """The literal value to pass into translate_engines.get_engine, per
    engine:
      - ollama: a resolved key/URL if configured, else the literal "local"
        (it points at a locally-run server).
      - everything else: whatever services.settings_service.resolve_key
        finds, or None if nothing is configured.
    """
    if engine_name in translate_engines.KEYLESS_ENGINES:
        return settings_service.resolve_key(engine_name, env_path) or "local"
    return settings_service.resolve_key(engine_name, env_path)


CAP_STOP_MESSAGE = ("Stopped at chunk {first} of {total}: the next chunk would pass your "
                    "monthly cap. {finished}")
REQUEST_CHUNKS = 8   # chunks per request (standalone_translate's batch_size)


class _UsageMeter:
    """Wraps an engine for translate_engines.standalone_translate_partial, which
    is also its budget: refuse() is asked before each request and may stop the
    run before the one that would pass the monthly cap, and every request that
    is sent is logged on its own (success, failure, or abandoned at its
    deadline), so the month's spend that the next refuse() re-reads is current."""

    def __init__(self, engine, engine_name: str, operation: str = "translate_text"):
        self._engine = engine
        self._engine_name = engine_name
        self._operation = operation
        self._lock = threading.Lock()
        self._request = None

    @property
    def unwrapped_engine(self):
        return self._engine

    def __getattr__(self, name):
        return getattr(self._engine, name)

    def _capped(self) -> bool:
        return translate_run_service.engine_cap_applies(
            self._engine_name, bool(getattr(self._engine, "free_tier", False)))

    def refuse(self, batch: list, first_chunk: int, total_chunks: int) -> Optional[str]:
        """A plain stop message when sending `batch` could take this month's
        spend past the cap, else None. Also readies the next request's record."""
        monthly = translate_run_service.month_cap_usd() if self._capped() else 0
        if monthly:
            # This run's earlier requests are already in the usage log, so the
            # logged month total already counts them.
            projected = db.get_month_spend() + translate_engines.estimate_translation_cost(
                self._engine, batch)
            if projected > monthly:
                finished = (f"The {first_chunk} finished chunks are returned." if first_chunk
                            else "Nothing was translated.")
                return CAP_STOP_MESSAGE.format(first=first_chunk + 1, total=total_chunks,
                                               finished=finished)
        with self._lock:
            self._request = {"abandoned": False}
        return None

    def translate_batch(self, texts, context=None):
        with self._lock:
            request = self._request
            if request is None:
                request = self._request = {"abandoned": False}
        if isinstance(getattr(self._engine, "last_usage", None), dict):
            # Set only on success: a failed request must not log the last one's tokens.
            self._engine.last_usage = {"input_tokens": 0, "output_tokens": 0}
        try:
            return self._engine.translate_batch(texts, context)
        finally:
            with self._lock:
                already_logged = request["abandoned"]
                request["logged"] = True
            if not already_logged:
                usage = getattr(self._engine, "last_usage", None) or {}
                self._log(usage.get("input_tokens", 0) or 0, usage.get("output_tokens", 0) or 0,
                          usage.get("cache_read_tokens", 0) or 0,
                          usage.get("cache_write_tokens", 0) or 0)

    def abandoned(self, batch: list) -> None:
        """A request passed its deadline and was left running: log it at its
        estimate unless it finished and logged itself in the meantime."""
        with self._lock:
            request = self._request
            if request is not None and request.get("logged"):
                return
            if request is not None:
                request["abandoned"] = True
        self._log(*translate_engines.estimate_translation_tokens(batch))

    def _log(self, input_tokens: int, output_tokens: int, cache_read: int = 0,
             cache_write: int = 0) -> None:
        db.log_usage(None, self._engine_name, getattr(self._engine, "model", self._engine_name),
                     self._operation, input_tokens, output_tokens,
                     translate_engines.estimate_cost_for_engine(
                         self._engine, input_tokens, output_tokens, cache_read, cache_write),
                     cache_read_tokens=cache_read)


def _checked_request(text, engine_name, source_language, target_language, model, free_tier):
    """Validation shared by translate() and estimate(); returns use_free_tier."""
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))

    ok, message = translate_engines.standalone_direction_support(
        engine_name, source_language, target_language)
    if not ok:
        raise UnsupportedOperationError(message)
    # A free-form model string would otherwise reach the engine, priced at $0 or a guess.
    translate_run_service._require_offered_model(engine_name, model)
    return (engine_name == "gemini"
            and settings_service.resolve_gemini_free_tier(free_tier))


def translate(text: str, engine_name: str, source_language: str, target_language: str,
              model: Optional[str] = None, free_tier: Optional[bool] = None,
              env_path: Optional[str] = None, principal=None) -> dict:
    """Translates text with engine_name (a translate_engines.ENGINES key),
    resolving its key server-side (D2) rather than accepting one from the
    caller, and saves the result to history. Raises
    InvalidInputError for an unknown engine, UnsupportedOperationError if
    the engine/direction pair is refused (translate_engines.
    standalone_direction_support), and DependencyUnavailableError if the
    engine needs a key/endpoint that isn't configured yet. free_tier None
    means the saved Gemini free-tier setting. Ollama always uses the
    configured Ollama URL; a caller-supplied URL is never fetched (SSRF).

    There is no length limit; instead each request is priced first and the run
    stops before the one that would pass the monthly cap. A stopped run
    returns the finished text plus a "message" saying where and why (the key
    is absent from a run that finished)."""
    use_free_tier = _checked_request(
        text, engine_name, source_language, target_language, model, free_tier)

    api_key = resolve_api_key(engine_name, env_path)
    if api_key is None:
        raise MissingKeyError(engine_name)

    translate_run_service.refuse_when_cap_spent(engine_name, use_free_tier)
    engine = translate_engines.get_engine(
        engine_name, api_key, model, free_tier=use_free_tier,
        base_url=((settings_service.resolve_key("ollama_url") or None)
                  if engine_name == "ollama" else None))
    metered = _UsageMeter(engine, engine_name)

    try:
        translated_text, stopped = translate_engines.standalone_translate_partial(
            text, metered, source_language, target_language, REQUEST_CHUNKS, budget=metered)
    except translate_engines.UnsupportedDirectionError as exc:
        raise UnsupportedOperationError(str(exc)) from exc
    except translate_engines.OllamaUnavailableError as exc:
        raise DependencyUnavailableError(
            translate_engines.redact_secrets(exc.message), details={"reason": exc.reason}) from None

    if translated_text:
        db.save_translate_history(source_language, target_language, engine_name, text,
                                  translated_text,
                                  user_id=None if principal is None else principal.get("user_id"))
    return {"translated_text": translated_text, **({"message": stopped} if stopped else {})}


def estimate(text: str, engine_name: str, source_language: str, target_language: str,
             model: Optional[str] = None, free_tier: Optional[bool] = None) -> dict:
    """What translate() would cost, for the Translate page to show before the
    owner presses the button. Read-only: no key is needed and nothing is sent
    or logged. cap_left_usd is None when the engine isn't capped or no monthly
    cap is set."""
    use_free_tier = _checked_request(
        text, engine_name, source_language, target_language, model, free_tier)
    priced = types.SimpleNamespace(
        model=model or translate_engines.effective_default_model(engine_name) or "",
        free_tier=use_free_tier)
    chunks = translate_engines.chunk_standalone_text(text)
    requests = [chunks[i:i + REQUEST_CHUNKS] for i in range(0, len(chunks), REQUEST_CHUNKS)]
    estimated = sum(translate_engines.estimate_translation_cost(priced, r) for r in requests)
    cap_applies = translate_run_service.engine_cap_applies(engine_name, use_free_tier)
    monthly = translate_run_service.month_cap_usd() if cap_applies else 0
    cap_left = max(0.0, monthly - db.get_month_spend()) if monthly else None
    return {"engine": engine_name, "model": priced.model or None, "chunks": len(chunks),
            "requests": len(requests), "estimated_usd": estimated, "cap_applies": cap_applies,
            "monthly_cap_usd": float(monthly) if monthly else None, "cap_left_usd": cap_left,
            "fits_cap": cap_left is None or estimated <= cap_left}


def clear_history(confirm: bool = False) -> dict:
    """Deletes every row in the (global, not drama-scoped) translate_history
    table via db.clear_translate_history(). A deliberate two-step gate
    against an accidental permanent delete: confirm must be passed as True,
    or the call is refused with InvalidInputError rather than silently
    no-op'ing or silently proceeding."""
    if confirm is not True:
        raise InvalidInputError(
            "Clearing translate history requires confirm=true. "
            "This permanently deletes all saved translation history.")
    db.clear_translate_history()
    return {"cleared": True}


def sync_translation_status(drama_id: int) -> None:
    """Keeps "aligned"/"translated" true to the saved lines after a write that
    can fill the last blank or open a new one (a line added, an English
    blanked). Only those two statuses move: "dubbed" and "exported" were built
    from the lines as they were."""
    bulk_translate.mark_translated_if_complete(drama_id)
    drama = db.get_drama(drama_id)
    if drama and drama.get("status") == "translated" \
            and bulk_translate.untranslated_line_count(drama_id):
        db.set_status_if(drama_id, "translated", "aligned")


def save_synced(drama_id: int, lines) -> None:
    """Full-sync `lines`, then bring the title status in line with them."""
    db.save_lines(drama_id, lines)
    sync_translation_status(drama_id)

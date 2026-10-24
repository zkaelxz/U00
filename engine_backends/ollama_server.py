"""What the configured Ollama server has: whether it answers, and which models
are pulled on it (GET /api/tags). Split from local.py, which re-exports these
names, so the chat engine and the server probes each stay one file."""
import json

from lib import http, settings_schema

from .shared import PROVIDER_RESPONSE_MAX_BYTES


# {base_url: (checked_at, reachable)} -- Ollama is exempted from the
# API-key check entirely, so with nothing in its place, clicking
# Translate against a stopped local server would start a background job
# that only fails once translate_batch's own 300s request timeout expires.
# check_ollama_reachable() lets the UI disable that button BEFORE starting
# the job instead. Cached briefly per base_url so a UI that re-checks on
# nearly every interaction doesn't re-hit the health check every time.
_ollama_reachability_cache = {}
OLLAMA_REACHABILITY_CACHE_SECONDS = 5


def check_ollama_reachable(base_url: str = settings_schema.OLLAMA_URL_DEFAULT) -> bool:
    """Cheap health check (GET /api/tags, 2.5s timeout) -- true only if
    the server actually responds, not just that the URL is well-formed."""
    import time
    base_url = settings_schema.normalize_ollama_url(base_url)
    now = time.time()
    cached = _ollama_reachability_cache.get(base_url)
    if cached and now - cached[0] < OLLAMA_REACHABILITY_CACHE_SECONDS:
        return cached[1]
    try:
        # Only the status matters: truncate=True cuts the body instead of failing on a long model list.
        # guard=None: base_url is the Ollama address the user configured (loopback or LAN).
        # Redirects are followed like _ollama_chat_request does, so a proxy that
        # answers 308 doesn't make Live say Ollama is down while translation works.
        resp = http.get(f"{base_url}/api/tags", timeout=2.5, max_bytes=1, truncate=True, guard=None,
                        allow_redirects=True)
        reachable = 200 <= resp.status < 300
    except Exception:
        reachable = False
    _ollama_reachability_cache[base_url] = (now, reachable)
    return reachable


class _TagsUnreadable(ValueError):
    """The server answered /api/tags, but not with a model list."""


def _tag_entries(base_url: str, timeout: float = 5) -> list:
    """The model entries GET /api/tags lists. Raises _TagsUnreadable when the
    server answered without a usable list, and any other error when it did
    not answer (or answered with an HTTP error)."""
    base_url = settings_schema.normalize_ollama_url(base_url)
    resp = http.get(f"{base_url}/api/tags", timeout=timeout, max_bytes=PROVIDER_RESPONSE_MAX_BYTES,
                    guard=None, allow_redirects=True)
    if not 200 <= resp.status < 300:
        raise ValueError(f"HTTP {resp.status}")
    try:
        tags = json.loads(resp.body)
    except ValueError:
        raise _TagsUnreadable("not JSON") from None
    entries = tags.get("models") if isinstance(tags, dict) else None
    return [e for e in entries or [] if isinstance(e, dict)]


def _entry_names(entry: dict) -> list:
    return [n for n in (entry.get("name"), entry.get("model")) if isinstance(n, str)]


def _is_embedding_model(entry: dict) -> bool:
    """Embedding models (nomic-embed-text, mxbai-embed-large, bge-m3) can't
    chat, so they are not offered as translators. /api/tags has no capability
    field; the BERT families and the "embed" in their names mark them."""
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
    families = [details.get("family")] + list(details.get("families") or [])
    if any(isinstance(f, str) and "bert" in f.lower() for f in families):
        return True
    return any("embed" in n.lower() for n in _entry_names(entry))


# {base_url: (checked_at, names)}. Kept as long as the reachability answer it
# is fetched with, so the two never disagree about whether Ollama is up.
_pulled_models_cache = {}


def pulled_ollama_models(base_url: str) -> list:
    """The chat models pulled on the Ollama server, so the picker offers what
    is really there rather than only the built-in tags; [] when it doesn't
    answer. Only names that are safe as a model id are returned. The same
    request answers check_ollama_reachable for the next few seconds, so a
    picker that asks for both makes one request."""
    import time
    from .engine_registry import MODEL_ID_RE
    base_url = settings_schema.normalize_ollama_url(base_url)
    now = time.time()
    cached = _pulled_models_cache.get(base_url)
    if cached and now - cached[0] < OLLAMA_REACHABILITY_CACHE_SECONDS:
        return list(cached[1])
    names, reachable = [], True
    try:
        for entry in _tag_entries(base_url, timeout=2.5):
            if _is_embedding_model(entry):
                continue
            for name in _entry_names(entry):
                if MODEL_ID_RE.fullmatch(name) and ".." not in name and name not in names:
                    names.append(name)
    except _TagsUnreadable:
        pass
    except Exception:
        reachable = False
    _pulled_models_cache[base_url] = (now, names)
    _ollama_reachability_cache[base_url] = (now, reachable)
    return list(names)


def check_ollama_model_installed(base_url: str, model: str) -> None:
    """Raises OllamaUnavailableError (the same plain texts the chat call uses)
    when the server is down or `model` isn't pulled, so a live session fails
    at Start rather than on its first chunk."""
    from .local import OllamaUnavailableError, is_ollama_cloud_model  # local imports this module
    try:
        installed = {n for entry in _tag_entries(base_url) for n in _entry_names(entry)}
    except Exception:
        raise OllamaUnavailableError(
            "ollama_unreachable",
            "Ollama isn't running. Start it, or pick another translator in Settings.") from None
    if is_ollama_cloud_model(model):
        # The local list only shows a hosted model after its first use, so
        # absence from it says nothing about whether the tag works.
        return
    # Ollama resolves a bare "name" to "name:latest".
    wanted = model if ":" in model else f"{model}:latest"
    if wanted not in installed:
        raise OllamaUnavailableError(
            "ollama_model_missing",
            f"Ollama doesn't have the model {model}. Run \"ollama pull {model}\" first, "
            "or pick another model in Settings.")

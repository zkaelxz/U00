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
    details = entry.get("details")
    if not isinstance(details, dict):
        details = {}
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


def model_pulled(base_url: str, model: str) -> bool:
    """Whether `model` is pulled on the server, embedding models included (pulled_ollama_models
    leaves those out). False when the server does not answer."""
    wanted = model if ":" in model else f"{model}:latest"
    try:
        return any(wanted in _entry_names(e) or model in _entry_names(e)
                   for e in _tag_entries(base_url, timeout=2.5))
    except Exception:
        return False


# Only a backstop: a pull's progress lines are about 150 bytes each.
_PULL_MAX_BYTES = 64 * 1024 * 1024
# Ollama pauses its progress while it verifies a finished layer, so the read timeout is generous;
# a cancel does not wait for it because the reader runs in its own thread.
_PULL_READ_TIMEOUT_SECONDS = 300
PULL_TIMEOUT_SECONDS = 60 * 60
_PULL_POLL_SECONDS = 0.5


def pull_model(base_url: str, model: str, on_progress=None, cancelled=None) -> None:
    """Pulls `model` through the server's streamed /api/pull and returns when it is in.
    `on_progress(done_bytes, total_bytes)` is called as layers arrive (the total grows as Ollama
    names more layers); `cancelled()` is polled about twice a second, and a true answer closes
    the connection, which makes Ollama stop (the part already fetched is kept for the next try).
    Returns False when stopped by `cancelled()`, else True. Raises ValueError with the server's
    own reason (redacted) when it refuses; a server that is down raises the http error."""
    import queue
    import threading
    import time
    from diagnostics_report import redact_for_support
    base_url = settings_schema.normalize_ollama_url(base_url)
    # guard=None: the address is the one the PC owner set in Settings.
    timeout = (5, _PULL_READ_TIMEOUT_SECONDS)
    session = http.session(timeout=timeout, guard=None, max_bytes=_PULL_MAX_BYTES)
    lines: queue.Queue = queue.Queue()
    holder = {}

    def read():
        try:
            resp = session.post(f"{base_url}/api/pull", json={"model": model, "stream": True}, timeout=timeout)
            holder["resp"] = resp
            if not resp.ok:
                lines.put(("error", f"Ollama answered HTTP {resp.status_code}"))
                return
            for raw in resp.iter_lines():
                if raw:
                    lines.put(("line", raw))
            lines.put(("end", None))
        except Exception as exc:
            lines.put(("down", exc))

    threading.Thread(target=read, daemon=True, name="ollama-pull").start()
    layers = {}
    started = time.monotonic()
    try:
        while True:
            if cancelled is not None and cancelled():
                return False
            if time.monotonic() - started > PULL_TIMEOUT_SECONDS:
                raise ValueError("The pull took more than an hour and was stopped.")
            try:
                kind, value = lines.get(timeout=_PULL_POLL_SECONDS)
            except queue.Empty:
                continue
            if kind == "end":
                return True
            if kind == "error":
                raise ValueError(value)
            if kind == "down":
                raise value
            try:
                event = json.loads(value)
            except ValueError:
                continue
            if event.get("error"):
                # Ollama's pull errors quote blob paths and registry URLs.
                raise ValueError(redact_for_support(str(event["error"]))[:200])
            if event.get("digest") and event.get("total"):
                layers[event["digest"]] = (event.get("completed") or 0, event["total"])
                if on_progress is not None:
                    on_progress(sum(c for c, _ in layers.values()), sum(t for _, t in layers.values()))
    finally:
        resp = holder.get("resp")
        if resp is not None:
            resp.close()

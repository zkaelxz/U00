"""Free a local Ollama server's GPU memory before a GPU transcription loads.

Ollama keeps the last translation model loaded for its idle timeout (5 minutes
by default). That is a separate process the app's own GPU job limit cannot see,
so a Whisper / Qwen3 load that starts inside the window can run out of VRAM and
fall back to the CPU. Everything here is best effort: no failure, and no wait
longer than UNLOAD_WAIT_SECONDS, may ever hold up or fail a transcription.

Endpoints (https://github.com/ollama/ollama/blob/main/docs/api.md):
GET /api/ps lists loaded models; POST /api/generate with {"model", "keep_alive": 0}
unloads one.
"""
import re
import threading
import time
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "http://localhost:11434"
SETTING_KEY = "unload_ollama_before_transcribe"
SETTING_LABEL = "Free Ollama's GPU memory before transcribing"

# The whole unload-and-wait step never takes longer than this.
UNLOAD_WAIT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 3.0
# A job loads up to three models (Whisper, Qwen3-ASR, the aligner), each through
# a loader that calls this; one check per thread per window is enough.
RECHECK_SECONDS = 30.0

_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
# Only plain tag characters reach a notice: a model name comes from a server
# response and ends up in a stored job result.
_SAFE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}")
_MAX_TAGS_IN_NOTICE = 3

# Per thread, because each job runs on its own thread and its result is built
# on that same thread: a second job's check must not hide or steal this one's.
_state = threading.local()


def is_enabled() -> bool:
    import db
    try:
        return bool(db.get_app_setting(SETTING_KEY, True))
    except Exception:
        return True  # the default; a settings read error must not change behaviour


def set_enabled(enabled: bool):
    import db
    db.set_app_setting(SETTING_KEY, bool(enabled))


def _local_base_url():
    """The configured Ollama base URL, or None unless it is this machine:
    unload requests must never go to a remote Ollama host."""
    from services import settings_service
    base = (settings_service.resolve_key("ollama_url") or DEFAULT_BASE_URL).strip().rstrip("/")
    try:
        parts = urlsplit(base)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or (host or "").lower() not in _LOOPBACK_HOSTS:
        return None
    return base


def _warn(what: str, exc: Exception):
    try:
        import applog
        from translate_engines import redact_secrets
        applog.get_logger().warning(f"{what}: " + redact_secrets(f"{type(exc).__name__}: {exc}")[:200])
    except Exception:
        pass


def _loaded_models(base: str, timeout: float):
    """Names of the models Ollama has loaded, or None when it can't be read
    (not running, slow, or a reply that isn't the documented shape)."""
    import requests
    from engine_backends.shared import read_json_capped
    try:
        resp = requests.get(f"{base}/api/ps", timeout=timeout, stream=True)
        data = read_json_capped(resp, timeout)
        names = []
        for entry in data["models"]:
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str) and name:
                names.append(name)
        return names
    except Exception as exc:
        _warn("could not list Ollama's loaded models", exc)
        return None


def _unload(base: str, name: str, timeout: float):
    import requests
    try:
        requests.post(f"{base}/api/generate", json={"model": name, "keep_alive": 0},
                      timeout=timeout).close()
    except Exception as exc:
        _warn("could not ask Ollama to unload a model", exc)


def _notice(tags, setting_on: bool) -> str:
    safe = [t for t in tags if _SAFE_TAG.fullmatch(t)][:_MAX_TAGS_IN_NOTICE]
    named = f" ({', '.join(safe)})" if safe else ""
    stop = "; ".join(f"ollama stop {t}" for t in safe) or "ollama stop <model>"
    fix = f"Free it with: {stop}"
    if not setting_on:
        fix += f", or turn on Settings > {SETTING_LABEL}"
    return ("Ollama still has a model loaded" + named + ", which may make transcription run out of "
            "GPU memory and fall back to the CPU. " + fix + ".")


def _free_ollama_gpu_memory() -> None:
    base = _local_base_url()
    if base is None:
        return
    start = time.monotonic()
    remaining = lambda: UNLOAD_WAIT_SECONDS - (time.monotonic() - start)  # noqa: E731
    loaded = _loaded_models(base, min(REQUEST_TIMEOUT_SECONDS, remaining()))
    if not loaded:
        return
    setting_on = is_enabled()
    if setting_on:
        for name in loaded:
            if remaining() <= 0:
                break
            _unload(base, name, max(0.5, min(REQUEST_TIMEOUT_SECONDS, remaining())))
        while remaining() > 0:
            loaded = _loaded_models(base, max(0.5, min(REQUEST_TIMEOUT_SECONDS, remaining())))
            if not loaded:  # empty, or unreadable: nothing left to warn about
                return
            time.sleep(min(POLL_INTERVAL_SECONDS, max(0.0, remaining())))
    _state.notice = _notice(loaded, setting_on)


def prepare_gpu_for_transcription(use_gpu) -> None:
    """Called by each GPU model loader before it loads. Does nothing for a CPU
    run. Never raises."""
    if not use_gpu:
        return
    now = time.monotonic()
    if now - getattr(_state, "checked_at", float("-inf")) < RECHECK_SECONDS:
        return
    _state.checked_at = now
    _state.notice = None
    try:
        _free_ollama_gpu_memory()
    except Exception as exc:
        _warn("freeing Ollama's GPU memory failed", exc)


def take_notice_result() -> dict:
    """{"ollama_notice": text} for a job result when this thread's last check
    left a model loaded, else {}. Clears it, so it is reported once."""
    notice = getattr(_state, "notice", None)
    _state.notice = None
    return {"ollama_notice": notice} if notice else {}

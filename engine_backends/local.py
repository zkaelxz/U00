"""Local engine: Ollama."""

import re

from .prompts import build_batch_user_message, build_stable_system_text
from .shared import read_json_capped, request_translations_with_retry


# Ollama's own default context window can be as small as 2-4k tokens,
# and a prompt longer than it gets silently TRUNCATED FROM THE START --
# exactly where the system instructions/glossary/reference novel live --
# with no error at all. This floor is deliberately generous: asking for
# more context than needed costs some memory but never loses a prompt,
# while asking for too little does so silently.
OLLAMA_MIN_NUM_CTX = 16384


def estimate_ollama_num_ctx(system_text: str, numbered: str, floor: int = OLLAMA_MIN_NUM_CTX) -> int:
    """Rough token-count estimate for sizing num_ctx -- not precise (CJK
    and English tokenize very differently), so it deliberately errs
    generous (~1 token per 3 characters, then +20% headroom) rather than
    precise, since underestimating is what causes silent truncation."""
    total_chars = len(system_text) + len(numbered)
    estimated_tokens = int((total_chars / 3) * 1.2)
    return max(floor, estimated_tokens)


# A flat {"<id>": "<text>"} object, matching exactly what
# parse_id_keyed_json expects back -- passed as Ollama's `format` so
# structured output does the work of staying on-shape instead of hoping
# the model follows the prompt's instructions unprompted.
_OLLAMA_ID_KEYED_JSON_SCHEMA = {"type": "object", "additionalProperties": {"type": "string"}}


# Local Ollama models offered in the picker. gemma4:12b is the default: it fits
# a 12 GB GPU fully, while the 26b/31b tags offload to the CPU and run slower.
# A title or preset that saved another tag (an old Qwen one, say) keeps it and
# still runs if Ollama has it; it is just no longer offered here.
OLLAMA_DEFAULT_MODEL = "gemma4:12b"


OLLAMA_MODELS = {
    "gemma4:12b": "Gemma 4 12B -- recommended default, about 8 GB, fits fully on a 12 GB GPU; too big for 8 GB",
    "gemma4:26b": "Gemma 4 26B (MoE) -- about 16-19 GB, won't fit a 12 GB GPU; offloads to the CPU and runs slower",
    "gemma4:31b": "Gemma 4 31B -- about 19-20 GB, won't fit a 12 GB GPU; offloads to the CPU and runs much slower",
}


class OllamaUnavailableError(Exception):
    """Ollama can't serve the request for a reason the user can fix.
    `reason` is a stable machine id; the message never carries the Ollama
    URL, which can be a private address."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


# Local models can be slow, especially CPU-only or larger ones.
OLLAMA_CHAT_TIMEOUT = 300

# With "stream": False Ollama sends nothing until the whole reply is done,
# so this is also the wait for the first byte. Models too big for a 12 GB
# card split across GPU and CPU and can need far longer than 300 s for a
# long batch; the cap stays finite so a hung server still fails.
OLLAMA_SLOW_MODEL_CHAT_TIMEOUT = 900
_OLLAMA_SLOW_MODELS = frozenset({"gemma4:26b", "gemma4:31b"})


def ollama_chat_timeout(model: str) -> int:
    return OLLAMA_SLOW_MODEL_CHAT_TIMEOUT if model in _OLLAMA_SLOW_MODELS else OLLAMA_CHAT_TIMEOUT


# Thinking-capable models (Qwen3, Gemma 4) may put reasoning inline in
# `content` as <think>...</think> instead of the separate `thinking` field.
# Braces inside it would otherwise be picked up by the first-JSON-value scan.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)


def strip_ollama_thinking(content: str) -> str:
    content = _THINK_BLOCK_RE.sub("", content)
    # An unterminated block means the reply was cut off mid-reasoning: no answer.
    return _THINK_OPEN_RE.sub("", content).strip()


def _ollama_chat(base_url: str, payload: dict) -> dict:
    """POST /api/chat with the slow-local-model timeout; returns the JSON
    reply, read with the provider byte cap. A refused/unresolvable/unreachable
    server and a model that isn't pulled become OllamaUnavailableError;
    requests' own messages embed the URL, so none of that text is kept."""
    import requests
    timeout = ollama_chat_timeout(str(payload.get("model") or ""))
    try:
        resp = requests.post(f"{base_url}/api/chat", json=payload, stream=True,
                             timeout=timeout)
    except requests.ConnectionError:  # includes ConnectTimeout and DNS failures
        raise OllamaUnavailableError(
            "ollama_unreachable",
            "Ollama isn't running. Start it, or pick another translator in Settings.") from None
    except requests.ReadTimeout:
        raise OllamaUnavailableError(
            "ollama_timeout",
            "Ollama took too long to answer. Try a smaller model, or pick another translator in Settings.") from None
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        resp.close()
        if getattr(exc.response, "status_code", None) != 404:
            raise
        model = str(payload.get("model") or "")
        raise OllamaUnavailableError(
            "ollama_model_missing",
            f"Ollama doesn't have the model {model}. Run \"ollama pull {model}\" first, "
            "or pick another model in Settings.") from None
    try:
        return read_json_capped(resp, timeout)
    except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as exc:
        # The body is read after the headers now, so a stall or reset there
        # raises from the read, not from post(), and its text names the host.
        # requests reports a read timeout during iter_content as a
        # ConnectionError wrapping urllib3's ReadTimeoutError, not ReadTimeout.
        from urllib3.exceptions import ReadTimeoutError
        if exc.args and isinstance(exc.args[0], ReadTimeoutError):
            raise OllamaUnavailableError(
                "ollama_timeout",
                "Ollama took too long to answer. Try a smaller model, or pick another translator in Settings.") from None
        raise OllamaUnavailableError(
            "ollama_unreachable",
            "Ollama isn't running. Start it, or pick another translator in Settings.") from None


class OllamaEngine:
    """Fully local/offline translation via Ollama (https://ollama.com) --
    no API key, no internet needed once you've pulled a model. Quality
    depends heavily on which model you run locally; a capable general
    model (e.g. qwen3, llama3.1) handles Chinese->English reasonably,
    but won't match Claude/DeepSeek on tone/nuance. Good for cost-free
    bulk drafts you'll hand-polish, or for offline-only environments."""
    name = "ollama"
    supports_reference = True

    def __init__(self, api_key: str = None, model: str = OLLAMA_DEFAULT_MODEL,
                 base_url: str = "http://localhost:11434"):
        # api_key is unused (kept for a consistent engine constructor signature)
        self.model = model
        self.base_url = base_url.rstrip("/")

    def translate_batch(self, zh_lines, context: dict):
        system_text = build_stable_system_text(context)
        num_ctx_override = context.get("ollama_num_ctx_override")

        def call_model(numbered):
            user_text = build_batch_user_message(context, numbered)
            estimated = estimate_ollama_num_ctx(system_text, user_text)
            # The override can only raise the window, never lower it below
            # what's actually needed -- a manual value smaller than the
            # estimate would silently reintroduce the exact truncation bug
            # this exists to prevent, so the larger of the two always wins.
            num_ctx = max(estimated, num_ctx_override) if num_ctx_override else estimated
            if num_ctx_override and num_ctx_override < estimated:
                import applog
                applog.get_logger().warning(
                    f"Ollama num_ctx override ({num_ctx_override}) is smaller than the "
                    f"estimated prompt size ({estimated}) -- using {estimated} instead to "
                    "avoid silently truncating the prompt.")
            resp = _ollama_chat(self.base_url, {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_text},
                ],
                "stream": False,
                "format": _OLLAMA_ID_KEYED_JSON_SCHEMA,
                "options": {"num_ctx": num_ctx},
            })
            return strip_ollama_thinking(resp["message"]["content"])

        return request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"), engine_name="ollama",
                                                line_languages=context.get("line_languages"))


# {base_url: (checked_at, reachable)} -- Ollama is exempted from the
# API-key check entirely, so with nothing in its place, clicking
# Translate against a stopped local server would start a background job
# that only fails once translate_batch's own 300s request timeout expires.
# check_ollama_reachable() lets the UI disable that button BEFORE starting
# the job instead. Cached briefly per base_url so a UI that re-checks on
# nearly every interaction doesn't re-hit the health check every time.
_ollama_reachability_cache = {}
OLLAMA_REACHABILITY_CACHE_SECONDS = 5


def check_ollama_reachable(base_url: str = "http://localhost:11434") -> bool:
    """Cheap health check (GET /api/tags, 2.5s timeout) -- true only if
    the server actually responds, not just that the URL is well-formed."""
    import time
    import requests
    base_url = base_url.rstrip("/")
    now = time.time()
    cached = _ollama_reachability_cache.get(base_url)
    if cached and now - cached[0] < OLLAMA_REACHABILITY_CACHE_SECONDS:
        return cached[1]
    try:
        # stream=True so only the status is read; the model list isn't needed here.
        resp = requests.get(f"{base_url}/api/tags", timeout=2.5, stream=True)
        reachable = resp.ok
        resp.close()
    except Exception:
        reachable = False
    _ollama_reachability_cache[base_url] = (now, reachable)
    return reachable

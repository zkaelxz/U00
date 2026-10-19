"""Local engine: Ollama."""

import contextvars
import functools
import json
import re
import threading
import weakref

from lib import capped_body, http

from .prompts import build_batch_user_message, build_stable_system_text
from .shared import (
    PROVIDER_RESPONSE_MAX_BYTES,
    ContentModerationBlocked,
    TranslationCancelled,
    TruncatedReply,
    _is_rate_limit_error,
    read_json_capped,
    redact_secrets,
    request_translations_with_retry,
)
from .temperature import temperature_of
from .thinking import ollama_chat_with_think, thinking_choice
from .translation_guard import allowed_source_runs


# Ollama's own default context window can be as small as 2-4k tokens,
# and a prompt longer than it gets silently TRUNCATED FROM THE START --
# exactly where the system instructions/glossary/reference novel live --
# with no error at all. This floor is deliberately generous: asking for
# more context than needed costs some memory but never loses a prompt,
# while asking for too little does so silently.
OLLAMA_MIN_NUM_CTX = 16384

# The window is rounded up to one of these and never shrinks during a run:
# a different num_ctx can make Ollama reload the model, which costs a model
# load per request when every request is sized to the token.
OLLAMA_NUM_CTX_STEPS = (16384, 32768, 65536)
# Beyond this the KV cache of a 26-31B model no longer fits one 32 GB card.
# The Settings override may go higher; it is the owner's explicit choice.
OLLAMA_MAX_NUM_CTX = 65536
# Ollama's default is no limit at all, so a model stuck repeating itself
# generates until the request times out.
OLLAMA_MAX_NUM_PREDICT = 8192

_CJK_CHAR = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]")


def estimate_ollama_prompt_tokens(*texts: str) -> int:
    """Rough token count -- not precise, so it errs generous: Gemma/Qwen
    tokenizers spend about one token per CJK character but one per ~4
    characters of English, so the two are counted apart (1 and 1/3), then
    +20% headroom. Underestimating is what causes silent truncation."""
    text = "".join(texts)
    cjk = len(_CJK_CHAR.findall(text))
    return int((cjk + (len(text) - cjk) / 3) * 1.2)


def estimate_ollama_num_ctx(system_text: str, numbered: str, floor: int = OLLAMA_MIN_NUM_CTX) -> int:
    return max(floor, estimate_ollama_prompt_tokens(system_text, numbered))


def id_keyed_schema(ids) -> dict:
    """Ollama's `format` for one request: an object with exactly these ids as
    string fields, so structured output keeps a small model from dropping,
    renaming or inventing an id instead of hoping it follows the prompt."""
    keys = [str(i) for i in ids]
    return {"type": "object", "properties": {k: {"type": "string"} for k in keys},
            "required": keys, "additionalProperties": False}


# (base_url, model) pairs whose server refused a JSON schema in `format`
# (an Ollama too old for structured output); they get plain JSON mode.
_schema_refused = set()


# Ollama's wording when it can't use a schema in `format`; any other 400 that
# happens to say "format" (a date, an option) is not about structured output.
_SCHEMA_REFUSAL_RE = re.compile(r"schema|invalid format|format\b.{0,40}\b(?:not supported|unsupported)",
                                re.IGNORECASE | re.DOTALL)


def _refuses_schema(exc) -> bool:
    import requests
    if not isinstance(exc, requests.HTTPError) or getattr(exc.response, "status_code", None) != 400:
        return False
    return bool(_SCHEMA_REFUSAL_RE.search(getattr(exc, "body_text", None) or ""))


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


# Ollama's hosted models, reached through the local app once the owner has
# run `ollama signin`: same /api/chat on the local URL, but the tag ends in
# "cloud" and the subtitle text is processed on Ollama's servers. Kept out
# of the default and out of every fallback so a local-only setup never
# starts sending text off the PC without the owner picking one.
OLLAMA_CLOUD_MODELS = {
    "gemma4:31b-cloud": "Gemma 4 31B -- CLOUD: runs on Ollama's servers, sends your subtitle text off this PC; needs Ollama sign-in; free use is capped",
    "gemma4:cloud": "Gemma 4 -- CLOUD: runs on Ollama's servers, sends your subtitle text off this PC; needs Ollama sign-in; free use is capped",
}


def is_ollama_cloud_model(model) -> bool:
    return isinstance(model, str) and model.lower().endswith(("-cloud", ":cloud"))


# A member's own key sends cloud tags straight to Ollama's hosted API, so the
# member's runs spend their account, not the PC's signed-in one.
OLLAMA_CLOUD_URL = "https://ollama.com"
_NOT_CARRIED = object()
# Carried in place of a key a member saved that can no longer be decrypted.
# It has a space, so no real key (validated: no whitespace) equals it.
UNREADABLE_MEMBER_KEY = "saved key unreadable"
# Set only by carry_member_cloud_key, in a job's thread or child process. A
# new thread starts with an empty context, so none inherits it by accident.
_member_cloud_key_var = contextvars.ContextVar("ollama_member_cloud_key", default=_NOT_CARRIED)


def _run_with_member_cloud_key(key, target, *args, **kwargs):
    token = _member_cloud_key_var.set(key)
    try:
        return target(*args, **kwargs)
    finally:
        _member_cloud_key_var.reset(token)


def carry_member_cloud_key(target, key):
    """`target` running with `key` (None: the PC's own sign-in) as the
    member key; a partial of a top-level function, so it pickles."""
    return functools.partial(_run_with_member_cloud_key, key, target)


def member_cloud_key():
    """The member key a cloud tag is sent with here, or None for the PC's
    own sign-in: the key a job carried from its start, else, in an API
    request, the signed-in member's own."""
    key = _member_cloud_key_var.get()
    if key is _NOT_CARRIED:
        from services import member_key_service   # services sit above engine_backends
        key = member_key_service.acting_key()
    return key or None


def _cloud_key_for(model):
    """The member key a request for `model` sends, or None (a local model, or
    the PC's own sign-in). A saved key that can't be read stops the call:
    falling back to the PC would spend the owner's account."""
    if not is_ollama_cloud_model(model):
        return None
    key = member_cloud_key()
    if key == UNREADABLE_MEMBER_KEY:
        raise OllamaUnavailableError(
            "ollama_cloud_key_unreadable",
            "Your saved Ollama key can't be read any more. Paste it again in "
            "Settings > Your Ollama key, or remove it there.")
    return key


def direct_cloud_model(tag: str) -> str:
    """ollama.com names a model without the app's cloud suffix:
    "gemma4:31b-cloud" -> "gemma4:31b", "gemma4:cloud" -> "gemma4"."""
    for suffix in ("-cloud", ":cloud"):
        if tag.lower().endswith(suffix):
            return tag[:-len(suffix)]
    return tag


def chat_target(base_url: str, model: str):
    """(url, headers, model) for a /api/chat request made outside
    _ollama_chat: ollama.com with the member key for a cloud tag when one
    applies, else the configured Ollama with no headers."""
    key = _cloud_key_for(model)
    if key:
        return (f"{OLLAMA_CLOUD_URL}/api/chat", {"Authorization": f"Bearer {key}"},
                direct_cloud_model(model))
    return f"{base_url}/api/chat", None, model


def ollama_touches_local_gpu(engine_name: str, model) -> bool:
    """Whether a run on this engine uses this PC's GPU/memory: a local
    Ollama model does, a cloud tag does not."""
    return engine_name == "ollama" and not is_ollama_cloud_model(model)


def chain_touches_local_gpu(chain) -> bool:
    """`ollama_touches_local_gpu` for any step of an engine chain."""
    return any(ollama_touches_local_gpu(c["engine"], c["model"]) for c in chain)


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
# so this is also the wait for the first byte. Models of 20B parameters and
# up split across GPU and CPU on most cards and can need far longer than
# 300 s for a long batch; the cap stays finite so a hung server still fails.
OLLAMA_SLOW_MODEL_CHAT_TIMEOUT = 900
OLLAMA_SLOW_MODEL_MIN_BILLIONS = 20
# "26b", "32B", "30b-a3b" (MoE: the first number is the total size); not
# the "4" of "e4b" or "a3b", which are effective/active sizes.
_MODEL_BILLIONS_RE = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)b(?![a-z0-9])", re.IGNORECASE)

# Loading a model from disk happens inside the first request and can take
# minutes on a slow disk; it must not use up the time meant for generating.
OLLAMA_COLD_LOAD_SECONDS = 300
# Under Ollama's default 5-minute keep_alive: after this long without a reply
# the model may have been unloaded, so the next request may load it again.
OLLAMA_WARM_SECONDS = 240
# {(base_url, model, num_ctx): monotonic time until which it counts as loaded}
_warm_until = {}


def ollama_chat_timeout(model: str) -> int:
    """Generation time allowed for one request, from the parameter count in
    the tag; a tag without one is treated as a small model."""
    if is_ollama_cloud_model(model):
        return OLLAMA_CHAT_TIMEOUT
    sizes = [float(m) for m in _MODEL_BILLIONS_RE.findall(model or "")]
    if sizes and max(sizes) >= OLLAMA_SLOW_MODEL_MIN_BILLIONS:
        return OLLAMA_SLOW_MODEL_CHAT_TIMEOUT
    return OLLAMA_CHAT_TIMEOUT


def ollama_max_request_seconds(model: str) -> int:
    """The longest one request to this model may be allowed: generation
    plus a cold load. Deadlines around a request must be at least this."""
    cold = 0 if is_ollama_cloud_model(model) else OLLAMA_COLD_LOAD_SECONDS
    return ollama_chat_timeout(model) + cold


def _warm_key(base_url: str, payload: dict):
    return (base_url, str(payload.get("model") or ""),
            (payload.get("options") or {}).get("num_ctx"))


def _request_timeout(base_url: str, payload: dict) -> int:
    import time
    model = str(payload.get("model") or "")
    if _warm_until.get(_warm_key(base_url, payload), 0) > time.monotonic():
        return ollama_chat_timeout(model)
    return ollama_max_request_seconds(model)


def _mark_warm(base_url: str, payload: dict) -> None:
    import time
    _warm_until[_warm_key(base_url, payload)] = time.monotonic() + OLLAMA_WARM_SECONDS


# Thinking-capable models (Qwen3, Gemma 4) may put reasoning inline in
# `content` instead of the separate `thinking` field: <think>-style tags, or
# channel tokens (harmony's <|channel|>analysis ... <|channel|>final<|message|>,
# Gemma's <|channel>thought ... <channel|>). Braces inside it would otherwise
# be picked up as the answer by the JSON scan.
_THINK_BLOCK_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<(?:think|thinking|reasoning)>.*\Z", re.DOTALL | re.IGNORECASE)
_FINAL_CHANNEL_RE = re.compile(r"<\|channel\|>\s*final\s*<\|message\|>", re.IGNORECASE)
_CHANNEL_BLOCK_RE = re.compile(
    r"<\|channel\|?>\s*(?:thought|analysis|commentary)\b.*?(?:<\|?channel\|>|<\|end\|>|\Z)",
    re.DOTALL | re.IGNORECASE)
_SPECIAL_TOKEN_RE = re.compile(r"<\|[a-z_]+\|?>|<[a-z_]+\|>", re.IGNORECASE)


def strip_ollama_thinking(content: str) -> str:
    content = _THINK_BLOCK_RE.sub("", content)
    # An unterminated block means the reply was cut off mid-reasoning: no answer.
    content = _THINK_OPEN_RE.sub("", content)
    finals = list(_FINAL_CHANNEL_RE.finditer(content))
    if finals:
        content = content[finals[-1].end():]
    content = _CHANNEL_BLOCK_RE.sub("", content)
    return _SPECIAL_TOKEN_RE.sub("", content).strip()


class OllamaRunStopped(RuntimeError):
    """Raised instead of the error that made too many batches in a row fail;
    stops_run tells the translate run to stop rather than try every batch."""
    stops_run = True


class OllamaReplyError(RuntimeError):
    """Ollama answered, but the reply can't be used: cut off, built from a
    truncated prompt, or not the shape the caller asked for. The message is
    plain text for the owner."""


CONTEXT_TOO_SMALL = ("The model's context window was too small for this request: the start of "
                     "the prompt (its instructions) was cut off. Use smaller batches, or raise "
                     "the Ollama num_ctx in Settings, Offline and performance.")
REPLY_CUT_OFF = ("The model's reply was cut off before it finished. Try again with less text, "
                 "or pick a bigger model.")
# A prompt that fills the window to within this many tokens left no room for
# a reply, and is what Ollama's front truncation leaves behind.
OLLAMA_FULL_WINDOW_MARGIN = 256


def check_ollama_reply(data: dict, num_ctx: int, estimated_prompt: int) -> None:
    """Raises OllamaReplyError when the prompt was evidently truncated.
    Ollama counts only the prompt tokens it actually evaluated: a window
    that came back full, or a count far below an estimate that never fit,
    means the front of the prompt was dropped. A cached prefix also lowers
    the count, so a low count alone is not trusted while the estimate fits."""
    evaluated = data.get("prompt_eval_count") or 0
    if not evaluated:
        return
    if evaluated >= num_ctx - OLLAMA_FULL_WINDOW_MARGIN or (
            estimated_prompt > num_ctx and evaluated < estimated_prompt / 2):
        raise OllamaReplyError(CONTEXT_TOO_SMALL)


def ollama_usage(data: dict) -> dict:
    return {"input_tokens": data.get("prompt_eval_count") or 0,
            "output_tokens": data.get("eval_count") or 0}


def ollama_task_reply(chat, engine, messages: list, max_tokens: int, expect: str,
                      usage_cb=None, translation_of: str = None) -> str:
    """One non-translation request (call_llm_json's Ollama branch). chat is
    the _ollama_chat to use. expect: "json_object" (Ollama's JSON mode),
    "json_array" or "text" -- JSON mode can only produce an object, so an
    array or prose prompt gets no `format`. Thinking is switched off: these
    callers size max_tokens for the visible answer.

    A cut-off reply or a truncated prompt raises OllamaReplyError instead of
    being parsed. A "text" reply must be prose: empty or JSON raises, and
    with translation_of (the source line it renders) so does a reply that
    isn't a usable English line for it."""
    from .shared import is_apology_refusal
    from .translation_guard import unusable_reply
    estimated = estimate_ollama_prompt_tokens(*(m["content"] for m in messages))
    num_predict = max(1, min(int(max_tokens), OLLAMA_MAX_NUM_PREDICT))
    num_ctx = engine.num_ctx_for(estimated + num_predict)
    payload = {"model": engine.model, "messages": messages, "stream": False,
               "options": {"num_ctx": num_ctx, "num_predict": num_predict}}
    if expect == "json_object":
        payload["format"] = "json"
    data = ollama_chat_with_think(chat, engine.base_url, payload, False)
    if usage_cb:
        usage = ollama_usage(data)
        usage_cb(usage["input_tokens"], usage["output_tokens"])
    check_ollama_reply(data, num_ctx, estimated)
    if data.get("done_reason") == "length":
        raise OllamaReplyError(REPLY_CUT_OFF)
    text = strip_ollama_thinking(data["message"]["content"])
    if expect != "text":
        return text
    if not text:
        raise OllamaReplyError("The model returned an empty reply.")
    if text.startswith("{"):
        raise OllamaReplyError("The model answered in JSON instead of plain text.")
    reason = None
    if translation_of:
        reason = ("a refusal" if is_apology_refusal(text)
                  else unusable_reply(translation_of, text))
    if reason:
        raise OllamaReplyError(f"The model's reply wasn't a usable English line ({reason}).")
    return text


_ABORT_POLL_SECONDS = 0.25
# Set by a caller (the Live loop) that must be able to abandon a blocked
# Ollama request. Opt-in rather than reusing the translate run's cancel
# check, so other flows keep their plain requests.post call.
abort_check_var = contextvars.ContextVar("ollama_abort_check", default=None)


def _ollama_chat(base_url: str, payload: dict) -> dict:
    """POST /api/chat with the slow-local-model timeout; returns the JSON
    reply, read with the provider byte cap. A refused/unresolvable/unreachable
    server and a model that isn't pulled become OllamaUnavailableError;
    requests' own messages embed the URL, so none of that text is kept.

    When the caller set abort_check_var, the request can be abandoned: see
    _ollama_chat_abortable. A cloud tag goes to ollama.com instead when a
    member key applies (member_cloud_key)."""
    import requests
    key = _cloud_key_for(payload.get("model"))
    check = abort_check_var.get()
    if check is None:
        reply = _ollama_chat_request(requests.post, base_url, payload, cloud_key=key)
    else:
        reply = _ollama_chat_abortable(base_url, payload, check, cloud_key=key)
    _mark_warm(base_url, payload)
    return reply


def _ollama_chat_abortable(base_url: str, payload: dict, check, cloud_key: str = None) -> dict:
    """Runs the request on a helper thread and polls `check`; on a cancel it
    shuts the request's sockets down and raises TranslationCancelled. With
    "stream": False Ollama sends nothing until the reply is done (a model
    load can take minutes), and a blocked read has no other way to be woken.
    Closing the connection also tells Ollama to stop generating, which frees
    the GPU for whatever runs next."""
    # The tracking adapter is live_fetch's: it keeps every socket (even one
    # still in its TLS handshake or awaiting headers) reachable for shutdown.
    from live_fetch import _TrackingAdapter, _shutdown

    timeout = _request_timeout(base_url, payload)
    lock = threading.Lock()
    sockets = weakref.WeakSet()
    state = {"aborted": False}

    def track(sock):
        with lock:
            sockets.add(sock)
            if state["aborted"]:
                _shutdown(sock)

    def release(sock):
        with lock:
            sockets.discard(sock)
            sock.close()

    # guard=None: the address is the one the PC owner set in Settings.
    session = http.session(timeout=timeout,
                           guard=http.check_public if cloud_key else None,
                           # only a backstop: read_json_capped refuses at the exact cap
                           max_bytes=PROVIDER_RESPONSE_MAX_BYTES + capped_body.DEFAULT_CHUNK,
                           adapter=_TrackingAdapter(track, release))
    box = {}

    def work():
        try:
            box["value"] = _ollama_chat_request(session.post, base_url, payload, timeout,
                                                cloud_key=cloud_key)
        except BaseException as exc:
            box["error"] = exc

    worker = threading.Thread(target=work, daemon=True, name="ollama-chat")
    worker.start()
    try:
        while True:
            worker.join(_ABORT_POLL_SECONDS)
            if not worker.is_alive():
                break
            if check():
                with lock:
                    state["aborted"] = True
                    for sock in list(sockets):
                        _shutdown(sock)
                # Only a courtesy wait: the thread is a daemon. Kept well under
                # the 3 s a cancel is expected to take.
                worker.join(1.0)
                raise TranslationCancelled("cancelled")
    finally:
        session.close()
    if "error" in box:
        raise box["error"]
    return box["value"]


# Enough for Ollama's one-line JSON error.
OLLAMA_ERROR_BODY_MAX_BYTES = 2000


def _error_body_text(resp) -> str:
    """A small, redacted slice of an error response's body; "" if unreadable.
    read_capped closes the response."""
    from lib import capped_body
    try:
        raw = capped_body.read_capped(resp, OLLAMA_ERROR_BODY_MAX_BYTES, 5.0,
                                      lambda: ValueError("error body too large"))
        return redact_secrets(raw.decode("utf-8", "replace"))
    except Exception:
        resp.close()
        return ""


def _ollama_server_error(exc, status, body: str):
    """The HTTPError to raise for an Ollama error answer: Ollama's own
    explanation (e.g. CUDA out of memory, "model requires more system
    memory"), redacted and without the request URL that requests' own
    message carries. Still an HTTPError with the response, so the thinking
    retry and the backoff read the status as before."""
    import requests
    detail = body
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
            detail = parsed["error"]
    except ValueError:
        pass
    detail = scrub_error_text(detail)
    message = f"Ollama answered with an error (HTTP {status})" + (f": {detail}" if detail else ".")
    err = requests.HTTPError(message, response=exc.response, request=getattr(exc, "request", None))
    err.body_text = body
    return err


_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.IGNORECASE)
# Drive paths (C:\..., C:/...), UNC shares (\\host\share) and absolute POSIX
# paths: Ollama's errors name its model files, whose path holds the account name.
_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>:]*|\\\\[^\s\"'<>:]+"
                      r"|(?<![\w.:/\\])/[^\s\"'<>/:]+(?:/[^\s\"'<>/:]*)+")


def scrub_error_text(text: str) -> str:
    """Server error text fit to store and show: secrets redacted, URLs and
    filesystem paths removed, cut to 300 characters."""
    text = _URL_RE.sub("[url]", redact_secrets(text or ""))
    return _PATH_RE.sub("[path]", text).strip()[:300]


def _ollama_chat_request(post, base_url: str, payload: dict, timeout: int = None,
                         cloud_key: str = None) -> dict:
    import requests
    timeout = timeout or _request_timeout(base_url, payload)
    model = str(payload.get("model") or "")
    if cloud_key:
        url = f"{OLLAMA_CLOUD_URL}/api/chat"
        # No redirects: the key must reach ollama.com and nothing else.
        kwargs = {"headers": {"Authorization": f"Bearer {cloud_key}"}, "allow_redirects": False}
        payload = dict(payload, model=direct_cloud_model(model))
        unreachable = ("ollama_cloud_unreachable",
                       "Couldn't reach Ollama's servers. Check this PC's internet connection, "
                       "or pick a local model.")
        too_slow = ("ollama_timeout", "Ollama's servers took too long to answer. Try again later, "
                                      "or pick a local model.")
    else:
        url, kwargs = f"{base_url}/api/chat", {}
        unreachable = ("ollama_unreachable",
                       "Ollama isn't running. Start it, or pick another translator in Settings.")
        too_slow = ("ollama_timeout", "Ollama took too long to answer. Try a smaller model, "
                                      "or pick another translator in Settings.")
    try:
        resp = post(url, json=payload, stream=True, timeout=timeout, **kwargs)
    except requests.ConnectionError:  # includes ConnectTimeout and DNS failures
        raise OllamaUnavailableError(*unreachable) from None
    except requests.ReadTimeout:
        raise OllamaUnavailableError(*too_slow) from None
    try:
        resp.raise_for_status()
        if cloud_key and resp.status_code >= 300:
            # A redirect left unfollowed: not a reply.
            resp.close()
            raise OllamaUnavailableError(*unreachable)
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if is_ollama_cloud_model(model) and status in (429, 401, 403):
            # The body of a streamed response is unreadable once it is closed.
            detail = _error_body_text(resp).strip()[:200] if status == 429 else ""
            resp.close()
            if status == 429:
                raise OllamaCloudLimitError(detail, own_key=bool(cloud_key)) from None
            if cloud_key:
                raise OllamaUnavailableError(
                    "ollama_cloud_key_refused",
                    "Ollama refused your API key. Paste a new one under Settings > Your Ollama "
                    "key, or pick a local model.") from None
            raise OllamaUnavailableError(
                "ollama_cloud_signin",
                "Ollama cloud models need you to be signed in. Run \"ollama signin\", "
                "or pick a local model in Settings.") from None
        if status != 404:
            # Closing a streamed response discards its body, and the caller
            # needs the server's wording to tell "can't think" from other 400s.
            raise _ollama_server_error(exc, status, _error_body_text(resp)) from None
        resp.close()
        if cloud_key:
            raise OllamaUnavailableError(
                "ollama_model_missing",
                f"Ollama's servers don't offer the model {model}. Pick another model.") from None
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
            raise OllamaUnavailableError(*too_slow) from None
        raise OllamaUnavailableError(*unreachable) from None


class OllamaCloudLimitError(Exception):
    """Ollama's hosted service answered 429. `status_code` lets
    shared._is_rate_limit_error back off and retry; the message is what
    the owner reads if the retries run out."""

    status_code = 429

    def __init__(self, detail: str, own_key: bool = False):
        whose = ("Your Ollama account is rate-limited or over its free usage cap right now. "
                 if own_key else
                 "Ollama's cloud models are rate-limited or over the free usage cap right now. ")
        super().__init__(
            whose + "Wait and resume the run later, use a local model, or check your Ollama plan."
            + (f" Ollama said: {detail}" if detail else ""))


class OllamaEngine:
    """Fully local/offline translation via Ollama (https://ollama.com) --
    no API key, no internet needed once you've pulled a model. Quality
    depends heavily on which model you run locally; a capable general
    model (e.g. qwen3, llama3.1) handles Chinese->English reasonably,
    but won't match Claude/DeepSeek on tone/nuance. Good for cost-free
    bulk drafts you'll hand-polish, or for offline-only environments."""
    name = "ollama"
    supports_reference = True
    # A run stops after this many batches in a row fail: an out-of-memory or
    # "does not fit" error repeats on every batch, and each costs a timeout.
    consecutive_failure_limit = 3

    def __init__(self, api_key: str = None, model: str = OLLAMA_DEFAULT_MODEL,
                 base_url: str = "http://localhost:11434"):
        # api_key is unused (kept for a consistent engine constructor signature)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._headroom_checked = False
        self._num_ctx = None
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}
        # {line id: reason} for lines of the last batch left untranslated
        # because every reply for them was unusable.
        self.last_rejected = {}
        self._failures_in_a_row = 0

    def num_ctx_for(self, needed_tokens: int, override: int = None) -> int:
        """The context window for a request needing about needed_tokens: the
        owner's override when it is big enough, else the next coarse step;
        never smaller than this engine has already used, never past the
        ceiling (which an override can lift)."""
        if override and override >= needed_tokens:
            wanted = override
        else:
            wanted = next((s for s in OLLAMA_NUM_CTX_STEPS if s >= needed_tokens), needed_tokens)
        ceiling = max(OLLAMA_MAX_NUM_CTX, override or 0)
        self._num_ctx = min(max(wanted, self._num_ctx or 0), ceiling)
        return self._num_ctx

    def translate_batch(self, zh_lines, context: dict):
        try:
            out = self._translate_batch(zh_lines, context)
        except (TranslationCancelled, ContentModerationBlocked):
            raise
        except Exception as exc:
            if _is_rate_limit_error(exc):
                # call_with_backoff retries these and words its own final error.
                raise
            self._failures_in_a_row += 1
            if self._failures_in_a_row < self.consecutive_failure_limit:
                raise
            stop = OllamaRunStopped(f"Stopped: {self._failures_in_a_row} batches in a row failed "
                                    f"({scrub_error_text(str(exc))})")
            raise stop from exc
        self._failures_in_a_row = 0
        return out

    def _translate_batch(self, zh_lines, context: dict):
        if not self._headroom_checked:
            # Once per engine: later batches reuse the model Ollama has loaded by then.
            import memory_headroom
            memory_headroom.check_ollama(self.base_url, self.model)
            self._headroom_checked = True
        system_text = build_stable_system_text(context)
        num_ctx_override = context.get("ollama_num_ctx_override")
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

        def call_model(numbered, ids):
            user_text = build_batch_user_message(context, numbered)
            estimated = estimate_ollama_prompt_tokens(system_text, user_text)
            # The reply is about as long as the lines it translates.
            needed = estimated + estimate_ollama_prompt_tokens(numbered)
            # The override can only raise the window, never lower it below
            # what's actually needed -- a manual value smaller than the
            # estimate would silently reintroduce the exact truncation bug
            # this exists to prevent, so the larger of the two always wins.
            num_ctx = self.num_ctx_for(needed, num_ctx_override)
            if num_ctx_override and num_ctx_override < needed:
                import applog
                applog.get_logger().warning(
                    f"Ollama num_ctx override ({num_ctx_override}) is smaller than the "
                    f"estimated prompt size ({needed}) -- using {num_ctx} instead to "
                    "avoid silently truncating the prompt.")
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_text},
                ],
                "stream": False,
                "format": ("json" if (self.base_url, self.model) in _schema_refused
                           else id_keyed_schema(ids)),
                "options": {"num_ctx": num_ctx, "num_predict": OLLAMA_MAX_NUM_PREDICT},
            }
            temperature = temperature_of(context, "ollama")
            if temperature is not None:
                payload["options"]["temperature"] = temperature
            try:
                resp = send(payload)
            except Exception as exc:
                if payload["format"] == "json" or not _refuses_schema(exc):
                    raise
                _schema_refused.add((self.base_url, self.model))
                import applog
                applog.get_logger().warning(
                    f"Ollama refused a JSON schema for {self.model}; using plain JSON mode "
                    "for it until restart.")
                resp = send({**payload, "format": "json"})
            usage = ollama_usage(resp)
            for key, value in usage.items():
                self.last_usage[key] += value
            check_ollama_reply(resp, num_ctx, estimated)
            # A reply cut off by num_predict (done_reason "length") is still
            # returned: its complete pairs are kept and the rest asked again,
            # in smaller batches.
            text = strip_ollama_thinking(resp["message"]["content"])
            return TruncatedReply(text) if resp.get("done_reason") == "length" else text

        def send(payload):
            choice = thinking_choice(context)
            if choice is None:
                return _ollama_chat(self.base_url, payload)
            return ollama_chat_with_think(_ollama_chat, self.base_url, payload, choice)

        rejected = {}
        line_ids = context.get("line_ids") or []
        try:
            return request_translations_with_retry(
                zh_lines, context.get("speaker_labels"), call_model,
                line_ids=line_ids, engine_name="ollama",
                line_languages=context.get("line_languages"),
                sentence_groups=context.get("sentence_groups"), recover_replies=True,
                # The reply guard set to Off in Settings switches these off too.
                check_replies=context.get("check_replies") is not False,
                allowed_runs=allowed_source_runs(context.get("glossary_terms"),
                                                 context.get("style_guidelines") or ""),
                rejected=rejected)
        finally:
            # Merged, not replaced: an extra call for some of a batch's lines
            # (sentence mode, the reply guard) must not unflag the others.
            for line_id in line_ids:
                self.last_rejected.pop(line_id, None)
            self.last_rejected.update({line_ids[p]: reason for p, reason in rejected.items()
                                       if p < len(line_ids) and isinstance(line_ids[p], int)})


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
    base_url = base_url.rstrip("/")
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


def check_ollama_model_installed(base_url: str, model: str) -> None:
    """Raises OllamaUnavailableError (the same plain texts the chat call uses)
    when the server is down or `model` isn't pulled, so a live session fails
    at Start rather than on its first chunk."""
    base_url = base_url.rstrip("/")
    try:
        resp = http.get(f"{base_url}/api/tags", timeout=5, max_bytes=PROVIDER_RESPONSE_MAX_BYTES,
                        guard=None, allow_redirects=True)
        if not 200 <= resp.status < 300:
            raise ValueError(f"HTTP {resp.status}")
        tags = json.loads(resp.body)
    except Exception:
        raise OllamaUnavailableError(
            "ollama_unreachable",
            "Ollama isn't running. Start it, or pick another translator in Settings.") from None
    if is_ollama_cloud_model(model):
        # The local list only shows a hosted model after its first use, so
        # absence from it says nothing about whether the tag works.
        return
    installed = set()
    for entry in tags.get("models") or []:
        for key in ("name", "model"):
            name = entry.get(key) if isinstance(entry, dict) else None
            if isinstance(name, str):
                installed.add(name)
    # Ollama resolves a bare "name" to "name:latest".
    wanted = model if ":" in model else f"{model}:latest"
    if wanted not in installed:
        raise OllamaUnavailableError(
            "ollama_model_missing",
            f"Ollama doesn't have the model {model}. Run \"ollama pull {model}\" first, "
            "or pick another model in Settings.")

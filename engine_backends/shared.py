"""Helpers every engine uses: usage totals, retry/backoff, secret redaction,
id-keyed request and parsing, content-moderation detection."""

import contextvars
import json
import re
import time

from core import LANGUAGE_NAMES
from services import capped_body


def _empty_usage() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}


def _add_usage(total: dict, usage: dict):
    for k in _empty_usage():
        total[k] = total.get(k, 0) + (usage.get(k) or 0)


def claude_usage(usage) -> dict:
    """A Claude response's usage in this app's shape. Claude's own
    input_tokens EXCLUDES cached tokens, so the whole prompt is the sum of
    all three input fields."""
    if usage is None:
        return _empty_usage()
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return {"input_tokens": (getattr(usage, "input_tokens", 0) or 0) + read + write,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read_tokens": read, "cache_write_tokens": write}


def gemini_usage(usage_metadata) -> dict:
    """Gemini's usageMetadata in this app's shape. cachedContentTokenCount
    (implicit or explicit cache hits) is already part of promptTokenCount."""
    u = usage_metadata or {}
    return {"input_tokens": u.get("promptTokenCount", 0) or 0,
            "output_tokens": u.get("candidatesTokenCount", 0) or 0,
            "cache_read_tokens": u.get("cachedContentTokenCount", 0) or 0,
            "cache_write_tokens": 0}


def _is_rate_limit_error(e: Exception) -> bool:
    """Detects rate-limit responses across different SDK styles (Anthropic,
    OpenAI-compatible, raw requests) so backoff only kicks in for the
    specific error where waiting actually helps -- not for genuine
    failures like a bad API key or malformed request."""
    status = getattr(e, "status_code", None)
    resp = getattr(e, "response", None)
    if resp is not None:
        status = status or getattr(resp, "status_code", None)
    if status == 429:
        return True
    cls_name = type(e).__name__.lower()
    if "ratelimit" in cls_name or "rate_limit" in cls_name:
        return True
    # Text match only when no HTTP status was reported: a known non-429
    # status (or an unrelated number such as "4290 lines") isn't a rate limit.
    if status is None and re.search(r"(?<!\d)429(?!\d)", str(e)):
        return True
    return False


class TranslationCancelled(Exception):
    """A wait was cut short because the running job was cancelled."""


class FreeTierDailyLimitReached(RuntimeError):
    """The free-tier daily request limit is used up; waiting it out would
    take hours, so the run stops instead."""


# Set by translate_lines_with_engine for the duration of a run so the
# backoff/throttle waits below can notice a cancel without every engine
# call having to thread a callback through.
_cancel_check_var = contextvars.ContextVar("translate_cancel_check", default=None)
# Same idea for the retry-wait notice: Reflect passes retry inside
# call_llm_json's own call_with_backoff, below any hook the pipeline could
# pass explicitly.
_backoff_wait_var = contextvars.ContextVar("translate_backoff_wait", default=None)
_SLEEP_SLICE_SECONDS = 0.5
# Longest free-tier throttle wait (RPM/TPM windows are 60 s) worth sleeping
# through; anything longer is the daily limit.
_MAX_THROTTLE_WAIT_SECONDS = 120.0


def _cancellable_sleep(seconds: float):
    """time.sleep in short slices; raises TranslationCancelled as soon as
    the run's cancel check reports a cancel."""
    check = _cancel_check_var.get()
    if check is None:
        time.sleep(seconds)
        return
    remaining = seconds
    while remaining > 0:
        if check():
            raise TranslationCancelled("cancelled")
        step = min(_SLEEP_SLICE_SECONDS, remaining)
        time.sleep(step)
        remaining -= step
    if check():
        raise TranslationCancelled("cancelled")


def call_with_backoff(fn, max_retries: int = 5, base_delay: float = 2.0, max_delay: float = 60.0,
                      on_wait=None):
    """Runs fn() with retry logic:
    - Rate-limit errors get exponential backoff (2s, 4s, 8s, ... capped
      at max_delay) up to max_retries -- these are expected/recoverable,
      so it's worth waiting them out rather than giving up.
    - Other errors get exactly one quick retry (covers a transient
      network blip) before being raised -- so a genuinely broken
      request (bad key, malformed input) fails fast instead of
      retrying pointlessly for a minute.

    on_wait: optional callable (delay_seconds, next_attempt, max_retries)
    invoked just before each retry sleep, so a caller can show a long wait
    as "waiting to retry" instead of a hang. It gets numbers only, never the
    exception, so nothing from an error body can reach a job message.
    """
    last_exception = None
    non_rate_limit_retried = False
    on_wait = on_wait or _backoff_wait_var.get()
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_exception = e
            if isinstance(e, (TranslationCancelled, FreeTierDailyLimitReached)):
                raise
            if getattr(e, "_fallback_chain_exhausted", False) and _is_rate_limit_error(e):
                # FallbackEngine already retried and tried every engine.
                raise
            if _is_rate_limit_error(e):
                delay = min(base_delay * (2 ** attempt), max_delay)
                if on_wait:
                    on_wait(delay, attempt + 2, max_retries)
                _cancellable_sleep(delay)
                continue
            elif not non_rate_limit_retried:
                non_rate_limit_retried = True
                if on_wait:
                    on_wait(1, attempt + 2, max_retries)
                _cancellable_sleep(1)
                continue
            else:
                raise
    raise last_exception


# Far above any real non-streamed reply (a whole batch's output tokens are a
# few hundred KB of JSON), yet a broken or hostile endpoint can't fill memory.
PROVIDER_RESPONSE_MAX_BYTES = 16 * 1024 * 1024


class ProviderResponseTooLarge(RuntimeError):
    """A provider's reply was over its byte cap or took too long to read."""


def read_json_capped(resp, deadline_seconds: float, cap_bytes: int = PROVIDER_RESPONSE_MAX_BYTES,
                     make_error=None):
    """The JSON body of a `stream=True` requests response, read through
    services.capped_body. A non-2xx status raises requests.HTTPError, as
    raise_for_status does, without reading the body."""
    if not resp.ok:
        resp.close()
        resp.raise_for_status()
    make_error = make_error or (lambda: ProviderResponseTooLarge(
        "The provider's reply was too large or too slow to read."))
    return json.loads(capped_body.read_capped(resp, cap_bytes, deadline_seconds, make_error))


# Matches a raw API key/token sitting in an error string -- a query
# param ("...&key=AIzaSy..."), an Authorization header dump, or a
# provider key by its own prefix (sk-... for Claude/OpenAI-compatible,
# AIza... for Google). Belt-and-suspenders alongside sending keys as
# headers rather than URL params: a raise_for_status() failure's message
# includes the request URL, and that message is what gets stored on
# dramas.last_translate_errors and shown in the UI.
_SECRET_PATTERNS = [
    re.compile(r'([?&]key=)[^&\s"\']+', re.IGNORECASE),
    re.compile(r'(authorization["\']?\s*[:=]\s*["\']?(?:Bearer\s+)?)[A-Za-z0-9_\-\.]{10,}',
               re.IGNORECASE),
    re.compile(r'\bsk-[A-Za-z0-9_-]{10,}\b'),
    re.compile(r'\bAIza[A-Za-z0-9_-]{10,}\b'),
    # Hugging Face user access tokens: hf_ + 34 letters/digits today. The
    # 20-char floor keeps ordinary identifiers like "hf_model" untouched.
    re.compile(r'\bhf_[A-Za-z0-9]{20,}\b'),
    # Groq keys: gsk_ + ~52 letters/digits.
    re.compile(r'\bgsk_[A-Za-z0-9]{20,}\b'),
    # Notion integration secrets (roadmap 112): ntn_ (current) or secret_
    # (older) + 40+ letters/digits. Same floor idea as hf_ above, so words like
    # "secret_key" or "ntn_status" are left alone.
    re.compile(r'\b(?:ntn|secret)_[A-Za-z0-9]{20,}\b'),
    # DeepL keys: a UUID, with ":fx" on Free-plan keys. A bare UUID is
    # only redacted with the ":fx" suffix or after "DeepL-Auth-Key", so
    # the app's own UUID ids stay readable in logs.
    re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:fx\b',
               re.IGNORECASE),
    re.compile(r'(DeepL-Auth-Key\s+)[A-Za-z0-9:\-]{10,}', re.IGNORECASE),
    # Google OAuth client secrets (sign-in, step 134): GOCSPX- + ~28 chars.
    re.compile(r'\bGOCSPX-[A-Za-z0-9_-]{10,}'),
    # GitHub tokens: ghp_/gho_/ghu_/ghs_/ghr_ and fine-grained github_pat_.
    re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
    # Discord webhook URLs: the id/token path is the secret.
    re.compile(r'(discord(?:app)?\.com/api/(?:v\d+/)?webhooks/)[^\s"\'<>]+', re.IGNORECASE),
]


# scheme://user:pass@host or scheme://token@host: the userinfo is the secret,
# host and path stay readable. Greedy so a raw "@" inside the password is
# still covered.
_URL_USERINFO_PATTERN = re.compile(r'(\b[a-z][a-z0-9+.-]*://)[^\s/?#"\'<>]+@', re.IGNORECASE)


def redact_secrets(text: str) -> str:
    """Strips anything that looks like an API key or bearer token out of
    an error string before it's shown in the UI, stored on the drama, or
    written to the log file."""
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "[REDACTED]" if m.groups() else "[REDACTED]", text)
    # After the token patterns: userinfo they already replaced stays as is.
    return _URL_USERINFO_PATTERN.sub(
        lambda m: m.group(0) if "[REDACTED]" in m.group(0) else m.group(1) + "***@", text)


_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)


def safe_url(url) -> str:
    """scheme + host + path only: no query, fragment or userinfo (a source
    URL can carry a signed token). Anything unparsable gives ""."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(str(url or "").strip())
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return ""
    if not parts.scheme or not host:
        return ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


def strip_url_queries(text):
    """Reduces every absolute URL inside `text` to scheme+host+path
    (redact_secrets leaves query strings and fragments alone)."""
    if not text:
        return text
    return _URL_IN_TEXT.sub(lambda m: safe_url(m.group(0)) or "[url]", str(text))


def redact_for_storage(text):
    """redact_secrets plus URL query stripping, for text written to a
    database that ends up in backups."""
    if not text:
        return text
    return strip_url_queries(redact_secrets(str(text)))


def parse_json_array(text: str, fallback_count: int):
    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        arr = json.loads(text)
        if isinstance(arr, list):
            return arr
    except json.JSONDecodeError:
        pass
    # fallback: try to split numbered lines
    lines = [l.split(". ", 1)[-1].strip() for l in text.splitlines() if l.strip()]
    return lines[:fallback_count] if lines else [""] * fallback_count


def language_name(lang, default: str = None) -> str:
    """Display name of a line language code; LANGUAGE_NAMES has no English
    because a title's own language never is."""
    return LANGUAGE_NAMES.get(lang, "English" if lang == "en" else default or lang)


def spoken_language_tag(lang) -> str:
    """The prompt prefix for a line spoken in a language other than the
    title's ("" for a line in the title's language, which is untagged so
    a single-language title's prompt is unchanged)."""
    if not lang:
        return ""
    return f"(spoken in {language_name(lang)}) "


def tagged_line_languages(lines, title_language: str):
    """context["line_languages"] for these lines: each line's lang where it
    differs from the title's, None elsewhere; None overall when no line
    differs so a single-language title sends the prompt it always did."""
    langs = [getattr(ln, "lang", None) for ln in lines]
    langs = [lang if lang and lang != title_language else None for lang in langs]
    return langs if any(langs) else None


def tagged_source_texts(lines, title_language: str) -> list:
    """Each line's source text as the model should read it: prefixed with
    the spoken-language tag where that differs from the title's."""
    langs = tagged_line_languages(lines, title_language) or [None] * len(lines)
    return [spoken_language_tag(lang) + ln.zh for lang, ln in zip(langs, lines)]


def is_english_line(line) -> bool:
    """Already English, so translating it would only spend a model call."""
    return getattr(line, "lang", None) == "en"


def build_numbered_lines(ids: list, zh_lines: list, speaker_names: list = None,
                         languages: list = None) -> str:
    """
    Builds the numbered-line block shown to the model, e.g.:
        1. [Xiaoling] 你好
        2. 那天下着雨。
    speaker_names (parallel to zh_lines, None entries allowed) prefixes
    only the lines a name is actually known for -- a line with no known
    speaker (narration, an unlabeled line) is left unprefixed rather
    than showing a placeholder like "[Unknown]", which would just be
    noise the model has to ignore.
    languages (parallel, None entries allowed) tags only lines spoken in a
    language other than the title's, e.g. "3. (spoken in Korean) 안녕".
    """
    out = []
    for idx, (i, zh) in enumerate(zip(ids, zh_lines)):
        name = speaker_names[idx] if speaker_names else None
        prefix = f"[{name}] " if name else ""
        tag = spoken_language_tag(languages[idx]) if languages else ""
        out.append(f"{i}. {prefix}{tag}{zh}")
    return "\n".join(out)


def extract_first_json_value(text: str):
    """Finds and parses the first valid JSON object/array anywhere in
    text, tolerating surrounding prose ("Here you go:\n{...}\nHope that
    helps!") -- small local models wrap their JSON in commentary like
    this often. Uses the real JSON decoder to find the end of the
    value (via raw_decode), rather than a regex guessing at matching
    brackets, so a brace/bracket character inside a string value can't
    make it stop early or grab too much. Returns None if nothing in
    the text parses as JSON."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                value, _ = decoder.raw_decode(text, i)
                return value
            except json.JSONDecodeError:
                continue
    return None


def parse_id_keyed_json(text: str, expected_ids: list) -> dict:
    """
    Parses a response expected to be a JSON object mapping each line's
    id (as a string) to its translation, e.g. {"1": "Hello.", "2": "Hi."}.
    Returns {id_str: text} for whichever of expected_ids actually came
    back with an actual string translation -- a missing id (or one
    whose value isn't a string, e.g. null or a nested list) just isn't
    a key here, it's the caller's job (request_translations_with_retry)
    to decide what to do about that, not this function's.

    A plain JSON array (a model ignoring the object-shape instruction) is
    malformed and returns {}: an array can be short, long or reordered,
    so matching it to ids by position could put a translation on the
    wrong line. The retry path re-requests those ids instead.
    """
    stripped = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    expected_str = {str(i) for i in expected_ids}
    data = extract_first_json_value(stripped)
    if data is None:
        return {}
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items()
                if str(k) in expected_str and isinstance(v, str)}
    return {}


def _id_keyed_batch_request(ids: list, build_batch_text, call_model_fn, max_retries: int = 1,
                            engine_name: str = None) -> dict:
    """The actual id-keyed request/parse/retry-missing loop shared by
    request_translations_with_retry below (every engine's own
    translate_batch) and reflect_translate_batch (three passes,
    each with its own prompt shape). build_batch_text(batch_ids) returns
    the prompt-ready text for just those ids -- a retry only re-sends
    whichever ids came back missing, not the whole batch. Returns
    {str(id): value} for whichever ids actually came back with a usable
    value; a still-missing id after max_retries just isn't a key here,
    same contract parse_id_keyed_json already documents.

    engine_name: opts into the best-effort soft-refusal text
    heuristic (see _detect_soft_refusal_text) -- deliberately not passed
    by reflect_translate_batch's own three calls, since refusal detection is scoped
    to the plain translate_batch path each engine's own translate_batch
    already raises ContentModerationBlocked from directly for a real
    structural signal; left None there keeps Reflect mode's behavior
    exactly as it was."""
    remaining_ids = list(ids)
    result_map = {}
    for _attempt in range(max_retries + 1):
        if not remaining_ids:
            break
        text = call_model_fn(build_batch_text(remaining_ids))
        # By id, never position: a short, padded or reordered reply would
        # otherwise shift translations onto the wrong lines.
        parsed = parse_id_keyed_json(text, remaining_ids)
        if engine_name and text.strip() and not parsed:
            # A real structural refusal signal (stop_reason/refusal) is
            # already checked -- and raises directly -- inside each
            # engine's own translate_batch, before the text ever reaches
            # here. Reaching here with non-empty text that parsed to
            # nothing means no such signal was available, so this is the
            # lower-confidence, best-effort fallback.
            soft_reason = _detect_soft_refusal_text(text)
            if soft_reason:
                raise ContentModerationBlocked(engine_name, soft_reason)
        result_map.update(parsed)
        remaining_ids = [i for i in ids if str(i) not in result_map]
    return result_map


def request_translations_with_retry(zh_lines: list, speaker_names, call_model_fn, max_retries: int = 1,
                                     line_ids=None, engine_name: str = None, line_languages=None):
    """
    The shared id-keyed request/parse/retry-missing logic behind every
    LLM translation engine's own translate_batch (Claude/DeepSeek/
    Gemini/Ollama) -- built once here instead of once per engine, same
    reasoning as call_llm_json's own consolidation elsewhere in this file.

    call_model_fn(numbered_text: str) -> raw response text from the
    model; each engine supplies its own closure that does its own API
    call (and updates its own usage tracking) with the given numbered
    lines substituted into its own instructions.

    This is what actually fixes the real bug this replaces: the old
    code trusted a plain JSON array's POSITION to mean the same thing as
    the input batch's position, so a response one line short (a common
    LLM failure: merging two lines into one, or silently dropping a
    line) silently shifted every translation after the gap onto the
    wrong line. Requesting an id-keyed object and looking up by id
    instead of position means a missing/extra/reordered response entry
    can only ever affect ITS OWN line, never any other one -- and this
    retries the specific lines that came back missing (once) before
    giving up and leaving only THOSE blank, rather than treating one
    incomplete response as a reason to redo (or lose) the whole batch.
    """
    ids = list(range(1, len(zh_lines) + 1))
    # The lines' own permanent ids when every line has one and
    # they're unique -- the same id a line keeps through merges and
    # re-saves. Otherwise (unsaved lines, non-translation callers) 1..n.
    if (line_ids is not None and len(line_ids) == len(zh_lines)
            and all(isinstance(i, int) for i in line_ids) and len(set(line_ids)) == len(line_ids)):
        ids = list(line_ids)
    pos = {i: p for p, i in enumerate(ids)}

    def build_batch_text(batch_ids):
        batch_lines = [zh_lines[pos[i]] for i in batch_ids]
        batch_names = ([speaker_names[pos[i]] for i in batch_ids] if speaker_names else None)
        batch_langs = ([line_languages[pos[i]] for i in batch_ids]
                       if line_languages and len(line_languages) == len(zh_lines) else None)
        return build_numbered_lines(batch_ids, batch_lines, batch_names, batch_langs)

    result_map = _id_keyed_batch_request(ids, build_batch_text, call_model_fn, max_retries,
                                         engine_name=engine_name)
    # Back to input order by id; a still-missing id is "" (untranslated),
    # never filled from a neighbour's slot.
    return [result_map.get(str(i), "") for i in ids]


# ---------------------------------------------------------------------------
# Content-moderation refusal detection
# ---------------------------------------------------------------------------

class ContentModerationBlocked(Exception):
    """Raised by an engine's own translate_batch when the provider's own
    safety/content-moderation system blocked the request -- distinct from
    any other failure (a network error, a malformed response) so the
    caller can flag the affected line(s) accurately instead of showing a
    raw/cryptic error or silently leaving them blank. engine: the
    provider's short name (e.g. "gemini"). reason: whatever real reason
    string the provider itself gave (a blockReason/finishReason/refusal
    field) -- never a guess."""

    def __init__(self, engine: str, reason: str):
        self.engine = engine
        self.reason = reason
        super().__init__(f"{engine} blocked this request: {reason}")


# Best-effort, lower-confidence fallback for the "soft refusal" case
# Only reached when a batch's raw response text is
# non-empty but zero ids parsed out of it, AND no real structural signal
# (stop_reason/refusal -- checked inside each engine's own translate_batch,
# which raises ContentModerationBlocked directly when one exists) was
# available. A short, deliberately narrow list of clear refusal-shaped
# openers -- this can both false-positive (a legitimate translation that
# happens to start this way) and false-negative (a refusal phrased some
# other way), so it's a secondary signal, not the primary detection path.
_SOFT_REFUSAL_OPENERS = (
    "i can't", "i cannot", "i won't", "i will not",
    "i'm not able to", "i am not able to", "i'm unable to", "i am unable to",
    "sorry, i can't", "sorry, i cannot",
    "i'm sorry, but i can't", "i'm sorry, but i cannot",
    "i apologize, but i can't", "i apologize, but i cannot",
)


def _detect_soft_refusal_text(text: str):
    """None, or the matched opener's own surrounding text (truncated) as
    the best-effort "reason" -- see _SOFT_REFUSAL_OPENERS above."""
    stripped = text.strip()
    lowered = stripped.lower()
    for opener in _SOFT_REFUSAL_OPENERS:
        if lowered.startswith(opener):
            return stripped[:200]
    return None


# Per-request timeout (seconds) for the Anthropic/OpenAI SDK clients, so a
# hung server can't leave a job stuck at "running". A non-streaming reply
# sends nothing until it is complete, and a 4000-token batch from a slow
# model can pass the cloud REST paths' 120 s, so this uses the slow-path
# bound the Ollama REST call uses (300 s) rather than retrying (and
# re-billing) a reply that was still coming.
SDK_REQUEST_TIMEOUT = 300

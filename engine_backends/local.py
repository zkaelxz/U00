"""Local engines: NLLB-200 and Ollama."""

from .prompts import build_batch_user_message, build_stable_system_text
from .shared import read_json_capped, request_translations_with_retry


# ---------------------------------------------------------------------------
# Local NLLB-200 -- genuinely free, fully offline neural MT, no API key
# ---------------------------------------------------------------------------

# NLLB-200's own language codes for the three source languages this app
# supports, plus English (Step 26b: needed as a target for zh/ja/ko ->
# English, the app's existing default, and as a source for the standalone
# tool's new English -> zh/ja/ko direction). zh always maps to Simplified
# here (NLLB has a separate zho_Hant code for Traditional) -- see
# NLLBEngine's docstring for why that's a real, currently-unaddressed
# limitation rather than an oversight.
_NLLB_LANG_CODES = {"zh": "zho_Hans", "ja": "jpn_Jpan", "ko": "kor_Hang", "en": "eng_Latn"}

NLLB_MODELS = {
    "facebook/nllb-200-distilled-600M": "600M -- fastest, lightest download (~2.4GB), practical on CPU",
    "facebook/nllb-200-distilled-1.3B": "1.3B -- better quality, slower, heavier download (~5.2GB)",
}

# Keyed by (model_name, source_language, target_language) -- NLLB bakes
# both src_lang and tgt_lang into the pipeline object itself, so a drama
# that mixes source languages across runs (or Step 26b's standalone tool,
# which can ask for either direction) needs a separate pipeline per
# language pair, same shape as Whisper's own _whisper_model_cache in
# core.py.
_nllb_pipeline_cache = {}


class NLLBEngine:
    """Fully local, offline neural machine translation via Meta's NLLB-200
    -- no API key, no network once the model's downloaded once, no
    per-token cost. This is a REAL translation engine, not a placeholder
    like a stub: it actually produces usable (if rougher) English,
    just with meaningfully lower quality than Claude/DeepSeek/Gemini on
    tone, idiom, and character-voice consistency, since it's pure
    sequence-to-sequence MT with no instruction-following ability at all
    -- not an LLM. Good for a genuinely
    free bulk draft, or for fully offline/no-budget use; expect to
    hand-polish idiom-heavy or emotionally nuanced lines afterward.

    Known limitation: chinese_script isn't threaded through here yet --
    zh always uses NLLB's Simplified code (zho_Hans). NLLB does have a
    separate zho_Hant code for Traditional, so a Traditional-script drama
    translated through this engine is feeding NLLB text in a script it
    isn't being told to expect, which will cost some accuracy. Worth
    fixing if this engine sees real use on Traditional-script content;
    not done here since it needs the same context-threading this file's
    source_language fix just added, for a script that isn't the default.

    Requires: `pip install transformers sentencepiece torch` (already a
    dependency of several other optional features in this app). The
    model downloads from Hugging Face on first use and is cached on disk
    afterward, the same as a Whisper model -- no API key involved at any
    point, this only ever runs locally.
    """
    name = "nllb"
    supports_reference = False

    def __init__(self, api_key: str = None, model: str = "facebook/nllb-200-distilled-600M"):
        # api_key is unused (kept for get_engine's consistent constructor
        # signature across engines -- NLLB needs no key at all).
        self.model_name = model

    def _get_pipeline(self, source_language: str, target_language: str = "en"):
        cache_key = (self.model_name, source_language, target_language)
        # One .get(): release_gpu_models() may clear the cache at any moment.
        pipe = _nllb_pipeline_cache.get(cache_key)
        if pipe is None:
            from transformers import pipeline
            src_lang = _NLLB_LANG_CODES.get(source_language, "zho_Hans")
            tgt_lang = _NLLB_LANG_CODES.get(target_language, "eng_Latn")
            pipe = pipeline(
                "translation", model=self.model_name, src_lang=src_lang, tgt_lang=tgt_lang)
            _nllb_pipeline_cache[cache_key] = pipe
        return pipe

    def translate_batch(self, zh_lines, context: dict):
        pipe = self._get_pipeline(context.get("source_language", "zh"),
                                  context.get("target_language", "en"))
        results = pipe(list(zh_lines))
        return [r["translation_text"] for r in results]


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
# _parse_id_keyed_json expects back -- passed as Ollama's `format` so
# structured output does the work of staying on-shape instead of hoping
# the model follows the prompt's instructions unprompted.
_OLLAMA_ID_KEYED_JSON_SCHEMA = {"type": "object", "additionalProperties": {"type": "string"}}


# Local Ollama models offered in the picker. qwen3:8b is the default: it
# beat qwen2.5:7b on translation benchmarks at the same size (see the
# roadmap's Step 5 / model registry). 14B is opt-in -- its quantized weights
# don't fit cleanly alongside everything else in 8 GB of VRAM, so Ollama
# offloads part of it to the CPU and it runs much slower there.
OLLAMA_DEFAULT_MODEL = "qwen3:8b"


OLLAMA_MODELS = {
    "qwen3:8b": "Qwen3 8B -- recommended default, fits a typical 8 GB GPU",
    "qwen2.5:14b": "Qwen2.5 14B -- may not fit in 8 GB; expect CPU offload (much slower)",
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


def _ollama_chat(base_url: str, payload: dict) -> dict:
    """POST /api/chat with the slow-local-model timeout; returns the JSON
    reply, read with the provider byte cap. A refused/unresolvable/unreachable
    server and a model that isn't pulled become OllamaUnavailableError;
    requests' own messages embed the URL, so none of that text is kept."""
    import requests
    try:
        resp = requests.post(f"{base_url}/api/chat", json=payload, stream=True,
                             timeout=OLLAMA_CHAT_TIMEOUT)
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
        return read_json_capped(resp, OLLAMA_CHAT_TIMEOUT)
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
            return resp["message"]["content"].strip()

        return request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"), engine_name="ollama")


# {base_url: (checked_at, reachable)} -- Ollama is exempted from the
# API-key check entirely, so with nothing in its place, clicking
# Translate against a stopped local server used to start a background
# job that only failed once translate_batch's own 300s request timeout
# expired. check_ollama_reachable() lets the UI disable that button
# BEFORE starting the job instead. Cached briefly per base_url so a
# Streamlit rerun (which happens on almost every interaction) doesn't
# re-hit the health check every time.
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

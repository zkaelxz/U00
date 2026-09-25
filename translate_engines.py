"""
translate_engines.py -- pluggable translation backends.

Each engine exposes: translate_batch(zh_lines: list[str], context: dict) -> list[str]
"context" carries: drama metadata, novel_reference, style_note, and (for
LLM engines) a system-prompt builder so behavior stays consistent across
engines that support instructions vs. engines that are pure MT.
"""

import re
import json
import time

# Rough per-million-token pricing in USD, for cost estimation only --
# these change over time and vary by exact model tier, so treat this as
# an approximation, not a bill. Update if pricing changes materially.
# USD per million tokens. Verified against provider pricing pages in
# July 2026. Providers reprice frequently -- treat these as estimates for
# the dashboard, not as a billing source of truth, and re-check before
# budgeting a large batch.
# Selectable Claude models, current as of this writing. Anthropic updates
# this lineup periodically -- if a model here stops working, check
# https://docs.claude.com for the current list and update both this and
# PRICING_PER_MILLION_TOKENS below.
CLAUDE_MODELS = {
    "claude-sonnet-5": "Sonnet 5 -- balanced quality and cost (recommended default)",
    "claude-opus-4-8": "Opus 4.8 -- highest quality, most expensive",
    "claude-haiku-4-5-20251001": "Haiku 4.5 -- fastest and cheapest, lower nuance",
    "claude-sonnet-4-6": "Sonnet 4.6 -- previous generation",
}

# Selectable Gemini models. Google's naming/lineup changes at least as
# often as Anthropic's -- if a model here starts 404ing, check
# https://ai.google.dev/gemini-api/docs/models for the current list and
# update both this and PRICING_PER_MILLION_TOKENS below. Verified against
# ai.google.dev/gemini-api/docs/pricing in September 2026.
GEMINI_MODELS = {
    "gemini-flash-lite-latest": "Flash-Lite -- cheapest, recommended default for bulk subtitle translation",
    "gemini-flash-latest": "Flash -- stronger nuance than Flash-Lite, still inexpensive",
    "gemini-pro-latest": "Pro -- highest quality, most expensive",
}

PRICING_PER_MILLION_TOKENS = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-opus-4-8": {"input": 15.0, "output": 75.0},
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
    "deepseek-v4-pro": {"input": 0.435, "output": 0.87},
    # Legacy aliases, retired July 2026 -- kept so old usage_log rows still
    # cost out instead of silently reporting $0.
    "deepseek-chat": {"input": 0.28, "output": 0.42},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "gemini-flash-lite-latest": {"input": 0.30, "output": 2.50},
    "gemini-flash-latest": {"input": 0.75, "output": 3.75},
    "gemini-pro-latest": {"input": 2.0, "output": 12.0},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = PRICING_PER_MILLION_TOKENS.get(model)
    if not rates:
        return 0.0
    return (input_tokens / 1_000_000 * rates["input"]) + (output_tokens / 1_000_000 * rates["output"])


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
    if "429" in str(e):
        return True
    return False


def call_with_backoff(fn, max_retries: int = 5, base_delay: float = 2.0, max_delay: float = 60.0):
    """Runs fn() with retry logic:
    - Rate-limit errors get exponential backoff (2s, 4s, 8s, ... capped
      at max_delay) up to max_retries -- these are expected/recoverable,
      so it's worth waiting them out rather than giving up.
    - Other errors get exactly one quick retry (covers a transient
      network blip) before being raised -- so a genuinely broken
      request (bad key, malformed input) fails fast instead of
      retrying pointlessly for a minute.
    """
    last_exception = None
    non_rate_limit_retried = False
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_exception = e
            if _is_rate_limit_error(e):
                delay = min(base_delay * (2 ** attempt), max_delay)
                time.sleep(delay)
                continue
            elif not non_rate_limit_retried:
                non_rate_limit_retried = True
                time.sleep(1)
                continue
            else:
                raise
    raise last_exception


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
]


def redact_secrets(text: str) -> str:
    """Strips anything that looks like an API key or bearer token out of
    an error string before it's shown in the UI, stored on the drama, or
    written to the log file."""
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "[REDACTED]" if m.groups() else "[REDACTED]", text)
    return text


# Reused from forced_align.py rather than duplicated -- both files need
# the same "zh"/"ja"/"ko" -> full language name mapping.
from forced_align import LANGUAGE_NAMES

# How each of this app's own content_mode/media_type values reads in a
# sentence, for the opening line of the system prompt. Falls back to
# "content" (still correct, just generic) for anything not listed here
# rather than guessing at a label.
_MEDIUM_DESCRIPTIONS = {
    "audio_drama": "audio drama", "video_drama": "video drama",
    "streamer_vod": "livestream VOD", "novel_narration": "novel",
    "novel": "novel", "manhwa": "manhwa", "manga": "manga", "manhua": "manhua",
    "asmr": "ASMR audio", "other": "content",
}


def build_llm_instructions(style_note: str, drama_meta: dict, novel_reference, locale: str = "en-US",
                            glossary_terms=None, style_guidelines: str = "", recent_context=None,
                            upcoming_lines=None):
    """
    upcoming_lines: raw (untranslated) source text for the few lines
    immediately AFTER this batch, shown for context only -- the model is
    told explicitly not to translate them here. Fixes a real, one-sided
    gap: recent_context already showed how PRECEDING lines were
    translated, but nothing showed what comes next, so a line ending on
    a cliffhanger or an incomplete thought had no forward context to
    resolve against, only backward.
    """
    meta_lines = []
    for label, key in [("Title", "title_en"), ("Original title", "title_zh"),
                        ("Author", "author"), ("Studio", "studio"),
                        ("Director", "director"), ("Voice actors", "voice_actors")]:
        val = drama_meta.get(key)
        if val:
            meta_lines.append(f"{label}: {val}")
    meta_block = "\n".join(meta_lines)

    locale_names = {"en-US": "American English", "en-GB": "British English", "en-AU": "Australian English"}
    locale_instruction = (
        f"Write in {locale_names.get(locale, 'American English')} -- match its spelling "
        f"conventions (e.g. {'colour/realise' if locale == 'en-GB' else 'color/realize'}) and "
        "phrasing register consistently throughout.\n"
    )

    # When full craft guidelines are supplied (from translation_guide.py) they
    # already include the term glossary grouped by handling policy, so the
    # simpler flat glossary list below is skipped to avoid duplicate instructions.
    if style_guidelines:
        glossary_block = f"\n{style_guidelines}\n"
    elif glossary_terms:
        pairs = "\n".join(f"- {t['term_original']} -> {t['term_translation']}"
                          + (f" ({t['notes']})" if t.get("notes") else "")
                          for t in glossary_terms)
        glossary_block = (
            f"\nSeries glossary (use these exact translations whenever these terms appear -- "
            f"this keeps terminology consistent across every drama in this series):\n{pairs}\n"
        )
    else:
        glossary_block = ""

    if recent_context:
        ctx_pairs = "\n".join(f"- {zh} -> {en}" for zh, en in recent_context)
        context_block = (
            "\nHow the lines immediately before this batch were just translated "
            "(for continuity -- a pronoun, an ongoing topic, or a person referred "
            "to only by relation may depend on this). These are NOT part of what "
            f"you're translating now:\n{ctx_pairs}\n"
        )
    else:
        context_block = ""

    if upcoming_lines:
        upcoming_block = (
            "\nWhat's said immediately AFTER this batch, in the original language "
            "(for context only -- a line that ends on an unresolved thought or a "
            "cliffhanger may need this to translate correctly). Do NOT translate "
            "these here, they'll be translated in a later batch:\n"
            + "\n".join(f"- {t}" for t in upcoming_lines) + "\n"
        )
    else:
        upcoming_block = ""

    source_language = drama_meta.get("source_language") or "zh"
    source_language_name = LANGUAGE_NAMES.get(source_language, "the source language")
    medium = _MEDIUM_DESCRIPTIONS.get(
        drama_meta.get("content_mode") or drama_meta.get("media_type"), "content")

    instructions = (
        f"You are translating {source_language_name} baihe (GL/yuri) {medium} into "
        "natural, idiomatic English subtitles. You will be given numbered lines to "
        "translate in each request, each optionally prefixed with the name of the "
        "character speaking it in [brackets] -- use that to get pronouns, honorifics, "
        "and register right, but never include the bracketed name itself in your "
        "translation.\n\n"
        + (f"Drama metadata:\n{meta_block}\n\n" if meta_block else "")
        + locale_instruction
        + glossary_block
        + context_block
        + upcoming_block
        + "Rules:\n"
        "- Keep each translation concise enough to read comfortably as a subtitle.\n"
        "- Keep character names, honorifics, and recurring terms consistent.\n"
        "- Match tone/register to the reference novel translation below, if provided -- "
        "that is the authoritative source for names, relationships, and voice for THIS "
        "drama only. Do not draw on any other novel or outside knowledge of similarly-"
        "named characters.\n"
        "- If a line has no clear match in the reference, translate it naturally while "
        "staying consistent with the established voice.\n"
        + (f"- Additional style notes: {style_note}\n" if style_note else "")
        + "- Return ONLY a JSON object mapping each line's number (as a string) to its "
        "translation, e.g. {\"1\": \"...\", \"2\": \"...\"} -- include EVERY number you "
        "were given, and no numbers you weren't. No preamble, no markdown fences, no "
        "commentary."
    )
    return instructions, meta_block


def _parse_json_array(text: str, fallback_count: int):
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


def _build_numbered_lines(ids: list, zh_lines: list, speaker_names: list = None) -> str:
    """
    Builds the numbered-line block shown to the model, e.g.:
        1. [Xiaoling] 你好
        2. 那天下着雨。
    speaker_names (parallel to zh_lines, None entries allowed) prefixes
    only the lines a name is actually known for -- a line with no known
    speaker (narration, an unlabeled line) is left unprefixed rather
    than showing a placeholder like "[Unknown]", which would just be
    noise the model has to ignore.
    """
    out = []
    for idx, (i, zh) in enumerate(zip(ids, zh_lines)):
        name = speaker_names[idx] if speaker_names else None
        prefix = f"[{name}] " if name else ""
        out.append(f"{i}. {prefix}{zh}")
    return "\n".join(out)


def _extract_first_json_value(text: str):
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


def _parse_id_keyed_json(text: str, expected_ids: list) -> dict:
    """
    Parses a response expected to be a JSON object mapping each line's
    id (as a string) to its translation, e.g. {"1": "Hello.", "2": "Hi."}.
    Returns {id_str: text} for whichever of expected_ids actually came
    back with an actual string translation -- a missing id (or one
    whose value isn't a string, e.g. null or a nested list) just isn't
    a key here, it's the caller's job (_request_translations_with_retry)
    to decide what to do about that, not this function's.

    Tolerates a plain JSON array too (mapping array position to id
    positionally) for a model that ignores the object-shape instruction
    -- graceful degradation, not the primary path. Only when the array's
    length matches expected_ids exactly: a short or long array has no
    reliable position-to-id mapping (["A", "C"] for ids [1, 2, 3] would
    otherwise put line 3's translation on line 2's id), so those are
    left for the retry path to re-request instead of guessed at here.
    """
    stripped = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    expected_str = {str(i) for i in expected_ids}
    data = _extract_first_json_value(stripped)
    if data is None:
        return {}
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items()
                if str(k) in expected_str and isinstance(v, str)}
    if isinstance(data, list):
        if len(data) != len(expected_ids):
            return {}
        return {str(expected_ids[i]): v for i, v in enumerate(data) if isinstance(v, str)}
    return {}


def _request_translations_with_retry(zh_lines: list, speaker_names, call_model_fn, max_retries: int = 1,
                                     line_ids=None):
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
    # The lines' own permanent ids (Step 2) when every line has one and
    # they're unique -- the same id a line keeps through merges and
    # re-saves. Otherwise (unsaved lines, non-translation callers) 1..n.
    if (line_ids is not None and len(line_ids) == len(zh_lines)
            and all(isinstance(i, int) for i in line_ids) and len(set(line_ids)) == len(line_ids)):
        ids = list(line_ids)
    pos = {i: p for p, i in enumerate(ids)}
    remaining_ids = list(ids)
    result_map = {}
    for _attempt in range(max_retries + 1):
        if not remaining_ids:
            break
        batch_lines = [zh_lines[pos[i]] for i in remaining_ids]
        batch_names = ([speaker_names[pos[i]] for i in remaining_ids] if speaker_names else None)
        numbered = _build_numbered_lines(remaining_ids, batch_lines, batch_names)
        text = call_model_fn(numbered)
        result_map.update(_parse_id_keyed_json(text, remaining_ids))
        remaining_ids = [i for i in ids if str(i) not in result_map]
    return [result_map.get(str(i), "") for i in ids]


def call_llm_json(engine, prompt: str, max_tokens: int = 2000, fallback: str = "[]",
                   usage_cb=None) -> str:
    """
    Shared single-prompt LLM call for every feature that isn't a
    translation batch: emotion tagging, translation notes, glossary
    extraction, adaptive style analysis, story tools, the universe wiki,
    line tools, Q&A, dictionary's LLM fallback, Scanlate's page
    translation, metadata lookup, and bulk import.

    Until this existed, each of those ~11 call sites carried its own
    copy of this function, and every copy only handled two of this
    app's three LLM call shapes: Claude's Messages API and an
    OpenAI-compatible chat client (DeepSeek/etc.). GeminiEngine has
    neither -- it calls Gemini's REST endpoint directly with `requests`
    -- so picking Gemini as the engine made every one of these features
    silently return nothing (an empty result, not an error) instead of
    a real answer. Confirmed directly: `hasattr(engine, "client")` is
    False for GeminiEngine, so every copy fell through to its "decline
    quietly" branch. `test_offline` had a worse version of the same gap:
    its `.client` attribute exists but is `None` (by design, so it can
    "decline cleanly" per its own docstring), but `hasattr(engine,
    "client")` is True either way, so the old code took the OpenAI-shaped
    branch and crashed on `None.chat` instead of declining -- meaning the
    app's own "try it for free first" onboarding path crashed the moment
    you clicked most of these features.

    usage_cb, if given, is called with (input_tokens, output_tokens)
    after a successful call, the same shape already used by the main
    Translate job's own usage_cb -- so a caller can log real spend here
    too, instead of only translation ever reaching the cost dashboard.
    """
    client = getattr(engine, "client", None)
    if client is not None and hasattr(client, "messages"):
        resp = call_with_backoff(lambda: client.messages.create(
            model=engine.model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ))
        if usage_cb and hasattr(resp, "usage"):
            usage_cb(getattr(resp.usage, "input_tokens", 0),
                     getattr(resp.usage, "output_tokens", 0))
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    if client is not None:
        resp = call_with_backoff(lambda: client.chat.completions.create(
            model=engine.model, messages=[{"role": "user", "content": prompt}],
        ))
        if usage_cb and getattr(resp, "usage", None):
            usage_cb(getattr(resp.usage, "prompt_tokens", 0),
                     getattr(resp.usage, "completion_tokens", 0))
        return resp.choices[0].message.content.strip()

    if isinstance(engine, GeminiEngine):
        import requests
        engine._throttle_for_free_tier()
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{engine.model}:generateContent")
        resp = call_with_backoff(lambda: requests.post(
            url, headers={"x-goog-api-key": engine.api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=120))
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usageMetadata") or {}
        if usage_cb:
            usage_cb(usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0))
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError):
            return fallback

    if isinstance(engine, OllamaEngine):
        import requests
        resp = requests.post(f"{engine.base_url}/api/chat", json={
            "model": engine.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"num_ctx": _estimate_ollama_num_ctx(prompt, "")},
        }, timeout=300)  # local models can be slow, especially CPU-only or larger ones
        resp.raise_for_status()
        data = resp.json()
        if usage_cb:
            usage_cb(data.get("prompt_eval_count", 0), data.get("eval_count", 0))
        return data["message"]["content"].strip()

    if isinstance(engine, TestOfflineEngine):
        # fallback is already a valid, correctly-shaped "nothing found"
        # result for every caller of this function (an empty list/object,
        # or an empty string) -- exactly what a real engine's response
        # collapses to today when parsing fails. Explicit here, not an
        # accident of falling through with no client and not being
        # Gemini/Ollama, so Test mode's own behavior can't silently
        # change if a future engine is added above it.
        return fallback

    raise RuntimeError(f"{getattr(engine, 'name', type(engine).__name__)} can't run this feature.")


# ---------------------------------------------------------------------------
# Claude (Anthropic)
# ---------------------------------------------------------------------------

class ClaudeEngine:
    name = "claude"
    supports_reference = True

    def __init__(self, api_key: str, model: str = "claude-sonnet-5"):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, zh_lines, context: dict):
        instructions, _ = build_llm_instructions(
            context.get("style_note", ""), context.get("drama_meta", {}),
            context.get("novel_reference"), locale=context.get("locale", "en-US"),
            glossary_terms=context.get("glossary_terms"),
            style_guidelines=context.get("style_guidelines", ""),
            recent_context=context.get("recent_context"),
            upcoming_lines=context.get("upcoming_lines"),
        )
        blocks = [{"type": "text", "text": instructions}]
        novel_reference = context.get("novel_reference")
        if novel_reference and novel_reference.strip():
            blocks.append({
                "type": "text",
                "text": "REFERENCE NOVEL TRANSLATION (authoritative for THIS drama only):\n\n"
                        + novel_reference.strip(),
                "cache_control": {"type": "ephemeral"},
            })
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

        def call_model(numbered):
            resp = self.client.messages.create(
                model=self.model, max_tokens=4000, system=blocks,
                messages=[{"role": "user", "content": "Translate these lines:\n\n" + numbered}],
            )
            if hasattr(resp, "usage"):
                self.last_usage["input_tokens"] += getattr(resp.usage, "input_tokens", 0)
                self.last_usage["output_tokens"] += getattr(resp.usage, "output_tokens", 0)
            return "".join(b.text for b in resp.content if b.type == "text").strip()

        return _request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"))


# ---------------------------------------------------------------------------
# DeepSeek (OpenAI-compatible chat API) -- cheap, strong on Chinese
# ---------------------------------------------------------------------------

class DeepSeekEngine:
    name = "deepseek"
    supports_reference = True

    def __init__(self, api_key: str, model: str = "deepseek-v4-flash"):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self.model = model
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, zh_lines, context: dict):
        instructions, _ = build_llm_instructions(
            context.get("style_note", ""), context.get("drama_meta", {}),
            context.get("novel_reference"), locale=context.get("locale", "en-US"),
            glossary_terms=context.get("glossary_terms"),
            style_guidelines=context.get("style_guidelines", ""),
            recent_context=context.get("recent_context"),
            upcoming_lines=context.get("upcoming_lines"),
        )
        novel_reference = context.get("novel_reference")
        system_text = instructions
        if novel_reference and novel_reference.strip():
            system_text += ("\n\nREFERENCE NOVEL TRANSLATION (authoritative for THIS drama "
                             "only):\n\n" + novel_reference.strip())
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

        def call_model(numbered):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": "Translate these lines:\n\n" + numbered},
                ],
            )
            if hasattr(resp, "usage") and resp.usage:
                self.last_usage["input_tokens"] += getattr(resp.usage, "prompt_tokens", 0)
                self.last_usage["output_tokens"] += getattr(resp.usage, "completion_tokens", 0)
            return resp.choices[0].message.content.strip()

        return _request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"))


# ---------------------------------------------------------------------------
# Gemini (Google) -- cheap, strong multilingual, OpenAI-style REST call
# ---------------------------------------------------------------------------

class GeminiEngine:
    """Uses the plain generateContent REST endpoint with an API-key query
    param (like GoogleEngine below), rather than the google-genai SDK --
    no extra dependency needed, and it's a simple enough API that the SDK
    doesn't buy much here."""
    name = "gemini"
    supports_reference = True

    def __init__(self, api_key: str, model: str = "gemini-flash-lite-latest",
                 free_tier: bool = False):
        self.api_key = api_key
        self.model = model
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}
        # Free-tier Gemini keys hard-error past ~10 requests/minute rather
        # than queuing -- self-pacing client-side is cheaper than handling
        # 429s. Paid keys have no such limit, so this only applies here.
        self.free_tier = free_tier
        self._free_tier_request_times = []

    def _throttle_for_free_tier(self):
        if not self.free_tier:
            return
        now = time.monotonic()
        self._free_tier_request_times = [
            t for t in self._free_tier_request_times if now - t < 60]
        if len(self._free_tier_request_times) >= GEMINI_FREE_TIER_MAX_PER_MINUTE:
            wait = 60 - (now - self._free_tier_request_times[0])
            if wait > 0:
                time.sleep(wait)
            now = time.monotonic()
            self._free_tier_request_times = [
                t for t in self._free_tier_request_times if now - t < 60]
        self._free_tier_request_times.append(now)

    def translate_batch(self, zh_lines, context: dict):
        import requests
        instructions, _ = build_llm_instructions(
            context.get("style_note", ""), context.get("drama_meta", {}),
            context.get("novel_reference"), locale=context.get("locale", "en-US"),
            glossary_terms=context.get("glossary_terms"),
            style_guidelines=context.get("style_guidelines", ""),
            recent_context=context.get("recent_context"),
            upcoming_lines=context.get("upcoming_lines"),
        )
        novel_reference = context.get("novel_reference")
        if novel_reference and novel_reference.strip():
            instructions += ("\n\nREFERENCE NOVEL TRANSLATION (authoritative for THIS drama "
                              "only):\n\n" + novel_reference.strip())
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

        def call_model(numbered):
            self._throttle_for_free_tier()
            resp = requests.post(url, headers={"x-goog-api-key": self.api_key}, json={
                "systemInstruction": {"parts": [{"text": instructions}]},
                "contents": [{"parts": [{"text": "Translate these lines:\n\n" + numbered}]}],
            }, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usageMetadata") or {}
            self.last_usage["input_tokens"] += usage.get("promptTokenCount", 0)
            self.last_usage["output_tokens"] += usage.get("candidatesTokenCount", 0)
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()

        return _request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"))


# ---------------------------------------------------------------------------
# DeepL -- fast, cheap, pure MT (no reference-novel awareness)
# ---------------------------------------------------------------------------

# DeepL's own source-language codes -- confirmed via direct testing that
# hardcoding "ZH" regardless of the drama's actual source language was a
# real bug: a Japanese or Korean drama translated through DeepL was
# silently telling DeepL its audio was Chinese the whole time.
_DEEPL_SOURCE_LANGS = {"zh": "ZH", "ja": "JA", "ko": "KO"}


class DeepLEngine:
    name = "deepl"
    supports_reference = False

    def __init__(self, api_key: str):
        import deepl
        self.translator = deepl.Translator(api_key)

    def translate_batch(self, zh_lines, context: dict):
        source_lang = _DEEPL_SOURCE_LANGS.get(context.get("source_language", "zh"), "ZH")
        results = self.translator.translate_text(
            zh_lines, source_lang=source_lang, target_lang="EN-US"
        )
        if not isinstance(results, list):
            results = [results]
        return [r.text for r in results]


# ---------------------------------------------------------------------------
# Google Cloud Translation -- broadest coverage, pure MT
# ---------------------------------------------------------------------------

class GoogleEngine:
    name = "google"
    supports_reference = False

    def __init__(self, api_key: str):
        # Uses the simple API-key REST endpoint rather than the full
        # google-cloud-translate SDK, to avoid needing service-account setup.
        self.api_key = api_key

    def translate_batch(self, zh_lines, context: dict):
        import requests
        url = "https://translation.googleapis.com/language/translate/v2"
        # This app's own source_language values ("zh"/"ja"/"ko") already
        # match Google's own codes directly -- no mapping table needed,
        # unlike DeepL's differently-cased codes above. Hardcoding "zh"
        # here regardless of the actual source was the same real bug as
        # DeepLEngine's: a Japanese/Korean drama silently mistranslated.
        resp = requests.post(url, headers={"X-Goog-Api-Key": self.api_key}, json={
            "q": zh_lines, "source": context.get("source_language", "zh"),
            "target": "en", "format": "text",
        }, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return [t["translatedText"] for t in data["data"]["translations"]]


# ---------------------------------------------------------------------------
# Local NLLB-200 -- genuinely free, fully offline neural MT, no API key
# ---------------------------------------------------------------------------

# NLLB-200's own language codes for the three source languages this app
# supports. zh always maps to Simplified here (NLLB has a separate
# zho_Hant code for Traditional) -- see NLLBEngine's docstring for why
# that's a real, currently-unaddressed limitation rather than an oversight.
_NLLB_LANG_CODES = {"zh": "zho_Hans", "ja": "jpn_Jpan", "ko": "kor_Hang"}

NLLB_MODELS = {
    "facebook/nllb-200-distilled-600M": "600M -- fastest, lightest download (~2.4GB), practical on CPU",
    "facebook/nllb-200-distilled-1.3B": "1.3B -- better quality, slower, heavier download (~5.2GB)",
}

# Keyed by (model_name, source_language) -- NLLB bakes src_lang into the
# pipeline object itself, so a drama that mixes source languages across
# runs needs a separate pipeline per language, same shape as Whisper's own
# _whisper_model_cache in core.py.
_nllb_pipeline_cache = {}


class NLLBEngine:
    """Fully local, offline neural machine translation via Meta's NLLB-200
    -- no API key, no network once the model's downloaded once, no
    per-token cost. This is a REAL translation engine, not a placeholder
    like test_offline: it actually produces usable (if rougher) English,
    just with meaningfully lower quality than Claude/DeepSeek/Gemini on
    tone, idiom, and character-voice consistency, since it's pure
    sequence-to-sequence MT with no instruction-following ability at all
    -- the same category as DeepL/Google, not an LLM. Good for a genuinely
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

    def _get_pipeline(self, source_language: str):
        cache_key = (self.model_name, source_language)
        if cache_key not in _nllb_pipeline_cache:
            from transformers import pipeline
            src_lang = _NLLB_LANG_CODES.get(source_language, "zho_Hans")
            _nllb_pipeline_cache[cache_key] = pipeline(
                "translation", model=self.model_name, src_lang=src_lang, tgt_lang="eng_Latn")
        return _nllb_pipeline_cache[cache_key]

    def translate_batch(self, zh_lines, context: dict):
        pipe = self._get_pipeline(context.get("source_language", "zh"))
        results = pipe(list(zh_lines))
        return [r["translation_text"] for r in results]


def tag_speakers_llm(zh_chunks, engine, known_characters=None, batch_size: int = 15, usage_cb=None):
    """For novel narration mode (no audio, no diarization available):
    asks the translation engine to guess who's speaking each chunk --
    a character name, or 'Narrator' for descriptive prose. Works with
    any LLM-capable engine (Claude, DeepSeek); pure-MT engines (DeepL,
    Google) can't do this and will return 'Narrator' for everything.

    Returns a list of speaker labels, same length/order as zh_chunks.
    This is a best-effort heuristic -- always let the user correct
    labels in the review table afterwards."""
    if not getattr(engine, "supports_reference", False):
        return ["Narrator"] * len(zh_chunks)

    known = ", ".join(known_characters) if known_characters else "(none known yet)"
    labels = []
    for start in range(0, len(zh_chunks), batch_size):
        batch = zh_chunks[start:start + batch_size]

        def call_model(numbered, known=known):
            prompt = (
                "For each numbered chunk of Chinese novel text below, identify who is "
                "speaking. If it's dialogue attributed to a specific character, return "
                "that character's name (romanized consistently). If it's narration/"
                "description with no speaking character, return 'Narrator'. "
                f"Known characters so far: {known}. Prefer reusing a known name over "
                "inventing a new one when it's clearly the same person.\n\n"
                'Return ONLY a JSON object mapping each number to its speaker label, e.g. '
                '{"1": "Xiaoling", "2": "Narrator"}. Include EVERY number you were given, '
                "and no numbers you weren't. No preamble, no markdown fences.\n\n" + numbered
            )
            # Reuse whichever engine's underlying client is available for a raw completion.
            return call_llm_json(engine, prompt, max_tokens=2000, fallback="{}", usage_cb=usage_cb)

        # Id-keyed, same reasoning/mechanism as translation's own fix: a
        # plain positional array silently misassigns a chunk's label to
        # the wrong chunk if the response comes back short, long, or
        # reordered. Missing ids retry once, then default to "Narrator"
        # (the safe fallback for novel narration) rather than staying
        # blank.
        batch_labels = _request_translations_with_retry(batch, None, call_model)
        labels.extend(lbl if lbl.strip() else "Narrator" for lbl in batch_labels)
    return labels[:len(zh_chunks)]


def smart_segment_lines(en_lines, target_wpm: float = 160, min_seconds: float = 1.2):
    """Post-translation pacing pass (idea borrowed from KrillinAI/
    VideoLingo): flags English lines that are too short to read
    comfortably at their assigned duration, or too long to say
    naturally within it, using a simple words-per-minute estimate.
    Returns a list of {idx, issue, suggestion} for lines worth a second
    look before dubbing -- this doesn't auto-edit anything, just tells
    you where the pacing is likely to feel rushed or dragged out."""
    flags = []
    for ln in en_lines:
        duration = max(ln.end - ln.start, 0.01)
        word_count = len(ln.en.split())
        needed_seconds = word_count / (target_wpm / 60)
        if needed_seconds > duration * 1.15:
            flags.append({"idx": ln.idx, "issue": "too_long_for_slot",
                          "detail": f"~{needed_seconds:.1f}s needed, only {duration:.1f}s available"})
        elif duration > min_seconds and needed_seconds < duration * 0.4:
            flags.append({"idx": ln.idx, "issue": "very_short_relative_to_slot",
                          "detail": f"~{needed_seconds:.1f}s needed, {duration:.1f}s available -- "
                                    "may sound like a long pause"})
    return flags


def rewrite_for_pacing_llm(lines_to_fix, engine, batch_size: int = 15, usage_cb=None):
    """For lines flagged as too long to say in their time slot: asks
    the LLM to rewrite them more concisely while preserving meaning,
    so the dub actually fits. Only touches .en; leaves .zh untouched.
    lines_to_fix: list of Line objects (already has .en set)."""
    if not getattr(engine, "supports_reference", False) or not lines_to_fix:
        return lines_to_fix
    for start in range(0, len(lines_to_fix), batch_size):
        batch = lines_to_fix[start:start + batch_size]

        def call_model(numbered):
            prompt = (
                "These English subtitle lines need to be shortened so they can be spoken "
                "naturally within their time slot. Rewrite each one more concisely -- cut "
                "filler words, tighten phrasing -- while keeping the same meaning and tone. "
                'Return ONLY a JSON object mapping each number to its rewritten line, e.g. '
                '{"1": "Shortened line.", "2": "Another one."}. Include EVERY number you '
                "were given, and no numbers you weren't. No preamble, no markdown fences.\n\n"
                + numbered
            )
            return call_llm_json(engine, prompt, max_tokens=2000, fallback="{}", usage_cb=usage_cb)

        # Id-keyed for the same reason as translation itself: a plain
        # positional array silently misassigns a rewrite to the wrong
        # line if the response comes back short, long, or reordered.
        # Missing ids retry once, then fall back to leaving that line's
        # existing .en untouched rather than blanking it.
        rewritten = _request_translations_with_retry([ln.en for ln in batch], None, call_model)
        for ln, new_text in zip(batch, rewritten):
            if new_text.strip():
                ln.en = new_text.strip()
    return lines_to_fix


def check_consistency_llm(lines, engine, batch_size: int = 60, usage_cb=None):
    """Reviews already-translated lines for consistency issues: the same
    Chinese term/name translated differently in different places. Works
    on the .zh/.en pairs already present -- doesn't call any external
    dictionary, just asks the LLM to spot drift across the batch it's
    given. Returns a list of {"term", "variants": [...], "note"} for
    review -- doesn't auto-fix anything, since the "right" choice
    depends on context you'd want to confirm yourself.

    Only meaningful with an LLM-capable engine; pure-MT engines return
    an empty list (they don't reason about the whole set at once)."""
    if not getattr(engine, "supports_reference", False):
        return []
    translated = [ln for ln in lines if ln.en.strip()]
    if not translated:
        return []

    issues = []
    for start in range(0, len(translated), batch_size):
        batch = translated[start:start + batch_size]
        pairs = "\n".join(f"{i+1}. {ln.zh} -> {ln.en}" for i, ln in enumerate(batch))
        prompt = (
            "Below are Chinese source lines paired with their English translations, from the "
            "same drama. Look for CONSISTENCY issues: the same Chinese name, term, or recurring "
            "phrase translated differently in different lines (e.g. a character's name spelled "
            "two ways, or a recurring term like a nickname/title rendered inconsistently). "
            "Ignore normal translation variation for ordinary sentences -- only flag terms that "
            "should clearly stay fixed (names, titles, recurring phrases) but don't.\n\n"
            'Return ONLY a JSON array of objects: [{"term": "...", "variants": ["...", "..."], '
            '"note": "brief description"}]. Empty array if nothing found. No preamble, no '
            "markdown fences.\n\n" + pairs
        )
        try:
            text = call_llm_json(engine, prompt, max_tokens=2000, fallback=None,
                                  usage_cb=usage_cb)
            if text is None:
                continue
        except Exception:
            continue  # a check failing shouldn't block anything -- just skip that batch
        batch_issues = _parse_json_array(text, 0)
        if isinstance(batch_issues, list):
            issues.extend(i for i in batch_issues if isinstance(i, dict) and i.get("term"))
    return issues


# Kept intentionally to what's actually assessable from the text alone --
# "speaker uncertain" and "overlapping speech"/"audio unclear" would need
# real diarization-confidence or audio evidence this codebase doesn't
# expose yet (see diarize.py), not something worth guessing at from text.
FLAG_REASONS = {
    "uncertain_translation": "Possible mistranslation or awkward phrasing worth a second look",
    "ambiguous_reference": "A pronoun or reference ('she', 'that place') that isn't clearly resolved",
    "name_uncertain": "A name or term that might be misspelled or inconsistently romanized",
    "slang_idiom": "Slang or an idiom that may not have translated well",
}


def flag_uncertain_lines(lines, engine, batch_size: int = 30, progress_cb=None, usage_cb=None):
    """
    Reviews already-translated lines and flags the ones worth a second
    look -- the review-queue idea: instead of scanning a whole multi-hour
    transcript line by line, review just the handful the model itself
    wasn't confident about. Doesn't touch the translation itself, just
    annotates .flag/.flag_note on the Line objects it's given (mutated in
    place, same convention as translate_lines_with_engine).

    Only meaningful with an LLM-capable engine; pure-MT engines (DeepL,
    Google) can't reason about their own confidence and are left
    untouched -- every line's .flag stays whatever it already was.
    """
    if not getattr(engine, "supports_reference", False):
        return lines
    translated = [ln for ln in lines if ln.en.strip()]
    if not translated:
        return lines

    reasons_desc = "\n".join(f"  - {k}: {v}" for k, v in FLAG_REASONS.items())
    n_batches = (len(translated) + batch_size - 1) // batch_size

    for bi, start in enumerate(range(0, len(translated), batch_size)):
        batch = translated[start:start + batch_size]
        pairs = "\n".join(f"[{ln.idx}] {ln.zh} -> {ln.en}" for ln in batch)
        prompt = (
            "Below are Chinese source lines paired with their English translations. Flag ONLY "
            "the lines that genuinely need a second look -- most lines need none at all, and "
            "over-flagging defeats the point (the person reviewing this can't tell a real issue "
            "from noise). Reasons worth flagging:\n\n"
            f"{reasons_desc}\n\n"
            'Return ONLY a JSON array: [{"line_idx": 0, "reason": "uncertain_translation", '
            '"note": "brief reason"}]. Empty array if nothing needs flagging (the common case). '
            "No preamble, no markdown fences.\n\n" + pairs
        )
        try:
            text = call_llm_json(engine, prompt, max_tokens=2000, fallback="[]",
                                  usage_cb=usage_cb)
        except Exception:
            text = "[]"  # a check failing shouldn't block anything -- just skip that batch

        if progress_cb:
            progress_cb((bi + 1) / n_batches)

        flagged = _parse_json_array(text, 0)
        if not isinstance(flagged, list):
            continue
        by_idx = {ln.idx: ln for ln in batch}
        for f in flagged:
            if not isinstance(f, dict) or f.get("line_idx") is None:
                continue
            ln = by_idx.get(int(f["line_idx"]))
            if ln is None:
                continue
            reason = f.get("reason") if f.get("reason") in FLAG_REASONS else "uncertain_translation"
            ln.flag = reason
            ln.flag_note = f.get("note", "")
    return lines


class TestOfflineEngine:
    """A no-cost, no-network engine for verifying the pipeline works.

    Produces deterministic placeholder translations instead of calling any
    API. The point is to exercise the whole flow -- align, translate,
    review, merge, export, dub -- and confirm your install is sound
    BEFORE spending tokens on a real run. Output is obviously fake so it
    can never be mistaken for a real translation.

    Supports the reference/glossary interface so the surrounding code
    paths get exercised too, but it ignores the content: there's no model
    here to follow instructions.
    """
    name = "test_offline"
    supports_reference = True

    def __init__(self, api_key: str = None, model: str = "test-offline"):
        self.model = model
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}
        self.client = None  # no SDK client; free-form LLM features will decline cleanly

    def translate_batch(self, zh_lines, context: dict):
        out = []
        for i, line in enumerate(zh_lines):
            preview = (line or "").strip()
            if len(preview) > 40:
                preview = preview[:40] + "…"
            out.append(f"[TEST] {preview}")
        # Rough token accounting so the cost dashboard has something to show,
        # while estimate_cost() returns 0 for this model -- as it should.
        self.last_usage = {
            "input_tokens": sum(len(l) for l in zh_lines),
            "output_tokens": sum(len(o) for o in out),
        }
        return out


# Ollama's own default context window can be as small as 2-4k tokens,
# and a prompt longer than it gets silently TRUNCATED FROM THE START --
# exactly where the system instructions/glossary/reference novel live --
# with no error at all. This floor is deliberately generous: asking for
# more context than needed costs some memory but never loses a prompt,
# while asking for too little does so silently.
OLLAMA_MIN_NUM_CTX = 16384


def _estimate_ollama_num_ctx(system_text: str, numbered: str, floor: int = OLLAMA_MIN_NUM_CTX) -> int:
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


class OllamaEngine:
    """Fully local/offline translation via Ollama (https://ollama.com) --
    no API key, no internet needed once you've pulled a model. Quality
    depends heavily on which model you run locally; a capable general
    model (e.g. qwen2.5, llama3.1) handles Chinese->English reasonably,
    but won't match Claude/DeepSeek on tone/nuance. Good for cost-free
    bulk drafts you'll hand-polish, or for offline-only environments."""
    name = "ollama"
    supports_reference = True

    def __init__(self, api_key: str = None, model: str = "qwen2.5:14b", base_url: str = "http://localhost:11434"):
        # api_key is unused (kept for a consistent engine constructor signature)
        self.model = model
        self.base_url = base_url.rstrip("/")

    def translate_batch(self, zh_lines, context: dict):
        import requests
        instructions, _ = build_llm_instructions(
            context.get("style_note", ""), context.get("drama_meta", {}),
            context.get("novel_reference"), locale=context.get("locale", "en-US"),
            glossary_terms=context.get("glossary_terms"),
            style_guidelines=context.get("style_guidelines", ""),
            recent_context=context.get("recent_context"),
            upcoming_lines=context.get("upcoming_lines"),
        )
        novel_reference = context.get("novel_reference")
        system_text = instructions
        if novel_reference and novel_reference.strip():
            system_text += ("\n\nREFERENCE NOVEL TRANSLATION (authoritative for THIS drama "
                             "only):\n\n" + novel_reference.strip())
        num_ctx_override = context.get("ollama_num_ctx_override")

        def call_model(numbered):
            estimated = _estimate_ollama_num_ctx(system_text, numbered)
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
            resp = requests.post(f"{self.base_url}/api/chat", json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": "Translate these lines:\n\n" + numbered},
                ],
                "stream": False,
                "format": _OLLAMA_ID_KEYED_JSON_SCHEMA,
                "options": {"num_ctx": num_ctx},
            }, timeout=300)  # local models can be slow, especially CPU-only or larger ones
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()

        return _request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"))


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
        resp = requests.get(f"{base_url}/api/tags", timeout=2.5)
        reachable = resp.ok
    except Exception:
        reachable = False
    _ollama_reachability_cache[base_url] = (now, reachable)
    return reachable


class LibreTranslateEngine:
    """Talks to any LibreTranslate-compatible /translate endpoint.

    Cost, accurately: the SOFTWARE is AGPL-3.0 and free, but that is not
    the same as free to use.
      - Self-hosted LibreTranslate: no per-word cost, but you run the
        server. Loading all 30+ languages wants ~8GB RAM and ~10GB disk.
      - Self-hosted LTEngine (https://github.com/LibreTranslate/LTEngine):
        runs LLMs locally via llama.cpp for quality reportedly near DeepL
        on some pairs. Its largest model (gemma3-27b) needs roughly a
        24GB-VRAM GPU; CPU-only runs but is slow.
      - The HOSTED libretranslate.com API is a paid service with pricing
        tiers, and needs an API key.

    So: free of per-token billing if you self-host and already have the
    hardware. Not free if you point it at the public hosted endpoint."""
    name = "libretranslate"
    supports_reference = False

    def __init__(self, api_key: str = None, base_url: str = "http://localhost:5000"):
        self.api_key = api_key  # None for local LTEngine; LibreTranslate hosted instances may need a key
        self.base_url = base_url.rstrip("/")

    def translate_batch(self, zh_lines, context: dict):
        import requests
        out = []
        for line in zh_lines:
            payload = {"q": line, "source": "auto", "target": "en"}
            if self.api_key:
                payload["api_key"] = self.api_key
            resp = requests.post(f"{self.base_url}/translate", json=payload, timeout=30)
            resp.raise_for_status()
            out.append(resp.json().get("translatedText", ""))
        return out


ENGINES = {
    # Paid/normal engines first, then the free-for-testing ones grouped
    # together at the end (see FREE_ENGINES/engine_picker_label below) --
    # st.selectbox has no real optgroup support, so keeping them
    # contiguous in iteration order is the closest every picker built
    # from ENGINES.keys() can get to a visually grouped list.
    "claude": ClaudeEngine,
    "deepseek": DeepSeekEngine,
    "gemini": GeminiEngine,
    "deepl": DeepLEngine,
    "google": GoogleEngine,
    "test_offline": TestOfflineEngine,
    "ollama": OllamaEngine,
    "nllb": NLLBEngine,
    "libretranslate": LibreTranslateEngine,
}

# Pure machine-translation engines: no instruction-following ability at
# all, so they can only ever translate. Plugging one into any other
# feature used to silently produce nothing (each feature's own
# `supports_reference` guard already declines quietly; call_llm_json's
# fallback used to do the same before Step 1d made it raise instead).
TRANSLATION_ONLY_ENGINES = {"deepl", "google", "nllb", "libretranslate"}

# Every other engine (Claude, DeepSeek, Gemini, Ollama, and Test mode)
# is a real LLM (or a stand-in for one) and can run every feature below,
# via call_llm_json or its own translate_batch/dispatch.
LLM_CAPABLE_ENGINES = set(ENGINES.keys()) - TRANSLATION_ONLY_ENGINES

# One small table: feature -> which engines can actually run it. Kept
# here (not inferred purely from TRANSLATION_ONLY_ENGINES) so a future
# engine that supports translation but not, say, Q&A has somewhere to
# say so explicitly instead of being silently assumed capable.
FEATURE_SUPPORTED_ENGINES = {
    "translate": set(ENGINES.keys()),
    "flag_review": LLM_CAPABLE_ENGINES,
    "consistency_check": LLM_CAPABLE_ENGINES,
    "emotion_detect": LLM_CAPABLE_ENGINES,
    "translation_notes": LLM_CAPABLE_ENGINES,
    "speaker_tagging": LLM_CAPABLE_ENGINES,
    "pacing_rewrite": LLM_CAPABLE_ENGINES,
    "qa": LLM_CAPABLE_ENGINES,
}

# Engines that are free to use every time, no conditions attached.
# Gemini isn't here -- it uses the same engine/API for free and paid
# keys, so whether a given run is "free" depends on the per-session
# "My Gemini key is free-tier" setting (settings_tab.py), not on which
# engine was picked. See engine_picker_label / estimate_cost_for_engine.
FREE_ENGINES = {"test_offline", "ollama", "nllb", "libretranslate"}

GEMINI_FREE_TIER_MAX_PER_MINUTE = 10

ENGINE_NOTES = {
    "claude": "Best for tone/character voice, supports novel reference + prompt caching.",
    "deepseek": "Far and away the cheapest capable option -- roughly 5-10 cents per drama on V4 Flash, and its prompt caching makes the repeated glossary/style block nearly free. Strong on Chinese, supports novel reference. OpenAI-compatible API.",
    "gemini": "Cheap and strong on Chinese/Japanese, close to DeepSeek pricing on Flash-Lite. Supports novel reference. Google model naming/pricing changes often -- double check GEMINI_MODELS if a run starts failing.",
    "deepl": "Fast, natural phrasing, but no reference-novel awareness -- pure MT.",
    "google": "Broadest language coverage, cheapest at scale, no reference-novel awareness.",
    "test_offline": "🧪 Free — for testing: fake output, no AI. Checks the app works; never use for real subtitles.",
    "ollama": "🧪 Free — for testing: local AI on your GPU. Private and unlimited, but lower quality than paid engines.",
    "nllb": "🧪 Free — for testing: offline, translation only. Non-commercial licence.",
    "libretranslate": "🧪 Free — for testing: translation only. Basic quality.",
}

# Shown instead of ENGINE_NOTES["gemini"] when the "My Gemini key is
# free-tier" checkbox (Settings) is ticked -- Gemini itself isn't in
# FREE_ENGINES since this only applies conditionally. See
# engine_picker_label, the one place that decides which note to show.
GEMINI_FREE_TIER_NOTE = (
    "🧪 Free — for testing: Google free tier. Rate-limited (about "
    f"{GEMINI_FREE_TIER_MAX_PER_MINUTE} requests/minute on Flash). Google may use your "
    "text to improve its products, and people may read it."
)


def engine_picker_label(engine_name: str, gemini_free_tier: bool = False) -> str:
    """The descriptive text an engine picker shows next to `engine_name`
    (format_func's job in every st.selectbox built from ENGINES.keys()).
    A plain lookup except for Gemini, whose free-vs-paid status isn't a
    property of the engine itself but of the per-session "My Gemini key
    is free-tier" setting."""
    if engine_name == "gemini" and gemini_free_tier:
        return GEMINI_FREE_TIER_NOTE
    return ENGINE_NOTES[engine_name]


def get_engine(engine_name: str, api_key: str = None, model: str = None,
               free_tier: bool = False):
    cls = ENGINES[engine_name]
    kwargs = {"free_tier": free_tier} if engine_name == "gemini" else {}
    if model:
        return cls(api_key, model, **kwargs)
    return cls(api_key, **kwargs)


def estimate_cost_for_engine(engine, input_tokens: int, output_tokens: int) -> float:
    """Same as estimate_cost, but $0 for a Gemini engine running under its
    free tier -- PRICING_PER_MILLION_TOKENS prices the paid tier, which
    doesn't apply once free_tier is set on the engine instance."""
    if getattr(engine, "free_tier", False):
        return 0.0
    return estimate_cost(getattr(engine, "model", ""), input_tokens, output_tokens)


def translate_lines_with_engine(lines, engine, drama_meta: dict, batch_size: int = 20,
                                 style_note: str = "", novel_reference=None, progress_cb=None,
                                 save_cb=None, force_retranslate: bool = False,
                                 locale: str = "en-US", glossary_terms=None, usage_cb=None,
                                 style_guidelines: str = "", cancel_check_cb=None,
                                 context_window: int = 6, context_window_ahead: int = 3,
                                 character_names: dict = None, ollama_num_ctx_override: int = None):
    """cancel_check_cb: optional callable returning True if the run should
    stop cooperatively between batches -- e.g. background_jobs.is_cancel_requested,
    so a background translation job can be stopped safely (rather than
    racing a destructive action like a full library reset against a
    thread that's still writing).

    ollama_num_ctx_override: optional Settings override for OllamaEngine's
    context-window size. Ignored by every other engine. OllamaEngine
    itself never lets this go below what the actual prompt needs --
    see _estimate_ollama_num_ctx's docstring for why."""
    """lines: list of objects with .zh and .en attributes (mutated in place).

    character_names: optional {speaker_label: character_name} (see
    db.list_characters) -- resolves each line's raw diarization label
    into an actual name shown to the translator, e.g. "[Xiaoling] 你好"
    instead of just "你好". Drives correct pronouns/honorifics/register,
    which the model previously had zero signal for. A line whose speaker
    has no name set is shown with no prefix at all, not the raw label
    (a diarization id like "SPEAKER_00" isn't a name and would just be
    noise for the model to ignore).

    save_cb: optional callback invoked after every batch (successful or
    not) with the full `lines` list so far, so progress survives a
    crash/network failure partway through -- call db.save_lines(...)
    from it. Without this, a failure on batch N loses batches 1..N-1
    too if the caller only saves once at the very end.

    usage_cb: optional callback invoked after every successful batch
    with (input_tokens, output_tokens) -- call db.log_usage(...) from
    it for cost tracking. Only fires for engines that expose
    .last_usage (currently Claude and DeepSeek); pure-MT engines don't
    report token counts the same way, so nothing is logged for those.

    A failed batch is retried once, then, if it fails again, its lines
    are left untranslated (.en stays empty) and noted in the returned
    `errors` list, rather than raising and losing everything after it.
    A batch whose engine returns the WRONG NUMBER of translations is
    treated the same way -- a real, confirmed bug this replaces: the
    old code paired the response with the batch by raw list position
    (zip()), so a response one line short (a common LLM failure: merging
    two lines into one, or silently dropping a line) silently shifted
    every translation after the gap onto the wrong line. A length
    mismatch is now always a batch error, never a misassignment --
    fixing the actual mismatch (an engine's own id-keyed JSON response
    parsing, for Claude/DeepSeek/Gemini/Ollama) lives in each engine's
    own translate_batch, since that's where the real per-line recovery
    (retry only the missing ones, not the whole batch) can happen with
    the id information still available; by the time a bare list[str]
    reaches here, all this can safely check is its length.

    By default, lines that already have non-empty .en are skipped --
    so calling this again after a partial failure only retries what's
    actually missing, instead of re-translating (and re-paying for)
    everything. Pass force_retranslate=True to redo everything anyway.

    context_window: how many already-translated lines immediately before
    each batch get shown to the model (as "how this was already
    translated", not something to retranslate). Batches are translated in
    isolation otherwise -- a pronoun or a person referred to only by
    relation ("her", "that guy") a few lines back has nothing to resolve
    against, and the model has to guess fresh every batch instead of
    staying consistent with what came right before it. 0 disables this.

    context_window_ahead: same idea, forward instead of back -- how many
    lines immediately AFTER this batch (raw, untranslated) get shown for
    context only. A line ending on a cliffhanger or an incomplete thought
    previously had nothing to resolve against going forward, only
    backward. 0 disables this.
    """
    target_lines = lines if force_retranslate else [ln for ln in lines if not ln.en.strip()]
    if not target_lines:
        if progress_cb:
            progress_cb(1.0)
        return lines, []

    character_names = character_names or {}
    context = {
        "drama_meta": drama_meta,
        "style_note": style_note,
        "novel_reference": novel_reference if getattr(engine, "supports_reference", False) else None,
        "locale": locale,
        "glossary_terms": glossary_terms,
        "style_guidelines": style_guidelines,
        # Regression fix: DeepL/Google both used to hardcode "zh" here
        # regardless of the drama's actual source language -- a Japanese
        # or Korean drama translated through either silently mistranslated.
        "source_language": (drama_meta or {}).get("source_language", "zh"),
        "ollama_num_ctx_override": ollama_num_ctx_override,
    }
    errors = []
    n_batches = (len(target_lines) + batch_size - 1) // batch_size
    for bi, start in enumerate(range(0, len(target_lines), batch_size)):
        if cancel_check_cb and cancel_check_cb():
            break
        batch = target_lines[start:start + batch_size]
        first_pos = next(i for i, ln in enumerate(lines) if ln.idx == batch[0].idx)
        if context_window > 0:
            # Recomputed each batch (not just once outside the loop) since
            # more lines have been translated -- including by this very
            # loop -- by the time later batches run.
            preceding = lines[max(0, first_pos - context_window):first_pos]
            context["recent_context"] = [(ln.zh, ln.en) for ln in preceding if ln.en.strip()]
        if context_window_ahead > 0:
            # batch[-1]'s own position, not first_pos + len(batch) - 1 --
            # that assumed the batch is a contiguous slice of `lines`,
            # which isn't true when force_retranslate=False and an
            # already-translated line sits in the middle of what would
            # otherwise be contiguous target lines: target_lines skips it,
            # so the batch is shorter than the span it covers in `lines`.
            # The stale arithmetic could land back inside the batch itself,
            # showing the model a line as "upcoming" (don't translate this)
            # while also asking it to translate that same line right now.
            last_pos = next(i for i, ln in enumerate(lines) if ln.idx == batch[-1].idx)
            batch_idxs = {ln.idx for ln in batch}
            upcoming = lines[last_pos + 1:last_pos + 1 + context_window_ahead]
            context["upcoming_lines"] = [ln.zh for ln in upcoming
                                         if ln.zh.strip() and ln.idx not in batch_idxs]
        context["speaker_labels"] = [character_names.get(ln.speaker) for ln in batch]
        context["line_ids"] = [getattr(ln, "id", None) for ln in batch]
        try:
            translations = call_with_backoff(
                lambda: engine.translate_batch([ln.zh for ln in batch], context)
            )
            if usage_cb and hasattr(engine, "last_usage"):
                usage_cb(engine.last_usage.get("input_tokens", 0), engine.last_usage.get("output_tokens", 0))
            if len(translations) != len(batch):
                errors.append({"batch_index": bi, "lines": [ln.idx for ln in batch],
                               "error": f"engine returned {len(translations)} translation(s) for "
                                        f"{len(batch)} line(s) -- left untranslated rather than "
                                        f"risk assigning a translation to the wrong line"})
            else:
                for ln, tr in zip(batch, translations):
                    ln.en = tr
        except Exception as e:
            redacted = redact_secrets(str(e))
            errors.append({"batch_index": bi, "lines": [ln.idx for ln in batch],
                           "error": redacted})
            import applog
            applog.get_logger().error(f"translate batch {bi} failed: {redacted}")
        if save_cb:
            save_cb(lines)
        if progress_cb:
            progress_cb((bi + 1) / n_batches)
    return lines, errors

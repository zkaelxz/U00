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


def build_llm_instructions(style_note: str, drama_meta: dict, novel_reference, locale: str = "en-US",
                            glossary_terms=None, style_guidelines: str = "", recent_context=None):
    meta_lines = []
    for label, key in [("Title", "title_en"), ("Chinese title", "title_zh"),
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

    instructions = (
        "You are translating a Chinese baihe (GL/yuri) audio drama into "
        "natural, idiomatic English subtitles. You will be given numbered "
        "lines of dialogue to translate in each request.\n\n"
        + (f"Drama metadata:\n{meta_block}\n\n" if meta_block else "")
        + locale_instruction
        + glossary_block
        + context_block
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
        + "- Return ONLY a JSON array of strings, one per input line, in the same order. "
        "No preamble, no markdown fences, no commentary."
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
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(zh_lines))
        resp = self.client.messages.create(
            model=self.model, max_tokens=4000, system=blocks,
            messages=[{"role": "user", "content": "Translate these lines:\n\n" + numbered}],
        )
        if hasattr(resp, "usage"):
            self.last_usage = {
                "input_tokens": getattr(resp.usage, "input_tokens", 0),
                "output_tokens": getattr(resp.usage, "output_tokens", 0),
            }
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return _parse_json_array(text, len(zh_lines))


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
        )
        novel_reference = context.get("novel_reference")
        system_text = instructions
        if novel_reference and novel_reference.strip():
            system_text += ("\n\nREFERENCE NOVEL TRANSLATION (authoritative for THIS drama "
                             "only):\n\n" + novel_reference.strip())
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(zh_lines))
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": "Translate these lines:\n\n" + numbered},
            ],
        )
        if hasattr(resp, "usage") and resp.usage:
            self.last_usage = {
                "input_tokens": getattr(resp.usage, "prompt_tokens", 0),
                "output_tokens": getattr(resp.usage, "completion_tokens", 0),
            }
        text = resp.choices[0].message.content.strip()
        return _parse_json_array(text, len(zh_lines))


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

    def __init__(self, api_key: str, model: str = "gemini-flash-lite-latest"):
        self.api_key = api_key
        self.model = model
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, zh_lines, context: dict):
        import requests
        instructions, _ = build_llm_instructions(
            context.get("style_note", ""), context.get("drama_meta", {}),
            context.get("novel_reference"), locale=context.get("locale", "en-US"),
            glossary_terms=context.get("glossary_terms"),
            style_guidelines=context.get("style_guidelines", ""),
            recent_context=context.get("recent_context"),
        )
        novel_reference = context.get("novel_reference")
        if novel_reference and novel_reference.strip():
            instructions += ("\n\nREFERENCE NOVEL TRANSLATION (authoritative for THIS drama "
                              "only):\n\n" + novel_reference.strip())
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(zh_lines))
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        resp = requests.post(url, params={"key": self.api_key}, json={
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"parts": [{"text": "Translate these lines:\n\n" + numbered}]}],
        })
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usageMetadata") or {}
        self.last_usage = {
            "input_tokens": usage.get("promptTokenCount", 0),
            "output_tokens": usage.get("candidatesTokenCount", 0),
        }
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return _parse_json_array(text, len(zh_lines))


# ---------------------------------------------------------------------------
# DeepL -- fast, cheap, pure MT (no reference-novel awareness)
# ---------------------------------------------------------------------------

class DeepLEngine:
    name = "deepl"
    supports_reference = False

    def __init__(self, api_key: str):
        import deepl
        self.translator = deepl.Translator(api_key)

    def translate_batch(self, zh_lines, context: dict):
        results = self.translator.translate_text(
            zh_lines, source_lang="ZH", target_lang="EN-US"
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
        resp = requests.post(url, params={"key": self.api_key}, json={
            "q": zh_lines, "source": "zh", "target": "en", "format": "text",
        })
        resp.raise_for_status()
        data = resp.json()
        return [t["translatedText"] for t in data["data"]["translations"]]


def tag_speakers_llm(zh_chunks, engine, known_characters=None, batch_size: int = 15):
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
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(batch))
        prompt = (
            "For each numbered chunk of Chinese novel text below, identify who is "
            "speaking. If it's dialogue attributed to a specific character, return "
            "that character's name (romanized consistently). If it's narration/"
            "description with no speaking character, return 'Narrator'. "
            f"Known characters so far: {known}. Prefer reusing a known name over "
            "inventing a new one when it's clearly the same person.\n\n"
            "Return ONLY a JSON array of strings, one per chunk, in order. "
            "No preamble, no markdown fences.\n\n" + numbered
        )
        # Reuse whichever engine's underlying client is available for a raw completion.
        if hasattr(engine, "client") and hasattr(engine.client, "messages"):
            resp = engine.client.messages.create(
                model=engine.model, max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
        elif hasattr(engine, "client"):
            resp = engine.client.chat.completions.create(
                model=engine.model, messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content.strip()
        else:
            text = "[]"
        batch_labels = _parse_json_array(text, len(batch))
        labels.extend(batch_labels)
        if len(labels) < start + len(batch):
            labels.extend(["Narrator"] * (start + len(batch) - len(labels)))
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


def rewrite_for_pacing_llm(lines_to_fix, engine, batch_size: int = 15):
    """For lines flagged as too long to say in their time slot: asks
    the LLM to rewrite them more concisely while preserving meaning,
    so the dub actually fits. Only touches .en; leaves .zh untouched.
    lines_to_fix: list of Line objects (already has .en set)."""
    if not getattr(engine, "supports_reference", False) or not lines_to_fix:
        return lines_to_fix
    for start in range(0, len(lines_to_fix), batch_size):
        batch = lines_to_fix[start:start + batch_size]
        numbered = "\n".join(f"{i+1}. {ln.en}" for i, ln in enumerate(batch))
        prompt = (
            "These English subtitle lines need to be shortened so they can be spoken "
            "naturally within their time slot. Rewrite each one more concisely -- cut "
            "filler words, tighten phrasing -- while keeping the same meaning and tone. "
            "Return ONLY a JSON array of strings, one per line, in order. No preamble, "
            "no markdown fences.\n\n" + numbered
        )
        if hasattr(engine, "client") and hasattr(engine.client, "messages"):
            resp = engine.client.messages.create(
                model=engine.model, max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
        elif hasattr(engine, "client"):
            resp = engine.client.chat.completions.create(
                model=engine.model, messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content.strip()
        else:
            continue
        rewritten = _parse_json_array(text, len(batch))
        for ln, new_text in zip(batch, rewritten):
            if new_text.strip():
                ln.en = new_text.strip()
    return lines_to_fix


def check_consistency_llm(lines, engine, batch_size: int = 60):
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
            if hasattr(engine, "client") and hasattr(engine.client, "messages"):
                resp = call_with_backoff(lambda: engine.client.messages.create(
                    model=engine.model, max_tokens=2000,
                    messages=[{"role": "user", "content": prompt}],
                ))
                text = "".join(b.text for b in resp.content if b.type == "text").strip()
            elif hasattr(engine, "client"):
                resp = call_with_backoff(lambda: engine.client.chat.completions.create(
                    model=engine.model, messages=[{"role": "user", "content": prompt}],
                ))
                text = resp.choices[0].message.content.strip()
            else:
                continue
        except Exception:
            continue  # a check failing shouldn't block anything -- just skip that batch
        batch_issues = _parse_json_array(text, 0)
        if isinstance(batch_issues, list):
            issues.extend(i for i in batch_issues if isinstance(i, dict) and i.get("term"))
    return issues


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
        )
        novel_reference = context.get("novel_reference")
        system_text = instructions
        if novel_reference and novel_reference.strip():
            system_text += ("\n\nREFERENCE NOVEL TRANSLATION (authoritative for THIS drama "
                             "only):\n\n" + novel_reference.strip())
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(zh_lines))
        resp = requests.post(f"{self.base_url}/api/chat", json={
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_text},
                {"role": "user", "content": "Translate these lines:\n\n" + numbered},
            ],
            "stream": False,
        })
        resp.raise_for_status()
        text = resp.json()["message"]["content"].strip()
        return _parse_json_array(text, len(zh_lines))


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
    "test_offline": TestOfflineEngine,
    "claude": ClaudeEngine,
    "deepseek": DeepSeekEngine,
    "gemini": GeminiEngine,
    "deepl": DeepLEngine,
    "google": GoogleEngine,
    "ollama": OllamaEngine,
    "libretranslate": LibreTranslateEngine,
}

ENGINE_NOTES = {
    "test_offline": "FREE dry run -- no API key, no network, no cost. Produces obvious [TEST] placeholder text so you can verify the whole pipeline works before spending anything. Not a real translation.",
    "claude": "Best for tone/character voice, supports novel reference + prompt caching.",
    "deepseek": "Far and away the cheapest capable option -- roughly 5-10 cents per drama on V4 Flash, and its prompt caching makes the repeated glossary/style block nearly free. Strong on Chinese, supports novel reference. OpenAI-compatible API.",
    "gemini": "Cheap and strong on Chinese/Japanese, close to DeepSeek pricing on Flash-Lite. Supports novel reference. Google model naming/pricing changes often -- double check GEMINI_MODELS if a run starts failing.",
    "deepl": "Fast, natural phrasing, but no reference-novel awareness -- pure MT.",
    "google": "Broadest language coverage, cheapest at scale, no reference-novel awareness.",
    "ollama": "Runs models locally via Ollama. No per-token billing, but quality depends on your hardware -- a usable model needs meaningful RAM/VRAM. Supports novel reference.",
    "libretranslate": "Self-hosted LibreTranslate or LTEngine. No per-word cost once running, but you host it: LibreTranslate needs ~8GB RAM for full language support, and LTEngine's best model wants a 24GB GPU. The hosted libretranslate.com API is PAID. Pure MT, no reference-novel awareness.",
}


def get_engine(engine_name: str, api_key: str = None, model: str = None):
    cls = ENGINES[engine_name]
    if model:
        return cls(api_key, model)
    return cls(api_key)


def translate_lines_with_engine(lines, engine, drama_meta: dict, batch_size: int = 20,
                                 style_note: str = "", novel_reference=None, progress_cb=None,
                                 save_cb=None, force_retranslate: bool = False,
                                 locale: str = "en-US", glossary_terms=None, usage_cb=None,
                                 style_guidelines: str = "", cancel_check_cb=None,
                                 context_window: int = 6):
    """cancel_check_cb: optional callable returning True if the run should
    stop cooperatively between batches -- e.g. background_jobs.is_cancel_requested,
    so a background translation job can be stopped safely (rather than
    racing a destructive action like a full library reset against a
    thread that's still writing)."""
    """lines: list of objects with .zh and .en attributes (mutated in place).

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
    """
    target_lines = lines if force_retranslate else [ln for ln in lines if not ln.en.strip()]
    if not target_lines:
        if progress_cb:
            progress_cb(1.0)
        return lines, []

    context = {
        "drama_meta": drama_meta,
        "style_note": style_note,
        "novel_reference": novel_reference if getattr(engine, "supports_reference", False) else None,
        "locale": locale,
        "glossary_terms": glossary_terms,
        "style_guidelines": style_guidelines,
    }
    errors = []
    n_batches = (len(target_lines) + batch_size - 1) // batch_size
    for bi, start in enumerate(range(0, len(target_lines), batch_size)):
        if cancel_check_cb and cancel_check_cb():
            break
        batch = target_lines[start:start + batch_size]
        if context_window > 0:
            # Recomputed each batch (not just once outside the loop) since
            # more lines have been translated -- including by this very
            # loop -- by the time later batches run.
            first_pos = next(i for i, ln in enumerate(lines) if ln.idx == batch[0].idx)
            preceding = lines[max(0, first_pos - context_window):first_pos]
            context["recent_context"] = [(ln.zh, ln.en) for ln in preceding if ln.en.strip()]
        try:
            translations = call_with_backoff(
                lambda: engine.translate_batch([ln.zh for ln in batch], context)
            )
            if usage_cb and hasattr(engine, "last_usage"):
                usage_cb(engine.last_usage.get("input_tokens", 0), engine.last_usage.get("output_tokens", 0))
            for ln, tr in zip(batch, translations):
                ln.en = tr
        except Exception as e:
            errors.append({"batch_index": bi, "lines": [ln.idx for ln in batch], "error": str(e)})
        if save_cb:
            save_cb(lines)
        if progress_cb:
            progress_cb((bi + 1) / n_batches)
    return lines, errors

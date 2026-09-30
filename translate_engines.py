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
    # Opt-in only, never the default: confirmed as a stable model id and
    # priced at ai.google.dev in September 2026, but there's no CJK-specific
    # quality benchmark for it yet, so no quality claim is made here.
    "gemini-3.1-flash-lite": "3.1 Flash-Lite -- cheaper, lighter tier (no quality claim yet)",
}

PRICING_PER_MILLION_TOKENS = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-opus-4-8": {"input": 15.0, "output": 75.0},
    # Step 9e: corrected against api-docs.deepseek.com/quick_start/pricing's
    # raw page source (checked directly, not a summarized fetch) -- these
    # previous flat figures didn't match DeepSeek's real pricing structure
    # at all, which splits every price by peak/off-peak (peak: 01:00-04:00
    # and 06:00-10:00 UTC, Mon-Fri, excluding Chinese holidays; off-peak is
    # exactly half) and separately by cache hit/miss. Priced here at PEAK,
    # CACHE-MISS rates -- the most expensive real case -- since this is a
    # single flat estimate with no time-of-day or cache-hit awareness of
    # its own; same "never undercut a spending cap" direction as
    # CACHE_READ_PRICE_FACTOR below. A DeepSeek Bulk job (schedule_offpeak_
    # translation) always actually runs off-peak, so its real cost will
    # typically come in under this estimate -- a safe direction to be
    # wrong in, never the reverse. Real cache-hit input price is far
    # cheaper than this (Flash: $0.006 peak / $0.003 off-peak per 1M vs.
    # the $0.3/$0.15 cache-miss prices below; Pro: $0.044/$0.022 vs.
    # $1.32/$0.66) -- CACHE_READ_PRICE_FACTOR's flat 10% already
    # over-estimates that case too, conservatively.
    "deepseek-v4-flash": {"input": 0.3, "output": 1.2},
    "deepseek-v4-pro": {"input": 1.32, "output": 3.96},
    # Legacy aliases, retired July 2026 -- kept so old usage_log rows still
    # cost out instead of silently reporting $0.
    "deepseek-chat": {"input": 0.28, "output": 0.42},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "gemini-flash-lite-latest": {"input": 0.30, "output": 2.50},
    "gemini-flash-latest": {"input": 0.75, "output": 3.75},
    "gemini-pro-latest": {"input": 2.0, "output": 12.0},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
}


# Cache reads are billed at a fraction of the input price: 10% on Claude,
# less than that on Gemini and DeepSeek. One conservative figure for all
# of them -- a spending cap should never be undercut by an estimate that
# came out cheaper than the real bill.
CACHE_READ_PRICE_FACTOR = 0.1
# Claude charges 25% extra on the tokens it writes to the cache.
CACHE_WRITE_PRICE_FACTOR = 1.25


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """input_tokens is the whole prompt; cache_read_tokens/cache_write_tokens
    are the parts of it that were served from / written to a prompt cache."""
    rates = PRICING_PER_MILLION_TOKENS.get(model)
    if not rates:
        return 0.0
    uncached = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    effective_input = (uncached + cache_read_tokens * CACHE_READ_PRICE_FACTOR
                       + cache_write_tokens * CACHE_WRITE_PRICE_FACTOR)
    return (effective_input / 1_000_000 * rates["input"]) + (output_tokens / 1_000_000 * rates["output"])


# DeepL and Google Cloud Translation bill per character sent, not per
# token -- unlike every other engine here. USD per million characters,
# checked against each provider's own pricing page in September 2026;
# same "approximation, not a bill" caveat as PRICING_PER_MILLION_TOKENS
# above. Google: Cloud Translation Basic (v2), $20/million characters
# (the first 500k/month free tier isn't modeled here). DeepL: its current
# per-character overage rate ($25-27.50/million depending on plan) --
# priced at the higher end, same "never undercut a spending cap" direction
# as CACHE_READ_PRICE_FACTOR above; a plan's own monthly base fee isn't
# modeled here either.
PRICING_PER_MILLION_CHARACTERS = {
    "google": 20.0,
    "deepl": 27.5,
}


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


# Real quota headers Gemini's API sometimes returns (confirmed via Google's
# own docs and independent API guides) -- reliable on a 429 response, not
# guaranteed on every successful one depending on the installed SDK version.
_GEMINI_RATE_HEADERS = {
    "limit_requests": "x-ratelimit-limit-requests",
    "remaining_requests": "x-ratelimit-remaining-requests",
    "remaining_tokens": "x-ratelimit-remaining-tokens",
    "reset_requests": "x-ratelimit-reset-requests",
}


def _parse_gemini_rate_headers(headers) -> dict:
    """{} if none of the expected headers are present on this response --
    the caller falls back to its own self-tracked RPM/RPD/TPM counts."""
    out = {}
    for field, header_name in _GEMINI_RATE_HEADERS.items():
        v = headers.get(header_name) if headers else None
        if v is not None:
            out[field] = v
    return out


def gemini_rate_status_text(engine) -> str:
    """The small persistent note shown near the engine picker / job
    progress while a free-tier Gemini engine is active. None if there's
    nothing to show yet (no request made this session, or not a free-tier
    Gemini engine)."""
    status = getattr(engine, "rate_status", None)
    if not status:
        return None
    note = (f"{status['rpm_used']}/{status['rpm_limit']} req/min · "
            f"{status['rpd_used']}/{status['rpd_limit']} req/day · "
            f"{status['tpm_used']:,}/{status['tpm_limit']:,} tokens/min")
    if status["source"] == "header":
        note += " (Google's own reported quota)"
    limit_header = status.get("limit_requests")
    if limit_header is not None:
        try:
            if int(limit_header) != status["rpm_limit"]:
                note += " -- ⚠️ Google's real limit differs from this app's table; may need updating"
        except (TypeError, ValueError):
            pass
    return note


def progress_message_with_rate_status(engine, frac: float, base: str = None) -> str:
    """A job's progress message, with the free-tier Gemini rate-status note
    appended when there's one to show. `base` defaults to the plain
    "Translating..." text every other engine already shows."""
    message = base or f"Translating... {frac*100:.0f}%"
    if getattr(engine, "free_tier", False):
        note = gemini_rate_status_text(engine)
        if note:
            return f"{message} — {note}"
    return message


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
    # Hugging Face user access tokens: hf_ + 34 letters/digits today. The
    # 20-char floor keeps ordinary identifiers like "hf_model" untouched.
    re.compile(r'\bhf_[A-Za-z0-9]{20,}\b'),
    # Groq keys: gsk_ + ~52 letters/digits.
    re.compile(r'\bgsk_[A-Za-z0-9]{20,}\b'),
    # DeepL keys: a UUID, with ":fx" on Free-plan keys. A bare UUID is
    # only redacted with the ":fx" suffix or after "DeepL-Auth-Key", so
    # the app's own UUID ids stay readable in logs.
    re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:fx\b',
               re.IGNORECASE),
    re.compile(r'(DeepL-Auth-Key\s+)[A-Za-z0-9:\-]{10,}', re.IGNORECASE),
    # Google OAuth client secrets (sign-in, step 134): GOCSPX- + ~28 chars.
    re.compile(r'\bGOCSPX-[A-Za-z0-9_-]{10,}'),
    # Discord webhook URLs (Step 44): the id/token path is the secret.
    re.compile(r'(discord(?:app)?\.com/api/(?:v\d+/)?webhooks/)[^\s"\'<>]+', re.IGNORECASE),
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


def build_project_instructions_block(drama_meta: dict) -> str:
    """The persisted per-series and per-drama instructions (Step 12e), as
    one prompt block -- series first, then the drama's own, which is more
    specific and so gets the last word. Empty string when neither is set."""
    parts = []
    for label, key in [("For every drama in this series", "series_instructions"),
                       ("For this drama specifically", "project_instructions")]:
        text = (drama_meta.get(key) or "").strip()
        if text:
            parts.append(f"{label}:\n{text}")
    if not parts:
        return ""
    return ("- Project instructions from the person running this translation -- follow "
            "them unless they conflict with the output format below:\n"
            + "\n".join(parts) + "\n")


def build_previous_episode_summary_block(drama_meta: dict) -> str:
    """Step 74: the immediately preceding episode's stored running summary
    (db._DRAMA_SELECT's previous_episode_summary, resolved from
    dramas.episode_number -- see its own docstring), as a fixed prompt
    block. Only ever the ONE immediately-preceding episode's summary, not
    the whole prior episode's transcript and not every earlier episode's
    summary concatenated -- a fixed, small, once-per-episode context cost
    that doesn't grow with how many episodes came before it. Empty string
    when there's no series, no episode ordering, or no earlier episode
    yet."""
    text = (drama_meta.get("previous_episode_summary") or "").strip()
    if not text:
        return ""
    return ("- Continuity from the previous episode of this series (a fixed summary, "
            "not the full prior transcript) -- use it to resolve callbacks, pronouns, "
            "or references to earlier events; don't restate it or mention it directly "
            "in your translation:\n" + text + "\n")


# Step 54: markers in a drama's own (freeform) genre field that mean "yes,
# this really is baihe/yuri content" -- matched as whole words so a genre
# like "tangled romance" doesn't false-positive on "gl". An unset/blank
# genre still defaults to the baihe framing below (this app's original,
# and still primary, use case) -- only an explicit, DIFFERENT genre tag
# turns the framing off.
_BAIHE_GENRE_MARKERS = re.compile(r"\b(baihe|yuri|gl)\b", re.IGNORECASE)


def _wants_baihe_framing(drama_meta: dict) -> bool:
    genre = (drama_meta.get("genre") or "").strip()
    return not genre or bool(_BAIHE_GENRE_MARKERS.search(genre))


def build_llm_instructions(style_note: str, drama_meta: dict, locale: str = "en-US",
                            glossary_terms=None, style_guidelines: str = ""):
    """The STABLE part of every translation prompt for one drama/job --
    identical across all of its batches, so provider prompt caching
    (Claude cache_control, Gemini implicit caching, DeepSeek prefix
    caching) can hit on it. Anything that changes per batch belongs in
    build_batch_context() instead, which goes after this, in the user
    message.

    Step 12e: drama_meta's series_instructions (inherited by every drama
    in a series) and project_instructions (this drama only) -- both
    persisted, multi-line, per project -- go in as their own block, in
    addition to style_note (the single-line global Settings default),
    never replacing it."""
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

    source_language = drama_meta.get("source_language") or "zh"
    source_language_name = LANGUAGE_NAMES.get(source_language, "the source language")
    medium = _MEDIUM_DESCRIPTIONS.get(
        drama_meta.get("content_mode") or drama_meta.get("media_type"), "content")

    genre_framing = "baihe (GL/yuri) " if _wants_baihe_framing(drama_meta) else ""
    instructions = (
        f"You are translating {source_language_name} {genre_framing}{medium} into "
        "natural, idiomatic English subtitles. You will be given numbered lines to "
        "translate in each request, each optionally prefixed with the name of the "
        "character speaking it in [brackets] -- use that to get pronouns, honorifics, "
        "and register right, but never include the bracketed name itself in your "
        "translation.\n\n"
        + (f"Drama metadata:\n{meta_block}\n\n" if meta_block else "")
        + locale_instruction
        + glossary_block
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
        + build_project_instructions_block(drama_meta)
        + build_previous_episode_summary_block(drama_meta)
        + "- Return ONLY a JSON object mapping each line's number (as a string) to its "
        "translation, e.g. {\"1\": \"...\", \"2\": \"...\"} -- include EVERY number you "
        "were given, and no numbers you weren't. No preamble, no markdown fences, no "
        "commentary."
    )
    return instructions


def build_batch_context(recent_context=None, upcoming_lines=None) -> str:
    """The per-batch part of a translation prompt: how the lines just
    before this batch were translated, and the raw source of the lines
    just after it. Changes every batch, so it goes in the user message
    after the stable instructions -- never inside them, where it would
    break the cached prefix for everything after it.

    upcoming_lines: raw (untranslated) source text for the few lines
    immediately AFTER this batch, shown for context only -- the model is
    told explicitly not to translate them here. Fixes a real, one-sided
    gap: recent_context already showed how PRECEDING lines were
    translated, but nothing showed what comes next, so a line ending on
    a cliffhanger or an incomplete thought had no forward context to
    resolve against, only backward."""
    parts = []
    if recent_context:
        ctx_pairs = "\n".join(f"- {zh} -> {en}" for zh, en in recent_context)
        parts.append(
            "How the lines immediately before this batch were just translated "
            "(for continuity -- a pronoun, an ongoing topic, or a person referred "
            "to only by relation may depend on this). These are NOT part of what "
            f"you're translating now:\n{ctx_pairs}\n")
    if upcoming_lines:
        parts.append(
            "What's said immediately AFTER this batch, in the original language "
            "(for context only -- a line that ends on an unresolved thought or a "
            "cliffhanger may need this to translate correctly). Do NOT translate "
            "these here, they'll be translated in a later batch:\n"
            + "\n".join(f"- {t}" for t in upcoming_lines) + "\n")
    return "\n".join(parts)


def build_standalone_instructions(source_language: str, target_language: str) -> str:
    """Step 26b's system prompt for the standalone translate tool -- plain
    prose translation with no subtitle formatting, genre assumption, or
    drama metadata, unlike build_llm_instructions above (kept exactly as
    it was for drama translation; this is a separate prompt, not a
    variant of it)."""
    source_name = "English" if source_language == "en" else LANGUAGE_NAMES.get(source_language, source_language)
    target_name = "English" if target_language == "en" else LANGUAGE_NAMES.get(target_language, target_language)
    return (
        f"You are translating text from {source_name} to {target_name}. You will be "
        "given numbered chunks of text to translate in each request.\n\n"
        "Rules:\n"
        "- Translate naturally and idiomatically -- favor clear, readable "
        f"{target_name} over a literal word-for-word rendering.\n"
        "- Preserve the original meaning, tone, and register as closely as natural "
        "phrasing allows.\n"
        "- Keep names, terms, and phrasing consistent across chunks.\n"
        "- Preserve paragraph breaks within a chunk.\n"
        "- Return ONLY a JSON object mapping each chunk's number (as a string) to its "
        "translation, e.g. {\"1\": \"...\", \"2\": \"...\"} -- include EVERY number you "
        "were given, and no numbers you weren't. No preamble, no markdown fences, no "
        "commentary."
    )


# Step 50: a reference novel at or under this size is sent whole and
# unchanged (this is why every short-reference prompt/test is unaffected);
# a longer one is trimmed to the passages most likely to matter for THIS
# batch instead of the whole file, unbounded, every time.
NOVEL_REFERENCE_BUDGET_CHARS = 6000


def _select_relevant_novel_passages(novel_reference: str, batch_source_lines: list = None,
                                    speaker_labels: list = None, glossary_terms: list = None,
                                    budget_chars: int = NOVEL_REFERENCE_BUDGET_CHARS) -> str:
    """Bounded, relevance-based excerpt of `novel_reference` for one
    translation batch (Step 50) -- replaces sending the whole reference,
    unbounded, to every batch regardless of the batch's actual content.

    The reference novel is itself an existing English translation (see
    tabs/workspace_tab.py's "Upload novel translation" uploader), so
    relevance can't be scored by keyword-matching it against the batch's
    own source-language text directly. Instead it's scored against query
    terms this batch already has in English: each line's already-resolved
    speaker name, plus the English side of any glossary term whose
    source-language form appears in this batch's source lines. The
    highest-scoring paragraphs are kept, in their original order (so the
    excerpt still reads as continuous prose), up to budget_chars.

    Falls back to the reference's own beginning, still bounded, when no
    query term matches anywhere -- there's no relevance signal to rank by
    in that case, but the unbounded-whole-file problem this step exists
    to fix still needs fixing regardless."""
    if not novel_reference:
        return ""
    if len(novel_reference) <= budget_chars:
        return novel_reference

    terms = {label for label in (speaker_labels or []) if label}
    batch_text = "".join(batch_source_lines or [])
    for term in (glossary_terms or []):
        original, translation = term.get("term_original"), term.get("term_translation")
        if original and translation and original in batch_text:
            terms.add(translation)

    paragraphs = [p for p in re.split(r"\n\s*\n", novel_reference) if p.strip()]
    if not paragraphs:
        return novel_reference[:budget_chars]

    scored = []
    if terms:
        lowered_terms = [t.lower() for t in terms]
        for i, para in enumerate(paragraphs):
            lowered = para.lower()
            score = sum(lowered.count(t) for t in lowered_terms)
            if score > 0:
                scored.append((score, i, para))
        scored.sort(key=lambda x: (-x[0], x[1]))

    if not scored:
        return novel_reference[:budget_chars]

    picked, total = [], 0
    for _, i, para in scored:
        picked.append((i, para))
        total += len(para)
        if total >= budget_chars:
            break
    picked.sort(key=lambda x: x[0])
    return "\n\n".join(p for _, p in picked)[:budget_chars]


def build_stable_prompt(context: dict):
    """(instructions, novel_reference_block) -- the two stable pieces of
    a translation prompt, in cache order: instructions (style guide and
    glossary included), then the reference novel. novel_reference_block
    is "" when there's no reference.

    context["standalone"] routes to Step 26b's generic, non-drama prompt
    instead (build_standalone_instructions) -- the only place that flag
    is checked, so every existing drama-translation call site (which
    never sets it) is completely unaffected."""
    if context.get("standalone"):
        instructions = build_standalone_instructions(
            context.get("source_language", "zh"), context.get("target_language", "en"))
        return instructions, ""
    instructions = build_llm_instructions(
        context.get("style_note", ""), context.get("drama_meta", {}),
        locale=context.get("locale", "en-US"),
        glossary_terms=context.get("glossary_terms"),
        style_guidelines=context.get("style_guidelines", ""),
    )
    novel_reference = context.get("novel_reference")
    novel_block = ""
    if novel_reference and novel_reference.strip():
        excerpt = _select_relevant_novel_passages(
            novel_reference.strip(), batch_source_lines=context.get("batch_source_lines"),
            speaker_labels=context.get("speaker_labels"), glossary_terms=context.get("glossary_terms"))
        # Step 50 item 3: recorded on the per-batch context (same pattern as
        # speaker_labels/line_ids below) so "why did this line translate
        # this way" can inspect exactly what reference text this batch
        # actually saw, not just that a reference existed.
        context["novel_reference_excerpt_used"] = excerpt
        novel_block = ("REFERENCE NOVEL TRANSLATION (authoritative for THIS drama only):\n\n"
                       + excerpt)
    return instructions, novel_block


def build_stable_system_text(context: dict) -> str:
    """The stable prefix as one string, for engines with a single system
    field (DeepSeek, Gemini, Ollama)."""
    instructions, novel_block = build_stable_prompt(context)
    return instructions + ("\n\n" + novel_block if novel_block else "")


def build_claude_system_blocks(context: dict) -> list:
    """The stable prefix as Claude system blocks, with cache_control on
    the last one so the whole stable part is cached, not just the
    reference novel. A prefix under the model's minimum cacheable length
    simply isn't cached -- no error."""
    instructions, novel_block = build_stable_prompt(context)
    blocks = [{"type": "text", "text": instructions}]
    if novel_block:
        blocks.append({"type": "text", "text": novel_block})
    blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def build_batch_user_message(context: dict, numbered: str) -> str:
    batch_ctx = build_batch_context(context.get("recent_context"), context.get("upcoming_lines"))
    return (batch_ctx + "\n" if batch_ctx else "") + "Translate these lines:\n\n" + numbered


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


def _id_keyed_batch_request(ids: list, build_batch_text, call_model_fn, max_retries: int = 1,
                            engine_name: str = None) -> dict:
    """The actual id-keyed request/parse/retry-missing loop shared by
    _request_translations_with_retry below (every engine's own
    translate_batch) and Step 7's reflect_translate_batch (three passes,
    each with its own prompt shape). build_batch_text(batch_ids) returns
    the prompt-ready text for just those ids -- a retry only re-sends
    whichever ids came back missing, not the whole batch. Returns
    {str(id): value} for whichever ids actually came back with a usable
    value; a still-missing id after max_retries just isn't a key here,
    same contract _parse_id_keyed_json already documents.

    engine_name (Step 31): opts into the best-effort soft-refusal text
    heuristic (see _detect_soft_refusal_text) -- deliberately not passed
    by reflect_translate_batch's own three calls, since Step 31 is scoped
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
        parsed = _parse_id_keyed_json(text, remaining_ids)
        if engine_name and text.strip() and not parsed:
            # A real structural refusal signal (stop_reason/refusal) is
            # already checked -- and raises directly -- inside each
            # engine's own translate_batch, before the text ever reaches
            # here. Reaching here with non-empty text that parsed to
            # nothing means no such signal was available, so this is the
            # lower-confidence, best-effort fallback (Step 31 item 2).
            soft_reason = _detect_soft_refusal_text(text)
            if soft_reason:
                raise ContentModerationBlocked(engine_name, soft_reason)
        result_map.update(parsed)
        remaining_ids = [i for i in ids if str(i) not in result_map]
    return result_map


def _request_translations_with_retry(zh_lines: list, speaker_names, call_model_fn, max_retries: int = 1,
                                     line_ids=None, engine_name: str = None):
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

    def build_batch_text(batch_ids):
        batch_lines = [zh_lines[pos[i]] for i in batch_ids]
        batch_names = ([speaker_names[pos[i]] for i in batch_ids] if speaker_names else None)
        return _build_numbered_lines(batch_ids, batch_lines, batch_names)

    result_map = _id_keyed_batch_request(ids, build_batch_text, call_model_fn, max_retries,
                                         engine_name=engine_name)
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
# Step 31: content-moderation refusal detection
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
# (Step 31 item 2): only reached when a batch's raw response text is
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
        self.last_usage = _empty_usage()

    def build_request_params(self, context: dict, numbered: str) -> dict:
        """One translation request's Messages API params -- shared by
        translate_batch and bulk mode's Message Batches submission, so a
        bulk request is byte-for-byte the prompt a live one would send."""
        return {
            "model": self.model, "max_tokens": 4000,
            "system": build_claude_system_blocks(context),
            "messages": [{"role": "user", "content": build_batch_user_message(context, numbered)}],
        }

    def translate_batch(self, zh_lines, context: dict):
        self.last_usage = _empty_usage()

        def call_model(numbered):
            resp = self.client.messages.create(**self.build_request_params(context, numbered))
            _add_usage(self.last_usage, claude_usage(getattr(resp, "usage", None)))
            # Step 31: Claude's Messages API sets stop_reason to "refusal"
            # when it declines a request on content-policy grounds -- a
            # real, documented signal, not a guess. Checked before ever
            # falling through to the soft-refusal text heuristic.
            if getattr(resp, "stop_reason", None) == "refusal":
                raise ContentModerationBlocked("claude", "refusal")
            return "".join(b.text for b in resp.content if b.type == "text").strip()

        return _request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"), engine_name="claude")


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
        self.last_usage = _empty_usage()

    def translate_batch(self, zh_lines, context: dict):
        system_text = build_stable_system_text(context)
        self.last_usage = _empty_usage()

        def call_model(numbered):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": build_batch_user_message(context, numbered)},
                ],
            )
            usage = getattr(resp, "usage", None)
            if usage:
                # DeepSeek reports its automatic prefix-cache hits as
                # prompt_cache_hit_tokens; the OpenAI-standard field is
                # prompt_tokens_details.cached_tokens. Either way it's a
                # subset of prompt_tokens.
                details = getattr(usage, "prompt_tokens_details", None)
                cached = (getattr(usage, "prompt_cache_hit_tokens", None)
                          or getattr(details, "cached_tokens", None) or 0)
                _add_usage(self.last_usage, {
                    "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                    "cache_read_tokens": cached or 0})
            message = resp.choices[0].message
            # Step 31: the OpenAI-compatible refusal shape -- content is
            # None/empty and a separate `refusal` field explains why,
            # rather than the requested translation. A real, documented
            # signal, not a guess; guards the bare .content.strip() this
            # replaced, which crashed with a raw AttributeError on this
            # exact shape (None has no .strip()).
            refusal = getattr(message, "refusal", None)
            if not message.content and refusal:
                raise ContentModerationBlocked("deepseek", refusal)
            return (message.content or "").strip()

        return _request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"), engine_name="deepseek")


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
        self.last_usage = _empty_usage()
        # Free-tier Gemini keys hard-error past a per-model RPM/RPD limit,
        # plus a shared TPM limit across every model -- self-pacing
        # client-side is cheaper than handling 429s. Paid keys have no such
        # limit, so this only applies here.
        self.free_tier = free_tier
        self._free_tier_request_times = []       # 60s window, for RPM
        self._free_tier_daily_request_times = []  # 24h window, for RPD
        self._free_tier_token_counts = []         # [(timestamp, tokens)], 60s window, for TPM
        # Populated after the first request when free_tier is set --
        # gemini_rate_status_text() reads this to show the small
        # persistent rate-status note near the engine picker / job progress.
        self.rate_status = None

    def _prune_free_tier_windows(self, now):
        self._free_tier_request_times = [
            t for t in self._free_tier_request_times if now - t < 60]
        self._free_tier_daily_request_times = [
            t for t in self._free_tier_daily_request_times if now - t < 86400]
        self._free_tier_token_counts = [
            (t, n) for t, n in self._free_tier_token_counts if now - t < 60]

    def _throttle_for_free_tier(self):
        if not self.free_tier:
            return
        limits = gemini_free_tier_limits_for(self.model)
        now = time.monotonic()
        self._prune_free_tier_windows(now)

        # Whichever ceiling is closest to being hit decides how long to
        # wait -- RPM and TPM (60s windows) or RPD (24h window). The
        # upcoming request's own token count isn't known yet, so TPM can
        # only react to what past requests have already used.
        wait = 0.0
        if len(self._free_tier_request_times) >= limits["rpm"]:
            wait = max(wait, 60 - (now - self._free_tier_request_times[0]))
        if len(self._free_tier_daily_request_times) >= limits["rpd"]:
            wait = max(wait, 86400 - (now - self._free_tier_daily_request_times[0]))
        tokens_in_window = sum(n for _, n in self._free_tier_token_counts)
        if self._free_tier_token_counts and tokens_in_window >= GEMINI_FREE_TIER_TPM:
            wait = max(wait, 60 - (now - self._free_tier_token_counts[0][0]))

        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
            self._prune_free_tier_windows(now)

        self._free_tier_request_times.append(now)
        self._free_tier_daily_request_times.append(now)

    def _update_rate_status(self, headers, usage: dict):
        """Refreshes self.rate_status after a request -- self-tracked
        RPM/RPD/TPM counts always, plus Google's own reported quota
        headers layered on top when the response included them (reliable
        on a 429, not guaranteed on every 200 depending on SDK version)."""
        now = time.monotonic()
        self._free_tier_token_counts.append(
            (now, usage.get("input_tokens", 0) + usage.get("output_tokens", 0)))
        self._prune_free_tier_windows(now)
        limits = gemini_free_tier_limits_for(self.model)
        header_info = _parse_gemini_rate_headers(headers)
        status = {
            "source": "header" if header_info else "estimated",
            "rpm_used": len(self._free_tier_request_times),
            "rpm_limit": limits["rpm"],
            "rpd_used": len(self._free_tier_daily_request_times),
            "rpd_limit": limits["rpd"],
            "tpm_used": sum(n for _, n in self._free_tier_token_counts),
            "tpm_limit": GEMINI_FREE_TIER_TPM,
        }
        status.update(header_info)
        self.rate_status = status

    def build_request_body(self, context: dict, numbered: str) -> dict:
        """One translation request's generateContent body -- shared by
        translate_batch and bulk mode's Batch API submission."""
        return {
            "systemInstruction": {"parts": [{"text": build_stable_system_text(context)}]},
            "contents": [{"parts": [{"text": build_batch_user_message(context, numbered)}]}],
        }

    def translate_batch(self, zh_lines, context: dict):
        import requests
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        self.last_usage = _empty_usage()

        def call_model(numbered):
            self._throttle_for_free_tier()
            resp = requests.post(url, headers={"x-goog-api-key": self.api_key},
                                 json=self.build_request_body(context, numbered), timeout=120)
            resp.raise_for_status()
            data = resp.json()
            usage = gemini_usage(data.get("usageMetadata"))
            _add_usage(self.last_usage, usage)
            if self.free_tier:
                self._update_rate_status(resp.headers, usage)
            # Step 31: a real safety block returns either no candidates at
            # all (blocked before generation even started -- the reason is
            # in promptFeedback.blockReason) or a candidate whose
            # finishReason is SAFETY/PROHIBITED_CONTENT with no content --
            # both shapes previously raised a bare KeyError/IndexError from
            # the raw indexing below. Real, documented finishReason values,
            # not a guess.
            candidates = data.get("candidates")
            if not candidates:
                reason = (data.get("promptFeedback") or {}).get(
                    "blockReason", "blocked with no reason given")
                raise ContentModerationBlocked("gemini", reason)
            finish_reason = candidates[0].get("finishReason")
            if finish_reason in ("SAFETY", "PROHIBITED_CONTENT"):
                raise ContentModerationBlocked("gemini", finish_reason)
            return candidates[0]["content"]["parts"][0]["text"].strip()

        return _request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"), engine_name="gemini")


# ---------------------------------------------------------------------------
# DeepL -- fast, cheap, pure MT (no reference-novel awareness)
# ---------------------------------------------------------------------------

# DeepL's own source-language codes -- confirmed via direct testing that
# hardcoding "ZH" regardless of the drama's actual source language was a
# real bug: a Japanese or Korean drama translated through DeepL was
# silently telling DeepL its audio was Chinese the whole time.
_DEEPL_SOURCE_LANGS = {"zh": "ZH", "ja": "JA", "ko": "KO", "en": "EN"}
# DeepL's target codes -- separate table since English needs a regional
# variant as a target (EN-US) but not as a source (plain EN). Step 26b:
# added so the standalone translate tool can go English -> zh/ja/ko too,
# defaulting to "en" everywhere else keeps every existing drama call
# (which never sets target_language) landing on EN-US exactly as before.
_DEEPL_TARGET_LANGS = {"zh": "ZH", "ja": "JA", "ko": "KO", "en": "EN-US"}


class DeepLEngine:
    name = "deepl"
    supports_reference = False

    def __init__(self, api_key: str):
        import deepl
        self.translator = deepl.Translator(api_key)
        self.last_usage = _empty_usage()

    def translate_batch(self, zh_lines, context: dict):
        source_lang = _DEEPL_SOURCE_LANGS.get(context.get("source_language", "zh"), "ZH")
        target_lang = _DEEPL_TARGET_LANGS.get(context.get("target_language", "en"), "EN-US")
        results = self.translator.translate_text(
            zh_lines, source_lang=source_lang, target_lang=target_lang
        )
        if not isinstance(results, list):
            results = [results]
        # Step 25w: DeepL bills per character sent, not per token -- there
        # was previously no last_usage at all here, so the cost-cap system
        # could never see any spend from this engine. billed_characters is
        # the API's own real per-result count; fall back to the source
        # text's own length for an older SDK that doesn't expose it, since
        # that's what's billed in the common (no-glossary) case. Stored in
        # the "input_tokens" slot -- this engine has no separate input/
        # output token concept, so estimate_cost_for_engine prices this
        # value per-character instead of per-token.
        self.last_usage = _empty_usage()
        self.last_usage["input_tokens"] = sum(
            getattr(r, "billed_characters", None) or len(z)
            for r, z in zip(results, zh_lines))
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
        self.last_usage = _empty_usage()

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
            "target": context.get("target_language", "en"), "format": "text",
        }, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        # Step 25w: there was previously no last_usage at all here, so the
        # cost-cap system could never see any spend from this engine. The
        # v2 API doesn't report usage in its response, but it bills every
        # character sent for processing (confirmed against Google's own
        # billing docs) -- exactly the length of what was just sent, no
        # estimate needed. Stored in "input_tokens" for the same reason as
        # DeepLEngine above: priced per-character, not per-token.
        self.last_usage = _empty_usage()
        self.last_usage["input_tokens"] = sum(len(z) for z in zh_lines)
        return [t["translatedText"] for t in data["data"]["translations"]]


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

    def _get_pipeline(self, source_language: str, target_language: str = "en"):
        cache_key = (self.model_name, source_language, target_language)
        if cache_key not in _nllb_pipeline_cache:
            from transformers import pipeline
            src_lang = _NLLB_LANG_CODES.get(source_language, "zho_Hans")
            tgt_lang = _NLLB_LANG_CODES.get(target_language, "eng_Latn")
            _nllb_pipeline_cache[cache_key] = pipeline(
                "translation", model=self.model_name, src_lang=src_lang, tgt_lang=tgt_lang)
        return _nllb_pipeline_cache[cache_key]

    def translate_batch(self, zh_lines, context: dict):
        pipe = self._get_pipeline(context.get("source_language", "zh"),
                                  context.get("target_language", "en"))
        results = pipe(list(zh_lines))
        return [r["translation_text"] for r in results]


def tag_speakers_by_id(id_to_zh: dict, engine, known_characters=None, batch_size: int = 15, usage_cb=None):
    """For novel narration mode (no audio, no diarization available):
    asks the translation engine to guess who's speaking each chunk --
    a character name, or 'Narrator' for descriptive prose. Works with
    any LLM-capable engine (Claude, DeepSeek); pure-MT engines (DeepL,
    Google) can't do this and will return 'Narrator' for everything.

    id_to_zh maps each chunk's own id (its line idx) to its text. Returns
    {id: label} with an entry for EVERY id given: a label the model
    didn't return for an id (after one retry) is "Narrator", and ids the
    model invented are ignored -- a label can only ever land on the chunk
    it was keyed to, never by list position.
    This is a best-effort heuristic -- always let the user correct
    labels in the review table afterwards."""
    if not getattr(engine, "supports_reference", False):
        return {i: "Narrator" for i in id_to_zh}

    known = ", ".join(known_characters) if known_characters else "(none known yet)"
    labels = {}
    all_ids = list(id_to_zh)
    for start in range(0, len(all_ids), batch_size):
        batch_ids = all_ids[start:start + batch_size]

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
        result_map = _id_keyed_batch_request(
            batch_ids,
            lambda ids: _build_numbered_lines(ids, [id_to_zh[i] for i in ids]),
            call_model)
        for i in batch_ids:
            labels[i] = (result_map.get(str(i)) or "").strip() or "Narrator"
    return labels


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


def build_consistency_prompt(batch: list) -> str:
    """The consistency-check prompt for one window of already-translated
    lines. Position-based (1., 2., ...), not id-keyed -- an issue names a
    TERM ("a character's name spelled two ways"), never a specific line,
    so there's no per-line id for the model to echo back here. Shared by
    check_consistency_llm (live) and bulk_translate.py's bulk submission
    (Step 9d) -- both send byte-for-byte the same prompt for the same
    window of lines."""
    pairs = "\n".join(f"{i+1}. {ln.zh} -> {ln.en}" for i, ln in enumerate(batch))
    return (
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


def check_consistency_llm(lines, engine, batch_size: int = 60, usage_cb=None, cancel_check=None):
    """Reviews already-translated lines for consistency issues: the same
    Chinese term/name translated differently in different places. Works
    on the .zh/.en pairs already present -- doesn't call any external
    dictionary, just asks the LLM to spot drift across the batch it's
    given. Returns (issues, failed_batches, total_batches):
    issues is a list of {"term", "variants": [...], "note"} for review --
    doesn't auto-fix anything, since the "right" choice depends on
    context you'd want to confirm yourself. failed_batches/total_batches
    (Step 55) let the caller tell "nothing to flag" apart from "some
    batches silently couldn't be checked at all" -- previously a batch
    that errored or came back empty was skipped with no trace, so a run
    that failed on every batch looked identical to one that genuinely
    found nothing.

    Only meaningful with an LLM-capable engine; pure-MT engines return
    ([], 0, 0) (they don't reason about the whole set at once).
    cancel_check (B-05): called before each batch; it may raise to stop the
    run between batches (a batch already sent still finishes)."""
    if not getattr(engine, "supports_reference", False):
        return [], 0, 0
    translated = [ln for ln in lines if ln.en.strip()]
    if not translated:
        return [], 0, 0

    issues = []
    failed_batches = 0
    total_batches = 0
    for start in range(0, len(translated), batch_size):
        if cancel_check:
            cancel_check()
        batch = translated[start:start + batch_size]
        total_batches += 1
        prompt = build_consistency_prompt(batch)
        try:
            text = call_llm_json(engine, prompt, max_tokens=2000, fallback=None,
                                  usage_cb=usage_cb)
            if text is None:
                failed_batches += 1
                continue
        except Exception as e:
            failed_batches += 1  # a check failing shouldn't block anything -- just skip that batch
            import applog
            applog.get_logger().warning(
                f"consistency check batch {total_batches} failed: {redact_secrets(str(e))}")
            continue
        batch_issues = _parse_json_array(text, 0)
        if isinstance(batch_issues, list):
            issues.extend(i for i in batch_issues if isinstance(i, dict) and i.get("term"))
    return issues, failed_batches, total_batches


def build_episode_summary_prompt(lines) -> str:
    """Step 74's per-episode running-summary prompt -- one call over the
    WHOLE finished episode's English text, not a per-batch window (unlike
    build_consistency_prompt above), since the point is a fixed, once-per-
    episode artifact that the next episode's translation can afford to
    read in full."""
    body = "\n".join(ln.en.strip() for ln in lines if (ln.en or "").strip())
    return (
        "Below is the complete English translation of one finished episode from an "
        "ongoing series. Write a short running summary for continuity into the NEXT "
        "episode of the same series: key events, any unresolved plot threads or "
        "questions, and each named character's state at the end of this episode "
        "(relationships, secrets revealed or still hidden, where they ended up). A "
        "few sentences of plain prose -- no preamble, no episode-by-episode recap of "
        "earlier episodes, just what a translator picking up the next episode cold "
        "would need to resolve a callback or an ambiguous reference correctly.\n\n"
        'Return ONLY a JSON object: {"summary": "..."}. No markdown fences, no other '
        "keys.\n\n" + body
    )


def generate_episode_summary(lines, engine, usage_cb=None) -> str:
    """Step 74: one LLM call per finished episode (never once per
    translation batch) producing a short running summary for cross-
    episode narrative continuity -- distinct from glossary/translation
    memory's terminology-only continuity. Stored on the drama row
    (dramas.episode_summary) by the caller; this function only generates
    the text.

    Declines quietly (returns "") for a pure-MT engine that can't reason
    about a whole episode's text (same supports_reference guard as
    check_consistency_llm/flag_uncertain_lines above), when there's
    nothing translated yet to summarize, or when the call itself fails --
    a missing/unreachable summary engine must never block or fail the
    translation run that just finished."""
    if not getattr(engine, "supports_reference", False):
        return ""
    translated = [ln for ln in lines if (ln.en or "").strip()]
    if not translated:
        return ""
    prompt = build_episode_summary_prompt(translated)
    try:
        text = call_llm_json(engine, prompt, max_tokens=500, fallback=None, usage_cb=usage_cb)
        if text is None:
            return ""
    except Exception as e:
        import applog
        applog.get_logger().warning(f"episode summary generation failed: {redact_secrets(str(e))}")
        return ""
    data = _extract_first_json_value(text)
    if isinstance(data, dict) and isinstance(data.get("summary"), str):
        return data["summary"].strip()
    return ""


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

# Flags the app sets itself (not offered to the LLM as a reason to pick).
SYSTEM_FLAG_REASONS = {
    "timing_uncertain": "Timing uncertain -- forced alignment fell back to approximate timing",
    "timing_overlap": "Overlaps the next line -- exports trim it",
    "reading_speed": "Too fast to read -- too many characters for the time it's shown",
    "factual_detail": ("Auto QC: a number, date, name, amount or unit differs between the "
                       "source and the translation"),
    "bulk_source_changed": ("Source text changed while a bulk translation was pending -- its "
                            "result wasn't applied; translate this line again"),
    "content_blocked": ("Blocked by the translation engine's own content-moderation system -- "
                        "see the note for which engine and its stated reason"),
}


def flag_reason_label(flag: str) -> str:
    return FLAG_REASONS.get(flag) or SYSTEM_FLAG_REASONS.get(flag) or flag


def matching_glossary_terms(zh: str, glossary_terms) -> list:
    """Glossary entries whose source term (or a recorded alias, Step 30)
    literally appears in zh -- the same "in play for this line" heuristic
    line_tools.explain_translation already used, factored out so Step 58's
    "what happened here?" view can show the same real, non-fabricated
    match set instead of re-deriving it differently."""
    def _term_forms(t):
        aliases = [a.strip() for a in re.split(r"[|,，、]", t.get("aliases") or "") if a.strip()]
        return [t.get("term_original", "")] + aliases
    return [t for t in (glossary_terms or []) if any(f and f in zh for f in _term_forms(t))]


def build_flag_prompt(batch: list, id_fn=lambda ln: ln.idx) -> str:
    """The review-queue prompt for one batch of already-translated lines,
    each numbered by id_fn(ln) (its position by default, matching what
    flag_uncertain_lines' own by_idx lookup expects back). bulk_translate.py's
    bulk submission (Step 9d) passes id_fn=lambda ln: ln.id instead --
    results can come back hours later, by which point a position-based id
    could point at an entirely different line if the drama was edited in
    the meantime, where the permanent line id can't."""
    reasons_desc = "\n".join(f"  - {k}: {v}" for k, v in FLAG_REASONS.items())
    pairs = "\n".join(f"[{id_fn(ln)}] {ln.zh} -> {ln.en}" for ln in batch)
    return (
        "Below are Chinese source lines paired with their English translations. Flag ONLY "
        "the lines that genuinely need a second look -- most lines need none at all, and "
        "over-flagging defeats the point (the person reviewing this can't tell a real issue "
        "from noise). Reasons worth flagging:\n\n"
        f"{reasons_desc}\n\n"
        'Return ONLY a JSON array: [{"line_idx": 0, "reason": "uncertain_translation", '
        '"note": "brief reason"}]. Empty array if nothing needs flagging (the common case). '
        "No preamble, no markdown fences.\n\n" + pairs
    )


def flag_uncertain_lines(lines, engine, batch_size: int = 30, progress_cb=None, usage_cb=None,
                         cancel_check=None):
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
    cancel_check (B-05): called before each batch; it may raise to stop the
    run between batches (a batch already sent still finishes).
    """
    if not getattr(engine, "supports_reference", False):
        return lines
    translated = [ln for ln in lines if ln.en.strip()]
    if not translated:
        return lines

    n_batches = (len(translated) + batch_size - 1) // batch_size

    for bi, start in enumerate(range(0, len(translated), batch_size)):
        if cancel_check:
            cancel_check()
        batch = translated[start:start + batch_size]
        prompt = build_flag_prompt(batch)
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


# Local Ollama models offered in the picker. qwen3:8b is the default: it
# beat qwen2.5:7b on translation benchmarks at the same size (see the
# roadmap's Step 5 / model registry). 14B is opt-in -- its quantized weights
# don't fit cleanly alongside everything else in 8 GB of VRAM, so Ollama
# offloads part of it to the CPU and it runs much slower there.
OLLAMA_DEFAULT_MODEL = "qwen3:8b"
# Step 12e: Draft / Standard / Release starting tiers -- sensible starting
# points for engine + Step 7's Reflect mode + Step 12b's Auto QC pass,
# applied in one click. Built-in and fixed, layered on top of (not
# replacing) Step 9c's saved presets, which stay the way to keep a
# customized set of values. Engines follow §7.2's price ordering: DeepSeek
# is the cheapest capable LLM, Claude Sonnet the recommended default,
# Claude Opus the highest quality.
#
# auto_qc turns on Workspace's "Auto QC before export" check (auto_qc.py,
# Step 12b) -- the export section then lists lines with a factual-detail
# mismatch before anything is downloaded.
WORKFLOW_TIERS = {
    "draft": {"label": "Draft -- fast and cheap", "translation_engine": "deepseek",
              "engine_model": None, "reflect": False, "auto_qc": False},
    "standard": {"label": "Standard -- balanced", "translation_engine": "claude",
                 "engine_model": "claude-sonnet-5", "reflect": False, "auto_qc": False},
    "release": {"label": "Release -- best quality, checked before export",
                "translation_engine": "claude", "engine_model": "claude-opus-4-8",
                "reflect": True, "auto_qc": True},
}


OLLAMA_MODELS = {
    "qwen3:8b": "Qwen3 8B -- recommended default, fits a typical 8 GB GPU",
    "qwen2.5:14b": "Qwen2.5 14B -- may not fit in 8 GB; expect CPU offload (much slower)",
}


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
        import requests
        system_text = build_stable_system_text(context)
        num_ctx_override = context.get("ollama_num_ctx_override")

        def call_model(numbered):
            user_text = build_batch_user_message(context, numbered)
            estimated = _estimate_ollama_num_ctx(system_text, user_text)
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
                    {"role": "user", "content": user_text},
                ],
                "stream": False,
                "format": _OLLAMA_ID_KEYED_JSON_SCHEMA,
                "options": {"num_ctx": num_ctx},
            }, timeout=300)  # local models can be slow, especially CPU-only or larger ones
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()

        return _request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
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


# ---------------------------------------------------------------------------
# Step 36: capability tags per engine -- what each engine can actually do,
# so a task asks services/engine_routing_service.resolve_capability() for a
# capability instead of naming an engine. Descriptive only: nothing here
# switches engines on its own (settled decision: no automatic switching).
# ---------------------------------------------------------------------------

CAP_TRANSLATE = "translate"            # can translate a batch of lines
CAP_INSTRUCTIONS = "instructions"      # follows instructions / returns JSON (QC, summaries, helpers)
CAP_LONG_CONTEXT = "long_context"      # large context window (novel reference, whole episodes)
CAP_LOCAL = "local"                    # runs on this PC; nothing leaves it
CAP_CHEAP = "cheap"                    # free or a few cents per drama
CAP_GROUNDED_SEARCH = "grounded_search"  # web-grounded answers (Gemini Search Grounding)

ENGINE_CAPABILITIES = {
    "claude": frozenset({CAP_TRANSLATE, CAP_INSTRUCTIONS, CAP_LONG_CONTEXT}),
    "deepseek": frozenset({CAP_TRANSLATE, CAP_INSTRUCTIONS, CAP_LONG_CONTEXT, CAP_CHEAP}),
    "gemini": frozenset({CAP_TRANSLATE, CAP_INSTRUCTIONS, CAP_LONG_CONTEXT, CAP_CHEAP,
                         CAP_GROUNDED_SEARCH}),
    "deepl": frozenset({CAP_TRANSLATE}),
    "google": frozenset({CAP_TRANSLATE, CAP_CHEAP}),
    "test_offline": frozenset({CAP_TRANSLATE, CAP_LOCAL, CAP_CHEAP}),
    "ollama": frozenset({CAP_TRANSLATE, CAP_INSTRUCTIONS, CAP_LOCAL, CAP_CHEAP}),
    "nllb": frozenset({CAP_TRANSLATE, CAP_LOCAL, CAP_CHEAP}),
    "libretranslate": frozenset({CAP_TRANSLATE, CAP_CHEAP}),
}


def engine_capabilities(engine_name: str) -> frozenset:
    """The capability tags of a registered engine (empty for an unknown one)."""
    return ENGINE_CAPABILITIES.get(engine_name, frozenset())


def engines_with_capability(tag: str) -> list:
    """Registered engines carrying `tag`, in ENGINES order."""
    return [name for name in ENGINES if tag in engine_capabilities(name)]


# ---------------------------------------------------------------------------
# Step 97b: translate fallback chain
# ---------------------------------------------------------------------------

_FALLBACK_NAME_HINTS = ("timeout", "connectionerror", "apiconnection", "authentication",
                        "permissiondenied", "unauthorized")


def is_fallback_error(e: Exception) -> bool:
    """True only for a real transient/credential failure worth trying the
    next engine for: auth failure (401/403), rate limit, timeout, or
    connection error. Never a content-moderation refusal (Step 31 handles
    that itself) and never a bare/unknown exception, which could be a real
    bug rather than a real provider problem."""
    if isinstance(e, ContentModerationBlocked):
        return False
    status = getattr(e, "status_code", None)
    resp = getattr(e, "response", None)
    if resp is not None:
        status = status or getattr(resp, "status_code", None)
    if status in (401, 403) or _is_rate_limit_error(e):
        return True
    return any(hint in cls.__name__.lower()
               for cls in type(e).__mro__ for hint in _FALLBACK_NAME_HINTS)


# Bug B-06 (Step 124): transient errors (rate limit, timeout, connection)
# retry the SAME engine with a short capped backoff before the chain moves
# on; auth errors still switch immediately (waiting cannot fix a bad key).
FALLBACK_TRANSIENT_RETRIES = 2
FALLBACK_BACKOFF_BASE_SECONDS = 1.0
FALLBACK_BACKOFF_CAP_SECONDS = 8.0
_fallback_sleep = time.sleep  # patchable so tests never really sleep
_TRANSIENT_NAME_HINTS = ("timeout", "connectionerror", "apiconnection")


def is_transient_fallback_error(e: Exception) -> bool:
    """True for the is_fallback_error cases where retrying the same engine
    can help: rate limit, timeout, connection error. Never auth (401/403)."""
    if not is_fallback_error(e):
        return False
    status = getattr(e, "status_code", None)
    resp = getattr(e, "response", None)
    if resp is not None:
        status = status or getattr(resp, "status_code", None)
    if status in (401, 403):
        return False
    if _is_rate_limit_error(e):
        return True
    return any(hint in cls.__name__.lower()
               for cls in type(e).__mro__ for hint in _TRANSIENT_NAME_HINTS)


class FallbackEngine:
    """Wraps an ordered chain of engines of the SAME class (all
    instruction-following, or all in TRANSLATION_ONLY_ENGINES -- the caller
    enforces that, so glossary/style adherence is never silently dropped).
    translate_batch tries the active engine; on a transient error it first
    retries that engine up to FALLBACK_TRANSIENT_RETRIES times with a capped
    backoff, and only then (or at once for an auth error) on an
    is_fallback_error it switches -- for the rest of the run -- to the next one, recording the
    switch in `events`. Everything else (name/model/free_tier/last_usage/
    supports_reference...) reads through to the active engine so cost and
    usage logging stay correct per engine. Each engine has its own cost
    cap and its own spend (a failed attempt reports no usage, so it adds
    nothing; a finished batch always counts against the engine that ran it).
    """

    def __init__(self, engines: list, choices: list, caps: list = None):
        self.engines = list(engines)
        self.choices = list(choices)
        self.caps = list(caps) if caps else [None] * len(engines)
        self.spent = [0.0] * len(engines)
        self.active = 0
        self.events = []

    def __getattr__(self, name):
        if name.startswith("__") or name in ("engines", "active"):
            raise AttributeError(name)
        return getattr(self.engines[self.active], name)

    @property
    def active_choice(self) -> str:
        return self.choices[self.active]

    def cap_exhausted(self) -> bool:
        cap = self.caps[self.active]
        return cap is not None and self.spent[self.active] >= cap

    def translate_batch(self, zh_lines, context):
        retries = 0
        while True:
            engine = self.engines[self.active]
            try:
                result = engine.translate_batch(zh_lines, context)
            except Exception as e:
                if not is_fallback_error(e):
                    raise
                if is_transient_fallback_error(e) and retries < FALLBACK_TRANSIENT_RETRIES:
                    _fallback_sleep(min(FALLBACK_BACKOFF_CAP_SECONDS,
                                        FALLBACK_BACKOFF_BASE_SECONDS * (2 ** retries)))
                    retries += 1
                    continue
                if self.active + 1 >= len(self.engines):
                    raise
                retries = 0
                self.events.append({"from": self.choices[self.active],
                                    "to": self.choices[self.active + 1],
                                    "reason": type(e).__name__,
                                    "detail": redact_secrets(str(e))[:200]})
                self.active += 1
                continue
            u = getattr(engine, "last_usage", None)
            if u:
                self.spent[self.active] += estimate_cost_for_engine(
                    engine, u.get("input_tokens", 0), u.get("output_tokens", 0),
                    u.get("cache_read_tokens", 0), u.get("cache_write_tokens", 0))
            return result


class UnsupportedDirectionError(Exception):
    """Raised by standalone_translate (Step 26b) when the requested engine
    can't handle the requested translation direction -- see
    standalone_direction_support for which engines/directions this
    applies to and why."""


def standalone_direction_support(engine_name: str, source_language: str, target_language: str):
    """(ok, message) for whether engine_name can translate FROM
    source_language TO target_language in Step 26b's standalone translate
    tool. ok=False means refuse outright -- the caller must not call
    translate_batch at all. ok=True with a message means attempt it but
    show the message as a warning; ok=True with message=None means no
    caveat.

    zh/ja/ko -> English is this app's existing, well-tested direction --
    every engine already does this and keeps doing it unchanged. English
    -> zh/ja/ko is new (Step 26b item 6):
      - DeepL/Google/NLLB take an explicit source+target pair in their own
        API/pipeline, so they're just as capable in either direction.
      - Claude/DeepSeek/Gemini/test_offline are prompted for the direction
        directly (build_standalone_instructions), same as any other LLM
        instruction.
      - Ollama's real capability depends entirely on whichever local model
        is loaded, which this app has no way to verify -- attempted, but
        flagged as a warning rather than assumed reliable.
      - LibreTranslate/LTEngine's own translate_batch has no source-
        language parameter at all (see its docstring -- self-hosted
        language-pair coverage varies and isn't discoverable from here),
        so English -> zh/ja/ko is refused for it rather than silently
        attempted and possibly mistranslated or empty.
    """
    if source_language != "en":
        return True, None
    if engine_name == "ollama":
        target_name = LANGUAGE_NAMES.get(target_language, target_language)
        return True, (
            f"Ollama's quality translating English -> {target_name} depends entirely on "
            "which local model you have loaded -- some handle it well, some not at all. "
            "Check the output carefully.")
    if engine_name == "libretranslate":
        return False, (
            "This app can't confirm your LibreTranslate/LTEngine server has an English "
            "source model installed for this pair -- pick a different engine, or check "
            "your server's supported language pairs first.")
    return True, None


def chunk_standalone_text(text: str, max_chars_per_chunk: int = 1500) -> list:
    """Splits text into paragraph-based chunks for Step 26b's standalone
    translate tool, keeping paragraph breaks intact so translated chunks
    can be rejoined the same way. Consecutive short paragraphs are grouped
    up to max_chars_per_chunk; a single paragraph longer than that is kept
    whole rather than cut mid-sentence."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if not paragraphs:
        return []
    chunks = []
    current = []
    current_len = 0
    for p in paragraphs:
        if current and current_len + len(p) > max_chars_per_chunk:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(p)
        current_len += len(p)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def standalone_translate(text: str, engine, source_language: str, target_language: str,
                         batch_size: int = 8) -> str:
    """Step 26b's standalone translate tool: translates arbitrary pasted
    text -- not tied to any drama/project -- from source_language to
    target_language (one side of which is always "en"). Chunks long text
    with chunk_standalone_text, sent in groups of batch_size per
    translate_batch call (same reasoning as translate_lines_with_engine's
    own batch_size, just simpler since there's no cross-batch context
    window here), and reassembled using each engine's own translate_batch
    -- which already resolves a response back to its own chunk by id
    rather than by position (see _request_translations_with_retry's own
    docstring for the exact bug that protects against) -- built once
    there, not reimplemented here.

    Raises UnsupportedDirectionError if standalone_direction_support
    refuses this engine/direction combination; callers should check that
    first (to show a live warning/refusal in the UI) but this checks
    again itself so it's never silently skipped by a caller that forgets.
    """
    ok, message = standalone_direction_support(engine.name, source_language, target_language)
    if not ok:
        raise UnsupportedDirectionError(message)
    chunks = chunk_standalone_text(text)
    if not chunks:
        return ""
    context = {
        "source_language": source_language,
        "target_language": target_language,
        "standalone": True,
    }
    translated_chunks = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        translated_chunks.extend(engine.translate_batch(batch, context))
    return "\n\n".join(t or "" for t in translated_chunks)


# Engines that are free to use every time, no conditions attached.
# Gemini isn't here -- it uses the same engine/API for free and paid
# keys, so whether a given run is "free" depends on the per-session
# "My Gemini key is free-tier" setting (settings_tab.py), not on which
# engine was picked. See engine_picker_label / estimate_cost_for_engine.
FREE_ENGINES = {"test_offline", "ollama", "nllb", "libretranslate"}

# Free-tier limits, confirmed against ai.google.dev/gemini-api/docs/rate-limits
# in September 2026 -- Step 1d's original "~10 requests/minute on Flash" note
# was a full year stale, didn't distinguish Flash from Flash-Lite, and didn't
# mention the shared token ceiling at all. Three independent limits, not one.
GEMINI_FREE_TIER_LIMITS = {
    "flash-lite": {"rpm": 15, "rpd": 1000},
    "flash": {"rpm": 10, "rpd": 250},
}
GEMINI_FREE_TIER_DEFAULT_LIMITS = {"rpm": 10, "rpd": 250}
GEMINI_FREE_TIER_TPM = 250_000

# Removed from the free tier entirely in April 2026 -- a free-tier key can no
# longer reach it at all, regardless of RPM/RPD/TPM standing.
GEMINI_FREE_TIER_UNAVAILABLE_MODELS = {"gemini-pro-latest"}


def gemini_free_tier_limits_for(model: str) -> dict:
    """{"rpm": int, "rpd": int} for a given Gemini model under the free
    tier. Checked in this order since "flash-lite" also contains "flash"."""
    model = model or ""
    if "flash-lite" in model:
        return GEMINI_FREE_TIER_LIMITS["flash-lite"]
    if "flash" in model:
        return GEMINI_FREE_TIER_LIMITS["flash"]
    return GEMINI_FREE_TIER_DEFAULT_LIMITS


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
    "🧪 Free — for testing: Google free tier. Rate-limited -- Flash: "
    f"{GEMINI_FREE_TIER_LIMITS['flash']['rpm']} requests/min, "
    f"{GEMINI_FREE_TIER_LIMITS['flash']['rpd']}/day; Flash-Lite: "
    f"{GEMINI_FREE_TIER_LIMITS['flash-lite']['rpm']}/min, "
    f"{GEMINI_FREE_TIER_LIMITS['flash-lite']['rpd']}/day; shared "
    f"{GEMINI_FREE_TIER_TPM:,} tokens/min across models. Pro isn't available "
    "on the free tier. Google may use your text to improve its products, "
    "and people may read it."
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
               free_tier: bool = False, base_url: str = None):
    cls = ENGINES[engine_name]
    kwargs = {}
    if engine_name == "gemini":
        kwargs["free_tier"] = free_tier
    if engine_name == "ollama" and base_url:
        kwargs["base_url"] = base_url
    if model:
        return cls(api_key, model, **kwargs)
    return cls(api_key, **kwargs)


def estimate_cost_for_engine(engine, input_tokens: int, output_tokens: int,
                             cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Same as estimate_cost, but $0 for a Gemini engine running under its
    free tier -- PRICING_PER_MILLION_TOKENS prices the paid tier, which
    doesn't apply once free_tier is set on the engine instance.

    DeepL and Google are pure per-character-billed MT engines with no
    token concept of their own -- for those, `input_tokens` actually holds
    the billed character count (see GoogleEngine/DeepLEngine.last_usage)
    and is priced from PRICING_PER_MILLION_CHARACTERS instead."""
    if getattr(engine, "free_tier", False):
        return 0.0
    name = getattr(engine, "name", "")
    if name in PRICING_PER_MILLION_CHARACTERS:
        return input_tokens / 1_000_000 * PRICING_PER_MILLION_CHARACTERS[name]
    return estimate_cost(getattr(engine, "model", ""), input_tokens, output_tokens,
                         cache_read_tokens, cache_write_tokens)


def estimate_translation_cost(engine, zh_lines: list) -> float:
    """Rough pre-run estimate of a normal single-pass translation run,
    from the lines' own character count. For token-billed engines this
    uses a tokenizer-free ~3.5 chars/token heuristic -- an
    order-of-magnitude estimate, not a precise bill. For DeepL/Google
    (billed per character, not per token) the character count itself is
    passed straight through, since that's exactly what they bill."""
    chars = sum(len(z) for z in zh_lines)
    if getattr(engine, "name", "") in PRICING_PER_MILLION_CHARACTERS:
        return estimate_cost_for_engine(engine, chars, 0)
    input_tokens = int(chars / 3.5) + 300  # + a rough fixed cost for the instructions block
    output_tokens = int(chars / 2.5)  # English translations tend to run a bit longer than CJK source
    return estimate_cost_for_engine(engine, input_tokens, output_tokens)


def estimate_reflect_mode_cost(engine, zh_lines: list) -> float:
    """Rough pre-run estimate for Step 7's Reflect mode, shown before the
    user starts it (it costs real money to run and can't be cancelled
    mid-line the way a single bad batch can). Reflect mode is three LLM
    calls instead of translate_batch's one, so this is a normal run's
    estimate times 3."""
    return estimate_translation_cost(engine, zh_lines) * 3


def resolve_cost_cap(job_cap_usd=None, monthly_cap_usd=None, month_spend_usd: float = 0.0):
    """(cap, refusal) for a job about to start. cap is the tighter of the
    per-job cap and whatever is left of the monthly cap, or None when
    neither is set (0 or None both mean "no cap"). refusal is a plain
    message when the monthly cap is already used up -- don't start."""
    caps = []
    if job_cap_usd:
        caps.append(float(job_cap_usd))
    if monthly_cap_usd:
        remaining = float(monthly_cap_usd) - float(month_spend_usd or 0)
        if remaining <= 0:
            return None, (f"This month's spending cap (${float(monthly_cap_usd):.2f}) is already "
                          f"used up (${float(month_spend_usd):.2f} logged so far). Raise it in "
                          "Settings to keep going.")
        caps.append(remaining)
    return (min(caps) if caps else None), None


def build_reflect_faithful_prompt(instructions: str, batch_ctx: str, ids: list, zh_by_id: dict,
                                  speaker_by_id: dict = None) -> str:
    """Reflect mode's pass 1 (faithfulness) prompt for one batch of ids.
    Shared by reflect_translate_batch (live, in-process) and
    bulk_translate.py's Reflect pipeline (Step 9d) -- both build
    byte-for-byte the same prompt for the same ids/lines."""
    numbered = _build_numbered_lines(
        ids, [zh_by_id[i] for i in ids],
        [speaker_by_id.get(i) for i in ids] if speaker_by_id else None)
    return (
        f"{instructions}\n\n"
        "This is the FAITHFULNESS pass of a multi-stage translation: translate each line "
        "below preserving its exact literal meaning, grammar and information -- word choice "
        "and register still matter, but don't optimize yet for how naturally it reads as a "
        "subtitle; a later pass polishes that. Keep names and glossary terms consistent as "
        "instructed above.\n\n"
        'Return ONLY a JSON object mapping each line number to its translation, e.g. '
        '{"1": "...", "2": "..."}. No preamble, no markdown fences.\n\n'
        + batch_ctx +
        f"Lines:\n{numbered}"
    )


def build_reflect_reflection_prompt(instructions: str, batch_ctx: str, ids: list, zh_by_id: dict,
                                    draft_by_id: dict) -> str:
    """Reflect mode's pass 2 (reflection/critique) prompt -- see
    build_reflect_faithful_prompt's docstring. draft_by_id: pass 1's own
    saved output for each id (never empty here -- the caller only
    includes ids that got an actual faithfulness draft)."""
    pairs = "\n".join(f"{i}. {zh_by_id[i]} -> {draft_by_id[i]}" for i in ids)
    return (
        f"{instructions}\n\n"
        "This is the REFLECTION pass: for each numbered line below (source -> a literal "
        "draft translation), critique that SPECIFIC draft -- where it's technically correct "
        "but reads unnaturally as a subtitle, plus accuracy, pronoun/gender choices, glossary "
        "use, tone and register. One or two plain sentences per line. Skip a line's key "
        "entirely if the draft is already good -- don't invent a critique to fill space.\n\n"
        'Return ONLY a JSON object mapping each line number to its critique, e.g. '
        '{"1": "..."}. Omit a key entirely for a line that needs no critique. No preamble, '
        "no markdown fences.\n\n"
        + batch_ctx +
        f"Lines:\n{pairs}"
    )


def build_reflect_expressive_prompt(instructions: str, batch_ctx: str, ids: list, zh_by_id: dict,
                                    draft_by_id: dict, critique_by_id: dict) -> str:
    """Reflect mode's pass 3 (expressiveness/rewrite) prompt -- see
    build_reflect_faithful_prompt's docstring. critique_by_id: pass 2's
    own saved output; an id with none (the draft was already judged
    good) just gets no "Critique:" line, same as the live path."""
    parts = []
    for i in ids:
        entry = f"{i}. Source: {zh_by_id[i]}\n   Draft: {draft_by_id[i]}"
        critique = critique_by_id.get(i)
        if critique:
            entry += f"\n   Critique: {critique}"
        parts.append(entry)
    return (
        f"{instructions}\n\n"
        "This is the EXPRESSIVENESS pass: rewrite each line's draft translation using its "
        "critique (where one is given) so it reads naturally as a subtitle -- fluent, "
        "well-paced, in-register -- while keeping the source's exact meaning. A line with no "
        "critique is probably already close; still return your best final wording for every "
        "line.\n\n"
        'Return ONLY a JSON object mapping each line number to its final translation, e.g. '
        '{"1": "..."}. No preamble, no markdown fences.\n\n'
        + batch_ctx +
        "Lines:\n" + "\n".join(parts)
    )


def reflect_translate_batch(engine, zh_lines: list, context: dict, usage_cb=None, max_retries: int = 1):
    """
    Step 7's "High quality" Reflect mode: three separate LLM passes for
    one batch, instead of translate_batch's single call --

      1. Faithfulness: a literal translation preserving exact meaning,
         not yet polished for how it reads.
      2. Reflection: the same engine critiques that SPECIFIC draft --
         where it's technically correct but reads unnaturally, plus
         accuracy, pronouns/gender, glossary use, tone and register.
      3. Expressiveness: rewrites using the reflection, now optimizing
         for how it reads as a subtitle.

    Deliberately three real passes, not VideoLingo's two-call version
    (their reflection is folded invisibly into the rewrite prompt) --
    giving the critique its own pass means it can be stored and shown
    (as translation notes), not just silently baked into a rewrite.

    Goes through call_llm_json, not each engine's own translate_batch:
    call_llm_json already dispatches Claude/DeepSeek/Gemini/Ollama
    uniformly for a single free-form prompt, which is exactly what three
    differently-worded passes need -- translate_batch is fixed to one
    particular (already-natural-reading) prompt shape and isn't reusable
    for this. Every pass is id-keyed via _id_keyed_batch_request, the
    same exact-id matching translate_batch uses (Step 1) -- deliberately
    not VideoLingo's own SequenceMatcher fuzzy-similarity matching, which
    is strictly less robust than an exact id.

    context: same shape translate_batch's engines already take
    (style_note, drama_meta, novel_reference, locale, glossary_terms,
    style_guidelines, recent_context, upcoming_lines, speaker_labels,
    line_ids) -- built once by translate_lines_with_engine either way.

    Returns (translations, critiques): both lists parallel to zh_lines.
    translations is the expressiveness pass's final wording, falling
    back to the faithfulness draft for any line expressiveness never
    returned, then "" if even that never came back. critiques is the
    reflection pass's critique for that line, or "" if it had none (a
    draft judged already good) or the pass never returned one.
    """
    if not getattr(engine, "supports_reference", False):
        raise RuntimeError(f"{getattr(engine, 'name', type(engine).__name__)} can't run Reflect "
                           "mode (needs an LLM engine, not a translation-only one).")

    ids = list(range(1, len(zh_lines) + 1))
    line_ids = context.get("line_ids")
    if (line_ids is not None and len(line_ids) == len(zh_lines)
            and all(isinstance(i, int) for i in line_ids) and len(set(line_ids)) == len(line_ids)):
        ids = list(line_ids)
    pos = {i: p for p, i in enumerate(ids)}
    speaker_names = context.get("speaker_labels")

    # Step 9e: build_llm_instructions() alone never actually inserts the
    # reference novel text anywhere -- only build_stable_prompt()'s own
    # novel_block does that (the normal, non-Reflect path already goes
    # through it). Calling build_llm_instructions() directly here meant
    # Reflect mode's own instructions told the model to consult "the
    # reference novel translation below," but nothing was ever below it.
    instructions, novel_block = build_stable_prompt(context)
    if novel_block:
        instructions = instructions + "\n\n" + novel_block
    batch_ctx = build_batch_context(context.get("recent_context"), context.get("upcoming_lines"))
    batch_ctx = batch_ctx + "\n" if batch_ctx else ""

    def call(prompt):
        return call_llm_json(engine, prompt, max_tokens=4000, fallback="{}", usage_cb=usage_cb)

    def build_faithful_batch(batch_ids):
        return build_reflect_faithful_prompt(
            instructions, batch_ctx, batch_ids, {i: zh_lines[pos[i]] for i in batch_ids},
            {i: speaker_names[pos[i]] for i in batch_ids} if speaker_names else None)

    direct_map = _id_keyed_batch_request(ids, build_faithful_batch, call, max_retries)
    direct = {i: direct_map.get(str(i), "") for i in ids}

    def build_reflection_batch(batch_ids):
        return build_reflect_reflection_prompt(
            instructions, batch_ctx, batch_ids, {i: zh_lines[pos[i]] for i in batch_ids}, direct)

    # Only ids with an actual faithfulness draft go on to reflection/
    # expressiveness -- there's nothing sensible to critique or rewrite
    # for a line that never got one (an empty "Draft: " in the prompt).
    # No retry on the reflection call itself (unlike the other two
    # passes): a line the model chose NOT to critique is a normal,
    # expected answer here -- most lines in a real batch have nothing
    # worth flagging -- and treating every omitted id as "missing, must
    # retry" would re-request the whole batch on almost every run, since
    # it's rare for every single line to get a critique. An id absent
    # here just means "no critique".
    has_draft_ids = [i for i in ids if direct[i]]
    critique_map = (_id_keyed_batch_request(has_draft_ids, build_reflection_batch, call, max_retries=0)
                   if has_draft_ids else {})

    def build_expressive_batch(batch_ids):
        return build_reflect_expressive_prompt(
            instructions, batch_ctx, batch_ids, {i: zh_lines[pos[i]] for i in batch_ids}, direct,
            {i: critique_map.get(str(i)) for i in batch_ids})

    final_map = (_id_keyed_batch_request(has_draft_ids, build_expressive_batch, call, max_retries)
                if has_draft_ids else {})
    translations = [final_map.get(str(i), direct.get(i, "")) for i in ids]
    critiques = [critique_map.get(str(i), "") for i in ids]
    return translations, critiques


def build_translation_context(engine, drama_meta: dict, style_note: str = "", novel_reference=None,
                              locale: str = "en-US", glossary_terms=None, style_guidelines: str = "",
                              ollama_num_ctx_override: int = None) -> dict:
    """The per-job context every engine's prompt is built from -- shared by
    live translation and bulk submission so both send the same prompt."""
    return {
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


def translate_lines_with_engine(lines, engine, drama_meta: dict, batch_size: int = 20,
                                 style_note: str = "", novel_reference=None, progress_cb=None,
                                 save_cb=None, force_retranslate: bool = False,
                                 locale: str = "en-US", glossary_terms=None, usage_cb=None,
                                 style_guidelines: str = "", cancel_check_cb=None,
                                 context_window: int = 6, context_window_ahead: int = 3,
                                 character_names: dict = None, ollama_num_ctx_override: int = None,
                                 reflect: bool = False, notes_cb=None, cost_cap_usd: float = None,
                                 cap_cb=None, target_ids=None):
    """cancel_check_cb: optional callable returning True if the run should
    stop cooperatively between batches -- e.g. background_jobs.is_cancel_requested,
    so a background translation job can be stopped safely (rather than
    racing a destructive action like a full library reset against a
    thread that's still writing).

    reflect: Step 7's "High quality" mode -- runs reflect_translate_batch
    (three passes: faithfulness, reflection, expressiveness) per batch
    instead of engine.translate_batch's single pass. notes_cb, if given,
    is called once per batch with a list of {line_idx, term, note_type,
    note} dicts (translation_guide's translation-notes shape) for
    whichever lines the reflection pass actually critiqued -- call
    db.save_translation_notes(...) from it. Ignored when reflect=False.

    ollama_num_ctx_override: optional Settings override for OllamaEngine's
    context-window size. Ignored by every other engine. OllamaEngine
    itself never lets this go below what the actual prompt needs --
    see _estimate_ollama_num_ctx's docstring for why.

    lines: list of objects with .zh and .en attributes (mutated in place).

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
    with (input_tokens, output_tokens, cache_read_tokens,
    cache_write_tokens) -- call db.log_usage(...) from it for cost
    tracking. input_tokens is the whole prompt; the cache counts are the
    parts of it read from / written to a provider prompt cache. In
    Reflect mode it's called once per LLM pass with just (input_tokens,
    output_tokens), so give the last two parameters defaults. Only fires
    for engines that report usage; pure-MT engines don't report token
    counts the same way, so nothing is logged for those.

    cost_cap_usd: stop cleanly once this run's estimated spend
    (estimate_cost_for_engine over each batch's real reported usage)
    reaches this many dollars. Checked after each batch, so the batch
    that crosses the cap still finishes and is saved -- the run can go
    over by at most one batch, and never loses finished work. cap_cb, if
    given, is called with the amount spent when the cap stops a run that
    still had batches left.

    target_ids: optional set of permanent line ids -- only those lines are
    translated (on top of the force_retranslate rule); every other line
    still serves as look-back/look-ahead context.

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
    if target_ids is not None:
        target_lines = [ln for ln in target_lines if getattr(ln, "id", None) in target_ids]
    if not target_lines:
        if progress_cb:
            progress_cb(1.0)
        return lines, []

    character_names = character_names or {}
    context = build_translation_context(
        engine, drama_meta, style_note=style_note, novel_reference=novel_reference, locale=locale,
        glossary_terms=glossary_terms, style_guidelines=style_guidelines,
        ollama_num_ctx_override=ollama_num_ctx_override)
    errors = []
    spent = 0.0

    def record_usage(inp, out, cache_read=0, cache_write=0):
        nonlocal spent
        spent += estimate_cost_for_engine(engine, inp, out, cache_read, cache_write)
        if usage_cb:
            usage_cb(inp, out, cache_read, cache_write)

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
        def _translate_chunk(chunk):
            """Runs one translate attempt for chunk (the whole batch, or
            one bisected half of it -- Step 31 item 3). Returns
            (translations, critiques) -- critiques is None outside Reflect
            mode. A ContentModerationBlocked (or any other exception)
            propagates to the caller, which decides what to do about it."""
            chunk_context = dict(context)
            chunk_context["speaker_labels"] = [character_names.get(ln.speaker) for ln in chunk]
            chunk_context["line_ids"] = [getattr(ln, "id", None) for ln in chunk]
            chunk_context["batch_source_lines"] = [ln.zh for ln in chunk]
            if reflect:
                return call_with_backoff(
                    lambda: reflect_translate_batch(engine, [ln.zh for ln in chunk], chunk_context,
                                                    usage_cb=record_usage))
            translations = call_with_backoff(
                lambda: engine.translate_batch([ln.zh for ln in chunk], chunk_context))
            if hasattr(engine, "last_usage"):
                u = engine.last_usage
                record_usage(u.get("input_tokens", 0), u.get("output_tokens", 0),
                             u.get("cache_read_tokens", 0), u.get("cache_write_tokens", 0))
            return translations, None

        def _process_chunk(chunk, allow_bisect):
            """Translates chunk, applying results directly onto the Line
            objects, or flagging/recording an error for whichever lines
            couldn't be translated. allow_bisect: whether a
            ContentModerationBlocked caught here should trigger one
            bisection retry (Step 31 item 3's bounded, one-level split --
            only True for the original, un-split batch, never for an
            already-bisected half)."""
            try:
                translations, critiques = _translate_chunk(chunk)
            except ContentModerationBlocked as blocked:
                if allow_bisect and len(chunk) > 1:
                    mid = len(chunk) // 2
                    _process_chunk(chunk[:mid], allow_bisect=False)
                    _process_chunk(chunk[mid:], allow_bisect=False)
                else:
                    for ln in chunk:
                        ln.flag = "content_blocked"
                        ln.flag_note = f"{blocked.engine}: {blocked.reason}"
                    errors.append({"batch_index": bi, "lines": [ln.idx for ln in chunk],
                                   "error": f"blocked by {blocked.engine}'s content filter: "
                                            f"{blocked.reason}"})
                return
            except Exception as e:
                redacted = redact_secrets(str(e))
                errors.append({"batch_index": bi, "lines": [ln.idx for ln in chunk],
                               "error": redacted})
                import applog
                applog.get_logger().error(f"translate batch {bi} failed: {redacted}")
                return
            if notes_cb and critiques is not None:
                notes = [{"line_idx": ln.idx, "term": "", "note_type": "reflection", "note": c}
                         for ln, c in zip(chunk, critiques) if c]
                if notes:
                    notes_cb(notes)
            if len(translations) != len(chunk):
                errors.append({"batch_index": bi, "lines": [ln.idx for ln in chunk],
                               "error": f"engine returned {len(translations)} translation(s) for "
                                        f"{len(chunk)} line(s) -- left untranslated rather than "
                                        f"risk assigning a translation to the wrong line"})
            else:
                for ln, tr in zip(chunk, translations):
                    ln.en = tr

        _process_chunk(batch, allow_bisect=True)
        if save_cb:
            save_cb(lines)
        if progress_cb:
            progress_cb((bi + 1) / n_batches)
        if isinstance(engine, FallbackEngine) and engine.cap_exhausted() and bi + 1 < n_batches:
            if cap_cb:
                cap_cb(engine.spent[engine.active])
            break
        if cost_cap_usd is not None and spent >= cost_cap_usd and bi + 1 < n_batches:
            if cap_cb:
                cap_cb(spent)
            break
    return lines, errors

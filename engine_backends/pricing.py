"""Model lists, per-million-token prices and cost estimation."""

import json
import re


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
    "claude-sonnet-5-5": "Sonnet 5.5 -- balanced quality and cost (recommended default)",
    "claude-opus-5-5": "Opus 5.5 -- highest quality",
    "claude-haiku-4-5-20251001": "Haiku 4.5 -- fastest and cheapest, lower nuance",
    # Kept selectable so a saved preset, title or promoted benchmark model that
    # names one of these still runs instead of being refused as "not offered".
    "claude-sonnet-5": "Sonnet 5 -- previous generation",
    "claude-opus-4-8": "Opus 4.8 -- previous generation, costs more than Opus 5.5",
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

# OpenAI's Chat Completions endpoint, the one place its address is set.
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# Selectable OpenAI models. The default is the first-listed mini tier; the
# lineup changes often, so check platform.openai.com/docs/models if a run
# starts failing, and update this and PRICING_PER_MILLION_TOKENS together.
OPENAI_MODELS = {
    "gpt-5-mini": "GPT-5 mini -- balanced quality and cost (recommended default)",
    "gpt-5-nano": "GPT-5 nano -- cheapest, lower nuance",
    "gpt-5": "GPT-5 -- highest quality, most expensive",
}

# Newer OpenAI models the provider itself lists (the manual model check in
# services/model_registry_service) can be used without an edit here. Only the
# GPT-5-and-later chat models: older ids (gpt-4, o-series) cost far more than
# any rate estimate_cost could fall back to, and the others are not Chat
# Completions models. Newer GPT-5.x releases can also cost more than gpt-5,
# so an unpriced one is costed at OPENAI_EXTRA_MODEL_CEILING instead.
PROVIDER_CHECK_CACHE_KEY = "model_registry_provider_check"
OPENAI_EXTRA_MODEL_CEILING = {"input": 5.0, "output": 40.0}
_OPENAI_EXTRA_RE = re.compile(r"gpt-(?:[5-9]|[1-9][0-9])[a-z0-9._-]{0,60}")
_OPENAI_EXCLUDED_WORDS = re.compile(
    r"(?:^|[-.])(?:pro|codex|realtime|search|research|instruct|audio|image|tts|"
    r"transcribe|embedding|moderation|diarize)(?:[-.]|$)")


def is_openai_extra_model(model) -> bool:
    return (isinstance(model, str) and bool(_OPENAI_EXTRA_RE.fullmatch(model))
            and not _OPENAI_EXCLUDED_WORDS.search(model))


def openai_listed_extra_models() -> list:
    """GPT-5+ chat models OpenAI listed in the last manual check that the app
    does not list itself; [] when no check has succeeded. Reads the cached
    answer only, never the network."""
    try:
        import db
        raw = db.get_app_setting(PROVIDER_CHECK_CACHE_KEY)
        data = json.loads(raw) if isinstance(raw, str) else raw
        provider = (data.get("engines") or {}).get("openai") or {}
        models = provider.get("models") if provider.get("ok") else []
        if not isinstance(models, list):
            return []
        return [m for m in dict.fromkeys(models)
                if is_openai_extra_model(m) and m not in OPENAI_MODELS]
    except Exception:
        return []


PRICING_PER_MILLION_TOKENS = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
    # Anthropic's pricing page, October 2026. 5-minute cache writes are 125% of
    # input, as CACHE_WRITE_PRICE_FACTOR encodes. Cache reads on these two are
    # 5% of input, so CACHE_READ_PRICE_FACTOR's 10% over-estimates them.
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0},
    "claude-fable-5-1": {"input": 10.0, "output": 50.0},
    # Corrected against api-docs.deepseek.com/quick_start/pricing's
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
    # OpenAI list prices as recalled from platform.openai.com/docs/pricing
    # (not re-verified here); re-check before budgeting a large batch.
    "gpt-5-mini": {"input": 0.25, "output": 2.0},
    "gpt-5-nano": {"input": 0.05, "output": 0.40},
    "gpt-5": {"input": 1.25, "output": 10.0},
}


# Cache reads are billed at a fraction of the input price: 10% on Claude,
# less than that on Gemini and DeepSeek. One conservative figure for all
# of them -- a spending cap should never be undercut by an estimate that
# came out cheaper than the real bill.
CACHE_READ_PRICE_FACTOR = 0.1
# Claude charges 25% extra on the tokens it writes to the cache.
CACHE_WRITE_PRICE_FACTOR = 1.25


# Provider families whose unpriced models fall back to that family's highest
# known rates (see estimate_cost).
_PRICED_FAMILY_PREFIXES = ("claude-", "gemini-", "deepseek-", "gpt-")


# Tier words per provider, most specific first ("flash-lite" before "flash").
_FAMILY_TIERS = {
    "claude-": ("sonnet", "opus", "haiku"),
    "gemini-": ("flash-lite", "flash", "pro"),
}


def _model_tier(prefix: str, model: str):
    for tier in _FAMILY_TIERS.get(prefix, ()):
        if re.search(rf"(?:^|[-.]){tier}(?:[-.]|$)", model):
            return tier
    return None


def _highest_family_rates(model: str):
    if is_openai_extra_model(model):
        return dict(OPENAI_EXTRA_MODEL_CEILING)
    prefix = next((p for p in _PRICED_FAMILY_PREFIXES if model.startswith(p)), None)
    family = [(m, r) for m, r in PRICING_PER_MILLION_TOKENS.items() if prefix and m.startswith(prefix)]
    # A new Sonnet must not be costed at Opus rates (~7x too high, which
    # inflates the spend caps), so prefer priced models of the same tier and
    # only widen to the whole family when the id names no known tier.
    tier = _model_tier(prefix, model) if prefix else None
    same_tier = [r for m, r in family if tier and _model_tier(prefix, m) == tier]
    rates = same_tier or [r for _, r in family]
    if not rates:
        return None
    return {"input": max(r["input"] for r in rates), "output": max(r["output"] for r in rates)}


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """input_tokens is the whole prompt; cache_read_tokens/cache_write_tokens
    are the parts of it that were served from / written to a prompt cache."""
    rates = PRICING_PER_MILLION_TOKENS.get(model)
    if not rates and isinstance(model, str):
        # A Claude/Gemini/DeepSeek model the app has no price for (one offered
        # from the provider's list): price it at that provider's highest known
        # rates so the cost cap still protects, rather than reporting $0.
        rates = _highest_family_rates(model)
    if not rates:
        return 0.0
    uncached = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    effective_input = (uncached + cache_read_tokens * CACHE_READ_PRICE_FACTOR
                       + cache_write_tokens * CACHE_WRITE_PRICE_FACTOR)
    return (effective_input / 1_000_000 * rates["input"]) + (output_tokens / 1_000_000 * rates["output"])


def estimate_cost_for_engine(engine, input_tokens: int, output_tokens: int,
                             cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Same as estimate_cost, but $0 for a Gemini engine running under its
    free tier -- PRICING_PER_MILLION_TOKENS prices the paid tier, which
    doesn't apply once free_tier is set on the engine instance."""
    if getattr(engine, "free_tier", False):
        return 0.0
    return estimate_cost(getattr(engine, "model", ""), input_tokens, output_tokens,
                         cache_read_tokens, cache_write_tokens)


def estimate_translation_cost(engine, zh_lines: list) -> float:
    """Rough pre-run estimate of a normal single-pass translation run,
    from the lines' own character count. For token-billed engines this
    uses a tokenizer-free ~3.5 chars/token heuristic -- an
    order-of-magnitude estimate, not a precise bill."""
    chars = sum(len(z) for z in zh_lines)
    input_tokens = int(chars / 3.5) + 300  # + a rough fixed cost for the instructions block
    output_tokens = int(chars / 2.5)  # English translations tend to run a bit longer than CJK source
    return estimate_cost_for_engine(engine, input_tokens, output_tokens)


def estimate_reflect_mode_cost(engine, zh_lines: list) -> float:
    """Rough pre-run estimate for Reflect mode, shown before the
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

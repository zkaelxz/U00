"""
translate_engines.py -- pluggable translation backends.

Each engine exposes: translate_batch(zh_lines: list[str], context: dict) -> list[str]
"context" carries: drama metadata, novel_reference, style_note, and (for
LLM engines) a system-prompt builder so behavior stays consistent across
engines that support instructions vs. engines that are pure MT.

The code lives in the engine_backends package, grouped by provider and role
(see engine_backends/__init__.py); this module re-exports every public and
private name so `import translate_engines` and `from translate_engines import X`
keep working. A patch must target the module that uses the name (for example
engine_backends.llm_tasks.call_llm_json), not this re-export.
"""

import re  # noqa: F401  (kept as module attributes: callers and tests reach translate_engines.time etc.)
import json  # noqa: F401
import time  # noqa: F401
import contextvars  # noqa: F401
import inspect  # noqa: F401

from core import LANGUAGE_NAMES  # noqa: F401

from engine_backends.pricing import (  # noqa: F401
    CACHE_READ_PRICE_FACTOR,
    CACHE_WRITE_PRICE_FACTOR,
    CLAUDE_MODELS,
    GEMINI_MODELS,
    OPENAI_CHAT_URL,
    OPENAI_EXTRA_MODEL_CEILING,
    OPENAI_MODELS,
    PRICING_PER_MILLION_TOKENS,
    PROVIDER_CHECK_CACHE_KEY,
    _OPENAI_EXCLUDED_WORDS,
    _OPENAI_EXTRA_RE,
    _PRICED_FAMILY_PREFIXES,
    _highest_family_rates,
    estimate_cost,
    estimate_cost_for_engine,
    estimate_reflect_mode_cost,
    estimate_translation_cost,
    is_openai_extra_model,
    openai_listed_extra_models,
    resolve_cost_cap,
)
from engine_backends.shared import (  # noqa: F401
    ContentModerationBlocked,
    FreeTierDailyLimitReached,
    SDK_REQUEST_TIMEOUT,
    TranslationCancelled,
    _MAX_THROTTLE_WAIT_SECONDS,
    _SECRET_PATTERNS,
    _SLEEP_SLICE_SECONDS,
    _SOFT_REFUSAL_OPENERS,
    _URL_IN_TEXT,
    _URL_USERINFO_PATTERN,
    _add_usage,
    _cancel_check_var,
    _cancellable_sleep,
    _detect_soft_refusal_text,
    _empty_usage,
    _id_keyed_batch_request,
    _is_rate_limit_error,
    build_numbered_lines,
    call_with_backoff,
    claude_usage,
    extract_first_json_value,
    gemini_usage,
    parse_id_keyed_json,
    parse_json_array,
    redact_for_storage,
    redact_secrets,
    request_translations_with_retry,
    safe_url,
    strip_url_queries,
)
from engine_backends.prompts import (  # noqa: F401
    NOVEL_REFERENCE_BUDGET_CHARS,
    _BAIHE_GENRE_MARKERS,
    _MEDIUM_DESCRIPTIONS,
    _select_relevant_novel_passages,
    _wants_baihe_framing,
    build_batch_context,
    build_batch_user_message,
    build_claude_system_blocks,
    build_llm_instructions,
    build_previous_episode_summary_block,
    build_project_instructions_block,
    build_stable_prompt,
    build_stable_system_text,
    build_standalone_instructions,
)
from engine_backends.claude import (  # noqa: F401
    ClaudeEngine,
)
from engine_backends.openai_compat import (  # noqa: F401
    DeepSeekEngine,
    OpenAIEngine,
)
from engine_backends.gemini import (  # noqa: F401
    GEMINI_FREE_TIER_DEFAULT_LIMITS,
    GEMINI_FREE_TIER_LIMITS,
    GEMINI_FREE_TIER_TPM,
    GEMINI_FREE_TIER_UNAVAILABLE_MODELS,
    GeminiEngine,
    _GEMINI_RATE_HEADERS,
    _parse_gemini_rate_headers,
    gemini_free_tier_limits_for,
    gemini_rate_status_text,
    progress_message_with_rate_status,
)
from engine_backends.local import (  # noqa: F401
    NLLBEngine,
    NLLB_MODELS,
    OLLAMA_DEFAULT_MODEL,
    OLLAMA_MIN_NUM_CTX,
    OLLAMA_MODELS,
    OLLAMA_REACHABILITY_CACHE_SECONDS,
    OllamaEngine,
    OllamaUnavailableError,
    _NLLB_LANG_CODES,
    _OLLAMA_ID_KEYED_JSON_SCHEMA,
    _nllb_pipeline_cache,
    _ollama_chat,
    _ollama_reachability_cache,
    check_ollama_reachable,
    estimate_ollama_num_ctx,
)
from engine_backends.llm_tasks import (  # noqa: F401
    FLAG_REASONS,
    SYSTEM_FLAG_REASONS,
    TAG_SPEAKERS_PROMPT_VERSION,
    build_consistency_prompt,
    build_episode_summary_prompt,
    build_flag_prompt,
    call_llm_json,
    check_consistency_llm,
    flag_reason_label,
    flag_uncertain_lines,
    generate_episode_summary,
    matching_glossary_terms,
    rewrite_for_pacing_llm,
    smart_segment_lines,
    tag_speakers_by_id,
)
from engine_backends.engine_registry import (  # noqa: F401
    CAP_CHEAP,
    CAP_GROUNDED_SEARCH,
    CAP_INSTRUCTIONS,
    CAP_LOCAL,
    CAP_LONG_CONTEXT,
    CAP_TRANSLATE,
    ENGINES,
    ENGINE_CAPABILITIES,
    ENGINE_NOTES,
    FREE_ENGINES,
    GEMINI_FREE_TIER_NOTE,
    KEYLESS_ENGINES,
    MODEL_ID_RE,
    MODEL_OVERRIDE_DEFAULTS_KEY,
    MODEL_OVERRIDE_TIERS_KEY,
    REMOVED_ENGINES,
    TRANSLATION_ONLY_ENGINES,
    WORKFLOW_TIERS,
    _read_overrides,
    builtin_default_model,
    effective_default_model,
    effective_tier,
    effective_tier_model,
    engine_capabilities,
    engine_picker_label,
    engines_with_capability,
    get_engine,
    model_override_for_default,
    model_override_for_tier,
    override_models,
    unknown_engine_message,
)
from engine_backends.fallback import (  # noqa: F401
    FALLBACK_BACKOFF_BASE_SECONDS,
    FALLBACK_BACKOFF_CAP_SECONDS,
    FALLBACK_TRANSIENT_RETRIES,
    FallbackEngine,
    MAX_FALLBACK_ENGINES,
    _FALLBACK_NAME_HINTS,
    _TRANSIENT_NAME_HINTS,
    _fallback_sleep,
    fallback_chain_error,
    is_fallback_error,
    is_transient_fallback_error,
)
from engine_backends.standalone import (  # noqa: F401
    UnsupportedDirectionError,
    chunk_standalone_text,
    standalone_direction_support,
    standalone_translate,
)
from engine_backends.translate_pipeline import (  # noqa: F401
    TRANSLATE_PROMPT_VERSION,
    _translate_lines_with_engine,
    build_reflect_expressive_prompt,
    build_reflect_faithful_prompt,
    build_reflect_reflection_prompt,
    build_translation_context,
    reflect_translate_batch,
    translate_lines_with_engine,
)

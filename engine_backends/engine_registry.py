"""Engine registry: ENGINES, capability tags, notes, model overrides and get_engine."""

import inspect
import re
from .claude import ClaudeEngine
from .gemini import GeminiEngine
from .local import OllamaEngine
from .openai_compat import DeepSeekEngine, OpenAIEngine


# Draft / Standard / Release starting tiers -- sensible starting
# points for engine + Reflect mode + the Auto QC pass,
# applied in one click. Built-in and fixed, layered on top of (not
# replacing) the saved presets, which stay the way to keep a
# customized set of values. Engines follow §7.2's price ordering: DeepSeek
# is the cheapest capable LLM, Claude Sonnet the recommended default,
# Claude Opus the highest quality.
#
# auto_qc turns on Workspace's "Auto QC before export" check (auto_qc.py)
# -- the export section then lists lines with a factual-detail
# mismatch before anything is downloaded.
WORKFLOW_TIERS = {
    "draft": {"label": "Draft -- fast and cheap", "translation_engine": "deepseek",
              "engine_model": None, "reflect": False, "auto_qc": False},
    "standard": {"label": "Standard -- balanced", "translation_engine": "claude",
                 "engine_model": "claude-sonnet-5-5", "reflect": False, "auto_qc": False},
    "release": {"label": "Release -- best quality, checked before export",
                "translation_engine": "claude", "engine_model": "claude-opus-5-5",
                "reflect": True, "auto_qc": True},
}


ENGINES = {
    # Paid/normal engines first, then the free-for-testing ones grouped
    # together at the end (see FREE_ENGINES/engine_picker_label below) --
    # keeping them contiguous in iteration order is what lets every picker
    # built from ENGINES.keys() show them as one visual group.
    "claude": ClaudeEngine,
    "deepseek": DeepSeekEngine,
    "gemini": GeminiEngine,
    "openai": OpenAIEngine,
    "ollama": OllamaEngine,
}

# Pure machine-translation engines (no instruction-following, so every
# non-translate feature refuses them). None are offered now; the guards that
# read this set stay so adding one back needs no changes in the services.
# A set, not a frozenset: tests/fake_engine.py registers a stand-in in place,
# since fallback.py and the services hold references to this same object.
TRANSLATION_ONLY_ENGINES = set()

# Engines that used to be offered. Saved presets, routing rules, fallback
# chains and history rows may still name them; they are no longer in ENGINES,
# so running with one is refused with unknown_engine_message().
REMOVED_ENGINES = frozenset({"deepl", "google", "libretranslate", "nllb"})


def unknown_engine_message(engine_name) -> str:
    """The refusal text for an engine name that is not in ENGINES."""
    if engine_name in REMOVED_ENGINES:
        return f"The {engine_name} engine was removed. Pick another engine."
    return "Unknown engine."


# ---------------------------------------------------------------------------
# Capability tags per engine -- what each engine can actually do,
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
    "openai": frozenset({CAP_TRANSLATE, CAP_INSTRUCTIONS, CAP_LONG_CONTEXT}),
    "ollama": frozenset({CAP_TRANSLATE, CAP_INSTRUCTIONS, CAP_LOCAL, CAP_CHEAP}),
}


def engine_capabilities(engine_name: str) -> frozenset:
    """The capability tags of a registered engine (empty for an unknown one)."""
    return ENGINE_CAPABILITIES.get(engine_name, frozenset())


def engines_with_capability(tag: str) -> list:
    """Registered engines carrying `tag`, in ENGINES order."""
    return [name for name in ENGINES if tag in engine_capabilities(name)]


# Engines that are free to use every time, no conditions attached.
# Gemini isn't here -- it uses the same engine/API for free and paid
# keys, so whether a given run is "free" depends on the saved
# "My Gemini key is free-tier" setting (services/settings_service.py
# get_gemini_free_tier), not on which engine was picked. See
# engine_picker_label / estimate_cost_for_engine.
FREE_ENGINES = {"ollama"}

# Engines that run without an API key: a local model or a local server.
KEYLESS_ENGINES = {"ollama"}

# Ids a provider renamed but still serves: a preset or title that saved one
# keeps running, so the offered lists keep them after the built-in default moves.
LEGACY_MODEL_ALIASES = {"deepseek": ("deepseek-v4-flash",)}


# One short sentence each: the closed engine <select> shows it verbatim, and
# the Translate page shows its first sentence. Longer detail (rate limits,
# pricing caveats) lives in docs/engine-backends.md.
ENGINE_NOTES = {
    "claude": "Paid, cloud; best tone and character voice.",
    "deepseek": "Paid, cloud, very cheap; strong on Chinese.",
    "gemini": "Paid, cloud, cheap; strong on Chinese/Japanese.",
    "openai": "Paid, cloud; GPT-5 models, billed per token.",
    "ollama": "Free and private; local Gemma 4 on your GPU.",
}

# Shown instead of ENGINE_NOTES["gemini"] when the "My Gemini key is
# free-tier" checkbox (Settings) is ticked -- Gemini itself isn't in
# FREE_ENGINES since this only applies conditionally. See
# engine_picker_label, the one place that decides which note to show.
GEMINI_FREE_TIER_NOTE = "Free tier, rate-limited; Google may use your text."


def engine_picker_label(engine_name: str, gemini_free_tier: bool = False) -> str:
    """The descriptive text an engine picker shows next to `engine_name`
    (services/translate_service.list_engines puts it on every entry).
    A plain lookup except for Gemini, whose free-vs-paid status isn't a
    property of the engine itself but of the saved "My Gemini key is
    free-tier" setting."""
    if engine_name == "gemini" and gemini_free_tier:
        return GEMINI_FREE_TIER_NOTE
    return ENGINE_NOTES[engine_name]


# The user's own replacements for a built-in default model and for a workflow
# tier's model, kept as app settings ({engine: model} and {tier_key: model}).
# Only an explicit action in Diagnostics writes them (services/
# model_registry_service); this module only reads, and falls back to the
# built-in value for anything missing or odd-shaped.
MODEL_OVERRIDE_DEFAULTS_KEY = "model_overrides.defaults"
MODEL_OVERRIDE_TIERS_KEY = "model_overrides.tiers"
MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}")


def _read_overrides(setting_key: str, valid_keys) -> dict:
    try:
        import db
        raw = db.get_app_setting(setting_key)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items()
            if k in valid_keys and isinstance(v, str) and MODEL_ID_RE.fullmatch(v) and ".." not in v}


def model_override_for_default(engine_name: str):
    """The user's chosen model for `engine_name`'s built-in default, or None.
    An override for an engine this build no longer has is ignored."""
    return _read_overrides(MODEL_OVERRIDE_DEFAULTS_KEY, ENGINES).get(engine_name)


def model_override_for_tier(tier_key: str):
    return _read_overrides(MODEL_OVERRIDE_TIERS_KEY, WORKFLOW_TIERS).get(tier_key)


def builtin_default_model(engine_name: str):
    """The model an engine's constructor defaults to (None if it has none)."""
    cls = ENGINES.get(engine_name)
    if cls is None:
        return None
    param = inspect.signature(cls.__init__).parameters.get("model")
    return param.default if param is not None and param.default is not inspect.Parameter.empty else None


def effective_default_model(engine_name: str):
    """The model an engine uses when none is given: the user's override, else
    the built-in default. The one place that decision is made."""
    return model_override_for_default(engine_name) or builtin_default_model(engine_name)


def override_models(engine_name: str) -> list:
    """Models the user chose for `engine_name`'s default or for a workflow
    tier that runs on it; the offered lists include them so a run started
    with the effective model is not refused."""
    out = []
    default = model_override_for_default(engine_name)
    if default:
        out.append(default)
    for key, tier in WORKFLOW_TIERS.items():
        model = model_override_for_tier(key)
        if model and tier["translation_engine"] == engine_name and model not in out:
            out.append(model)
    return out


def effective_tier_model(tier_key: str):
    """A workflow tier's model: the user's override, else the tier's own, else
    (a tier that names none) its engine's effective default."""
    tier = WORKFLOW_TIERS[tier_key]
    return (model_override_for_tier(tier_key) or tier["engine_model"]
            or effective_default_model(tier["translation_engine"]))


def effective_tier(tier_key: str):
    """A copy of WORKFLOW_TIERS[tier_key] with engine_model as the tier really
    runs it, or None for an unknown tier. A tier with no model of its own
    stays None unless the user overrode it or its engine's default."""
    tier = WORKFLOW_TIERS.get(tier_key) if isinstance(tier_key, str) else None
    if tier is None:
        return None
    model = (model_override_for_tier(tier_key) or tier["engine_model"]
             or model_override_for_default(tier["translation_engine"]))
    return {**tier, "engine_model": model}


def get_engine(engine_name: str, api_key: str = None, model: str = None,
               free_tier: bool = False, base_url: str = None):
    cls = ENGINES[engine_name]
    model = model or model_override_for_default(engine_name)
    kwargs = {}
    if engine_name == "gemini":
        kwargs["free_tier"] = free_tier
    if engine_name == "ollama" and base_url:
        kwargs["base_url"] = base_url
    if model:
        return cls(api_key, model, **kwargs)
    return cls(api_key, **kwargs)

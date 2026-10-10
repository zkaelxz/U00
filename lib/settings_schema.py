"""Declared settings: one row per key the app keeps in `app_settings` (or `.env`).

Pure data plus `coerce`; nothing here reads the database, so `lib/` stays free
of app imports. `services/settings_service.get` pairs a row with the stored
value and the resolved choices.

`store_key` is the name the value is saved under today (`pref.default_engine`,
`bulk.auto_resume`, `assistant.developer_mode`, bare keys as they are), so
values saved by earlier versions keep reading back.
"""
import copy
import math
from dataclasses import dataclass
from typing import Any, Optional

TYPES = ("bool", "int", "float", "text", "choice", "path", "json")
SCOPES = ("app", "title", "developer")
WRITERS = ("pc_owner", "admin", "member")

# Repeated from memory_headroom, which lib may not import;
# tests/test_settings_schema.py fails if they drift apart.
_PATH_MAX = 1024
_STYLE_NOTE_MAX = 2000
_NUM_CTX_MAX = 1_048_576
_MONTHLY_CAP_MAX = 1_000_000.0
_KEEP_FREE_GB_MAX = 1024
UPLOAD_MB_DEFAULT = 20480
UPLOAD_MB_MIN = 100
UPLOAD_MB_MAX = 1_048_576


@dataclass(frozen=True)
class Setting:
    key: str
    store_key: str
    type: str
    default: Any = None
    scope: str = "app"
    # Every write is the PC owner's today: POST /api/settings is local_only.
    write: str = "pc_owner"
    secret: bool = False
    # "env" values live in .env, never in app_settings, and are never read through get().
    store: str = "db"
    label: str = ""
    help: str = ""
    # An empty tab means state the app keeps for itself, with no field on the page.
    tab: str = ""
    section: str = ""
    # A hand-written card owns the UI for this key.
    custom: bool = False
    # A symbolic name ("engines", "ocr_backends"); the service resolves it, so
    # this module needs no engine or OCR knowledge.
    choices: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    max_len: Optional[int] = None
    multiline: bool = False
    dev_only: bool = False
    # Some readers pull an out-of-range number into range instead of rejecting it
    # (gpu_max_parallel, qwen_asr_batch_size), so they never stop a job over a bad row.
    clamp: bool = False
    digits: Optional[int] = None


def _pref(name, type, default, **kw):
    return Setting(name, "pref." + name, type, default, **kw)


def _own(key, type, default, **kw):
    return Setting(key, key, type, default, **kw)


def _env(key, store_key, **kw):
    return Setting(key, store_key, "text", None, store="env", secret=True,
                   tab="translation", section="Keys", **kw)


_ASSISTANT = dict(scope="developer", dev_only=True, tab="system", section="Developer Mode")

SETTINGS = (
    # --- Translation and keys
    _pref("default_engine", "choice", "claude", choices="engines", label="Default engine",
          tab="translation", section="Defaults for new dramas"),
    _pref("default_locale", "choice", "en-US", choices="locales", label="English variant",
          tab="translation", section="Translation style"),
    _pref("default_style_note", "text", "", max_len=_STYLE_NOTE_MAX, multiline=True,
          label="Style note", tab="translation", section="Translation style"),
    _pref("scene_aware_batches", "bool", True, label="Start batches at scene breaks",
          tab="translation", section="Translation style"),
    _pref("episode_summary_engine", "choice", "ollama", choices="summary_engines",
          label="Episode summary engine", tab="translation", section="Defaults for new dramas"),
    _pref("monthly_cap_usd", "float", None, min=0, max=_MONTHLY_CAP_MAX,
          label="Monthly spending cap (USD)", tab="translation", section="Spending"),
    _own("gemini_free_tier", "bool", False, label="My Gemini key is free-tier",
         tab="translation", section="Keys"),
    _own("offer_provider_models", "bool", False, label="Offer models the app doesn't know yet",
         tab="translation", section="Keys"),
    _env("claude", "BAIHE_CLAUDE_KEY", label="Claude key"),
    _env("deepseek", "BAIHE_DEEPSEEK_KEY", label="DeepSeek key"),
    _env("gemini", "BAIHE_GEMINI_KEY", label="Gemini key"),
    _env("openai", "BAIHE_OPENAI_KEY", label="OpenAI key"),
    _env("groq", "BAIHE_GROQ_KEY", label="Groq key"),
    _env("hf_token", "BAIHE_HF_TOKEN", label="Hugging Face token"),
    # The address can carry credentials, so it is reported as a boolean like a key.
    _env("ollama_url", "BAIHE_OLLAMA_URL", label="Ollama URL"),
    _own("model_overrides.defaults", "json", {}),
    _own("model_overrides.tiers", "json", {}),
    _own("model_registry_provider_check", "json", None),
    _own("model_reeval_settings", "json", None),
    _own("model_reeval_production", "json", None),
    _own("model_reeval_last_run", "json", None),
    _own("model_reeval_last_attempt", "json", None),

    # --- Preferences
    _own("notify_on_completion", "bool", False, label="Notify when a job finishes",
         tab="preferences", section="Notifications"),
    _own("notify_categories", "json", {}, custom=True, tab="preferences",
         section="Notifications", label="Notification categories"),
    _own("auto_backup.settings", "json", {"enabled": False, "frequency": "daily",
                                          "include_media": False, "folder": ""},
         custom=True, tab="preferences", section="Automatic backups", label="Automatic backups"),
    _own("comic_save", "json", {}, custom=True, tab="preferences", section="Save folder",
         label="Save folder"),
    _own("update.auto_check", "bool", False, label="Check for updates automatically",
         tab="preferences", section="App updates"),
    _own("update.last_check_at", "float", 0.0, min=0),
    _own("household.share_by_default", "bool", False, label="Share new items with the household",
         tab="preferences", section="Sharing"),
    _own("web_search", "json", {}, custom=True, tab="preferences", section="Web search",
         label="Web search"),
    _own("extension_translation_engine", "json", None, custom=True, tab="preferences",
         section="Browser extension", label="Browser extension engine"),
    _own("grounded_search_usage", "json", {}),
    _own("remote_health", "json", None),

    # --- System
    _own("use_gpu", "bool", False, label="Use the GPU", tab="system", section="Performance"),
    _own("gpu_limit_enabled", "bool", True, label="Limit GPU jobs",
         tab="system", section="Performance"),
    _own("gpu_max_parallel", "int", 1, min=1, max=4, clamp=True, label="GPU jobs at once",
         tab="system", section="Performance"),
    _own("unload_ollama_before_transcribe", "bool", True,
         label="Free Ollama's GPU memory before transcribing", tab="system", section="Performance"),
    Setting("bulk_auto_resume", "bulk.auto_resume", "bool", False,
            label="Resume interrupted batches", tab="system",
            section="Resume interrupted batches"),
    _pref("keep_free_vram_gb", "float", 0.0, min=0, max=_KEEP_FREE_GB_MAX, digits=1,
          label="Keep free graphics memory (GB)", tab="system", section="Advanced"),
    _pref("keep_free_ram_gb", "float", 0.0, min=0, max=_KEEP_FREE_GB_MAX, digits=1,
          label="Keep free RAM (GB)", tab="system", section="Advanced"),
    _pref("ollama_num_ctx_override", "int", 0, min=0, max=_NUM_CTX_MAX,
          label="Ollama context window", tab="system", section="Advanced"),
    _pref("whisper_model_path", "path", "", max_len=_PATH_MAX, label="Offline Whisper model folder",
          tab="system", section="Advanced"),
    _pref("ocr_backend", "choice", "auto", choices="ocr_backends", label="OCR backend",
          tab="system", section="Advanced"),
    _pref("ocr_prefer_paddle_vl_manga", "bool", False, label="Prefer PaddleOCR-VL for manga",
          tab="system", section="Advanced"),
    _pref("tesseract_cmd", "path", "", max_len=_PATH_MAX, label="Tesseract program",
          tab="system", section="Advanced"),
    _pref("lncrawl_cmd", "path", "", max_len=_PATH_MAX, label="Novel downloader program",
          tab="system", section="Advanced"),
    _pref("cookies_browser", "choice", None, choices="cookie_browsers",
          label="Browser to take cookies from", tab="system", section="Advanced"),
    _pref("cookies_file", "path", "", max_len=_PATH_MAX, label="Cookies file",
          tab="system", section="Advanced"),
    _pref("max_upload_mb", "int", UPLOAD_MB_DEFAULT, min=UPLOAD_MB_MIN, max=UPLOAD_MB_MAX,
          label="Upload size limit (MB)", tab="system", section="Advanced"),
    _own("qwen_asr_batch_size", "int", 1, min=1, max=16, clamp=True,
         label="Qwen3-ASR batch size", tab="system", section="Transcription experiments"),
    _own("qwen_vad_refine_timing", "bool", False, label="Refine timing with the forced aligner",
         tab="system", section="Transcription experiments"),
    _own("mixed_languages", "bool", False, label="Detect the language of each speech span",
         tab="system", section="Transcription experiments"),
    _own("voice_detector", "choice", "auto", choices="voice_detectors", label="Voice detector",
         tab="system", section="Transcription experiments"),
    _own("transcribe_speed", "json", {}),
    _own("monthly_spend_reset_at", "text", None),

    # --- Developer Mode (maintenance assistant)
    Setting("developer_mode", "assistant.developer_mode", "bool", False,
            label="Developer Mode", **_ASSISTANT),
    _own("assistant.roles_enabled", "bool", False, label="Separate reviewer", **_ASSISTANT),
    _own("assistant.engine", "text", None, max_len=100, label="Assistant engine", **_ASSISTANT),
    _own("assistant.model", "text", None, max_len=100, label="Assistant model", **_ASSISTANT),
    _own("assistant.review_engine", "text", None, max_len=100, label="Reviewer engine",
         **_ASSISTANT),
    _own("assistant.review_model", "text", None, max_len=100, label="Reviewer model",
         **_ASSISTANT),
    _own("assistant.cloud_consent", "json", {}, label="Cloud consent per engine", **_ASSISTANT),
    _own("assistant.tiers", "json", None, label="Escalation ladder", **_ASSISTANT),
    _own("assistant.github.enabled", "bool", False, label="Open GitHub pull requests",
         **_ASSISTANT),
    _own("assistant.github.repo", "text", None, max_len=140, label="GitHub repository",
         **_ASSISTANT),
    _own("assistant.github.base_branch", "text", None, max_len=100, label="GitHub base branch",
         **_ASSISTANT),
)

BY_KEY = {s.key: s for s in SETTINGS}


def default_of(setting: Setting):
    # A fresh copy: callers must not be able to edit the declared default.
    return copy.deepcopy(setting.default)


def _check_text(s: Setting, raw):
    if not isinstance(raw, str):
        raise ValueError("not text")
    value = raw.strip()
    if s.max_len is not None and len(value) > s.max_len:
        raise ValueError("too long")
    allowed = "\n\t" if s.multiline else ""
    if any((ord(c) < 32 and c not in allowed) or ord(c) == 127 for c in value):
        raise ValueError("control characters")
    return value


def _check_number(s: Setting, raw):
    # bool is an int subclass; True must not read as 1.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw \
            or math.isinf(raw):
        raise ValueError("not a number")
    if s.type == "int" and not isinstance(raw, int):
        raise ValueError("not a whole number")
    if (s.min is not None and raw < s.min) or (s.max is not None and raw > s.max):
        raise ValueError("out of range")
    if s.type == "float":
        raw = float(raw)
        return round(raw, s.digits) if s.digits is not None else raw
    return raw


def _check_json(s: Setting, raw):
    # Some modules store the document as JSON text inside the JSON value.
    if isinstance(raw, str):
        import json
        raw = json.loads(raw)
    if not isinstance(raw, (dict, list)):
        raise ValueError("not a JSON document")
    if isinstance(s.default, (dict, list)) and not isinstance(raw, type(s.default)):
        raise ValueError("wrong JSON shape")
    return raw


def _parse(s: Setting, raw, choices):
    if s.type == "bool":
        if not isinstance(raw, bool):
            raise ValueError("not true or false")
        return raw
    if s.type in ("int", "float"):
        if s.clamp:
            n = int(raw)
            return max(int(s.min), min(n, int(s.max)))
        return _check_number(s, raw)
    if s.type == "choice":
        # Not stripped: " claude" is not an engine, as in the old validators.
        if not isinstance(raw, str) or (choices is not None and raw not in choices):
            raise ValueError("not an allowed choice")
        return raw or default_of(s)
    if s.type in ("text", "path"):
        value = _check_text(s, raw)
        return value if value or s.default is not None else default_of(s)
    return _check_json(s, raw)


def coerce(key: str, raw, choices=None):
    """The typed value for a stored `raw`, or the declared default when `raw` is
    missing, the wrong type, out of range or not one of `choices`.

    `choices` are the resolved options for a `choice` setting; without them any
    non-empty text passes. A stored `None` means "unset", which is also the
    default for every key today, so it needs no per-key handling.
    """
    s = BY_KEY[key]
    if raw is None:
        return default_of(s)
    try:
        return _parse(s, raw, choices)
    except (TypeError, ValueError, OverflowError):
        return default_of(s)


def validate(key: str, raw, choices=None):
    """The cleaned value to store for a write of `raw`, or ValueError(reason).

    Stricter than `coerce`, which forgives a bad stored row: a write is refused,
    and an unset value (None) puts the default back, except for a switch or a
    choice that has a default to fall back to, which must be given. A choice
    whose default is None (cookies_browser) is cleared by None or "".
    """
    s = BY_KEY[key]
    if s.clamp and (isinstance(raw, bool) or not isinstance(raw, int)):
        raise ValueError("not a whole number")
    if raw is None:
        if s.type == "bool":
            raise ValueError("not true or false")
        if s.type == "choice" and s.default is not None:
            raise ValueError("not an allowed choice")
        return default_of(s)
    if s.type == "choice" and s.default is None and raw == "":
        return None
    try:
        return _parse(s, raw, choices)
    except OverflowError:
        raise ValueError("not a number") from None

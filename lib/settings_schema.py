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
    unit: str = ""
    placeholder: str = ""
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


def _timing(name, type, default, lo, hi, section, label, help, unit="", dev=False, digits=None):
    """A timing or splitting threshold. The default repeats the module constant that
    stays the fallback for code that is not handed a value (tests/test_timing_settings.py
    fails if the two drift apart). The three sections are one Settings card, so every
    row shares the tab."""
    kw = dict(scope="developer", dev_only=True) if dev else {}
    return _pref(name, type, default, min=lo, max=hi, unit=unit, label=label, help=help,
                 tab="system", section=section, digits=digits, **kw)


# Pairs that must stay in order (lower key < upper key); set_settings refuses a write
# that breaks one, and the readers fall back to the defaults if stored rows disagree.
# The long-window cut search (2-10 s) can never reach the window (15-30 s), so the
# ranges alone keep that pair in order.
ORDERED_PAIRS = (
    ("sentence_group_gap_seconds", "scene_break_gap_seconds",
     "must be shorter than the scene-break gap, so a sentence can never straddle a scene break"),
    ("asmr_stop_threshold", "asmr_start_threshold",
     "must be lower than the start threshold, or a piece of speech could never end"),
)

VOICE = "Voice detection"
SPLIT = "Line splitting"
GROUPING = "Sentence grouping"
TIMING_SECTIONS = (VOICE, SPLIT, GROUPING)


def _env(key, store_key, **kw):
    return Setting(key, store_key, "text", None, store="env", secret=True,
                   tab="translation", section="Keys", **kw)


_ASSISTANT = dict(scope="developer", dev_only=True, tab="system", section="Developer Mode")

SETTINGS = (
    # --- Translation and keys
    _pref("default_engine", "choice", "claude", choices="engines", label="Default engine",
          tab="translation", section="Defaults for new dramas"),
    _pref("default_locale", "choice", "en-US", choices="locales", label="English variant",
          help="Spelling for new translations. Pre-fills the Translate form.",
          tab="translation", section="Translation style"),
    _pref("default_style_note", "text", "", max_len=_STYLE_NOTE_MAX, multiline=True,
          label="Style note",
          help="Pre-fills the Translate form's style note.", tab="translation", section="Translation style"),
    _pref("scene_aware_batches", "bool", True, label="Start batches at scene breaks",
          help="Translate batches start where the scene changes, so a scene is not split across two requests.",
          tab="translation", section="Translation style"),
    _pref("episode_summary_engine", "choice", "ollama", choices="summary_engines",
          label="Episode summary engine", tab="translation", section="Defaults for new dramas"),
    _pref("monthly_cap_usd", "float", None, min=0, max=_MONTHLY_CAP_MAX,
          label="Monthly cap", unit="USD",
          help="Compared with the estimated spend this month (UTC). Once it is used up, new translations won't start and a running one stops, keeping finished lines. 0 means no cap; blank uses the cap set in .env.", tab="translation", section="Spending"),
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
          label="Keep free graphics memory", unit="GB", placeholder="Off",
          help="For other programs on this PC, such as Jellyfin transcoding. A local model that would use this memory is not loaded and the job stops with a message. Sizes are estimates. Blank or 0 turns it off.", tab="system", section="Advanced"),
    _pref("keep_free_ram_gb", "float", 0.0, min=0, max=_KEEP_FREE_GB_MAX, digits=1,
          label="Keep free RAM", unit="GB", placeholder="Off",
          help="The same for system memory when a model runs on the CPU. Blank or 0 turns it off.", tab="system", section="Advanced"),
    _pref("ollama_num_ctx_override", "int", 0, min=0, max=_NUM_CTX_MAX,
          label="Ollama context window", unit="tokens", placeholder="Auto",
          help="Blank or 0 sizes it from each prompt (recommended). A value here can only raise it above that, never lower it.", tab="system", section="Advanced"),
    _pref("whisper_model_path", "path", "", max_len=_PATH_MAX, label="Offline Whisper model folder",
          help="For a PC that can't reach Hugging Face: a folder on the Baihe PC with a downloaded faster-whisper model. Blank downloads it on first use.",
          tab="system", section="Advanced"),
    _pref("ocr_backend", "choice", "auto", choices="ocr_backends", label="Default backend",
          help="Used when a page has no OCR choice. Auto picks manga_ocr for Japanese, PaddleOCR for Chinese and Korean, otherwise Tesseract.",
          tab="system", section="Advanced"),
    _pref("ocr_prefer_paddle_vl_manga", "bool", False, label="Japanese: prefer PaddleOCR-VL",
          help="Changes only what Auto picks for Japanese. Leave off unless it reads your pages better than manga_ocr.",
          tab="system", section="Advanced"),
    _pref("tesseract_cmd", "path", "", max_len=_PATH_MAX, label="Tesseract program", placeholder=r"C:\Program Files\Tesseract-OCR\tesseract.exe",
          help="Full path to tesseract.exe on the Baihe PC. Needed only if OCR says Tesseract is missing after you installed it. Leave blank otherwise.",
          tab="system", section="Advanced"),
    _pref("lncrawl_cmd", "path", "", max_len=_PATH_MAX, label="Novel downloader (lightnovel-crawler)", placeholder=r"C:\Users\you\.local\bin\lncrawl.exe",
          help="Full path to lncrawl on the Baihe PC (a separate program you install). Needed only if it isn't on PATH. The file must be named lncrawl or lightnovel-crawler. Blank finds it on PATH.",
          tab="system", section="Advanced"),
    _pref("cookies_browser", "choice", None, choices="cookie_browsers",
          label="Cookies from browser",
          help="yt-dlp reads this browser's cookies on the Baihe PC.", tab="system", section="Advanced"),
    _pref("cookies_file", "path", "", max_len=_PATH_MAX, label="Cookie file (cookies.txt)",
          help="Path to a cookies.txt file on the Baihe PC (export one with a browser add-on). Used instead of the browser above. Only the path is saved, never the file.",
          tab="system", section="Advanced"),
    _pref("max_upload_mb", "int", UPLOAD_MB_DEFAULT, min=UPLOAD_MB_MIN, max=UPLOAD_MB_MAX,
          label="Upload size limit (MB)", placeholder=str(UPLOAD_MB_DEFAULT),
          help="The largest audio, video or backup file you can upload to this PC (100 to 1,048,576 MB). Blank uses 20,480 MB. The drive needs room for it. Other devices can't upload.", tab="system", section="Advanced"),
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

    # --- Timing and splitting (one Settings card, three groups)
    _timing("sentence_split_min_silence_ms", "int", 2000, 1000, 3000, VOICE,
            "Silence that ends a piece (sentence mode)",
            "With 'Split lines by sentences' on, Whisper only breaks at pauses this long and the app then cuts at sentence ends. Lower gives Whisper shorter pieces, which can split sentences.",
            unit="ms", dev=True),
    _timing("sensitive_vad_threshold", "float", 0.35, 0.2, 0.45, VOICE,
            "Sensitive preset threshold",
            "The detector threshold the 'Sensitive' preset uses. Lower catches quieter speech but invents more text over music.",
            dev=True, digits=2),
    _timing("qwen_vad_threshold", "float", 0.5, 0.2, 0.8, VOICE,
            "Qwen3 detection: confidence",
            "How sure the detector must be that a sound is speech, for the Qwen3 'detect speech first' engine. Separate from the Whisper setting because the two engines need different tuning.",
            dev=True, digits=2),
    _timing("qwen_vad_min_silence_ms", "int", 300, 100, 3000, VOICE,
            "Qwen3 detection: silence that ends speech",
            "The shortest silence that ends a piece of speech for the Qwen3 speech-detection engines (both the short and the long-window one).",
            unit="ms", dev=True),
    _timing("qwen_vad_min_speech_ms", "int", 250, 0, 500, VOICE,
            "Qwen3 detection: shortest speech",
            "Speech blips shorter than this are ignored. Lower keeps short interjections; higher drops clicks and breaths.",
            unit="ms", dev=True),
    _timing("qwen_vad_pad_ms", "int", 100, 0, 500, VOICE,
            "Qwen3 detection: padding",
            "Audio kept on each side of detected speech. Raise if word endings are clipped; lower if neighbouring lines run together.",
            unit="ms", dev=True),
    _timing("qwen_long_vad_threshold", "float", 0.35, 0.2, 0.8, VOICE,
            "Long window: confidence",
            "How sure the detector must be that a sound is speech, for the Qwen3 long-window engine. Gentler than the short engine so quiet speech is kept.",
            dev=True, digits=2),
    _timing("qwen_long_vad_min_speech_ms", "int", 0, 0, 500, VOICE,
            "Long window: shortest speech",
            "Speech blips shorter than this are ignored by the long-window engine. 0 keeps everything the detector finds.",
            unit="ms", dev=True),
    _timing("qwen_long_vad_pad_ms", "int", 300, 0, 500, VOICE,
            "Long window: padding",
            "Audio kept on each side of detected speech by the long-window engine. Raise if word endings are clipped.",
            unit="ms", dev=True),
    _timing("qwen_merge_gap_seconds", "float", 1.0, 0.2, 3.0, VOICE,
            "Qwen3 detection: pauses kept in one piece",
            "Pauses shorter than this stay inside one transcription window. Raise for fewer cuts mid-sentence; lower to keep quick speaker changes apart.",
            unit="s", dev=True, digits=2),
    _timing("qwen_merge_gap_mixed_seconds", "float", 0.3, 0.1, 1.0, VOICE,
            "Qwen3 detection: pauses kept in one piece (mixed languages)",
            "The same, when the language of each piece is detected. Kept short so a quick change of language is not merged. Lower separates language switches better.",
            unit="s", dev=True, digits=2),
    _timing("qwen_span_cap_seconds", "float", 15.0, 8.0, 30.0, VOICE,
            "Qwen3 detection: longest piece",
            "Longest speech piece sent to the model. Longer pieces are cut at the quietest point near this length.",
            unit="s", dev=True, digits=1),
    _timing("qwen_span_cap_search_seconds", "float", 3.0, 1.0, 5.0, VOICE,
            "Qwen3 detection: cut search",
            "How far back from the longest-piece limit the app looks for the quietest place to cut.",
            unit="s", dev=True, digits=1),
    _timing("qwen_context_pad_seconds", "float", 2.0, 0.0, 3.0, VOICE,
            "Qwen3 detection: surrounding audio",
            "Extra surrounding audio the model hears but the line does not show. Helps accuracy (numbers come out as digits); 0 turns it off.",
            unit="s", dev=True, digits=1),
    _timing("qwen_long_merge_gap_seconds", "float", 3.0, 1.0, 6.0, VOICE,
            "Long window: pauses kept in one window",
            "Pauses up to this length stay inside one window; longer ones are treated as music or silence. Raise to keep more context; lower if text gets invented over music.",
            unit="s", dev=True, digits=1),
    _timing("qwen_long_window_seconds", "float", 30.0, 15.0, 30.0, VOICE,
            "Long window: longest window",
            "Longest window sent to the model at once. Longer gives more context; above 30 s the timing drifts, so 30 s is the most it accepts.",
            unit="s", dev=True, digits=1),
    _timing("qwen_long_cut_search_seconds", "float", 8.0, 2.0, 10.0, VOICE,
            "Long window: cut search",
            "How far back from the window limit the app may look for a quiet spot to cut. Wider finds better cuts but gives shorter windows. Must be less than the longest window.",
            unit="s", dev=True, digits=1),
    _timing("asmr_start_threshold", "float", 0.5, 0.3, 0.8, VOICE,
            "Whisper-voice detector: start",
            "How loud a whisper must score to start a piece of speech, when the voice detector is set to ASMR. Lower catches softer whispering.",
            dev=True, digits=2),
    _timing("asmr_stop_threshold", "float", 0.35, 0.2, 0.7, VOICE,
            "Whisper-voice detector: stop",
            "How far the score must fall to end a piece of speech, when the voice detector is set to ASMR. Must be lower than the start value.",
            dev=True, digits=2),
    _timing("speech_coverage_vad_threshold", "float", 0.3, 0.2, 0.5, VOICE,
            "Missed-speech check: confidence",
            "Detector threshold for 'Find missed speech' and 'Check timing'. Lower finds more possible missed speech, including music.",
            dev=True, digits=2),

    _timing("split_max_seconds", "float", 8.0, 4.0, 20.0, SPLIT,
            "Longest line (time)",
            "Lines that run longer than this are cut at sentence ends, commas or pauses. Raise for fewer, longer lines; lower for shorter lines that are easier to read.",
            unit="s", digits=1),
    _timing("split_max_cjk_chars", "int", 40, 20, 80, SPLIT,
            "Longest line (characters)",
            "Lines with more Chinese, Japanese or Korean characters than this are split. Lower gives shorter lines; very low values make choppy subtitles.",
            unit="characters"),
    _timing("split_min_piece_seconds", "float", 0.8, 0.3, 2.0, SPLIT,
            "Shortest piece (time)",
            "Pieces shorter than this are joined to a neighbour instead of shown on their own. Raise to avoid flashes; lower to allow very short lines.",
            unit="s", dev=True, digits=2),
    _timing("split_min_piece_cjk_chars", "int", 4, 2, 8, SPLIT,
            "Shortest piece (characters)",
            "Chinese, Japanese or Korean pieces with fewer characters than this are joined to a neighbour.",
            unit="characters", dev=True),
    _timing("split_min_piece_words", "int", 2, 1, 4, SPLIT,
            "Shortest piece (words)",
            "Pieces of other languages with fewer words than this are joined to a neighbour.",
            unit="words", dev=True),
    _timing("quick_split_seconds", "float", 12.0, 2.0, 120.0, SPLIT,
            "One-click split: longest line",
            "Duration cap used by the one-click 'Split long lines' button in Review. Lines longer than this are cut.",
            unit="s", digits=1),
    _timing("realign_min_seconds", "float", 12.0, 6.0, 30.0, SPLIT,
            "Realign: shortest line to re-cut",
            "With 'Realign long segments' on, lines at least this long are re-cut by word timing. Lower re-cuts more lines but takes longer.",
            unit="s", dev=True, digits=1),
    _timing("realign_pause_seconds", "float", 0.6, 0.2, 2.0, SPLIT,
            "Realign: pause that starts a new line",
            "During realignment, a pause this long between words starts a new line.",
            unit="s", dev=True, digits=2),
    _timing("stock_phrase_isolation_seconds", "float", 5.0, 2.0, 15.0, SPLIT,
            "Credit lines: silence needed to drop them",
            "Credit lines Whisper invents over silence ('Thanks for watching') are removed only when this much silence surrounds them. Lower removes more but may drop real dialogue.",
            unit="s", dev=True, digits=1),
    _timing("repeat_loop_min_repeats", "int", 4, 3, 8, SPLIT,
            "Repeated lines treated as a loop",
            "Identical lines repeated this many times in a row are treated as a model loop and kept once. Lower removes more but may drop real repetition.",
            unit="repeats", dev=True),
    _timing("retime_window_pad_seconds", "float", 3.0, 1.0, 5.0, SPLIT,
            "Re-time: search distance",
            "How far around a line Re-time searches for its speech. Raise if lines are badly late.",
            unit="s", dev=True, digits=1),
    _timing("gap_fill_min_gap_seconds", "float", 2.0, 0.5, 10.0, SPLIT,
            "Gaps offered for transcribing",
            "Silent stretches between lines at least this long are offered for 'transcribe this gap'.",
            unit="s", digits=1),

    _timing("scene_break_gap_seconds", "float", 3.0, 1.5, 15.0, GROUPING,
            "Pause that counts as a scene change",
            "Pauses at least this long count as scene changes, and translation batches end there. Raise for fewer, larger scenes. Must be longer than the sentence gap.",
            unit="s", digits=1),
    _timing("sentence_group_gap_seconds", "float", 1.0, 0.3, 2.5, GROUPING,
            "Pause inside one sentence",
            "With 'Translate by sentence' on, lines closer than this that don't end a sentence are translated together as one sentence. Must be shorter than the scene-change pause.",
            unit="s", digits=2),
    _timing("sentence_group_max_lines", "int", 4, 2, 8, GROUPING,
            "Most lines in one sentence",
            "Largest number of short lines joined into one sentence for translation.",
            unit="lines", dev=True),
    _timing("sentence_group_max_chars", "int", 60, 30, 150, GROUPING,
            "Longest sentence (characters)",
            "Longest joined sentence, counted in characters of the original text.",
            unit="characters", dev=True),
    _timing("sentence_group_max_seconds", "float", 12.0, 5.0, 30.0, GROUPING,
            "Longest sentence (time)",
            "Longest joined sentence, counted in seconds from its first line to its last.",
            unit="s", dev=True, digits=1),

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
    _own("update.include_prereleases", "bool", False, label="Include pre-releases in app updates",
         help="Offer beta releases when checking for updates. Only a newer version than this one is ever offered.",
         **_ASSISTANT),
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
        # "" is a deliberate pick only when the list offers it; otherwise it
        # means unset.
        if raw == "" and choices is not None and "" in choices:
            return raw
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

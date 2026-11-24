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
import re
from urllib.parse import urlsplit
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


_BLOCKED_PHRASES_DEFAULT = "\n".join((
    "please like, share and subscribe", "like, share and subscribe", "subtitles by Amara.org",
    "Amara.org", "點贊和分享", "点赞和分享", "訂閱我們的頻道", "订阅我们的频道",
    "頻道的臉書", "频道的脸书"))


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


OLLAMA_DEFAULT_PORT = 11434
OLLAMA_URL_DEFAULT = f"http://localhost:{OLLAMA_DEFAULT_PORT}"
# "host:port" with no scheme, as `ollama serve` prints it. A bare word without a
# port is left alone so it is still refused rather than guessed at.
_BARE_HOST_PORT = re.compile(r"^[^/:@\s]+:\d+(/|$)")


def normalize_ollama_url(raw) -> str:
    """The Ollama base URL every caller builds `/api/...` paths on. People paste
    "localhost:11434" (no scheme: requests refuses it) or Ollama's own
    "http://host:11434/api" (the app would then ask for /api/api/chat), so both
    are repaired here; a blank value means Ollama's own default address."""
    url = str(raw or "").strip()
    if not url:
        return OLLAMA_URL_DEFAULT
    if _BARE_HOST_PORT.match(url):
        url = "http://" + url
    url = url.rstrip("/")
    try:
        parts = urlsplit(url)
        own_api_path = parts.path.lower() == "/api" and parts.port == OLLAMA_DEFAULT_PORT
    except ValueError:
        own_api_path = False
    # Only Ollama's own port: behind a reverse proxy "/api" may be the
    # prefix the proxy serves Ollama under, and must stay.
    if own_api_path:
        url = url[:-4]
    return url


def _env(key, store_key, **kw):
    return Setting(key, store_key, "text", None, store="env", secret=True,
                   tab="translation", section="Keys", **kw)


_ASSISTANT: dict[str, Any] = dict(scope="developer", dev_only=True, tab="system", section="Developer Mode")

SETTINGS = (
    # --- Engines and keys
    _pref("default_engine", "choice", "claude", choices="engines", label="Default engine",
          help="The translation engine a new title is set to. Titles you already have keep theirs, and the Translate form can still pick another engine for a run.",
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
    _pref("scanlate_chapter_brief", "bool", True, label="Read the whole comic chapter before translating",
          help="Before a comic run translates page by page, one extra request reads the chapter's whole text and writes a short brief (who speaks, names, what happens) that every page's translation carries, so names and plot stay consistent across pages. It costs one request per chapter and delays the first page while the chapter is read; the brief is kept, so running the chapter again is free. Turn off to translate each page with only the page before it as context.",
          tab="translation", section="Translation style"),
    _pref("scanlate_page_image", "bool", True, label="Show the comic page to the translator",
          help="A comic run sends each page's picture along with its OCR text, so the model can correct OCR mistakes, tell who is speaking, read the tone and ignore a site's watermark. When the run's engine is a paid cloud engine (Claude, Gemini, OpenAI or DeepSeek) or an Ollama hosted model, the picture is read by Gemma 4 31B on Ollama's servers (gemma4:31b-cloud), which won the most pages in a blind test of six models at about 0.03 cents a page; that needs Ollama running and signed in (ollama signin), and sends the page to Ollama's servers. When Ollama can't serve it, the picture is read by the run's own engine (Claude reads it with Haiku 5.5; DeepSeek takes no pictures), and when that fails too the page is translated from its text alone. An Ollama model on this PC reads the picture itself and never sends it away. Turn off to translate from the OCR text only.",
          tab="translation", section="Translation style"),
    _pref("scanlate_sfx_gloss", "bool", False, label="Translate comic sound effects beside the art",
          help="A comic run leaves sound effects (like 털썩) as the artist drew them. Turn on to have each one translated into a short English word set small beside it; the original lettering is never painted out. It adds the effects to the page's translation request, and the word is only as good as the OCR read of the effect.",
          tab="translation", section="Translation style"),
    _pref("comic_glossary_custom", "text", "", multiline=True, max_len=12000,
          label="Your comic glossary lines",
          help="Quick read and Scanlate always carry a built-in glossary for Japanese, Korean and Chinese comics: forms of address, ranks and titles, fantasy and martial arts terms and common sound effects. Only the entries that appear on a page are sent with it. Write one line per change, as source = English (for example 師兄 = senior brother). A line replaces the built-in entry with the same source; leave the English empty (師兄 =) to drop the built-in entry; a line starting with # is a note. A series or title glossary term with the same source always wins over both.",
          tab="translation", section="Comic glossary"),
    _pref("scanlate_match_text_color", "bool", True, label="Match the original lettering colour in comic balloons",
          help="A comic balloon's English is set in the colour of the original lettering when that was a real colour (orange, blue, red); black and white lettering stays black, or white on a dark balloon. A colour you set by hand on a box always wins. Turn off to always use black or white. Applies to pages typeset after you change it.",
          tab="translation", section="Translation style"),
    _pref("scanlate_rect_layout", "bool", False, label="Set comic balloon text in one rectangle",
          help="A comic balloon's English follows the balloon's outline, so each line is as wide as the balloon is at that height and the block can look staggered in an oval. Turn on to set the text in the largest rectangle that fits inside the balloon instead: even lines, but often a smaller size. A balloon where that rectangle is too small keeps the outline layout. Applies to pages typeset after you turn it on.",
          tab="translation", section="Translation style"),
    _pref("episode_brief", "bool", True, label="Read the whole episode before translating",
          help="Before a subtitle or novel-narration run translates in batches, one extra request reads the whole episode and writes a short brief (who is who, register, recurring terms, ambiguous lines) that every batch carries; it is shown under Details > More details to edit, and an edited brief is used as written. It costs one request per episode and delays the first batch while the episode is read; the brief is kept, so running the episode again is free. A wrong brief colours every line, so check it. Turn off to translate each batch with only its neighbouring lines and the previous episode's summary as context.",
          tab="translation", section="Translation style"),
    _pref("stream_plain_prompt", "bool", True, label="Plain prompt for Gemma 4 stream translations",
          help="A stream title translated with Gemma 4 uses a short plain prompt: only the names and terms in your glossary, a short note on who talks to whom and the register, and a line asking for normal sentences with capitals and end punctuation. The preset rules, learned style and emotion notes are left out, and a run starts with no neighbouring-line context and 10 lines per request. On 127 marked lines it made the same or fewer mistakes than the full prompt (2 wrong and 2 mixed, against 2 wrong and 4 mixed), and it sends 40 to 65% fewer input tokens, which made a run about 35 to 45% cheaper. It was measured only on Gemma 4 (gemma4:31b-cloud) on Chinese streams. Turn off to use the full prompt again. Other models and other titles are not affected.",
          tab="translation", section="Translation style"),
    _pref("ollama_cloud_default", "bool", True, label="Allow a hosted Ollama model as the default",
          help="On (the default) lets a hosted Ollama model, Gemma 4 31B (gemma4:31b-cloud) included, be the default and be carried into re-runs, queued runs and Library batches. Off ignores a hosted model chosen as the default in Diagnostics > Models. A hosted model sends the text to Ollama's servers and is billed per token; the monthly spending cap still counts it. An account without paid-engine permission or its own Ollama key can run only Gemma 4 31B of the hosted models.",
          tab="translation", section="Translation style"),
    _pref("translate_reply_guard", "choice", "auto", choices="reply_guard_modes",
         label="Check translations for merged or shifted lines",
         help="After each batch, lines that look merged into a neighbour, shifted, duplicated or still in the source script are asked again once. Extra requests are billed like any other. Claude models rarely need this and weaker engines do, so Auto checks every engine except Claude and Gemini Pro. On always checks; Off never does.",
         tab="translation", section="Translation style"),
    _pref("novel_parallel_chapters", "int", 3, min=1, max=3, clamp=True,
          label="Novel chapters translated at once",
          help="A novel translated on Claude, GPT, DeepSeek or a paid Gemini key works through up to 3 chapters at the same time instead of one after another: about twice as fast on the same text, at about the same cost and quality in tests. Batches inside a chapter still run in order, so only the first batch of a chapter misses the English of the chapter before it. Several requests run at once, so a provider's rate limit can slow a run. Under a spending cap batches wait for room beside the ones already sent, so a capped run is less parallel and stays close to the cap (a batch dearer than any before it can still pass it by a little). An Ollama hosted model (a name ending in -cloud) also counts. A model on this PC and the Gemini free tier always translate one chapter at a time. 1 keeps the one-after-another run.",
          tab="translation", section="Translation style"),
    _pref("claude_reasoning", "choice", "default", choices="reasoning_modes",
          label="Claude Sonnet 5.5 reasoning",
          help="Default lets Sonnet 5.5 think before it answers, which reads slang best. Less turns that off: on a 90-line stream chunk it was about twice as fast and 40% cheaper but slightly worse on slang and nicknames. Applies to Translate runs on Sonnet 5.5 only; other Claude models are unchanged.",
          tab="translation", section="Translation style"),
    _pref("openai_reasoning", "choice", "less", choices="reasoning_modes",
          label="GPT-6 Luna reasoning",
          help="Less (the default) asks Luna for low reasoning: about 9 minutes per 2,000 lines and under half the cost on a 90-line stream chunk, with about the same quality (one slang line was lost). Default is Luna's full reasoning, which read slang best but took about 25 minutes per 2,000 lines. Applies to Translate runs on Luna only; Terra and Sol are unchanged.",
          tab="translation", section="Translation style"),
    _pref("translate_alignment_check", "bool", True, label="Flag translations that don't match their source",
          help="After a translation, compares each line's English with its source using the bge-m3 model on your Ollama server and flags lines that look shifted, skipped or about something else. With an AI translation engine, the few lines it doubts are also shown to that engine, billed like the translation. Needs `ollama pull bge-m3`; without it the check is skipped. No text is changed. On by default.",
          tab="translation", section="Translation style"),
    _pref("episode_summary_engine", "choice", "claude", choices="summary_engines",
          label="Episode summary engine",
          help="The engine that writes a title's short summary once all its lines are translated; the next episode in its series gets that summary as context. The default, Claude, wrote the most accurate summaries in a blind test; each summary is billed like any other request (a few cents). If the engine can't be reached or has no key, the summary is skipped and the translation is still saved.",
          tab="translation", section="Defaults for new dramas"),
    _pref("monthly_cap_usd", "float", None, min=0, max=_MONTHLY_CAP_MAX,
          label="Monthly cap", unit="USD",
          help="Compared with the estimated spend this month (UTC). Once it is used up, new translations won't start and a running one stops, keeping finished lines. 0 means no cap; blank uses the cap set in .env.", tab="translation", section="Spending"),
    _own("gemini_free_tier", "bool", False, label="My Gemini key is free-tier",
         tab="translation", section="Keys"),
    _own("offer_provider_models", "bool", False, label="Offer models the app doesn't know yet",
         help="Adds models your providers list that this app doesn't offer yet to the model pickers. One without a known price is estimated at the provider's highest rate until the app is updated. The list comes from your last \"Check providers now\" in Diagnostics.",
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
    _own("notify_on_completion", "bool", True, label="Notify when a job finishes",
         tab="preferences", section="Notifications"),
    _own("notify_categories", "json", {}, custom=True, tab="preferences",
         section="Notifications", label="Notification categories"),
    _own("auto_backup.settings", "json", {"enabled": False, "frequency": "daily",
                                          "include_media": False, "folder": ""},
         custom=True, tab="preferences", section="Automatic backups", label="Automatic backups"),
    _own("comic_save", "json", {}, custom=True, tab="preferences", section="Save folder",
         label="Save folder"),
    _own("update.auto_check", "bool", True, label="Check for updates automatically",
         tab="preferences", section="App updates"),
    _own("update.last_check_at", "float", 0.0, min=0),
    # The version the automatic update last ran Setup silently for: one try each.
    _own("update.auto_setup_tried", "text", ""),
    # Shown by the App updates card, which saves it through /api/system/update/settings.
    _own("update.include_prereleases", "bool", False, label="Include pre-releases in app updates",
         tab="preferences", section="App updates"),
    # Shown by the App updates card wherever an app update can be applied, which saves it
    # through /api/system/update/settings. The key predates the desktop app taking it too.
    _own("update.service_auto_apply", "bool", True,
         label="Install app updates automatically",
         tab="preferences", section="App updates"),
    _own("household.share_by_default", "bool", False, label="Share new items with the household",
         tab="preferences", section="Sharing"),
    _own("web_search", "json", {}, custom=True, tab="preferences", section="Web search",
         label="Web search"),
    _own("extension_translation_engine", "json", None, custom=True, tab="preferences",
         section="Browser extension", label="Browser extension engine"),
    _own("grounded_search_usage", "json", {}),
    _own("remote_health", "json", None),
    _own("resource_alert_rules", "json", None),

    # --- System
    _own("use_gpu", "bool", True, label="Use the GPU",
         help="Transcription, speaker detection and comic text removal run on the GPU when there is one (much faster). Off runs them on the processor, and a transcription set to separate vocals first stops, since that step cannot be kept off the GPU. Voice cloning and Ollama models are not affected.",
         tab="system", section="Performance"),
    _own("gpu_limit_enabled", "bool", True, label="Limit GPU jobs",
         help="Jobs beyond \"GPU jobs at once\" wait in line, so the GPU does not run out of memory. Off lets every GPU job start at once.",
         tab="system", section="Performance"),
    _own("gpu_max_parallel", "int", 1, min=1, max=4, clamp=True, label="GPU jobs at once",
         help="How many GPU jobs run together while the limit is on. A job joins a running one only if the GPU has 2 GB free; without nvidia-smi they run one at a time.",
         tab="system", section="Performance"),
    _own("unload_ollama_before_transcribe", "bool", True,
         label="Free Ollama's GPU memory before transcribing", tab="system", section="Performance"),
    _pref("second_hearing", "bool", False, label="Hear doubtful lines a second time",
          help="After a transcription, the lines Whisper was least sure of (up to 30) are heard again by Qwen3-ASR, or by Whisper on a Qwen3 title, and flagged where the two disagree. Nothing is rewritten. It adds a second model load and some GPU time to every run, and waits like any GPU job.",
          tab="system", section="Performance"),
    Setting("bulk_auto_resume", "bulk.auto_resume", "bool", True,
            label="Resume interrupted batches",
            help="After a restart, batches on a free local engine start again by themselves. Batches on a paid engine stay paused until you press Resume, and one notification says they are waiting.", tab="system",
            section="Resume interrupted batches"),
    _pref("keep_free_vram_gb", "float", 0.0, min=0, max=_KEEP_FREE_GB_MAX, digits=1,
          label="Keep free graphics memory", unit="GB", placeholder="Off",
          help="For other programs on this PC, such as Jellyfin transcoding. A local model that would use this memory is not loaded and the job stops with a message. Sizes are estimates. Blank or 0 turns it off.", tab="system", section="Advanced"),
    _pref("keep_free_ram_gb", "float", 0.0, min=0, max=_KEEP_FREE_GB_MAX, digits=1,
          label="Keep free RAM", unit="GB", placeholder="Off",
          help="The same for system memory when a model runs on the CPU. Blank or 0 turns it off.", tab="system", section="Advanced"),
    # Read by jobs/gpu_guard.py on every GPU-queue poll.
    _pref("gpu_guard", "bool", True, label="Pause GPU jobs when the GPU is short of memory or hot",
          help="GPU jobs wait while free graphics memory is under Keep free graphics memory (1 GB when that is off) or the GPU temperature reaches the limit below. Idle models are unloaded when memory runs short. Needs Limit GPU jobs on.",
          tab="system", section="Advanced"),
    _pref("gpu_guard_temp_c", "int", 85, min=60, max=100, label="GPU too hot at", unit="°C",
          help="GPU temperature. Jobs wait at this or above, and go again once the GPU is 5 °C cooler.",
          tab="system", section="Advanced"),
    _pref("gpu_guard_stop_minutes", "int", 2, min=0, max=60, label="Stop a running job after", unit="min",
          placeholder="Never",
          help="If the GPU stays short of memory or hot this long, a running transcription, speaker detection or local-model translation stops and starts again by itself once the card is clear. 0 never stops a running job.",
          tab="system", section="Advanced"),
    _pref("ollama_num_ctx_override", "int", 0, min=0, max=_NUM_CTX_MAX,
          label="Ollama context window", unit="tokens", placeholder="Auto",
          help="Blank or 0 sizes it from each prompt (recommended). A value here can only raise it above that, never lower it.", tab="system", section="Advanced"),
    _pref("whisper_model_path", "path", "", max_len=_PATH_MAX, label="Offline Whisper model folder",
          help="For a PC that can't reach Hugging Face: a folder on the Baihe PC with a downloaded faster-whisper model. Blank downloads it on first use.",
          tab="system", section="Advanced"),
    _pref("ocr_backend", "choice", "auto", choices="ocr_backends", label="Default backend",
          help="Auto uses manga_ocr for Japanese, RapidOCR for Chinese and Korean, else Tesseract.",
          tab="system", section="Advanced"),
    _pref("speaker_model", "choice", "nemotron", choices="speaker_models", label="Speaker detection model",
          help="Nemotron-3 is NVIDIA's newer model and mixed up fewer lines than pyannote on the titles we scored; "
               "it needs transformers 5.18 or newer and librosa (the Speaker detection pack in Diagnostics) "
               "and downloads about 380 MB the first time. "
               "Pyannote (community-1) is the older choice: pick it to go back. "
               "Nemotron ignores a typed speaker count; pyannote runs only when Nemotron isn't available on this PC.",
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
    _pref("narration_pace", "float", 0.9, min=0.75, max=1.25, digits=2,
          label="Narration pace",
          help="How fast a novel's narration plays: 1 is as the voice speaks; the default 0.9 is about 10% slower, for a calmer audiobook reading. The voice keeps its pitch and the gap between lines stays the same. Applies the next time Listen makes the narration, and needs no new clips, so a re-run only joins the saved ones again.",
          tab="system", section="Advanced"),
    _pref("max_upload_mb", "int", UPLOAD_MB_DEFAULT, min=UPLOAD_MB_MIN, max=UPLOAD_MB_MAX,
          label="Upload size limit (MB)", placeholder=str(UPLOAD_MB_DEFAULT),
          help="The largest audio, video or backup file you can upload to this PC (100 to 1,048,576 MB). Blank uses 20,480 MB. The drive needs room for it. Other devices can't upload.", tab="system", section="Advanced"),
    _own("qwen_asr_batch_size", "int", 1, min=1, max=16, clamp=True,
         label="Qwen3-ASR batch size", tab="system", section="Transcription experiments"),
    _own("qwen_vad_refine_timing", "bool", False, label="Refine timing with the forced aligner",
         help="For the experimental Qwen3 ASR with speech detection engine only; Whisper ignores it, and Qwen3 ASR on long windows always does it. Tightens each line's start and end inside its speech span. Slower; lines whose timing stays uncertain are flagged. Ignored while \"Detect the language of each speech span\" is on.",
         tab="system", section="Transcription experiments"),
    _own("mixed_languages", "bool", False, label="Detect the language of each speech span",
         help="For titles where more than one language is spoken. Detects each stretch's language and marks lines that differ from the title's language in Review. Works with Whisper on this PC and the Qwen3 ASR engines that need no Whisper; Groq ignores it, and it turns the Qwen3 forced aligner off. Slower. Off transcribes everything in the title's language.",
         tab="system", section="Transcription experiments"),
    # The default repeats whisper_hallucination.BLOCKED_PHRASES_DEFAULT (lib may not import it);
    # tests/test_blocked_phrases.py fails if they drift apart.
    _pref("blocked_phrases", "text", _BLOCKED_PHRASES_DEFAULT, multiline=True, max_len=4000,
         label="Blocked phrases",
         help="One phrase per line. A subtitle line that contains one is removed when a title is transcribed only if Whisper made it up (the stock lines it invents, such as 'please like, share and subscribe'): the voice detector hears no speech under it. Where it hears a voice, the line is kept. Without the detector (or with Difficult audio on), only the phrase alone in silence is removed. Case, spaces and punctuation are ignored; a phrase needs at least 4 letters. Clear the box to block nothing. Applies to transcriptions that start afterwards.",
         tab="system", section="Transcription experiments"),
    _own("voice_detector", "choice", "auto", choices="voice_detectors", label="Voice detector",
         help="Which speech detector the Qwen3 ASR engines that need no Whisper use; Whisper uses its own. Auto picks the ASMR detector for a Japanese ASMR title once its model is downloaded, and Standard otherwise. The ASMR detector is trained on Japanese ASMR; without its model a run uses Standard and says so.",
         tab="system", section="Transcription experiments"),
    _own("transcribe_speed", "json", {}),
    _own("inbox", "json", {}),
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
    _timing("difficult_no_speech_threshold", "float", 0.8, 0.5, 1.0, VOICE,
            "Difficult audio: no-speech cutoff",
            "With Difficult audio on, Whisper drops a stretch it rates as more likely non-speech than this, unless it is confident of the words. Higher keeps more screamed or whispered speech but adds more false lines. Whisper's own default is 0.6.",
            dev=True, digits=2),
    _timing("difficult_log_prob_threshold", "float", -1.5, -3.0, -0.5, VOICE,
            "Difficult audio: confidence floor",
            "With Difficult audio on, the average word confidence (log probability) below which Whisper may drop a likely non-speech stretch. Lower keeps more low-confidence speech. Whisper's own default is -1.0.",
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
    # Access, not a developer knob: its card is on People and access.
    _own("remote_uploads", "bool", False, label="Allow uploads from away", custom=True,
         tab="system", section="Uploads from away"),
    # When the switch above was last turned on; it lapses REMOTE_UPLOADS_DAYS later. Not the
    # older "remote_uploads.on_at": a stamp from when the switch also needed Developer Mode must
    # not count, or a PC with that mode off would start allowing uploads on its own.
    _own("remote_uploads.since", "float", 0.0, min=0),
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
            if s.min is None or s.max is None:
                raise TypeError(f"{s.key} clamps but has no min and max")
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

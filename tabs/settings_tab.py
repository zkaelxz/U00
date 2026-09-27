"""
tabs/settings.py -- a sidebar panel where API keys are entered once per
session and reused as defaults across every tab, instead of retyping
them in Workspace, Reader, Scanlate, Navigator, and Discover separately.

Session-only by default: nothing here is written to disk unless you
click "Save to .env" next to an API key/endpoint field, which writes
that one value into the project's local .env file (plain text, not
encrypted -- see the caption next to the button). Each tab's own key
field still works independently if you want to override it there.

Split into two tiers, Common and Advanced: Common holds what changes
often (reading preferences, OCR default, per-drama defaults, API keys);
Advanced holds what's set once and rarely touched again (offline
model paths, GPU/performance tuning, spending cap, download cookies).
"""
import os
from common import st, synced_api_key_input
import ocr
import translate_engines
import ui_theme
import video_download


def _load_env_defaults(env_path: str = None):
    """Reads API keys from a local .env file (or real environment
    variables) on every render, so editing .env while the app is still
    running is picked up on the next rerun instead of needing a full
    restart. .env is gitignored -- keys never reach the database or a
    commit. Values already set in session_state (typed manually, or
    loaded from .env on an earlier run) always win over the file, so
    this can't silently override a key you've since changed by hand --
    that's what actually protects "already typed wins", not how often
    this function runs, so calling it every render is safe. Re-parsing
    a few-line file on each rerun is cheap enough not to matter.

    env_path: override for tests -- normally the project root's .env,
    derived from this file's own location.
    """
    env = {}
    if env_path is None:
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        try:
            # utf-8-sig, not utf-8: Notepad (the default editor most Windows
            # users would reach for to make a .env file) saves UTF-8 files
            # WITH a byte-order-mark by default. Plain utf-8 doesn't strip
            # it, so the first key in the file silently comes back as
            # "﻿HF_TOKEN" instead of "HF_TOKEN" -- a real, confirmed
            # failure mode: whichever variable happens to be first in the
            # file never matches its lookup name, while later variables
            # (or the same file saved by an editor that doesn't add a BOM)
            # work fine, which is exactly what makes it so confusing to
            # diagnose from the outside. utf-8-sig strips a BOM if present
            # and behaves identically to utf-8 when there isn't one.
            with open(env_path, encoding="utf-8-sig") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass  # a malformed .env should never stop the app starting

    for settings_key, env_names in _ENV_NAMES.items():
        if st.session_state.get(f"settings_{settings_key}"):
            continue
        for name in env_names:
            val = env.get(name) or os.environ.get(name)
            if val:
                st.session_state[f"settings_{settings_key}"] = val
                break


# Per settings key, the env var name(s) _load_env_defaults() reads on
# startup, in priority order -- the first entry is also the canonical
# name save_key_to_env() writes back, so a saved key round-trips through
# the exact same name it would be read back under.
_ENV_NAMES = {
    "claude": ("BAIHE_CLAUDE_KEY", "ANTHROPIC_API_KEY"),
    "deepseek": ("BAIHE_DEEPSEEK_KEY", "DEEPSEEK_API_KEY"),
    # Deliberately NOT falling back to GOOGLE_API_KEY here -- that name
    # is already claimed by the separate Google Translate engine above,
    # and a Cloud Translation key isn't guaranteed to also work as a
    # Gemini API key (different products, often different projects).
    "gemini": ("BAIHE_GEMINI_KEY", "GEMINI_API_KEY"),
    "deepl": ("BAIHE_DEEPL_KEY", "DEEPL_API_KEY"),
    "google": ("BAIHE_GOOGLE_KEY", "GOOGLE_API_KEY"),
    "groq": ("BAIHE_GROQ_KEY", "GROQ_API_KEY"),
    "hf_token": ("BAIHE_HF_TOKEN", "HF_TOKEN", "HUGGINGFACE_TOKEN"),
    "ollama_url": ("BAIHE_OLLAMA_URL",),
    "libretranslate_url": ("BAIHE_LIBRETRANSLATE_URL",),
    "gpt_sovits_url": ("BAIHE_GPT_SOVITS_URL",),
    "monthly_cap_usd": ("BAIHE_MONTHLY_CAP_USD",),
}


def save_key_to_env(settings_key: str, value: str, env_path: str = None) -> str:
    """Writes settings_key's current value into the local .env file under
    its canonical name (_ENV_NAMES[settings_key][0] -- the same name
    _load_env_defaults() reads back), updating an existing line in place
    rather than appending a duplicate. Creates the file if it doesn't
    exist yet. Plain text, same as every other .env value -- not
    encrypted, which is why the Settings UI says so next to the button
    that calls this.

    Returns the env var name written, so the caller can confirm to the
    user which line changed.
    """
    if env_path is None:
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    var_name = _ENV_NAMES[settings_key][0]

    lines = []
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8-sig") as fh:
            lines = fh.readlines()

    new_line = f"{var_name}={value}\n"
    for i, line in enumerate(lines):
        if line.strip().split("=", 1)[0].strip() == var_name:
            lines[i] = new_line
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(new_line)

    with open(env_path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    return var_name


SETTINGS_KEYS = {
    "claude": "Claude / Anthropic API key",
    "deepseek": "DeepSeek API key",
    "gemini": "Gemini API key",
    "deepl": "DeepL API key",
    "google": "Google Translate API key",
    "ollama_url": "Ollama base URL",
    "libretranslate_url": "LibreTranslate/LTEngine base URL",
    "gpt_sovits_url": "GPT-SoVITS server URL (voice cloning; default http://127.0.0.1:9880)",
    "groq": "Groq API key (cloud speech recognition)",
    "hf_token": "Hugging Face token (diarization)",
}


def render_settings_sidebar():
    _load_env_defaults()
    with st.sidebar:
        ui_theme.type_scale_scope()
        st.header("⚙️ Settings")
        st.session_state["app_dark_mode"] = st.toggle(
            "🌙 Dark mode", value=st.session_state.get("app_dark_mode", False),
            help="Applies to the whole app. The Reader has its own separate theme "
                 "(light/sepia/dark) under Reading experience.")
        st.caption("Entered once, reused as defaults everywhere in this session. "
                  "Never written to the database. To avoid retyping them after a "
                  "restart, either put them in a `.env` file yourself (gitignored -- "
                  "see .env.example) or click \"Save to .env\" below each field.")

        st.subheader("Common")
        st.caption("Changed often -- start here.")

        with st.expander("Reading experience", expanded=False):
            st.session_state["spoiler_free_mode"] = st.checkbox(
                "🙈 Spoiler-free mode", value=st.session_state.get("spoiler_free_mode", True),
                help="Scopes every AI feature -- wiki, recaps, character lookup -- to the page "
                     "you've reached. Turn off only if you've finished the story.")
            st.session_state["reader_font_size"] = st.slider(
                "Text size", 14, 36, st.session_state.get("reader_font_size", 22))
            st.session_state["reader_line_height"] = st.slider(
                "Line spacing", 1.5, 4.0, st.session_state.get("reader_line_height", 2.4), 0.1)
            st.session_state["reader_max_width"] = st.slider(
                "Content width (px)", 600, 1600, st.session_state.get("reader_max_width", 1200), 50)
            st.session_state["reader_theme"] = st.selectbox(
                "Theme", ["light", "sepia", "dark"],
                index=["light", "sepia", "dark"].index(st.session_state.get("reader_theme", "light")))
            st.session_state["reader_font"] = st.selectbox(
                "Font", ["system", "serif", "sans-serif", "monospace"],
                index=["system", "serif", "sans-serif", "monospace"].index(
                    st.session_state.get("reader_font", "system")))

        with st.expander("OCR", expanded=False):
            _default_ocr_backend = st.session_state.get("settings_ocr_backend", "auto")
            if _default_ocr_backend not in ocr.OCR_BACKEND_OPTIONS:
                _default_ocr_backend = "auto"
            st.session_state["settings_ocr_backend"] = st.selectbox(
                "Default OCR backend", ocr.OCR_BACKEND_OPTIONS,
                format_func=lambda b: "🤖 Auto (by source language)" if b == "auto" else b,
                index=ocr.OCR_BACKEND_OPTIONS.index(_default_ocr_backend),
                help="Used everywhere OCR runs (Scanlate, novel narration from image pages) "
                     "unless overridden for one page there. Auto picks manga_ocr for "
                     "Japanese, paddle for Chinese/Korean, tesseract otherwise.")
            st.session_state["settings_ocr_prefer_paddle_vl_manga"] = st.checkbox(
                "For Japanese, Auto prefers PaddleOCR-VL-For-Manga over manga_ocr",
                value=st.session_state.get("settings_ocr_prefer_paddle_vl_manga", False),
                help="Opt-in second Japanese backend -- only affects what Auto picks. Its own "
                     "model card doesn't benchmark against manga_ocr, so leave this off until "
                     "a real side-by-side on your own pages says it's actually better.")
            st.session_state["settings_tesseract_cmd"] = st.text_input(
                "Tesseract binary path (optional)",
                value=st.session_state.get("settings_tesseract_cmd", ""),
                placeholder=r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                help="Only needed if OCR fails with \"tesseract is not installed or it's not "
                     "in your PATH\" even after installing it -- the Windows installer doesn't "
                     "always add itself to PATH. Point this at tesseract.exe directly instead "
                     "of editing a system PATH variable by hand. Leave blank if OCR already "
                     "works.")

        with st.expander("Defaults for new dramas", expanded=False):
            _default_engine_options = ["claude", "deepseek", "deepl", "google", "ollama",
                                        "libretranslate", "nllb"]
            st.session_state["settings_default_engine"] = st.selectbox(
                "Default translation engine", _default_engine_options,
                index=_default_engine_options.index(
                    st.session_state.get("settings_default_engine", "claude"))
                    if st.session_state.get("settings_default_engine", "claude") in _default_engine_options
                    else 0)
            st.session_state["settings_default_locale"] = st.selectbox(
                "Default English variant", ["en-US", "en-GB", "en-AU"],
                index=["en-US", "en-GB", "en-AU"].index(st.session_state.get("settings_default_locale", "en-US")))
            st.session_state["settings_default_style_note"] = st.text_input(
                "Default style notes", value=st.session_state.get("settings_default_style_note", ""))

        with st.expander("API keys & endpoints", expanded=False):
            st.caption("\"Save to .env\" writes that one value into this project's local "
                      "`.env` file, in plain text (not encrypted) -- fine for this app's "
                      "single-user, local-machine threat model, but worth knowing before "
                      "saving a key on a shared machine.")
            for key, label in SETTINGS_KEYS.items():
                is_url = key.endswith("_url")
                val = synced_api_key_input(label, key, f"settings_input_{key}",
                                            type="default" if is_url else "password")
                if st.button("💾 Save to .env", key=f"save_env_{key}", disabled=not val,
                             help=f"Writes {_ENV_NAMES[key][0]}=... to .env so this survives "
                                  "an app restart."):
                    var_name = save_key_to_env(key, val)
                    st.toast(f"Saved {var_name} to .env.", icon="💾")
                if key == "gemini":
                    st.session_state["gemini_free_tier"] = st.checkbox(
                        "My Gemini key is free-tier",
                        value=st.session_state.get("gemini_free_tier", False),
                        help="Free-tier Gemini keys are rate-limited: Flash allows "
                             f"{translate_engines.GEMINI_FREE_TIER_LIMITS['flash']['rpm']} "
                             f"requests/min / {translate_engines.GEMINI_FREE_TIER_LIMITS['flash']['rpd']}"
                             "/day, Flash-Lite allows "
                             f"{translate_engines.GEMINI_FREE_TIER_LIMITS['flash-lite']['rpm']}"
                             f"/min / {translate_engines.GEMINI_FREE_TIER_LIMITS['flash-lite']['rpd']}"
                             "/day, with a shared "
                             f"{translate_engines.GEMINI_FREE_TIER_TPM:,} tokens/minute ceiling "
                             "across models -- Pro isn't available on the free tier at all. Google "
                             "may use the text you send to improve its products. Ticking this "
                             "labels Gemini as free everywhere it's picked, and paces requests "
                             "automatically against all three limits instead of hitting rate-limit "
                             "errors.")

        st.subheader("Advanced")
        st.caption("Changed rarely -- tucked away so they don't crowd the settings above.")

        with st.expander("Offline / restricted networks", expanded=False):
            st.session_state["settings_whisper_model_path"] = st.text_input(
                "Local Whisper model folder (optional)",
                value=st.session_state.get("settings_whisper_model_path", ""),
                help="If this machine can't reach Hugging Face, download a faster-whisper "
                     "model elsewhere and point at the folder here.")
            st.caption("Leave blank to download automatically on first use. Models are cached "
                      "after the first download, so this only matters once per model size.")

        with st.expander("Performance", expanded=False):
            st.session_state["use_gpu"] = st.checkbox(
                "Use GPU where available", value=st.session_state.get("use_gpu", False),
                help="Speeds up Whisper transcription, diarization, and local voice cloning "
                     "if you have a CUDA GPU. Falls back to CPU automatically if unavailable.")
            st.caption("Requires a CUDA-capable GPU and the GPU build of PyTorch. "
                      "If transcription errors after enabling this, turn it back off.")
            st.session_state["settings_limit_one_gpu_job"] = st.checkbox(
                "Limit to one GPU job at a time",
                value=st.session_state.get("settings_limit_one_gpu_job", True),
                help="Transcription, diarization, OCR, TTS/dub, and local-model (Ollama) "
                     "translation all load a model onto the GPU. On an 8-12GB consumer GPU, "
                     "two of these running at once (e.g. from two different tabs) can overwhelm "
                     "its VRAM -- with this on, a second GPU-touching job waits for the first "
                     "to finish instead of starting alongside it. Turn off only if you know your "
                     "hardware can handle several at once (24GB+ VRAM).")
            import background_jobs
            background_jobs.set_gpu_limit_enabled(st.session_state["settings_limit_one_gpu_job"])

            st.session_state["settings_notify_on_job_done"] = st.checkbox(
                "🔔 Desktop notification when a background job finishes",
                value=st.session_state.get("settings_notify_on_job_done", False),
                help="A local OS notification (not email -- nothing to email to for a local "
                     "single-user app) so a long job, especially an unattended bulk series "
                     "translate, doesn't need the tab watched the whole time. Off by default "
                     "until confirmed reliable on your desktop -- needs `pip install plyer`; "
                     "silently does nothing if it's missing or your desktop has no notification "
                     "daemon.")
            background_jobs.set_notify_on_completion(st.session_state["settings_notify_on_job_done"])

            st.session_state["settings_ollama_num_ctx_override"] = st.number_input(
                "Ollama context window override (num_ctx, optional)",
                min_value=0, step=1024,
                value=st.session_state.get("settings_ollama_num_ctx_override", 0) or 0,
                help="Leave at 0 to size this automatically from the actual prompt each "
                     "time (recommended). Ollama's own default context window can be as "
                     "small as 2-4k tokens and silently truncates a longer prompt with no "
                     "error -- a value set here can only raise the window above the "
                     "automatic estimate, never below it, so it can't reintroduce that bug.")

        with st.expander("Spending", expanded=False):
            try:
                _cap_default = float(st.session_state.get("settings_monthly_cap_usd") or 0)
            except (TypeError, ValueError):
                _cap_default = 0.0
            st.session_state["settings_monthly_cap_usd"] = st.number_input(
                "Monthly spending cap (USD, 0 = none)", min_value=0.0, step=1.0,
                value=_cap_default,
                help="Checked against the estimated spend already logged this calendar month "
                     "(UTC). A translation won't start once it's used up, and a running one "
                     "stops cleanly -- keeping every finished line -- when it reaches what's "
                     "left. Estimates, not a bill. To keep it across restarts, set "
                     "BAIHE_MONTHLY_CAP_USD in your .env file.")

        with st.expander("Downloads", expanded=False):
            st.caption("Some sites (TikTok and Instagram especially, more aggressively than "
                      "YouTube) block plain unauthenticated requests. Pass yt-dlp your own "
                      "browser login to reach content that needs you signed in -- used for "
                      "both the Workspace URL downloader and Live capture.")
            _cookie_options = ["-- none --"] + video_download.COOKIE_BROWSERS
            _saved_browser = st.session_state.get("settings_cookies_browser") or "-- none --"
            _picked_browser = st.selectbox(
                "Pull cookies from this browser", _cookie_options,
                index=_cookie_options.index(_saved_browser) if _saved_browser in _cookie_options else 0)
            st.session_state["settings_cookies_browser"] = (
                None if _picked_browser == "-- none --" else _picked_browser)
            st.session_state["settings_cookies_file"] = st.text_input(
                "...or a cookies.txt file path (takes priority over the browser above)",
                value=st.session_state.get("settings_cookies_file", ""),
                help="Export one with a browser extension (e.g. \"Get cookies.txt\") if the "
                     "browser option above can't read your profile directly.")

        _render_browser_extension_settings()


def _render_browser_extension_settings():
    """Step 33's expander: the local endpoint the browser extension talks
    to, plus the token to paste into it.

    This is also the bridge that gets translation settings to the
    endpoint's background thread, which has no `st.session_state` of its
    own -- the same shape as `background_jobs.set_gpu_limit_enabled`
    above. API keys stay in session state and are handed over in memory;
    nothing here writes a key to disk.
    """
    import page_server
    from sources import store as src_store

    with st.expander("Browser extension (translate the page you're on)", expanded=False):
        st.caption(
            "Lets a browser extension send the comic page you're reading straight into "
            "Baihe -- useful for a site with no adapter, and for pages an adapter can't "
            "reach because only your own browser can unscramble or decrypt them. Opens a "
            "small HTTP endpoint on this computer only (127.0.0.1); nothing on your "
            "network can reach it, and every request needs the token below.")
        stored = bool(src_store.get_setting("page_server_enabled"))
        enabled = st.checkbox(
            "Run the local endpoint", value=stored, key="settings_page_server_enabled",
            help="Off by default, because it opens a port. Turn it on only while you want "
                 "to use the extension.")
        if enabled != stored:
            src_store.set_setting("page_server_enabled", enabled)

        if not enabled:
            return

        page_server.ensure_server_started()
        if page_server.server_running():
            st.success(f"Listening on http://127.0.0.1:{page_server.server_port() or page_server.DEFAULT_PORT}")
        else:
            st.error(
                f"Couldn't start on port {page_server.DEFAULT_PORT} -- most likely something "
                "else is already using it. Close that program and reload this page.")

        # The engine is chosen here rather than reusing whatever a tab
        # last used, because the endpoint runs without a tab open.
        engine_names = list(translate_engines.ENGINES.keys())
        engine_choice = st.selectbox(
            "Translate extension pages with", engine_names,
            format_func=lambda e: f"{e} — {translate_engines.engine_picker_label(e, st.session_state.get('gemini_free_tier', False))}",
            key="settings_page_server_engine",
            help="Uses the matching API key from 'API keys & endpoints' above. With no key "
                 "set, pages still come back with their original text read by OCR -- clearly "
                 "marked as untranslated rather than passed off as a translation.")
        page_server.set_translation_config(
            engine=engine_choice,
            api_key=st.session_state.get(f"settings_{engine_choice}", "") or "",
            free_tier=bool(st.session_state.get("gemini_free_tier", False)),
            base_url=st.session_state.get("settings_ollama_url") or None,
            hf_token=st.session_state.get("settings_hf_token", "") or None,
            tesseract_cmd=st.session_state.get("settings_tesseract_cmd") or None)

        st.text_input(
            "Token for the extension", value=page_server.load_or_create_token(),
            key="settings_page_server_token", disabled=True,
            help="Paste this into the extension's own settings. It's what stops any other "
                 "page in your browser from quietly sending things to this app. Treat it "
                 "like a password.")
        st.caption(
            f"Load the extension from the `extension/` folder in this project "
            f"(chrome://extensions → Developer mode → Load unpacked), then paste the token "
            f"above into it. See `docs/browser-extension.md`.")

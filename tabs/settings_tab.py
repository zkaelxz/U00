"""
tabs/settings.py -- a sidebar panel where API keys are entered once per
session and reused as defaults across every tab, instead of retyping
them in Workspace, Reader, Scanlate, Navigator, and Discover separately.

Session-only: nothing here is written to disk. Each tab's own key
field still works independently if you want to override it there.
"""
import os
from common import st, synced_api_key_input


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

    for settings_key, env_names in {
        "claude": ("BAIHE_CLAUDE_KEY", "ANTHROPIC_API_KEY"),
        "deepseek": ("BAIHE_DEEPSEEK_KEY", "DEEPSEEK_API_KEY"),
        # Deliberately NOT falling back to GOOGLE_API_KEY here -- that name
        # is already claimed by the separate Google Translate engine above,
        # and a Cloud Translation key isn't guaranteed to also work as a
        # Gemini API key (different products, often different projects).
        "gemini": ("BAIHE_GEMINI_KEY", "GEMINI_API_KEY"),
        "deepl": ("BAIHE_DEEPL_KEY", "DEEPL_API_KEY"),
        "google": ("BAIHE_GOOGLE_KEY", "GOOGLE_API_KEY"),
        "elevenlabs": ("BAIHE_ELEVENLABS_KEY", "ELEVENLABS_API_KEY"),
        "hf_token": ("BAIHE_HF_TOKEN", "HF_TOKEN", "HUGGINGFACE_TOKEN"),
        "ollama_url": ("BAIHE_OLLAMA_URL",),
        "libretranslate_url": ("BAIHE_LIBRETRANSLATE_URL",),
    }.items():
        if st.session_state.get(f"settings_{settings_key}"):
            continue
        for name in env_names:
            val = env.get(name) or os.environ.get(name)
            if val:
                st.session_state[f"settings_{settings_key}"] = val
                break

SETTINGS_KEYS = {
    "claude": "Claude / Anthropic API key",
    "deepseek": "DeepSeek API key",
    "gemini": "Gemini API key",
    "deepl": "DeepL API key",
    "google": "Google Translate API key",
    "ollama_url": "Ollama base URL",
    "libretranslate_url": "LibreTranslate/LTEngine base URL",
    "elevenlabs": "ElevenLabs API key",
    "hf_token": "Hugging Face token (diarization)",
}


def render_settings_sidebar():
    _load_env_defaults()
    with st.sidebar:
        st.header("⚙️ Settings")
        st.session_state["app_dark_mode"] = st.toggle(
            "🌙 Dark mode", value=st.session_state.get("app_dark_mode", False),
            help="Applies to the whole app. The Reader has its own separate theme "
                 "(light/sepia/dark) under Reading experience.")
        st.caption("Entered once, reused as defaults everywhere in this session. "
                  "Never written to the database. To avoid retyping them after a "
                  "restart, put them in a `.env` file in the project folder -- it's "
                  "gitignored. See .env.example.")
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

        with st.expander("Offline / restricted networks", expanded=False):
            st.session_state["settings_whisper_model_path"] = st.text_input(
                "Local Whisper model folder (optional)",
                value=st.session_state.get("settings_whisper_model_path", ""),
                help="If this machine can't reach Hugging Face, download a faster-whisper "
                     "model elsewhere and point at the folder here.")
            st.caption("Leave blank to download automatically on first use. Models are cached "
                      "after the first download, so this only matters once per model size.")

        with st.expander("OCR", expanded=False):
            st.session_state["settings_tesseract_cmd"] = st.text_input(
                "Tesseract binary path (optional)",
                value=st.session_state.get("settings_tesseract_cmd", ""),
                placeholder=r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                help="Only needed if OCR fails with \"tesseract is not installed or it's not "
                     "in your PATH\" even after installing it -- the Windows installer doesn't "
                     "always add itself to PATH. Point this at tesseract.exe directly instead "
                     "of editing a system PATH variable by hand. Leave blank if OCR already "
                     "works.")

        with st.expander("Performance", expanded=False):
            st.session_state["use_gpu"] = st.checkbox(
                "Use GPU where available", value=st.session_state.get("use_gpu", False),
                help="Speeds up Whisper transcription, diarization, and local voice cloning "
                     "if you have a CUDA GPU. Falls back to CPU automatically if unavailable.")
            st.caption("Requires a CUDA-capable GPU and the GPU build of PyTorch. "
                      "If transcription errors after enabling this, turn it back off.")

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
            for key, label in SETTINGS_KEYS.items():
                is_url = key.endswith("_url")
                synced_api_key_input(label, key, f"settings_input_{key}",
                                      type="default" if is_url else "password")
            st.session_state["settings_ollama_num_ctx_override"] = st.number_input(
                "Ollama context window override (num_ctx, optional)",
                min_value=0, step=1024,
                value=st.session_state.get("settings_ollama_num_ctx_override", 0) or 0,
                help="Leave at 0 to size this automatically from the actual prompt each "
                     "time (recommended). Ollama's own default context window can be as "
                     "small as 2-4k tokens and silently truncates a longer prompt with no "
                     "error -- a value set here can only raise the window above the "
                     "automatic estimate, never below it, so it can't reintroduce that bug.")


def get_default(key: str, fallback: str = "") -> str:
    """Reads a setting saved via the sidebar panel, e.g. get_default('claude')."""
    import streamlit as st_module
    return st_module.session_state.get(f"settings_{key}", fallback)

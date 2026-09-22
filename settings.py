"""
tabs/settings.py -- a sidebar panel where API keys are entered once per
session and reused as defaults across every tab, instead of retyping
them in Workspace, Reader, Scanlate, Navigator, and Discover separately.

Session-only: nothing here is written to disk. Each tab's own key
field still works independently if you want to override it there.
"""
from common import st

SETTINGS_KEYS = {
    "claude": "Claude / Anthropic API key",
    "deepseek": "DeepSeek API key",
    "deepl": "DeepL API key",
    "google": "Google Translate API key",
    "ollama_url": "Ollama base URL",
    "libretranslate_url": "LibreTranslate/LTEngine base URL",
    "elevenlabs": "ElevenLabs API key",
    "hf_token": "Hugging Face token (diarization)",
}


def render_settings_sidebar():
    with st.sidebar:
        st.header("⚙️ Settings")
        st.caption("Entered once, reused as defaults everywhere in this session. "
                  "Never written to disk. Each field elsewhere can still be "
                  "overridden individually if you want a different key there.")
        with st.expander("API keys & endpoints", expanded=False):
            for key, label in SETTINGS_KEYS.items():
                session_key = f"settings_{key}"
                is_url = key.endswith("_url")
                st.session_state[session_key] = st.text_input(
                    label, value=st.session_state.get(session_key, ""),
                    type="default" if is_url else "password", key=f"settings_input_{key}")


def get_default(key: str, fallback: str = "") -> str:
    """Reads a setting saved via the sidebar panel, e.g. get_default('claude')."""
    import streamlit as st_module
    return st_module.session_state.get(f"settings_{key}", fallback)

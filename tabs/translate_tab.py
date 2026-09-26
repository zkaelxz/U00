"""
tabs/translate_tab.py -- Step 26b: a standalone translate tool. Paste or
upload source text and get a translation without creating a drama or
project, in either direction (zh/ja/ko -> English, or English -> zh/ja/ko).

This is a thin UI over translate_engines.standalone_translate -- engine
picker, API keys, and the id-keyed chunking/retry machinery are all
reused from there, not reimplemented here. No series glossary/character
context in this first version (per the roadmap's own decision) -- just
plain text in, plain text out, with a saved history.
"""
from common import *

_LANGUAGE_LABELS = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean", "en": "English"}


def render_translate_tab():
    st.subheader("🌐 Standalone translate")
    st.caption(
        "Paste or upload text and translate it directly -- no drama or project needed. "
        "Every translation is saved to the history below (viewable and clearable), but "
        "nothing here creates a drama or project.")

    direction = st.radio(
        "Direction", ["to_english", "from_english"],
        format_func=lambda d: "zh / ja / ko → English" if d == "to_english" else "English → zh / ja / ko",
        horizontal=True, key="translate_tab_direction")

    if direction == "to_english":
        source_language = st.selectbox(
            "Source language", ["zh", "ja", "ko"],
            format_func=lambda l: _LANGUAGE_LABELS[l], key="translate_tab_source_lang")
        target_language = "en"
    else:
        source_language = "en"
        target_language = st.selectbox(
            "Target language", ["zh", "ja", "ko"],
            format_func=lambda l: _LANGUAGE_LABELS[l], key="translate_tab_target_lang")

    _gemini_free_tier = st.session_state.get("gemini_free_tier", False)
    _ollama_base_url = st.session_state.get("settings_ollama_url") or None

    engine_list = list(translate_engines.ENGINES.keys())
    _saved_engine = st.session_state.get("settings_default_engine", "claude")
    engine_choice = st.selectbox(
        "Translation engine", engine_list,
        index=engine_list.index(_saved_engine) if _saved_engine in engine_list else 0,
        format_func=lambda e: f"{e} — {translate_engines.engine_picker_label(e, _gemini_free_tier)}",
        key="translate_tab_engine")

    engine_model = None
    if engine_choice == "claude":
        _model_keys = list(translate_engines.CLAUDE_MODELS.keys())
        _saved_model = st.session_state.get("settings_claude_model", _model_keys[0])
        engine_model = st.selectbox(
            "Claude model", _model_keys,
            index=_model_keys.index(_saved_model) if _saved_model in _model_keys else 0,
            format_func=lambda m: translate_engines.CLAUDE_MODELS[m], key="translate_tab_claude_model")
    elif engine_choice == "gemini":
        _model_keys = list(translate_engines.GEMINI_MODELS.keys())
        _saved_model = st.session_state.get("settings_gemini_model", _model_keys[0])
        engine_model = st.selectbox(
            "Gemini model", _model_keys,
            index=_model_keys.index(_saved_model) if _saved_model in _model_keys else 0,
            format_func=lambda m: translate_engines.GEMINI_MODELS[m], key="translate_tab_gemini_model")
    elif engine_choice == "ollama":
        _model_keys = list(translate_engines.OLLAMA_MODELS.keys())
        _saved_model = st.session_state.get("settings_ollama_model", _model_keys[0])
        engine_model = st.selectbox(
            "Ollama model", _model_keys,
            index=_model_keys.index(_saved_model) if _saved_model in _model_keys else 0,
            format_func=lambda m: translate_engines.OLLAMA_MODELS[m], key="translate_tab_ollama_model")
    elif engine_choice == "nllb":
        _model_keys = list(translate_engines.NLLB_MODELS.keys())
        engine_model = st.selectbox(
            "NLLB model size", _model_keys,
            format_func=lambda m: translate_engines.NLLB_MODELS[m], key="translate_tab_nllb_model")

    if engine_choice == "test_offline":
        api_key = "offline"
        st.success("Dry-run mode: no API key, no network, no cost. Produces obvious [TEST] "
                  "placeholder text so you can confirm this tool works before spending anything.")
    elif engine_choice in ("ollama", "libretranslate"):
        api_key = synced_api_key_input(
            f"{engine_choice} API key (optional)", engine_choice,
            f"translate_tab_api_key_{engine_choice}") or "local"
    else:
        api_key = synced_api_key_input(
            f"{engine_choice} API key", engine_choice, f"translate_tab_api_key_{engine_choice}")
        if not api_key:
            st.caption("⚠️ Required — set it here or in the ⚙️ Settings sidebar. "
                      "To try this tool for free first, choose `test_offline` above.")

    support_ok, support_message = translate_engines.standalone_direction_support(
        engine_choice, source_language, target_language)
    if not support_ok:
        st.error(f"🚫 {support_message}")
    elif support_message:
        st.warning(f"⚠️ {support_message}")

    st.divider()

    uploaded = st.file_uploader(
        "Upload a file to translate instead of pasting (optional)",
        type=["txt", "md", "epub"], key="translate_tab_upload")
    if uploaded is not None and st.session_state.get("translate_tab_last_upload") != uploaded.name:
        try:
            st.session_state["translate_tab_text"] = core_module.load_novel_text_for_context(
                uploaded.getvalue(), uploaded.name)
            st.session_state["translate_tab_last_upload"] = uploaded.name
        except ImportError as exc:
            st.error(str(exc))

    source_text = st.text_area(
        "Text to translate", value=st.session_state.get("translate_tab_text", ""),
        height=250, key="translate_tab_text")

    col_go, col_clear = st.columns([1, 1])
    can_translate = bool(support_ok and source_text.strip() and api_key)
    translate_clicked = col_go.button("🌐 Translate", type="primary", disabled=not can_translate)
    if col_clear.button("Clear"):
        st.session_state["translate_tab_text"] = ""
        st.session_state.pop("translate_tab_output", None)
        st.rerun()

    if translate_clicked:
        try:
            engine = translate_engines.get_engine(
                engine_choice, api_key, engine_model,
                free_tier=engine_choice == "gemini" and _gemini_free_tier,
                base_url=_ollama_base_url if engine_choice == "ollama" else None)
        except Exception as exc:
            st.error(f"Couldn't set up the {engine_choice} engine: "
                    f"{translate_engines.redact_secrets(str(exc))}")
        else:
            with st.spinner("Translating..."):
                try:
                    output_text = translate_engines.standalone_translate(
                        source_text, engine, source_language, target_language)
                except translate_engines.UnsupportedDirectionError as exc:
                    st.error(f"🚫 {exc}")
                except Exception as exc:
                    st.error(f"Translation failed: "
                            f"{translate_engines.redact_secrets(str(exc))}")
                else:
                    st.session_state["translate_tab_output"] = output_text
                    st.session_state["translate_tab_output_source"] = source_text
                    db.save_translate_history(
                        source_language, target_language, engine_choice, source_text, output_text)
                    st.success("Done.")

    if st.session_state.get("translate_tab_output"):
        st.divider()
        st.subheader("Result")
        out_col1, out_col2 = st.columns(2)
        with out_col1:
            st.caption("Source")
            st.text_area(
                "Source text (read-only)",
                value=st.session_state.get("translate_tab_output_source", ""),
                height=300, key="translate_tab_source_display", disabled=True)
        with out_col2:
            st.caption("Translation (click the icon in the corner to copy)")
            st.code(st.session_state["translate_tab_output"], language=None, wrap_lines=True)
        st.download_button(
            "⬇️ Download translation (.txt)",
            st.session_state["translate_tab_output"].encode("utf-8"),
            file_name="translation.txt")

    st.divider()
    st.subheader("History")
    history = db.list_translate_history(limit=50)
    if not history:
        st.caption("No translations yet.")
    else:
        hc1, hc2 = st.columns([4, 1])
        hc1.caption(f"{len(history)} saved translation(s), most recent first.")
        if hc2.button("🗑️ Clear history"):
            db.clear_translate_history()
            st.rerun()
        for h in history:
            src_label = _LANGUAGE_LABELS.get(h["source_language"], h["source_language"])
            tgt_label = _LANGUAGE_LABELS.get(h["target_language"], h["target_language"])
            snippet = h["source_text"][:80] + ("…" if len(h["source_text"]) > 80 else "")
            with st.expander(
                    f"{(h['created_at'] or '')[:19]} — {src_label} → {tgt_label} — "
                    f"{h['engine']} — {snippet}"):
                hcol1, hcol2 = st.columns(2)
                hcol1.caption("Source")
                hcol1.text(h["source_text"])
                hcol2.caption("Translation")
                hcol2.text(h["translated_text"])

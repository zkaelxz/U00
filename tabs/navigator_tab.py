"""
tabs/navigator.py -- Navigator tab UI, extracted from the former monolithic app.py.
"""
from common import *


def render_navigator_tab():
    st.subheader("Site navigation helper")
    st.caption(
        "For sites in a language you don't read (JJWXC, Fanjiao, etc.): paste a public page's "
        "URL, translate its visible menu/labels, and get step-by-step navigation guidance in "
        "your language. This only describes how to use the site's own public interface -- it "
        "doesn't log in, purchase, or fetch anything for you."
    )
    import known_sites
    with st.expander("📋 Known official platforms (baihe / Korean GL / Japanese yuri)"):
        kc1, kc2 = st.columns(2)
        filter_lang = kc1.selectbox("Language", ["", "zh", "ko", "ja"],
                                     format_func=lambda l: {"": "All", "zh": "Chinese", "ko": "Korean", "ja": "Japanese"}[l],
                                     key="known_site_lang")
        filter_type = kc2.selectbox("Content type", ["", "novel", "manhwa", "manga", "manhua", "audio_drama"],
                                     format_func=lambda t: "All" if t == "" else t.replace("_", " ").title(),
                                     key="known_site_type")
        sites = known_sites.list_sites(content_type=filter_type or None, language=filter_lang or None)
        for s in sites:
            st.markdown(f"**[{s['name']}]({s['url']})** — {s['region']} · {', '.join(s['content_types'])}  \n{s['notes']}")

    nav_known_site = st.selectbox(
        "Start from a known site (optional)",
        ["-- paste a URL manually below --"] + [s["name"] for s in known_sites.KNOWN_SITES],
        key="nav_known_site")
    default_nav_url = ""
    if nav_known_site != "-- paste a URL manually below --":
        default_nav_url = next(s["url"] for s in known_sites.KNOWN_SITES if s["name"] == nav_known_site)

    nav_url = st.text_input("Page URL *(required)*", value=default_nav_url, key="nav_url")
    nav_goal = st.text_area("What are you trying to do? *(required)*",
                             placeholder="e.g. Find the audio drama section for a specific title, "
                                         "or find the episode list for a show I already found",
                             key="nav_goal")
    nav_target_lang = st.text_input("Your language", value="English", key="nav_target_lang")
    _nav_gemini_free_tier = st.session_state.get("gemini_free_tier", False)
    nav_engine_choice = st.selectbox(
        "Engine", [e for e in translate_engines.ENGINES if translate_engines.ENGINES[e].supports_reference],
        format_func=lambda e: f"{e} — {translate_engines.engine_picker_label(e, _nav_gemini_free_tier)}",
        key="nav_engine")
    _nav_needs_key = nav_engine_choice != "ollama"
    nav_api_key = synced_api_key_input(
        "API key" + (" *(required)*" if _nav_needs_key else " (optional)"),
        nav_engine_choice, "nav_api_key")

    _nav_missing = []
    if not nav_url:
        _nav_missing.append("a page URL")
    if not nav_goal.strip():
        _nav_missing.append("what you're trying to do (e.g. \"find this title's audio drama page\")")
    if _nav_needs_key and not nav_api_key:
        _nav_missing.append("an API key (set one in the ⚙️ Settings sidebar)")
    if _nav_missing:
        st.info("Still needed: " + "; ".join(_nav_missing) + ".")

    if st.button("🧭 Translate page + get navigation steps", disabled=bool(_nav_missing)):
        import navigator
        engine = translate_engines.get_engine(
            nav_engine_choice, nav_api_key,
            free_tier=nav_engine_choice == "gemini" and _nav_gemini_free_tier,
            base_url=(st.session_state.get("settings_ollama_url") or None)
            if nav_engine_choice == "ollama" else None)
        try:
            with st.spinner("Fetching page labels..."):
                labels = navigator.fetch_visible_labels(nav_url)
            with st.spinner("Translating labels..."):
                translated = navigator.translate_labels(labels, nav_target_lang, engine)
            with st.spinner("Generating navigation steps..."):
                steps = navigator.generate_navigation_steps(
                    nav_url, nav_goal, nav_target_lang, engine, translated_labels=translated)
            st.subheader("Steps")
            st.markdown(steps)
            with st.expander("Translated page labels"):
                for orig, trans in translated.items():
                    st.caption(f"{orig} → {trans}")
        except Exception as e:
            st.error(f"Couldn't fetch or process that page: {e}")


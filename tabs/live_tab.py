"""
tabs/live_tab.py -- near-live translation of an ongoing stream.

See live_translate.py for the actual capture/transcribe/translate
pipeline; this just wires it to a Streamlit UI, following the same
background-job + manual-refresh pattern already used for Transcribe/
Translate/Emotion elsewhere (Workspace) -- a job that runs indefinitely
(until you stop it or the stream ends) can't drive its own polling any
more than those can, since Streamlit doesn't poll on its own.

Deliberately standalone rather than tied to a Library drama for this
first version: it doesn't save its cues anywhere persistent yet, so
copy anything worth keeping out of the feed below before stopping.
"""
import os
import tempfile

from common import *
import live_translate

_JOB_ID = "live_capture"


def render_live_tab():
    st.subheader("🔴 Live (experimental)")
    st.caption(
        "Near-live translation of an ongoing stream, for watching along in "
        "roughly real time rather than producing a polished subtitle file. "
        "There's an unavoidable delay of at least one chunk's length, and "
        "accuracy is lower than the normal align/translate pipeline -- see "
        "the README for the full list of tradeoffs. Doesn't save into your "
        "Library yet; copy anything worth keeping from the feed below."
    )

    job = background_jobs.get_status(_JOB_ID)
    is_running = bool(job and job["status"] == "running")

    url = st.text_input("Live stream URL", key="live_url", disabled=is_running,
                         placeholder="https://www.youtube.com/watch?v=...")

    c1, c2, c3 = st.columns(3)
    source_language = c1.selectbox(
        "Source language", ["zh", "ja", "ko"], key="live_source_language",
        disabled=is_running)
    whisper_size = c2.selectbox(
        "Whisper model", ["tiny", "base", "small", "medium"], index=2,
        key="live_whisper_size", disabled=is_running,
        help="Smaller = faster per chunk, closer to real time. 'medium' is "
             "this app's usual default elsewhere but is slower than a short "
             "chunk really has time for here.")
    segment_seconds = c3.slider(
        "Chunk length (seconds)", 10, 60, 20, step=5, key="live_segment_seconds",
        disabled=is_running,
        help="Shorter = lower latency, but Whisper loses cross-sentence "
             "context at each cut. Longer = better transcription per chunk, "
             "more delay before it appears.")

    engine_list = list(translate_engines.ENGINES.keys())
    saved_engine = st.session_state.get("settings_default_engine", "claude")
    _live_gemini_free_tier = st.session_state.get("gemini_free_tier", False)
    engine_choice = st.selectbox(
        "Translation engine", engine_list,
        index=engine_list.index(saved_engine) if saved_engine in engine_list else 0,
        format_func=lambda e: f"{e} — {translate_engines.engine_picker_label(e, _live_gemini_free_tier)}",
        key="live_engine_choice", disabled=is_running)

    needs_key = engine_choice not in ("test_offline", "ollama", "libretranslate")
    if engine_choice == "test_offline":
        api_key = "offline"
        st.success("Dry-run mode: no API key, no network, no cost.")
    else:
        api_key = synced_api_key_input(
            f"{engine_choice} API key" + (" *(required)*" if needs_key else " (optional)"),
            engine_choice, "live_api_key_input", disabled=is_running)
        api_key = api_key or ("local" if not needs_key else "")

    if not is_running:
        if st.button("▶️ Start", type="primary",
                      disabled=not url.strip() or (needs_key and not api_key)):
            engine = translate_engines.get_engine(
                engine_choice, api_key,
                free_tier=engine_choice == "gemini" and _live_gemini_free_tier)
            out_dir = os.path.join(tempfile.gettempdir(), "baihe_live_capture")
            started = background_jobs.start_job(
                _JOB_ID, live_translate.run_live_job,
                _JOB_ID, url.strip(), out_dir, segment_seconds, source_language,
                whisper_size, engine)
            if started:
                st.rerun()
            else:
                st.warning("A live session is already running.")
    else:
        st.info(job.get("message") or "Running...")
        if st.button("⏹️ Stop"):
            background_jobs.request_cancel(_JOB_ID)
            st.rerun()

    if not job:
        return

    cues = job.get("result") or []
    if cues:
        st.caption(f"{len(cues)} line(s) captured so far.")
        for cue in reversed(cues[-50:]):
            st.markdown(f"**[{fmt_ts(cue['start'])}]** {cue['text']}  \n"
                        f"→ {cue['translated']}")
    elif is_running:
        st.caption("Waiting for the first chunk to finish processing...")

    if is_running:
        if st.button("🔄 Refresh", key="refresh_live"):
            st.rerun()
    elif job["status"] == "error":
        st.error(f"Live capture stopped with an error: {job['error']}")
        with st.expander("Details"):
            st.code(job.get("traceback", ""), language="text")
    elif job["status"] == "done" and cues:
        st.success("Stopped. Start a new session above, or copy lines from the feed first.")

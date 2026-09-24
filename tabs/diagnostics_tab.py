"""
tabs/diagnostics.py -- "Check my setup" page. One glance instead of an
iterative back-and-forth when something's missing or misconfigured.
"""
from common import *
import diagnostics
import time

# job_id prefixes this app actually uses (see background_jobs.start_job
# call sites) -- everything before the last "_<drama_id>" segment.
_JOB_LABELS = {
    "translate": "Translating",
    "transcribe": "Transcribing / reading captions",
    "flag": "Review queue (flagging lines)",
    "emotion": "Detecting emotional register",
    "consistency": "Checking translation consistency",
    "notes": "Generating translation notes",
}


def _describe_job(job_id: str) -> str:
    """Turns a raw job_id like 'emotion_42' into 'Detecting emotional
    register -- Some Drama Title', so the jobs list means something at a
    glance instead of showing internal id strings."""
    if job_id == "live_capture":
        return "🔴 Live capture"
    prefix, _, suffix = job_id.rpartition("_")
    if prefix in _JOB_LABELS and suffix.isdigit():
        drama = db.get_drama(int(suffix))
        title = (drama.get("title_en") or drama.get("title_zh") or f"drama #{suffix}") if drama else f"drama #{suffix} (deleted)"
        return f"{_JOB_LABELS[prefix]} -- {title}"
    return job_id


def render_diagnostics_tab():
    st.subheader("🩺 Check my setup")
    st.caption("Run this any time something isn't working -- shows what's installed, what's "
              "missing, and what's configured, in one place.")

    st.divider()
    st.subheader("🏃 Running jobs")
    st.caption("Everything currently running in the background across every drama -- "
              "translation, transcription, review checks, and Live capture all show up here "
              "the moment they start, not just in the tab that started them.")
    running = background_jobs.list_running_jobs()
    if not running:
        st.caption("Nothing running right now.")
    else:
        for job_id, job in running.items():
            jc1, jc2 = st.columns([5, 1])
            jc1.progress(job.get("progress", 0.0) or 0.0,
                         text=f"{_describe_job(job_id)} -- {job.get('message') or 'Running...'}")
            if jc2.button("⏹️ Cancel", key=f"jobs_panel_cancel_{job_id}"):
                background_jobs.request_cancel(job_id)
                st.rerun()
        if st.button("🔄 Refresh", key="jobs_panel_refresh"):
            st.rerun()

    st.divider()
    st.subheader("☠️ Danger zone")
    with st.expander("Reset the entire library"):
        st.error(
            "**Deletes everything**: every drama, translation, glossary, series, progress, "
            "vocab, translation notes, and version history. Also deletes every drama's files "
            "(audio, dub tracks, page images, exports) from disk. This cannot be undone."
        )
        st.caption("Meant for wiping test data during setup, not for everyday cleanup -- "
                  "for removing a single drama, use the delete button in the Library or "
                  "Workspace tab instead.")

        stats = db.get_library_stats()
        if stats["total_dramas"] == 0:
            st.caption("Library is already empty.")
        else:
            st.caption(f"Currently: {stats['total_dramas']} drama(s), "
                      f"{stats['total_lines']:,} line(s) across all of them.")

            confirm_reset = st.checkbox(
                "I understand this permanently deletes all library data",
                key="confirm_full_reset")
            typed = st.text_input(
                'Type RESET to confirm', key="reset_typed",
                disabled=not confirm_reset,
                help="A second, deliberate step -- this is the one destructive action in "
                     "the app with no way back.")

            if st.button("🗑️ Reset everything", type="secondary",
                          disabled=not (confirm_reset and typed.strip() == "RESET")):
                # A background job (translation, dubbing) still writing to
                # the database when the reset happens is a real hazard: the
                # reset can't preserve ID monotonicity across a full file
                # wipe, so a job's stale writes could land on a brand-new
                # drama that happens to reuse the deleted one's id, silently
                # corrupting it. So: ask every running job to stop, and
                # actually wait for them to, before doing anything destructive.
                running = background_jobs.list_running_jobs()
                if running:
                    with st.spinner(f"Stopping {len(running)} running job(s) before reset..."):
                        for jid in running:
                            background_jobs.request_cancel(jid)
                        deadline = time.time() + 10
                        while time.time() < deadline and any(
                                background_jobs.is_running(j) for j in running):
                            time.sleep(0.1)
                    still_running = [j for j in running if background_jobs.is_running(j)]
                    if still_running:
                        st.error(
                            f"{len(still_running)} job(s) didn't stop within 10 seconds "
                            f"(likely mid-way through a single slow API call, which can't be "
                            f"interrupted between batches). Reset was NOT performed -- wait for "
                            f"it to finish or fail on its own, then try again.")
                        st.stop()

                db.reset_library()
                st.session_state.active_drama_id = None
                st.session_state.lines = None
                background_jobs.clear_all_jobs()
                st.success("Library reset. Starting fresh.")
                st.rerun()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    api_keys_set = {
        key: bool(st.session_state.get(f"settings_{key}"))
        for key in ["claude", "deepseek", "gemini", "deepl", "google", "elevenlabs", "hf_token"]
    }

    if st.button("🔍 Run diagnostics", type="primary"):
        with st.spinner("Checking..."):
            results = diagnostics.run_full_diagnostics(project_root, db.LIBRARY_DIR, api_keys_set)
        st.session_state["diagnostics_results"] = results

    results = st.session_state.get("diagnostics_results")
    if not results:
        st.info("Click the button above to run a check.")
        return

    # ---- Python & ffmpeg ----
    st.subheader("Core requirements")
    c1, c2, c3 = st.columns(3)
    py = results["python"]
    c1.metric("Python", py["version"], "OK" if py["ok"] else "Needs 3.9+")
    ff = results["ffmpeg"]
    c2.metric("ffmpeg", "Found" if ff["found"] else "Missing")
    c3.metric("Library writable", "Yes" if results["library_writable"] else "No")
    if not ff["found"]:
        st.error("ffmpeg not found on PATH -- most features (audio, video, dubbing) won't work "
                 "until it's installed. See README for install steps.")
    elif ff.get("version"):
        st.caption(ff["version"])

    # ---- File completeness ----
    st.subheader("Project files")
    files = results["files"]
    if files["all_present"]:
        st.success("All expected files present.")
    else:
        st.error("Missing files detected -- this is almost certainly the cause of any "
                 "`ModuleNotFoundError` you're seeing.")
        if files["missing_top_level"]:
            st.write("**Missing from the main folder:**")
            for f in files["missing_top_level"]:
                st.write(f"- `{f}`")
        if files["missing_tabs"]:
            st.write("**Missing from `tabs/`:**")
            for f in files["missing_tabs"]:
                st.write(f"- `{f}`")

    # ---- API keys ----
    st.subheader("API keys (from Settings)")
    kc = st.columns(len(api_keys_set))
    for i, (key, is_set) in enumerate(api_keys_set.items()):
        kc[i].metric(key, "✅ set" if is_set else "— not set")
    if not any(api_keys_set.values()):
        st.warning("No API keys configured yet -- add them in the ⚙️ Settings sidebar panel.")

    # ---- Dependencies ----
    st.subheader("Dependencies")
    deps = results["dependencies"]
    tier_order = ["required", "engine", "feature", "dev"]
    tier_labels = {"required": "Core (needed for the app to run at all)",
                   "engine": "Translation engines",
                   "feature": "Optional features",
                   "dev": "Development"}
    for tier in tier_order:
        tier_deps = {k: v for k, v in deps.items() if v["tier"] == tier}
        if not tier_deps:
            continue
        with st.expander(f"{tier_labels[tier]} ({sum(1 for v in tier_deps.values() if v['installed'])}/{len(tier_deps)} installed)",
                          expanded=(tier == "required")):
            for name, info in sorted(tier_deps.items()):
                icon = "✅" if info["installed"] else "❌"
                st.caption(f"{icon} **{name}** -- {info['powers']}")

    missing_required = [k for k, v in deps.items() if v["tier"] == "required" and not v["installed"]]
    if missing_required:
        st.error(f"Missing required dependencies: {', '.join(missing_required)}. "
                 f"Run `pip install -r requirements.txt` again.")


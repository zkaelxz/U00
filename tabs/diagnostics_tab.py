"""
tabs/diagnostics.py -- "Check my setup" page. One glance instead of an
iterative back-and-forth when something's missing or misconfigured.
"""
from common import *
import diagnostics
import storage
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
    "diarize": "Detecting speakers",
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


def _run_pip_stream(running_label: str, done_label: str, failed_label: str, stream_gen) -> dict:
    """Runs a diagnostics.stream_* generator inside an st.status box,
    writing each real output line as it arrives rather than just
    spinning, and returns {"ok": bool} once it's done. Shared by the
    generic per-package Install button and the GPU-PyTorch reinstall
    button below -- both need the exact same "show real progress, never
    swallow the real error" handling."""
    result = {"ok": False}
    with st.status(running_label, expanded=True) as box:
        for item in stream_gen:
            if item.get("done"):
                result["ok"] = item["ok"]
            else:
                box.write(item["line"])
        box.update(label=done_label if result["ok"] else failed_label,
                   state="complete" if result["ok"] else "error")
    return result


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
    st.subheader("💾 Downloaded model cache")
    st.caption("Whisper/pyannote/Qwen3-ASR/F5-TTS weights live in Hugging Face's own cache "
              "(usually `~/.cache/huggingface`), separate from this app's own `library/` "
              "folder above -- across several backends this can reach tens of GB.")
    hf_cache = diagnostics.scan_hf_cache()
    if not hf_cache:
        st.caption("Nothing cached yet, or `huggingface_hub` isn't installed.")
    else:
        st.caption(f"{len(hf_cache)} cached revision(s), "
                  f"{storage.format_bytes(sum(e['size_bytes'] for e in hf_cache))} total.")
        for entry in hf_cache:
            hc1, hc2 = st.columns([5, 1])
            hc1.caption(f"**{entry['repo_id']}** ({entry['repo_type']}) -- "
                       f"{storage.format_bytes(entry['size_bytes'])} -- `{entry['revision'][:12]}`")
            if hc2.button("🗑️ Delete", key=f"hf_cache_del_{entry['revision']}"):
                if diagnostics.delete_hf_cache_revision(entry["revision"]):
                    st.success(f"Deleted {entry['repo_id']}.")
                    st.rerun()
                else:
                    st.error("Delete failed -- see the log for details.")

    st.divider()
    st.subheader("🧩 Model & engine versions")
    st.caption("What's actually installed/configured locally for every AI model or engine "
              "this app wires into a feature. No network call -- this doesn't check whether "
              "something newer exists, only what's here right now.")
    model_versions = diagnostics.get_model_engine_versions(
        st.session_state.get("settings_ollama_model"))
    for m in model_versions:
        st.caption(f"**{m['name']}**: `{m['version']}` -- [{m['url']}]({m['url']})")

    if diagnostics.gpu_torch_mismatch():
        st.warning("A real NVIDIA GPU is on this machine, but the installed PyTorch build is "
                  "CPU-only -- every GPU-touching stage (diarization, vocal separation, "
                  "transcription, TTS) is running on CPU instead of your GPU.")
        if st.button("⚡ Install GPU PyTorch", key="install_gpu_torch_btn"):
            gpu_result = _run_pip_stream(
                "Reinstalling PyTorch with GPU/CUDA support...",
                "GPU PyTorch installed.", "GPU PyTorch reinstall failed -- see output above.",
                diagnostics.stream_gpu_torch_reinstall(
                    project_root=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            if gpu_result["ok"]:
                st.success("Done -- re-checking...")
                st.rerun()
            else:
                st.error("GPU PyTorch reinstall failed -- see the streamed output above for "
                         "the real pip error.")

    st.divider()
    st.subheader("🔒 pyannote gated model access")
    st.caption("`diarize.load_pipeline()` tries `speaker-diarization-community-1` first, falling "
              "back to `-3.1` only if that fails -- so a 403 naming `-3.1` specifically can mean "
              "BOTH models are gated on your Hugging Face account, not just one. This check "
              "reaches Hugging Face's API (the only check on this page that does), so it only "
              "runs when you click the button below, not automatically.")
    if st.button("🔍 Check pyannote access", key="check_pyannote_access"):
        with st.spinner("Checking..."):
            st.session_state["pyannote_access_results"] = diagnostics.check_pyannote_gated_access(
                st.session_state.get("settings_hf_token") or None)
    _pa_results = st.session_state.get("pyannote_access_results")
    if _pa_results is not None:
        if not _pa_results:
            st.caption("`huggingface_hub` isn't installed -- can't check.")
        for r in _pa_results:
            if r["accessible"]:
                st.caption(f"🟢 `{r['model']}`: accessible")
            else:
                st.error(f"🔴 `{r['model']}`: gated, terms not accepted (or another error) -- "
                        f"visit https://huggingface.co/{r['model']} to accept the terms. "
                        f"({r['error']})")

    st.divider()
    st.subheader("🤖 App Assistant")
    st.caption("Ask \"where is X\" or describe something that seems off -- grounded in this "
              "app's own current tab sections, regenerated fresh every time so it can't drift "
              "out of sync with the real UI the way a hand-written help doc would. Says so "
              "plainly, and offers a developer report, rather than guessing when it doesn't know.")
    help_key = "app_help_history"
    if help_key not in st.session_state:
        st.session_state[help_key] = []

    help_api_key = synced_api_key_input("API key", "claude", "app_help_key")
    for msg in st.session_state[help_key]:
        st.chat_message(msg["role"]).write(msg["content"])

    help_question = st.chat_input("Ask about the app...", key="app_help_input")
    if help_question and help_api_key:
        st.session_state[help_key].append({"role": "user", "content": help_question})
        st.chat_message("user").write(help_question)
        help_engine = translate_engines.get_engine("claude", help_api_key)
        with st.spinner("Thinking..."):
            help_answer = app_help.ask_about_app(
                help_question, help_engine, chat_history=st.session_state[help_key][:-1])
        st.session_state[help_key].append({"role": "assistant", "content": help_answer})
        st.chat_message("assistant").write(help_answer)
    elif help_question:
        st.warning("Enter an API key above first.")

    if st.session_state[help_key] and st.button(
            "📋 Copy a report for the developer", key="app_help_report_btn"):
        last_question = next((m["content"] for m in reversed(st.session_state[help_key])
                              if m["role"] == "user"), "")
        last_answer = next((m["content"] for m in reversed(st.session_state[help_key])
                            if m["role"] == "assistant"), "(no answer yet)")
        help_diag_results = st.session_state.get("diagnostics_results") or diagnostics.run_full_diagnostics(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), db.LIBRARY_DIR,
            {key: bool(st.session_state.get(f"settings_{key}"))
             for key in ["claude", "deepseek", "gemini", "deepl", "google", "hf_token"]})
        help_diag_text = diagnostics.format_diagnostics_report(help_diag_results, hf_cache, model_versions)
        st.session_state["app_help_report_text"] = diagnostics.redact_for_support(
            app_help.format_help_report(last_question, last_answer, help_diag_text))
    if st.session_state.get("app_help_report_text"):
        st.code(st.session_state["app_help_report_text"], language="text")

    st.divider()
    st.subheader("📋 Copy diagnostics for support")
    st.caption("A redacted summary of everything above -- API keys, local file paths, and your "
              "OS username are stripped, even though the panels above show the real values. "
              "Use the copy icon in the top-right of the box below.")
    if st.button("📋 Build copyable report", key="build_support_report"):
        _report_results = st.session_state.get("diagnostics_results") or diagnostics.run_full_diagnostics(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), db.LIBRARY_DIR,
            {key: bool(st.session_state.get(f"settings_{key}"))
             for key in ["claude", "deepseek", "gemini", "deepl", "google", "hf_token"]})
        st.session_state["support_report_text"] = diagnostics.redact_for_support(
            diagnostics.format_diagnostics_report(_report_results, hf_cache, model_versions))
    if st.session_state.get("support_report_text"):
        st.code(st.session_state["support_report_text"], language="text")

    st.divider()
    st.subheader("📜 Log")
    st.caption("The last lines from library/logs/app.log -- background job failures land "
              "here even after the on-screen message is gone. Use the copy icon in the "
              "top-right of the box below to copy it for a bug report.")
    import applog
    log_lines = applog.tail(50)
    if log_lines:
        st.code("\n".join(log_lines), language="text")
    else:
        st.caption("Nothing logged yet.")

    st.divider()
    st.subheader("🎯 Accuracy benchmark")
    st.caption(
        "Catches an 'improvement' that actually makes things worse. Register a real sample "
        "of your own content once per type you use -- a short audio clip, a novel excerpt, a "
        "manhua page -- optionally with the text you KNOW is correct, then re-run the same "
        "cases any time a pipeline setting changes and compare scores instead of just hoping "
        "it helped. A case with no reference text still gets timed and checked for errors, "
        "just without a quality score."
    )

    _bm_stage_labels = {
        "transcription": "🎙️ Speech recognition (audio drama / streamer VOD)",
        "translation": "🌐 Translation (novel excerpt or any text)",
        "ocr": "🔤 OCR (manhua/comic page or scanned novel page)",
    }
    _bm_content_types = {
        "transcription": ["audio_drama", "streamer_vod"],
        "translation": ["novel", "audio_drama", "streamer_vod", "manhua"],
        "ocr": ["manhua", "novel"],
    }

    with st.expander("➕ Register a new case"):
        bm_stage = st.selectbox("What does this test?", list(_bm_stage_labels.keys()),
                                 format_func=lambda s: _bm_stage_labels[s], key="bm_new_stage")
        bm_label = st.text_input("Label (so you can tell cases apart later)", key="bm_new_label")
        bm_content_type = st.selectbox("Content type", _bm_content_types[bm_stage],
                                        format_func=lambda m: m.replace("_", " ").title(),
                                        key="bm_new_content_type")
        bm_source_language = st.selectbox("Source language", ["zh", "ja", "ko"], key="bm_new_lang")

        bm_source_text, bm_input_file = None, None
        if bm_stage == "translation":
            bm_source_text = st.text_area("Source text to translate", key="bm_new_source_text")
            bm_reference_text = st.text_area("Known-correct translation (optional)",
                                              key="bm_new_ref_text")
        else:
            file_types = ["wav", "mp3", "m4a", "flac", "mp4"] if bm_stage == "transcription" else ["png", "jpg", "jpeg"]
            bm_input_file = st.file_uploader("Upload the sample file", type=file_types, key="bm_new_file")
            ref_label = "transcript" if bm_stage == "transcription" else "OCR text"
            bm_reference_text = st.text_area(f"Known-correct {ref_label} (optional)",
                                              key="bm_new_ref_text_file")

        if st.button("Add case", key="bm_add_case"):
            if bm_stage != "translation" and not bm_input_file:
                st.warning("Upload a file first.")
            elif bm_stage == "translation" and not (bm_source_text or "").strip():
                st.warning("Enter some source text first.")
            elif not bm_label.strip():
                st.warning("Give it a label.")
            else:
                bm_input_filename = None
                if bm_input_file:
                    os.makedirs(db.BENCHMARK_DIR, exist_ok=True)
                    ext = os.path.splitext(bm_input_file.name)[1]
                    bm_input_filename = f"case_{int(time.time())}{ext}"
                    with open(os.path.join(db.BENCHMARK_DIR, bm_input_filename), "wb") as f:
                        f.write(bm_input_file.getbuffer())
                db.create_benchmark_case(
                    bm_label.strip(), bm_stage, bm_content_type, source_language=bm_source_language,
                    input_filename=bm_input_filename, source_text=bm_source_text,
                    reference_text=(bm_reference_text or "").strip() or None)
                st.success(f"Added '{bm_label}'.")
                st.rerun()

    bm_cases = db.list_benchmark_cases()
    if not bm_cases:
        st.caption("No cases registered yet -- add one above to start tracking accuracy over time.")
    else:
        bm_run_label = st.text_input(
            "Label this run (optional -- e.g. \"after VAD threshold change\")", key="bm_run_label")
        if st.button("▶️ Run all cases through the current pipeline", type="primary"):
            import benchmark
            skipped_stages = []
            with st.spinner(f"Running {len(bm_cases)} case(s)..."):
                for stage in ("transcription", "translation", "ocr"):
                    stage_cases = [c for c in bm_cases if c["stage"] == stage]
                    if not stage_cases:
                        continue
                    prepared = []
                    for c in stage_cases:
                        case_dict = dict(c)
                        if c.get("input_filename"):
                            case_dict["input_path"] = os.path.join(db.BENCHMARK_DIR, c["input_filename"])
                        prepared.append(case_dict)

                    kwargs = {}
                    if stage == "translation":
                        engine_choice = st.session_state.get("settings_default_engine", "claude")
                        api_key = st.session_state.get(f"settings_{engine_choice}", "")
                        needs_key = engine_choice not in ("ollama", "test_offline", "libretranslate", "nllb")
                        if needs_key and not api_key:
                            skipped_stages.append(
                                f"translation (no API key set for '{engine_choice}' -- "
                                f"set one in ⚙️ Settings, or change the default engine there)")
                            continue
                        kwargs["engine"] = translate_engines.get_engine(engine_choice, api_key or "local")
                    elif stage == "transcription":
                        kwargs["use_gpu"] = st.session_state.get("use_gpu", False)

                    results = benchmark.run_suite(prepared, stage, **kwargs)
                    for r in results:
                        db.save_benchmark_run(r["case_id"], r, run_label=bm_run_label)
            if skipped_stages:
                st.warning("Skipped: " + "; ".join(skipped_stages))
            st.success("Done.")
            st.rerun()

        st.markdown("**Cases & latest results**")
        latest_runs = db.latest_benchmark_run_per_case()
        for c in bm_cases:
            history = db.list_benchmark_runs(c["id"])
            with st.container(border=True):
                hc1, hc2, hc3 = st.columns([3, 1, 1])
                hc1.write(f"**{c['label']}** — {_bm_stage_labels[c['stage']]}")
                latest = latest_runs.get(c["id"])
                if not latest:
                    hc2.caption("Not run yet")
                elif latest.get("error"):
                    hc2.error("Failed")
                elif latest.get("score") is not None:
                    hc2.metric("Score", f"{latest['score']:.0%}")
                else:
                    hc2.caption("No reference — unscored")
                if hc3.button("🗑️ Remove", key=f"bm_del_{c['id']}"):
                    db.delete_benchmark_case(c["id"])
                    st.rerun()

                if latest and latest.get("error"):
                    st.code(latest["error"], language="text")
                elif len(history) >= 2 and history[-1].get("score") is not None and history[-2].get("score") is not None:
                    delta = history[-1]["score"] - history[-2]["score"]
                    if delta < -0.05:
                        st.warning(f"⚠️ Regression: score dropped {abs(delta):.0%} since the previous run "
                                  f"({history[-2]['score']:.0%} → {history[-1]['score']:.0%}).")
                    elif delta > 0.05:
                        st.success(f"Improved {delta:.0%} since the previous run "
                                  f"({history[-2]['score']:.0%} → {history[-1]['score']:.0%}).")
                if history:
                    with st.expander(f"Run history ({len(history)})"):
                        for run in reversed(history):
                            score_str = f"{run['score']:.0%}" if run.get("score") is not None else "unscored"
                            st.caption(f"{run['created_at'][:19]} — {score_str} — "
                                      f"{run['duration_seconds']:.1f}s"
                                      + (f" — {run['run_label']}" if run.get("run_label") else "")
                                      + (f" — ❌ {run['error']}" if run.get("error") else ""))

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
        for key in ["claude", "deepseek", "gemini", "deepl", "google", "hf_token"]
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
    c1, c2, c3, c4 = st.columns(4)
    py = results["python"]
    c1.metric("Python", py["version"], "OK" if py["ok"] else "Needs 3.9+")
    ff = results["ffmpeg"]
    c2.metric("ffmpeg", "Found" if ff["found"] else "Missing")
    c3.metric("Library writable", "Yes" if results["library_writable"] else "No")
    js_rt = results["js_runtime"]
    c4.metric("JS runtime (YouTube)", js_rt["name"].title() if js_rt["found"] else "Missing")
    if not ff["found"]:
        st.error("ffmpeg not found on PATH -- most features (audio, video, dubbing) won't work "
                 "until it's installed. See README for install steps.")
    elif ff.get("version"):
        st.caption(ff["version"])
    if not js_rt["found"]:
        st.warning("No JavaScript runtime (Deno, Node, Bun or QuickJS) found on PATH -- "
                   "downloads and Live capture from any site (not just YouTube) may silently "
                   "lose formats without one. Install Deno (https://deno.land) and run "
                   "`pip install -U yt-dlp`.")
    st.caption("If a download or Live capture fails because the site needs you signed in "
              "(common on TikTok and Instagram, less so on YouTube), turn on cookie-based "
              "login under ⚙️ Settings → Downloads.")

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
                installable = tier in diagnostics.INSTALLABLE_TIERS and not info["installed"]
                if not installable:
                    st.caption(f"{icon} **{name}** -- {info['powers']}")
                    continue
                dep_c1, dep_c2 = st.columns([5, 1])
                dep_c1.caption(f"{icon} **{name}** -- {info['powers']}")
                if dep_c2.button("⬇️ Install", key=f"install_dep_btn_{name}"):
                    dep_result = _run_pip_stream(
                        f"Installing {name}...", f"Installed {name}.",
                        f"Install failed for {name} -- see output above.",
                        diagnostics.stream_pip_install([name]))
                    if dep_result["ok"]:
                        st.session_state["diagnostics_results"] = diagnostics.run_full_diagnostics(
                            project_root, db.LIBRARY_DIR, api_keys_set)
                        st.rerun()

    missing_required = [k for k, v in deps.items() if v["tier"] == "required" and not v["installed"]]
    if missing_required:
        st.error(f"Missing required dependencies: {', '.join(missing_required)}. "
                 f"Run `pip install -r requirements.txt` again.")


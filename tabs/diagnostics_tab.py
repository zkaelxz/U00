"""
tabs/diagnostics.py -- "Check my setup" page. One glance instead of an
iterative back-and-forth when something's missing or misconfigured.

Step 18: regrouped into individually collapsible sections so a routine
health check (Check my setup, Running jobs, Model & engine versions --
open by default) doesn't sit in the same view as the accuracy-benchmark
tool or the danger-zone reset (collapsed by default).
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


def _benchmark_translation_engine(engine_choice: str):
    """(engine, None), or (None, why it can't run) when the engine needs an
    API key that isn't set."""
    api_key = st.session_state.get(f"settings_{engine_choice}", "")
    needs_key = engine_choice not in ("ollama", "test_offline", "libretranslate", "nllb")
    if needs_key and not api_key:
        return None, (f"no API key set for '{engine_choice}' -- set one in ⚙️ Settings, "
                      f"or pick another engine")
    # Step 25d item 7: same gap Step 5b item 1 already fixed elsewhere --
    # without these, a custom Ollama URL was ignored and Gemini was always
    # billed as paid-tier.
    return translate_engines.get_engine(
        engine_choice, api_key or "local",
        free_tier=engine_choice == "gemini" and st.session_state.get("gemini_free_tier", False),
        base_url=st.session_state.get("settings_ollama_url") or None
        if engine_choice == "ollama" else None), None


# Step 24: what "compare two engines" picks between, per benchmark stage.
_BM_COMPARE_OPTIONS = {
    "translation": ("Engine", lambda: list(translate_engines.ENGINES.keys())),
    "ocr": ("OCR backend", lambda: ["tesseract", "paddle", "manga_ocr", "paddle_vl_manga"]),
    "transcription": ("Whisper size", lambda: list(core_module.WHISPER_MODELS.keys())),
}


def _run_pip_stream(running_label: str, done_label: str, failed_label: str, stream_gen) -> dict:
    """Runs a diagnostics.stream_* generator inside an st.status box,
    writing each real output line as it arrives rather than just
    spinning, and returns the final {"done": True, ...} item (minus that
    marker key) once it's done -- always at least {"ok": bool}, plus
    whatever else that particular stream reports (Step 62's Deno install
    also reports "on_path"/"needs_restart"). Shared by the generic
    per-package Install button, the GPU-PyTorch reinstall button, and
    Step 62's bulk/Deno installs below -- all need the exact same "show
    real progress, never swallow the real error" handling."""
    result = {"ok": False}
    with st.status(running_label, expanded=True) as box:
        for item in stream_gen:
            if item.get("done"):
                result = {k: v for k, v in item.items() if k != "done"}
            else:
                box.write(item["line"])
        box.update(label=done_label if result["ok"] else failed_label,
                   state="complete" if result["ok"] else "error")
    return result


def _run_bulk_install_stream(tier_label: str, requirements_path: str) -> dict:
    """Runs diagnostics.stream_bulk_install inside one st.status box, with
    a header line before each package's own output so a bad package's
    failure is easy to find in a long combined log even though the whole
    batch keeps going past it. Returns {package: ok} for every package
    the file named, once all of them have been attempted."""
    results = {}
    with st.status(f"Installing everything in {tier_label}...", expanded=True) as box:
        for item in diagnostics.stream_bulk_install(requirements_path):
            if item.get("bulk_done"):
                results = item["results"]
            elif item.get("start"):
                box.write(f"**{item['package']}**")
            elif item.get("done"):
                box.write("✅ done" if item["ok"] else "❌ failed")
            else:
                box.write(item["line"])
        if not results:
            box.update(label=f"Nothing to install in {tier_label} (file empty or missing).",
                       state="complete")
        else:
            n_ok = sum(1 for ok in results.values() if ok)
            box.update(label=f"{tier_label}: {n_ok}/{len(results)} installed.",
                       state="complete" if n_ok == len(results) else "error")
    return results


def _install_confirmed(container, key: str, warning: str) -> bool:
    """Renders an "Install" button in `container`; returns True the
    instant the install should actually run. Step 47 item 4: when
    `warning` is set (a redundant heavy local TTS backend is already
    installed), the first click only arms a confirmation -- shown as a
    full-width warning plus its own "Install anyway"/"Cancel" pair --
    rather than installing immediately. Never blocks the install, only
    adds one extra deliberate click, matching this app's existing
    "confirm before a consequential action" pattern scaled down for a
    reversible one. With no warning, behaves exactly like a plain button.

    Step 64: `type="primary"` (the app's one accent colour, per
    ui_theme.py) plus the icon in its own native `icon=` slot rather than
    glued onto the label text -- the same emoji as before, just rendered
    as a real icon instead of a leading character, so this reads as a
    deliberate action button rather than a bare default with an emoji
    stuck on the front. Visual only; the confirm/cancel/install behavior
    itself is unchanged."""
    confirm_key = f"{key}__confirm_redundant"
    if st.session_state.get(confirm_key):
        st.warning(warning)
        wc1, wc2 = st.columns(2)
        if wc1.button("Install anyway", key=f"{key}__proceed", icon="⬇️", type="primary"):
            st.session_state.pop(confirm_key, None)
            return True
        if wc2.button("Cancel", key=f"{key}__cancel"):
            st.session_state.pop(confirm_key, None)
            st.rerun()
        return False
    if container.button("Install", key=key, icon="⬇️", type="primary"):
        if warning:
            st.session_state[confirm_key] = True
            st.rerun()
        return True
    return False


def render_diagnostics_tab():
    ui_theme.type_scale_scope()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    api_keys_set = {
        key: bool(st.session_state.get(f"settings_{key}"))
        for key in ["claude", "deepseek", "gemini", "deepl", "google", "hf_token"]
    }

    # ---- Routine health checks: open by default -----------------------
    with st.expander("🩺 Check my setup", expanded=True):
        st.caption("Run this any time something isn't working -- shows what's installed, what's "
                  "missing, and what's configured, in one place.")
        if st.button("🔍 Run diagnostics", type="primary"):
            with st.spinner("Checking..."):
                st.session_state["diagnostics_results"] = diagnostics.run_full_diagnostics(
                    project_root, db.LIBRARY_DIR, api_keys_set)

        results = st.session_state.get("diagnostics_results")
        if not results:
            st.info("Click the button above to run a check.")
        else:
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
                jsc1, jsc2 = st.columns([5, 1])
                jsc1.warning("No JavaScript runtime (Deno, Node, Bun or QuickJS) found on PATH -- "
                            "downloads and Live capture from any site (not just YouTube) may silently "
                            "lose formats without one. Install Deno below (or install one of the "
                            "others yourself) and run `pip install -U yt-dlp`.")
                if jsc2.button("⬇️ Install Deno", key="install_deno_btn"):
                    deno_result = _run_pip_stream(
                        "Installing Deno...", "Deno installed.",
                        "Deno install failed -- see output above.",
                        diagnostics.stream_deno_install())
                    if deno_result["ok"] and deno_result.get("needs_restart"):
                        st.info("Deno was installed, but this app needs a restart before it can "
                               "see it on PATH -- close and reopen it (or just your terminal) "
                               "once, then re-run diagnostics.")
                    elif deno_result["ok"]:
                        st.success("Done -- re-checking...")
                        st.rerun()
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

            # ---- Dependencies ----
            st.subheader("Dependencies")
            deps = results["dependencies"]
            tier_order = ["required", "engine", "feature", "dev"]
            tier_labels = {"required": "Core (needed for the app to run at all)",
                           "engine": "Translation engines",
                           "feature": "Optional features",
                           "dev": "Development"}

            st.caption("Checks every installed dependency's version against PyPI's latest release -- "
                      "reaches the network (the only other check on this page that does, besides "
                      "pyannote access below) only when you click this button, never automatically.")
            if st.button("🔍 Check for dependency updates", key="check_dep_versions_btn"):
                with st.spinner("Checking PyPI..."):
                    st.session_state["dependency_version_results"] = diagnostics.check_dependency_versions(deps)
            version_results = st.session_state.get("dependency_version_results") or {}
            installed_dep_names = {k for k, v in deps.items() if v["installed"]}

            for tier in tier_order:
                tier_deps = {k: v for k, v in deps.items() if v["tier"] == tier}
                if not tier_deps:
                    continue
                with st.expander(f"{tier_labels[tier]} ({sum(1 for v in tier_deps.values() if v['installed'])}/{len(tier_deps)} installed)",
                                  expanded=(tier == "required")):
                    for name, info in sorted(tier_deps.items()):
                        icon = "✅" if info["installed"] else "❌"
                        installable = tier in diagnostics.INSTALLABLE_TIERS and not info["installed"]

                        version_suffix = ""
                        version_info = version_results.get(name)
                        upgradeable = False
                        # Step 61: a not-yet-installed package can have the same
                        # kind of known, Python-version-specific install gap
                        # upgrade_blocked_reason already tracks for an outdated
                        # one (audio-separator/diffq-fixed on Python 3.14) --
                        # say so up front rather than a bare "not installed"
                        # that only reveals the real reason once Install is
                        # clicked and fails.
                        known_install_reason = (diagnostics.known_install_limitation_reason(name)
                                                if installable else None)
                        if known_install_reason:
                            version_suffix = f" -- ⚠️ {known_install_reason}"
                        if version_info:
                            iv, lv = version_info["installed_version"], version_info["latest_version"]
                            if version_info["outdated"] is True:
                                version_suffix = f" -- 🔶 outdated (`{iv}` installed, `{lv}` latest)"
                                blocked_reason = diagnostics.upgrade_blocked_reason(
                                    name, lv, project_root=project_root)
                                if blocked_reason:
                                    version_suffix += f" -- {blocked_reason}"
                                else:
                                    upgradeable = tier in diagnostics.INSTALLABLE_TIERS
                            elif version_info["outdated"] is False:
                                version_suffix = f" -- 🟢 up to date (`{iv}`)"
                            elif iv:
                                version_suffix = f" -- `{iv}` (couldn't reach PyPI to check the latest)"

                        if not installable and not upgradeable:
                            st.caption(f"{icon} **{name}** -- {info['powers']}{version_suffix}")
                            continue
                        dep_c1, dep_c2 = st.columns([5, 1])
                        dep_c1.caption(f"{icon} **{name}** -- {info['powers']}{version_suffix}")
                        if installable:
                            redundant_warning = diagnostics.redundant_tts_install_warning(
                                name, installed_dep_names)
                            if _install_confirmed(dep_c2, f"install_dep_btn_{name}", redundant_warning):
                                dep_result = _run_pip_stream(
                                    f"Installing {name}...", f"Installed {name}.",
                                    f"Install failed for {name} -- see output above.",
                                    diagnostics.stream_dependency_install(name, project_root=project_root))
                                if not dep_result["ok"] and known_install_reason:
                                    # Step 61: the real pip/Cython traceback above is
                                    # never hidden (see stream_pip_install's own
                                    # docstring on why), but a known, already-diagnosed
                                    # failure shouldn't leave that traceback as the
                                    # only signal either -- say plainly what's going
                                    # on and that the app still works without it.
                                    st.info(f"This is a known issue, not something wrong with your "
                                            f"setup: {known_install_reason}")
                                if dep_result["ok"]:
                                    st.session_state["diagnostics_results"] = diagnostics.run_full_diagnostics(
                                        project_root, db.LIBRARY_DIR, api_keys_set)
                                    st.rerun()
                        elif upgradeable:
                            if dep_c2.button("Upgrade", key=f"upgrade_dep_btn_{name}",
                                            icon="⬆️", type="primary"):
                                dep_result = _run_pip_stream(
                                    f"Upgrading {name}...", f"Upgraded {name}.",
                                    f"Upgrade failed for {name} -- see output above.",
                                    diagnostics.stream_pip_install(
                                        diagnostics.upgrade_pip_args(name, project_root)))
                                if dep_result["ok"]:
                                    st.session_state["diagnostics_results"] = diagnostics.run_full_diagnostics(
                                        project_root, db.LIBRARY_DIR, api_keys_set)
                                    # The version we just upgraded past is now stale;
                                    # drop it rather than making another automatic
                                    # PyPI call to refresh it (this page's own "no
                                    # network call unless you click" rule).
                                    st.session_state.pop("dependency_version_results", None)
                                    st.rerun()

            missing_required = [k for k, v in deps.items() if v["tier"] == "required" and not v["installed"]]
            if missing_required:
                st.error(f"Missing required dependencies: {', '.join(missing_required)}. "
                         f"Run `pip install -r requirements.txt` again.")

            st.markdown("**Bulk install a whole tier**")
            st.caption(
                "Installs every package in the file one at a time -- unlike a plain `pip install "
                "-r`, one package failing (Step 61's audio-separator/diffq-fixed case, for example) "
                "doesn't abort the rest; every package still gets attempted and reported.")
            bulk_files = {"requirements-core.txt": "Core", "requirements-media.txt": "Media",
                         "requirements-optional.txt": "Optional"}
            bulk_cols = st.columns(len(bulk_files))
            for col, (fname, label) in zip(bulk_cols, bulk_files.items()):
                if col.button(f"Install everything in {fname}", key=f"bulk_install_{fname}"):
                    st.session_state["bulk_install_results"] = _run_bulk_install_stream(
                        label, os.path.join(project_root, fname))
            _bulk_results = st.session_state.get("bulk_install_results")
            if _bulk_results:
                for pkg, ok in _bulk_results.items():
                    (st.success if ok else st.error)(f"{'✅' if ok else '❌'} {pkg}")

    with st.expander("🏃 Running jobs", expanded=True):
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

    with st.expander("🔍 What happened? (job history)", expanded=False):
        st.caption("Step 58: \"why did my last job take so long / fail?\" for a job still "
                  "resident in this process's memory. Jobs aren't persisted across an app "
                  "restart, so nothing shows here after one -- and only the job's total "
                  "wall-clock time is available; a per-stage (download/ASR/diarization/"
                  "translation/etc.) breakdown needs Step 41, which hasn't landed yet.")
        finished = {jid: j for jid, j in background_jobs.list_all_jobs().items()
                   if j["status"] != "running"}
        if not finished:
            st.caption("No finished job in this process's memory yet.")
        else:
            for job_id in sorted(finished, key=lambda j: finished[j].get("finished_at") or 0,
                                 reverse=True):
                job = finished[job_id]
                with st.expander(f"{_describe_job(job_id)} -- {job['status']}"):
                    explanation = debug_view.explain_job(job_id)
                    if explanation["duration_seconds"] is not None:
                        st.write(f"**Total time:** {explanation['duration_seconds']:.1f}s")
                    if explanation["error"]:
                        st.error(explanation["error"])
                    st.caption(explanation["per_stage_breakdown_note"])

    with st.expander("🐞 Saved bug-reproduction bundles", expanded=False):
        st.caption("Step 58 item 5: a frozen input/output snapshot saved from a line's "
                  "\"What happened here?\" panel in Review & edit -- re-run it here to check "
                  "whether it still reproduces the same result.")
        bundles = db.list_bug_reports()
        if not bundles:
            st.caption("No bug bundle saved yet.")
        else:
            for b in bundles:
                drama = db.get_drama(b["drama_id"])
                title = (drama.get("title_en") or drama.get("title_zh")) if drama else "(deleted drama)"
                with st.expander(f"#{b['id']} {b['label']} -- {title}"):
                    st.caption(f"Recorded engine/model: {b['engine']} / {b['model'] or '—'}")
                    st.write(f"**Output when saved:** {b['produced_output']}")
                    if b["replayed"]:
                        st.write(f"**Last replay:** {b['replay_output']}")
                        if b["reproduced"]:
                            st.warning("Still reproduces the same output.")
                        else:
                            st.success("No longer reproduces -- output has changed.")
                    _key = b["engine"]
                    _api_key = st.session_state.get(f"settings_{_key}", "")
                    if st.button("▶️ Replay", key=f"bug_replay_{b['id']}",
                                disabled=_key not in translate_engines.ENGINES):
                        replay_engine = translate_engines.get_engine(_key, _api_key, b["model"] or None)
                        with st.spinner("Replaying..."):
                            debug_view.replay_bug_bundle(b["id"], replay_engine)
                        st.rerun()
                    if st.button("🗑️ Delete bundle", key=f"bug_delete_{b['id']}"):
                        db.delete_bug_report(b["id"])
                        st.rerun()

    with st.expander("🧩 Model & engine versions", expanded=True):
        st.caption("What's actually installed/configured locally for every AI model or engine "
                  "this app wires into a feature. No network call -- this doesn't check whether "
                  "something newer exists, only what's here right now.")
        model_versions = diagnostics.get_model_engine_versions(
            st.session_state.get("settings_ollama_model"))
        installed_model_packages = {m["package"] for m in model_versions
                                    if m.get("package") and m["installed"]}
        for m in model_versions:
            icon = "✅" if m["installed"] else "❌"
            version_text = f"`{m['version']}`" if m["installed"] else "*not installed*"
            row_c1, row_c2, row_c3, row_c4 = st.columns([0.5, 4, 2, 1.6])
            if m.get("help"):
                with row_c1.popover("❓"):
                    st.caption(m["help"])
            row_c2.markdown(f"{icon} [**{m['name']}**]({m['url']})")
            row_c3.markdown(version_text)
            if not m["installed"] and m.get("package"):
                redundant_warning = diagnostics.redundant_tts_install_warning(
                    m["package"], installed_model_packages)
                if _install_confirmed(row_c4, f"install_model_btn_{m['name']}", redundant_warning):
                    model_result = _run_pip_stream(
                        f"Installing {m['name']}...", f"Installed {m['name']}.",
                        f"Install failed for {m['name']} -- see output above.",
                        diagnostics.stream_dependency_install(m["package"], project_root=project_root))
                    if model_result["ok"]:
                        st.rerun()

        st.markdown("**GPU status**")
        gpu_status = diagnostics.get_gpu_status()
        if gpu_status["available"]:
            st.caption(f"**{gpu_status['name']}** -- "
                      f"{gpu_status['vram_used_gb']:.1f} GB / {gpu_status['vram_total_gb']:.1f} GB VRAM used"
                      + (f" (torch built for CUDA {gpu_status['torch_cuda_version']})"
                         if gpu_status.get("torch_cuda_version") else ""))
        else:
            st.caption(gpu_status.get("message", "GPU info unavailable."))

        if diagnostics.gpu_torch_mismatch():
            st.warning("A real NVIDIA GPU is on this machine, but the installed PyTorch build is "
                      "CPU-only -- every GPU-touching stage (diarization, vocal separation, "
                      "transcription, TTS) is running on CPU instead of your GPU.")
            if st.button("⚡ Install GPU PyTorch", key="install_gpu_torch_btn"):
                gpu_result = _run_pip_stream(
                    "Reinstalling PyTorch with GPU/CUDA support...",
                    "GPU PyTorch installed.", "GPU PyTorch reinstall failed -- see output above.",
                    diagnostics.stream_gpu_torch_reinstall(project_root=project_root))
                if gpu_result["ok"]:
                    st.success("Done -- re-checking...")
                    st.rerun()
                else:
                    st.error("GPU PyTorch reinstall failed -- see the streamed output above for "
                             "the real pip error.")

    # ---- Less frequently needed: collapsed by default ------------------
    with st.expander("💾 Downloaded model cache", expanded=False):
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

        # Step 25d item 14: Piper voices (Step 25c item 1's offline-voice
        # picker) download to library/piper_voices instead of the Hugging
        # Face cache above, so they were invisible to this whole panel.
        piper_voices = diagnostics.scan_piper_voices()
        if piper_voices:
            st.caption(f"**Piper voices** (`library/piper_voices`): {len(piper_voices)} downloaded, "
                      f"{storage.format_bytes(sum(e['size_bytes'] for e in piper_voices))} total.")
            for entry in piper_voices:
                pc1, pc2 = st.columns([5, 1])
                pc1.caption(f"**{entry['voice']}** -- {storage.format_bytes(entry['size_bytes'])}")
                if pc2.button("🗑️ Delete", key=f"piper_voice_del_{entry['voice']}"):
                    if diagnostics.delete_piper_voice(entry["voice"]):
                        st.success(f"Deleted {entry['voice']}.")
                        st.rerun()
                    else:
                        st.error("Delete failed -- see the log for details.")

    with st.expander("🔒 pyannote gated model access", expanded=False):
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

    with st.expander("🤖 App Assistant", expanded=False):
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
                project_root, db.LIBRARY_DIR, api_keys_set)
            help_diag_text = diagnostics.format_diagnostics_report(
                help_diag_results, hf_cache, model_versions)
            st.session_state["app_help_report_text"] = diagnostics.redact_for_support(
                app_help.format_help_report(last_question, last_answer, help_diag_text))
        if st.session_state.get("app_help_report_text"):
            st.code(st.session_state["app_help_report_text"], language="text")

    with st.expander("📋 Copy diagnostics for support", expanded=False):
        st.caption("A redacted summary of everything above -- API keys, local file paths, and your "
                  "OS username are stripped, even though the panels above show the real values. "
                  "Use the copy icon in the top-right of the box below.")
        if st.button("📋 Build copyable report", key="build_support_report"):
            _report_results = st.session_state.get("diagnostics_results") or diagnostics.run_full_diagnostics(
                project_root, db.LIBRARY_DIR, api_keys_set)
            st.session_state["support_report_text"] = diagnostics.redact_for_support(
                diagnostics.format_diagnostics_report(_report_results, hf_cache, model_versions))
        if st.session_state.get("support_report_text"):
            st.code(st.session_state["support_report_text"], language="text")

    with st.expander("📜 Log", expanded=False):
        st.caption("The last lines from library/logs/app.log -- background job failures land "
                  "here even after the on-screen message is gone. Use the copy icon in the "
                  "top-right of the box below to copy it for a bug report.")
        import applog
        log_filter = st.text_input(
            "Filter (e.g. a drama title or \"ERROR\")", key="diagnostics_log_filter")
        log_lines = applog.filter_lines(applog.tail(50), log_filter)
        if log_lines:
            st.code("\n".join(log_lines), language="text")
        elif log_filter.strip():
            st.caption("No log lines match that filter.")
        else:
            st.caption("Nothing logged yet.")

    with st.expander("🎯 Accuracy benchmark", expanded=False):
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
                            engine, skip_reason = _benchmark_translation_engine(engine_choice)
                            if engine is None:
                                skipped_stages.append(f"translation ({skip_reason})")
                                continue
                            kwargs["engine"] = engine
                        elif stage == "transcription":
                            kwargs["use_gpu"] = st.session_state.get("use_gpu", False)

                        results = benchmark.run_suite(prepared, stage, **kwargs)
                        for r in results:
                            db.save_benchmark_run(r["case_id"], r, run_label=bm_run_label)
                if skipped_stages:
                    st.warning("Skipped: " + "; ".join(skipped_stages))
                st.success("Done.")
                st.rerun()

            with st.expander("⚖️ Compare engines on one case"):
                st.caption(
                    "Runs one case through two engines back to back and shows both outputs side "
                    "by side. Comparison runs aren't saved to the case's run history, so they "
                    "never skew its regression check.")
                cmp_case = st.selectbox(
                    "Case", bm_cases, key="bm_cmp_case",
                    format_func=lambda c: f"{c['label']} — {_bm_stage_labels[c['stage']]}")
                cmp_label, cmp_options = _BM_COMPARE_OPTIONS[cmp_case["stage"]]
                cmp_options = cmp_options()
                cc1, cc2 = st.columns(2)
                cmp_a = cc1.selectbox(f"{cmp_label} A", cmp_options, key=f"bm_cmp_a_{cmp_case['stage']}")
                cmp_b = cc2.selectbox(f"{cmp_label} B", cmp_options, index=min(1, len(cmp_options) - 1),
                                      key=f"bm_cmp_b_{cmp_case['stage']}")
                if st.button("⚖️ Compare", key="bm_cmp_run", disabled=cmp_a == cmp_b):
                    import benchmark
                    case_dict = dict(cmp_case)
                    if cmp_case.get("input_filename"):
                        case_dict["input_path"] = os.path.join(db.BENCHMARK_DIR, cmp_case["input_filename"])
                    configs, cmp_error = [], None
                    for choice in (cmp_a, cmp_b):
                        if cmp_case["stage"] == "translation":
                            engine, skip_reason = _benchmark_translation_engine(choice)
                            if engine is None:
                                cmp_error = skip_reason
                                break
                            configs.append((choice, {"engine": engine}))
                        elif cmp_case["stage"] == "ocr":
                            configs.append((choice, {"backend": choice}))
                        else:
                            configs.append((choice, {"whisper_size": choice,
                                                     "use_gpu": st.session_state.get("use_gpu", False)}))
                    if cmp_error:
                        st.warning(cmp_error)
                    else:
                        with st.spinner("Running both..."):
                            st.session_state["bm_cmp_results"] = {
                                "case_id": cmp_case["id"],
                                "results": benchmark.compare_configs(case_dict, cmp_case["stage"], configs)}
                _cmp = st.session_state.get("bm_cmp_results")
                if _cmp and _cmp["case_id"] == cmp_case["id"]:
                    for col, r in zip(st.columns(len(_cmp["results"])), _cmp["results"]):
                        with col:
                            st.markdown(f"**{r['label']}**")
                            if r.get("error"):
                                st.error(r["error"])
                            elif r.get("score") is not None:
                                st.metric("Score", f"{r['score']:.0%}")
                            else:
                                st.caption("No reference — unscored")
                            st.caption(f"{r['duration_seconds']:.1f}s"
                                       + (f" · ${r['cost_usd']:.4f}" if r.get("cost_usd") else ""))
                            st.code(r.get("output_text") or "(no output)", language=None, wrap_lines=True)

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

    with st.expander("☠️ Danger zone", expanded=False):
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

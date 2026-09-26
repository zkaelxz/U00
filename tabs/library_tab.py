"""
tabs/library.py -- Library tab UI, extracted from the former monolithic app.py.
"""
import time

from common import *


def cache_hit_share(usage: dict) -> float:
    """Share of logged input tokens that were prompt-cache reads."""
    total = usage.get("input_tokens") or 0
    return (usage.get("cache_read_tokens") or 0) / total if total else 0.0


BULK_SERIES_TRANSLATE_JOB_ID = "bulk_series_translate"


def run_bulk_series_translate_job(job_id, drama_ids, api_keys: dict, default_locale: str = "en-US",
                                  ollama_base_url: str = None, gemini_free_tier: bool = False):
    """Step 9b.3: translates every drama in drama_ids that has no
    translation yet, queued ONE AT A TIME rather than all at once (same
    GPU/API-load reasoning as everywhere else in this app that queues
    rather than parallelizes). Each drama uses its own saved engine
    (`drama.translation_engine`) and, if it belongs to a series, that
    series' own glossary and style hints -- the same settings its own
    Workspace tab would build for it (mirrors cli.cmd_translate's own
    UI-parity logic). Skips (does not queue) a drama that already has a
    translate job running elsewhere, rather than racing it.

    Deliberately reuses the real per-drama job id ("translate_<id>")
    run_translate_job already uses -- if the user opens that drama's own
    Workspace tab mid-run, they see the same real job, not a shadow copy.
    This coordinator job's own progress/message combine the queue
    position with that live per-drama progress into one line, for
    Library's combined status display.

    api_keys: {engine_name: api_key} gathered from Settings by the caller
    BEFORE starting this as a background job -- this function runs in a
    thread and must never touch st.session_state (background_jobs.py's
    hard rule).
    """
    results = {"translated": [], "skipped_running": [], "skipped_no_key": [],
               "skipped_no_lines": [], "errors": {}}
    total = len(drama_ids) or 1
    for i, did in enumerate(drama_ids):
        if background_jobs.is_cancel_requested(job_id):
            break
        drama = db.get_drama(did)
        title = (drama.get("title_en") or drama.get("title_zh") or f"drama #{did}") if drama else f"drama #{did}"
        per_job_id = f"translate_{did}"
        background_jobs.update_progress(job_id, i / total, f"Translating {i + 1}/{len(drama_ids)} -- {title} (0%)")
        if not drama:
            continue
        if background_jobs.is_running(per_job_id):
            results["skipped_running"].append(did)
            continue
        rows = db.load_lines(did)
        if not rows:
            results["skipped_no_lines"].append(did)
            continue

        engine_choice = drama.get("translation_engine") or "claude"
        needs_key = engine_choice not in ("ollama", "test_offline", "libretranslate", "nllb")
        api_key = api_keys.get(engine_choice)
        if needs_key and not api_key:
            results["skipped_no_key"].append(did)
            continue
        if not api_key:
            api_key = "offline" if engine_choice == "test_offline" else "local"
        try:
            engine = translate_engines.get_engine(
                engine_choice, api_key, None,
                free_tier=engine_choice == "gemini" and gemini_free_tier,
                base_url=ollama_base_url if engine_choice == "ollama" else None)
        except Exception as e:
            results["errors"][did] = translate_engines.redact_secrets(str(e))
            continue

        lines = core_module.lines_from_rows(rows)
        series_id = drama.get("series_id")
        glossary_terms = db.list_glossary_terms(series_id) if series_id else None
        series_chars = db.list_series_characters(series_id) if series_id else []
        drama_chars = db.list_characters_with_series_names(did)
        style_guidelines = tguide.build_style_guidelines(
            style_preset="audio_drama", glossary_terms=glossary_terms,
            custom_notes=tguide.build_character_gender_hints(series_chars, drama_chars))
        character_names = tguide.build_speaker_labels(drama_chars, series_chars)
        novel_reference = None
        if drama.get("novel_reference_filename"):
            novel_path = os.path.join(db.drama_dir(did), drama["novel_reference_filename"])
            if os.path.exists(novel_path):
                with open(novel_path, encoding="utf-8") as f:
                    novel_reference = f.read()

        import tabs.workspace_tab as workspace_tab
        background_jobs.start_job(
            per_job_id, workspace_tab.run_translate_job,
            per_job_id, did, lines, engine, drama, "", novel_reference, False, default_locale,
            glossary_terms, style_guidelines, engine_choice, "audio_drama", 6, None)

        while background_jobs.is_running(per_job_id):
            if background_jobs.is_cancel_requested(job_id):
                background_jobs.request_cancel(per_job_id)
            per_status = background_jobs.get_status(per_job_id) or {}
            background_jobs.update_progress(
                job_id, (i + (per_status.get("progress") or 0.0)) / total,
                f"Translating {i + 1}/{len(drama_ids)} -- {title} "
                f"({(per_status.get('progress') or 0.0) * 100:.0f}%)")
            time.sleep(0.5)

        if background_jobs.is_cancel_requested(job_id):
            break
        per_status = background_jobs.get_status(per_job_id) or {}
        if per_status.get("status") == "error":
            results["errors"][did] = per_status.get("error")
        else:
            results["translated"].append(did)

    background_jobs.update_progress(job_id, 1.0, "Done")
    background_jobs.set_result(job_id, results)


def render_library_tab():
    stats = db.get_library_stats()
    usage = db.get_usage_summary()
    st.subheader("📊 Dashboard")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total dramas", stats["total_dramas"])
    c2.metric("Lines translated", f"{stats['translated_lines']:,} / {stats['total_lines']:,}")
    c3.metric("API calls logged", usage["call_count"])
    c4.metric("Estimated spend", f"${usage['estimated_cost_usd']:.2f}")
    if usage["input_tokens"]:
        st.caption(f"Prompt-cache hits: {cache_hit_share(usage):.0%} of input tokens were "
                   "served from a provider's cache (billed at a fraction of the normal price).")
    if stats["by_status"]:
        status_line = " · ".join(f"{k}: {v}" for k, v in stats["by_status"].items())
        st.caption(f"By status — {status_line}")
    if stats["by_media_type"]:
        media_line = " · ".join(f"{k}: {v}" for k, v in stats["by_media_type"].items())
        st.caption(f"By type — {media_line}")

    _notice = st.session_state.pop("nav_notice", None)
    if _notice:
        st.info(_notice, icon="▶️")

    continuing = db.list_continue_reading(8)
    if continuing:
        st.subheader("▶️ Continue")
        cols = st.columns(min(4, len(continuing)))
        for i, d in enumerate(continuing):
            with cols[i % len(cols)]:
                ddir_c = db.drama_dir(d["id"])
                cover = d.get("cover_art_filename")
                if cover and os.path.exists(os.path.join(ddir_c, cover)):
                    st.image(os.path.join(ddir_c, cover), width='stretch')
                st.caption(f"**{d['title_en'] or d['title_zh']}**")
                st.progress(min(1.0, (d.get("percent_complete") or 0) / 100.0),
                            text=f"{d.get('percent_complete') or 0:.0f}%")
                if st.button("Resume", key=f"resume_{d['id']}"):
                    st.session_state.active_drama_id = d["id"]
                    st.session_state["reader_jump_page"] = d.get("last_page") or 1
                    st.session_state["reader_resume_pending"] = d["id"]
                    st.session_state.lines = None
                    # Streamlit's st.tabs has no API for switching tabs from
                    # Python, so this can only set the destination and say so
                    # plainly rather than pretending to navigate.
                    st.session_state["nav_notice"] = (
                        f"**{d['title_en'] or d['title_zh']}** is queued at page "
                        f"{d.get('last_page') or 1} — open the **📖 Read & Watch** tab above "
                        f"to pick up where you left off.")
                    st.rerun()

    recent = db.list_dramas_recently_active(8)
    if recent:
        with st.expander("🕓 Recently active"):
            for d in recent:
                st.caption(f"#{d['id']} {d['title_en'] or d['title_zh']} — {d['status']} "
                          f"(updated {d['updated_at'][:16].replace('T', ' ') if d['updated_at'] else '?'})")

    with st.expander("💰 Cost breakdown by drama"):
        by_drama = db.get_usage_by_drama()
        # call_count, not estimated_cost_usd > 0: a drama translated with a
        # free engine (or a free-tier Gemini key) has real usage logged but
        # legitimately costs $0 -- it belongs in this table labelled as
        # free, not silently dropped as if nothing had run.
        spending = [d for d in by_drama if d["call_count"] > 0]
        if spending:
            cost_df = pd.DataFrame(spending)[["id", "title_en", "title_zh", "input_tokens",
                                                "output_tokens", "estimated_cost_usd"]]
            cost_df["Est. cost"] = [
                "$0.00 (free)" if d["estimated_cost_usd"] == 0 else f"${d['estimated_cost_usd']:.2f}"
                for d in spending
            ]
            cost_df["Cache hits"] = [f"{cache_hit_share(d):.0%}" for d in spending]
            cost_df = cost_df.drop(columns=["estimated_cost_usd"])
            st.dataframe(cost_df, width='stretch', hide_index=True)
        else:
            st.caption("No usage logged yet.")

    st.divider()
    st.subheader("🔍 Search across all dramas")
    global_query = st.text_input("Search text in any drama's lines (Chinese or English)")
    if global_query:
        results = db.search_lines_globally(global_query)
        st.caption(f"{len(results)} match(es)")
        for r in results[:50]:
            st.caption(f"**#{r['drama_id']} {r['title_en'] or r['title_zh']}** line {r['idx']+1}: "
                      f"{r['zh']} → {r['en']}")

    st.divider()
    st.subheader("Filter")
    fc1, fc2, fc3, fc4, fc5, fc6, fc7 = st.columns(7)
    search = fc1.text_input("Search title/summary")
    studio_f = fc2.selectbox("Studio", [""] + db.distinct_values("studio"))
    author_f = fc3.selectbox("Author", [""] + db.distinct_values("author"))
    va_f = fc4.selectbox("Voice actor", [""] + db.distinct_voice_actors())
    status_f = fc5.selectbox("Status", ["", "not started", "aligned", "translated", "dubbed", "exported"])
    lang_f = fc6.selectbox("Language", ["", "zh", "ja", "ko"],
                            format_func=lambda l: {"": "All", "zh": "Chinese", "ja": "Japanese", "ko": "Korean"}[l])
    media_f = fc7.selectbox("Type", ["", "audio_drama", "video_drama", "novel", "manhwa", "manga", "manhua", "asmr", "other"],
                             format_func=lambda m: "All" if m == "" else m.replace("_", " ").title())

    all_tags = db.distinct_custom_tags()
    tag_f = st.multiselect("Custom tags", all_tags) if all_tags else []

    dramas = db.list_dramas(search=search, studio=studio_f, author=author_f,
                             voice_actor=va_f, status=status_f, source_language=lang_f, media_type=media_f)
    if tag_f:
        dramas = [d for d in dramas
                  if all(t in [x.strip() for x in (d.get("custom_tags") or "").split(",")] for t in tag_f)]
    st.caption(f"{len(dramas)} drama(s)")

    if dramas:
        _rows = []
        for _n, _d in enumerate(dramas, start=1):
            _rows.append({
                "#": _n,                      # tidy display position
                "id": _d["id"],               # real database id, never reused
                "title_en": _d.get("title_en"),
                "title_zh": _d.get("title_zh"),
                "author": tguide.format_bilingual_credit(
                    _d.get("author"), _d.get("author_romanized")),
                "studio": tguide.format_bilingual_credit(
                    _d.get("studio"), _d.get("studio_romanized")),
                "director": tguide.format_bilingual_credit(
                    _d.get("director"), _d.get("director_romanized")),
                "voice_actors": tguide.format_bilingual_credit(
                    _d.get("voice_actors"), _d.get("voice_actors_romanized")),
                "translation_engine": _d.get("translation_engine"),
                "status": _d.get("status"),
            })
        df = pd.DataFrame(_rows)
        df.insert(0, "Select", False)
        edited_df = st.data_editor(df, width='stretch', hide_index=True,
                                    disabled=[c for c in df.columns if c != "Select"],
                                    key="library_bulk_select")
        selected_ids = edited_df[edited_df["Select"]]["id"].tolist()

        if selected_ids:
            st.caption(f"{len(selected_ids)} drama(s) selected")
            bc1, bc2, bc3 = st.columns(3)
            bulk_status = bc1.selectbox("Set status to", ["not started", "aligned", "translated", "dubbed", "exported"],
                                         key="bulk_status_pick")
            if bc1.button("Apply status to selected"):
                for did in selected_ids:
                    db.update_drama(did, status=bulk_status)
                st.success(f"Updated {len(selected_ids)} drama(s).")
                st.rerun()
            confirm_bulk_delete = bc2.checkbox("Confirm delete", key="confirm_bulk_delete")
            if bc2.button("🗑️ Delete selected", disabled=not confirm_bulk_delete):
                for did in selected_ids:
                    db.delete_drama(did)
                st.success(f"Deleted {len(selected_ids)} drama(s).")
                st.rerun()

            untranslated_selected = [did for did in selected_ids
                                     if (db.get_drama(did) or {}).get("status") == "aligned"]
            bulk_translate_running = background_jobs.is_running(BULK_SERIES_TRANSLATE_JOB_ID)
            if bc3.button(f"🌐 Bulk translate ({len(untranslated_selected)})",
                          disabled=not untranslated_selected or bulk_translate_running):
                api_keys = {engine: st.session_state.get(f"settings_{engine}")
                           for engine in translate_engines.ENGINES}
                started = background_jobs.start_job(
                    BULK_SERIES_TRANSLATE_JOB_ID, run_bulk_series_translate_job,
                    BULK_SERIES_TRANSLATE_JOB_ID, untranslated_selected, api_keys,
                    default_locale=st.session_state.get("settings_default_locale", "en-US"),
                    ollama_base_url=st.session_state.get("settings_ollama_url") or None,
                    gemini_free_tier=st.session_state.get("gemini_free_tier", False))
                if started:
                    st.info("Bulk translation started -- queued one drama at a time. "
                           "Come back here any time to see progress.")
                    st.rerun()
            if not untranslated_selected and selected_ids:
                bc3.caption("None of the selected dramas are untranslated (status 'aligned').")

        _bulk_job = background_jobs.get_status(BULK_SERIES_TRANSLATE_JOB_ID)
        if _bulk_job:
            if _bulk_job["status"] == "running":
                bjc1, bjc2 = st.columns([5, 1])
                bjc1.progress(_bulk_job["progress"],
                             text=(_bulk_job.get("message") or "Translating...")
                             + background_jobs.eta_text(_bulk_job))
                if bjc2.button("⏹️ Cancel", key="bulk_series_translate_cancel"):
                    background_jobs.request_cancel(BULK_SERIES_TRANSLATE_JOB_ID)
                    st.rerun()
                if st.button("🔄 Refresh", key="bulk_series_translate_refresh"):
                    st.rerun()
            elif _bulk_job["status"] == "done":
                r = _bulk_job.get("result") or {}
                parts = [f"{len(r.get('translated', []))} translated"]
                if r.get("skipped_running"):
                    parts.append(f"{len(r['skipped_running'])} already running elsewhere")
                if r.get("skipped_no_key"):
                    parts.append(f"{len(r['skipped_no_key'])} skipped (no API key)")
                if r.get("skipped_no_lines"):
                    parts.append(f"{len(r['skipped_no_lines'])} skipped (no lines)")
                if r.get("errors"):
                    parts.append(f"{len(r['errors'])} failed")
                st.success("Bulk translation finished: " + ", ".join(parts) + ".")
                background_jobs.clear_job(BULK_SERIES_TRANSLATE_JOB_ID)
                # Step 9i: the same Step 9h staleness, triggered from a
                # different tab -- if the drama open in Workspace right now
                # is one of the ones just bulk-translated here, its
                # st.session_state.lines (and cached en_<idx>/zh_<idx>
                # widget values) are still the pre-translation snapshot
                # until this reloads them, same as a regular Translate job
                # completion already does.
                if st.session_state.get("active_drama_id") in r.get("translated", []):
                    import tabs.workspace_tab as workspace_tab
                    st.session_state.lines = db.load_line_objects(st.session_state["active_drama_id"])
                    workspace_tab._clear_line_widget_state()
            elif _bulk_job["status"] == "error":
                st.error(f"Bulk translation failed: {_bulk_job['error']}")
                background_jobs.clear_job(BULK_SERIES_TRANSLATE_JOB_ID)

        st.subheader("Bulk export")
        exportable = [d for d in dramas if d["status"] in ("translated", "dubbed", "exported")]
        if exportable:
            if st.button(f"📦 Export all {len(exportable)} translated dramas as .zip"):
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w") as zf:
                    for d in exportable:
                        rows = db.load_lines(d["id"])
                        lns = [Line(idx=r["idx"], start=r["start"], end=r["end"],
                                     zh=r["zh"], en=r["en"] or "", speaker=r.get("speaker")) for r in rows]
                        if not lns:
                            continue
                        safe_title = re.sub(r"[^\w\- ]", "", d["title_en"] or d["title_zh"] or str(d["id"]))
                        zf.writestr(f"{safe_title}/english.srt", lines_to_srt(lns, "en"))
                        zf.writestr(f"{safe_title}/chinese.srt", lines_to_srt(lns, "zh"))
                        zf.writestr(f"{safe_title}/bilingual.srt", lines_to_bilingual_srt(lns))
                        ddir = db.drama_dir(d["id"])
                        dub_path = os.path.join(ddir, "dub_track.wav")
                        if os.path.exists(dub_path):
                            zf.write(dub_path, f"{safe_title}/dub_track.wav")
                st.download_button("Download .zip", buf.getvalue(), file_name="dramas_export.zip")
        else:
            st.caption("No translated dramas yet to bulk export.")

        st.caption("For 50-100+ dramas, `cli.py` supports the same pipeline headlessly -- "
                   "see the README for batch commands.")
    else:
        st.info("No dramas yet — add one in the Workspace tab.")

    st.divider()
    st.subheader("🗄️ Storage")
    st.caption("Where disk space is going, and what's safe to reclaim. Source audio/video, "
              "reference novels, voice-clone samples, and the database are never touched.")
    sq1, sq2 = st.columns([2, 1])
    quality = sq1.selectbox("Storage quality preset", list(storage.STORAGE_QUALITY_PRESETS.keys()),
                             index=1, format_func=lambda k: storage.STORAGE_QUALITY_PRESETS[k]["label"])
    sq1.caption(storage.STORAGE_QUALITY_PRESETS[quality]["note"])
    if sq2.button("📊 Scan storage"):
        with st.spinner("Scanning..."):
            st.session_state["storage_scan"] = storage.scan_library_storage(
                db.LIBRARY_DIR, [d["id"] for d in db.list_dramas()])
    scan = st.session_state.get("storage_scan")
    if scan:
        m1, m2 = st.columns(2)
        m1.metric("Library size", storage.format_bytes(scan["total_bytes"]))
        m2.metric("Reclaimable", storage.format_bytes(scan["reclaimable_bytes"]))
        with st.expander("By category"):
            for k, v in scan["categories"].items():
                if v:
                    cfg = storage.CLEANABLE_CATEGORIES[k]
                    st.caption(f"**{cfg['label']}** — {storage.format_bytes(v)}. {cfg['note']}")
        with st.expander("Largest dramas"):
            for d in scan["per_drama"][:10]:
                dr = db.get_drama(d["drama_id"])
                nm = (dr["title_en"] or dr["title_zh"]) if dr else f"#{d['drama_id']}"
                st.caption(f"#{d['drama_id']} {nm} — {storage.format_bytes(d['total_bytes'])} "
                          f"({storage.format_bytes(d['reclaimable_bytes'])} reclaimable)")
        cats = storage.categories_for_preset(quality)
        confirm_clean = st.checkbox(f"Confirm cleanup using '{quality}' preset", key="confirm_clean")
        if st.button("🧹 Clean now", disabled=not confirm_clean):
            freed = 0
            for d in db.list_dramas():
                r = storage.clean_drama_storage(db.drama_dir(d["id"]), cats)
                freed += r["freed_bytes"]
            st.session_state["storage_scan"] = None
            st.success(f"Reclaimed {storage.format_bytes(freed)}.")
            st.rerun()

    st.divider()
    st.subheader("📜 Reading history")
    hist = db.list_reading_history(limit=25)
    if hist:
        for h in hist[:15]:
            when = h["accessed_at"][:16].replace("T", " ") if h["accessed_at"] else "?"
            pct = f"{h['percent_complete']:.0f}%" if h.get("percent_complete") is not None else ""
            st.caption(f"{when} — **{h['title_en'] or h['title_zh']}** {pct}")
        if st.button("Clear reading history"):
            db.clear_reading_history()
            st.rerun()
    else:
        st.caption("No reading history yet.")

    st.divider()
    st.subheader("💾 Backup & restore")
    st.caption("Backs up your whole library -- the database plus every drama's audio/video/dub "
              "files and reference clips. Worth doing before any big batch run.")

    bc1, bc2 = st.columns(2)
    with bc1:
        st.markdown("**Backup**")
        backups_dir = os.path.join(db.LIBRARY_DIR, "backups")

        if st.button("🗄️ Database-only backup (fast, small)"):
            import datetime
            os.makedirs(backups_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            db_backup_path = os.path.join(backups_dir, f"library_{ts}.db")
            db.snapshot_database(db_backup_path)
            with open(db_backup_path, "rb") as f:
                st.session_state["db_backup_bytes"] = f.read()
            st.session_state["db_backup_name"] = f"library_{ts}.db"
            st.success(f"Database snapshot saved to {db_backup_path}")
        if st.session_state.get("db_backup_bytes"):
            st.download_button("Download database backup", st.session_state["db_backup_bytes"],
                                file_name=st.session_state["db_backup_name"])

        if st.button("📦 Create full backup .zip (database + media)"):
            import datetime
            import tempfile
            os.makedirs(backups_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_path = os.path.join(backups_dir, f"baihe_library_backup_{ts}.zip")
            library_dir = db.LIBRARY_DIR
            skip_paths = {db.DB_PATH, db.DB_PATH + "-wal", db.DB_PATH + "-shm"}
            # Written straight to disk, one file at a time, rather than built
            # up in an in-memory BytesIO -- a multi-GB library (with video)
            # zipped entirely into memory, then kept a second time via
            # getvalue() and a third via session_state, could exhaust RAM.
            with tempfile.TemporaryDirectory() as tmpdir:
                db_snapshot_path = os.path.join(tmpdir, "library.db")
                db.snapshot_database(db_snapshot_path)
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, dirs, files in os.walk(library_dir, topdown=True):
                        if root == library_dir:
                            dirs[:] = [d for d in dirs if d != "backups"]
                        for fname in files:
                            full_path = os.path.join(root, fname)
                            if full_path in skip_paths:
                                continue
                            arcname = os.path.relpath(full_path, library_dir)
                            zf.write(full_path, arcname)
                    # A consistent snapshot of the database (see above),
                    # not the live library.db file, which under WAL mode
                    # can be missing writes still sitting in library.db-wal.
                    zf.write(db_snapshot_path, "library.db")
            st.success(f"Backup saved to {zip_path} "
                       f"({os.path.getsize(zip_path) / 1_000_000:.1f} MB).")

    with bc2:
        st.markdown("**Restore**")
        st.caption("⚠️ Replaces your entire current library. Back up first if unsure.")
        restore_file = st.file_uploader("Backup .zip to restore", type=["zip"], key="restore_upload")
        confirm_restore = st.checkbox("I understand this replaces all current library data", key="confirm_restore")
        if st.button("♻️ Restore from backup", disabled=not (restore_file and confirm_restore)):
            import shutil
            library_dir = db.LIBRARY_DIR
            try:
                if os.path.exists(library_dir):
                    shutil.rmtree(library_dir)
                os.makedirs(library_dir, exist_ok=True)
                with zipfile.ZipFile(io.BytesIO(restore_file.read())) as zf:
                    zf.extractall(library_dir)
                st.success("Restored. Reload the app to see the restored library.")
            except Exception as e:
                st.error(f"Restore failed: {e}")

    st.divider()
    st.subheader("🎛️ Presets")
    st.caption("Saved Workspace configurations (engine + model, translation style, English "
              "variant, pronoun default, genre-guidance toggle) -- save one from a drama's "
              "🌐 Translation section, apply it to any drama regardless of series. Deleting or "
              "renaming a preset never changes any drama it was previously applied to; a preset "
              "is a one-time fill-in, not a live link.")
    _presets = db.list_presets()
    if not _presets:
        st.caption("No presets saved yet.")
    else:
        for _p in _presets:
            _pc1, _pc2, _pc3 = st.columns([3, 2, 1])
            with _pc1:
                _fields = []
                if _p.get("translation_engine"):
                    _fields.append(_p["translation_engine"]
                                    + (f" ({_p['engine_model']})" if _p.get("engine_model") else ""))
                if _p.get("style_preset"):
                    _fields.append(_p["style_preset"])
                if _p.get("locale"):
                    _fields.append(_p["locale"])
                _fields.append("she/her default" if _p.get("default_female_pronouns") else "no pronoun default")
                _fields.append("genre guidance on" if _p.get("include_genre_notes") else "genre guidance off")
                st.markdown(f"**{_p['name']}**")
                st.caption(" · ".join(_fields))
            with _pc2:
                _new_name = st.text_input("Rename to", value=_p["name"],
                                          key=f"rename_preset_{_p['id']}", label_visibility="collapsed")
                if st.button("Rename", key=f"rename_preset_btn_{_p['id']}",
                            disabled=not _new_name.strip() or _new_name.strip() == _p["name"]):
                    db.rename_preset(_p["id"], _new_name.strip())
                    st.rerun()
            with _pc3:
                if st.button("🗑️", key=f"delete_preset_{_p['id']}", help=f"Delete \"{_p['name']}\""):
                    db.delete_preset(_p["id"])
                    st.rerun()


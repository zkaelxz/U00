"""
tabs/library.py -- Library tab UI, extracted from the former monolithic app.py.
"""
from common import *


def render_library_tab():
    stats = db.get_library_stats()
    usage = db.get_usage_summary()
    st.subheader("📊 Dashboard")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total dramas", stats["total_dramas"])
    c2.metric("Lines translated", f"{stats['translated_lines']:,} / {stats['total_lines']:,}")
    c3.metric("API calls logged", usage["call_count"])
    c4.metric("Estimated spend", f"${usage['estimated_cost_usd']:.2f}")
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
        spending = [d for d in by_drama if d["estimated_cost_usd"] > 0]
        if spending:
            cost_df = pd.DataFrame(spending)[["id", "title_en", "title_zh", "input_tokens",
                                                "output_tokens", "estimated_cost_usd"]]
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

        st.subheader("Open a drama")
        options = {f"#{d['id']} — {d['title_en'] or d['title_zh']} ({d['status']})": d["id"] for d in dramas}

        choice = st.selectbox("Select", list(options.keys()))
        if st.button("Open in Workspace →"):
            st.session_state.active_drama_id = options[choice]
            st.session_state.lines = None
            # Same Streamlit limitation as Resume above: st.tabs() can't be
            # switched from Python, so this sets the drama active and says
            # so plainly -- without this notice the click looks like it did
            # nothing, since the visible tab never changes on its own.
            st.session_state["nav_notice"] = (
                f"**{choice}** is now the active drama — open the **🛠️ Workspace** "
                f"tab above to work on it.")
            st.rerun()

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
        if st.button("📦 Create backup .zip"):
            import shutil
            buf = io.BytesIO()
            library_dir = db.LIBRARY_DIR
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(library_dir):
                    for fname in files:
                        full_path = os.path.join(root, fname)
                        arcname = os.path.relpath(full_path, library_dir)
                        zf.write(full_path, arcname)
            st.session_state["backup_buf"] = buf.getvalue()
            st.success(f"Backup ready ({len(st.session_state['backup_buf']) / 1_000_000:.1f} MB).")
        if st.session_state.get("backup_buf"):
            st.download_button("Download backup.zip", st.session_state["backup_buf"],
                                file_name="baihe_library_backup.zip")

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


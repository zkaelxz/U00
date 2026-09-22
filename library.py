"""
tabs/library.py -- Library tab UI, extracted from the former monolithic app.py.
"""
from common import *


def render_library_tab():
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

    dramas = db.list_dramas(search=search, studio=studio_f, author=author_f,
                             voice_actor=va_f, status=status_f, source_language=lang_f, media_type=media_f)
    st.caption(f"{len(dramas)} drama(s)")

    if dramas:
        df = pd.DataFrame(dramas)[["id", "title_en", "title_zh", "author", "studio",
                                    "director", "voice_actors", "translation_engine", "status"]]
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.subheader("Open a drama")
        options = {f"#{d['id']} — {d['title_en'] or d['title_zh']} ({d['status']})": d["id"] for d in dramas}
        choice = st.selectbox("Select", list(options.keys()))
        if st.button("Open in Workspace →"):
            st.session_state.active_drama_id = options[choice]
            st.session_state.lines = None
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


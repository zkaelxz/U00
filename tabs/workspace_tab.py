"""
tabs/workspace.py -- Workspace tab UI, extracted from the former monolithic app.py.
"""
from common import *


def run_translate_job(job_id, drama_id, lines, engine, drama_meta, style_note,
                       novel_reference, force_retranslate, locale, glossary_terms,
                       style_guidelines, engine_choice, style_preset):
    """
    The actual translation work, run inside a background thread by the
    Translate button. Deliberately touches nothing from Streamlit (no
    st.session_state, no widgets) -- only plain Python objects and the
    database, both of which are safe from a background thread. Progress
    goes through background_jobs.update_progress(); the main script polls
    that on its next rerun rather than this function updating any UI
    directly, which it structurally cannot do from here.
    """
    _, errors = translate_engines.translate_lines_with_engine(
        lines, engine, drama_meta=drama_meta, style_note=style_note,
        novel_reference=novel_reference, force_retranslate=force_retranslate,
        locale=locale, glossary_terms=glossary_terms, style_guidelines=style_guidelines,
        progress_cb=lambda frac: background_jobs.update_progress(
            job_id, frac, f"Translating... {frac*100:.0f}%"),
        save_cb=lambda ls: db.save_lines(drama_id, ls),
        cancel_check_cb=lambda: background_jobs.is_cancel_requested(job_id),
        usage_cb=lambda inp, out: db.log_usage(
            drama_id, engine_choice, getattr(engine, "model", engine_choice), "translate",
            inp, out, translate_engines.estimate_cost(getattr(engine, "model", ""), inp, out)),
    )

    enforced = [t for t in (glossary_terms or []) if t.get("enforce_exact")]
    if enforced:
        for ln in lines:
            if ln.en:
                ln.en = tguide.apply_hard_term_substitutions(ln.en, enforced)
        db.save_lines(drama_id, lines)

    db.save_translation_version(
        drama_id, lines, label=f"{engine_choice} · {style_preset}",
        engine=engine_choice, model=getattr(engine, "model", ""), make_active=True)
    # Persisted, not just handed to the ephemeral job-status dict: if the app
    # restarts or the completion rerun is missed, the record of what failed
    # (and why some lines are untranslated) must not vanish with it.
    db.update_drama(drama_id, status="translated", translation_engine=engine_choice,
                     last_translate_errors=json.dumps(errors, ensure_ascii=False) if errors else None)

    background_jobs.set_result(job_id, {"errors": errors})


def render_workspace_tab():
    st.subheader("1. Choose a drama")
    all_dramas = db.list_dramas()
    options = {"➕ New drama": None}
    options.update({f"#{d['id']} — {d['title_en'] or d['title_zh']}": d["id"] for d in all_dramas})
    default_label = "➕ New drama"
    if st.session_state.active_drama_id:
        for label, did in options.items():
            if did == st.session_state.active_drama_id:
                default_label = label
    picked_label = st.selectbox("Drama", list(options.keys()),
                                 index=list(options.keys()).index(default_label))
    picked_id = options[picked_label]

    if picked_id is None:
        st.markdown("**New drama metadata**")
        with st.expander("🔍 Auto-fill from a public listing page (optional)"):
            st.caption("Paste a link to a public listing/info page. Only bibliographic metadata "
                      "(title, author, cast, synopsis) is extracted -- never the actual chapters/"
                      "episodes. Always review before saving.")
            import known_sites
            with st.popover("📋 Known official platforms"):
                for s in known_sites.KNOWN_SITES:
                    st.markdown(f"**[{s['name']}]({s['url']})** — {', '.join(s['content_types'])}")
            lookup_url = st.text_input("URL")
            lookup_engine = st.selectbox("Engine", [e for e in translate_engines.ENGINES if translate_engines.ENGINES[e].supports_reference], key="lookup_engine")
            lookup_key = st.text_input("API key for lookup", type="password", value=st.session_state.get(f"settings_{lookup_engine}", ""), key="lookup_key")
            if st.button("Fetch & extract metadata") and lookup_url and lookup_key:
                import metadata_lookup
                with st.spinner("Fetching page and extracting metadata..."):
                    engine = translate_engines.get_engine(lookup_engine, lookup_key)
                    found, status = metadata_lookup.lookup_metadata(lookup_url, engine)
                if not status["ok"]:
                    st.warning(status["message"])
                    if status.get("needs_manual"):
                        st.session_state["show_manual_meta_paste"] = True
                if found:
                    st.session_state["autofill_metadata"] = found
                    st.success(f"Found: {', '.join(found.keys())}. Fields below are pre-filled -- review before saving.")
            if st.session_state.get("show_manual_meta_paste"):
                st.caption("**Manual fallback** — open the page in your browser, select all "
                          "(Ctrl+A), copy, and paste here. This always works.")
                pasted_page = st.text_area("Pasted page text", height=140, key="manual_meta_paste")
                if st.button("Extract from pasted text") and pasted_page.strip() and lookup_key:
                    engine_m = translate_engines.get_engine(lookup_engine, lookup_key)
                    found_m, status_m = metadata_lookup.lookup_metadata_from_text(pasted_page, engine_m)
                    if found_m:
                        st.session_state["autofill_metadata"] = found_m
                        st.success(f"Found: {', '.join(found_m.keys())}.")
                        st.rerun()
                    else:
                        st.warning(status_m["message"])

        prefill = st.session_state.get("autofill_metadata", {})
        c1, c2 = st.columns(2)
        title_en = c1.text_input("Title (English)", value=prefill.get("title_en", ""))
        title_zh = c2.text_input("Title (Chinese)", value=prefill.get("title_zh", ""))
        author = c1.text_input("Author", value=prefill.get("author", ""))
        studio = c2.text_input("Studio", value=prefill.get("studio", ""))
        director = c1.text_input("Director", value=prefill.get("director", ""))
        voice_actors = c2.text_input("Voice actors (comma-separated)", value=prefill.get("voice_actors", ""))
        summary = st.text_area("Summary", value=prefill.get("summary", ""), height=100)
        media_type = st.selectbox("Content type",
                                   ["audio_drama", "video_drama", "novel", "manhwa", "manga", "manhua", "asmr", "other"],
                                   format_func=lambda m: m.replace("_", " ").title())
        if st.button("Create drama"):
            new_id = db.create_drama(title_en=title_en, title_zh=title_zh, author=author,
                                      studio=studio, director=director,
                                      voice_actors=voice_actors, summary=summary, media_type=media_type)
            st.session_state.pop("autofill_metadata", None)
            st.session_state.active_drama_id = new_id
            st.session_state.lines = None
            st.rerun()
        st.stop()

    drama = db.get_drama(picked_id)
    st.session_state.active_drama_id = picked_id
    ddir = db.drama_dir(picked_id)

    # Defined here, once, right where `drama` first becomes available --
    # not down in "Content source" where it used to live. Two separate
    # sections above that point (Romanize credits, Raw novel upload) both
    # need it, and each read it before that later definition ran.
    source_language = drama.get("source_language") or "zh"

    with st.expander("✏️ Edit metadata", expanded=False):
        c1, c2 = st.columns(2)
        title_en = c1.text_input("Title (English)", value=drama["title_en"] or "")
        title_zh = c2.text_input("Title (Chinese)", value=drama["title_zh"] or "")
        author = c1.text_input("Author", value=drama["author"] or "")
        studio = c2.text_input("Studio", value=drama["studio"] or "")
        director = c1.text_input("Director", value=drama["director"] or "")
        voice_actors = c2.text_input("Voice actors (comma-separated)", value=drama["voice_actors"] or "")
        summary = st.text_area("Summary", value=drama["summary"] or "", height=100)
        media_type_options = ["audio_drama", "video_drama", "novel", "manhwa", "manga", "manhua", "asmr", "other"]
        media_type = c1.selectbox("Content type", media_type_options,
                                   index=media_type_options.index(drama.get("media_type") or "audio_drama"),
                                   format_func=lambda m: m.replace("_", " ").title())
        genre = c2.text_input("Genre", value=drama.get("genre") or "",
                               placeholder="historical, modern, fantasy...")
        status_opts = ["unknown", "ongoing", "completed", "hiatus"]
        pub_status = c1.selectbox("Publication status", status_opts,
                                   index=status_opts.index(drama.get("publication_status") or "unknown"))
        chapter_count = c2.number_input("Chapter/episode count", value=int(drama.get("chapter_count") or 0),
                                         min_value=0, step=1)
        custom_tags = st.text_input("Custom tags (comma-separated)", value=drama.get("custom_tags") or "",
                                     placeholder="favorite, slow burn, rec to friends")
        personal_notes = st.text_area("Personal notes (private, yours only)",
                                       value=drama.get("personal_notes") or "", height=80)
        if st.button("🔤 Romanize credits", key=f"roman_{picked_id}"):
            _rk = st.session_state.get(f"settings_{drama.get('translation_engine') or 'claude'}", "")
            if not _rk:
                st.warning("Set an API key in the ⚙️ Settings sidebar first.")
            else:
                _eng_r = translate_engines.get_engine(
                    drama.get("translation_engine") or "claude", _rk)
                with st.spinner("Romanizing credits..."):
                    _rom = tguide.romanize_metadata(
                        {"author": author, "studio": studio, "director": director,
                         "voice_actors": voice_actors},
                        _eng_r, source_language=source_language)
                if _rom:
                    db.update_drama(
                        picked_id,
                        author_romanized=_rom.get("author"),
                        studio_romanized=_rom.get("studio"),
                        director_romanized=_rom.get("director"),
                        voice_actors_romanized=_rom.get("voice_actors"))
                    st.success("Credits romanized — originals kept alongside.")
                    st.rerun()
                else:
                    st.warning("Couldn't romanize those credits.")

        for _f, _label in [("author", "Author"), ("studio", "Studio"),
                            ("director", "Director"), ("voice_actors", "Cast")]:
            _rv = drama.get(f"{_f}_romanized")
            if _rv:
                st.caption(f"**{_label}:** "
                          + tguide.format_bilingual_credit(drama.get(_f), _rv))

        cover_file = st.file_uploader("Cover art", type=["png", "jpg", "jpeg", "webp"], key=f"cover_{picked_id}")
        if drama.get("cover_art_filename"):
            _cp = os.path.join(ddir, drama["cover_art_filename"])
            if os.path.exists(_cp):
                st.image(_cp, width=140, caption="Current cover")
        if st.button("Save metadata"):
            if cover_file is not None:
                _ext = os.path.splitext(cover_file.name)[1]
                with open(os.path.join(ddir, f"cover{_ext}"), "wb") as _f:
                    _f.write(cover_file.getbuffer())
                db.update_drama(picked_id, cover_art_filename=f"cover{_ext}")
            db.update_drama(picked_id, title_en=title_en, title_zh=title_zh, author=author,
                             studio=studio, director=director, voice_actors=voice_actors,
                             summary=summary, media_type=media_type, genre=genre,
                             publication_status=pub_status,
                             chapter_count=int(chapter_count) if chapter_count else None,
                             custom_tags=custom_tags, personal_notes=personal_notes)
            st.success("Saved.")
            st.rerun()
        if st.button("🗑️ Delete this drama", type="secondary"):
            db.delete_drama(picked_id)
            st.session_state.active_drama_id = None
            st.session_state.lines = None
            st.rerun()

    st.divider()
    with st.expander("2. 📥 Content source", expanded=False):

        source_language = st.selectbox(
            "Source language",
            ["zh", "ja", "ko"],
            index=["zh", "ja", "ko"].index(source_language),
            format_func=lambda l: {"zh": "🇨🇳 Chinese", "ja": "🇯🇵 Japanese", "ko": "🇰🇷 Korean"}[l],
        )
        if source_language != drama.get("source_language"):
            db.update_drama(picked_id, source_language=source_language)

        with st.expander(f"📕 Raw {source_language.upper()} novel (optional -- helps transcription)"):
            st.caption(
                "Different from the reference translation below: this is the ORIGINAL-language "
                "novel, used as context for speech recognition, not for translation. Whisper "
                "primes on a short excerpt plus your glossary's names, which meaningfully helps it "
                "guess the right proper nouns and phrasing instead of the nearest-sounding word."
            )
            raw_novel_file = st.file_uploader(
                f"Upload the raw {source_language.upper()} novel (.txt/.md/.epub)",
                type=["txt", "md", "epub"], key=f"raw_novel_{picked_id}")
            _existing_raw_path = os.path.join(ddir, "raw_novel_context.txt")
            _has_existing_raw = os.path.exists(_existing_raw_path)
            if raw_novel_file is not None:
                try:
                    _raw_text = core_module.load_novel_text_for_context(
                        raw_novel_file.getvalue(), raw_novel_file.name)
                    with open(_existing_raw_path, "w", encoding="utf-8") as f:
                        f.write(_raw_text)
                    st.success(f"Loaded {len(_raw_text):,} characters.")
                    _has_existing_raw = True
                except ImportError as e:
                    st.error(str(e))
            if _has_existing_raw:
                st.caption(f"✅ Raw novel context saved (~{os.path.getsize(_existing_raw_path):,} bytes).")
                if st.button("🗑️ Remove raw novel context", key=f"rmraw_{picked_id}"):
                    os.remove(_existing_raw_path)
                    st.rerun()

        content_mode = st.radio(
            "What are you working from?",
            ["audio_drama", "novel_narration"],
            index=0 if (drama.get("content_mode") or "audio_drama") == "audio_drama" else 1,
            format_func=lambda m: "🎧 Audio drama (I have the audio, + transcript)"
                         if m == "audio_drama" else
                         "📖 Novel only (no audio -- generate a full AI narration)",
            horizontal=False,
        )
        if content_mode != drama.get("content_mode"):
            db.update_drama(picked_id, content_mode=content_mode)

        audio_file = None
        transcript_text = ""
        novel_narration_text = ""
        existing_audio = None

        if content_mode == "audio_drama":
            if drama["audio_filename"]:
                p = os.path.join(ddir, drama["audio_filename"])
                if os.path.exists(p):
                    existing_audio = p

            # A segmented choice instead of "uploader always visible, download
            # option buried in a collapsed expander below it" -- the expander
            # version was easy to scroll past entirely, which is exactly why
            # people couldn't find the download option.
            import_method = st.radio(
                "Add audio/video" + (" — already added" if existing_audio else ""),
                ["upload", "url"],
                format_func=lambda m: "📁 Upload a file" if m == "upload"
                                       else "🔗 Download from a URL (yt-dlp)",
                horizontal=True, key=f"import_method_{picked_id}")

            if import_method == "upload":
                audio_file = st.file_uploader(
                    "Audio or video file *(required)*",
                    type=["mp3", "wav", "m4a", "flac", "ogg", "mp4", "mkv", "mov", "webm"],
                    key=f"audio_up_{picked_id}")
            else:
                st.caption(
                    "Fetches audio/video directly via yt-dlp (YouTube and many other sites) -- "
                    "no need to run it on the command line and upload the result yourself. Only "
                    "use this for content you actually have the right to use. Needs "
                    "`pip install yt-dlp`."
                )
                dl_url = st.text_input("Video URL", key=f"dl_url_{picked_id}")
                dl_audio_only = st.checkbox(
                    "Audio only (recommended -- smaller, and this is all the pipeline needs "
                    "unless you also want the video for hardsub/dub export later)",
                    value=True, key=f"dl_audio_only_{picked_id}")
                if st.button("⬇️ Download", disabled=not dl_url.strip()):
                    progress_bar = st.progress(0.0)
                    status = st.empty()
                    try:
                        import video_download
                        downloaded_path = video_download.download(
                            dl_url.strip(), ddir, audio_only=dl_audio_only,
                            progress_cb=lambda frac, msg: (progress_bar.progress(frac), status.caption(msg)))
                        if dl_audio_only:
                            db.update_drama(picked_id, audio_filename=os.path.basename(downloaded_path))
                        else:
                            audio_out = os.path.join(ddir, "audio.wav")
                            with st.spinner("Extracting audio from downloaded video..."):
                                core_module.extract_audio_from_video(downloaded_path, audio_out)
                            db.update_drama(picked_id, audio_filename="audio.wav",
                                             source_video_filename=os.path.basename(downloaded_path))
                        st.success("Downloaded.")
                        st.rerun()
                    except ImportError as exc:
                        st.error(str(exc))
                    except video_download.DownloadError as exc:
                        st.error(str(exc))

            st.markdown("**Transcript**")
            transcript_mode = st.radio(
                "Where does the transcript come from?",
                ["have_transcript", "whisper"],
                format_func=lambda m: ("I have the transcript (most accurate)"
                                        if m == "have_transcript" else
                                        "I don't have one -- let Whisper transcribe the audio"),
                key=f"tmode_{picked_id}", horizontal=False)

            if transcript_mode == "have_transcript":
                st.caption("Paste the official or fan transcript. Using a real transcript is "
                          "meaningfully better than speech recognition -- Whisper misreads names "
                          "and uncommon terms, and those errors carry straight into the translation.")
                transcript_text = st.text_area("Transcript *(required)*", height=180,
                                                key=f"transcript_{picked_id}")
            else:
                st.caption("Whisper will produce the transcript from the audio itself. Expect errors "
                          "on names, sect terms, and anything homophone-heavy -- you can correct them "
                          "in the review table before translating. Larger model = fewer mistakes.")
                transcript_text = ""
        else:
            st.caption(
                "Paste the novel text (Chinese). It'll be chunked into narration lines, "
                "speaker-tagged automatically (dialogue vs. narrator), translated, and "
                "synthesized into a full AI narration track -- no source audio needed."
            )
            with st.expander("📷 Or extract text from chapter/page scan images (OCR)"):
                st.caption(
                    "For chapters served as images instead of selectable text. "
                    "Backend options depend on source language -- see README for install steps."
                )
                ocr_images = st.file_uploader("Upload page images (in reading order)",
                                               type=["png", "jpg", "jpeg"], accept_multiple_files=True)
                ocr_backend_options = ["tesseract", "paddle"] if source_language == "zh" else (
                    ["manga_ocr", "tesseract"] if source_language == "ja" else ["tesseract"])
                ocr_backend = st.radio("OCR backend", ocr_backend_options, horizontal=True,
                                        help="manga_ocr: best for JP speech-bubble crops. "
                                             "paddle: higher accuracy for Chinese, heavier install.")
                if ocr_images and st.button("Extract text from images"):
                    import ocr as ocr_module
                    img_paths = []
                    for img in ocr_images:
                        p = os.path.join(ddir, f"ocr_{img.name}")
                        with open(p, "wb") as f:
                            f.write(img.getbuffer())
                        img_paths.append(p)
                    with st.spinner("Running OCR..."):
                        extracted = ocr_module.extract_text_from_images(
                            img_paths, backend=ocr_backend, source_language=source_language)
                    st.session_state[f"ocr_text_{picked_id}"] = extracted
                    st.success(f"Extracted {len(extracted):,} characters. Review below before using.")

            ocr_default = st.session_state.get(f"ocr_text_{picked_id}", "")

            with st.expander("📚 Or import from an EPUB you own"):
                st.caption("Requires `pip install ebooklib beautifulsoup4`.")
                epub_file = st.file_uploader("Upload .epub", type=["epub"], key="epub_upload")
                if epub_file:
                    epub_path = os.path.join(ddir, "source.epub")
                    with open(epub_path, "wb") as f:
                        f.write(epub_file.getbuffer())
                    import epub_io
                    try:
                        n_chapters = epub_io.get_epub_chapter_count(epub_path)
                        ec1, ec2 = st.columns(2)
                        ch_start = ec1.number_input("From chapter", value=1, min_value=1, max_value=n_chapters)
                        ch_end = ec2.number_input("To chapter", value=min(5, n_chapters), min_value=1, max_value=n_chapters)
                        if st.button("Import chapters from EPUB"):
                            with st.spinner("Extracting text..."):
                                extracted = epub_io.import_epub_text(epub_path, chapter_range=(ch_start - 1, ch_end))
                            st.session_state[f"ocr_text_{picked_id}"] = extracted
                            st.success(f"Imported {len(extracted):,} characters from chapters {ch_start}-{ch_end}.")
                            st.rerun()
                    except Exception as e:
                        st.error(f"Couldn't read that EPUB: {e}")

            novel_narration_text = st.text_area("Novel text *(required)*", value=ocr_default, height=220)

    with st.expander("3. 📚 Reference novel (optional) & recognition settings", expanded=False):
        st.caption("If this drama already has an official/fan English translation elsewhere, "
                   "paste it here to keep terminology consistent -- separate from the novel "
                   "narration text above, which is what actually gets read aloud.")
        existing_novel_text = None
        novel_path = os.path.join(ddir, drama["novel_reference_filename"]) if drama["novel_reference_filename"] else None
        if novel_path and os.path.exists(novel_path):
            with open(novel_path, "r", encoding="utf-8") as f:
                existing_novel_text = f.read()
            st.caption(f"Novel reference already saved (~{len(existing_novel_text):,} chars).")
        novel_file = st.file_uploader("Upload novel translation (.txt/.md)", type=["txt", "md"], key="novel_up")
        novel_pasted = st.text_area("...or paste it here", height=100, key="novel_paste")

        # ---- Build a glossary from the novel ----------------------------------
        with st.expander("📕 Build a glossary from this novel"):
            st.caption(
                "The novel is usually the better source for terminology than the drama's "
                "dialogue -- it's longer and introduces more names, sects and places. Terms are "
                "sampled from across the whole text, not just the opening chapters, so later "
                "introductions aren't missed. Only terminology is extracted; no passages are stored."
            )
            _gl_series = drama.get("series_id")
            if not _gl_series:
                st.info("Assign this drama to a series first (under Translation below) -- "
                       "glossaries are shared across a series so every book and season stays "
                       "consistent.")
            else:
                _novel_src = (existing_novel_text or "")
                if novel_file is not None:
                    _novel_src = novel_file.getvalue().decode("utf-8", errors="ignore")
                elif novel_pasted.strip():
                    _novel_src = novel_pasted

                # Reuses the raw novel uploaded above (Content source -> "Raw novel") rather
                # than asking for it a second time -- one upload now feeds both transcription
                # priming and this pairing, instead of needing the same file twice.
                _raw_context_path = os.path.join(ddir, "raw_novel_context.txt")
                if os.path.exists(_raw_context_path):
                    with open(_raw_context_path, "r", encoding="utf-8") as f:
                        _orig_src = f.read()
                    st.caption(f"✅ Using the raw {source_language.upper()} novel uploaded above "
                              f"(~{len(_orig_src):,} chars) as the paired original-language source. "
                              f"With both, terms are extracted as matched pairs -- capturing how "
                              f"each was actually rendered rather than inventing new wording.")
                else:
                    st.caption(f"Optionally provide the ORIGINAL {source_language.upper()} novel too. "
                              f"With both, terms are extracted as matched pairs -- capturing how "
                              f"each was actually rendered rather than inventing new wording. "
                              f"(Uploading it here also saves it for transcription priming above.)")
                    orig_novel_file = st.file_uploader(
                        f"Original {source_language.upper()} novel", type=["txt", "md", "epub"],
                        key="orig_novel_up")
                    _orig_src = ""
                    if orig_novel_file is not None:
                        try:
                            _orig_src = core_module.load_novel_text_for_context(
                                orig_novel_file.getvalue(), orig_novel_file.name)
                            with open(_raw_context_path, "w", encoding="utf-8") as f:
                                f.write(_orig_src)
                            st.success(f"Saved -- also now feeding transcription priming above.")
                        except ImportError as e:
                            st.error(str(e))

                gl_key = st.session_state.get(f"settings_{drama.get('translation_engine') or 'claude'}", "")
                if st.button("📖 Extract glossary from novel"):
                    if not (_novel_src.strip() or _orig_src.strip()):
                        st.warning("Upload or paste a novel first.")
                    elif not gl_key:
                        st.warning("Set an API key in the ⚙️ Settings sidebar first.")
                    else:
                        eng_gl = translate_engines.get_engine(
                            drama.get("translation_engine") or "claude", gl_key)
                        # If both are supplied the original is the source and the
                        # existing translation shows the established rendering.
                        src_text = _orig_src if _orig_src.strip() else _novel_src
                        en_text = _novel_src if _orig_src.strip() else ""
                        bar = st.progress(0.0, text="Reading the novel...")
                        try:
                            proposed_gl = tguide.extract_glossary_from_novel(
                                src_text, eng_gl, source_language=source_language,
                                english_translation=en_text,
                                known_terms=db.list_glossary_terms(_gl_series),
                                progress_cb=lambda f: bar.progress(f, text=f"Reading... {f*100:.0f}%"))
                            bar.empty()
                            st.session_state[f"novel_glossary_{picked_id}"] = proposed_gl
                            st.success(f"Proposed {len(proposed_gl)} term(s). Review below.")
                        except Exception as e:
                            bar.empty()
                            st.error(f"Extraction failed: {e}")

                _proposed_gl = st.session_state.get(f"novel_glossary_{picked_id}", [])
                if _proposed_gl:
                    st.caption(f"Review {len(_proposed_gl)} proposed term(s) -- uncheck anything wrong:")
                    gl_df = pd.DataFrame(_proposed_gl)
                    gl_df.insert(0, "Add", True)
                    edited_gl = st.data_editor(gl_df, width='stretch', hide_index=True,
                                                key=f"gl_editor_{picked_id}")
                    if st.button("➕ Add these terms to the series glossary"):
                        n_added = 0
                        for row in edited_gl[edited_gl["Add"]].to_dict("records"):
                            db.upsert_glossary_term(
                                _gl_series, row.get("term", ""), row.get("suggested_translation", ""),
                                notes=row.get("reason", ""), category=row.get("category"),
                                policy=row.get("policy"))
                            n_added += 1
                        st.session_state[f"novel_glossary_{picked_id}"] = []
                        st.success(f"Added {n_added} term(s) to the glossary.")
                        st.rerun()

                st.divider()
                st.caption("**Or import a glossary file you already have** (CSV, TSV, or JSON). "
                          "A plain two-column term/translation sheet works.")
                gl_file = st.file_uploader("Glossary file", type=["csv", "tsv", "json"], key="gl_file_up")
                if gl_file is not None and st.button("📥 Import glossary file"):
                    raw = gl_file.getvalue().decode("utf-8", errors="ignore")
                    imported, warns = tguide.parse_glossary_file(raw, gl_file.name)
                    for w in warns[:10]:
                        st.caption(f"⚠️ {w}")
                    if imported:
                        for t in imported:
                            db.upsert_glossary_term(
                                _gl_series, t["term_original"], t["term_translation"],
                                notes=t["notes"], category=t["category"],
                                policy=t["policy"], enforce_exact=t["enforce_exact"])
                        st.success(f"Imported {len(imported)} term(s).")
                        st.rerun()
                    else:
                        st.error("Nothing could be imported from that file.")

                _current_gl = db.list_glossary_terms(_gl_series)
                if _current_gl:
                    st.download_button(
                        f"📤 Export glossary ({len(_current_gl)} terms) as CSV",
                        tguide.glossary_to_csv(_current_gl),
                        file_name="glossary.csv")

        whisper_size = st.selectbox(
            "Speech recognition model", ["small", "medium", "large-v3"], index=1,
            disabled=content_mode == "novel_narration",
            help="large-v3 is markedly better on Chinese names and homophones. It's free, "
                 "just slower and ~3GB to download — and much faster with GPU enabled.")

        with st.expander("🎯 Recognition accuracy (free — worth doing)"):
            st.caption(
                "Whisper mishears proper nouns constantly in Chinese, because a wrong guess is "
                "usually still a real word — nothing looks broken until you read the translation. "
                "Priming it with the names it should expect fixes a lot of that at no cost."
            )
            _gl_terms = (db.list_glossary_terms(drama["series_id"])
                         if drama.get("series_id") else [])
            _auto_prompt = core_module.build_initial_prompt(_gl_terms)
            if _auto_prompt:
                st.caption(f"From this series' glossary ({len(_gl_terms)} terms): `{_auto_prompt[:120]}`")
            else:
                st.caption("No glossary terms yet. Build one from the novel above, or type names "
                          "below — either feeds recognition.")
            _manual_prompt = st.text_input(
                "Extra names to expect (、 or comma separated)",
                key=f"initprompt_{picked_id}",
                placeholder="沈清疑、云隐宗、神机营")
            _name_prompt = "、".join(x for x in [_auto_prompt.rstrip("。"),
                                                  _manual_prompt.strip()] if x)
            if _name_prompt:
                _name_prompt += "。"

            _raw_novel_path = os.path.join(ddir, "raw_novel_context.txt")
            if os.path.exists(_raw_novel_path):
                with open(_raw_novel_path, "r", encoding="utf-8") as f:
                    _novel_excerpt = core_module.extract_novel_excerpt_for_prompt(f.read())
                initial_prompt = core_module.combine_initial_prompt(_name_prompt, _novel_excerpt)
                st.caption(f"Including an excerpt from the raw novel you uploaded above "
                          f"({len(_novel_excerpt):,} of its characters, names take priority).")
            else:
                initial_prompt = _name_prompt

            beam_size = st.slider("Search width (beam size)", 1, 10, 5,
                                   help="Higher considers more alternatives before committing. "
                                        "8-10 helps on difficult audio; it costs time, not money.")

            min_silence_ms = st.slider(
                "Speech-splitting sensitivity (ms of silence to start a new line)", 300, 3000, 2000, 100,
                help="The default (2000ms) merges any two stretches of speech separated by less "
                     "than 2 seconds of silence into ONE segment -- with only the first sentence "
                     "kept as that line's text. For back-to-back dialogue, internal-monologue "
                     "narration, or quick exchanges, this routinely swallows several real lines "
                     "into one oversized block. Lower it (500-1000ms) if lines feel too long or "
                     "thoughts/dialogue seem to go missing. Too low starts splitting mid-sentence "
                     "on normal speech pauses -- there's no universally correct value.")
            if min_silence_ms < 2000:
                st.caption(f"Set to {min_silence_ms}ms -- more, shorter lines than the default; "
                          f"re-run 'Check line coverage' below after aligning to see the effect.")

            if not st.session_state.get("use_gpu"):
                st.caption("💡 GPU is off. On your card, enabling it under Settings → Performance "
                          "makes large-v3 practical rather than painfully slow.")

        alignment_method = st.selectbox(
            "Timing method (when you have a real transcript)",
            ["whisper_diff", "qwen3_forced_align"],
            format_func=lambda m: ("Whisper + character-diff (current default)"
                                    if m == "whisper_diff" else
                                    "Qwen3-ForcedAligner (experimental -- true forced alignment)"),
            disabled=content_mode == "novel_narration",
            help="The default runs Whisper for timing, then fuzzy-matches your real transcript "
                 "against Whisper's (often wrong) text character-by-character, guessing each "
                 "match's timestamp as an even split across its Whisper segment. Qwen3-ForcedAligner "
                 "instead aligns your ACTUAL transcript text directly against the audio -- no "
                 "guessing, no fuzzy-matching against ASR errors -- but needs `pip install "
                 "qwen-asr torch` and is unverified on this project's content. Only applies when "
                 "you supplied a real transcript above; Whisper's own text has nothing to align "
                 "against. Falls back to the default automatically if qwen-asr isn't installed.")

        asr_backend_choice = st.selectbox(
            "Transcription model (when Whisper is doing the transcript, not just timing)",
            ["whisper", "qwen3_asr"],
            format_func=lambda m: ("Whisper (current default)" if m == "whisper" else
                                    "Qwen3-ASR (experimental -- purpose-built for zh/ja/ko)"),
            disabled=content_mode == "novel_narration",
            help="Only applies when you picked 'let Whisper transcribe the audio' above -- if you "
                 "supplied a real transcript, this has no effect (nothing to transcribe). Public "
                 "benchmarks show Qwen3-ASR well ahead of Whisper on Mandarin, especially under "
                 "noise; no direct Japanese comparison was found, so this is unverified on that "
                 "language specifically. Needs `pip install qwen-asr torch`, re-transcribes each "
                 "of Whisper's segments individually (so it's slower than one Whisper pass), and "
                 "keeps Whisper's own segment timing either way -- only the transcribed text "
                 "changes. Falls back to Whisper automatically if qwen-asr isn't installed.")

    with st.expander("4. 🎙️ Speaker diarization", expanded=False):
        if content_mode == "audio_drama":
            st.caption(
                "Distinguishes different voices/characters in the audio, so lines can be grouped "
                "by character and dubbed with different voices (or cloned). Needs `pyannote.audio` "
                "installed and a free Hugging Face token -- see README."
            )
            hf_token = st.text_input("Hugging Face token (for diarization)", type="password",
                                      value=st.session_state.get("settings_hf_token", ""))
            run_diarize = st.checkbox("Run speaker diarization during alignment", value=False,
                                       disabled=not hf_token)
        else:
            st.caption("For novel narration, speaker attribution is done by the translation LLM "
                       "(who's speaking each line) instead of audio diarization -- no audio to analyze.")
            hf_token, run_diarize = None, False

    st.subheader("5. Translation")

    if drama.get("last_translate_errors"):
        try:
            _persisted_errors = json.loads(drama["last_translate_errors"])
        except (json.JSONDecodeError, TypeError):
            _persisted_errors = []
        if _persisted_errors:
            _failed_nums = sorted({i + 1 for e in _persisted_errors for i in e.get("lines", [])})
            st.warning(
                f"⚠️ The last translation run had {len(_persisted_errors)} batch failure(s) -- "
                f"line(s) {_failed_nums} are still untranslated. This is why some lines have "
                f"no English text; it's not a display bug. Click **Translate all lines** below "
                f"to retry just the missing ones (already-translated lines are skipped "
                f"automatically, so this won't re-cost anything already done).")
            if st.button("Dismiss this notice", key=f"dismiss_tr_err_{picked_id}"):
                db.update_drama(picked_id, last_translate_errors=None)
                st.rerun()

    import translation_guide as tguide
    _style_keys = list(tguide.STYLE_PRESETS.keys())
    _default_style = "novel" if content_mode == "novel_narration" else "audio_drama"
    style_preset = st.selectbox(
        "Translation style", _style_keys, index=_style_keys.index(_default_style),
        format_func=lambda k: tguide.STYLE_PRESETS[k]["label"],
        help="Changes register and pacing guidance -- spoken dialogue reads very "
             "differently from prose or bubble text.")
    with st.expander("ℹ️ What this style asks the translator for"):
        st.caption(tguide.STYLE_PRESETS[style_preset]["guidance"])
    include_genre_notes = st.checkbox(
        "Include baihe/GL genre guidance (pronoun clarity, kinship-term nuance, "
        "don't soften romantic content)", value=True)
    custom_guide_notes = st.text_area(
        "Project-specific translation notes (optional)", height=68,
        placeholder="e.g. this character always speaks formally; keep the narrator distant")

    with st.expander("📖 Series glossary & term handling", expanded=False):
        existing_series = db.list_series()
        series_options = ["-- none --"] + [s["name"] for s in existing_series] + ["+ New series..."]
        current_series_name = next((s["name"] for s in existing_series if s["id"] == drama.get("series_id")), "-- none --")
        series_pick = st.selectbox("Series", series_options,
                                    index=series_options.index(current_series_name) if current_series_name in series_options else 0)
        if series_pick == "+ New series...":
            new_series_name = st.text_input("New series name")
            if new_series_name and st.button("Create & assign series"):
                sid = db.get_or_create_series(new_series_name)
                db.update_drama(picked_id, series_id=sid)
                st.success(f"Assigned to series '{new_series_name}'.")
                st.rerun()
        elif series_pick != "-- none --":
            sid = next(s["id"] for s in existing_series if s["name"] == series_pick)
            if sid != drama.get("series_id"):
                db.update_drama(picked_id, series_id=sid)
                st.rerun()

            st.markdown("**Auto-extract terms from the source text**")
            st.caption("Scans for names, sects, titles, honorifics, and concepts needing "
                      "consistent handling, and proposes a policy for each. Always review "
                      "before adding -- these are judgment calls.")
            _extract_key = st.session_state.get(f"settings_{drama.get('translation_engine') or 'claude'}", "")
            if st.button("🔍 Extract terms"):
                source_lines = [ln.zh for ln in (st.session_state.lines or [])]
                if not source_lines:
                    st.warning("No source lines yet -- align or chunk the text first.")
                elif not _extract_key:
                    st.warning("Set an API key in the ⚙️ Settings sidebar first.")
                else:
                    engine_x = translate_engines.get_engine(
                        drama.get("translation_engine") or "claude", _extract_key)
                    with st.spinner("Scanning for terms..."):
                        proposed = tguide.extract_terms_llm(
                            source_lines, engine_x, source_language=source_language,
                            known_terms=db.list_glossary_terms(sid))
                    st.session_state[f"proposed_terms_{picked_id}"] = proposed
                    st.success(f"Proposed {len(proposed)} term(s) for review.")

            proposed = st.session_state.get(f"proposed_terms_{picked_id}", [])
            if proposed:
                st.caption(f"Review {len(proposed)} proposed term(s):")
                prop_df = pd.DataFrame(proposed)
                prop_df.insert(0, "Add", True)
                edited_prop = st.data_editor(
                    prop_df, width='stretch', hide_index=True,
                    key=f"prop_editor_{picked_id}")
                if st.button("➕ Add selected terms to glossary"):
                    added = 0
                    for row in edited_prop[edited_prop["Add"]].to_dict("records"):
                        db.upsert_glossary_term(
                            sid, row.get("term", ""), row.get("suggested_translation", ""),
                            notes=row.get("reason", ""), category=row.get("category"),
                            policy=row.get("policy"))
                        added += 1
                    st.session_state[f"proposed_terms_{picked_id}"] = []
                    st.success(f"Added {added} term(s).")
                    st.rerun()

            st.markdown("**Current glossary**")
            terms = db.list_glossary_terms(sid)
            if terms:
                for t in terms:
                    pol_label = tguide.TERM_POLICIES.get(t.get("policy") or "keep_pinyin", {}).get("label", "")
                    lock = " 🔒" if t.get("enforce_exact") else ""
                    st.caption(f"**{t['term_original']}** → {t['term_translation']}{lock} "
                              f"_{pol_label}_" + (f" — {t['notes']}" if t.get("notes") else ""))
            else:
                st.caption("No terms yet.")

            with st.form(f"add_glossary_{sid}", clear_on_submit=True):
                gc1, gc2 = st.columns(2)
                g_orig = gc1.text_input("Original term")
                g_trans = gc2.text_input("Translation")
                gc3, gc4 = st.columns(2)
                g_cat = gc3.selectbox("Category", list(tguide.TERM_CATEGORIES.keys()),
                                       format_func=lambda k: tguide.TERM_CATEGORIES[k])
                g_pol = gc4.selectbox("Handling policy", list(tguide.TERM_POLICIES.keys()),
                                       format_func=lambda k: f"{tguide.TERM_POLICIES[k]['label']} "
                                                             f"({tguide.TERM_POLICIES[k]['example']})")
                g_notes = st.text_input("Notes / known wrong variants (pipe-separated)",
                                         help="For enforced terms, list variants to auto-correct, "
                                              "e.g. Shen Qing Yi|Chen Qingyi")
                g_enforce = st.checkbox("🔒 Enforce exactly (hard find-replace after translation)")
                if st.form_submit_button("Add term") and g_orig and g_trans:
                    db.upsert_glossary_term(sid, g_orig, g_trans, g_notes, g_cat, g_pol, g_enforce)
                    st.success("Added.")
                    st.rerun()


    default_engine_list = list(translate_engines.ENGINES.keys())
    saved_engine = drama.get("translation_engine") or st.session_state.get("settings_default_engine", "claude")
    engine_choice = st.selectbox(
        "Translation engine",
        default_engine_list,
        index=default_engine_list.index(saved_engine) if saved_engine in default_engine_list else 0,
        format_func=lambda e: f"{e} — {translate_engines.ENGINE_NOTES[e]}",
    )
    engine_model = None
    if engine_choice == "claude":
        _model_keys = list(translate_engines.CLAUDE_MODELS.keys())
        _saved_model = st.session_state.get(f"settings_claude_model", _model_keys[0])
        engine_model = st.selectbox(
            "Claude model", _model_keys,
            index=_model_keys.index(_saved_model) if _saved_model in _model_keys else 0,
            format_func=lambda m: translate_engines.CLAUDE_MODELS[m],
            help="Anthropic updates this lineup periodically. If a model here starts "
                 "erroring, check console.anthropic.com for what's currently available.")
        st.session_state["settings_claude_model"] = engine_model

    _needs_key = engine_choice not in ("test_offline", "ollama", "libretranslate")
    if engine_choice == "test_offline":
        st.success("Dry-run mode: no API key, no network, no cost. Produces obvious [TEST] "
                  "placeholder text so you can confirm the pipeline works end to end before "
                  "spending anything.")
        api_key = "offline"
    else:
        api_key = st.text_input(
            f"{engine_choice} API key" + (" *(required)*" if _needs_key else " (optional)"),
            type="password",
            value=st.session_state.get(f"settings_{engine_choice}", ""),
            help="Claude keys come from console.anthropic.com and are billed separately "
                 "from any Claude.ai subscription.")
        if _needs_key and not api_key:
            st.caption("⚠️ Required — set it here or in the ⚙️ Settings sidebar. "
                      "To try the pipeline for free first, choose `test_offline` above.")
        elif not _needs_key:
            api_key = api_key or "local"
    style_note = st.text_input("Optional style notes",
                                value=st.session_state.get("settings_default_style_note", ""))
    locale_options = ["en-US", "en-GB", "en-AU"]
    default_locale = st.session_state.get("settings_default_locale", "en-US")
    locale = st.selectbox("English variant", locale_options,
                           index=locale_options.index(default_locale) if default_locale in locale_options else 0,
                           format_func=lambda l: {"en-US": "American English", "en-GB": "British English",
                                                   "en-AU": "Australian English"}[l])

    b1, b2 = st.columns(2)
    if content_mode == "audio_drama":
        _has_audio = bool(audio_file or existing_audio)
        _whisper_mode = st.session_state.get(f"tmode_{picked_id}") == "whisper"
        _has_transcript = bool(transcript_text.strip()) or _whisper_mode
        can_prep = _has_audio and _has_transcript
        prep_label = "▶ Transcribe & Align" if not _whisper_mode else "▶ Transcribe with Whisper"

        # A disabled button with no explanation is a dead end -- say what's missing.
        if not can_prep:
            _missing = []
            if not _has_audio:
                _missing.append("an audio or video file")
            if not _has_transcript:
                _missing.append("a transcript (paste one, or switch to Whisper above)")
            st.info("Still needed before this can run: " + " and ".join(_missing) + ".")
    else:
        can_prep = bool(novel_narration_text.strip())
        prep_label = "▶ Chunk & Tag Speakers"
    if content_mode == "audio_drama" and can_prep:
        if not core_module.is_whisper_model_cached(whisper_size):
            st.caption(f"ℹ️ The '{whisper_size}' model isn't downloaded yet — first run will "
                      f"fetch it from Hugging Face (a few hundred MB to ~3GB). Needs a working "
                      f"internet connection; it's cached afterwards.")

    run_prep = b1.button(prep_label, type="primary", disabled=not can_prep)
    run_translate = b2.button("🌐 Translate all lines",
                               disabled=st.session_state.lines is None or not api_key)
    force_retranslate = b2.checkbox("Force re-translate everything (ignore already-translated lines)",
                                     value=False, key="force_retranslate")

    if run_prep and content_mode == "audio_drama":
        audio_path = existing_audio
        if audio_file is not None:
            ext = os.path.splitext(audio_file.name)[1]
            saved_path = os.path.join(ddir, f"source{ext}")
            with open(saved_path, "wb") as f:
                f.write(audio_file.getbuffer())
            if ext.lower() in (".mp4", ".mkv", ".mov", ".webm"):
                audio_path = os.path.join(ddir, "audio.wav")
                with st.spinner("Extracting audio from video..."):
                    extract_audio_from_video(saved_path, audio_path)
                db.update_drama(picked_id, audio_filename="audio.wav",
                                 source_video_filename=f"source{ext}")
            else:
                audio_path = saved_path
                db.update_drama(picked_id, audio_filename=f"source{ext}")

        novel_reference = existing_novel_text
        if novel_file is not None:
            novel_reference = novel_file.read().decode("utf-8", errors="ignore")
        elif novel_pasted.strip():
            novel_reference = novel_pasted
        if novel_reference:
            with open(os.path.join(ddir, "novel_reference.txt"), "w", encoding="utf-8") as f:
                f.write(novel_reference)
            db.update_drama(picked_id, novel_reference_filename="novel_reference.txt")

        with open(os.path.join(ddir, "transcript.txt"), "w", encoding="utf-8") as f:
            f.write(transcript_text)

        _use_whisper_text = st.session_state.get(f"tmode_{picked_id}") == "whisper"
        _local_model = st.session_state.get("settings_whisper_model_path", "").strip() or None
        _gpu_fallback_msg = []
        try:
            with st.spinner("Running speech recognition..."):
                segments = transcribe_for_timing(
                    audio_path, whisper_size, language=source_language,
                    use_gpu=st.session_state.get("use_gpu", False),
                    local_model_path=_local_model,
                    hf_token=st.session_state.get("settings_hf_token", "") or None,
                    initial_prompt=initial_prompt, beam_size=beam_size,
                    min_silence_duration_ms=min_silence_ms,
                    on_gpu_fallback=lambda exc: _gpu_fallback_msg.append(str(exc)))
            if _gpu_fallback_msg:
                st.warning(
                    "GPU was requested but failed at the actual transcription step, so this "
                    "ran on CPU instead (slower, but it completed). This is a CUDA/driver "
                    "problem on this machine, not something wrong with your audio.\n\n"
                    f"Error: {_gpu_fallback_msg[0]}\n\n"
                    "Common cause: PyTorch/ctranslate2 installed without CUDA support, or a "
                    "CUDA toolkit version that doesn't match your driver. Turn GPU off in "
                    "Settings → Performance if you'd rather not see this each time, or "
                    "reinstall the CUDA-enabled build matching your driver version.")
        except core_module.ModelDownloadError as exc:
            st.error("Speech recognition model couldn't be downloaded.")
            st.code(str(exc), language="text")
            st.caption("Nothing was lost -- your audio, transcript and settings are saved. "
                      "Fix the connection and press the button again.")
            st.stop()

        if not segments:
            st.error("Speech recognition returned nothing. Check the file actually contains "
                     "audio, and that ffmpeg is installed (see the Diagnostics tab).")
            st.stop()

        if _use_whisper_text:
            # No supplied transcript: use a transcription model's own text.
            # Segment TIMING always comes from Whisper's VAD (segments, above)
            # -- asr_backend_choice only affects which model's TEXT fills
            # those segments. See asr_backend.py's module docstring for why
            # that split is deliberate.
            if asr_backend_choice == "qwen3_asr":
                try:
                    import asr_backend
                    segments = asr_backend.Qwen3ASRBackend().transcribe(
                        audio_path, source_language, whisper_segments=segments,
                        use_gpu=st.session_state.get("use_gpu", False))
                except ImportError:
                    st.warning("Qwen3-ASR needs `pip install qwen-asr torch` -- using Whisper's "
                              "own transcription for this run.")
                except core_module.ModelDownloadError as exc:
                    st.warning(f"Qwen3-ASR couldn't be downloaded ({exc}) -- using Whisper's "
                              "own transcription for this run.")
            lines = [Line(idx=i, start=seg["start"], end=seg["end"], zh=seg["text"])
                     for i, seg in enumerate(segments) if seg["text"].strip()]
            transcript_text = "\n".join(ln.zh for ln in lines)
            st.warning("This transcript came from speech recognition, so expect errors on "
                      "names and uncommon terms. Correct them in the review table below "
                      "**before** translating -- mistakes here carry into the translation.")
        else:
            with st.spinner("Aligning transcript to timing..."):
                user_lines = split_user_transcript(transcript_text)
                if alignment_method == "qwen3_forced_align":
                    try:
                        import forced_align
                        lines = forced_align.align_with_qwen3(
                            audio_path, user_lines, segments, language=source_language,
                            use_gpu=st.session_state.get("use_gpu", False))
                    except ImportError:
                        st.warning("Qwen3 forced alignment needs `pip install qwen-asr torch` -- "
                                  "using the default character-alignment method for this run.")
                        lines = align_transcript_to_timing(user_lines, segments)
                    except core_module.ModelDownloadError as exc:
                        st.warning(f"Qwen3-ForcedAligner couldn't be downloaded ({exc}) -- "
                                  "using the default character-alignment method for this run.")
                        lines = align_transcript_to_timing(user_lines, segments)
                    except ValueError as exc:
                        st.warning(f"Qwen3 forced alignment couldn't run ({exc}) -- using the "
                                  "default character-alignment method for this run.")
                        lines = align_transcript_to_timing(user_lines, segments)
                else:
                    lines = align_transcript_to_timing(user_lines, segments)

        speaker_segments = None
        if run_diarize and hf_token:
            with st.spinner("Running speaker diarization... (first run downloads the model)"):
                try:
                    import diarize
                    speaker_segments = diarize.diarize(audio_path, hf_token)
                    diarize.label_lines_with_speakers(lines, speaker_segments)
                    for label in sorted({ln.speaker for ln in lines if ln.speaker}):
                        db.upsert_character(picked_id, label)
                    st.session_state[f"speaker_segments_{picked_id}"] = speaker_segments
                    st.success(f"Diarization found {len(set(ln.speaker for ln in lines if ln.speaker))} speaker(s).")
                except Exception as e:
                    st.warning(f"Diarization failed ({e}) -- alignment still saved without speaker "
                              f"labels. Check your Hugging Face token and pyannote.audio install, "
                              f"then re-run just diarization if you want it.")

        st.session_state.lines = lines
        db.save_lines(picked_id, lines)
        db.update_drama(picked_id, status="aligned")
        st.success(f"Aligned {len(lines)} lines.")

    elif run_prep and content_mode == "novel_narration":
        with open(os.path.join(ddir, "novel_narration_source.txt"), "w", encoding="utf-8") as f:
            f.write(novel_narration_text)
        with st.spinner("Chunking novel text..."):
            chunks = chunk_novel_text(novel_narration_text)
            lines = [Line(idx=i, start=float(i), end=float(i) + 1.0, zh=c) for i, c in enumerate(chunks)]
        if api_key:
            with st.spinner("Tagging speakers with the translation LLM..."):
                engine = translate_engines.get_engine(engine_choice, api_key, engine_model)
                known_chars = [c["character_name"] for c in db.list_characters(picked_id) if c["character_name"]]
                speakers = translate_engines.tag_speakers_llm([ln.zh for ln in lines], engine, known_chars)
                for ln, sp in zip(lines, speakers):
                    ln.speaker = sp
                for label in sorted(set(speakers)):
                    db.upsert_character(picked_id, label, character_name=label)
        else:
            st.warning("No API key yet -- skipped speaker tagging (needs an LLM engine). "
                       "All lines marked 'Narrator' for now; add a key and re-run to tag them.")
            for ln in lines:
                ln.speaker = "Narrator"
            db.upsert_character(picked_id, "Narrator", character_name="Narrator")

        st.session_state.lines = lines
        db.save_lines(picked_id, lines)
        db.update_drama(picked_id, status="aligned")
        st.success(f"Prepared {len(lines)} narration chunks.")

    if st.session_state.lines is None:
        saved = db.load_lines(picked_id)
        if saved:
            st.session_state.lines = [Line(idx=r["idx"], start=r["start"], end=r["end"],
                                             zh=r["zh"], en=r["en"] or "", speaker=r.get("speaker"),
                                             dub_filename=r.get("dub_filename")) for r in saved]

    _translate_job_id = f"translate_{picked_id}"
    _job = background_jobs.get_status(_translate_job_id)

    if run_translate and st.session_state.lines:
        novel_reference = existing_novel_text
        if content_mode == "audio_drama":
            if novel_file is not None:
                novel_reference = novel_file.read().decode("utf-8", errors="ignore")
            elif novel_pasted.strip():
                novel_reference = novel_pasted
        engine = translate_engines.get_engine(engine_choice, api_key, engine_model)
        if force_retranslate and any(ln.en for ln in st.session_state.lines):
            db.save_line_history_snapshot(picked_id, st.session_state.lines,
                                           "before force re-translate")
        glossary_terms = db.list_glossary_terms(drama["series_id"]) if drama.get("series_id") else None
        _scope = f"series:{drama['series_id']}" if drama.get("series_id") else "global"
        _prof = db.get_style_profile(_scope)
        _learned = ""
        if _prof and st.session_state.get("apply_style_profile", True):
            _learned = adaptive_style.profile_to_prompt_block(_prof.get("profile", {}))
        _emap = st.session_state.get(f"emotions_{picked_id}", {})
        _emotion_block = emotion.build_emotion_guidance(
            _emap, [ln.idx for ln in st.session_state.lines]) if _emap else ""
        style_guidelines = tguide.build_style_guidelines(
            style_preset, glossary_terms=glossary_terms,
            include_genre_notes=include_genre_notes,
            custom_notes=(custom_guide_notes
                           + ("\n\n" + _learned if _learned else "")
                           + ("\n\n" + _emotion_block if _emotion_block else "")))

        # A copy, not the live list -- the background thread mutates its own
        # lines and saves through the database; the main script reloads from
        # there once the job is visible again, rather than two threads
        # touching the same objects st.session_state also holds.
        _lines_copy = [Line(idx=l.idx, start=l.start, end=l.end, zh=l.zh, en=l.en,
                             speaker=l.speaker, dub_filename=l.dub_filename)
                       for l in st.session_state.lines]

        started = background_jobs.start_job(
            _translate_job_id, run_translate_job,
            _translate_job_id, picked_id, _lines_copy, engine, drama, style_note,
            novel_reference, force_retranslate, locale, glossary_terms, style_guidelines,
            engine_choice, style_preset)
        if started:
            st.info("Translation started in the background -- it keeps running even if you "
                    "switch tabs or close this one. Come back here any time to see progress; "
                    "it'll pick up right where it is.")
            st.rerun()
        else:
            st.warning("A translation is already running for this drama.")

    if _job:
        if _job["status"] == "running":
            st.progress(_job["progress"], text=_job.get("message") or "Translating...")
            st.caption("Running in the background -- safe to switch tabs, use other dramas, "
                      "or close the browser tab. Come back and this will show current progress.")
            if st.button("🔄 Refresh progress", key=f"refresh_tr_{picked_id}"):
                st.rerun()
        elif _job["status"] == "done":
            st.session_state.lines = [
                Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r["zh"],
                     en=r.get("en") or "", speaker=r.get("speaker"),
                     dub_filename=r.get("dub_filename"))
                for r in db.load_lines(picked_id)]
            _errors = (_job.get("result") or {}).get("errors", [])
            if _errors:
                failed_line_nums = [i + 1 for e in _errors for i in e["lines"]]
                st.warning(f"Translated with {len(_errors)} batch failure(s) -- lines "
                          f"{failed_line_nums} are still untranslated, but everything else was "
                          f"saved. Click Translate again to retry just the missing lines.")
            else:
                st.success("Translation complete.")
            background_jobs.clear_job(_translate_job_id)
        elif _job["status"] == "error":
            st.error(f"Translation failed: {_job['error']}")
            with st.expander("Details"):
                st.code(_job.get("traceback", ""), language="text")
            background_jobs.clear_job(_translate_job_id)

    # ---------------------------------------------------- Character naming
    characters = db.list_characters(picked_id)
    if characters:
        st.divider()
        st.subheader("6. Name your characters & set up voice cloning")
        st.caption("Map speaker labels to character names, and optionally attach a reference "
                   "voice clip per character for cloning (instead of the free TTS pool).")
        speaker_segments = st.session_state.get(f"speaker_segments_{picked_id}")
        can_auto_extract = content_mode == "audio_drama" and speaker_segments is not None

        if can_auto_extract and st.button("🎯 Auto-extract reference clips from this audio"):
            audio_path = os.path.join(ddir, drama["audio_filename"]) if drama["audio_filename"] else None
            if audio_path and os.path.exists(audio_path):
                import diarize as _diarize
                clips = dub_module.extract_reference_clips(audio_path, st.session_state.lines, speaker_segments, ddir)
                for label, info in clips.items():
                    matching_zh = next((ln.zh for ln in st.session_state.lines
                                         if ln.speaker == label and info["start"] <= ln.start <= info["end"] + 1), "")
                    db.upsert_character(picked_id, label,
                                         ref_audio_filename=os.path.relpath(info["path"], ddir),
                                         ref_text=matching_zh)
                st.success(f"Extracted {len(clips)} reference clip(s).")
                st.rerun()

        for c in characters:
            with st.container(border=True):
                cc1, cc2, cc3, cc4 = st.columns([1, 2, 2, 2])
                cc1.write(c["speaker_label"])
                name = cc2.text_input("name", value=c["character_name"] or "",
                                       label_visibility="collapsed", key=f"cname_{c['speaker_label']}")
                va = cc3.text_input("voice actor", value=c["voice_actor"] or "",
                                     placeholder="voice actor", label_visibility="collapsed",
                                     key=f"cva_{c['speaker_label']}")
                voice = cc4.selectbox("tts voice (fallback)", dub_module.DEFAULT_VOICE_POOL,
                                       index=dub_module.DEFAULT_VOICE_POOL.index(c["tts_voice"])
                                       if c["tts_voice"] in dub_module.DEFAULT_VOICE_POOL else 0,
                                       label_visibility="collapsed", key=f"cvoice_{c['speaker_label']}")
                if name != (c["character_name"] or "") or va != (c["voice_actor"] or "") or voice != c["tts_voice"]:
                    db.upsert_character(picked_id, c["speaker_label"], character_name=name,
                                         voice_actor=va, tts_voice=voice)

                rc1, rc2 = st.columns([1, 2])
                if c["ref_audio_filename"]:
                    rc1.caption(f"✅ Clone ref: {c['ref_audio_filename']}")
                else:
                    rc1.caption("No clone reference set")
                ref_upload = rc2.file_uploader(f"Upload clone reference for {name or c['speaker_label']}",
                                                type=["wav", "mp3", "m4a"], key=f"refup_{c['speaker_label']}",
                                                label_visibility="collapsed")
                ref_text_input = st.text_input(
                    f"What's said in that clip (original language, for {name or c['speaker_label']})",
                    value=c["ref_text"] or "", key=f"reftext_{c['speaker_label']}")
                if ref_upload is not None:
                    ref_filename = f"clone_ref_{c['speaker_label']}{os.path.splitext(ref_upload.name)[1]}"
                    with open(os.path.join(ddir, ref_filename), "wb") as f:
                        f.write(ref_upload.getbuffer())
                    db.upsert_character(picked_id, c["speaker_label"], ref_audio_filename=ref_filename)
                if ref_text_input != (c["ref_text"] or ""):
                    db.upsert_character(picked_id, c["speaker_label"], ref_text=ref_text_input)

        with st.expander("☁️ Or use ElevenLabs cloning instead (hosted, no GPU needed)", expanded=False):
            st.caption("Paid API with a limited free tier. Simpler to get working than F5-TTS since "
                      "there's no local model to install -- worth trying first if F5-TTS gives you trouble.")
            el_key = st.text_input("ElevenLabs API key", type="password",
                                    value=st.session_state.get("settings_elevenlabs", ""),
                                    key=f"el_key_{picked_id}")
            for c in characters:
                if not c["ref_audio_filename"]:
                    continue
                ec1, ec2 = st.columns([2, 1])
                label = c['character_name'] or c['speaker_label']
                if c.get("elevenlabs_voice_id"):
                    ec1.caption(f"{label} — ✅ cloned (voice ID: {c['elevenlabs_voice_id']})")
                else:
                    ec1.caption(label)
                if ec2.button(f"Clone via ElevenLabs", key=f"elclone_{c['speaker_label']}", disabled=not el_key):
                    voice_id = dub_module.clone_voice_elevenlabs(
                        el_key, c["character_name"] or c["speaker_label"],
                        os.path.join(ddir, c["ref_audio_filename"]))
                    db.upsert_character(picked_id, c["speaker_label"], elevenlabs_voice_id=voice_id)
                    st.success(f"Cloned and saved. Voice ID: {voice_id}")
                    st.rerun()

    # ---------------------------------------------------- Review & edit
    if st.session_state.lines:
        st.divider()
        st.subheader("7. Review & edit")

        all_lines = st.session_state.lines
        review_page_size = st.number_input("Lines per page", value=40, min_value=10, max_value=200,
                                            step=10, key="review_page_size")
        n_review_pages = max(1, (len(all_lines) + review_page_size - 1) // review_page_size)
        review_page = st.number_input(f"Page (1-{n_review_pages})", value=1, min_value=1,
                                       max_value=n_review_pages, step=1, key="review_page")
        page_start = (review_page - 1) * review_page_size
        page_slice = all_lines[page_start: page_start + review_page_size]

        edited_page_rows = []
        for ln in page_slice:
            cols = st.columns([1, 1, 1, 3, 3, 0.5])
            start = cols[0].number_input("start", value=round(ln.start, 2), step=0.1,
                                          label_visibility="collapsed", key=f"start_{ln.idx}")
            end = cols[1].number_input("end", value=round(ln.end, 2), step=0.1,
                                        label_visibility="collapsed", key=f"end_{ln.idx}")
            cols[2].caption(ln.speaker or "—")
            zh = cols[3].text_area("zh", value=ln.zh, height=68, label_visibility="collapsed", key=f"zh_{ln.idx}")
            en = cols[4].text_area("en", value=ln.en, height=68, label_visibility="collapsed", key=f"en_{ln.idx}")
            cols[5].write(f"#{ln.idx + 1}")
            edited_page_rows.append(Line(idx=ln.idx, start=start, end=end, zh=zh, en=en,
                                          speaker=ln.speaker, dub_filename=ln.dub_filename))

        # Splice the edited page back into the full list -- lines outside
        # this page stay untouched rather than being re-rendered/re-edited.
        edited_rows = list(all_lines)
        for i, ln in enumerate(edited_page_rows):
            edited_rows[page_start + i] = ln
        st.session_state.lines = edited_rows

        if st.button("💾 Save edits (this page)"):
            # Capture what you actually changed, so the style profile can learn
            # from real edits rather than guesswork.
            _prev = {r["idx"]: r.get("en") or "" for r in db.load_lines(picked_id)}
            for ln in edited_page_rows:
                before = _prev.get(ln.idx, "")
                if before and ln.en and before.strip() != ln.en.strip():
                    db.record_edit_sample(picked_id, ln.zh, before, ln.en)
            db.save_lines(picked_id, edited_rows)
            st.success("Saved.")

        with st.expander("🔍 Check line coverage (do this before translating)"):
            st.caption(
                "Scans for the patterns that usually mean real dialogue got missed or merged "
                "during transcription -- worth running before spending on translation, since "
                "fixing timing after is free and fixing it after translating means re-doing "
                "the translation too."
            )
            if st.button("Run coverage check"):
                st.session_state[f"coverage_report_{picked_id}"] = core_module.diagnose_line_coverage(
                    edited_rows)
            report = st.session_state.get(f"coverage_report_{picked_id}")
            if report:
                cc1, cc2, cc3, cc4 = st.columns(4)
                cc1.metric("Long/merged lines", len(report["long_lines"]))
                cc2.metric("Large silent gaps", len(report["large_gaps"]))
                cc3.metric("No source text", len(report["blank_zh"]))
                cc4.metric("Untranslated", len(report["blank_en"]))

                if report["long_lines"]:
                    st.markdown("**Suspiciously long lines** (likely several merged into one -- "
                              "lower the speech-splitting sensitivity above and re-align to fix)")
                    for l in report["long_lines"][:15]:
                        st.caption(f"Line {l['idx']+1} ({fmt_ts(l['start'])}–{fmt_ts(l['end'])}): "
                                  f"{l['note']} — \"{l['zh'][:40]}\"")
                if report["large_gaps"]:
                    st.markdown("**Large silent gaps** (real silence, or quiet dialogue the "
                              "detector missed -- worth a quick listen)")
                    for g in report["large_gaps"][:15]:
                        st.caption(f"{g['gap_seconds']:.1f}s gap between line {g['after_idx']+1} "
                                  f"and {g['before_idx']+1} ({fmt_ts(g['gap_start'])}–"
                                  f"{fmt_ts(g['gap_end'])})")
                if not report["long_lines"] and not report["large_gaps"]:
                    st.success("No obvious coverage problems found.")

        with st.expander("⏱️ Check dubbing pacing (optional)"):
            st.caption(
                "Flags lines that are too long to say naturally within their time slot, "
                "or oddly short relative to it. Doesn't change anything by itself."
            )
            if st.button("Check pacing"):
                flags = translate_engines.smart_segment_lines(edited_rows)
                if flags:
                    st.session_state[f"pacing_flags_{picked_id}"] = flags
                    st.warning(f"{len(flags)} line(s) flagged.")
                else:
                    st.success("No pacing issues detected.")
            flags = st.session_state.get(f"pacing_flags_{picked_id}", [])
            if flags:
                for f in flags:
                    st.caption(f"Line #{f['idx']+1} ({f['issue']}): {f['detail']}")
                too_long_idxs = {f["idx"] for f in flags if f["issue"] == "too_long_for_slot"}
                if too_long_idxs and api_key and st.button("✂️ Auto-shorten overlong lines with LLM"):
                    engine = translate_engines.get_engine(engine_choice, api_key, engine_model)
                    to_fix = [ln for ln in edited_rows if ln.idx in too_long_idxs]
                    translate_engines.rewrite_for_pacing_llm(to_fix, engine)
                    db.save_lines(picked_id, edited_rows)
                    st.session_state.lines = edited_rows
                    st.session_state[f"pacing_flags_{picked_id}"] = []
                    st.success(f"Shortened {len(to_fix)} line(s). Review below.")
                    st.rerun()

        with st.expander("🔍 Check translation consistency (optional)"):
            st.caption(
                "Flags the same Chinese name/term translated differently in different lines "
                "(e.g. a character's name spelled two ways). Doesn't change anything by itself."
            )
            if st.button("Check consistency") and api_key:
                engine = translate_engines.get_engine(engine_choice, api_key, engine_model)
                with st.spinner("Reviewing..."):
                    issues = translate_engines.check_consistency_llm(edited_rows, engine)
                st.session_state[f"consistency_issues_{picked_id}"] = issues
                if issues:
                    st.warning(f"{len(issues)} consistency issue(s) found.")
                else:
                    st.success("No consistency issues detected.")
            issues = st.session_state.get(f"consistency_issues_{picked_id}", [])
            for issue in issues:
                st.caption(f"**{issue.get('term')}**: {', '.join(issue.get('variants', []))} "
                          f"— {issue.get('note', '')}")

        with st.expander("🎭 Emotional register (sarcasm, humour, anger)"):
            st.caption(
                "Tags each line's emotional charge so translation preserves it. Sarcasm read "
                "as sincerity, or suppressed anger read as calm, breaks a scene even when the "
                "words are technically correct -- these are the registers most often flattened."
            )
            emap = st.session_state.get(f"emotions_{picked_id}", {})
            ec1, ec2 = st.columns([1, 1])
            use_cues = ec2.checkbox("Use audio delivery cues", value=(content_mode == "audio_drama"),
                                     help="Uses pacing and pauses from the original timing as "
                                          "weak evidence for emotional register.")
            if ec1.button("Detect emotional register") and api_key:
                eng_e = translate_engines.get_engine(engine_choice, api_key, engine_model)
                with st.spinner("Reading tone..."):
                    emap = emotion.detect_emotions(edited_rows, eng_e, use_audio_cues=use_cues)
                st.session_state[f"emotions_{picked_id}"] = emap
                st.rerun()

            if emap:
                summ = emotion.emotion_summary(emap)
                sc1, sc2 = st.columns(2)
                sc1.metric("Lines tagged", summ["total"])
                sc2.metric("High-risk register", summ["high_risk"],
                            help="Sarcasm, dry humour, suppressed anger, flirtation, evasion -- "
                                 "the registers most likely to be lost in translation.")
                st.caption(" · ".join(f"{k}: {v}" for k, v in
                                       sorted(summ["by_emotion"].items(), key=lambda x: -x[1])))
                st.caption("These tags are applied automatically on the next translation run.")

        with st.expander("🎯 Adaptive style (learns from your edits)"):
            st.caption(
                "Every line you rewrite is recorded. Once enough accumulate, they're analyzed "
                "for consistent patterns and folded into future translation prompts. "
                "Conservative by design -- it only reports preferences it can see repeatedly."
            )
            samples = db.list_edit_samples(picked_id)
            tend = adaptive_style.summarize_edit_tendencies(samples)
            if tend["total"]:
                tc1, tc2, tc3, tc4 = st.columns(4)
                tc1.metric("Edits recorded", tend["total"])
                tc2.metric("Shortened", tend["shortened"])
                tc3.metric("Expanded", tend["expanded"])
                tc4.metric("Avg word change", f"{tend['avg_word_delta']:+.1f}")
            else:
                st.caption("No edits recorded yet -- rewrite some lines above and save.")

            scope = f"series:{drama['series_id']}" if drama.get("series_id") else "global"
            existing_profile = db.get_style_profile(scope)
            if st.button("🧠 Learn my style from these edits") and api_key:
                eng_a = translate_engines.get_engine(engine_choice, api_key, engine_model)
                all_samples = db.list_edit_samples()
                with st.spinner("Analyzing your edits..."):
                    result = adaptive_style.analyze_edit_patterns(
                        all_samples, eng_a,
                        existing_profile=(existing_profile or {}).get("profile"))
                if result.get("preferences"):
                    db.save_style_profile(scope, result, sample_count=len(all_samples))
                    st.success(f"Learned {len(result['preferences'])} preference(s).")
                    st.rerun()
                else:
                    st.info(result.get("summary", "No clear patterns found yet."))

            if existing_profile and existing_profile["profile"].get("preferences"):
                pr = existing_profile["profile"]
                st.caption(f"**Learned profile** (confidence: {pr.get('confidence','?')}, "
                          f"from {existing_profile['sample_count']} edits)")
                if pr.get("summary"):
                    st.caption(f"_{pr['summary']}_")
                for p_ in pr["preferences"]:
                    st.caption(f"• {p_}")
                st.session_state["apply_style_profile"] = st.checkbox(
                    "Apply this profile to future translations",
                    value=st.session_state.get("apply_style_profile", True))
                if st.button("Reset learned style"):
                    db.save_style_profile(scope, {"preferences": []}, 0)
                    st.rerun()

        with st.expander("🔀 Translation versions (compare models)"):
            st.caption(
                "Every translation run is saved as a version, so re-translating with a "
                "different model never destroys the previous attempt. Compare them "
                "side-by-side and activate whichever reads better."
            )
            versions = db.list_translation_versions(picked_id)
            if not versions:
                st.caption("No saved versions yet -- run a translation first.")
            else:
                for v in versions:
                    vc1, vc2, vc3 = st.columns([3, 1, 1])
                    active = " ✅ **active**" if v["is_active"] else ""
                    when = v["created_at"][:16].replace("T", " ") if v["created_at"] else "?"
                    vc1.caption(f"**{v['label']}**{active} — {v['model'] or v['engine']} · {when}")
                    if not v["is_active"] and vc2.button("Activate", key=f"actv_{v['id']}"):
                        full = db.get_translation_version(v["id"])
                        if full:
                            db.save_line_history_snapshot(picked_id, st.session_state.lines,
                                                           "before switching version")
                            restored = [Line(idx=r["idx"], start=r["start"], end=r["end"],
                                              zh=r["zh"], en=r["en"], speaker=r.get("speaker"))
                                        for r in full["lines"]]
                            db.save_lines(picked_id, restored)
                            db.set_active_translation_version(picked_id, v["id"])
                            st.session_state.lines = restored
                            st.success(f"Activated '{v['label']}'.")
                            st.rerun()
                    if vc3.button("🗑️", key=f"delv_{v['id']}"):
                        db.delete_translation_version(v["id"])
                        st.rerun()

                if len(versions) >= 2:
                    st.markdown("**Compare two versions**")
                    vlabels = {f"{v['label']} ({v['created_at'][:10]})": v["id"] for v in versions}
                    cc1, cc2 = st.columns(2)
                    left = cc1.selectbox("Left", list(vlabels.keys()), index=0, key="cmp_left")
                    right = cc2.selectbox("Right", list(vlabels.keys()),
                                           index=min(1, len(vlabels) - 1), key="cmp_right")
                    if st.button("Show differences"):
                        lv = db.get_translation_version(vlabels[left])
                        rv = db.get_translation_version(vlabels[right])
                        rmap = {r["idx"]: r["en"] for r in rv["lines"]}
                        diffs = [(r["idx"], r["zh"], r["en"], rmap.get(r["idx"], ""))
                                 for r in lv["lines"] if rmap.get(r["idx"], "") != r["en"]]
                        st.caption(f"{len(diffs)} line(s) differ out of {len(lv['lines'])}.")
                        for idx, zh, l_en, r_en in diffs[:60]:
                            st.markdown(f"**Line {idx+1}** · {zh}")
                            dc1, dc2 = st.columns(2)
                            dc1.info(l_en or "_(empty)_")
                            dc2.warning(r_en or "_(empty)_")

        with st.expander("📝 Translation notes (idioms, wordplay, meaningful names)"):
            st.caption(
                "Reviews the translation for things that lost something crossing languages -- "
                "四字成语 and set phrases, puns, names whose characters carry meaning, literary "
                "allusions, and honorifics whose nuance doesn't survive a direct rendering. "
                "Produces notes for readers; doesn't change any line."
            )
            if st.button("Generate translation notes") and api_key:
                engine_n = translate_engines.get_engine(engine_choice, api_key, engine_model)
                with st.spinner("Reviewing for idioms, wordplay, and allusions..."):
                    found_notes = tguide.generate_translation_notes_llm(
                        edited_rows, engine_n, source_language=source_language)
                if found_notes:
                    db.save_translation_notes(picked_id, found_notes)
                    st.success(f"Found {len(found_notes)} note(s).")
                else:
                    st.info("Nothing flagged as needing a note.")
                st.rerun()

            existing_notes = db.list_translation_notes(picked_id)
            if existing_notes:
                st.caption(f"{len(existing_notes)} note(s) recorded:")
                for n in existing_notes:
                    nc1, nc2 = st.columns([5, 1])
                    line_ref = f"Line {n['line_idx'] + 1}" if n.get("line_idx") is not None else "—"
                    nc1.caption(f"**{n['term']}** ({n['note_type']}, {line_ref}): {n['note']}")
                    if nc2.button("🗑️", key=f"delnote_{n['id']}"):
                        db.delete_translation_note(n["id"])
                        st.rerun()

                notes_md = tguide.format_notes_as_markdown(
                    existing_notes, drama["title_en"] or drama["title_zh"] or "")
                st.download_button("📄 Download notes as Markdown", notes_md,
                                    file_name="translation_notes.md")

                with st.form(f"add_note_{picked_id}", clear_on_submit=True):
                    st.caption("Add your own note:")
                    anc1, anc2 = st.columns(2)
                    an_term = anc1.text_input("Term / phrase")
                    an_type = anc2.selectbox("Type", list(tguide.NOTE_TYPES.keys()),
                                              format_func=lambda k: tguide.NOTE_TYPES[k])
                    an_line = st.number_input("Line number (1-based)", value=1, min_value=1)
                    an_text = st.text_area("Note", height=68)
                    if st.form_submit_button("Add note") and an_term and an_text:
                        db.save_translation_notes(picked_id, [{
                            "line_idx": an_line - 1, "term": an_term,
                            "note_type": an_type, "note": an_text}])
                        st.success("Added.")
                        st.rerun()

        with st.expander("🔗 Merge short adjacent lines (optional)"):
            st.caption(
                "Combines consecutive short lines from the same speaker into one natural "
                "subtitle, when they're close enough in time that the split was probably just "
                "an artifact of the source transcript's line breaks. This changes your line "
                "count -- review the result before saving."
            )
            if st.button("Preview merge"):
                merged_preview = merge_adjacent_short_lines(list(edited_rows))
                st.session_state[f"merge_preview_{picked_id}"] = merged_preview
                st.info(f"{len(edited_rows)} lines -> {len(merged_preview)} lines after merging.")
            merge_preview = st.session_state.get(f"merge_preview_{picked_id}")
            if merge_preview:
                if st.button("✅ Apply merge"):
                    db.save_line_history_snapshot(picked_id, edited_rows, "before merge")
                    db.save_lines(picked_id, merge_preview)
                    st.session_state.lines = merge_preview
                    st.session_state[f"merge_preview_{picked_id}"] = None
                    st.success("Merged and saved. (Previous version saved to history -- "
                              "see 'Version history' below if you want it back.)")
                    st.rerun()

        with st.expander("🕓 Version history / undo"):
            st.caption(
                "Snapshots are taken automatically before operations that discard work "
                "(force re-translate, merge). Restoring replaces the current lines with the "
                "saved version -- and takes its own snapshot first, so you can undo the undo."
            )
            history = db.list_line_history(picked_id)
            if not history:
                st.caption("No snapshots yet for this drama.")
            else:
                for h in history:
                    hc1, hc2 = st.columns([3, 1])
                    when = h["created_at"][:16].replace("T", " ") if h["created_at"] else "?"
                    hc1.caption(f"**{h['label']}** — {when}")
                    if hc2.button("Restore", key=f"restore_{h['id']}"):
                        snapshot = db.get_line_history_snapshot(h["id"])
                        if snapshot:
                            db.save_line_history_snapshot(picked_id, st.session_state.lines,
                                                           "before restore")
                            restored = [Line(**s) for s in snapshot]
                            db.save_lines(picked_id, restored)
                            st.session_state.lines = restored
                            st.success(f"Restored '{h['label']}'.")
                            st.rerun()
                        else:
                            st.error("That snapshot could not be read.")

        st.subheader("8. AI dub / narration")
        st.caption(
            "Uses each character's cloning reference clip if set, otherwise falls back to "
            "the TTS engine chosen below. Voice cloning needs `f5-tts` installed locally. "
            "Requires ffmpeg on PATH."
        )
        tts_engine = st.radio(
            "Fallback TTS engine (used where no clone reference is set)",
            ["edge_tts", "offline"],
            format_func=lambda e: "🌐 edge-tts (free, online, more natural)"
                         if e == "edge_tts" else
                         "📴 Offline / Piper (fully local, no internet, lower quality)",
            horizontal=False,
        )
        voice_pool = dub_module.DEFAULT_VOICE_POOL if tts_engine == "edge_tts" else dub_module.DEFAULT_OFFLINE_VOICE_POOL
        dub_button_label = "🎙️ Generate narration track" if content_mode == "novel_narration" else "🎙️ Generate dub track"
        if st.button(dub_button_label):
            chars = db.list_characters(picked_id)
            voice_map = {c["speaker_label"]: c["tts_voice"] for c in chars if c["tts_voice"]}
            clone_map = {}
            el_key_for_dub = st.session_state.get(f"el_key_{picked_id}", "") or st.session_state.get("settings_elevenlabs", "")
            for c in chars:
                if c.get("elevenlabs_voice_id"):
                    clone_map[c["speaker_label"]] = {
                        "engine": "elevenlabs", "voice_id": c["elevenlabs_voice_id"],
                        "api_key": el_key_for_dub,
                    }
                elif c["ref_audio_filename"]:
                    clone_map[c["speaker_label"]] = {
                        "ref_audio": os.path.join(ddir, c["ref_audio_filename"]),
                        "ref_text": c["ref_text"] or "",
                    }
            progress_bar = st.progress(0.0, text="Generating...")
            try:
                build_fn = dub_module.build_narration_track if content_mode == "novel_narration" else dub_module.build_dub_track
                out_path, dub_errors = build_fn(
                    st.session_state.lines, ddir, voice_map, character_clone_map=clone_map,
                    tts_engine=tts_engine,
                    progress_cb=lambda frac: progress_bar.progress(frac, text=f"Generating... {frac*100:.0f}%"),
                )
                progress_bar.empty()
                db.save_lines(picked_id, st.session_state.lines)
                db.update_drama(picked_id, status="dubbed")
                if dub_errors:
                    failed_nums = [e["line_idx"] + 1 for e in dub_errors]
                    st.warning(f"Generated with {len(dub_errors)} line failure(s) -- lines "
                              f"{failed_nums} are silent in the track. Already-generated clips were "
                              f"kept; click Generate again to retry just the missing ones.")
                else:
                    st.success("Track generated." + (" Line timings updated to match narration audio -- "
                               "re-download the .srt below to stay in sync." if content_mode == "novel_narration" else ""))
                with open(out_path, "rb") as f:
                    st.download_button(f"Download {os.path.basename(out_path)}", f.read(),
                                        file_name=os.path.basename(out_path))
            except Exception as e:
                progress_bar.empty()
                st.error(f"Generation failed: {e}. Check ffmpeg / edge-tts / piper-tts / f5-tts install (see README).")

        st.subheader("9. Export subtitles")

        _total_lines = len(st.session_state.lines)
        _zh_filled = sum(1 for ln in st.session_state.lines if ln.zh.strip())
        _en_filled = sum(1 for ln in st.session_state.lines if ln.en.strip())

        # A timed-but-textless .srt is technically valid and gives no error --
        # it just looks broken when you open it. Say so before the download
        # happens rather than after someone's confused by an empty file.
        if _en_filled == 0:
            st.warning(f"⚠️ No lines are translated yet (0/{_total_lines}). The English and "
                      f"bilingual exports below will have correct timing but blank text. "
                      f"Run **Translate all lines** above first — or use the free "
                      f"`test_offline` engine to check the export pipeline without spending "
                      f"anything.")
        elif _en_filled < _total_lines:
            st.caption(f"ℹ️ {_total_lines - _en_filled} of {_total_lines} lines aren't "
                      f"translated yet — those will export with blank English text.")
        if _zh_filled == 0:
            st.error(f"⚠️ No source text on any line (0/{_total_lines}) — alignment may not "
                    f"have completed. Chinese/bilingual exports will be entirely blank.")

        c1, c2, c3 = st.columns(3)
        c1.download_button("Download English .srt", lines_to_srt(st.session_state.lines, "en"),
                            file_name="english.srt", disabled=(_en_filled == 0))
        c2.download_button("Download Chinese .srt", lines_to_srt(st.session_state.lines, "zh"),
                            file_name="chinese.srt", disabled=(_zh_filled == 0))
        c3.download_button("Download Bilingual .srt", lines_to_bilingual_srt(st.session_state.lines),
                            file_name="bilingual.srt", disabled=(_zh_filled == 0 and _en_filled == 0))

        if content_mode == "novel_narration":
            st.caption("Novel/narration content -- also export as an EPUB for reading in any e-reader app.")
            if st.button("📚 Generate EPUB"):
                import epub_io
                try:
                    epub_path = os.path.join(ddir, "translated.epub")
                    epub_io.export_epub(st.session_state.lines, drama["title_en"] or drama["title_zh"] or "Untitled",
                                         drama.get("author", ""), epub_path, field="en")
                    with open(epub_path, "rb") as f:
                        st.download_button("Download .epub", f.read(), file_name="translated.epub")
                except Exception as e:
                    st.error(f"EPUB export failed: {e}. Check `pip install ebooklib`.")

        source_video_path = None
        if drama.get("source_video_filename"):
            p = os.path.join(ddir, drama["source_video_filename"])
            if os.path.exists(p):
                source_video_path = p

        if source_video_path:
            st.subheader("10. Export full subtitled episode")
            st.caption("Uses the original video you uploaded + your reviewed English subtitles.")
            sub_style = st.radio(
                "Subtitle style",
                ["hardsub", "softsub"],
                format_func=lambda s: "🔥 Burn-in (always visible, plays everywhere)"
                             if s == "hardsub" else
                             "🎚️ Soft subtitles (toggleable track, needs a compatible player)",
                horizontal=False,
            )
            sub_language = st.selectbox("Which subtitles to export on video", ["English", "Bilingual", "Chinese"])
            sub_text_map = {"English": lines_to_srt(st.session_state.lines, "en"),
                             "Bilingual": lines_to_bilingual_srt(st.session_state.lines),
                             "Chinese": lines_to_srt(st.session_state.lines, "zh")}

            if st.button("🎬 Generate subtitled episode"):
                import video_export
                out_ext = os.path.splitext(source_video_path)[1]
                out_path = os.path.join(ddir, f"subtitled_episode{out_ext}")
                try:
                    with st.spinner("Rendering subtitled video... this can take a while for long episodes."):
                        if sub_style == "hardsub":
                            video_export.burn_subtitles(source_video_path, sub_text_map[sub_language], out_path)
                        else:
                            if out_ext.lower() not in (".mp4", ".mkv"):
                                out_path = os.path.splitext(out_path)[0] + ".mp4"
                            video_export.mux_soft_subtitles(source_video_path, sub_text_map[sub_language], out_path)
                    st.success("Subtitled episode ready.")
                    with open(out_path, "rb") as f:
                        st.download_button(f"Download {os.path.basename(out_path)}", f.read(),
                                            file_name=os.path.basename(out_path))
                except Exception as e:
                    st.error(f"Video export failed: {e}. Check that ffmpeg (with libass for hardsub) is installed.")

            dub_track_path = os.path.join(ddir, "dub_track.wav")
            if os.path.exists(dub_track_path):
                st.caption("An AI dub track exists for this drama -- you can also replace/mix the video's "
                          "audio with it below.")
                keep_orig = st.checkbox("Mix original audio in quietly underneath (instead of full replace)")
                if st.button("🔊 Export video with dub audio"):
                    import video_export
                    out_path = os.path.join(ddir, f"dubbed_episode{os.path.splitext(source_video_path)[1]}")
                    try:
                        with st.spinner("Rendering dubbed video..."):
                            video_export.replace_audio_with_dub(
                                source_video_path, dub_track_path, out_path,
                                keep_original_at_db=-20.0 if keep_orig else None,
                            )
                        st.success("Dubbed episode ready.")
                        with open(out_path, "rb") as f:
                            st.download_button(f"Download {os.path.basename(out_path)}", f.read(),
                                                file_name=os.path.basename(out_path))
                    except Exception as e:
                        st.error(f"Video export failed: {e}")

        st.divider()
        st.subheader("📦 Export this drama as a package")
        st.caption(
            "Bundles everything for this one title -- metadata, all subtitle formats, the "
            "original audio/video, any dub/narration track, and the reference novel -- into a "
            "single zip. For archiving a finished drama or handing it off, without exporting "
            "your whole library."
        )
        if st.button("Build export package"):
            import export_package
            try:
                pkg_path = os.path.join(ddir, "export_package.zip")
                with st.spinner("Building package..."):
                    _, manifest = export_package.build_drama_export_package(
                        db, picked_id, pkg_path, lines_to_srt, lines_to_bilingual_srt, Line)
                st.success(f"Package built with {len(manifest)} item(s).")
                for m in manifest:
                    st.caption(f"• {m}")
                with open(pkg_path, "rb") as f:
                    safe_title = re.sub(r"[^\w\- ]", "", drama["title_en"] or drama["title_zh"] or str(picked_id))
                    st.download_button("Download package .zip", f.read(),
                                        file_name=f"{safe_title}_package.zip")
            except Exception as e:
                st.error(f"Package build failed: {e}")

        if st.button("Mark as exported"):
            db.update_drama(picked_id, status="exported")
            st.success("Marked exported.")
    else:
        st.info("Run **Transcribe & Align** above to get started on this drama.")


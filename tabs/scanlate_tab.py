"""
tabs/scanlate.py -- Scanlate tab UI, extracted from the former monolithic app.py.
"""
from common import *


def render_scanlate_tab():
    st.subheader("Manga/comic typesetting")
    st.caption(
        "Hybrid workflow: auto-detect speech bubbles, clean the original text, "
        "auto-translate and place text -- then adjust anything that needs it before final render. "
        "Detection is a free OpenCV heuristic (works well on clean scans with clear white bubbles; "
        "misses irregular/borderless bubbles -- add those manually below)."
    )
    all_dramas_sc = db.list_dramas()
    if not all_dramas_sc:
        st.info("Create a drama in the Workspace tab first (used to organize pages, even for standalone scans).")
    else:
        sc_options = {f"#{d['id']} — {d['title_en'] or d['title_zh']}": d for d in all_dramas_sc}
        sc_label = st.selectbox("Drama", list(sc_options.keys()), key="scanlate_drama_pick")
        sc_drama = sc_options[sc_label]
        sc_lang = sc_drama.get("source_language") or "zh"
        sc_ddir = db.drama_dir(sc_drama["id"])
        pages_dir = os.path.join(sc_ddir, "pages")
        os.makedirs(pages_dir, exist_ok=True)

        new_pages = st.file_uploader("Upload page image(s)", type=["png", "jpg", "jpeg"],
                                      accept_multiple_files=True, key="scanlate_upload")
        if new_pages and st.button("➕ Add these pages"):
            existing = db.list_pages(sc_drama["id"])
            next_idx = len(existing)
            from PIL import Image as PILImage
            for i, f in enumerate(new_pages):
                fname = f"page_{next_idx + i:04d}{os.path.splitext(f.name)[1]}"
                fpath = os.path.join(pages_dir, fname)
                with open(fpath, "wb") as out:
                    out.write(f.getbuffer())
                w, h = PILImage.open(fpath).size
                db.create_page(sc_drama["id"], next_idx + i, os.path.join("pages", fname), w, h)
            st.success(f"Added {len(new_pages)} page(s).")
            st.rerun()

        pages = db.list_pages(sc_drama["id"])
        if not pages:
            st.info("No pages yet -- upload some above.")
        else:
            page_labels = {f"Page {p['idx']+1}": p for p in pages}
            picked_page_label = st.selectbox("Page", list(page_labels.keys()))
            page = page_labels[picked_page_label]
            page_path = os.path.join(sc_ddir, page["filename"])

            _sc_gemini_free_tier = st.session_state.get("gemini_free_tier", False)
            sc_engine_choice = st.selectbox(
                "Translation engine", list(translate_engines.ENGINES.keys()),
                format_func=lambda e: f"{e} — {translate_engines.engine_picker_label(e, _sc_gemini_free_tier)}",
                key="sc_engine")
            sc_api_key = synced_api_key_input("API key", sc_engine_choice, "sc_api_key")
            sc_backend = st.radio(
                "Bubble detection", ["cv", "ml"],
                format_func=lambda b: "🆓 Free heuristic (OpenCV, works on clean white bubbles)"
                             if b == "cv" else
                             "🎯 ML model (better accuracy, needs ultralytics+huggingface_hub, downloads a model)",
                horizontal=False, key="sc_backend")

            if st.button("🔍 Detect bubbles + auto-clean + auto-translate"):
                import scanlate
                _detect_notes = []
                with st.spinner("Detecting bubbles..."):
                    try:
                        boxes = scanlate.detect_bubbles(
                            page_path, backend=sc_backend,
                            hf_token=st.session_state.get("settings_hf_token", "") or None)
                    except scanlate.BubbleModelUnavailable as exc:
                        _detect_notes.append(("warning", str(exc)))
                        boxes = scanlate.detect_bubbles_cv(page_path)
                if not boxes:
                    _, _rejected = scanlate.detect_bubbles_cv(page_path, debug=True)
                    _reasons = {}
                    for r in _rejected:
                        _reasons[r[4]] = _reasons.get(r[4], 0) + 1
                    _detail = ("Rejected candidates: "
                               + ", ".join(f"{n}× {why}" for why, n in
                                           sorted(_reasons.items(), key=lambda x: -x[1]))
                               ) if _reasons else "No light enclosed regions found at all."
                    _detect_notes.append(("error",
                        "**No bubbles detected on this page.**\n\n"
                        f"{_detail}\n\n"
                        "Detection looks for enclosed light regions that don't touch the page "
                        "edge. It struggles with borderless bubbles, dark/inverted panels, "
                        "very low-contrast scans, and text drawn straight onto artwork.\n\n"
                        "What to try: the ML backend if you can reach Hugging Face, or add "
                        "boxes by hand with '➕ Add a bubble manually' below."))
                    boxes = []

                # Persisted, because st.rerun() below would otherwise wipe these
                # messages after about a second -- which is what made this look
                # like a flicker rather than an explanation.
                st.session_state[f"detect_notes_{picked_page_label}"] = _detect_notes
                with st.spinner("Running OCR on each bubble..."):
                    import ocr as ocr_module
                    for b in boxes:
                        crop_path = os.path.join(sc_ddir, "_bubble_crop.png")
                        from PIL import Image as PILImage
                        # Inset slightly before cropping -- OCRing the box's own
                        # border can make Tesseract return nothing at all or a
                        # few stray characters instead of the real text.
                        ocr_box = scanlate.inset_box_for_ocr(b)
                        PILImage.open(page_path).crop(
                            (ocr_box["x"], ocr_box["y"],
                             ocr_box["x"] + ocr_box["w"], ocr_box["y"] + ocr_box["h"])
                        ).save(crop_path)
                        backend = "manga_ocr" if sc_lang == "ja" else "tesseract"
                        try:
                            b["source_text"] = ocr_module.extract_text_from_images(
                                [crop_path], backend=backend, source_language=sc_lang,
                                tesseract_cmd=st.session_state.get("settings_tesseract_cmd") or None
                            ).strip()
                        except Exception:
                            b["source_text"] = ""
                        # Classical-CV style guess (stroke weight/irregularity,
                        # not a trained font classifier -- see scanlate.py's
                        # own docstring) so the render step doesn't default
                        # every bubble on the page to the same font. Always
                        # reviewable/overridable below before final render.
                        style = scanlate.sample_text_style(page_path, b)
                        b["font_category"] = style["suggested_style"]
                        b["ink_ratio"] = style.get("ink_ratio")
                        b["irregular"] = style.get("irregular")
                if sc_api_key:
                    with st.spinner("Translating (with context from prior pages)..."):
                        engine = translate_engines.get_engine(
                            sc_engine_choice, sc_api_key,
                            free_tier=sc_engine_choice == "gemini" and _sc_gemini_free_tier,
                            base_url=(st.session_state.get("settings_ollama_url") or None)
                            if sc_engine_choice == "ollama" else None)
                        texts = [b["source_text"] for b in boxes]
                        prev_context = st.session_state.get(f"sc_context_{sc_drama['id']}", "")
                        try:
                            translations, new_context = scanlate.translate_page_with_context(
                                texts, engine, sc_drama, previous_context=prev_context,
                                usage_cb=lambda inp, out: db.log_usage(
                                    sc_drama["id"], sc_engine_choice,
                                    getattr(engine, "model", sc_engine_choice),
                                    "scanlate_translate", inp, out,
                                    translate_engines.estimate_cost_for_engine(engine, inp, out)))
                            st.session_state[f"sc_context_{sc_drama['id']}"] = new_context
                            for b, t in zip(boxes, translations):
                                b["translated_text"] = t
                                b["font_size"] = 18
                                b["skip"] = False
                        except Exception as e:
                            st.warning(f"Translation failed ({e}) -- OCR text was still captured and "
                                      f"saved below. Edit translated text manually, or fix the API "
                                      f"key/connection and click Detect again to retry.")
                            for b in boxes:
                                b.setdefault("translated_text", "")
                                b.setdefault("font_size", 18)
                                b.setdefault("skip", False)
                else:
                    for b in boxes:
                        b["translated_text"] = ""
                        b["font_size"] = 18
                        b["skip"] = False
                db.save_bubbles(page["id"], boxes)
                st.success(f"Found {len(boxes)} bubble(s).")
                st.rerun()

            for _lvl, _msg in st.session_state.get(f"detect_notes_{picked_page_label}", []):
                (st.error if _lvl == "error" else st.warning)(_msg)

            bubbles = db.load_bubbles(page["id"])
            if bubbles:
                import scanlate
                st.subheader("Review & adjust bubbles")
                edited_bubbles = []
                for b in bubbles:
                    with st.container(border=True):
                        c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1])
                        x = c1.number_input("x", value=b["x"], key=f"bx_{b['id']}")
                        y = c2.number_input("y", value=b["y"], key=f"by_{b['id']}")
                        w = c3.number_input("w", value=b["w"], key=f"bw_{b['id']}")
                        h = c4.number_input("h", value=b["h"], key=f"bh_{b['id']}")
                        font_size = c5.number_input("font", value=b["font_size"], min_value=6,
                                                     max_value=72, key=f"bfs_{b['id']}")
                        st.caption(f"Original: {b['source_text']}")
                        translated = st.text_area("Translated text", value=b["translated_text"],
                                                    height=60, key=f"btr_{b['id']}")
                        fc1, fc2 = st.columns([1, 2])
                        skip = fc1.checkbox("Skip this bubble (don't render)", value=bool(b["skip"]),
                                            key=f"bsk_{b['id']}")
                        _cat_options = scanlate.FONT_CATEGORIES
                        _cur_cat = b.get("font_category") or "regular"
                        font_category = fc2.selectbox(
                            "Font style", _cat_options,
                            index=_cat_options.index(_cur_cat) if _cur_cat in _cat_options else 0,
                            key=f"bfc_{b['id']}",
                            help="Auto-detected from the original bubble's stroke weight/"
                                 "irregularity (classical image analysis, not a trained font "
                                 "classifier) -- override here if it guessed wrong. "
                                 "\"handwritten\" only looks different if a custom font is set "
                                 "for it below; there's no reliable brush-style font on a stock "
                                 "system install.")
                        edited_bubbles.append({
                            "x": x, "y": y, "w": w, "h": h, "font_size": font_size,
                            "source_text": b["source_text"], "translated_text": translated, "skip": skip,
                            "font_category": font_category,
                        })

                with st.expander("➕ Add a bubble manually"):
                    mc1, mc2, mc3, mc4 = st.columns(4)
                    mx = mc1.number_input("x", value=0, key="manual_x")
                    my = mc2.number_input("y", value=0, key="manual_y")
                    mw = mc3.number_input("w", value=150, key="manual_w")
                    mh = mc4.number_input("h", value=80, key="manual_h")
                    mtext = st.text_input("Translated text", key="manual_text")
                    if st.button("Add bubble"):
                        edited_bubbles.append({"x": mx, "y": my, "w": mw, "h": mh, "font_size": 18,
                                               "source_text": "", "translated_text": mtext, "skip": False,
                                               "font_category": "regular"})

                with st.expander("🔤 Custom fonts (optional)"):
                    st.caption(
                        "Upload a .ttf/.otf per style category to use instead of the system "
                        "fallback fonts -- especially useful for \"handwritten\", which has no "
                        "reliable brush/handwriting-style font on a stock install of any OS."
                    )
                    custom_fonts = {}
                    fonts_dir = os.path.join(sc_ddir, "fonts")
                    for cat in scanlate.FONT_CATEGORIES:
                        existing_font = os.path.join(fonts_dir, f"{cat}.ttf")
                        uploaded = st.file_uploader(f"{cat.capitalize()} font", type=["ttf", "otf"],
                                                     key=f"font_upload_{cat}")
                        if uploaded:
                            os.makedirs(fonts_dir, exist_ok=True)
                            with open(existing_font, "wb") as f:
                                f.write(uploaded.getbuffer())
                            st.success(f"Saved as the {cat} font for this drama.")
                        if os.path.exists(existing_font):
                            custom_fonts[cat] = existing_font
                            st.caption(f"✅ Using uploaded {cat} font.")

                if st.button("💾 Save bubble edits"):
                    db.save_bubbles(page["id"], edited_bubbles)
                    st.success("Saved.")

                st.subheader("Preview / render")
                st.image(page_path, caption="Original", width=350)
                if st.button("🎨 Render typeset page"):
                    import scanlate
                    out_path = os.path.join(sc_ddir, "pages", f"typeset_{page['idx']:04d}.png")
                    try:
                        _, skipped_blank = scanlate.process_page(page_path, edited_bubbles, out_path,
                                                                   custom_fonts=custom_fonts)
                        db.update_page(page["id"], rendered_filename=os.path.join("pages", os.path.basename(out_path)))
                        if skipped_blank:
                            st.warning(
                                f"Rendered, but {len(skipped_blank)} bubble(s) had no translated text "
                                f"and were left as the original -- not blanked out. This usually means "
                                f"bubble detection found the wrong region (too small, or in the wrong "
                                f"spot) so OCR/translation had nothing real to work with. Check the "
                                f"bubble's x/y/w/h above against the actual speech bubble in the "
                                f"original image, adjust if needed, and re-detect or fill in the text "
                                f"by hand.")
                        else:
                            st.success("Rendered.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Render failed: {e}")

                if page.get("rendered_filename"):
                    rendered_path = os.path.join(sc_ddir, page["rendered_filename"])
                    if os.path.exists(rendered_path):
                        st.image(rendered_path, caption="Typeset result", width=350)
                        with open(rendered_path, "rb") as f:
                            st.download_button("Download typeset page", f.read(),
                                                file_name=os.path.basename(rendered_path))

                style_report_path = os.path.join(sc_ddir, "pages", f"font_styles_{page['idx']:04d}.json")
                scanlate.export_font_style_report(edited_bubbles, style_report_path)
                with open(style_report_path, "r", encoding="utf-8") as f:
                    st.download_button("📄 Export detected font styles (JSON)", f.read(),
                                        file_name=os.path.basename(style_report_path),
                                        help="Per-bubble box + font style, the same idea as "
                                             "BalloonsTranslator's font-detection export -- a "
                                             "reviewable record of what this page rendered with.")

        st.divider()
        st.subheader("📦 Bulk render all pages")
        st.caption("Renders every page that has saved bubbles and zips the result -- for a whole "
                  "chapter at once instead of one page at a time.")
        if st.button("🎨 Render all pages + download ZIP"):
            to_render = []
            for p in pages:
                p_bubbles = db.load_bubbles(p["id"])
                if p_bubbles:
                    p_path = os.path.join(sc_ddir, p["filename"])
                    out_name = f"typeset_{p['idx']:04d}.png"
                    to_render.append((p_path, p_bubbles, out_name))
            if not to_render:
                st.warning("No pages have saved bubbles yet -- detect/save at least one page first.")
            else:
                import scanlate
                _bulk_fonts_dir = os.path.join(sc_ddir, "fonts")
                _bulk_custom_fonts = {
                    cat: os.path.join(_bulk_fonts_dir, f"{cat}.ttf")
                    for cat in scanlate.FONT_CATEGORIES
                    if os.path.exists(os.path.join(_bulk_fonts_dir, f"{cat}.ttf"))
                }
                with st.spinner(f"Rendering {len(to_render)} page(s)..."):
                    zip_path, render_errors, blank_report = scanlate.bulk_render_pages(
                        to_render, os.path.join(sc_ddir, "pages"), custom_fonts=_bulk_custom_fonts)
                if render_errors:
                    st.warning(f"{len(render_errors)} page(s) failed to render: "
                              f"{[e['file'] for e in render_errors]}")
                if blank_report:
                    total_blank = sum(blank_report.values())
                    st.warning(
                        f"{total_blank} bubble(s) across {len(blank_report)} page(s) had no "
                        f"translated text and were left as the original rather than blanked out -- "
                        f"usually a sign bubble detection found the wrong region on those pages. "
                        f"Affected: {', '.join(f'{f} ({n})' for f, n in blank_report.items())}")
                with open(zip_path, "rb") as f:
                    st.download_button(f"Download {len(to_render) - len(render_errors)} typeset pages (.zip)",
                                        f.read(), file_name="typeset_pages.zip")


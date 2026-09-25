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

        new_pages = st.file_uploader(
            "Upload page image(s) or a PDF", type=["png", "jpg", "jpeg", "pdf"],
            accept_multiple_files=True, key="scanlate_upload",
            help="A PDF is split into one page per embedded image -- the common shape "
                 "for a scanned-raw or finished-scanlation PDF. A PDF page with no "
                 "embedded image (text/vector-only) is skipped, not added blank.")
        if new_pages and st.button("➕ Add these pages"):
            import shutil
            import tempfile
            import scanlate
            existing = db.list_pages(sc_drama["id"])
            next_idx = len(existing)
            from PIL import Image as PILImage
            added = 0
            pdf_skipped_total = 0
            for f in new_pages:
                ext = os.path.splitext(f.name)[1].lower()
                if ext == ".pdf":
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        tmp_pdf_path = os.path.join(tmp_dir, f.name)
                        with open(tmp_pdf_path, "wb") as out:
                            out.write(f.getbuffer())
                        extracted, skipped = scanlate.pdf_to_page_images(tmp_pdf_path, tmp_dir)
                        for p in extracted:
                            fname = f"page_{next_idx + added:04d}.png"
                            fpath = os.path.join(pages_dir, fname)
                            shutil.copy(p, fpath)
                            w, h = PILImage.open(fpath).size
                            db.create_page(sc_drama["id"], next_idx + added,
                                           os.path.join("pages", fname), w, h)
                            added += 1
                        pdf_skipped_total += len(skipped)
                else:
                    fname = f"page_{next_idx + added:04d}{ext}"
                    fpath = os.path.join(pages_dir, fname)
                    with open(fpath, "wb") as out:
                        out.write(f.getbuffer())
                    w, h = PILImage.open(fpath).size
                    db.create_page(sc_drama["id"], next_idx + added,
                                   os.path.join("pages", fname), w, h)
                    added += 1
            msg = f"Added {added} page(s)."
            if pdf_skipped_total:
                msg += f" {pdf_skipped_total} PDF page(s) had no embedded image and were skipped."
            st.success(msg)
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
                "Bubble detection", ["auto", "cv", "ml"],
                format_func=lambda b: {
                    "auto": "🤖 Auto (recommended -- ML model if its weights are already "
                            "cached locally, the free heuristic otherwise)",
                    "cv": "🆓 Free heuristic (OpenCV, works on clean white bubbles)",
                    "ml": "🎯 ML model (better accuracy, needs transformers+huggingface_hub, "
                          "downloads a model)",
                }[b], horizontal=False, key="sc_backend")
            st.caption("Inpainting (cleaning the original text) auto-selects the same way -- "
                       "LaMa-manga if its weights are cached, plain OpenCV inpainting otherwise.")

            _ocr_backend_options = ["auto", "manga_ocr", "paddle", "paddle_vl_manga", "tesseract"]
            oc1, oc2 = st.columns([2, 3])
            sc_ocr_backend_choice = oc1.selectbox(
                "OCR backend", _ocr_backend_options,
                format_func=lambda b: "🤖 Auto (by source language)" if b == "auto" else b,
                key="sc_ocr_backend",
                help="Auto picks manga_ocr for Japanese, paddle for Chinese/Korean, tesseract "
                     "otherwise -- override to force one, e.g. to compare paddle_vl_manga "
                     "against manga_ocr on a Japanese page.")
            sc_prefer_paddle_vl_manga = oc2.checkbox(
                "For Japanese, Auto prefers PaddleOCR-VL-For-Manga over manga_ocr",
                value=False, key="sc_prefer_paddle_vl_manga",
                help="Opt-in second Japanese backend -- only affects what Auto picks. Its own "
                     "model card doesn't benchmark against manga_ocr, so leave this off until "
                     "a real side-by-side on your own pages says it's actually better.")

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
                    ocr_backend_override = (None if sc_ocr_backend_choice == "auto"
                                             else sc_ocr_backend_choice)
                    for b in boxes:
                        # ocr_box_region() insets the box before cropping -- OCRing
                        # a bubble's own border can make some backends return
                        # nothing at all or a few stray characters instead of the
                        # real text -- and routes to the right backend for
                        # sc_lang (Step 11 item 4), or the manual override above.
                        try:
                            b["source_text"] = scanlate.ocr_box_region(
                                page_path, b, sc_lang, backend=ocr_backend_override,
                                tesseract_cmd=st.session_state.get("settings_tesseract_cmd") or None,
                                prefer_paddle_vl_manga=sc_prefer_paddle_vl_manga)
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
                    if st.button("🔎 OCR this region", key="manual_ocr_button",
                                 help="Runs OCR on the box above instead of requiring the "
                                      "source text to be typed in by hand -- for text the "
                                      "auto-detector missed."):
                        try:
                            ocr_text = scanlate.ocr_box_region(
                                page_path, {"x": mx, "y": my, "w": mw, "h": mh}, sc_lang,
                                backend=(None if sc_ocr_backend_choice == "auto"
                                         else sc_ocr_backend_choice),
                                tesseract_cmd=st.session_state.get("settings_tesseract_cmd") or None,
                                prefer_paddle_vl_manga=sc_prefer_paddle_vl_manga)
                            st.session_state["manual_source_text"] = ocr_text
                            if not ocr_text:
                                st.warning("OCR found no text in this region.")
                        except Exception as e:
                            st.warning(f"OCR failed: {e}")
                    msource = st.text_input(
                        "Source text (filled by OCR above, or type by hand)", key="manual_source_text")
                    mtext = st.text_input("Translated text", key="manual_text")
                    if st.button("Add bubble"):
                        edited_bubbles.append({"x": mx, "y": my, "w": mw, "h": mh, "font_size": 18,
                                               "source_text": msource, "translated_text": mtext,
                                               "skip": False, "font_category": "regular"})

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

                with st.expander("🖌️ Manual erase/heal brush"):
                    st.caption(
                        "Paint over anything the detector missed -- a sound effect, background "
                        "text, or a stray artifact outside any bubble box -- and it's inpainted "
                        "directly, no bubble/box required. Uses the same backend as the bubble "
                        "inpainting above (LaMa-manga if its weights are cached, OpenCV otherwise)."
                    )
                    try:
                        from streamlit_drawable_canvas import st_canvas
                        from PIL import Image as PILImage
                        _brush_src = PILImage.open(page_path).convert("RGB")
                        _disp_w = min(500, _brush_src.width)
                        _scale = _disp_w / _brush_src.width
                        _disp_h = max(1, int(_brush_src.height * _scale))
                        brush_size = st.slider("Brush size", 5, 60, 20, key="brush_size")
                        canvas_result = st_canvas(
                            fill_color="rgba(255, 0, 0, 0.4)", stroke_width=brush_size,
                            stroke_color="#ff0000",
                            background_image=_brush_src.resize((_disp_w, _disp_h)),
                            update_streamlit=True, height=_disp_h, width=_disp_w,
                            drawing_mode="freedraw", key="brush_canvas")
                        if st.button("🩹 Erase brushed region"):
                            _alpha = (canvas_result.image_data[:, :, 3]
                                      if canvas_result.image_data is not None else None)
                            if _alpha is None or not (_alpha > 0).any():
                                st.warning("Paint over something first.")
                            else:
                                import cv2 as _cv2
                                mask_small = (_alpha > 0).astype("uint8")
                                mask_full = _cv2.resize(
                                    mask_small, (_brush_src.width, _brush_src.height),
                                    interpolation=_cv2.INTER_NEAREST)
                                brush_out_path = os.path.join(
                                    sc_ddir, "pages", f"typeset_{page['idx']:04d}.png")
                                # Brush over the already-rendered typeset page when one
                                # exists, so this doesn't undo prior bubble renders --
                                # otherwise the original.
                                base_for_brush = (brush_out_path
                                                   if os.path.exists(brush_out_path) else page_path)
                                try:
                                    scanlate.inpaint_mask_region(
                                        base_for_brush, mask_full, out_path=brush_out_path)
                                    st.success("Erased.")
                                except scanlate.InpaintModelUnavailable as exc:
                                    st.warning(str(exc))
                                db.update_page(page["id"], rendered_filename=os.path.join(
                                    "pages", os.path.basename(brush_out_path)))
                                st.rerun()
                    except ImportError:
                        st.info(
                            "The manual erase/heal brush needs an extra package:\n\n"
                            "    pip install streamlit-drawable-canvas\n\n"
                            "(its PyPI page shows no release in the past 12 months -- a real "
                            "maintenance-inactive flag, though the component itself is simple "
                            "enough -- a Fabric.js wrapper -- that inactivity alone isn't "
                            "disqualifying; worth a quick compatibility check against this "
                            "app's pinned Streamlit version before relying on it.)"
                        )
                    except Exception as exc:
                        # Confirmed directly while building this: streamlit-drawable-canvas
                        # can fail at setup with a real incompatibility against a newer
                        # Streamlit release (StreamlitAPIException, not ImportError) rather
                        # than a missing package -- same maintenance-inactive risk flagged
                        # above, now observed rather than just suspected. Caught broadly so
                        # that shows as a clear message instead of crashing the whole tab.
                        st.info(
                            f"The manual erase/heal brush's component ("
                            f"streamlit-drawable-canvas) failed to load: "
                            f"{type(exc).__name__}: {exc}\n\n"
                            "This looks like the package's own maintenance-inactive gap "
                            "(no release in over a year) catching up with a newer Streamlit "
                            "release -- check for an updated/alternative canvas component."
                        )

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
                rendered_paths = [os.path.join(sc_ddir, "pages", name) for _, _, name in to_render
                                  if os.path.exists(os.path.join(sc_ddir, "pages", name))]
                if rendered_paths:
                    import scanlate
                    pdf_path = os.path.join(sc_ddir, "pages", "typeset_pages.pdf")
                    scanlate.pages_to_pdf(rendered_paths, pdf_path)
                    with open(pdf_path, "rb") as f:
                        st.download_button(f"Download {len(rendered_paths)} typeset pages (.pdf)",
                                            f.read(), file_name="typeset_pages.pdf")

        st.divider()
        st.subheader("🔎 Bulk find & replace")
        st.caption(
            "Retroactively corrects already-translated bubble text across every saved page of "
            "this drama at once -- a name translated inconsistently before a glossary entry "
            "existed, or a typo that repeats. Distinct from the glossary (shapes future "
            "translations) and from translation memory (suggests reuse going forward). Every "
            "match is shown before anything is applied."
        )
        fr1, fr2 = st.columns(2)
        fr_find = fr1.text_input("Find", key="sc_fr_find")
        fr_replace = fr2.text_input("Replace with", key="sc_fr_replace")
        fr3, fr4 = st.columns(2)
        fr_case_sensitive = fr3.checkbox("Case-sensitive", value=False, key="sc_fr_case")
        fr_regex = fr4.checkbox("Regex", value=False, key="sc_fr_regex")
        if st.button("🔍 Preview matches"):
            import scanlate
            if not fr_find:
                st.warning("Enter something to find first.")
            else:
                try:
                    matches = scanlate.bulk_find_replace_preview(
                        db.list_bubbles_for_drama(sc_drama["id"]), fr_find, fr_replace,
                        case_sensitive=fr_case_sensitive, use_regex=fr_regex)
                except ValueError as e:
                    matches = None
                    st.error(str(e))
                if matches is not None:
                    st.session_state["sc_fr_matches"] = matches
                    if not matches:
                        st.info("No matches found.")
                    else:
                        st.write(f"{len(matches)} match(es):")
                        st.table([{"Page": m["page_idx"] + 1, "Before": m["old_text"],
                                   "After": m["new_text"]} for m in matches])
        _fr_matches = st.session_state.get("sc_fr_matches") or []
        if _fr_matches and st.button(f"✅ Apply {len(_fr_matches)} change(s)"):
            for m in _fr_matches:
                db.update_bubble_text(m["id"], m["new_text"])
            st.session_state["sc_fr_matches"] = []
            st.success(f"Applied {len(_fr_matches)} change(s). Re-render affected pages to see "
                       f"them in the typeset output.")


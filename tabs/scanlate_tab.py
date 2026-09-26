"""
tabs/scanlate.py -- Scanlate tab UI, extracted from the former monolithic app.py.
"""
from common import *


def render_scanlate_tab():
    ui_theme.type_scale_scope()
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
        slice_strips = st.checkbox(
            "✂️ Slice tall webtoon strips into pages", value=False, key="scanlate_slice_strips",
            help="A long vertical strip (much taller than it is wide) is cut at the blank "
                 "gaps between panels into page-sized slices, with a small overlap so a bubble "
                 "on a cut isn't lost. Ordinary pages are added as they are.")
        if new_pages and st.button("➕ Add these pages"):
            added, pdf_skipped_total = add_uploaded_pages(sc_drama["id"], pages_dir, new_pages,
                                                          slice_strips=slice_strips)
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

            _sc_ocr_override = None if sc_ocr_backend_choice == "auto" else sc_ocr_backend_choice
            _sc_detect_kwargs = dict(
                detect_backend=sc_backend,
                hf_token=st.session_state.get("settings_hf_token", "") or None,
                ocr_backend=_sc_ocr_override,
                tesseract_cmd=st.session_state.get("settings_tesseract_cmd") or None,
                prefer_paddle_vl_manga=sc_prefer_paddle_vl_manga)
            # Same glossary lookup Workspace's translation uses -- honorifics
            # included (category "honorific") -- not a separate system.
            _sc_glossary = (db.list_glossary_terms(sc_drama["series_id"])
                            if sc_drama.get("series_id") else None)

            def _sc_engine():
                return translate_engines.get_engine(
                    sc_engine_choice, sc_api_key,
                    free_tier=sc_engine_choice == "gemini" and _sc_gemini_free_tier,
                    base_url=(st.session_state.get("settings_ollama_url") or None)
                    if sc_engine_choice == "ollama" else None)

            def _sc_usage_cb(engine):
                return lambda inp, out: db.log_usage(
                    sc_drama["id"], sc_engine_choice, getattr(engine, "model", sc_engine_choice),
                    "scanlate_translate", inp, out,
                    translate_engines.estimate_cost_for_engine(engine, inp, out))

            _sc_context_key = f"sc_context_{sc_drama['id']}"

            if st.button("🔍 Detect bubbles + auto-clean + auto-translate"):
                import scanlate
                with st.spinner("Detecting bubbles and running OCR..."):
                    boxes, _detect_notes = scanlate.detect_and_ocr_page(
                        page_path, sc_lang, page_id=page["id"], **_sc_detect_kwargs)
                # Persisted, because st.rerun() below would otherwise wipe these
                # messages after about a second -- which is what made this look
                # like a flicker rather than an explanation.
                st.session_state[f"detect_notes_{picked_page_label}"] = _detect_notes
                if sc_api_key and boxes:
                    with st.spinner("Translating (with context from prior pages)..."):
                        engine = _sc_engine()
                        try:
                            st.session_state[_sc_context_key] = scanlate.translate_page_bubbles(
                                boxes, engine, sc_drama,
                                previous_context=st.session_state.get(_sc_context_key, ""),
                                glossary_terms=_sc_glossary, usage_cb=_sc_usage_cb(engine))
                        except Exception as e:
                            st.warning(f"Translation failed ({e}) -- OCR text was still captured and "
                                      f"saved below. Fix the OCR text if needed, then use "
                                      f"'🌐 Translate from the source text above' to retry.")
                db.save_bubbles(page["id"], boxes)
                st.success(f"Found {len(boxes)} bubble(s).")
                st.rerun()

            with st.expander("📚 Batch: detect + OCR + translate every page"):
                st.caption(
                    "Runs the same detect → OCR → translate as the button above on every saved "
                    "page of this drama, in page order, carrying the prior-page context from one "
                    "page to the next. Translation only runs if an API key is set above; "
                    "otherwise this detects and OCRs only.")
                sc_batch_skip_existing = st.checkbox(
                    "Skip pages that already have saved bubbles", value=True,
                    key="sc_batch_skip_existing",
                    help="Leave on to keep any page you've already reviewed or edited. Turning "
                         "it off re-detects those pages too, replacing their saved bubbles.")
                if st.button("▶️ Run batch", key="sc_batch_run"):
                    import scanlate
                    _batch_pages = [
                        {"id": p["id"], "image_path": os.path.join(sc_ddir, p["filename"])}
                        for p in pages
                        if not (sc_batch_skip_existing and db.load_bubbles(p["id"]))]
                    if not _batch_pages:
                        st.info("Every page already has saved bubbles -- nothing to do.")
                    else:
                        _batch_engine = _sc_engine() if sc_api_key else None
                        _bar = st.progress(0.0, text=f"0 / {len(_batch_pages)} pages")
                        _report = scanlate.batch_process_pages(
                            _batch_pages, sc_lang, db.save_bubbles, engine=_batch_engine,
                            drama_meta=sc_drama, glossary_terms=_sc_glossary,
                            previous_context=st.session_state.get(_sc_context_key, ""),
                            usage_cb=_sc_usage_cb(_batch_engine) if _batch_engine else None,
                            progress_cb=lambda done, total, _p: _bar.progress(
                                done / total, text=f"{done} / {total} pages"),
                            **_sc_detect_kwargs)
                        st.session_state[_sc_context_key] = _report["context"]
                        _idx_by_id = {p["id"]: p["idx"] for p in pages}
                        st.success(f"Processed {len(_report['processed'])} page(s).")
                        for item in _report["processed"]:
                            for _lvl, _msg in item["notes"]:
                                (st.error if _lvl == "error" else st.warning)(
                                    f"Page {_idx_by_id[item['page_id']] + 1}: {_msg}")
                        for err in _report["errors"]:
                            st.error(f"Page {_idx_by_id[err['page_id']] + 1} failed: {err['error']}")

            for _lvl, _msg in st.session_state.get(f"detect_notes_{picked_page_label}", []):
                (st.error if _lvl == "error" else st.warning)(_msg)

            bubbles = db.load_bubbles(page["id"])
            if bubbles:
                import scanlate
                st.markdown("**Review & adjust bubbles**")
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
                        _kinds = list(scanlate.TEXT_REGION_KINDS)
                        _cur_kind = b.get("kind") or "bubble"
                        rc1, rc2 = st.columns([1, 2])
                        kind = rc1.selectbox(
                            "Region type", _kinds,
                            index=_kinds.index(_cur_kind) if _cur_kind in _kinds else 0,
                            format_func=lambda k: f"{k} — {scanlate.TEXT_REGION_KINDS[k]}",
                            key=f"bkind_{b['id']}",
                            help="Auto-classified from the region's look (a cheap geometry "
                                 "heuristic, not a trained model) -- correct it here if wrong.")
                        _meta = [f"reading order {b['idx'] + 1}",
                                 b.get("orientation") or "orientation unknown",
                                 f"language {b['language']}" if b.get("language") else None,
                                 (f"panel {b['panel_id'] + 1}" if b.get("panel_id") is not None
                                  else "no panel"),
                                 (f"detector confidence {b['confidence']:.2f}"
                                  if b.get("confidence") is not None else None)]
                        rc2.caption(" · ".join(m for m in _meta if m))
                        include_sfx = bool(b.get("include_sfx"))
                        if kind == "sfx":
                            st.warning("🔊 Sound effect -- left alone by default (the lettering is "
                                       "usually part of the art): not translated, cleaned, or "
                                       "replaced. Review it by hand.")
                            include_sfx = st.checkbox(
                                "Include this SFX in the automated translate/clean/replace pass",
                                value=include_sfx, key=f"bsfx_{b['id']}")
                        source = st.text_area(
                            "Original (OCR) text -- fix any OCR mistakes before translating",
                            value=b["source_text"] or "", height=60, key=f"bsrc_{b['id']}")
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
                        # save_bubbles() replaces every row on the page, so every
                        # region field has to be carried through here, not just
                        # the ones this form edits.
                        edited_bubbles.append({
                            "x": x, "y": y, "w": w, "h": h, "font_size": font_size,
                            "source_text": source, "translated_text": translated, "skip": skip,
                            "font_category": font_category, "kind": kind,
                            "kind_confidence": b.get("kind_confidence"),
                            "confidence": b.get("confidence"), "language": b.get("language"),
                            "orientation": b.get("orientation"), "panel_id": b.get("panel_id"),
                            "include_sfx": include_sfx,
                        })

                if st.button("🌐 Translate from the source text above",
                             help="Re-translates this page from the OCR text as it's edited above "
                                  "-- use after fixing OCR mistakes. Skipped bubbles and SFX left "
                                  "out of the automated pass aren't sent."):
                    if not sc_api_key:
                        st.warning("Set an API key for the translation engine above first.")
                    else:
                        _engine = _sc_engine()
                        try:
                            with st.spinner("Translating..."):
                                st.session_state[_sc_context_key] = scanlate.translate_page_bubbles(
                                    edited_bubbles, _engine, sc_drama,
                                    previous_context=st.session_state.get(_sc_context_key, ""),
                                    glossary_terms=_sc_glossary, usage_cb=_sc_usage_cb(_engine))
                            db.save_bubbles(page["id"], edited_bubbles)
                            # The text areas are keyed per bubble id and ids change on save,
                            # so the new translations show on the rerun.
                            st.rerun()
                        except Exception as e:
                            st.warning(f"Translation failed ({e}) -- nothing was changed.")

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

                st.markdown("**Preview / render**")
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

            import scanlate
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
                    new_bubble = {"x": mx, "y": my, "w": mw, "h": mh, "font_size": 18,
                                  "source_text": msource, "translated_text": mtext,
                                  "skip": False, "font_category": "regular",
                                  "kind": "bubble", "language": sc_lang}
                    db.save_bubbles(page["id"], db.load_bubbles(page["id"]) + [new_bubble])
                    st.success("Bubble added.")
                    st.rerun()

        with st.expander("📦 Bulk render all pages", expanded=False):
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

        with st.expander("🔎 Bulk find & replace", expanded=False):
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


def add_uploaded_pages(drama_id: int, pages_dir: str, uploads, slice_strips: bool = False):
    """Saves uploaded images/PDFs as the drama's next pages. Returns
    (pages added, PDF pages skipped for having no embedded image)."""
    import shutil
    import tempfile
    import scanlate
    from PIL import Image as PILImage
    next_idx = len(db.list_pages(drama_id))
    added = 0
    pdf_skipped_total = 0

    def add_copy(src_path, ext):
        nonlocal added
        fname = f"page_{next_idx + added:04d}{ext}"
        fpath = os.path.join(pages_dir, fname)
        shutil.copy(src_path, fpath)
        with PILImage.open(fpath) as im:
            w, h = im.size
        db.create_page(drama_id, next_idx + added, os.path.join("pages", fname), w, h)
        added += 1

    for f in uploads:
        ext = os.path.splitext(f.name)[1].lower()
        with tempfile.TemporaryDirectory() as tmp_dir:
            # A fixed temp name: OpenCV can't open non-ASCII paths on Windows.
            tmp_path = os.path.join(tmp_dir, "upload" + ext)
            with open(tmp_path, "wb") as out:
                out.write(f.getbuffer())
            if ext == ".pdf":
                extracted, skipped = scanlate.pdf_to_page_images(tmp_path, tmp_dir)
                for p in extracted:
                    add_copy(p, ".png")
                pdf_skipped_total += len(skipped)
                continue
            with PILImage.open(tmp_path) as im:
                size = im.size
            if slice_strips and scanlate.is_webtoon_strip(*size):
                for p in scanlate.slice_webtoon_to_files(tmp_path, tmp_dir):
                    add_copy(p, ".png")
            else:
                add_copy(tmp_path, ext)
    return added, pdf_skipped_total

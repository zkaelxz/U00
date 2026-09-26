"""
tabs/discover_tab.py -- Discover tab UI, extracted from the former monolithic app.py.
"""
from common import *
import page_fetch
import known_sites
import title_library


def render_discover_tab():
    st.subheader("Known titles library")
    st.caption(
        "A searchable catalog of known baihe/GL/yuri titles -- cataloging info only "
        "(title, author, tags, a short synopsis), not the actual works. Search in any language; "
        "results keep both the original title and an English rendering."
    )

    existing_count = len(db.list_known_titles())
    if existing_count == 0:
        st.info("Library is empty.")
        if st.button("🌱 Load starter titles (a few known baihe audio dramas)"):
            n = title_library.seed_known_titles(db)
            st.success(f"Added {n} starter title(s).")
            st.rerun()

    st.subheader("Search")
    dc1, dc2 = st.columns([3, 1])
    search_query = dc1.text_input("Search (any language)", key="discover_search")
    search_lang_f = dc2.selectbox("Filter language", ["", "zh", "ja", "ko"],
                                    format_func=lambda l: {"": "All", "zh": "Chinese", "ja": "Japanese", "ko": "Korean"}[l],
                                    key="discover_lang")
    results = db.list_known_titles(search=search_query, language=search_lang_f)
    _library_total = len(db.list_known_titles())

    if _library_total == 0:
        st.info("**Your title library is empty, so searching it returns nothing.** "
                "This box searches titles you've saved locally — it is not a web search. "
                "Use the 🌱 button above to load the starter titles, or add titles with the "
                "tools below.")
    elif not results and search_query:
        st.caption(f"No match for '{search_query}' among your {_library_total} saved title(s). "
                  "This searches your local library only.")
    else:
        st.caption(f"{len(results)} of {_library_total} saved title(s)"
                  + (f" matching '{search_query}'" if search_query else ""))
    for t in results:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"**{t['title_original']}**" + (f" / *{t['title_en']}*" if t['title_en'] else ""))
            c1.caption(f"{t['author'] or '—'} · {t['media_type']} · {t['language']} · {t['tags'] or ''}")
            if t['summary_en']:
                c1.write(t['summary_en'])
            if t['source_url']:
                c1.caption(f"Source: [{t['source_name']}]({t['source_url']})")
            if c2.button("➕ Import to my Library", key=f"import_kt_{t['id']}"):
                new_id = db.create_drama(
                    title_en=t['title_en'], title_zh=t['title_original'] if t['language'] == 'zh' else '',
                    author=t['author'], summary=t['summary_en'], source_language=t['language'],
                    media_type=t['media_type'] if t['media_type'] in
                    ('audio_drama', 'video_drama', 'novel', 'manhwa', 'manga', 'manhua', 'asmr', 'other') else 'other',
                )
                st.success(f"Imported as drama #{new_id}. Find it in the Workspace tab.")
            if c2.button("🗑️ Remove", key=f"del_kt_{t['id']}"):
                db.delete_known_title(t['id'])
                st.rerun()

    st.divider()
    st.subheader("🔎 Find a title on the official platforms")
    st.caption(
        "Type a title (English or Chinese) and get real search links across every known "
        "platform. This doesn't search from inside the app -- it builds the searches and "
        "hands you the links, so nothing can be invented or mis-reported. Open the ones "
        "that look right, then use 'Import a title from a URL' below on whatever you find."
    )
    fc1, fc2 = st.columns([3, 1])
    find_query = fc1.text_input("Title to find", key="find_query",
                                 placeholder="女将军和长公主  or  The General and the Princess")
    find_type = fc2.selectbox("Format", ["", "audio_drama", "novel", "manhua", "manhwa", "manga"],
                               format_func=lambda t: "Any" if t == "" else t.replace("_", " ").title(),
                               key="find_type")

    if find_query.strip():
        _find_key = st.session_state.get("settings_claude", "")
        zh_query = find_query
        if _find_key and not any("\u4e00" <= c <= "\u9fff" for c in find_query):
            try:
                _eng = translate_engines.get_engine("claude", _find_key)
                zh_query = title_library.translate_query_to_zh(find_query, _eng)
                if zh_query != find_query:
                    st.caption(f"Searching Chinese platforms for: **{zh_query}**")
            except Exception:
                st.caption("Couldn't translate the query -- searching with your text as typed.")

        links = known_sites.build_search_links(zh_query, content_type=find_type or None)
        for l in links:
            st.markdown(f"- [{l['site']}]({l['url']}) — {l['note']}")

        st.caption("Browse JJWXC's baihe tag directly: "
                  f"[百合 tag listing]({known_sites.jjwxc_tag_url()})")

    st.divider()
    st.subheader("Search baihehub.com (Chinese titles)")
    st.caption(
        "Searches baihehub's books, audio dramas and manhua through the same public "
        "API its own search page uses. If nothing comes back, you'll get a link to run the "
        "same search in your browser instead.")
    st.caption("English queries are translated to Chinese first, since baihehub is a "
              "Chinese-language database. All of this needs a working internet connection.")
    _discover_gemini_free_tier = st.session_state.get("gemini_free_tier", False)
    bh_query = st.text_input("Search query", key="bh_query")
    bh_engine_choice = st.selectbox(
        "Engine (for translation)",
        [e for e in translate_engines.ENGINES if translate_engines.ENGINES[e].supports_reference],
        format_func=lambda e: f"{e} — {translate_engines.engine_picker_label(e, _discover_gemini_free_tier)}",
        key="bh_engine")
    bh_api_key = synced_api_key_input(
        "API key *(required for translated search)*", bh_engine_choice, "bh_api_key")
    if st.button("Search baihehub") and bh_query:
        engine = translate_engines.get_engine(
            bh_engine_choice, bh_api_key,
            free_tier=bh_engine_choice == "gemini" and _discover_gemini_free_tier,
            base_url=(st.session_state.get("settings_ollama_url") or None)
            if bh_engine_choice == "ollama" else None
        ) if bh_api_key else None
        zh_query = title_library.translate_query_to_zh(bh_query, engine) if engine else bh_query
        st.caption(f"Searching for: {zh_query}")
        found = title_library.search_baihehub(zh_query)
        if found:
            for r in found:
                st.markdown(f"- [{r['title']}]({r['url']}) — {r['snippet']}")
        else:
            fallback_url = title_library.search_url_fallback(zh_query)
            st.warning(f"No results from automatic search. [Open this search in your browser]({fallback_url}) "
                      "and paste any interesting result page's URL below.")

    st.subheader("📥 Bulk import from a tag/ranking listing page")
    st.caption(
        "For pages that list many titles at once -- e.g. JJWXC's Baihe tag listing "
        "(look for the mic icon marking audio drama adaptations), or Fanjiao's ranking page. "
        "Extracts title/author/tags/audio-drama-indicator only, never chapter or episode "
        "content. Always review the results before committing them to your library."
    )
    with st.expander("📋 Known official platforms"):
        for s in known_sites.KNOWN_SITES:
            st.markdown(f"**[{s['name']}]({s['url']})** — {', '.join(s['content_types'])}")

    bulk_urls_text = st.text_area(
        "Page URL(s) -- one per line for multiple pages of a paginated listing",
        placeholder="https://www.jjwxc.net/tag.php?tag=百合\nhttps://www.jjwxc.net/tag.php?tag=百合&page=2",
        key="bulk_urls")
    bulk_source_name = st.text_input("Source label (for your own reference)", value="jjwxc_baihe_tag",
                                      key="bulk_source_name")
    bulk_engine_choice = st.selectbox(
        "Engine",
        [e for e in translate_engines.ENGINES if translate_engines.ENGINES[e].supports_reference],
        format_func=lambda e: f"{e} — {translate_engines.engine_picker_label(e, _discover_gemini_free_tier)}",
        key="bulk_engine")
    bulk_api_key = synced_api_key_input("API key *(required)*", bulk_engine_choice, "bulk_api_key")
    _bulk_free_tier = bulk_engine_choice == "gemini" and _discover_gemini_free_tier
    _bulk_ollama_url = ((st.session_state.get("settings_ollama_url") or None)
                        if bulk_engine_choice == "ollama" else None)

    if st.button("🔍 Extract entries (review before saving)") and bulk_urls_text and bulk_api_key:
        urls = [u.strip() for u in bulk_urls_text.splitlines() if u.strip()]
        engine = translate_engines.get_engine(bulk_engine_choice, bulk_api_key, free_tier=_bulk_free_tier,
                                              base_url=_bulk_ollama_url)
        progress_bar = st.progress(0.0, text="Extracting...")
        entries, statuses = bulk_import.bulk_extract(
            urls, engine, source_name=bulk_source_name,
            progress_cb=lambda frac: progress_bar.progress(frac, text=f"Extracting... {frac*100:.0f}%"))
        progress_bar.empty()
        st.session_state["bulk_extracted_entries"] = entries
        failed = [st_ for st_ in statuses if not st_["ok"]]
        if entries:
            st.success(f"Extracted {len(entries)} unique entries across {len(urls)} page(s).")
        if failed:
            st.warning(f"{len(failed)} of {len(urls)} page(s) couldn't be read:")
            for f in failed:
                st.caption(f"• {f['url']} — {f['message']}")
            if any(f.get("needs_manual") for f in failed):
                st.session_state["show_manual_bulk_paste"] = True

    if st.session_state.get("show_manual_bulk_paste"):
        with st.expander("📋 Manual fallback — paste the page text", expanded=True):
            st.caption(
                "Some sites (baihehub and Fanjiao included) build their listings with "
                "JavaScript, so a plain fetch only gets the page furniture. Open the listing "
                "in your browser, select all (Ctrl+A), copy, and paste below — this always "
                "works. Or install Playwright to let the app render those pages itself "
                "(see the Diagnostics tab).")
            pasted_listing = st.text_area("Pasted listing text", height=200, key="manual_bulk_paste")
            if st.button("Extract from pasted text") and pasted_listing.strip() and bulk_api_key:
                engine_p = translate_engines.get_engine(
                    bulk_engine_choice, bulk_api_key, free_tier=_bulk_free_tier,
                    base_url=_bulk_ollama_url)
                with st.spinner("Extracting..."):
                    manual_entries = bulk_import.extract_listing_from_text(
                        pasted_listing, engine_p, source_name=bulk_source_name)
                for e in manual_entries:
                    e.setdefault("source_url", "pasted")
                    e.setdefault("language", "zh")
                st.session_state["bulk_extracted_entries"] = manual_entries
                st.success(f"Extracted {len(manual_entries)} entries from pasted text.")
                st.rerun()

    extracted = st.session_state.get("bulk_extracted_entries", [])
    if extracted:
        st.subheader(f"Review {len(extracted)} extracted entries")
        st.caption("Uncheck any that look wrong before committing. Audio-drama detection is "
                  "best-effort -- double check the checkbox column if it matters to you.")
        review_df = pd.DataFrame(extracted)
        review_df.insert(0, "Include", True)
        edited_review = st.data_editor(review_df, width='stretch', hide_index=True,
                                        key="bulk_review_editor")
        to_commit = edited_review[edited_review["Include"]].to_dict("records")
        if st.button(f"💾 Add {len(to_commit)} entries to library"):
            n = bulk_import.commit_entries_to_library(db, to_commit, source_name=bulk_source_name)
            st.session_state["bulk_extracted_entries"] = []
            st.success(f"Added {n} title(s) to your library.")
            st.rerun()

    st.divider()
    st.subheader("🌐 Browse a site in-app")
    st.caption(
        "Opens a site inside the app so you can navigate it alongside the Navigator's "
        "translated menu labels. Be aware: most large sites send headers that forbid "
        "embedding, so the panel will often be blank — that's the site refusing, not a bug. "
        "When it is blank, use the Navigator tab and a normal browser tab side by side.")
    embed_url = st.text_input("Site URL to embed", key="embed_url",
                               placeholder="https://baihehub.com/audio-dramas")
    if embed_url:
        verdict = page_fetch.can_probably_embed(embed_url)
        if not verdict["embeddable"]:
            st.error(f"Won't embed: {verdict['reason']}. Open it in a normal browser tab instead.")
        else:
            st.caption(verdict["reason"])
            embed_height = st.slider("Panel height", 400, 1200, 700, 50, key="embed_h")
            st.iframe(embed_url, height=embed_height)

    st.subheader("Import a title from a URL")
    import_url = st.text_input("Listing page URL *(required)*", key="import_url",
                                placeholder="https://baihehub.com/audio-dramas/...")
    import_engine_choice = st.selectbox(
        "Engine",
        [e for e in translate_engines.ENGINES if translate_engines.ENGINES[e].supports_reference],
        format_func=lambda e: f"{e} — {translate_engines.engine_picker_label(e, _discover_gemini_free_tier)}",
        key="import_engine")
    import_api_key = synced_api_key_input(
        "API key *(required)*", import_engine_choice, "import_api_key")
    if st.button("Fetch & add to library") and import_url and import_api_key:
        engine = translate_engines.get_engine(
            import_engine_choice, import_api_key,
            free_tier=import_engine_choice == "gemini" and _discover_gemini_free_tier,
            base_url=(st.session_state.get("settings_ollama_url") or None)
            if import_engine_choice == "ollama" else None)
        with st.spinner("Fetching and extracting..."):
            found, status = title_library.import_title_from_url(import_url, engine)
        if not status["ok"]:
            st.warning(status["message"])
        if found:
            db.create_known_title(**found)
            st.success(f"Added: {found['title_original']}")
            st.rerun()


    with st.expander("✍️ Add a title manually (e.g. Japanese/Korean)"):
        mc1, mc2 = st.columns(2)
        m_title_orig = mc1.text_input("Title (original language)", key="manual_kt_title")
        m_title_en = mc2.text_input("Title (English)", key="manual_kt_title_en")
        m_author = mc1.text_input("Author", key="manual_kt_author")
        m_lang = mc2.selectbox("Language", ["zh", "ja", "ko"], key="manual_kt_lang")
        m_media = mc1.selectbox("Media type", ["novel", "audio_drama", "manhwa", "manga", "manhua", "game"],
                                 key="manual_kt_media")
        m_tags = mc2.text_input("Tags (comma-separated)", key="manual_kt_tags")
        m_summary = st.text_area("Summary", key="manual_kt_summary")
        m_url = st.text_input("Source URL (optional)", key="manual_kt_url")
        if st.button("Add manually"):
            db.create_known_title(title_original=m_title_orig, title_en=m_title_en, author=m_author,
                                   tags=m_tags, summary_en=m_summary, source_url=m_url,
                                   source_name="manual", language=m_lang, media_type=m_media)
            st.success("Added.")
            st.rerun()


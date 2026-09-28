"""
tabs/discover_tab.py -- Discover tab UI, extracted from the former monolithic app.py.

Step 17 folded the former Navigator tab in here as its own collapsible
section ("find a title" and "navigate a site you don't read" are the
same job, just split across two tabs before this step) and made every
section collapsible, since the page was one continuous scroll before.
"""
from common import *
import page_fetch
import known_sites
import title_library


def _known_title_already_imported(t):
    """Step 17 item 3: "Import to my Library" had no duplicate check --
    clicking it twice created two dramas. Matches on the known title's
    own title against existing dramas' title_en/title_zh."""
    candidates = db.list_dramas(search=t['title_en'] or t['title_original'])
    return any(
        (t['title_en'] and d.get('title_en') == t['title_en']) or
        (t['title_original'] and d.get('title_zh') == t['title_original'])
        for d in candidates
    )


def render_discover_tab():
    ui_theme.type_scale_scope()

    # Step 17 item 3: baihehub search, bulk import, and "Import a title
    # from a URL" each had their own engine + API-key picker, and "Find
    # a title" silently hard-coded the Claude key instead of using any
    # of them -- four separate places asking the same question. One
    # shared picker here, reused (plus the folded-in Navigator section
    # below) by everything on this tab that needs an engine.
    _discover_gemini_free_tier = st.session_state.get("gemini_free_tier", False)
    st.caption("Engine used for every AI-assisted action on this tab (translating a "
               "search query, extracting listing entries, translating a site's labels).")
    dc_engine_choice = st.selectbox(
        "Engine",
        [e for e in translate_engines.ENGINES if translate_engines.ENGINES[e].supports_reference],
        format_func=lambda e: f"{e} — {translate_engines.engine_picker_label(e, _discover_gemini_free_tier)}",
        key="discover_engine")
    _dc_needs_key = dc_engine_choice not in translate_engines.FREE_ENGINES
    dc_api_key = synced_api_key_input(
        "API key" + (" *(required for most actions below)*" if _dc_needs_key else " (optional)"),
        dc_engine_choice, "discover_api_key")

    def _discover_engine():
        if _dc_needs_key and not dc_api_key:
            return None
        return translate_engines.get_engine(
            dc_engine_choice, dc_api_key,
            free_tier=dc_engine_choice == "gemini" and _discover_gemini_free_tier,
            base_url=(st.session_state.get("settings_ollama_url") or None)
            if dc_engine_choice == "ollama" else None)

    known_titles_total = len(db.list_known_titles())

    with st.expander("📚 Known titles library"):
        st.caption(
            "A searchable catalog of known baihe/GL/yuri titles -- cataloging info only "
            "(title, author, tags, a short synopsis), not the actual works. Search below in "
            "any language; results keep both the original title and an English rendering."
        )
        if known_titles_total == 0:
            if st.button("🌱 Load starter titles (a few known baihe audio dramas)"):
                n = title_library.seed_known_titles(db)
                st.success(f"Added {n} starter title(s).")
                st.rerun()

    with st.expander("🔍 Search", expanded=True):
        dc_s1, dc_s2 = st.columns([3, 1])
        search_query = dc_s1.text_input("Search (any language)", key="discover_search")
        search_lang_f = dc_s2.selectbox("Filter language", ["", "zh", "ja", "ko"],
                                        format_func=lambda l: {"": "All", "zh": "Chinese", "ja": "Japanese", "ko": "Korean"}[l],
                                        key="discover_lang")
        results = db.list_known_titles(search=search_query, language=search_lang_f)

        if known_titles_total == 0:
            st.info("**Your title library is empty, so searching it returns nothing.** "
                    "This box searches titles you've saved locally — it is not a web search. "
                    "Use the 🌱 button in Known titles library above to load the starter "
                    "titles, or add titles with the tools below.")
        elif not results and search_query:
            st.caption(f"No match for '{search_query}' among your {known_titles_total} saved title(s). "
                      "This searches your local library only.")
        else:
            st.caption(f"{len(results)} of {known_titles_total} saved title(s)"
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
                if _known_title_already_imported(t):
                    c2.caption("Already in your Library")
                elif c2.button("➕ Import to my Library", key=f"import_kt_{t['id']}"):
                    new_id = db.create_drama(
                        title_en=t['title_en'], title_zh=t['title_original'] if t['language'] == 'zh' else '',
                        author=t['author'], summary=t['summary_en'], source_language=t['language'],
                        media_type=t['media_type'] if t['media_type'] in
                        ('audio_drama', 'video_drama', 'novel', 'manhwa', 'manga', 'manhua', 'asmr', 'other') else 'other',
                    )
                    st.success(f"Imported as drama #{new_id}. Find it in the Workspace tab.")
                    st.rerun()
                if c2.button("🗑️ Remove", key=f"del_kt_{t['id']}"):
                    db.delete_known_title(t['id'])
                    st.rerun()

    with st.expander("🔎 Find a title on the official platforms"):
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
            zh_query = find_query
            if not any("一" <= c <= "鿿" for c in find_query):
                # Step 25w: this used to call translate_query_to_zh unconditionally
                # on every Streamlit rerun -- which fires on ANY widget interaction
                # anywhere on the page, not just here -- burning one real translate
                # call per unrelated interaction for as long as this box stayed
                # populated. Cached by the exact query string instead, matching
                # every other LLM call on this tab (all behind a button): an
                # unchanged box now costs nothing on a rerun it didn't cause.
                _find_engine = _discover_engine()
                if _find_engine:
                    _find_cache = st.session_state.get("_find_query_zh_cache")
                    if _find_cache and _find_cache.get("query") == find_query:
                        zh_query = _find_cache["zh"]
                    else:
                        try:
                            zh_query = title_library.translate_query_to_zh(find_query, _find_engine)
                            st.session_state["_find_query_zh_cache"] = {"query": find_query, "zh": zh_query}
                        except Exception:
                            st.caption("Couldn't translate the query -- searching with your text as typed.")
                if zh_query != find_query:
                    st.caption(f"Searching Chinese platforms for: **{zh_query}**")

            links = known_sites.build_search_links(zh_query, content_type=find_type or None)
            for l in links:
                st.markdown(f"- [{l['site']}]({l['url']}) — {l['note']}")

            st.caption("Browse JJWXC's baihe tag directly: "
                      f"[百合 tag listing]({known_sites.jjwxc_tag_url()})")

    with st.expander("🧭 Site navigation helper"):
        st.caption(
            "For sites in a language you don't read (JJWXC, Fanjiao, etc.): paste a public "
            "page's URL, translate its visible menu/labels, and get step-by-step navigation "
            "guidance in your language. This only describes how to use the site's own public "
            "interface -- it doesn't log in, purchase, or fetch anything for you."
        )
        with st.expander("📋 Known official platforms (Chinese / Korean / Japanese)"):
            kc1, kc2 = st.columns(2)
            filter_lang = kc1.selectbox("Language", ["", "zh", "ko", "ja"],
                                         format_func=lambda l: {"": "All", "zh": "Chinese", "ko": "Korean", "ja": "Japanese"}[l],
                                         key="known_site_lang")
            filter_type = kc2.selectbox("Content type", ["", "novel", "manhwa", "manga", "manhua", "audio_drama"],
                                         format_func=lambda t: "All" if t == "" else t.replace("_", " ").title(),
                                         key="known_site_type")
            sites = known_sites.list_sites(content_type=filter_type or None, language=filter_lang or None)
            for s in sites:
                st.markdown(f"**[{s['name']}]({s['url']})** — {s['region']} · {', '.join(s['content_types'])}  \n{s['notes']}")

        nav_known_site = st.selectbox(
            "Start from a known site (optional)",
            ["-- paste a URL manually below --"] + [s["name"] for s in known_sites.KNOWN_SITES],
            key="nav_known_site")
        default_nav_url = ""
        if nav_known_site != "-- paste a URL manually below --":
            default_nav_url = next(s["url"] for s in known_sites.KNOWN_SITES if s["name"] == nav_known_site)

        nav_url = st.text_input("Page URL *(required)*", value=default_nav_url, key="nav_url")
        nav_goal = st.text_area("What are you trying to do? *(required)*",
                                 placeholder="e.g. Find the audio drama section for a specific title, "
                                             "or find the episode list for a show I already found",
                                 key="nav_goal")
        nav_target_lang = st.text_input("Your language", value="English", key="nav_target_lang")

        _nav_missing = []
        if not nav_url:
            _nav_missing.append("a page URL")
        if not nav_goal.strip():
            _nav_missing.append("what you're trying to do (e.g. \"find this title's audio drama page\")")
        if _dc_needs_key and not dc_api_key:
            _nav_missing.append("an API key (set one above, or in the ⚙️ Settings sidebar)")
        if _nav_missing:
            st.info("Still needed: " + "; ".join(_nav_missing) + ".")

        if st.button("🧭 Translate page + get navigation steps", disabled=bool(_nav_missing)):
            import navigator
            engine = _discover_engine()
            try:
                with st.spinner("Fetching page labels..."):
                    labels = navigator.fetch_visible_labels(nav_url)
                with st.spinner("Translating labels..."):
                    translated = navigator.translate_labels(labels, nav_target_lang, engine)
                with st.spinner("Generating navigation steps..."):
                    steps = navigator.generate_navigation_steps(
                        nav_url, nav_goal, nav_target_lang, engine, translated_labels=translated)
                st.subheader("Steps")
                st.markdown(steps)
                with st.expander("Translated page labels"):
                    for orig, trans in translated.items():
                        st.caption(f"{orig} → {trans}")
            except Exception as e:
                st.error(f"Couldn't fetch or process that page: {e}")

    with st.expander("Search baihehub.com (Chinese titles)"):
        st.caption(
            "Searches baihehub's books, audio dramas and manhua through the same public "
            "API its own search page uses. If nothing comes back, you'll get a link to run the "
            "same search in your browser instead.")
        st.caption("English queries are translated to Chinese first, since baihehub is a "
                  "Chinese-language database. All of this needs a working internet connection.")
        bh_query = st.text_input("Search query", key="bh_query")
        if st.button("Search baihehub") and bh_query:
            engine = _discover_engine()
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

    with st.expander("📥 Bulk import from a tag/ranking listing page"):
        st.caption(
            "For pages that list many titles at once -- e.g. JJWXC's Baihe tag listing "
            "(look for the mic icon marking audio drama adaptations), or Fanjiao's ranking page. "
            "Extracts title/author/tags/audio-drama-indicator only, never chapter or episode "
            "content. Always review the results before committing them to your library. See "
            "the Site navigation helper section above for the known-platforms list."
        )

        with st.expander("🔢 Generate URLs from a page-number pattern (optional)"):
            st.caption("For a paginated listing: copy the URL from page 2 of the listing, "
                      "replace the page number with {page}, and this fills in every page's URL.")
            pat1, pat2, pat3 = st.columns([3, 1, 1])
            url_pattern = pat1.text_input(
                "URL pattern (use {page} where the page number goes)",
                placeholder="https://www.jjwxc.net/tag.php?tag=百合&page={page}",
                key="bulk_url_pattern")
            pattern_start = pat2.number_input("Start page", min_value=1, value=1, step=1, key="bulk_pattern_start")
            pattern_end = pat3.number_input("End page", min_value=1, value=5, step=1, key="bulk_pattern_end")
            if st.button("Generate URLs", key="bulk_generate_urls") and "{page}" in url_pattern:
                st.session_state["bulk_urls"] = "\n".join(
                    bulk_import.paginate_urls(url_pattern, int(pattern_start), int(pattern_end)))

        bulk_urls_text = st.text_area(
            "Page URL(s) -- one per line for multiple pages of a paginated listing",
            placeholder="https://www.jjwxc.net/tag.php?tag=百合\nhttps://www.jjwxc.net/tag.php?tag=百合&page=2",
            key="bulk_urls")
        bulk_source_name = st.text_input("Source label (for your own reference)", value="jjwxc_baihe_tag",
                                          key="bulk_source_name")

        _bulk_extract_blocked = _dc_needs_key and not dc_api_key
        if _bulk_extract_blocked:
            st.info("Still needed: an API key (set one above, or in the ⚙️ Settings sidebar).")
        if st.button("🔍 Extract entries (review before saving)", disabled=_bulk_extract_blocked) and bulk_urls_text:
            urls = [u.strip() for u in bulk_urls_text.splitlines() if u.strip()]
            engine = _discover_engine()
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
                _manual_paste_blocked = _dc_needs_key and not dc_api_key
                if _manual_paste_blocked:
                    st.info("Still needed: an API key (set one above, or in the ⚙️ Settings sidebar).")
                if st.button("Extract from pasted text", disabled=_manual_paste_blocked) and pasted_listing.strip():
                    engine_p = _discover_engine()
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

    with st.expander("🌐 Browse a site in-app"):
        st.caption(
            "Opens a site inside the app so you can navigate it alongside the Site navigation "
            "helper section's translated menu labels above. Be aware: most large sites send "
            "headers that forbid embedding, so the panel will often be blank — that's the site "
            "refusing, not a bug. When it is blank, use the Site navigation helper and a normal "
            "browser tab side by side.")
        embed_url = st.text_input("Site URL to embed", key="embed_url",
                                   placeholder="https://baihehub.com/audio-dramas")
        if embed_url:
            verdict = page_fetch.can_probably_embed(embed_url)
            if not verdict["embeddable"]:
                st.error(f"Won't embed: {verdict['reason']}. Open it in a normal browser tab instead.")
            else:
                st.caption(verdict["reason"])
                embed_height = st.slider("Panel height", 400, 1200, 700, 50, key="embed_h")
                # Deliberately not dark-mode themed (Step 68 audit): this is a
                # live third-party site in its own browsing context -- the app
                # can't restyle it, and the site's own theme is what it shows.
                st.iframe(embed_url, height=embed_height)

    with st.expander("Import a title from a URL", expanded=True):
        st.caption(
            "This adds a **catalog entry** here (title/author/tags/synopsis only) -- it "
            "doesn't fetch chapters or episodes. To pull the actual content into a working "
            "drama, use the Sources tab's 🚪 Paste any URL instead.")
        import_url = st.text_input("Listing page URL *(required)*", key="import_url",
                                    placeholder="https://baihehub.com/audio-dramas/...")
        _import_url_blocked = _dc_needs_key and not dc_api_key
        if _import_url_blocked:
            st.info("Still needed: an API key (set one above, or in the ⚙️ Settings sidebar).")
        if st.button("Fetch & add to library", disabled=_import_url_blocked) and import_url:
            engine = _discover_engine()
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

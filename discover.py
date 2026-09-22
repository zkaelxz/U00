"""
tabs/discover.py -- Discover tab UI, extracted from the former monolithic app.py.
"""
from common import *


def render_discover_tab():
    st.subheader("Known titles library")
    st.caption(
        "A searchable catalog of known baihe/GL/yuri titles -- cataloging info only "
        "(title, author, tags, a short synopsis), not the actual works. Search in any language; "
        "results keep both the original title and an English rendering."
    )
    import title_library

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
    st.caption(f"{len(results)} title(s) in your library" + (f" matching '{search_query}'" if search_query else ""))
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
    st.subheader("Search baihehub.com (Chinese titles)")
    st.caption(
        "Searches in English get translated to Chinese first, since baihehub is a Chinese-language "
        "database. baihehub's live search couldn't be verified from this build environment (it's a "
        "client-rendered app) -- if the automatic search comes back empty, use the fallback link to "
        "search in your browser, then paste result page URLs below to import."
    )
    bh_query = st.text_input("Search query", key="bh_query")
    bh_engine_choice = st.selectbox("Engine (for translation)",
                                     [e for e in translate_engines.ENGINES if translate_engines.ENGINES[e].supports_reference],
                                     key="bh_engine")
    bh_api_key = st.text_input("API key", type="password", value=st.session_state.get(f"settings_{bh_engine_choice}", ""), key="bh_api_key")
    if st.button("Search baihehub") and bh_query:
        engine = translate_engines.get_engine(bh_engine_choice, bh_api_key) if bh_api_key else None
        zh_query = title_library.translate_query_to_zh(bh_query, engine) if engine else bh_query
        st.caption(f"Searching for: {zh_query}")
        found = title_library.search_baihehub(zh_query)
        if found:
            for r in found:
                st.markdown(f"- [{r['title']}]({r['url']}) — {r['snippet']}")
        else:
            fallback_url = title_library.search_url_fallback(zh_query)
            st.warning(f"Automatic search unavailable. [Open this search in your browser]({fallback_url}) "
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
    bulk_engine_choice = st.selectbox("Engine",
                                       [e for e in translate_engines.ENGINES if translate_engines.ENGINES[e].supports_reference],
                                       key="bulk_engine")
    bulk_api_key = st.text_input("API key", type="password",
                                  value=st.session_state.get(f"settings_{bulk_engine_choice}", ""),
                                  key="bulk_api_key")

    if st.button("🔍 Extract entries (review before saving)") and bulk_urls_text and bulk_api_key:
        urls = [u.strip() for u in bulk_urls_text.splitlines() if u.strip()]
        engine = translate_engines.get_engine(bulk_engine_choice, bulk_api_key)
        progress_bar = st.progress(0.0, text="Extracting...")
        entries = bulk_import.bulk_extract(
            urls, engine, source_name=bulk_source_name,
            progress_cb=lambda frac: progress_bar.progress(frac, text=f"Extracting... {frac*100:.0f}%"))
        progress_bar.empty()
        st.session_state["bulk_extracted_entries"] = entries
        st.success(f"Extracted {len(entries)} unique entries across {len(urls)} page(s). Review below.")

    extracted = st.session_state.get("bulk_extracted_entries", [])
    if extracted:
        st.subheader(f"Review {len(extracted)} extracted entries")
        st.caption("Uncheck any that look wrong before committing. Audio-drama detection is "
                  "best-effort -- double check the checkbox column if it matters to you.")
        review_df = pd.DataFrame(extracted)
        review_df.insert(0, "Include", True)
        edited_review = st.data_editor(review_df, use_container_width=True, hide_index=True,
                                        key="bulk_review_editor")
        to_commit = edited_review[edited_review["Include"]].to_dict("records")
        if st.button(f"💾 Add {len(to_commit)} entries to library"):
            n = bulk_import.commit_entries_to_library(db, to_commit, source_name=bulk_source_name)
            st.session_state["bulk_extracted_entries"] = []
            st.success(f"Added {n} title(s) to your library.")
            st.rerun()

    st.subheader("Import a title from a URL")
    import_url = st.text_input("Listing page URL (baihehub or elsewhere)", key="import_url")
    import_engine_choice = st.selectbox("Engine",
                                         [e for e in translate_engines.ENGINES if translate_engines.ENGINES[e].supports_reference],
                                         key="import_engine")
    import_api_key = st.text_input("API key", type="password", value=st.session_state.get(f"settings_{import_engine_choice}", ""), key="import_api_key")
    if st.button("Fetch & add to library") and import_url and import_api_key:
        engine = translate_engines.get_engine(import_engine_choice, import_api_key)
        with st.spinner("Fetching and extracting..."):
            found = title_library.import_title_from_url(import_url, engine)
        if found:
            db.create_known_title(**found)
            st.success(f"Added: {found['title_original']}")
            st.rerun()
        else:
            st.warning("Couldn't extract metadata from that page.")

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


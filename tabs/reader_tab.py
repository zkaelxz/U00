"""
tabs/reader.py -- Reader tab UI: raw+translation side-by-side, click-to-
define (now persisted for vocab export), click-to-seek audio, series
glossary display, and in-app Q&A grounded in the drama's transcript.
"""
from common import *
import subtitle_formats


def caption_tracks(lines):
    """The CC tracks for the Watch / listen video player, as
    {label: WebVTT text}. Only languages with at least one non-empty line
    get a track -- an all-blank English track would just be an empty menu
    entry -- and Bilingual only when both sides have something to pair.

    WebVTT rather than SRT: Streamlit sniffs SRT from the first 33 bytes
    and rejects it outright when that cut lands mid-way through a CJK
    character, which a short first cue of Chinese text easily does."""
    tracks = {}
    if any(ln.zh.strip() for ln in lines):
        tracks["Source"] = subtitle_formats.lines_to_vtt(lines, "zh")
    if any(ln.en.strip() for ln in lines):
        tracks["English"] = subtitle_formats.lines_to_vtt(lines, "en")
    if len(tracks) == 2:
        tracks["Bilingual"] = subtitle_formats.lines_to_vtt(lines, "bilingual")
    return tracks


def render_reader_tab():
    st.subheader("Interactive reader")
    st.caption(
        "Raw text with pinyin/furigana annotations, side-by-side with the translation. "
        "Click any word for its reading and definition."
    )
    readable_dramas = [d for d in db.list_dramas() if db.load_lines(d["id"])]
    if not readable_dramas:
        st.info("No dramas with lines yet -- align/translate one in the Workspace tab first.")
        return

    options = {f"#{d['id']} — {d['title_en'] or d['title_zh']}": d for d in readable_dramas}

    # A Resume click from the Library sets active_drama_id. A keyed selectbox
    # otherwise ignores that entirely and keeps whatever was last chosen here,
    # so the resume silently opened the wrong drama. Writing the key directly
    # is what actually moves the selection -- session_state wins over `index`.
    _pending = st.session_state.pop("reader_resume_pending", None)
    if _pending:
        for _label, _d in options.items():
            if _d["id"] == _pending:
                st.session_state["reader_drama_pick"] = _label
                st.session_state["reader_resume_banner"] = _label
                break

    picked_label = st.selectbox("Drama", list(options.keys()), key="reader_drama_pick")

    _banner = st.session_state.pop("reader_resume_banner", None)
    if _banner:
        st.success(f"Resumed **{_banner}** at your saved position.")
    rdrama = options[picked_label]
    rlang = rdrama.get("source_language") or "zh"
    rscript = rdrama.get("chinese_script") or "simplified"

    rows = db.load_lines(rdrama["id"])
    rlines = core_module.lines_from_rows(rows)

    rddir = db.drama_dir(rdrama["id"])
    media_path = None
    if rdrama.get("source_video_filename"):
        p = os.path.join(rddir, rdrama["source_video_filename"])
        if os.path.exists(p):
            media_path = ("video", p)
    if media_path is None and rdrama.get("audio_filename"):
        p = os.path.join(rddir, rdrama["audio_filename"])
        if os.path.exists(p):
            media_path = ("audio", p)
    dub_path = os.path.join(rddir, "dub_track.wav")
    narration_path = os.path.join(rddir, "narration_track.wav")

    if media_path or os.path.exists(dub_path) or os.path.exists(narration_path):
        with st.expander("▶️ Watch / listen", expanded=True):
            if media_path:
                kind, p = media_path
                st.caption("Original")
                if kind == "video":
                    # Built from this page load's lines, not an export file,
                    # so Workspace edits show up here without re-exporting.
                    # st.audio has no subtitles parameter, so audio-only
                    # dramas get the plain player.
                    st.video(p, subtitles=caption_tracks(rlines) or None)
                else:
                    st.audio(p)
                    _caption_tracks = caption_tracks(rlines)
                    if _caption_tracks:
                        # Step 45: an audio-only source (no video file was ever
                        # kept for this drama, e.g. a URL download made with
                        # "Audio only" checked) has no way to overlay timed
                        # captions the way the video player above does --
                        # that's a real Streamlit limitation, not a bug, but
                        # leaving it unexplained reads as "the subtitles are
                        # just missing." Show the same text as a plain,
                        # playback-unsynced readout instead of nothing at all.
                        st.caption("This source has no video file, so captions can't be "
                                   "overlaid on the player the way they are for a video -- "
                                   "here's the same text, not synced to playback:")
                        _cap_label = st.radio(
                            "Captions", list(_caption_tracks.keys()), horizontal=True,
                            key=f"reader_audio_captions_lang_{rdrama['id']}")
                        _cap_field = {"Source": "zh", "English": "en", "Bilingual": "bilingual"}[_cap_label]
                        with st.container(height=200):
                            for ln in rlines:
                                _txt = f"{ln.en}  \n{ln.zh}" if _cap_field == "bilingual" \
                                    else getattr(ln, _cap_field)
                                if _txt.strip():
                                    st.caption(f"`{fmt_ts(ln.start)[3:8]}` {_txt}")
            if os.path.exists(dub_path):
                st.caption("AI dub")
                st.audio(dub_path)
            if os.path.exists(narration_path):
                st.caption("AI narration")
                st.audio(narration_path)

    profile_id = get_active_profile_id()
    prog = db.get_progress(rdrama["id"], profile_id=profile_id)
    est = (story_context.estimate_listening_time(rlines) if rdrama.get("media_type") in
           ("audio_drama", "video_drama", "asmr")
           else story_context.estimate_reading_time(rlines))
    ic1, ic2, ic3 = st.columns(3)
    ic1.metric("Length", est["display"])
    ic2.metric("Progress", f"{(prog or {}).get('percent_complete', 0):.0f}%")
    ic3.metric("Lines", f"{len(rlines):,}")
    if prog and prog.get("percent_complete", 0) > 0:
        st.progress(min(1.0, prog["percent_complete"] / 100.0))

    c1, c2 = st.columns([1, 3])
    chapter_size = c1.number_input("Lines per page", value=40, min_value=10, max_value=200, step=10)
    n_pages = max(1, (len(rlines) + chapter_size - 1) // chapter_size)
    _resume_page = st.session_state.pop("reader_jump_page", None) or (prog or {}).get("last_page") or 1
    page = c2.number_input(f"Page (1-{n_pages})", value=min(int(_resume_page), n_pages),
                            min_value=1, max_value=n_pages, step=1)
    page_lines = rlines[(page - 1) * chapter_size: page * chapter_size]

    if page_lines:
        _last_idx = page_lines[-1].idx
        db.save_progress(rdrama["id"], profile_id=profile_id, last_line_idx=_last_idx, last_page=int(page),
                         percent_complete=story_context.compute_percent_complete(_last_idx, len(rlines)))

    cache_key = f"reader_defs_{rdrama['id']}_{page}"
    reader_api_key = synced_api_key_input(
        "API key for definitions on unrecognized words (optional for Chinese, "
        "needed for Japanese/Korean)", "claude", f"reader_key_{rdrama['id']}")

    embed_audio = st.checkbox("Enable click-to-seek audio for this page (embeds a clip of this "
                              "page's audio span -- needs original audio, keep pages reasonably "
                              "short for this)", value=False)

    if st.button("📖 Load / refresh this page"):
        import segment as segment_module
        with st.spinner("Segmenting text..."):
            all_words = []
            for ln in page_lines:
                for word, _reading in segment_module.segment_and_annotate(ln.zh, rlang, chinese_script=rscript):
                    if word.strip():
                        all_words.append(word)
        with st.spinner("Looking up definitions..."):
            engine = translate_engines.get_engine("claude", reader_api_key) if reader_api_key else None
            defs = dictionary.build_word_definitions(
                all_words, [ln.zh for ln in page_lines], engine, source_language=rlang)
        st.session_state[cache_key] = defs

        # Persist every definition looked up on this page for vocab export --
        # not just ones actually clicked, since we already computed them all.
        for word, entry in defs.items():
            first_line = next((ln.idx for ln in page_lines if word in ln.zh), None)
            db.save_vocab_lookup(rdrama["id"], word, entry.get("reading"),
                                  entry.get("definitions", []), rlang, first_line)
        st.success(f"Loaded definitions for {len(defs)} word(s) (saved to your vocab library).")

    defs = st.session_state.get(cache_key, {})

    if defs:
        with st.expander("🎴 Queue words for a richer Anki export (sentence + audio)"):
            st.caption(
                "Pick specific words from this page's definitions to add a richer "
                "card for -- front = the full sentence it appeared in (with an "
                "audio clip, for a drama with a source audio track), back = the "
                "line's translation plus the word's definition and reading. "
                "Separate from the plain word-only export below, which still "
                "covers every word looked up in this drama.")
            already_rich = {r["word"] for r in db.list_vocab_lookups(rdrama["id"], rich_only=True)}
            pick_words = st.multiselect(
                "Words defined on this page", options=sorted(defs.keys()),
                default=[], key=f"rich_pick_{rdrama['id']}_{page}")
            if st.button("➕ Add to rich Anki export queue", key=f"rich_add_{rdrama['id']}_{page}"):
                if pick_words:
                    for w in pick_words:
                        db.set_vocab_export_rich(rdrama["id"], w, True)
                    st.success(f"Queued {len(pick_words)} word(s) for the richer export.")
                else:
                    st.warning("Pick at least one word first.")
            if already_rich:
                st.caption(f"{len(already_rich)} word(s) already queued for this drama.")

    audio_data_uri = None
    if embed_audio and media_path and media_path[0] == "audio" and page_lines:
        with st.spinner("Preparing audio clip for this page..."):
            audio_data_uri = reader_module.build_page_audio_data_uri(
                media_path[1], page_lines[0].start, page_lines[-1].end)
        if audio_data_uri is None:
            st.warning("Couldn't prepare an audio clip for this page (check ffmpeg is installed).")

    spoiler_free = st.session_state.get("spoiler_free_mode", True)
    spoiler_idx = (page_lines[-1].idx if page_lines else len(rlines) - 1) if spoiler_free else len(rlines) - 1
    scoped_lines = [ln for ln in rlines if ln.idx <= spoiler_idx]
    if spoiler_free:
        st.caption(f"🙈 Spoiler-free mode: AI features limited to line {spoiler_idx + 1} "
                  f"of {len(rlines)}. Toggle in ⚙️ Settings.")

    if page_lines:
        html_str = reader_module.build_reader_html(
            page_lines, rlang, defs, audio_data_uri=audio_data_uri,
            theme=ui_theme.resolve_reader_theme(st.session_state.get("reader_theme", "match app"),
                                                bool(st.session_state.get("app_dark_mode"))),
            font_size=st.session_state.get("reader_font_size", 22),
            line_height=st.session_state.get("reader_line_height", 2.4),
            max_width=st.session_state.get("reader_max_width", 1200),
            font=st.session_state.get("reader_font", "system"))
        st.iframe(html_str, height=min(900, 200 + 120 * len(page_lines)))
    else:
        st.info("No lines on this page.")

    # ---------------------------------------------------- Series glossary
    if rdrama.get("series_id"):
        with st.expander("📖 Series glossary"):
            st.caption("Shared across every drama in this series -- see 🎭 Series in "
                      "📚 Library for the full list.")
            terms = db.list_glossary_terms(rdrama["series_id"])
            if terms:
                for t in terms:
                    st.caption(f"**{t['term_original']}** → {t['term_translation']}"
                              + (f" _{t['notes']}_" if t.get("notes") else ""))
            else:
                st.caption("No glossary terms yet for this series. Add some in the Workspace tab.")

    st.divider()
    with st.popover("📖 Story", width="stretch"):
        with st.expander("🧠 Story tools", expanded=True):
            st.caption("Grounded strictly in this drama's own lines -- these won't invent plot, "
                      "and recaps only cover what you've already reached.")
            story_key = synced_api_key_input("API key", "claude", f"story_key_{rdrama['id']}")

            stc1, stc2 = st.columns(2)
            with stc1:
                char_q = st.text_input("Who is this character?", placeholder="e.g. 沈清疑 or Shen Qingyi",
                                        key=f"charq_{rdrama['id']}")
                if st.button("Look up character") and char_q and story_key:
                    eng = translate_engines.get_engine("claude", story_key)
                    with st.spinner("Reading..."):
                        st.session_state[f"char_ans_{rdrama['id']}"] = story_context.who_is_character(
                            char_q, scoped_lines, rdrama, eng)
                if st.session_state.get(f"char_ans_{rdrama['id']}"):
                    st.info(st.session_state[f"char_ans_{rdrama['id']}"])

            with stc2:
                ref_q = st.text_input("Explain an idiom or reference",
                                       placeholder="e.g. 一石二鸟", key=f"refq_{rdrama['id']}")
                if st.button("Explain") and ref_q and story_key:
                    eng = translate_engines.get_engine("claude", story_key)
                    with st.spinner("Looking it up..."):
                        st.session_state[f"ref_ans_{rdrama['id']}"] = story_context.explain_reference(
                            ref_q, scoped_lines, eng, source_language=rlang)
                if st.session_state.get(f"ref_ans_{rdrama['id']}"):
                    st.info(st.session_state[f"ref_ans_{rdrama['id']}"])

            rc1, rc2 = st.columns(2)
            if rc1.button("📖 Recap what I've read so far") and story_key:
                eng = translate_engines.get_engine("claude", story_key)
                with st.spinner("Summarizing..."):
                    st.session_state[f"recap_{rdrama['id']}"] = story_context.summarize_section(
                        rlines[:(page - 1) * chapter_size] or rlines[:chapter_size], eng,
                        section_label=f"up to page {page}")
            if st.session_state.get(f"recap_{rdrama['id']}"):
                st.success(st.session_state[f"recap_{rdrama['id']}"])

            if rc2.button("🕸️ Build relationship map") and story_key:
                eng = translate_engines.get_engine("claude", story_key)
                with st.spinner("Mapping the cast..."):
                    st.session_state[f"relmap_{rdrama['id']}"] = story_context.build_relationship_map(
                        scoped_lines, eng)
            relmap = st.session_state.get(f"relmap_{rdrama['id']}")
            if relmap and relmap.get("characters"):
                for c in relmap["characters"]:
                    st.caption(f"**{c.get('name')}** — _{c.get('role','')}_: {c.get('description','')}")
                mermaid = story_context.relationship_map_to_mermaid(relmap)
                if mermaid:
                    with st.expander("Diagram (Mermaid source)"):
                        st.code(mermaid, language="mermaid")

        with st.expander("📚 Universe wiki"):
            st.caption("An encyclopedia of characters, places, sects, artifacts, and concepts that "
                      "grows as you read. Entries introduced after your current position stay hidden "
                      "while spoiler-free mode is on.")
            wc1, wc2 = st.columns([1, 1])
            if wc1.button("🔄 Update wiki from what I've read") and story_key:
                eng = translate_engines.get_engine("claude", story_key)
                with st.spinner("Reading and cataloguing..."):
                    found = universe_wiki.extract_wiki_entries(
                        scoped_lines, eng, spoiler_idx, rdrama,
                        existing_entries=db.list_wiki_entries(rdrama["id"]))
                for e in found:
                    db.upsert_wiki_entry(
                        rdrama["id"], e["entry_type"], e["name"],
                        description=e.get("description"), aliases=e.get("aliases"),
                        attributes=e.get("attributes"),
                        first_seen_line_idx=e.get("first_seen_line_idx"),
                        known_through_line_idx=e.get("known_through_line_idx"))
                st.success(f"Wiki updated with {len(found)} entr(ies).")
                st.rerun()
            if wc2.button("🗑️ Clear wiki"):
                db.clear_wiki(rdrama["id"])
                st.rerun()

            wiki_entries = db.list_wiki_entries(
                rdrama["id"], spoiler_limit_line_idx=spoiler_idx if spoiler_free else None)
            if wiki_entries:
                type_filter = st.selectbox("Show", ["all"] + list(universe_wiki.ENTRY_TYPES.keys()),
                                            key=f"wikitype_{rdrama['id']}")
                shown = [e for e in wiki_entries if type_filter == "all" or e["entry_type"] == type_filter]
                st.caption(f"{len(shown)} entr(ies)")
                for e in shown:
                    with st.expander(f"{e['entry_type']} · {e['name']}"):
                        if e.get("aliases"):
                            st.caption(f"_Also known as: {e['aliases']}_")
                        if e.get("description"):
                            st.write(e["description"])
                        for k, v in (e.get("attributes") or {}).items():
                            st.caption(f"**{k}**: {v}")
                        if e.get("first_seen_line_idx") is not None:
                            st.caption(f"First appears around line {e['first_seen_line_idx'] + 1}")
                st.download_button(
                    "📄 Download wiki as Markdown",
                    universe_wiki.format_wiki_as_markdown(
                        shown, rdrama["title_en"] or rdrama["title_zh"] or "",
                        spoiler_note=(f"Built from lines 1–{spoiler_idx + 1}." if spoiler_free else "")),
                    file_name="universe_wiki.md")
            else:
                st.caption("No entries yet -- click 'Update wiki' above.")
        with st.expander("💬 Ask about this drama"):
            st.caption("Grounded in the transcript/translation loaded on this page. Doesn't invent plot "
                      "details -- if the answer isn't in what's loaded, it'll say so.")
            qa_key = f"qa_history_{rdrama['id']}"
            if qa_key not in st.session_state:
                st.session_state[qa_key] = []

            qa_api_key = synced_api_key_input("API key", "claude", f"qa_key_{rdrama['id']}")
            for msg in st.session_state[qa_key]:
                st.chat_message(msg["role"]).write(msg["content"])

            question = st.chat_input("Ask a question about this drama...")
            if question and qa_api_key:
                st.session_state[qa_key].append({"role": "user", "content": question})
                st.chat_message("user").write(question)
                engine = translate_engines.get_engine("claude", qa_api_key)
                with st.spinner("Thinking..."):
                    answer = qa.ask_about_drama(question, rlines, rdrama, engine,
                                                  chat_history=st.session_state[qa_key][:-1])
                st.session_state[qa_key].append({"role": "assistant", "content": answer})
                st.chat_message("assistant").write(answer)
            elif question:
                st.warning("Enter an API key above first.")

    st.divider()
    with st.expander("🗒️ My notes"):
        # Step 26e: notes are private per profile now, not one field shared
        # by everyone with access to this drama -- the widget key includes
        # profile_id too, or switching profiles wouldn't refresh this box
        # (Streamlit only applies `value=` on a widget key's very first
        # render, same bug common.py's synced_api_key_input documents for
        # API keys).
        notes_val = st.text_area("Private notes on this drama",
                                  value=db.get_personal_notes(rdrama["id"], profile_id=profile_id),
                                  height=100, key=f"pnotes_{rdrama['id']}_{profile_id}")
        if st.button("Save notes"):
            db.save_personal_notes(rdrama["id"], notes_val, profile_id=profile_id)
            st.success("Saved.")

    st.divider()
    with st.expander("📇 Vocabulary export"):
        vocab = db.list_vocab_lookups(rdrama["id"])
        st.caption(f"{len(vocab)} word(s) looked up so far in this drama.")
        if vocab:
            vc1, vc2 = st.columns(2)
            csv_text = vocab_export.export_vocab_csv(vocab)
            vc1.download_button("Download as CSV (Anki-importable)", csv_text,
                                 file_name=f"{rdrama['title_en'] or rdrama['title_zh'] or 'vocab'}.csv")
            if vc2.button("Generate Anki .apkg"):
                try:
                    out_path = os.path.join(rddir, "vocab.apkg")
                    vocab_export.export_vocab_apkg(
                        vocab, rdrama["title_en"] or rdrama["title_zh"] or "Baihe Vocab", out_path)
                    with open(out_path, "rb") as f:
                        st.download_button("Download .apkg", f.read(), file_name="vocab.apkg")
                except ImportError:
                    st.warning("Needs `pip install genanki` for .apkg export -- CSV works without it.")

            rich_vocab = db.list_vocab_lookups(rdrama["id"], rich_only=True)
            st.divider()
            st.caption(
                f"{len(rich_vocab)} word(s) queued above for the richer sentence+audio card. "
                + ("This drama has a source audio track, so those cards include an audio "
                   "clip of the sentence." if media_path else
                   "This drama has no source audio track, so those cards are text-only "
                   "(sentence + translation + definition), no audio clip."))
            if rich_vocab and st.button("🎴 Generate rich Anki .apkg (sentence + audio)"):
                try:
                    out_path = os.path.join(rddir, "vocab_sentence.apkg")
                    vocab_export.export_vocab_apkg_sentence(
                        rich_vocab, rlines,
                        (rdrama["title_en"] or rdrama["title_zh"] or "Baihe Vocab") + " (sentences)",
                        out_path, audio_path=media_path[1] if media_path else None)
                    with open(out_path, "rb") as f:
                        st.download_button("Download sentence .apkg", f.read(),
                                            file_name="vocab_sentence.apkg")
                except ImportError:
                    st.warning("Needs `pip install genanki` for .apkg export -- CSV works without it.")

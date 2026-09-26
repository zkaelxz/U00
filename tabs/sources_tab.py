"""
tabs/sources_tab.py -- the Sources tab (roadmap Steps 23/23b): paste any
URL, search registered sources, browse a series' chapters and import the
ones you pick, watch what the app is doing to a source while it does it,
and see each source's health, capabilities and diagnostics.

All the logic lives in the `sources` package; this file is only UI. Pages
and text land in the same places Scanlate's upload and Workspace's
raw-novel upload put them, so nothing downstream changes.
"""
from common import *

import background_jobs
from sources import (adaptive, ai_extract, auth_browser, cache as src_cache, chapter_check,
                     chapter_order, front_door, generic_import, health, ladder as src_ladder,
                     pipeline, profiles as src_profiles, registry, store as src_store)
from sources.models import (AccessTier, CapabilityStatus, CHALLENGE_HANDOFF_MESSAGE,
                            ChallengeDetected, FailureReason, NotSupportedError, SourceError,
                            TermsProhibited)

_COMIC_MEDIA = ("manhua", "manga", "manhwa")


def _drama_picker(label, key, media_filter=None, warn_audio=False):
    dramas = db.list_dramas()
    if media_filter:
        preferred = [d for d in dramas if d.get("media_type") in media_filter]
        dramas = preferred + [d for d in dramas if d not in preferred]
    if not dramas:
        st.info("Create a drama in the Workspace tab first -- imported content is filed under one.")
        return None
    options = {}
    for d in dramas:
        tag = f" ({d.get('media_type') or '?'})"
        if warn_audio and d.get("audio_filename"):
            tag += " ⚠️ has audio"
        options[f"#{d['id']} — {d['title_en'] or d['title_zh'] or '(untitled)'}" + tag] = d["id"]
    return options[st.selectbox(label, list(options.keys()), key=key)]


def _render_handoff(handoff: dict, key: str, on_resume=None):
    """Item 2's hand-off screen: stop, say what was hit, give the person
    Open in Browser / Retry / Cancel -- and a way to resume from the page
    they reached themselves."""
    st.warning(f"🛑 {CHALLENGE_HANDOFF_MESSAGE}\n\nDetected: **{handoff['reason']}**"
               f" at {handoff.get('tier', 'STATIC_HTTP')}.")
    c1, c2, c3 = st.columns(3)
    c1.link_button("🌐 Open in Browser", handoff["url"])
    retry = c2.button("🔁 Retry", key=f"{key}_retry",
                      help="Sends one new request -- only because you asked. Nothing retries on its own.")
    cancel = c3.button("✖️ Cancel", key=f"{key}_cancel")
    if on_resume is not None:
        pasted = st.text_area(
            "Finished the verification? Paste the page's source here to continue from it",
            key=f"{key}_paste", height=120,
            help="In your browser: right-click the page → View page source → copy all. The app "
                 "reads what you paste; it doesn't contact the site for this step.")
        if st.button("▶️ Continue from pasted page", key=f"{key}_resume", disabled=not pasted.strip()):
            on_resume(pasted)
    return retry, cancel


def _render_front_door():
    st.caption("Paste a chapter, series, novel-chapter or video link. The app works out what it "
               "is and shows a preview first -- nothing is imported until you press Import.")
    url = st.text_input("URL", key="src_fd_url", placeholder="https://...")
    if st.button("🔍 Preview", key="src_fd_preview", disabled=not url.strip()):
        with st.spinner("Looking at the page (paced like every other request)..."):
            try:
                st.session_state.src_fd_result = front_door.preview(url.strip())
            except ChallengeDetected as e:
                st.session_state.src_fd_result = None
                st.session_state.src_fd_handoff = {"url": e.url, "reason": e.reason.value}
            except SourceError as e:
                st.session_state.src_fd_result = None
                st.error(f"{e.reason.value}: {e}")

    handoff = st.session_state.get("src_fd_handoff")
    p = st.session_state.get("src_fd_result")
    if p is not None and p.ladder is not None and p.ladder.handoff:
        handoff = p.ladder.handoff
    if handoff:
        def resume(pasted):
            st.session_state.src_fd_result = front_door.classify_html(handoff["url"], pasted)
            st.session_state.src_fd_result.html = pasted
            st.session_state.src_fd_user_html = pasted
            st.session_state.src_fd_handoff = None
            st.rerun()
        retry, cancel = _render_handoff(handoff, "src_fd", on_resume=resume)
        if retry:
            st.session_state.src_fd_handoff = None
            try:
                st.session_state.src_fd_result = front_door.preview(handoff["url"])
            except ChallengeDetected as e:
                st.session_state.src_fd_result = None
                st.session_state.src_fd_handoff = {"url": e.url, "reason": e.reason.value}
            except SourceError as e:
                st.session_state.src_fd_result = None
                st.error(f"{e.reason.value}: {e}")
            st.rerun()
        if cancel:
            st.session_state.src_fd_handoff = None
            st.session_state.src_fd_result = None
            st.rerun()
        return
    if p is None:
        return

    st.markdown(f"**{p.title or '(no title found)'}**")
    cols = st.columns(5)
    cols[0].metric("Type", p.content_type)
    cols[1].metric("Platform", p.platform or "—")
    cols[2].metric("Chapter", p.chapter or "—")
    cols[3].metric("Language", p.language or "—")
    cols[4].metric("Chapters", p.chapter_count if p.chapter_count is not None else "—")
    for note in p.notes:
        st.caption(note)

    user_html = st.session_state.get("src_fd_user_html")
    if p.adapter and p.series_id:
        if st.button("📚 Open in the series browser", key="src_fd_open_series"):
            st.session_state.src_series = (p.adapter, p.series_id)
            st.rerun()
    elif p.content_type == front_door.VIDEO:
        drama_id = _drama_picker("Into drama", "src_fd_video_drama", warn_audio=True)
        audio_only = st.checkbox("Audio only (recommended)", value=True, key="src_fd_audio_only")
        target = p.url
        if not p.adapter and not front_door.is_video_url(p.url) and p.html:
            target = _render_media_identify(p) or p.url
        existing_audio = (db.get_drama(drama_id) or {}).get("audio_filename") if drama_id else None
        confirm_overwrite = True
        if existing_audio:
            confirm_overwrite = st.checkbox(
                f"I understand this replaces drama #{drama_id}'s existing audio ({existing_audio}) "
                "-- any transcription, translation or dub already done for it will no longer "
                "match what's on disk",
                key=f"src_fd_video_confirm_overwrite_{drama_id}")
        if drama_id and st.button("⬇️ Import video", key="src_fd_import_video",
                                  disabled=not confirm_overwrite):
            bar = st.progress(0.0)
            status = st.empty()
            try:
                import video_download
                front_door.import_video(
                    target, drama_id, audio_only=audio_only,
                    progress_cb=lambda f, m: (bar.progress(min(f, 1.0)), status.caption(m)),
                    cookies_browser=st.session_state.get("settings_cookies_browser"),
                    cookies_file=st.session_state.get("settings_cookies_file") or None)
                st.success("Downloaded -- open the drama in Workspace to transcribe it.")
            except TermsProhibited as exc:
                st.error(str(exc))
            except ImportError as exc:
                st.error(str(exc))
            except video_download.DownloadError as exc:
                st.error(str(exc))
    elif p.content_type == front_door.COMIC:
        engine = _ai_fallback_engine()
        drama_id = _drama_picker("Add pages to drama", "src_fd_comic_drama", _COMIC_MEDIA)
        if drama_id and st.button("➕ Import pages into Scanlate", key="src_fd_import_comic"):
            with st.spinner("Downloading and checking each image..."):
                try:
                    res, report = adaptive.import_comic(p.url, engine, user_html=user_html)
                except (generic_import.NoContentFound, TermsProhibited) as e:
                    st.error(str(e))
                    st.session_state.src_fd_report = getattr(e, "report", None)
                    res = None
            if res is not None and res.ladder.handoff:
                st.session_state.src_fd_handoff = res.ladder.handoff
                st.rerun()
            elif res is not None:
                st.session_state.src_fd_report = report
                if report.needs_review or _diagnostics_mode():
                    st.session_state.src_fd_review = {
                        "kind": "comic", "url": p.url, "html": res.ladder.html,
                        "data": report.data, "report": report, "drama_id": drama_id,
                        "candidates": res.images + res.rejected, "nonce": f"_{id(report)}"}
                    st.rerun()
                n = pipeline.add_page_images(drama_id, [(c.content, c.ext) for c in res.images])
                st.success(f"Added {n} page(s) to the drama -- they're in the Scanlate tab now.")
                if res.rejected:
                    with st.expander(f"{len(res.rejected)} image(s) skipped as page furniture"):
                        for c in res.rejected:
                            st.caption(f"{c.url} — {c.reject_reason}")
    elif p.content_type == front_door.NOVEL:
        engine = _ai_fallback_engine()
        drama_id = _drama_picker("Save text to drama", "src_fd_novel_drama", ("novel",))
        append = st.checkbox("Append to the drama's existing raw novel text", value=True,
                             key="src_fd_append")
        if drama_id and st.button("📥 Import text", key="src_fd_import_novel"):
            try:
                res, report = adaptive.import_novel(p.url, engine, user_html=user_html)
            except (generic_import.NoContentFound, TermsProhibited) as e:
                st.error(str(e))
                st.session_state.src_fd_report = getattr(e, "report", None)
                res = None
            if res is not None and res.ladder.handoff:
                st.session_state.src_fd_handoff = res.ladder.handoff
                st.rerun()
            elif res is not None:
                st.session_state.src_fd_report = report
                if report.needs_review or _diagnostics_mode():
                    st.session_state.src_fd_review = {
                        "kind": "novel", "url": p.url, "html": res.ladder.html,
                        "data": report.data, "report": report, "drama_id": drama_id,
                        "append": append, "nonce": f"_{id(report)}"}
                    st.rerun()
                pipeline.save_novel_text(drama_id, res.text, append=append, heading=res.title)
                st.success(f"Saved {len(res.text):,} characters (extracted with {res.method}) as "
                           "the drama's raw novel text -- the same place Workspace's raw-novel "
                           "upload uses.")
                st.download_button("💾 Download as .txt", res.text.encode("utf-8"),
                                   file_name="chapter.txt", key="src_fd_novel_dl")
    else:
        st.info("Couldn't tell what this page is. If it's a comic or novel chapter, upload it "
                "manually in Scanlate or Workspace instead.")
    review = st.session_state.get("src_fd_review")
    if review and review["url"] == p.url:
        with st.container(border=True):
            _render_review_extraction(review)
    if p.ladder is not None:
        with st.expander("How the page was reached"):
            for line in p.ladder.summary_lines():
                st.caption(line)
    report = st.session_state.get("src_fd_report")
    if p.content_type != front_door.VIDEO and not p.series_id:
        needs = bool(p.ladder is not None and not p.ladder.ok and
                     set(p.ladder.reasons) & _SIGN_IN_HINTS) or bool(
            report is not None and report.url == p.url and report.access_tier is None and
            (report.access.get("authentication_required") or report.access.get("purchase_required")))
        with st.expander("🔐 Sign in to this site", expanded=needs):
            if _render_sign_in(p.url, "src_fd"):
                st.session_state.src_fd_result = front_door.preview(p.url)
                st.rerun()
    if report is not None and report.url == p.url:
        with st.expander("🩺 Source diagnostics", expanded=_diagnostics_mode()):
            _render_report(report)


# ---------------------------------------------------------------------------
# Step 23k: signing in through a real browser window
# ---------------------------------------------------------------------------

_SIGN_IN_HINTS = {FailureReason.AUTHENTICATION_REQUIRED, FailureReason.PURCHASE_REQUIRED,
                  FailureReason.COOKIE_REQUIRED, FailureReason.EMPTY_SPA_SHELL}


def _render_sign_in(url: str, key: str, source: str = None) -> bool:
    """Item 2's manual-login flow. Returns True right after a sign-in
    that the app then confirmed can see the page."""
    source = source or auth_browser.source_for(url)
    saved = auth_browser.has_profile(url, source)
    st.info(auth_browser.LOGIN_PROMPT)
    st.caption("You sign in yourself, in a real browser window on the computer running Baihe "
               "Studio -- the app never sees your password, never solves a CAPTCHA or MFA for "
               "you, and never gets around a purchase check. Close the window once the chapter "
               "is open; the app then checks it can really see that page before importing "
               "anything. Your sign-in stays in this site's own browser profile (never shown, "
               "logged, sent to an AI engine, or put in a library backup), so later imports "
               "don't ask again.")
    if saved:
        st.caption("✅ A saved sign-in exists for this site -- its pages are already read "
                   "through it.")
    c1, c2 = st.columns(2)
    confirmed = False
    if c1.button("🔐 Open browser to sign in", key=f"{key}_login"):
        with st.spinner("Waiting for you in the browser window -- close it once the chapter "
                        "is open. There's no time limit."):
            try:
                cls = registry.adapter_class_for_url(url)
                check = cls().login(url) if cls else auth_browser.manual_login(url, source)
            except TermsProhibited as e:
                st.error(str(e))
                return False
            except ImportError as e:
                st.error(str(e))
                return False
            except Exception as e:
                st.error(translate_engines.redact_secrets(f"The sign-in window failed: {e}"))
                return False
        st.session_state[f"{key}_login_check"] = check
        confirmed = check.ok
    check = st.session_state.get(f"{key}_login_check")
    if check is not None and check.url == url:
        (st.success if check.ok else st.warning)(check.message)
        for line in check.lines:
            st.caption(f"· {line}")
    if saved and c2.button("🗑️ Forget this site's sign-in", key=f"{key}_forget",
                           help="Deletes the saved browser profile. Imports go back to "
                                "reading the site signed out."):
        try:
            auth_browser.forget(url, source)
        except Exception as e:
            st.error(str(e))
        else:
            st.session_state.pop(f"{key}_login_check", None)
            st.rerun()
    return confirmed


# ---------------------------------------------------------------------------
# Step 23g: AI-assisted fallback, Review Extraction, Source Diagnostics
# ---------------------------------------------------------------------------

_BUCKET_ICON = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🟠", "FAILED": "🔴"}


def _diagnostics_mode() -> bool:
    return bool(src_store.get_setting("extraction_diagnostics"))


def _ai_fallback_engine():
    """The optional engine for the AI tier. Off by default -- then the
    ladder never makes an AI call and says so in diagnostics."""
    names = ["off"] + [e for e, cls in translate_engines.ENGINES.items()
                       if cls.supports_reference and e != "test_offline"]
    with st.expander("🤖 AI-assisted fallback (optional)"):
        st.caption("Only used when the normal extraction comes back empty or ambiguous: one AI "
                   "call per page (cached), and the site's layout is saved as a profile so its "
                   "next chapter needs none. The AI only points at parts of the page -- the text "
                   "and images are always copied from the page itself, never rewritten.")
        name = st.selectbox("Engine", names, key="src_ai_engine",
                            format_func=lambda e: "Off (no AI calls)" if e == "off" else e)
        if name == "off":
            return None
        key = "local" if name == "ollama" else synced_api_key_input(
            "API key", name, "src_ai_key")
        if not key:
            st.caption("Needs an API key -- without one the AI tier is skipped.")
            return None
        try:
            return translate_engines.get_engine(
                name, key, base_url=(st.session_state.get("settings_ollama_url") or None)
                if name == "ollama" else None)
        except Exception as e:
            st.warning(translate_engines.redact_secrets(f"Couldn't set up {name}: {e}"))
            return None


def _render_confidence(data: dict):
    conf = (data or {}).get("confidence") or {}
    o = ai_extract.overall(data or {})
    st.markdown(f"**Overall: {_BUCKET_ICON.get(o['bucket'], '')} {o['bucket']}** "
                f"({o['score']:.2f}) -- checked independently, not taken from the AI's own claim.")
    for f in list(ai_extract.FIELDS) + ["media_resources"]:
        c = conf.get(f)
        if not c:
            continue
        value = (data or {}).get(f)
        shown = "" if value is None or isinstance(value, (list, dict)) else f" = {str(value)[:60]}"
        st.caption(f"{_BUCKET_ICON.get(c['bucket'], '')} {f}{shown}: {c['bucket']} ({c['score']:.2f})"
                   + (f" — {'; '.join(c['checks'])}" if c["checks"] else ""))


def _render_report(report):
    st.markdown(f"**{report.headline()}**")
    st.caption(f"AI calls: {report.llm_calls}" + (" (cached result reused)" if report.cache_hit else ""))
    st.caption(adaptive.describe_profile(report.profile))
    if report.protection:
        st.caption("Protection detected: " + ", ".join(report.protection))
    _render_access_facts(report.access, report.resource_types)
    if report.reason:
        st.caption(f"Why: {report.reason}")
    for line in report.access_lines + report.lines:
        st.caption(f"· {line}")
    if report.data:
        _render_confidence(report.data)


def _render_access_facts(access: dict, resource_types):
    """Step 23k item 6: each fact on its own line, in its own words."""
    if not access:
        return
    st.caption(f"Authentication: {access.get('authentication')}")
    st.caption(f"Entitlement/purchase: {access.get('entitlement')}")
    st.caption("Resource types found: " + (", ".join(resource_types) if resource_types
                                           else "none identified"))
    st.caption(f"Technical protection: {access.get('technical_protection')}")
    for line in access.get("protection_detail") or []:
        st.caption(f"🔒 {line}")


def _render_review_extraction(rv: dict):
    report = rv["report"]
    st.markdown("#### 🔍 Review extraction")
    st.caption("Shown because " + ("diagnostics mode is on." if _diagnostics_mode() and
                                   not report.needs_review else
                                   "the result's confidence is low.") +
               " Corrections change which parts of the page are used and are saved as this "
               "site's profile -- the source text and images themselves are never edited.")
    _render_confidence(rv["data"])
    if report.pending_profile and st.button("✅ Approve the suggested site profile",
                                            key="src_rv_approve"):
        try:
            v = adaptive.approve_pending(report.pending_profile)
            report.pending_profile = None
            st.success(f"Saved as profile v{v['version']} for "
                       f"{src_profiles.domain_of(rv['url'])}.")
        except src_profiles.ProfileRejected as e:
            st.error(str(e))
    if rv["kind"] == "novel":
        _review_novel(rv)
    else:
        _review_comic(rv)
    if st.button("✖️ Close review", key="src_rv_close"):
        st.session_state.src_fd_review = None
        st.rerun()


def _save_profile_button(rv, kind):
    if rv.get("rules") and st.button("💾 Save these corrections as the site's profile",
                                     key=f"src_rv_save_{kind}"):
        try:
            v = src_profiles.save_version(src_profiles.domain_of(rv["url"]), kind, rv["rules"],
                                          rv["data"], origin="correction", approved=True)
            st.success(f"Saved as profile v{v['version']} -- the next chapter from this site "
                       "uses it" + (f"; v{v['replaces']} is kept for rollback." if v["replaces"] else "."))
        except src_profiles.ProfileRejected as e:
            st.error(str(e))


def _review_novel(rv: dict):
    data = rv["data"]
    sfx = rv.get("nonce", "")      # fresh widget state for each new review
    page = ai_extract.PageModel(rv["html"], rv["url"])
    st.text_area("Extracted text (preview)", (data.get("content") or "")[:4000], height=200,
                 disabled=True)
    base = rv.get("rules") or src_profiles.infer_novel_rules(page, data) or {}
    opts = src_profiles.container_options(page)
    sels = [s for s, _n, _t in opts]
    if base.get("content_selector") and base["content_selector"] not in sels:
        sels.insert(0, base["content_selector"])
    labels = {s: f"{s} — {n:,} chars — {t}" for s, n, t in opts}
    if not sels:
        st.caption("No containers on this page to choose from.")
        return
    content_sel = st.selectbox("The chapter text is inside", sels, key=f"src_rv_container{sfx}",
                               index=sels.index(base["content_selector"])
                               if base.get("content_selector") in sels else 0,
                               format_func=lambda s: labels.get(s, s))
    ex_opts = dict(src_profiles.exclusion_options(page, content_sel))
    exclude = st.multiselect("Leave out (navigation, comments, ads)", list(ex_opts),
                             default=[s for s in base.get("exclude_selectors") or [] if s in ex_opts],
                             key=f"src_rv_exclude{sfx}", format_func=lambda s: f"{s} — {ex_opts[s]}")
    heads = [b for b in page.blocks if b.tag in ai_extract.HEADING_TAGS
             or b.id == data.get("chapter_title_block")][:20]
    head_ids = [None] + [b.id for b in heads]
    title_id = st.selectbox("Chapter title", head_ids, key=f"src_rv_title{sfx}",
                            index=head_ids.index(data.get("chapter_title_block"))
                            if data.get("chapter_title_block") in head_ids else 0,
                            format_func=lambda i: "(none)" if i is None else page.block(i).text[:80])
    link_ids = [None] + [l.id for l in page.links[:80]]

    def link_label(i):
        if i is None:
            return "(none)"
        l = page.link(i)
        return f"{l.text or '(no text)'} → {l.url}"

    def link_index(url):
        return next((k for k, i in enumerate(link_ids) if i and page.link(i).url == url), 0)
    next_id = st.selectbox("Next-chapter link", link_ids, key=f"src_rv_next{sfx}",
                           index=link_index(data.get("next_url")), format_func=link_label)
    prev_id = st.selectbox("Previous-chapter link", link_ids, key=f"src_rv_prev{sfx}",
                           index=link_index(data.get("previous_url")), format_func=link_label)
    number_from = st.radio("Chapter number comes from", ["title", "url"], horizontal=True,
                           key=f"src_rv_numfrom{sfx}")
    if st.button("🔁 Re-run with these corrections", key="src_rv_rerun"):
        rules = src_profiles.novel_rules_from_choices(page, content_sel, exclude, title_id,
                                                      next_id, prev_id, number_from)
        new, why = src_profiles.apply_novel_rules(page, rules)
        if new is None:
            st.error(why)
        else:
            ai_extract.validate_novel(new, page)
            rv["data"], rv["rules"] = new, rules
            st.rerun()
    _save_profile_button(rv, "novel")
    if st.button("📥 Import this text", key="src_rv_import_novel",
                 disabled=not (rv["data"].get("content") or "").strip()):
        pipeline.save_novel_text(rv["drama_id"], rv["data"]["content"], append=rv.get("append", True),
                                 heading=rv["data"].get("chapter_title") or "")
        st.success(f"Saved {len(rv['data']['content']):,} characters as the drama's raw novel text.")


def _review_comic(rv: dict):
    data = rv["data"]
    cands = rv["candidates"]
    sfx = rv.get("nonce", "")
    page = ai_extract.PageModel(rv["html"], rv["url"])
    roles = {p["resource_url"]: p["role"] for p in data["pages"]}
    pos = {p["resource_url"]: p["index"] for p in data["pages"] if p["role"] == "content"}
    st.caption("Mark each image, and number the pages in reading order (0 = not a page).")
    new_roles, new_pos = {}, {}
    for i, c in enumerate(cands[:80]):
        cols = st.columns([1, 4, 2, 1])
        if c.content:
            try:
                cols[0].image(c.content, width=70)
            except Exception:
                cols[0].caption("(preview unavailable)")
        cols[1].caption(f"{c.url}\n{c.attr or 'src'} · {c.width}x{c.height}"
                        + (f" · {c.reject_reason}" if c.reject_reason else ""))
        role = roles.get(c.url, "other")
        new_roles[c.url] = cols[2].selectbox(
            "Role", ai_extract.COMIC_ROLES, key=f"src_rv_role_{i}{sfx}", label_visibility="collapsed",
            index=ai_extract.COMIC_ROLES.index(role) if role in ai_extract.COMIC_ROLES else 0)
        new_pos[c.url] = cols[3].number_input("Page", min_value=0, value=pos.get(c.url, -1) + 1,
                                              key=f"src_rv_pos_{i}{sfx}", label_visibility="collapsed")
    if st.button("🔁 Apply these corrections", key="src_rv_apply_comic"):
        order = {u: n for u, n in new_pos.items() if n > 0}
        new = src_profiles.comic_data_from_roles(page, cands, new_roles, order)
        ai_extract.validate_comic(new, page, adaptive.measured(cands))
        rv["data"], rv["rules"] = new, src_profiles.infer_comic_rules(cands, new)
        st.rerun()
    _save_profile_button(rv, "comic")
    kept, _ = adaptive.images_for(rv["data"], cands)
    if st.button(f"➕ Import these {len(kept)} page(s)", key="src_rv_import_comic", disabled=not kept):
        n = pipeline.add_page_images(rv["drama_id"], [(c.content, c.ext) for c in kept])
        st.success(f"Added {n} page(s) to the drama -- they're in the Scanlate tab now.")


def _render_media_identify(p):
    """Step 23g item 3: list media resources on a page nothing else
    recognizes, and let the person pick which one the existing video
    download path should fetch. Returns the chosen resource URL."""
    engine = _ai_fallback_engine()
    if st.button("🔎 Identify media on this page", key="src_fd_identify"):
        data, report = adaptive.identify_media(p.url, p.html, engine)
        st.session_state.src_fd_media = (p.url, data)
        st.session_state.src_fd_report = report
    got = st.session_state.get("src_fd_media")
    if not got or got[0] != p.url or not got[1]:
        return None
    data = got[1]
    for pr in data.get("protection") or []:
        st.warning(f"{pr} detected on this page -- a protected stream won't be decrypted.")
    playable = [r for r in data["resources"] if r["kind"] in ("video", "audio", "manifest", "embed")]
    if not playable:
        return None
    main = next((i for i, r in enumerate(playable) if r["role"] == "main"), 0)
    pick = st.selectbox("Resource to import", range(len(playable)), index=main,
                        key="src_fd_media_pick",
                        format_func=lambda i: f"{playable[i]['role']} · {playable[i]['kind']} · "
                                              f"{playable[i]['resource_url']}")
    subs = [r for r in data["resources"] if r["kind"] == "subtitle"]
    for r in subs:
        st.caption(f"Subtitle ({r.get('language') or '?'}): {r['resource_url']}")
    return playable[pick]["resource_url"]


def _render_search():
    st.caption("Searches every enabled source at once. Each source keeps its own pace, and a slow "
               "or failing source only loses its own results.")
    q = st.text_input("Title", key="src_search_q")
    if st.button("🔎 Search sources", key="src_search_go", disabled=not q.strip()):
        with st.spinner("Searching..."):
            st.session_state.src_search = registry.multi_search(q.strip())
    res = st.session_state.get("src_search")
    if res is None:
        return
    for name, err in res.errors.items():
        st.caption(f"⚠️ {name}: {err}")
    if not res.results:
        st.info("No results.")
    for i, merged in enumerate(res.results):
        with st.container(border=True):
            st.markdown(f"**{merged.title}** — on {', '.join(merged.sources)}")
            cols = st.columns(len(merged.entries))
            for j, entry in enumerate(merged.entries):
                if cols[j].button(f"Open on {entry.source}", key=f"src_open_{i}_{j}"):
                    st.session_state.src_series = (entry.source, entry.series_id)
                    st.rerun()


def _render_series_browser():
    picked = st.session_state.get("src_series")
    if not picked:
        st.caption("Open a series from search or the URL box to browse its chapters here.")
        return
    source, series_id = picked
    adapter = registry.get_adapter(source)
    # Streamlit re-runs this on every click; the chapter list is fetched
    # once and kept, so ticking checkboxes never re-hits the source.
    cache_key = f"src_series_data_{source}_{series_id}"
    if st.button("🔄 Reload chapter list", key="src_series_reload",
                 help="Fetches the series page again from the source."):
        st.session_state.pop(cache_key, None)
    if cache_key not in st.session_state:
        try:
            src_ladder.check_terms(source, adapter.capabilities())
            with st.spinner("Loading the series (paced like every other request)..."):
                info = adapter.get_series(series_id) if adapter.supports("get_series") else None
                chapters = chapter_order.sort_chapters_grouped(adapter.get_chapters(series_id))
        except ChallengeDetected as e:
            retry, cancel = _render_handoff({"url": e.url, "reason": e.reason.value}, "src_series")
            if cancel:
                st.session_state.src_series = None
                st.rerun()
            if retry:
                st.rerun()
            return
        except (SourceError, NotSupportedError) as e:
            st.error(f"{source}: {e}")
            return
        st.session_state[cache_key] = (info, chapters)
    info, chapters = st.session_state[cache_key]
    st.markdown(f"**{info.title if info else series_id}** · {source} · {len(chapters)} chapter(s)")
    if info and info.description:
        st.caption(info.description[:400])

    import pandas as pd
    table = pd.DataFrame({"Import": [False] * len(chapters),
                          "Chapter": [c.title for c in chapters],
                          "Section": [c.group for c in chapters]})
    edited = st.data_editor(table, key=f"src_ch_table_{source}_{series_id}", hide_index=True,
                            disabled=["Chapter", "Section"], width="stretch")
    selected = [c for c, keep in zip(chapters, edited["Import"].tolist()) if keep]

    drama_id = _drama_picker("Import into drama", "src_series_drama",
                             _COMIC_MEDIA if adapter.supports("get_pages") else ("novel",))
    c1, c2 = st.columns(2)
    job_id = pipeline.import_job_id(source, series_id)
    if c1.button(f"📥 Import {len(selected)} selected chapter(s)", key="src_import_go",
                 disabled=not (selected and drama_id)):
        if pipeline.start_import(source, series_id, selected, drama_id):
            watch = st.session_state.setdefault("src_watch_jobs", [])
            if job_id not in watch:
                watch.append(job_id)
            st.success("Import started -- follow it under Source Access below.")
        else:
            st.warning("An import for this series is already running.")
    tracked = any(r["source"] == source and r["series_id"] == series_id
                  for r in src_store.list_tracked_series())
    if not tracked and c2.button("🔔 Track for new chapters", key="src_track"):
        src_store.track_series(source, series_id, info.title if info else series_id,
                               info.url if info else "", drama_id, known_chapters=chapters)
        st.rerun()
    elif tracked:
        c2.caption("🔔 Tracked -- new chapters show up under New chapters.")
    status = background_jobs.get_status(job_id)
    if status:
        st.caption(f"Last import: {status['status']} — {status.get('message', '')}")


def _render_access_status():
    jobs = [(jid, j) for jid, j in background_jobs.list_running_jobs().items()
            if jid.startswith("source_import_") or jid == chapter_check.CHECK_JOB_ID]
    finished = list(st.session_state.get("src_watch_jobs", []))
    shown = False
    for jid, job in jobs:
        shown = True
        stats = (job.get("result") or {}).get("stats") or {}
        with st.container(border=True):
            st.markdown(f"**{job.get('description') or jid}**")
            st.progress(float(job.get("progress") or 0.0), text=job.get("message") or "")
            c = st.columns(4)
            c[0].metric("Access method", stats.get("access_method", "Normal HTTP"))
            c[1].metric("Requests", stats.get("requests", 0))
            c[2].metric("From cache", stats.get("cache_hits", 0))
            c[3].metric("Current delay", f"{stats.get('current_delay', 0.0):.1f}s")
            st.caption(f"Now: {stats.get('current_action', '—')}")
            if st.button("✖️ Cancel", key=f"src_cancel_{jid}"):
                background_jobs.request_cancel(jid)
            watch = st.session_state.setdefault("src_watch_jobs", [])
            if jid not in watch:
                watch.append(jid)
    for jid in finished:
        job = background_jobs.get_status(jid)
        if not job or job["status"] == "running":
            continue
        shown = True
        result = job.get("result") or {}
        with st.container(border=True):
            st.markdown(f"**{job.get('description') or jid}** — {job['status']}")
            if job.get("error"):
                st.error(job["error"])
            if result.get("cancelled"):
                st.info("Cancelled. Chapters finished before cancelling were kept.")
            for ch in result.get("chapters", []):
                st.caption(("✅ " if ch["ok"] else "❌ ") + ch["title"]
                           + (f" — {ch.get('pages')} page(s)" if ch.get("pages") else "")
                           + (f" — {ch['error']}" if ch.get("error") else ""))
            if result.get("handoff"):
                _render_handoff({**result["handoff"], "tier": "STATIC_HTTP"}, f"src_job_{jid}")
            if st.button("Dismiss", key=f"src_dismiss_{jid}"):
                st.session_state.src_watch_jobs.remove(jid)
                background_jobs.clear_job(jid)
                st.rerun()
    if not shown:
        st.caption("Nothing is being fetched right now.")
    elif st.button("🔄 Refresh", key="src_status_refresh"):
        st.rerun()


def _render_notifications():
    notes = src_store.list_notifications()
    c1, c2 = st.columns([1, 3])
    if c1.button("🔄 Check now", key="src_check_now"):
        if chapter_check.start_check_now():
            st.success("Checking in the background.")
    c2.caption("New chapters are announced here -- never downloaded automatically unless you "
               "turn on auto-import in the settings below.")
    if not notes:
        st.caption("No new chapters.")
    for n in notes:
        cols = st.columns([4, 1, 1])
        cols[0].markdown(f"🆕 **{n['title']}** · {n['source']}")
        if cols[1].button("Open", key=f"src_note_open_{n['id']}"):
            st.session_state.src_series = (n["source"], n["series_id"])
            st.rerun()
        if cols[2].button("Dismiss", key=f"src_note_dismiss_{n['id']}"):
            src_store.dismiss_notification(n["id"])
            st.rerun()
    tracked = src_store.list_tracked_series()
    if tracked:
        st.markdown("**Tracked series**")
    for t in tracked:
        cols = st.columns([4, 1])
        last = t["last_check_error"] or "ok"
        cols[0].caption(f"{t['title']} · {t['source']} · last check: {last}")
        if cols[1].button("Stop tracking", key=f"src_untrack_{t['source']}_{t['series_id']}"):
            src_store.untrack_series(t["source"], t["series_id"])
            st.rerun()


def _render_generic_diagnostics():
    """Source Diagnostics for pasted URLs with no dedicated adapter (Step
    23g item 7): read from the same access-attempt log as every other
    source, plus each site's saved extraction profiles with rollback."""
    rows = adaptive.recent_extractions(limit=15)
    if not rows:
        st.caption("No pasted-URL imports yet.")
    for a in rows:
        st.markdown(f"**{a['url']}** — {a.get('content_type')} — {a.get('headline')}")
        st.caption(f"AI calls: {a.get('llm_calls', 0)}"
                   + (" (cached result reused)" if a.get("cache_hit") else "")
                   + " · " + adaptive.describe_profile(a.get("profile") or {}))
        _render_access_facts(a.get("access") or {}, a.get("resource_types") or [])
        if a.get("reason"):
            st.caption(f"Why: {a['reason']}")
        for line in a.get("lines") or []:
            st.caption(f"  · {line}")
    domains = src_profiles.list_domains()
    if domains:
        st.markdown("**Saved site profiles**")
    for d in domains:
        for v in src_profiles.versions(d):
            fail = v.get("last_failure") or {}
            c = st.columns([5, 1])
            c[0].caption(f"{d} · {v['kind']} v{v['version']} · {v['status']} · from {v['origin']}"
                         + (f" · last failed: {fail.get('reason')}" if fail else ""))
            if v["status"] != "active" and c[1].button("Make active", key=f"src_prof_{d}_{v['kind']}_{v['version']}"):
                src_profiles.rollback(d, v["kind"], v["version"])
                st.rerun()


def _render_sources_detail():
    st.markdown("**Pasted-URL imports (no dedicated adapter)**")
    _render_generic_diagnostics()
    st.divider()
    classes = registry.adapter_classes()
    for name, cls in classes.items():
        if getattr(cls, "is_demo", False) and not src_store.get_setting("demo_source_enabled"):
            continue
        adapter = cls()
        caps = src_ladder.apply_terms(src_ladder.load_capabilities(name, adapter.capabilities()))
        tos_prohibited = caps.status == CapabilityStatus.TOS_PROHIBITED.value
        h = health.get(name)
        with st.expander(f"{health.light(name)} {cls.display_name or name} — {caps.status}"):
            enabled = st.toggle("Enabled", value=registry.is_enabled(name), key=f"src_en_{name}")
            if enabled != registry.is_enabled(name):
                registry.set_enabled(name, enabled)
                st.rerun()
            if cls.supports_adult_toggle:
                adult_on = src_store.adult_enabled(name)
                adult = st.toggle(
                    "🔞 Include adult-flagged works", value=adult_on, key=f"src_adult_{name}",
                    help="This site keeps some works behind its own \"I'm an adult\" switch. "
                         "Turning this on sends that same switch with this source's requests, "
                         "so those works' chapters and pages can be listed and imported. "
                         "Off by default; only affects this source.")
                if adult != adult_on:
                    src_store.set_adult_enabled(name, adult)
                    # Chapter lists fetched with the old setting are stale now.
                    for k in [k for k in st.session_state.keys()
                              if str(k).startswith(f"src_series_data_{name}_")]:
                        del st.session_state[k]
                    st.rerun()
            st.caption(f"Content: {', '.join(caps.content_types)} · Languages: "
                       f"{', '.join(caps.languages)} · Technical status: {caps.technical_status}"
                       f" · Resource types reached: {caps.content_access_status}")
            st.caption(src_ladder.describe_capability_fields(caps))
            st.caption(f"Last success: {h['last_success'] or '—'} · last failure: "
                       f"{h['last_failure'] or '—'} ({h['last_error_type'] or '—'}) · latency: "
                       f"{(h['last_latency'] or 0):.2f}s")
            wait = health.retry_after(name)
            if wait:
                st.caption(f"🔴 Left alone for another {wait:.0f}s after repeated failures.")
                if st.button("Try again now", key=f"src_reset_{name}",
                             help="Clears the backoff window -- your call, never automatic."):
                    health.reset(name)
                    st.rerun()
            st.markdown("**Access ladder, per tier**")
            for tier, res in caps.tiers.items():
                st.caption(f"{tier}: " + ("UNTESTED" if not res.tested else
                                          ("✅ works" if res.ok else f"❌ {res.reason} {res.detail}")))
            test_url = st.text_input("Page to test against", key=f"src_test_url_{name}",
                                     help="Any real page on this source. Each button runs exactly "
                                          "one tier and updates only that tier's line above.")
            b = st.columns(5)
            signed_in = bool(test_url.strip()) and auth_browser.has_profile(test_url.strip(), name)
            tier_fns = {
                AccessTier.STATIC_HTTP: src_ladder.static_tier(adapter.client),
                AccessTier.RENDERED_BROWSER: src_ladder.rendered_tier(adapter.client),
                AccessTier.AUTHENTICATED_BROWSER: src_ladder.authenticated_tier(
                    auth_browser.profile_dir(test_url.strip(), name), adapter.client),
            }
            if tos_prohibited:
                st.caption("🚫 This source's terms restrict automated access -- "
                           "\"Test Now\" is disabled the same way real imports are.")
            for col, (tier, label) in zip(b, [(AccessTier.STATIC_HTTP, "Test Static"),
                                              (AccessTier.RENDERED_BROWSER, "Test Browser"),
                                              (AccessTier.AUTHENTICATED_BROWSER, "Test Authenticated")]):
                # Testing the signed-in tier needs a sign-in first -- it would
                # otherwise create an empty, signed-out profile.
                if col.button(label, key=f"src_test_{name}_{tier.value}",
                              disabled=tos_prohibited or not test_url.strip() or (
                                  tier == AccessTier.AUTHENTICATED_BROWSER and not signed_in)):
                    try:
                        src_ladder.test_tier(name, tier, test_url.strip(), tier_fns[tier],
                                             adapter.capabilities())
                    except TermsProhibited as e:
                        st.error(str(e))
                    st.rerun()
            if test_url.strip():
                b[3].link_button("Open in Browser", test_url.strip())
                with st.expander("🔐 Sign in to this source"):
                    _render_sign_in(test_url.strip(), f"src_login_{name}", source=name)
            if caps.technical:
                st.caption(f"Technical findings: {caps.technical}")
            if caps.terms:
                st.caption(f"Terms findings: {caps.terms}")
            with st.expander("View diagnostics"):
                attempts = src_store.recent_attempts(name, limit=20)
                if not attempts:
                    st.caption("No attempts recorded yet.")
                for a in attempts:
                    st.caption(f"{a['url']} → {a.get('technical_status') or ''} "
                               f"{a.get('capability_status') or ''}")
                    for line in a.get("lines") or []:
                        st.caption(f"  {line}")


def _render_settings():
    s = src_store.all_settings()
    c1, c2 = st.columns(2)
    lo = c1.number_input("Min seconds between requests to one source", 0.0, 60.0,
                         float(s["pace_min_delay"]), 0.5, key="src_set_min")
    hi = c2.number_input("Max seconds between requests to one source", 0.0, 120.0,
                         float(s["pace_max_delay"]), 0.5, key="src_set_max")
    c3, c4 = st.columns(2)
    conc = c3.number_input("Requests at once per source", 1, 4, int(s["max_concurrent"]),
                           key="src_set_conc",
                           help="1 is the human-paced default. A source can still insist on a "
                                "slower pace of its own (e.g. its robots.txt crawl delay).")
    retries = c4.number_input("Max retries (for busy/overloaded replies only)", 0, 6,
                              int(s["max_retries"]), key="src_set_retries",
                              help="Never applies to a browser-verification page -- those are "
                                   "always handed to you instead.")
    mode = st.selectbox("Raw-content cache", src_cache.MODES,
                        index=src_cache.MODES.index(s["cache_mode"]),
                        format_func=lambda m: src_cache.MODE_LABELS[m], key="src_set_cache",
                        help="Stops the app re-downloading the same page from a source. "
                             "Translated output always lives in Scanlate regardless.")
    c5, c6 = st.columns(2)
    interval = c5.number_input("Check tracked series every N hours (0 = off)", 0, 168,
                               int(s["check_interval_hours"]), key="src_set_interval")
    auto = c6.checkbox("Auto-import new chapters of tracked series", value=bool(
        s["auto_queue_new_chapters"]), key="src_set_auto",
        help="Off by default: new chapters are announced, not downloaded.")
    demo = st.checkbox("Show the demo source (offline, for trying the workflow)",
                       value=bool(s["demo_source_enabled"]), key="src_set_demo")
    diag = st.checkbox("Extraction diagnostics mode", value=bool(s["extraction_diagnostics"]),
                       key="src_set_diag",
                       help="Always show the Review Extraction screen and the source diagnostics "
                            "for pasted-URL imports. Off: they only appear when confidence is low.")
    if st.button("💾 Save source settings", key="src_set_save"):
        for k, v in {"pace_min_delay": lo, "pace_max_delay": max(lo, hi), "max_concurrent": conc,
                     "max_retries": retries, "cache_mode": mode, "check_interval_hours": interval,
                     "auto_queue_new_chapters": auto, "demo_source_enabled": demo,
                     "extraction_diagnostics": diag}.items():
            src_store.set_setting(k, v)
        from sources.http import reset_pacing_state
        reset_pacing_state()
        st.success("Saved.")
    stats = src_cache.RawCache().stats()
    st.caption(f"Cache: {stats['entries']} item(s), {stats['bytes'] / 1e6:.1f} MB")
    if st.button("🗑️ Clear the raw-content cache", key="src_cache_clear"):
        src_cache.RawCache().clear_all()
        st.rerun()


def render_sources_tab():
    st.subheader("Sources")
    st.caption("Fetch raw chapters and pages from outside sources, at a human pace, into the "
               "same Scanlate and Workspace paths a manual upload uses. Browser-verification "
               "pages are always handed to you -- the app never tries to get past them.")
    chapter_check.ensure_scheduler_started()
    with st.expander("🚪 Paste any URL", expanded=True):
        _render_front_door()
    with st.expander("🔎 Search sources"):
        _render_search()
    with st.expander("📚 Series & chapters", expanded=bool(st.session_state.get("src_series"))):
        _render_series_browser()
    with st.expander("📡 Source access status", expanded=True):
        _render_access_status()
    notes = src_store.list_notifications()
    with st.expander(f"🔔 New chapters ({len(notes)})"):
        _render_notifications()
    with st.expander("🩺 Sources, health & diagnostics"):
        _render_sources_detail()
    with st.expander("⚙️ Source settings"):
        _render_settings()

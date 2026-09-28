"""
tests/test_sources_auth_browser.py -- Step 23k: persistent signed-in
browser profiles, the manual-login flow, the granular capability fields,
and the rules that keep a sign-in from ever becoming a permission.

Playwright is never imported: every browser here is a fake launcher that
hands back fake context/page objects. The fake keeps its "signed-in" state
in a file inside the profile directory -- the way Chromium's own cookie
store does -- so persistence is tested through the real page_fetch code.
"""

import ast
import inspect
import json
import os
import textwrap

import pytest

import page_fetch
from sources import (adaptive, ai_extract, auth_browser, front_door, generic_import, ladder,
                     registry, site_terms, store)
from sources.base import SourceAdapter
from sources.models import (AccessTier, AiMlUse, AutomationPermission, CapabilityStatus,
                            FailureReason, NotSupportedError, Requirement, SourceCapabilities,
                            TechnicalProtection, TermsProhibited)
from tests.sources_helpers import ScriptedTransport, make_client

SESSION_SECRET = "SESSDATA-9f8e7d6c5b4a-SECRET"
TOKEN_SECRET = "tok-3c2b1a0f-SECRET"
SIGNED_IN_FILE = "Cookies"   # what the fake "Chromium" writes when the person signs in

LOGIN_PAGE = ("<html><head><title>Sign in</title></head><body><form>"
              "<p>Please log in to continue reading.</p>"
              "<input type='text' name='user'><input type=\"password\" name='pw'>"
              "</form></body></html>")


def comic_page(n=4):
    imgs = "".join(
        f'<img src="https://img.site.invalid/c1/{i}.jpg?token={TOKEN_SECRET}&w=800" '
        f'alt="page {i + 1}" width="800" height="1200">' for i in range(n))
    return (f"<html><head><title>第1话 - Example</title></head><body><h1>第1话</h1>"
            f'<div id="reader">{imgs}</div>'
            f'<a href="https://site.invalid/ch/2?auth_key={TOKEN_SECRET}">下一话</a>'
            "</body></html>")


def novel_page():
    paras = "".join(f"<p>第{i}段。她推开门，走进了下着雨的院子，心里想着昨天的事情。</p>" for i in range(40))
    return (f"<html><head><title>第一章</title></head><body><h1>第一章 雨夜</h1>"
            f'<div id="content">{paras}</div></body></html>')


# ---------------------------------------------------------------------------
# Fake Playwright
# ---------------------------------------------------------------------------

class FakePage:
    def __init__(self, ctx):
        self.ctx = ctx
        self.url = "about:blank"

    def goto(self, url, **kw):
        self.url = url
        self.ctx.visits.append(url)

    def wait_for_timeout(self, ms):
        pass

    def wait_for_selector(self, sel, **kw):
        pass

    def content(self):
        return self.ctx.launcher.site(self.url, self.ctx.signed_in())


class FakeContext:
    def __init__(self, launcher, profile_dir, headless):
        self.launcher, self.profile_dir, self.headless = launcher, profile_dir, headless
        self.visits, self.closed, self.waits = [], False, []
        self.pages = []

    def signed_in(self) -> bool:
        return os.path.exists(os.path.join(self.profile_dir, SIGNED_IN_FILE))

    def new_page(self):
        p = FakePage(self)
        self.pages.append(p)
        return p

    def wait_for_event(self, name, timeout=None):
        # The person's turn: whatever they do in the window happens here.
        self.waits.append((name, timeout))
        if self.launcher.person is not None:
            self.launcher.person(self)

    def close(self):
        self.closed = True


class FakePlaywright:
    stopped = False

    def stop(self):
        self.stopped = True


class FakeLauncher:
    """launcher(profile_dir, headless) -> (playwright, context)."""

    def __init__(self, site, person=None):
        self.site = site
        self.person = person
        self.launches = []
        self.contexts = []

    def __call__(self, profile_dir, headless):
        self.launches.append((profile_dir, headless))
        ctx = FakeContext(self, profile_dir, headless)
        self.contexts.append(ctx)
        return FakePlaywright(), ctx

    @property
    def headed(self):
        return [l for l in self.launches if not l[1]]


def signs_in(ctx):
    """The person signs in, in the real window; the site's session cookie
    lands in the profile directory -- never anywhere the app reads."""
    with open(os.path.join(ctx.profile_dir, SIGNED_IN_FILE), "w") as f:
        f.write(f"SESSDATA={SESSION_SECRET}")


def gated(content_fn):
    """A site that shows its content only to a signed-in browser."""
    return lambda url, signed_in: content_fn() if signed_in else LOGIN_PAGE


@pytest.fixture
def fake_browser(monkeypatch):
    """Routes every persistent-profile launch through one FakeLauncher."""
    holder = {}

    def install(site, person=signs_in):
        launcher = FakeLauncher(site, person)
        monkeypatch.setattr(page_fetch, "_launch_persistent", launcher)
        holder["l"] = launcher
        return launcher
    return install


# ---------------------------------------------------------------------------
# Item 1: persistent profiles
# ---------------------------------------------------------------------------

class TestPersistentProfiles:
    URL = "https://manga.site.invalid/ch/1"

    def test_profile_dir_is_computed_from_the_library_at_call_time(self, isolated_db):
        import db
        d = store.browser_profile_dir("bilibili_manga")
        assert d == os.path.join(db.LIBRARY_DIR, "profiles", "bilibili_manga")
        assert os.path.dirname(store.browser_profile_dir("../../etc")) == \
            os.path.join(db.LIBRARY_DIR, "profiles")
        # A pasted URL no adapter covers gets its own site's profile.
        assert auth_browser.profile_dir(self.URL) == \
            os.path.join(db.LIBRARY_DIR, "profiles", "manga.site.invalid")

    def test_signed_in_profile_survives_two_separate_fetch_calls(self, isolated_db, fake_browser):
        """Exit condition 1: one sign-in, then two separate page_fetch-style
        reads -- both signed in, no second sign-in window."""
        launcher = fake_browser(gated(comic_page))
        check = auth_browser.manual_login(self.URL)
        assert check.ok, check.message
        assert len(launcher.headed) == 1

        t = ScriptedTransport({})
        for _ in range(2):
            lr = generic_import.fetch_page(self.URL, client=make_client("generic", t))
            assert lr.ok and lr.tier == AccessTier.AUTHENTICATED_BROWSER.value
            assert "reader" in lr.html and "Please log in" not in lr.html
        assert len(launcher.headed) == 1                # never asked to sign in again
        dirs = {d for d, _ in launcher.launches}
        assert dirs == {auth_browser.profile_dir(self.URL)}   # always the same persistent profile
        assert all(c.closed for c in launcher.contexts)       # nothing left running
        assert t.calls == []   # signed in: read through the profile, not a signed-out request

    def test_sign_in_survives_a_restart(self, isolated_db, fake_browser):
        """What persists is the profile on disk, not a live process: a brand
        new launcher (as after an app restart) still sees the sign-in."""
        fake_browser(gated(novel_page))
        assert auth_browser.manual_login(self.URL).ok
        fresh = fake_browser(gated(novel_page), person=None)
        html, text = page_fetch.fetch_with_profile(self.URL, auth_browser.profile_dir(self.URL))
        assert "第一章" in text and not fresh.headed

    def test_login_window_waits_for_the_person_with_no_timeout(self, isolated_db, fake_browser):
        launcher = fake_browser(gated(comic_page))
        auth_browser.manual_login(self.URL)
        headed = next(c for c in launcher.contexts if not c.headless)
        assert headed.waits == [("close", 0)]
        assert headed.visits == [self.URL]

    def test_closing_the_window_without_signing_in_is_reported_plainly(self, isolated_db,
                                                                        fake_browser):
        fake_browser(gated(comic_page), person=lambda ctx: None)
        check = auth_browser.manual_login(self.URL)
        assert not check.ok
        assert "Not signed in" in check.message
        assert check.facts["authentication_required"]

    def test_without_a_sign_in_the_ordinary_tiers_run(self, isolated_db, fake_browser):
        launcher = fake_browser(gated(comic_page))
        t = ScriptedTransport({})
        lr = generic_import.fetch_page(self.URL, client=make_client("generic", t),
                                       rendered_fetch=lambda u: (LOGIN_PAGE, ""))
        assert [a.tier for a in lr.attempts] == ["STATIC_HTTP", "RENDERED_BROWSER"]
        assert launcher.launches == []
        assert lr.reasons and FailureReason.AUTHENTICATION_REQUIRED in lr.reasons

    def test_forget_deletes_the_saved_profile(self, isolated_db, fake_browser):
        fake_browser(gated(comic_page))
        auth_browser.manual_login(self.URL)
        assert auth_browser.has_profile(self.URL)
        assert auth_browser.forget(self.URL)
        assert not auth_browser.has_profile(self.URL)

    def test_a_busy_profile_is_refused_not_opened_twice(self, isolated_db, fake_browser):
        fake_browser(gated(comic_page))
        lock = page_fetch._profile_lock(auth_browser.profile_dir(self.URL))
        with lock:
            with pytest.raises(page_fetch.ProfileBusy):
                auth_browser.manual_login(self.URL)

    def test_backups_leave_saved_sign_ins_out(self):
        from tabs import library_tab
        assert "src_store.BROWSER_PROFILES_DIRNAME" in inspect.getsource(library_tab)
        assert store.BROWSER_PROFILES_DIRNAME == "profiles"

    def test_base_adapter_login_is_the_manual_flow(self, isolated_db, fake_browser):
        class Site(SourceAdapter):
            name = "login_site"
            display_name = "Login Site"
            url_patterns = [r"login-site\.invalid/"]
            login_url = "https://login-site.invalid/ch/1"
        launcher = fake_browser(gated(comic_page))
        check = Site(client=make_client("login_site", ScriptedTransport({}))).login()
        assert check.ok and launcher.headed[0][0] == store.browser_profile_dir("login_site")

        class NoPage(SourceAdapter):
            name = "no_page"
            display_name = "No Page"
        with pytest.raises(NotSupportedError):
            NoPage(client=make_client("no_page", ScriptedTransport({}))).login()


# ---------------------------------------------------------------------------
# Items 3 and 4: the granular fields; a sign-in is never a permission
# ---------------------------------------------------------------------------

class NaverShaped(SourceAdapter):
    """Shaped like the real Naver/Novelpia findings: a directly-read ToS
    clause banning automated means, bots and scrapers."""
    name = "naver_shaped"
    display_name = "Naver-shaped"
    content_types = ["novel"]
    url_patterns = [r"naver-shaped\.invalid/"]

    def capabilities(self):
        caps = super().capabilities()
        caps.automation_permission = AutomationPermission.EXPLICITLY_RESTRICTED.value
        caps.terms = {"read": "Terms, read in full",
                      "clause": "bans automated means (bots, spiders, scrapers)"}
        return caps


class TestAutomationPermission:
    URL = "https://naver-shaped.invalid/viewer/1"

    @pytest.fixture
    def signed_in_restricted(self, isolated_db, monkeypatch, fake_browser):
        monkeypatch.setitem(registry._ADAPTERS, NaverShaped.name, NaverShaped)
        # A mocked, fully signed-in session for this source already exists.
        d = auth_browser.profile_dir(self.URL)
        os.makedirs(d)
        signs_in(type("C", (), {"profile_dir": d})())
        assert auth_browser.has_profile(self.URL)
        return fake_browser(lambda url, signed_in: novel_page())

    def test_record_reports_restricted_regardless_of_a_signed_in_session(
            self, signed_in_restricted):
        """Exit condition 3, first half. Even a stored record claiming
        PERMITTED, with a successful signed-in attempt folded in, can't
        clear what the source's own terms finding says."""
        default = NaverShaped(client=make_client(NaverShaped.name)).capabilities()
        stale = SourceCapabilities(platform="Naver-shaped",
                                   automation_permission=AutomationPermission.PERMITTED.value)
        ladder.save_capabilities(NaverShaped.name, stale)
        ok = ladder.run_ladder(self.URL, {
            AccessTier.STATIC_HTTP: lambda u: ladder.TierOutcome(
                False, html=LOGIN_PAGE, reasons=[FailureReason.AUTHENTICATION_REQUIRED]),
            AccessTier.AUTHENTICATED_BROWSER: lambda u: ladder.TierOutcome(True, html=novel_page())},
            source=NaverShaped.name, log=False)
        assert ok.ok and ok.tier == AccessTier.AUTHENTICATED_BROWSER.value
        caps = ladder.record_ladder_result(NaverShaped.name, ok, default)
        assert caps.automation_permission == AutomationPermission.EXPLICITLY_RESTRICTED.value
        assert caps.status == CapabilityStatus.TOS_PROHIBITED.value
        assert caps.authentication_required == Requirement.REQUIRED.value   # a separate fact
        assert caps.access_method == AccessTier.AUTHENTICATED_BROWSER.value
        assert ladder.load_capabilities(NaverShaped.name, default).automation_permission == \
            AutomationPermission.EXPLICITLY_RESTRICTED.value

    def test_a_signed_in_success_alone_doesnt_claim_sign_in_is_required(self, isolated_db):
        r = ladder.run_ladder("https://free.site.invalid/ch/1", {
            AccessTier.AUTHENTICATED_BROWSER: lambda u: ladder.TierOutcome(True, html=novel_page())},
            log=False)
        caps = ladder.record_ladder_result("free_site", r)
        assert caps.authentication_required == Requirement.UNKNOWN.value
        assert caps.technical_protection == TechnicalProtection.NONE.value

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_pipeline_refuses_before_anything_runs(self, signed_in_restricted):
        """Exit condition 3, second half: nothing past the capability check
        -- no request, no browser launch, no sign-in window."""
        launcher = signed_in_restricted
        t = ScriptedTransport({})
        client = make_client(NaverShaped.name, t)
        for call in (lambda: generic_import.fetch_page(self.URL, client=client),
                     lambda: adaptive.import_novel(self.URL, client=client),
                     lambda: adaptive.import_comic(self.URL, client=client),
                     lambda: front_door.preview(self.URL),
                     lambda: auth_browser.manual_login(self.URL),
                     lambda: auth_browser.verify(self.URL),
                     lambda: NaverShaped(client=client).login(self.URL)):
            with pytest.raises(TermsProhibited) as e:
                call()
            assert "signing in doesn't change that" in str(e.value)
        assert launcher.launches == [] and t.calls == []
        assert store.recent_attempts(NaverShaped.name) == []

    @pytest.mark.parametrize("url", [
        "https://series.naver.com/novel/detail.series?productNo=1",
        "https://comic.naver.com/webtoon/detail?titleId=1&no=2",
        "https://novelpia.com/viewer/123",
        "https://www.jjwxc.net/onebook.php?novelid=1&chapterid=2",
    ])
    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_real_vetted_restricted_sites_are_refused_signed_in_or_not(
            self, isolated_db, fake_browser, url):
        launcher = fake_browser(lambda u, s: novel_page())
        os.makedirs(auth_browser.profile_dir(url))
        signs_in(type("C", (), {"profile_dir": auth_browser.profile_dir(url)})())
        caps = site_terms.capabilities_for(url)
        assert caps.automation_permission == AutomationPermission.EXPLICITLY_RESTRICTED.value
        t = ScriptedTransport({})
        with pytest.raises(TermsProhibited):
            adaptive.import_novel(url, client=make_client("generic", t))
        with pytest.raises(TermsProhibited):
            auth_browser.manual_login(url)
        assert launcher.launches == [] and t.calls == []

    def test_kakaopage_stays_unknown_not_cleared(self, isolated_db):
        caps = site_terms.capabilities_for("https://page.kakao.com/content/123")
        assert caps.automation_permission == AutomationPermission.UNKNOWN.value
        assert caps.automation_permission != AutomationPermission.PERMITTED.value
        assert not caps.terms_restrictions()
        assert site_terms.capabilities_for("https://unrelated.invalid/") is None

    @pytest.mark.skip(reason="ToS/robots enforcement intentionally deactivated 2026-09-27 per explicit user decision -- see sources/ladder.py:check_terms")
    def test_an_ai_ml_use_restriction_refuses_too(self, isolated_db):
        default = SourceCapabilities(platform="AI-restricted",
                                     ai_ml_use=AiMlUse.EXPLICITLY_RESTRICTED.value)
        with pytest.raises(TermsProhibited) as e:
            ladder.check_terms("ai_site", default)
        assert "AI/ML" in str(e.value)

    def test_fields_default_to_unknown_and_stay_separate(self, isolated_db):
        caps = SourceCapabilities(platform="x")
        for f in ("authentication_required", "purchase_required", "technical_protection",
                  "automation_permission", "ai_ml_use"):
            assert getattr(caps, f) == "UNKNOWN"
        caps.authentication_required = Requirement.REQUIRED.value
        ladder.apply_terms(caps)
        assert caps.status != CapabilityStatus.TOS_PROHIBITED.value   # needing a login isn't a ban
        roundtrip = SourceCapabilities.from_dict(caps.to_dict())
        assert roundtrip == caps

    def test_records_stored_before_23k_migrate(self, isolated_db):
        old = SourceCapabilities(platform="x").to_dict()
        del old["authentication_required"]
        old["auth_required"] = True
        assert SourceCapabilities.from_dict(old).authentication_required == \
            Requirement.REQUIRED.value
        old["auth_required"] = False
        assert SourceCapabilities.from_dict(old).authentication_required == \
            Requirement.UNKNOWN.value

    def test_tos_prohibited_and_automation_permission_agree(self, isolated_db):
        caps = SourceCapabilities(platform="x", terms={"tos_prohibited": True})
        ladder.apply_terms(caps)
        assert caps.automation_permission == AutomationPermission.EXPLICITLY_RESTRICTED.value


# ---------------------------------------------------------------------------
# Item 6: specific failures, never a bare "blocked"
# ---------------------------------------------------------------------------

DRM_PAGE = ("<html><head><title>第1话</title></head><body><h1>第1话</h1>"
            "<video id='player'></video><script>navigator.requestMediaKeySystemAccess("
            "'com.widevine.alpha', cfg)</script>" + "<p>正文" * 50 + "</p></body></html>")


class TestProtectedResources:
    URL = "https://drm.site.invalid/watch/1"

    def test_protected_resource_is_reported_specifically(self, isolated_db, fake_browser):
        """Exit condition 4."""
        fake_browser(lambda url, signed_in: DRM_PAGE)
        auth_browser.manual_login(self.URL)
        with pytest.raises(generic_import.NoContentFound) as e:
            adaptive.import_comic(self.URL, client=make_client("generic", ScriptedTransport({})))
        msg = str(e.value)
        assert msg.startswith("Protected resource could not be processed without bypassing a "
                              "technical control (DRM")
        assert "blocked" not in msg.lower()
        report = e.value.report
        assert report.access["technical_protection"] == TechnicalProtection.DETECTED.value
        assert report.reason.startswith("Protected resource")
        logged = adaptive.recent_extractions()[0]
        assert logged["access"]["protection_detail"] and logged["reason"].startswith("Protected")
        caps = ladder.load_capabilities("generic")
        assert caps.technical_protection == TechnicalProtection.DETECTED.value

    def test_sign_in_check_names_protection_too(self, isolated_db, fake_browser):
        fake_browser(lambda url, signed_in: DRM_PAGE)
        check = auth_browser.manual_login(self.URL)
        assert not check.ok and check.message.startswith("Protected resource")

    def test_missing_purchase_is_named_not_worked_around(self, isolated_db, fake_browser):
        buy = ("<html><body><h1>第9话</h1><p>Unlock this chapter to keep reading.</p>"
               "<button>购买本章</button></body></html>")
        fake_browser(lambda url, signed_in: buy if signed_in else LOGIN_PAGE)
        check = auth_browser.manual_login("https://paid.site.invalid/ch/9")
        assert not check.ok and check.message.startswith("Purchase/entitlement required")
        assert "never works around" in check.message
        caps = ladder.load_capabilities("generic")
        assert caps.purchase_required == Requirement.REQUIRED.value


# ---------------------------------------------------------------------------
# Item 5: the full resource set, through the existing extractors
# ---------------------------------------------------------------------------

MIXED_PAGE = (
    "<html><head><title>第1话</title></head><body><h1>第1话</h1>"
    '<picture><source srcset="https://img.site.invalid/p1-400.webp 400w, '
    'https://img.site.invalid/p1-1200.webp 1200w"><img src="https://img.site.invalid/p1.jpg"></picture>'
    '<img data-src="https://img.site.invalid/p2.jpg" src="data:image/gif;base64,R0lGOD">'
    '<img srcset="https://img.site.invalid/p3.jpg 1x, https://img.site.invalid/p3@2x.jpg 2x">'
    '<script>window.__DATA__={"pages":["https:\\/\\/img.site.invalid\\/p4.jpg"]}</script>'
    '<video src="https://media.site.invalid/ep1.mp4"><track kind="subtitles" srclang="zh" '
    'src="https://media.site.invalid/ep1.zh.vtt"></video>'
    '<div id="content">' + "".join(f"<p>第{i}段，正文内容在这里继续展开。</p>" for i in range(30)) +
    "</div></body></html>")


class TestFullResourceSet:
    def test_signed_in_page_feeds_every_existing_extractor(self, isolated_db, fake_browser):
        url = "https://mixed.site.invalid/ch/1"
        fake_browser(lambda u, signed_in: MIXED_PAGE if signed_in else LOGIN_PAGE)
        assert auth_browser.manual_login(url).ok
        lr = generic_import.fetch_page(url, client=make_client("generic", ScriptedTransport({})))
        assert set(lr.resource_types) >= {"TEXT", "IMAGES", "VIDEO", "SUBTITLES"}
        assert lr.content_access == "MIXED"
        cands = ai_extract.comic_candidates(lr.html, url)
        by_attr = {c.attr for c in cands}
        assert {"srcset", "data-src", "manifest"} <= by_attr
        assert any(c.url.endswith("p1-1200.webp") for c in cands)      # <picture> best source
        kinds = {c.kind for c in ai_extract.media_candidates(lr.html, url)}
        assert {"video", "subtitle"} <= kinds
        assert ladder.load_capabilities("generic").content_access_status == "MIXED"


# ---------------------------------------------------------------------------
# Item 1 / exit condition 2: nothing session-shaped ever reaches the model
# ---------------------------------------------------------------------------

def _tree(obj):
    return ast.parse(textwrap.dedent(inspect.getsource(obj)))


def _identifiers(tree) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            out.add(node.arg)
    return out


def _imports(tree) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = ("." * node.level) + (node.module or "")
            out.add(mod)
            out.update(f"{mod}:{a.name}" for a in node.names)
    return out


SESSION_IDENTIFIERS = {"cookies", "cookie", "add_cookies", "storage_state", "context",
                       "launch_persistent_context", "profile_dir", "browser_profile_dir",
                       "fetch_with_profile", "open_login_window", "auth_browser", "headers",
                       "session"}


class TestPromptNeverCarriesSession:
    """Exit condition 2, checked on the parsed code -- so honest prose
    about cookies and tokens in docstrings can't trip it, and a later edit
    that wires session state toward the prompt can't slip past it."""

    def _sources_modules(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sources")
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(dirpath, f)
                    yield path, ast.parse(open(path, encoding="utf-8").read())

    def test_the_only_llm_call_in_sources_goes_through_prompt_safe(self):
        calls = []
        for path, tree in self._sources_modules():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    f = node.func
                    name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                    if name == "call_llm_json":
                        calls.append((os.path.basename(path), node))
        assert [p for p, _ in calls] == ["ai_extract.py"]
        prompt_arg = calls[0][1].args[1]
        assert isinstance(prompt_arg, ast.Call) and prompt_arg.func.id == "prompt_safe"

    @pytest.mark.parametrize("fn", [
        ai_extract.ask_json, ai_extract.prompt_safe, ai_extract.blocks_payload,
        ai_extract.comic_payload, ai_extract._desc, ai_extract.media_payload,
        adaptive._llm_picks, adaptive.extract_novel, adaptive._classify_comic,
        adaptive.classify_comic_page, adaptive.identify_media])
    def test_prompt_building_code_never_touches_session_state(self, fn):
        found = _identifiers(_tree(fn)) & SESSION_IDENTIFIERS
        assert not found, f"{fn.__qualname__} references {found}"

    def test_prompt_constants_carry_no_session_state(self):
        for name in ("NOVEL_PROMPT", "COMIC_PROMPT", "MEDIA_PROMPT"):
            text = getattr(ai_extract, name).lower()
            assert "cookie" not in text and "sessdata" not in text

    def test_llm_modules_never_import_the_session_layer(self):
        for mod in (ai_extract, adaptive):
            imported = _imports(_tree(mod))
            assert not any("auth_browser" in i or "fetch_with_profile" in i or
                           "open_login_window" in i for i in imported), mod.__name__

    def test_session_layer_never_imports_the_llm(self):
        for mod in (auth_browser,):
            imported = _imports(_tree(mod))
            assert not any("translate_engines" in i or "ai_extract" in i or "adaptive" in i
                           for i in imported)
        for fn in (page_fetch.fetch_with_profile, page_fetch.open_login_window,
                   page_fetch._launch_persistent):
            assert "call_llm_json" not in _identifiers(_tree(fn))

    def test_the_app_never_reads_session_data_at_all(self):
        """The cookies stay inside Chromium's profile: no code path in the
        session layer reads, copies or injects them."""
        forbidden = {"cookies", "add_cookies", "storage_state", "clear_cookies"}
        for obj in (auth_browser, page_fetch.fetch_with_profile, page_fetch.open_login_window,
                    page_fetch._launch_persistent, page_fetch._shut, ladder.authenticated_tier,
                    ladder._browser_outcome):
            assert not (_identifiers(_tree(obj)) & forbidden), obj

    def test_behaviour_signed_in_import_sends_no_session_or_token(self, isolated_db, fake_browser,
                                                                   monkeypatch):
        """End to end, through the real AI tier: a signed-in comic page
        whose image URLs and links carry session tokens, classified by a
        (fake) engine -- the prompt holds the page's content and ids,
        never a cookie or a token, and ids still map results back."""
        import translate_engines
        prompts = []

        def fake_llm(engine, prompt, max_tokens=2000, fallback="[]", **kw):
            prompts.append(prompt)
            if "IMAGES:" in prompt:
                return json.dumps({"images": [{"id": f"i{i}", "role": "content", "page": i + 1,
                                               "confidence": 0.9} for i in range(4)]})
            return json.dumps({"resources": [{"id": "m0", "role": "main"}]})
        monkeypatch.setattr(translate_engines, "call_llm_json", fake_llm)

        class Engine:
            supports_reference = True
            model = "fake"

        url = "https://tokens.site.invalid/ch/1"
        fake_browser(gated(comic_page))
        assert auth_browser.manual_login(url).ok
        lr = generic_import.fetch_page(url, client=make_client("generic", ScriptedTransport({})))
        assert TOKEN_SECRET in lr.html                  # the page really carries them
        data, report = adaptive.classify_comic_page(lr.html, url, Engine(), use_cache=False)
        assert report.llm_calls == 1 and prompts
        content = [p["resource_url"] for p in data["pages"] if p["role"] == "content"]
        assert len(content) == 4 and all(TOKEN_SECRET in u for u in content)  # app keeps real URLs
        # The novel tier's prompt carries the page's links too.
        adaptive.extract_novel(lr.html, url, Engine(), use_cache=False)
        comic_prompt = next(p for p in prompts if "IMAGES:" in p)
        novel_prompt = next(p for p in prompts if "BLOCKS:" in p)
        assert "token=[REDACTED]" in comic_prompt
        assert "auth_key=[REDACTED]" in novel_prompt and "下一话" in novel_prompt
        for p in prompts:
            assert SESSION_SECRET not in p and TOKEN_SECRET not in p
            assert "SESSDATA" not in p

    def test_prompt_safe_keeps_ordinary_parameters(self):
        s = ai_extract.prompt_safe("https://x.invalid/read?chapter=12&page=3&sign=abc&Key-Pair-Id=K1"
                                   "&X-Amz-Signature=deadbeef&access_token=zzz")
        assert "chapter=12" in s and "page=3" in s
        for secret in ("abc", "K1", "deadbeef", "zzz"):
            assert f"={secret}" not in s

    def test_prompt_safe_redacts_a_path_embedded_signed_token(self):
        """Step 28 gap 3: _SENSITIVE_PARAM only matched a token shaped as a
        `?key=value` query parameter -- a CDN carrying the same kind of
        session-derived credential as a raw path segment (no `=` at all)
        passed through untouched."""
        token = "aB3dEf9012345678gH-token_SECRET"
        s = ai_extract.prompt_safe(f"https://cdn.example.invalid/priv/{token}/page1.jpg")
        assert token not in s
        assert "https://cdn.example.invalid/priv/[REDACTED]/page1.jpg" == s

    def test_prompt_safe_keeps_an_ordinary_chapter_or_page_slug(self):
        """The same fix must not redact a real, non-token-shaped path
        segment -- a slug never sits right after a segment literally named
        token/auth/priv/etc., so it's never touched."""
        s = ai_extract.prompt_safe(
            "https://cdn.example.invalid/some-manga-title-chapter-104-full-release-eng/page-012.jpg")
        assert "some-manga-title-chapter-104-full-release-eng" in s
        assert "page-012.jpg" in s

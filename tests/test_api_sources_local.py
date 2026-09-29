"""Sources: check-now and the tracked-series drama link (S-7), the PC-only
sign-in (S-6), per-tier "Test now" (SO17) and the proxy URL (SO18). Fake
adapters and a fake browser; no network, no Playwright."""
import os
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, url_guard
from services import sources_signin_service as signin
from sources import auth_browser, chapter_check, ladder, registry, store
from sources.auth_browser import LoginCheck
from sources.base import SourceAdapter
from sources.http import PacingPolicy
from sources.ladder import TierOutcome
from sources.models import ChapterInfo, SeriesInfo, TermsProhibited
from tests.sources_helpers import ScriptedTransport

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
SITE = "https://site.example"
PAGE = f"{SITE}/book/7?t={SECRET}"


def _make(name, auth=False, login_url="", chapters=("c1",), gate=None, login=None):
    class Fake(SourceAdapter):
        pass

    Fake.name = name
    Fake.display_name = name.title()
    Fake.url_patterns = [r"site\.example/book/\d+"]
    Fake.auth_supported = auth
    Fake.login_url = login_url
    Fake.chapter_ids = list(chapters)

    def __init__(self, client=None, **kw):
        kw.setdefault("transport", ScriptedTransport({}))
        kw["policy"] = PacingPolicy(min_delay=0.0, max_delay=0.0, session_break_min_requests=0)
        SourceAdapter.__init__(self, client, **kw)

    def get_series(self, series_id):
        return SeriesInfo(name, series_id, "T", f"{SITE}/s/{series_id}")

    def get_chapters(self, series_id):
        if gate is not None:
            gate.wait(5)
        return [ChapterInfo(name, series_id, c, f"第{c}章", f"{SITE}/c/{c}")
                for c in Fake.chapter_ids]

    def get_chapter_text(self, ch):
        return "x"

    Fake.__init__ = __init__
    Fake.get_series = get_series
    Fake.get_chapters = get_chapters
    Fake.get_chapter_text = get_chapter_text
    if login is not None:
        Fake.login = login
    return Fake


@pytest.fixture
def fakes(isolated_db, monkeypatch):
    classes = {}
    monkeypatch.setattr(registry, "adapter_classes", lambda: dict(classes))
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])
    yield classes
    for jid in list(background_jobs.list_all_jobs()):
        if jid.startswith(("sources_", "sourceimport_")):
            _wait(jid)
            background_jobs.clear_job(jid)


@pytest.fixture
def client(fakes):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _wait(job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if not st or st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _result(client, job_id):
    _wait(job_id)
    r = client.get(f"/api/sources/jobs/{job_id}/result")
    assert SECRET not in r.text and db.LIBRARY_DIR not in r.text
    return r


def _code(r):
    return r.json()["error"]["code"]


def _remote_session(c, *perms):
    u = auth_service.add_user("kid@example.com")
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


# ---------------------------------------------------------------------------
# Check now (S-7)
# ---------------------------------------------------------------------------

def test_check_now_needs_a_tracked_series(client, fakes):
    r = client.post("/api/sources/check-now")
    assert r.status_code == 422 and _code(r) == "validation_error"


def test_check_now_announces_new_chapters(client, fakes):
    fakes["alpha"] = cls = _make("alpha", chapters=("c1",))
    store.track_series("alpha", "s1", "Alpha", "", None,
                       known_chapters=[ChapterInfo("alpha", "s1", "c1", "1", "")])
    cls.chapter_ids = ["c1", "c2"]
    r = client.post("/api/sources/check-now")
    assert r.status_code == 200 and r.json() == {"job_id": chapter_check.CHECK_JOB_ID}
    body = _result(client, chapter_check.CHECK_JOB_ID).json()
    assert body["status"] == "done"
    assert body["result"]["checked"] == 1 and body["result"]["new"] == 1
    notes = client.get("/api/sources/notifications").json()
    assert [n["chapter_id"] for n in notes] == ["c2"]


def test_check_now_second_start_is_409(client, fakes):
    gate = threading.Event()
    fakes["alpha"] = _make("alpha", gate=gate)
    store.track_series("alpha", "s1", "Alpha")
    try:
        assert client.post("/api/sources/check-now").status_code == 200
        r = client.post("/api/sources/check-now")
        assert r.status_code == 409 and r.json()["error"]["details"]["job_id"] == "sources_chapter_check"
    finally:
        gate.set()
        _wait(chapter_check.CHECK_JOB_ID)


def test_check_now_errors_are_scrubbed(client, fakes, monkeypatch):
    fakes["alpha"] = _make("alpha")
    store.track_series("alpha", "s1", "Alpha")

    def boom(self, series_id):
        raise RuntimeError(f"failed {SITE}/x?key={SECRET} in {db.LIBRARY_DIR}/sources.db")
    monkeypatch.setattr(fakes["alpha"], "get_chapters", boom)
    client.post("/api/sources/check-now")
    body = _result(client, chapter_check.CHECK_JOB_ID).json()
    assert body["result"]["checked"] == 0 and "Alpha" in body["result"]["errors"]
    assert SECRET not in client.get("/api/sources/tracked").text


# ---------------------------------------------------------------------------
# Tracked series: auto-import drama
# ---------------------------------------------------------------------------

def test_tracked_drama_set_and_clear(client, fakes):
    fakes["alpha"] = _make("alpha")
    store.track_series("alpha", "s1", "Alpha")
    novel = db.create_drama(title_en="N", media_type="novel")
    comic = db.create_drama(title_en="C", media_type="manhua")
    body = {"source": "alpha", "series_id": "s1", "drama_id": novel}
    r = client.post("/api/sources/tracked/drama", json=body)
    assert r.status_code == 200 and r.json()[0]["drama_id"] == novel
    r = client.post("/api/sources/tracked/drama", json={**body, "drama_id": comic})
    assert r.status_code == 422                        # a text source feeds a novel drama
    r = client.post("/api/sources/tracked/drama", json={**body, "drama_id": 9999})
    assert r.status_code == 404
    r = client.post("/api/sources/tracked/drama", json={**body, "drama_id": None})
    assert r.status_code == 200 and r.json()[0]["drama_id"] is None
    r = client.post("/api/sources/tracked/drama", json={**body, "series_id": "nope"})
    assert r.status_code == 404
    r = client.post("/api/sources/tracked/drama", json={**body, "series_id": "../x"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Sign-in (S-6)
# ---------------------------------------------------------------------------

def test_signin_refuses_sources_without_signin_and_off_site_urls(client, fakes):
    fakes["plain"] = _make("plain")
    fakes["alpha"] = _make("alpha", auth=True)
    r = client.post("/api/sources/plain/signin/open", json={"url": PAGE})
    assert r.status_code == 400
    r = client.post("/api/sources/alpha/signin/open", json={"url": ""})
    assert r.status_code == 422                        # no login page: a page is needed
    for bad in ("https://evil.example/book/7", "https://site.example.evil.example/book/7",
                "file:///etc/passwd", "http://127.0.0.1/book/7", "javascript:alert(1)"):
        r = client.post("/api/sources/alpha/signin/open", json={"url": bad})
        assert r.status_code == 422, bad
        assert "evil" not in r.text and "127.0.0.1" not in r.text
    assert client.post("/api/sources/nope/signin/open", json={}).status_code == 404


def test_signin_job_result_is_scrubbed_and_pc_only(fakes, monkeypatch):
    seen = []

    def login(self, url="", launcher=None):
        seen.append(url)
        return LoginCheck(url=url, ok=True, message=f"Signed in at {url}",
                          lines=[f"profile {db.LIBRARY_DIR}/browser_profiles/alpha"])
    fakes["alpha"] = _make("alpha", auth=True, login_url=f"{SITE}/login", login=login)
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = c.post("/api/sources/alpha/signin/open", json={})
    assert r.status_code == 200 and r.json()["job_id"] == "sources_signin_alpha"
    body = _result(c, "sources_signin_alpha").json()
    assert seen == [f"{SITE}/login"]
    res = body["result"]
    assert res["kind"] == "signin" and res["ok"] is True
    assert "?" not in res["message"] and "[path]" in res["lines"][0]

    # A page on the site (a subdomain of the login host counts) is used as given.
    assert c.post("/api/sources/alpha/signin/open",
                  json={"url": "https://m.site.example/other"}).status_code == 200
    _wait("sources_signin_alpha")
    assert seen[-1] == "https://m.site.example/other"

    # Another device never sees a PC-only job's result, even with library.read.
    on = TestClient(create_app(ApiSettings(auth_mode="on")),
                    base_url="https://baihe.example.com", raise_server_exceptions=False)
    h = _remote_session(on)
    assert on.get("/api/sources/jobs/sources_signin_alpha/result", headers=h).status_code == 404
    assert on.post("/api/sources/alpha/signin/open", json={}, headers=h).status_code == 403


def test_signin_errors_map(client, fakes):
    from page_fetch import ProfileBusy

    def busy(self, url="", launcher=None):
        raise ProfileBusy(f"busy at {db.LIBRARY_DIR}/browser_profiles/alpha")

    def terms(self, url="", launcher=None):
        raise TermsProhibited("restricted")

    def missing(self, url="", launcher=None):
        raise ImportError("No module named playwright")

    for fn, status in ((busy, 409), (terms, 400), (missing, 503)):
        fakes["alpha"] = _make("alpha", auth=True, login_url=f"{SITE}/login", login=fn)
        assert client.post("/api/sources/alpha/signin/open", json={}).status_code == 200
        r = _result(client, "sources_signin_alpha")
        assert r.status_code == status, (fn.__name__, r.text)
        background_jobs.clear_job("sources_signin_alpha")


def test_forget_signin(client, fakes):
    fakes["alpha"] = _make("alpha", auth=True, login_url=f"{SITE}/login")
    d = auth_browser.profile_dir("", "alpha")
    os.makedirs(d)
    open(os.path.join(d, "Cookies"), "w").close()
    assert client.get("/api/sources").json()[0]["has_saved_signin"] is True
    r = client.post("/api/sources/alpha/signin/forget", json={})
    assert r.status_code == 422 and os.path.isdir(d)
    r = client.post("/api/sources/alpha/signin/forget", json={"confirm": True})
    assert r.status_code == 200
    assert r.json() == {"source": "alpha", "forgotten": True, "has_saved_signin": False}
    assert not os.path.exists(d) and "Cookies" not in r.text and db.LIBRARY_DIR not in r.text


def test_forget_signin_while_open_is_409(client, fakes):
    from page_fetch import _profile_lock
    fakes["alpha"] = _make("alpha", auth=True, login_url=f"{SITE}/login")
    d = auth_browser.profile_dir("", "alpha")
    os.makedirs(d)
    lock = _profile_lock(d)
    assert lock.acquire(blocking=False)
    try:
        r = client.post("/api/sources/alpha/signin/forget", json={"confirm": True})
        assert r.status_code == 409 and os.path.isdir(d)
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Test one tier (SO17)
# ---------------------------------------------------------------------------

def test_tier_test_runs_one_tier(client, fakes, monkeypatch):
    fakes["alpha"] = _make("alpha", auth=True)
    urls = []

    def fake_static(_client):
        def run(url):
            urls.append(url)
            return TierOutcome(True, html="<p>ok</p>")
        return run
    monkeypatch.setattr(ladder, "static_tier", fake_static)
    r = client.post("/api/sources/alpha/tier-test", json={"tier": "static", "url": PAGE})
    assert r.status_code == 200 and r.json()["job_id"] == "sources_tiertest_alpha"
    res = _result(client, "sources_tiertest_alpha").json()["result"]
    assert res == {"kind": "tier_test", "source": "alpha", "tier": "static", "ok": True,
                   "reason": None, "detail": ""}
    assert urls == [PAGE]
    detail = client.get("/api/sources/alpha").json()
    assert detail["tiers"]["STATIC_HTTP"]["tested"] is True


def test_tier_test_validation(client, fakes):
    fakes["alpha"] = _make("alpha", auth=True)
    assert client.post("/api/sources/alpha/tier-test",
                       json={"tier": "magic", "url": PAGE}).status_code == 422
    assert client.post("/api/sources/alpha/tier-test",
                       json={"tier": "static", "url": "https://evil.example/"}).status_code == 422
    r = client.post("/api/sources/alpha/tier-test", json={"tier": "signed_in", "url": PAGE})
    assert r.status_code == 422 and "Sign in" in r.json()["error"]["message"]
    assert not os.path.exists(auth_browser.profile_dir("", "alpha"))


# ---------------------------------------------------------------------------
# Proxy (SO18)
# ---------------------------------------------------------------------------

def test_proxy_set_clear_and_never_echoed(client, fakes):
    secret_proxy = "http://user:hunter2@127.0.0.1:8080"
    r = client.post("/api/sources/settings/proxy", json={"url": secret_proxy})
    assert r.status_code == 200 and r.json()["proxy_configured"] is True
    assert "hunter2" not in r.text and "8080" not in r.text
    assert store.get_setting("http_proxy_url") == secret_proxy
    assert "hunter2" not in client.get("/api/sources/settings").text
    for bad in ("socks5://10.9.8.7:1080", "ftp://x", "http://", "http://h:99999",
                "http://h/path", "http://h?q=1", "http://a b", "x" * 600):
        r = client.post("/api/sources/settings/proxy", json={"url": bad})
        assert r.status_code == 422, bad
        assert "10.9.8.7" not in r.text
    assert store.get_setting("http_proxy_url") == secret_proxy
    r = client.post("/api/sources/settings/proxy", json={"url": "  "})
    assert r.status_code == 200 and r.json()["proxy_configured"] is False


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def test_auth_on_permissions(fakes):
    fakes["alpha"] = _make("alpha", auth=True, login_url=f"{SITE}/login")
    store.track_series("alpha", "s1", "Alpha")
    c = TestClient(create_app(ApiSettings(auth_mode="on")),
                   base_url="https://baihe.example.com", raise_server_exceptions=False)
    assert c.post("/api/sources/check-now").status_code == 401
    h = _remote_session(c)
    assert c.post("/api/sources/check-now", headers=h).status_code == 403
    assert c.post("/api/sources/tracked/drama", headers=h,
                  json={"source": "alpha", "series_id": "s1", "drama_id": None}).status_code == 403
    for path, body in (("/api/sources/settings/proxy", {"url": ""}),
                       ("/api/sources/alpha/signin/open", {}),
                       ("/api/sources/alpha/signin/forget", {"confirm": True}),
                       ("/api/sources/alpha/tier-test", {"tier": "static", "url": PAGE})):
        assert c.post(path, json=body, headers=h).status_code == 403, path
    admin = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(admin["id"])
    ha = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
          api_auth.CSRF_HEADER: s["csrf_token"]}
    # PC-only means PC-only, for an admin too.
    assert c.post("/api/sources/settings/proxy", json={"url": ""}, headers=ha).status_code == 403
    auth_service.grant_permission(admin["id"], "sources.import")
    assert c.post("/api/sources/check-now", headers=ha).status_code == 200
    _wait(chapter_check.CHECK_JOB_ID)
    assert c.get("/api/sources/jobs/sources_chapter_check/result", headers=ha).status_code == 200

"""Sources parity SO09/SO06/SO10: the pasted-URL AI fallback, comic import
into Scanlate and the Review extraction step (api/routers/
sources_extraction_routes.py, services/sources_extraction_service.py).

DNS, every fetch and the LLM are faked; no network, no real model. The
extraction ladder itself (sources/adaptive.py) runs for real."""
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import translate_engines
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, settings_service, url_guard
from sources import generic_import, registry
from sources.ladder import LadderResult
from sources.models import AccessTier, AttemptRecord

from .test_adaptive_extraction import (FakeEngine, _HTML_BY_URL, chapter_html, chapter_url,
                                       fake_llm, novel_answer)  # noqa: F401 (fixture)

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"


def _dns(monkeypatch, ip="93.184.216.34"):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", (ip, port))])


class FakeFetch:
    """Stands in for generic_import.fetch_page: serves `pages[url]`, or a
    verification-page hand-off for a URL in `handoffs`."""

    def __init__(self):
        self.pages, self.calls, self.handoffs, self.clients = {}, [], set(), []

    def __call__(self, url, client=None, rendered_fetch=None, user_html=None,
                 authenticated_fetch=None, allow_signed_in=True, allow_browser=True,
                 record=True):
        self.calls.append({"url": url, "signed_in": allow_signed_in, "browser": allow_browser})
        self.clients.append(client)
        lr = LadderResult(url)
        if url in self.handoffs:
            lr.handoff = {"tier": "STATIC_HTTP", "reason": "CAPTCHA", "url": url}
            return lr
        lr.tier, lr.html = AccessTier.STATIC_HTTP.value, self.pages[url]
        return lr


class Engines:
    """translate_engines.get_engine stand-in: records (name, key) and hands
    out one FakeEngine per call."""

    def __init__(self, answer=None):
        self.answer, self.built, self.engines = answer, [], []

    def __call__(self, name, api_key=None, model=None, free_tier=False, base_url=None):
        self.built.append((name, api_key))
        e = FakeEngine(self.answer or (lambda prompt: None))
        self.engines.append(e)
        return e


@pytest.fixture
def env(isolated_db, monkeypatch, fake_llm):  # noqa: F811
    monkeypatch.setattr(registry, "adapter_classes", lambda: {})
    _dns(monkeypatch)
    fetch = FakeFetch()
    monkeypatch.setattr(generic_import, "fetch_page", fetch)
    keys = {"claude": SECRET}
    monkeypatch.setattr(settings_service, "resolve_key", lambda name, env_path=None: keys.get(name))
    engines = Engines()
    monkeypatch.setattr(translate_engines, "get_engine", engines)
    from services import sources_extraction_service as svc
    monkeypatch.setattr(svc, "_REVIEWS", {})    # reviews are per process, drama ids per test DB
    yield {"fetch": fetch, "keys": keys, "engines": engines}
    for jid in list(background_jobs.list_all_jobs()):
        if jid.startswith(("sources_", "sourceimport_")):
            _wait(jid)
            background_jobs.clear_job(jid)


@pytest.fixture
def client(env):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _wait(job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if not st or st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _novel_page(env, n=12, **kw):
    url = chapter_url(n)
    html = chapter_html(n, **kw)
    _HTML_BY_URL[url] = html
    env["fetch"].pages[url] = html
    return url


def _run(client, path, body, did):
    r = client.post(path, json=body)
    assert r.status_code == 200, r.text
    assert r.json() == {"job_id": f"sourceimport_{did}"}
    _wait(f"sourceimport_{did}")
    return client.get(f"/api/sources/jobs/sourceimport_{did}/result")


def _raw(did):
    import os
    from sources import pipeline
    path = os.path.join(db.drama_dir(did), pipeline.RAW_NOVEL_FILENAME)
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


# ---------------------------------------------------------------------------
# SO09: the AI-assisted fallback, opt-in, with an engine picker
# ---------------------------------------------------------------------------

def test_engine_list_names_only(client):
    r = client.get("/api/sources/url/ai-engines")
    assert r.status_code == 200
    body = r.json()
    assert "claude" in body["engines"]
    assert body["default"] == "claude"
    assert SECRET not in r.text and "key" not in r.text.lower()


def test_default_engine_not_usable_is_none(client, monkeypatch):
    monkeypatch.setattr(settings_service, "get_default_engine", lambda: "google")
    assert client.get("/api/sources/url/ai-engines").json()["default"] is None
    did = db.create_drama(title_en="N", media_type="novel")
    r = client.post("/api/sources/url/import",
                    json={"url": chapter_url(12), "drama_id": did, "use_ai": True})
    assert r.status_code == 422


def test_off_by_default_no_engine_built(client, env):
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    r = _run(client, "/api/sources/url/import", {"url": url, "drama_id": did}, did)
    assert r.json()["result"]["needs_review"] is False
    assert env["engines"].built == []


def test_clear_page_makes_no_ai_call_even_when_opted_in(client, env):
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    r = _run(client, "/api/sources/url/import",
             {"url": url, "drama_id": did, "use_ai": True}, did)
    assert r.json()["result"]["needs_review"] is False
    assert env["engines"].built == [("claude", SECRET)]
    assert env["engines"].engines[0].calls == []      # deterministic worked: no call
    assert "第12章第0段" in _raw(did)


def test_ambiguous_page_asks_the_engine_once(client, env, monkeypatch):
    monkeypatch.setattr(generic_import, "extract_main_text",
                        lambda html, url="": ("\n".join(["首页 目录 排行"] * 30), "heuristic"))
    env["engines"].answer = novel_answer()
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    r = _run(client, "/api/sources/url/import",
             {"url": url, "drama_id": did, "use_ai": True, "engine": "claude"}, did)
    assert r.status_code == 200 and r.json()["result"]["needs_review"] is False
    assert len(env["engines"].engines[0].calls) == 1
    raw = _raw(did)
    assert "第12章第0段" in raw and "广告" not in raw
    assert SECRET not in env["engines"].engines[0].calls[0]


def test_engine_checks_before_any_job(client, env):
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    for engine in ("google", "nope"):
        r = client.post("/api/sources/url/import",
                        json={"url": url, "drama_id": did, "use_ai": True, "engine": engine})
        assert r.status_code == 422, engine
    env["keys"].clear()
    r = client.post("/api/sources/url/import",
                    json={"url": url, "drama_id": did, "use_ai": True, "engine": "claude"})
    assert r.status_code == 503 and "Settings" in r.json()["error"]["message"]
    assert background_jobs.get_status(f"sourceimport_{did}") is None
    assert env["fetch"].calls == []


def test_engine_setup_failure_is_redacted(client, env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(f"bad key {SECRET}")
    monkeypatch.setattr(translate_engines, "get_engine", boom)
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    r = client.post("/api/sources/url/import",
                    json={"url": url, "drama_id": did, "use_ai": True})
    assert r.status_code == 503 and SECRET not in r.text


def test_ollama_needs_no_key(client, env):
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    env["keys"].clear()
    _run(client, "/api/sources/url/import",
         {"url": url, "drama_id": did, "use_ai": True, "engine": "ollama"}, did)
    assert env["engines"].built == [("ollama", "local")]


def _remote_user(*perms):
    return _remote_user_named("kid@example.com", *perms)


def _remote_user_named(email, *perms):
    c = TestClient(create_app(ApiSettings(auth_mode="on")),
                   base_url="https://baihe.example.com", raise_server_exceptions=False)
    u = auth_service.add_user(email)
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return c, {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
               api_auth.CSRF_HEADER: s["csrf_token"]}


def test_auth_on_paid_engine_needs_engines_paid(env):
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    c, h = _remote_user("sources.import")
    assert c.get("/api/sources/url/ai-engines", headers=h).status_code == 200
    r = c.post("/api/sources/url/import",
               json={"url": url, "drama_id": did, "use_ai": True, "engine": "claude"}, headers=h)
    assert r.status_code == 403
    r = c.post("/api/sources/url/import",
               json={"url": url, "drama_id": did, "use_ai": True, "engine": "ollama"}, headers=h)
    assert r.status_code == 200
    _wait(f"sourceimport_{did}")
    assert env["fetch"].calls[-1]["signed_in"] is False


# ---------------------------------------------------------------------------
# SO06: comic pages from a pasted URL into Scanlate, with the skipped list
# ---------------------------------------------------------------------------

COMIC_URL = f"https://comic.example/read/77/5?token={SECRET}"


def _png(w, h, shade):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (shade, shade, shade)).save(buf, "PNG")
    return buf.getvalue()


def comic_page_html(n_pages=3):
    pages = "".join(f'<img src="https://img.comic.example/77/5/{i:03d}.png">'
                    for i in range(1, n_pages + 1))
    return ("<html><head><title>第5话</title></head><body><h1>第5话</h1>"
            '<img src="https://comic.example/icon.png">'
            f'<div id="reader">{pages}</div></body></html>')


class FakeImages:
    """The paced client's image downloads: url -> (content-type, bytes)."""

    def __init__(self):
        self.files, self.got = {}, []

    def get(self, url, classify_body=True, headers=None, action=""):
        from sources.http import Response
        self.got.append(url)
        ctype, body = self.files[url]
        return Response(200, {"Content-Type": ctype}, body, url)


@pytest.fixture
def comic(env, monkeypatch):
    from services import sources_import_service as imp
    images = FakeImages()
    images.files["https://comic.example/icon.png"] = ("image/png", _png(48, 48, 10))
    for i in range(1, 7):
        images.files[f"https://img.comic.example/77/5/{i:03d}.png"] = ("image/png", _png(800, 1200, 20 * i))
    monkeypatch.setattr(imp, "source_client", lambda url, job_id: images)
    env["fetch"].pages[COMIC_URL] = comic_page_html()
    return images


def _comic_drama():
    return db.create_drama(title_en="C", media_type="manhua")


def test_comic_import_adds_pages_and_lists_skipped(client, env, comic):
    did = _comic_drama()
    r = _run(client, "/api/sources/url/import-comic", {"url": COMIC_URL, "drama_id": did}, did)
    res = r.json()["result"]
    assert res["kind"] == "comic_import" and res["needs_review"] is False
    assert res["pages_added"] == 3 and len(db.list_pages(did)) == 3
    assert res["skipped_count"] == 1
    assert res["skipped"] == [{"display_url": "https://comic.example/icon.png",
                               "reason": "too small to be a page (48x48)"}]
    assert SECRET not in r.text and "token" not in r.text and "<html" not in r.text
    assert env["engines"].built == []
    assert env["fetch"].calls[-1]["signed_in"] is True


def test_comic_import_checks_before_any_job(client, env, comic):
    novel = db.create_drama(title_en="N", media_type="novel")
    r = client.post("/api/sources/url/import-comic", json={"url": COMIC_URL, "drama_id": novel})
    assert r.status_code == 422
    did = _comic_drama()
    assert client.post("/api/sources/url/import-comic",
                       json={"url": COMIC_URL, "drama_id": 99999}).status_code == 404
    assert client.post("/api/sources/url/import-comic",
                       json={"url": "file:///etc/passwd", "drama_id": did}).status_code == 422
    assert client.post("/api/sources/url/import-comic",
                       json={"url": COMIC_URL, "drama_id": did, "use_ai": True,
                             "engine": "google"}).status_code == 422
    assert client.post("/api/sources/url/import-comic",
                       json={"url": COMIC_URL, "drama_id": did, "html": "x"}).status_code == 422
    assert env["fetch"].calls == [] and comic.got == []


def test_comic_private_address_refused(client, env, comic, monkeypatch):
    _dns(monkeypatch, "10.0.0.5")
    did = _comic_drama()
    r = client.post("/api/sources/url/import-comic", json={"url": COMIC_URL, "drama_id": did})
    assert r.status_code == 422 and env["fetch"].calls == []


def test_comic_download_caps_and_content_type(client, env, comic, monkeypatch):
    env["fetch"].pages[COMIC_URL] = comic_page_html(6)
    comic.files["https://img.comic.example/77/5/002.png"] = ("text/html", b"<html>not an image</html>")
    one = max(len(comic.files[f"https://img.comic.example/77/5/{i:03d}.png"][1]) for i in (1, 3, 4))
    icon = len(comic.files["https://comic.example/icon.png"][1])
    from services import page_import_limits as limits
    monkeypatch.setattr(limits, "MAX_IMPORT_BYTES", icon + one * 3 + 10)   # icon + 3 pages
    monkeypatch.setattr(limits, "MAX_FILES_PER_IMPORT", 6)                 # attempts
    did = _comic_drama()
    res = _run(client, "/api/sources/url/import-comic", {"url": COMIC_URL, "drama_id": did}, did)
    res = res.json()["result"]
    reasons = {s["display_url"].rsplit("/", 1)[-1]: s["reason"] for s in res["skipped"]}
    assert reasons["002.png"] == "couldn't download (the server didn't send an image)"
    assert "total size limit" in reasons["005.png"]
    assert reasons["006.png"] == "not downloaded (too many images on the page)"
    assert "006.png" not in " ".join(comic.got)          # never requested
    assert res["pages_added"] == len(db.list_pages(did)) == 3


def test_comic_needs_review_writes_nothing(client, env, comic, monkeypatch):
    from types import SimpleNamespace
    from services import sources_import_service as imp
    from sources.generic_import import ComicImportResult

    def fake(url, engine=None, client=None, **kw):
        return (ComicImportResult(url, images=[], rejected=[], ladder=LadderResult(url)),
                SimpleNamespace(needs_review=True, data=None))
    monkeypatch.setattr(imp.adaptive, "import_comic", fake)
    did = _comic_drama()
    res = _run(client, "/api/sources/url/import-comic", {"url": COMIC_URL, "drama_id": did}, did)
    assert res.json()["result"]["needs_review"] is True
    assert db.list_pages(did) == []


def test_comic_no_pages_and_handoff(client, env, comic, monkeypatch):
    env["fetch"].pages[COMIC_URL] = "<html><body><p>nothing here</p></body></html>"
    did = _comic_drama()
    r = _run(client, "/api/sources/url/import-comic", {"url": COMIC_URL, "drama_id": did}, did)
    assert r.status_code == 422 and SECRET not in r.text
    err = r.json()["error"]
    assert err["message"].startswith("No comic pages were found") and "Why:" in err["message"]
    assert err["details"]["reason"] == "NO_CONTENT"

    def challenge(url, client=None, rendered_fetch=None, user_html=None, authenticated_fetch=None,
                  allow_signed_in=True, allow_browser=True, record=True):
        lr = LadderResult(url)
        lr.handoff = {"tier": "STATIC_HTTP", "reason": "CAPTCHA", "url": url}
        return lr
    monkeypatch.setattr(generic_import, "fetch_page", challenge)
    r = _run(client, "/api/sources/url/import-comic", {"url": COMIC_URL, "drama_id": did}, did)
    assert r.status_code == 409 and r.json()["error"]["details"]["handoff"] is True
    assert SECRET not in r.text and db.list_pages(did) == []


def _bilibili_no_pages(client, env, monkeypatch, attempt):
    url = "https://manga.bilibili.com/mc40738/2129714?from=manga_detail"
    env["fetch"].pages[url] = "<html><body><div id='app-vm'></div></body></html>"
    inner = env["fetch"]

    def fetch(*a, **kw):
        lr = inner(*a, **kw)
        lr.attempts.append(attempt)
        return lr
    monkeypatch.setattr(generic_import, "fetch_page", fetch)
    did = _comic_drama()
    r = _run(client, "/api/sources/url/import-comic", {"url": url, "drama_id": did}, did)
    assert r.status_code == 422
    assert "from=manga_detail" not in r.text and SECRET not in r.text
    return r.json()["error"]["message"]


def test_comic_no_pages_on_bilibili_manga_says_why_and_what_to_do(client, env, comic, monkeypatch):
    msg = _bilibili_no_pages(client, env, monkeypatch, AttemptRecord(
        tier=AccessTier.RENDERED_BROWSER.value, ok=True))
    assert "Why:" in msg and "Sign in to Bilibili Manga" in msg


def test_comic_no_pages_on_bilibili_manga_without_playwright_names_it(client, env, comic,
                                                                     monkeypatch):
    msg = _bilibili_no_pages(client, env, monkeypatch, AttemptRecord(
        tier=AccessTier.RENDERED_BROWSER.value, ok=False, reason="NOT_INSTALLED",
        missing="playwright"))
    assert "Playwright package is not installed" in msg and "Sign in" not in msg


def test_comic_auth_on_remote_is_static_only(env, comic):
    did = _comic_drama()
    c, h = _remote_user()
    assert c.post("/api/sources/url/import-comic", json={"url": COMIC_URL, "drama_id": did},
                  headers=h).status_code == 403
    c, h = _remote_user_named("b@example.com", "sources.import")
    r = c.post("/api/sources/url/import-comic", json={"url": COMIC_URL, "drama_id": did}, headers=h)
    assert r.status_code == 200
    _wait(f"sourceimport_{did}")
    assert env["fetch"].calls[-1] == {"url": COMIC_URL, "signed_in": False, "browser": False}


# ---------------------------------------------------------------------------
# SO10: Review extraction between the preview and the write
# ---------------------------------------------------------------------------

def _review(client, did):
    r = client.get(f"/api/sources/dramas/{did}/extraction")
    assert r.status_code == 200, r.text
    return r.json()


def _novel_review(client, env, **body):
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    r = _run(client, "/api/sources/url/import", {"url": url, "drama_id": did, **body}, did)
    res = r.json()["result"]
    assert res["needs_review"] is True and res["review_open"] is True
    assert _raw(did) == ""
    return did, _review(client, did)


def _leak_free(text):
    assert SECRET not in text and "<html" not in text and "?" not in text
    assert db.LIBRARY_DIR not in text


def test_asked_review_writes_nothing_and_offers_choices(client, env):
    did, rv = _novel_review(client, env, review=True)
    assert rv["kind"] == "extraction_review" and rv["content_type"] == "novel"
    assert rv["why"] == "asked" and rv["comic"] is None and rv["can_save_profile"] is False
    assert rv["display_url"] == "https://www.novel.example/book/77/1012.html"
    assert rv["confidence"]["overall"]["bucket"] in ("HIGH", "MEDIUM", "LOW", "FAILED")
    n = rv["novel"]
    assert n["char_count"] > 200 and "第12章第0段" in n["text_preview"]
    assert n["content_selector"] in [c["selector"] for c in n["containers"]]
    assert any(h["text"] == "第12章 重逢" for h in n["headings"])
    assert {l["text"] for l in n["links"]} >= {"下一章", "上一章"}
    assert n["next_link"] == next(l["id"] for l in n["links"] if l["text"] == "下一章")
    _leak_free(client.get(f"/api/sources/dramas/{did}/extraction").text)


def test_novel_rerun_leave_out_then_import(client, env):
    did, rv = _novel_review(client, env, review=True)
    n = rv["novel"]
    sel = n["content_selector"]
    container = next(c for c in n["containers"] if c["selector"] == sel)
    ad = next(o["selector"] for o in container["exclusions"] if "广告" in o["preview"])
    body = {"revision": rv["revision"], "content_selector": sel, "exclude_selectors": [ad],
            "title_block": n["title_block"], "next_link": n["next_link"],
            "previous_link": n["previous_link"], "number_from": "url"}
    r = client.post(f"/api/sources/dramas/{did}/extraction/rerun-novel", json=body)
    assert r.status_code == 200, r.text
    rv2 = r.json()
    assert rv2["revision"] != rv["revision"] and rv2["can_save_profile"] is True
    assert "广告" not in rv2["novel"]["text_preview"] and rv2["novel"]["number_from"] == "url"
    assert rv2["novel"]["exclude_selectors"] == [ad]

    # The old revision is stale now.
    assert client.post(f"/api/sources/dramas/{did}/extraction/rerun-novel",
                       json=body).status_code == 409
    assert client.post(f"/api/sources/dramas/{did}/extraction/import",
                       json={"revision": rv["revision"]}).status_code == 409

    r = client.post(f"/api/sources/dramas/{did}/extraction/save-profile",
                    json={"revision": rv2["revision"]})
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["kind"] == "novel"
    from sources import profiles
    assert profiles.active("novel.example", "novel")["origin"] == "correction"

    r = client.post(f"/api/sources/dramas/{did}/extraction/import",
                    json={"revision": rv2["revision"]})
    assert r.status_code == 200 and r.json() == {"job_id": f"sourceimport_{did}"}
    _wait(f"sourceimport_{did}")
    res = client.get(f"/api/sources/jobs/sourceimport_{did}/result").json()["result"]
    assert res["kind"] == "review_import" and res["content_type"] == "novel"
    raw = _raw(did)
    assert "第12章第0段" in raw and "广告" not in raw and raw.startswith("第12章 重逢")
    assert client.get(f"/api/sources/dramas/{did}/extraction").status_code == 404


def test_novel_rerun_refuses_anything_not_offered(client, env):
    did, rv = _novel_review(client, env, review=True)
    n = rv["novel"]
    base = {"revision": rv["revision"], "content_selector": n["content_selector"]}
    url = f"/api/sources/dramas/{did}/extraction/rerun-novel"
    for extra in ({"content_selector": "body *:has(p)"}, {"exclude_selectors": ["div"]},
                  {"title_block": "b99999"}, {"next_link": "L99999"},
                  {"number_from": "html"}, {"html": "<p>x</p>"}):
        r = client.post(url, json={**base, **extra})
        assert r.status_code == 422, extra
    assert client.post(f"/api/sources/dramas/{did}/extraction/save-profile",
                       json={"revision": rv["revision"]}).status_code == 422   # no corrections yet
    assert client.post(f"/api/sources/dramas/{did}/extraction/approve-profile",
                       json={"revision": rv["revision"]}).status_code == 422   # nothing pending
    assert client.post(f"/api/sources/dramas/{did}/extraction/rerun-comic",
                       json={"revision": rv["revision"], "images": []}).status_code == 422


def test_approve_a_held_profile(client, env):
    from services import sources_extraction_service as svc
    from sources import profiles
    did, rv = _novel_review(client, env, review=True)
    live = svc._REVIEWS[did]
    rules = profiles.infer_novel_rules(live.page, live.data)
    live.report.pending_profile = {"domain": "novel.example", "kind": "novel", "rules": rules,
                                   "origin": "llm", "validation": live.data}
    rv = _review(client, did)
    assert rv["report"]["pending_profile"] is not None
    r = client.post(f"/api/sources/dramas/{did}/extraction/approve-profile",
                    json={"revision": rv["revision"]})
    assert r.status_code == 200 and r.json()["version"] == 1
    assert profiles.active("novel.example", "novel")["approved"] is True
    assert _review(client, did)["report"]["pending_profile"] is None


def test_diagnostics_mode_always_reviews(client, env):
    from sources import store
    store.set_setting("extraction_diagnostics", True)
    _did, rv = _novel_review(client, env)
    assert rv["why"] == "diagnostics"


def test_low_confidence_opens_a_review(client, env, monkeypatch):
    from sources import adaptive

    real = adaptive.extract_novel

    def unsure(html, url, engine=None, use_cache=True, report=None, **kw):
        data, report = real(html, url, engine, use_cache, report, **kw)
        report.needs_review = True
        return data, report
    monkeypatch.setattr(adaptive, "extract_novel", unsure)
    _did, rv = _novel_review(client, env)
    assert rv["why"] == "low_confidence"


def test_no_review_is_404_and_a_new_direct_import_drops_the_old_one(client, env):
    did = db.create_drama(title_en="N", media_type="novel")
    assert client.get(f"/api/sources/dramas/{did}/extraction").status_code == 404
    assert client.post(f"/api/sources/dramas/{did}/extraction/import",
                       json={"revision": "x"}).status_code == 404
    did, _rv = _novel_review(client, env, review=True)
    _run(client, "/api/sources/url/import", {"url": chapter_url(12), "drama_id": did}, did)
    assert client.get(f"/api/sources/dramas/{did}/extraction").status_code == 404


def test_reviews_are_capped_and_expire(client, env, monkeypatch):
    from services import sources_extraction_service as svc
    dids = [_novel_review(client, env, review=True)[0] for _ in range(svc.MAX_REVIEWS + 1)]
    assert client.get(f"/api/sources/dramas/{dids[0]}/extraction").status_code == 404
    assert client.get(f"/api/sources/dramas/{dids[-1]}/extraction").status_code == 200
    monkeypatch.setattr(svc, "REVIEW_TTL", -1)
    assert client.get(f"/api/sources/dramas/{dids[-1]}/extraction").status_code == 404


def _comic_review(client, env, comic):
    did = _comic_drama()
    r = _run(client, "/api/sources/url/import-comic",
             {"url": COMIC_URL, "drama_id": did, "review": True}, did)
    res = r.json()["result"]
    assert res["needs_review"] is True and res["review_open"] is True and res["pages_added"] == 0
    assert db.list_pages(did) == []
    return did, _review(client, did)


def test_comic_review_roles_order_thumbnails_and_import(client, env, comic):
    did, rv = _comic_review(client, env, comic)
    c = rv["comic"]
    assert rv["content_type"] == "comic" and rv["novel"] is None and c["page_count"] == 3
    by_name = {i["display_url"].rsplit("/", 1)[-1]: i for i in c["images"]}
    assert by_name["icon.png"]["role"] != "content" and by_name["icon.png"]["page"] == 0
    assert [by_name[f"00{n}.png"]["page"] for n in (1, 2, 3)] == [1, 2, 3]
    assert "content" in c["roles"] and "ad" in c["roles"]
    _leak_free(client.get(f"/api/sources/dramas/{did}/extraction").text)

    img = client.get(f"/api/sources/dramas/{did}/extraction/images/{by_name['002.png']['id']}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in img.headers["content-security-policy"]
    assert img.content == comic.files["https://img.comic.example/77/5/002.png"][1]
    assert client.get(f"/api/sources/dramas/{did}/extraction/images/999").status_code == 404

    # 003 is an ad; 002 is read before 001.
    body = {"revision": rv["revision"], "images": [
        {"id": by_name["003.png"]["id"], "role": "ad", "page": 0},
        {"id": by_name["002.png"]["id"], "role": "content", "page": 1},
        {"id": by_name["001.png"]["id"], "role": "content", "page": 2}]}
    r = client.post(f"/api/sources/dramas/{did}/extraction/rerun-comic", json=body)
    assert r.status_code == 200, r.text
    rv2 = r.json()
    assert rv2["comic"]["page_count"] == 2 and rv2["can_save_profile"] is True
    r = client.post(f"/api/sources/dramas/{did}/extraction/import",
                    json={"revision": rv2["revision"]})
    assert r.status_code == 200
    _wait(f"sourceimport_{did}")
    res = client.get(f"/api/sources/jobs/sourceimport_{did}/result").json()["result"]
    assert res == {"kind": "review_import", "content_type": "comic", "pages_added": 2,
                   "skipped": [], "skipped_count": 0}
    import hashlib
    import os
    pages = db.list_pages(did)
    got = [hashlib.sha256(open(os.path.join(db.drama_dir(did), p["filename"]), "rb").read()).digest()
           for p in sorted(pages, key=lambda p: p["idx"])]
    want = [hashlib.sha256(comic.files[f"https://img.comic.example/77/5/00{n}.png"][1]).digest()
            for n in (2, 1)]
    assert got == want


def test_comic_rerun_refuses_unknown_ids_and_roles(client, env, comic):
    did, rv = _comic_review(client, env, comic)
    url = f"/api/sources/dramas/{did}/extraction/rerun-comic"
    for item in ({"id": 999, "role": "content"}, {"id": 0, "role": "hero"},
                 {"id": 0, "role": "content", "page": 501}, {"id": "0", "role": "content"},
                 {"id": 0, "role": "content", "url": "https://x.example/a.png"}):
        r = client.post(url, json={"revision": rv["revision"], "images": [item]})
        assert r.status_code == 422, item
    r = client.post(url, json={"revision": rv["revision"], "images": [
        {"id": i["id"], "role": "ad"} for i in rv["comic"]["images"]]})
    assert r.status_code == 200 and r.json()["comic"]["page_count"] == 0
    assert client.post(f"/api/sources/dramas/{did}/extraction/import",
                       json={"revision": r.json()["revision"]}).status_code == 422


def test_review_auth_on(env, comic):
    did = _comic_drama()
    c, h = _remote_user_named("imp@example.com", "sources.import")
    r = c.post("/api/sources/url/import-comic",
               json={"url": COMIC_URL, "drama_id": did, "review": True}, headers=h)
    assert r.status_code == 200
    _wait(f"sourceimport_{did}")
    rv = c.get(f"/api/sources/dramas/{did}/extraction", headers=h)
    assert rv.status_code == 200
    rev = rv.json()["revision"]
    first = rv.json()["comic"]["images"][1]["id"]
    assert c.get(f"/api/sources/dramas/{did}/extraction/images/{first}", headers=h).status_code == 200
    r = c.post(f"/api/sources/dramas/{did}/extraction/rerun-comic", headers=h,
               json={"revision": rev, "images": [{"id": first, "role": "content", "page": 1}]})
    assert r.status_code == 200
    rev = r.json()["revision"]
    # Site profiles are settings-like writes: PC only.
    for path in ("save-profile", "approve-profile"):
        assert c.post(f"/api/sources/dramas/{did}/extraction/{path}", headers=h,
                      json={"revision": rev}).status_code == 403
    c2, h2 = _remote_user_named("no@example.com")
    assert c2.get(f"/api/sources/dramas/{did}/extraction", headers=h2).status_code == 403
    assert c.post(f"/api/sources/dramas/{did}/extraction/import", headers=h,
                  json={"revision": rev}).status_code == 200
    _wait(f"sourceimport_{did}")


# ---------------------------------------------------------------------------
# Security review follow-ups
# ---------------------------------------------------------------------------

def test_unreadable_image_can_never_become_a_page(client, env, comic):
    comic.files["https://img.comic.example/77/5/002.png"] = ("image/png", b"<html>not really</html>")
    did, rv = _comic_review(client, env, comic)
    bad = next(i for i in rv["comic"]["images"] if i["display_url"].endswith("002.png"))
    assert bad["has_image"] is False and "readable" in bad["reason"]
    assert client.get(f"/api/sources/dramas/{did}/extraction/images/{bad['id']}").status_code == 404
    r = client.post(f"/api/sources/dramas/{did}/extraction/rerun-comic",
                    json={"revision": rv["revision"], "images": [{"id": bad["id"], "role": "content", "page": 1}]})
    assert r.status_code == 422
    r = client.post(f"/api/sources/dramas/{did}/extraction/import", json={"revision": rv["revision"]})
    assert r.status_code == 200
    _wait(f"sourceimport_{did}")
    import os
    names = sorted(os.listdir(os.path.join(db.drama_dir(did), "pages")))
    assert len(names) == len(db.list_pages(did)) == 2       # no stray non-image file


def test_remote_runs_hold_even_a_high_profile(isolated_db):
    from sources import adaptive, profiles
    rules = {"content_selector": "#content"}
    high = {"valid": True, "overall": {"bucket": "HIGH", "score": 0.95}, "problems": [], "confidence": {}}
    held = adaptive.ExtractionReport("https://a.example/1", "novel", hold_profiles=True)
    held.profile = {}
    adaptive._offer(held, "a.example", "novel", rules, high, "llm")
    assert held.pending_profile is not None and profiles.active("a.example", "novel") is None
    saved = adaptive.ExtractionReport("https://a.example/1", "novel")
    saved.profile = {}
    adaptive._offer(saved, "a.example", "novel", rules, high, "llm")
    assert saved.pending_profile is None and profiles.active("a.example", "novel") is not None


def test_remote_import_holds_profiles(env, monkeypatch):
    from sources import adaptive
    seen = []
    real = adaptive.import_novel

    def spy(*a, **kw):
        seen.append(kw.get("hold_profiles"))
        return real(*a, **kw)
    monkeypatch.setattr(adaptive, "import_novel", spy)
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    c, h = _remote_user_named("r@example.com", "sources.import")
    assert c.post("/api/sources/url/import", json={"url": url, "drama_id": did}, headers=h).status_code == 200
    _wait(f"sourceimport_{did}")
    assert seen == [True]


def test_signed_in_review_is_pc_only(env):
    from services import sources_extraction_service as svc
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    html = env["fetch"].pages[url]
    from sources import ai_extract
    data = ai_extract.deterministic_novel(ai_extract.PageModel(html, url))
    assert svc.open_review(did, "novel", url, html, data, None, svc.WHY_ASKED, pc_only=True)
    c, h = _remote_user_named("r2@example.com", "sources.import")
    assert c.get(f"/api/sources/dramas/{did}/extraction", headers=h).status_code == 404
    local = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    assert local.get(f"/api/sources/dramas/{did}/extraction").status_code == 200


def test_signed_paths_are_blanked():
    from services import sources_extraction_service as svc
    shown = svc.display_url("https://cdn.example/token/abcdefghijklmnopqrstuv/001.png?sig=1")
    assert shown == "https://cdn.example/token/[REDACTED]/001.png"


# ---------------------------------------------------------------------------
# Shared page-upload rules (services/page_import_limits.py; lead request)
# ---------------------------------------------------------------------------

def _img(fmt, w, h, shade=40, exif_orientation=None):
    import io
    from PIL import Image
    buf = io.BytesIO()
    im = Image.new("RGB", (w, h), (shade, shade, shade))
    kw = {}
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kw["exif"] = exif
    im.save(buf, fmt, **kw)
    return buf.getvalue()


def _set_page(comic, n, ctype, body):
    comic.files[f"https://img.comic.example/77/5/{n:03d}.png"] = (ctype, body)


def _skipped_reasons(res):
    return {s["display_url"].rsplit("/", 1)[-1]: s["reason"] for s in res["skipped"]}


def test_limits_accepted_types_and_caps(monkeypatch):
    from services import page_import_limits as limits
    assert limits.check_image(_img("PNG", 20, 30))[0] == "PNG"
    assert limits.check_image(_img("JPEG", 20, 30))[0] == "JPEG"
    assert limits.check_image(_img("WEBP", 20, 30))[0] == "WEBP"
    for fmt in ("GIF", "BMP", "TIFF"):
        with pytest.raises(limits.ImageRejected, match="not an accepted image type"):
            limits.check_image(_img(fmt, 20, 30))
    with pytest.raises(limits.ImageRejected):
        limits.check_image(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    monkeypatch.setattr(limits, "MAX_IMAGE_PIXELS", 500)
    with pytest.raises(limits.ImageRejected, match="megapixel limit"):
        limits.check_image(_img("PNG", 20, 30))           # 600 px
    monkeypatch.setattr(limits, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(limits.ImageRejected, match="per-image limit"):
        limits.check_image(_img("PNG", 2, 2))


def test_pixel_cap_is_checked_before_any_decode(monkeypatch):
    from PIL import ImageFile
    from services import page_import_limits as limits
    big = _img("PNG", 20, 30)
    monkeypatch.setattr(limits, "MAX_IMAGE_PIXELS", 500)
    loads = []
    real = ImageFile.ImageFile.load
    monkeypatch.setattr(ImageFile.ImageFile, "load", lambda self: loads.append(1) or real(self))
    with pytest.raises(limits.ImageRejected):
        limits.prepare_page(big)
    assert loads == []
    monkeypatch.setattr(limits, "MAX_IMAGE_PIXELS", 10_000)
    limits.prepare_page(big)
    assert loads                     # an accepted image is decoded, after the check


def test_exif_orientation_applied_and_strips_sliced(monkeypatch):
    import io
    from PIL import Image
    from services import page_import_limits as limits
    [(body, ext)] = limits.prepare_page(_img("JPEG", 40, 20, exif_orientation=6))
    assert ext == ".png" and Image.open(io.BytesIO(body)).size == (20, 40)
    [(body, ext)] = limits.prepare_page(_img("JPEG", 40, 20))
    assert ext == ".jpg"                                  # untouched
    import scanlate
    calls = []

    def fake_slices(path, out_dir, *a, **k):
        calls.append(Image.open(path).size)
        out = []
        for i in range(3):
            p = f"{out_dir}/s{i}.png"
            Image.new("RGB", (100, 100)).save(p)
            out.append(p)
        return out
    monkeypatch.setattr(scanlate, "slice_webtoon_to_files", fake_slices)
    pages = limits.prepare_page(_img("PNG", 100, 301))    # taller than 3x its width
    assert calls == [(100, 301)] and len(pages) == 3 and all(e == ".png" for _b, e in pages)
    assert len(limits.prepare_page(_img("PNG", 100, 300))) == 1   # exactly 3x: not a strip
    assert len(limits.prepare_page(_img("PNG", 100, 301), slice_strips=False)) == 1


def test_comic_import_skips_over_cap_images_without_failing(client, env, comic, monkeypatch):
    from services import page_import_limits as limits
    env["fetch"].pages[COMIC_URL] = comic_page_html(5)
    _set_page(comic, 2, "image/gif", _img("GIF", 800, 1200))
    _set_page(comic, 3, "image/tiff", _img("TIFF", 800, 1200))
    _set_page(comic, 4, "image/png", _img("PNG", 810, 1210, 99))      # over the patched cap
    monkeypatch.setattr(limits, "MAX_IMAGE_PIXELS", 800 * 1200)
    did = _comic_drama()
    res = _run(client, "/api/sources/url/import-comic", {"url": COMIC_URL, "drama_id": did}, did)
    res = res.json()["result"]
    why = _skipped_reasons(res)
    assert "not an accepted image type" in why["002.png"] and "not an accepted" in why["003.png"]
    assert "megapixel limit" in why["004.png"]
    assert res["pages_added"] == len(db.list_pages(did)) == 2       # 001 and 005


def test_comic_per_image_byte_cap_is_the_clients(env, comic, monkeypatch):
    from services import page_import_limits as limits
    from services import sources_import_service as imp
    seen = []
    real = imp.adaptive.import_comic

    def spy(url, engine=None, client=None, **kw):
        assert kw.get("remember", True) is True           # the memory is always read
        seen.append((client.max_image_bytes, kw.get("learn"), kw["budget"]))
        return real(url, engine=engine, client=client, **kw)
    monkeypatch.setattr(imp.adaptive, "import_comic", spy)
    did = _comic_drama()
    imp.start_comic_url_import(COMIC_URL, did, local=True)
    _wait(f"sourceimport_{did}")
    cap, learn, budget = seen[0]
    assert cap == limits.MAX_IMAGE_BYTES and learn is True
    assert (budget.max_images, budget.max_total_bytes) == (limits.MAX_FILES_PER_IMPORT,
                                                           limits.MAX_IMPORT_BYTES)
    assert budget.max_image_pixels == limits.MAX_IMAGE_PIXELS
    did2 = _comic_drama()
    imp.start_comic_url_import(COMIC_URL, did2, local=False)
    _wait(f"sourceimport_{did2}")
    assert seen[1][1] is False       # a remote run doesn't add to the shared image memory


def test_failed_downloads_count_against_the_budget():
    from sources.generic_import import DownloadBudget, ImageCandidate, download_candidates
    from sources.models import FailureReason, FetchFailed

    class Timeouts:
        def __init__(self):
            self.n = 0

        def get(self, url, **kw):
            self.n += 1
            raise FetchFailed("timed out", FailureReason.TIMEOUT)
    client = Timeouts()
    cands = [ImageCandidate(f"https://a.example/{i}.png", i) for i in range(10)]
    download_candidates(cands, "https://a.example/", client, DownloadBudget(3, 10**9))
    assert client.n == 3
    assert sum("too many images" in c.reject_reason for c in cands) == 7


def test_page_index_is_max_plus_one_not_the_row_count(isolated_db):
    import os
    from sources import pipeline
    did = db.create_drama(title_en="C", media_type="manhua")
    pages_dir = os.path.join(db.drama_dir(did), "pages")
    os.makedirs(pages_dir, exist_ok=True)
    db.create_page(did, 1, "pages/page_0001.png", 10, 10)     # 2 rows, idx 1 and 8
    db.create_page(did, 8, "pages/page_0008.png", 10, 10)
    assert pipeline.add_page_images(did, [(_img("PNG", 10, 10), ".png")]) == 1
    assert sorted(p["idx"] for p in db.list_pages(did)) == [1, 8, 9]


def test_review_closes_once_its_import_starts_and_approve_is_idempotent(client, env):
    from services import sources_extraction_service as svc
    from sources import profiles
    did, rv = _novel_review(client, env, review=True)
    live = svc._REVIEWS[did]
    live.report.pending_profile = {"domain": "novel.example", "kind": "novel", "origin": "llm",
                                   "rules": profiles.infer_novel_rules(live.page, live.data),
                                   "validation": live.data}
    url = f"/api/sources/dramas/{did}/extraction/approve-profile"
    assert client.post(url, json={"revision": rv["revision"]}).status_code == 200
    assert client.post(url, json={"revision": rv["revision"]}).status_code == 422
    assert len(profiles.versions("novel.example", "novel")) == 1
    r = client.post(f"/api/sources/dramas/{did}/extraction/import", json={"revision": rv["revision"]})
    assert r.status_code == 200
    assert client.post(f"/api/sources/dramas/{did}/extraction/import",
                       json={"revision": rv["revision"]}).status_code in (404, 409)
    _wait(f"sourceimport_{did}")
    assert _raw(did).count("第12章第0段") == 1


# ---------------------------------------------------------------------------
# Lead re-review follow-ups
# ---------------------------------------------------------------------------

def _truncated_rotated_jpeg():
    body = _img("JPEG", 800, 1200, 70, exif_orientation=6)
    return body[: len(body) // 2]


def test_damaged_image_is_rejected_not_raised():
    from services import page_import_limits as limits
    with pytest.raises(limits.ImageRejected, match="damaged or incomplete"):
        limits.prepare_page(_truncated_rotated_jpeg())
    strip = _img("PNG", 100, 400)
    with pytest.raises(limits.ImageRejected):
        limits.prepare_page(strip[: len(strip) // 2])


def test_direct_import_skips_an_image_that_fails_after_download(client, env, comic):
    env["fetch"].pages[COMIC_URL] = comic_page_html(4)
    _set_page(comic, 2, "image/jpeg", _truncated_rotated_jpeg())   # passes the header checks
    did = _comic_drama()
    res = _run(client, "/api/sources/url/import-comic", {"url": COMIC_URL, "drama_id": did}, did)
    res = res.json()["result"]
    assert "damaged or incomplete" in _skipped_reasons(res)["002.png"]
    assert res["pages_added"] == len(db.list_pages(did)) == 3


def test_write_pages_stops_between_images_on_cancel(isolated_db):
    from services import sources_extraction_service as svc
    from sources.generic_import import ImageCandidate
    did = db.create_drama(title_en="C", media_type="manhua")
    background_jobs.start_job("sourceimport_990", lambda: time.sleep(0.3))
    background_jobs.request_cancel("sourceimport_990")
    items = [(ImageCandidate(f"https://a.example/{i}.png", i), _img("PNG", 10, 10)) for i in range(3)]
    with pytest.raises(background_jobs.JobCancelled):
        svc.write_pages(did, items, "sourceimport_990")
    assert db.list_pages(did) == []
    _wait("sourceimport_990")


def test_remote_imports_read_but_never_write_the_image_memory(isolated_db):
    from sources import generic_import as gi, store
    from sources.generic_import import ImageCandidate
    page = "https://comic.example/read/77/6"

    def cand(sha, n):
        c = ImageCandidate(f"https://img.comic.example/{n}.png", n, content=b"x", ext=".png",
                           width=800, height=1200, sha256=sha)
        return c
    store.remember_images("comic.example", "https://comic.example/read/77/5", ["logo"])
    kept, rejected = gi.filter_candidates([cand("logo", 0), cand("p1", 1), cand("p2", 2),
                                           cand("p3", 3)], page, remember=True, learn=False)
    assert [c.sha256 for c in rejected] == ["logo"]           # read: the banner is still dropped
    assert store.hashes_seen_elsewhere("comic.example", "https://x/other", ["p1"]) == set()
    gi.filter_candidates([cand("p1", 1)], page, remember=True, learn=True)
    assert store.hashes_seen_elsewhere("comic.example", "https://x/other", ["p1"]) == {"p1"}


def test_one_comic_import_at_a_time(client, env, comic, monkeypatch):
    import threading
    from services import sources_import_service as imp
    gate = threading.Event()
    real = imp.adaptive.import_comic

    def held(*a, **kw):
        gate.wait(5)
        return real(*a, **kw)
    monkeypatch.setattr(imp.adaptive, "import_comic", held)
    a, b = _comic_drama(), _comic_drama()
    assert client.post("/api/sources/url/import-comic",
                       json={"url": COMIC_URL, "drama_id": a}).status_code == 200
    r = client.post("/api/sources/url/import-comic", json={"url": COMIC_URL, "drama_id": b})
    assert r.status_code == 409 and r.json()["error"]["details"]["job_id"] == f"sourceimport_{a}"
    gate.set()
    _wait(f"sourceimport_{a}")
    _run(client, "/api/sources/url/import-comic", {"url": COMIC_URL, "drama_id": b}, b)


def test_comic_review_images_live_on_disk_and_are_removed(client, env, comic, monkeypatch):
    import os
    from services import sources_extraction_service as svc
    did, rv = _comic_review(client, env, comic)
    live = svc._REVIEWS[did]
    assert live.tmp_dir.startswith(os.path.join(db.LIBRARY_DIR, "source_review_tmp"))
    assert all(not c.content for c in live.candidates)          # nothing held in memory
    assert len(os.listdir(live.tmp_dir)) == len(live.sizes) == 4
    first = next(i["id"] for i in rv["comic"]["images"] if i["display_url"].endswith("001.png"))
    img = client.get(f"/api/sources/dramas/{did}/extraction/images/{first}")
    assert img.status_code == 200 and img.content == comic.files["https://img.comic.example/77/5/001.png"][1]
    r = client.post(f"/api/sources/dramas/{did}/extraction/import", json={"revision": rv["revision"]})
    assert r.status_code == 200
    _wait(f"sourceimport_{did}")
    assert len(db.list_pages(did)) == 3 and not os.path.exists(live.tmp_dir)

    did2, _rv2 = _comic_review(client, env, comic)
    folder = svc._REVIEWS[did2].tmp_dir
    monkeypatch.setattr(svc, "REVIEW_TTL", -1)
    assert client.get(f"/api/sources/dramas/{did2}/extraction").status_code == 404
    assert not os.path.exists(folder)                           # expiry removes it too


# ---------------------------------------------------------------------------
# Lead re-review of a1318dc
# ---------------------------------------------------------------------------

def test_direct_import_writes_one_page_at_a_time(client, env, comic, monkeypatch):
    from services import sources_import_service as imp
    from sources import pipeline
    env["fetch"].pages[COMIC_URL] = comic_page_html(4)
    held = []
    real_add = pipeline.add_page_images
    real_import = imp.adaptive.import_comic
    box = {}

    def spy_import(*a, **kw):
        res, report = real_import(*a, **kw)
        box["images"] = list(res.images)
        return res, report

    def counting_add(drama_id, pages):
        pages = list(pages)
        # One image's pages per call; each written download is dropped at once,
        # so only the ones not yet written are still held.
        held.append((len(pages), sum(1 for c in box["images"] if c.content)))
        return real_add(drama_id, pages)
    monkeypatch.setattr(imp.adaptive, "import_comic", spy_import)
    monkeypatch.setattr(pipeline, "add_page_images", counting_add)
    did = _comic_drama()
    res = _run(client, "/api/sources/url/import-comic", {"url": COMIC_URL, "drama_id": did}, did)
    assert res.json()["result"]["pages_added"] == 4
    assert [n for n, _left in held] == [1, 1, 1, 1]
    assert [left for _n, left in held] == [3, 2, 1, 0]
    assert all(not c.content for c in box["images"])


def test_review_import_reads_one_image_at_a_time(client, env, comic, monkeypatch):
    from services import sources_extraction_service as svc
    from sources import pipeline
    did, rv = _comic_review(client, env, comic)
    reads, calls = [], []
    real_read, real_add = svc._read, pipeline.add_page_images
    monkeypatch.setattr(svc, "_read", lambda r, i: reads.append(i) or real_read(r, i))
    monkeypatch.setattr(pipeline, "add_page_images",
                        lambda d, pages: calls.append((len(reads), len(list(pages)))) or
                        real_add(d, pages))
    assert client.post(f"/api/sources/dramas/{did}/extraction/import",
                       json={"revision": rv["revision"]}).status_code == 200
    _wait(f"sourceimport_{did}")
    assert calls == [(1, 1), (2, 1), (3, 1)]          # read, write, read, write...


def test_a_failed_spill_leaves_no_folder(client, env, comic, monkeypatch):
    import builtins
    import os
    from services import sources_extraction_service as svc
    real_open = builtins.open

    def full_disk(path, mode="r", *a, **kw):
        if str(path).endswith(".img") and "w" in mode:
            raise OSError(28, "No space left on device")
        return real_open(path, mode, *a, **kw)
    monkeypatch.setattr(builtins, "open", full_disk)
    did = _comic_drama()
    r = _run(client, "/api/sources/url/import-comic",
             {"url": COMIC_URL, "drama_id": did, "review": True}, did)
    res = r.json()["result"]
    assert res["needs_review"] is True and res["review_open"] is False
    monkeypatch.setattr(builtins, "open", real_open)
    root = svc._tmp_root()
    assert not os.path.isdir(root) or os.listdir(root) == []
    assert db.list_pages(did) == []


def test_sweep_removes_only_folders_older_than_the_expiry(isolated_db):
    import os
    from services import sources_extraction_service as svc
    root = svc._tmp_root()
    old, live = os.path.join(root, "1_old"), os.path.join(root, "2_live")
    os.makedirs(old)
    os.makedirs(live)
    long_ago = time.time() - svc.REVIEW_TTL - 60
    os.utime(old, (long_ago, long_ago))
    svc._sweep_stale(time.time())
    assert not os.path.exists(old) and os.path.isdir(live)   # another process's live review stays


# ---------------------------------------------------------------------------
# Following next-chapter links: a multi-page review, then the chosen pages
# ---------------------------------------------------------------------------

def _chain(env, *numbers):
    for n in numbers:
        _novel_page(env, n)
    return chapter_url(numbers[0])


def _follow_review(client, env, pages=(12, 13, 14), follow=3):
    url = _chain(env, *pages)
    did = db.create_drama(title_en="N", media_type="novel")
    r = _run(client, "/api/sources/url/import",
             {"url": url, "drama_id": did, "follow_pages": follow}, did)
    assert r.status_code == 200, r.text
    return did, r.json()["result"]


def test_follow_pages_one_is_the_single_page_import(client, env):
    url = _chain(env, 12, 13)
    for body in ({}, {"follow_pages": 1}):
        did = db.create_drama(title_en="N", media_type="novel")
        env["fetch"].calls.clear()
        r = _run(client, "/api/sources/url/import", {"url": url, "drama_id": did, **body}, did)
        assert r.json()["result"] == {"kind": "url_import", "needs_review": False,
                                      "char_count": r.json()["result"]["char_count"],
                                      "review_open": False}
        assert [c["url"] for c in env["fetch"].calls] == [url]
        assert "第12章第0段" in _raw(did) and "第13章" not in _raw(did)


def test_follow_reads_pages_in_order_into_a_review_and_writes_nothing(client, env):
    did, res = _follow_review(client, env, pages=(12, 13, 14, 15))
    assert res["needs_review"] is True and res["review_open"] is True
    assert res["pages_found"] == 3 and res["follow_stop"] == "cap"
    assert [c["url"] for c in env["fetch"].calls] == [chapter_url(n) for n in (12, 13, 14)]
    # One client for the whole chain: one pacing state, one set of caps.
    assert len({id(c) for c in env["fetch"].clients}) == 1
    assert _raw(did) == ""
    r = client.get(f"/api/sources/dramas/{did}/extraction")
    rv = r.json()
    assert rv["why"] == "follow" and rv["novel"]["char_count"] > 200
    pages = rv["follow"]["pages"]
    assert [p["id"] for p in pages] == [0, 1, 2]
    assert [p["title"] for p in pages] == ["第12章 重逢", "第13章 重逢", "第14章 重逢"]
    assert all(p["host"] == "novel.example" and p["char_count"] > 200 for p in pages)
    assert rv["follow"]["stop"] == "cap"
    # The first page's links are shown as the one-page review shows them;
    # nothing read by following (page 3's address, page 2's links) goes out.
    assert "1014" not in r.text and "1015" not in r.text
    _leak_free(r.text)


def test_follow_imports_the_chosen_pages_in_order_once(client, env):
    did, _res = _follow_review(client, env)
    rv = _review(client, did)
    r = client.post(f"/api/sources/dramas/{did}/extraction/import",
                    json={"revision": rv["revision"], "pages": [2, 0]})
    assert r.status_code == 200, r.text
    _wait(f"sourceimport_{did}")
    res = client.get(f"/api/sources/jobs/sourceimport_{did}/result").json()["result"]
    assert res["pages_imported"] == 2 and res["content_type"] == "novel"
    raw = _raw(did)
    assert raw.startswith("第12章 重逢") and raw.count("第14章 重逢") == 1
    assert raw.index("第12章第0段") < raw.index("第14章第0段")
    assert "第13章" not in raw                                   # unticked: skipped
    assert res["char_count"] == sum(p["char_count"] for p in rv["follow"]["pages"]
                                    if p["id"] in (0, 2))
    # The review ended with its import: it can't be appended twice.
    assert client.post(f"/api/sources/dramas/{did}/extraction/import",
                       json={"revision": rv["revision"]}).status_code == 404
    assert _raw(did) == raw


def test_follow_import_without_pages_takes_them_all(client, env):
    did, _res = _follow_review(client, env)
    rv = _review(client, did)
    client.post(f"/api/sources/dramas/{did}/extraction/import", json={"revision": rv["revision"]})
    _wait(f"sourceimport_{did}")
    raw = _raw(did)
    assert raw.index("第12章第0段") < raw.index("第13章第0段") < raw.index("第14章第0段")


def test_follow_rerun_changes_the_first_page_only(client, env):
    did, _res = _follow_review(client, env)
    rv = _review(client, did)
    n = rv["novel"]
    sel = n["content_selector"]
    container = next(c for c in n["containers"] if c["selector"] == sel)
    ad = next(o["selector"] for o in container["exclusions"] if "广告" in o["preview"])
    r = client.post(f"/api/sources/dramas/{did}/extraction/rerun-novel", json={
        "revision": rv["revision"], "content_selector": sel, "exclude_selectors": [ad],
        "title_block": n["title_block"], "next_link": n["next_link"],
        "previous_link": n["previous_link"], "number_from": "title"})
    rv2 = r.json()
    assert rv2["revision"] != rv["revision"] and len(rv2["follow"]["pages"]) == 3
    assert rv2["follow"]["pages"][0]["char_count"] == rv2["novel"]["char_count"]
    assert rv2["follow"]["pages"][1:] == rv["follow"]["pages"][1:]


def test_follow_stops_at_a_hand_off_keeping_the_pages_so_far(client, env):
    url = _chain(env, 12, 13)
    env["fetch"].handoffs.add(chapter_url(14))
    did = db.create_drama(title_en="N", media_type="novel")
    r = _run(client, "/api/sources/url/import", {"url": url, "drama_id": did, "follow_pages": 5}, did)
    res = r.json()["result"]
    assert res["follow_stop"] == "handoff" and res["pages_found"] == 2
    rv = _review(client, did)
    assert rv["follow"]["stop"] == "handoff" and len(rv["follow"]["pages"]) == 2
    assert _raw(did) == ""


def test_follow_hand_off_on_the_first_page_is_the_usual_409(client, env):
    url = chapter_url(12)
    env["fetch"].handoffs.add(url)
    did = db.create_drama(title_en="N", media_type="novel")
    r = _run(client, "/api/sources/url/import", {"url": url, "drama_id": did, "follow_pages": 5}, did)
    assert r.status_code == 409 and r.json()["error"]["details"]["handoff"] is True
    assert client.get(f"/api/sources/dramas/{did}/extraction").status_code == 404
    assert _raw(did) == ""


def test_follow_request_validation(client, env):
    url = _chain(env, 12)
    did = db.create_drama(title_en="N", media_type="novel")
    for bad in (0, 51, "3", True, 2.0):
        r = client.post("/api/sources/url/import",
                        json={"url": url, "drama_id": did, "follow_pages": bad})
        assert r.status_code == 422, bad
    # The comic import takes no follow_pages.
    assert client.post("/api/sources/url/import-comic",
                       json={"url": url, "drama_id": did, "follow_pages": 2}).status_code == 422
    assert env["fetch"].calls == []


def test_follow_import_refuses_pages_not_offered(client, env):
    did, _res = _follow_review(client, env, pages=(12, 13), follow=2)
    rv = _review(client, did)
    path = f"/api/sources/dramas/{did}/extraction/import"
    for bad in ([2], [-1], [], ["0"], [True]):
        assert client.post(path, json={"revision": rv["revision"], "pages": bad}).status_code == 422, bad
    assert _raw(did) == ""
    # A one-page review has only page 0.
    did2, rv2 = _novel_review(client, env, review=True)
    assert rv2["follow"] is None
    assert client.post(f"/api/sources/dramas/{did2}/extraction/import",
                       json={"revision": rv2["revision"], "pages": [1]}).status_code == 422
    r = client.post(f"/api/sources/dramas/{did2}/extraction/import",
                    json={"revision": rv2["revision"], "pages": [0]})
    assert r.status_code == 200
    _wait(f"sourceimport_{did2}")
    assert "第12章第0段" in _raw(did2)


def test_follow_address_check_refuses_a_private_address(env, monkeypatch):
    from services import sources_import_service as imp
    assert imp._is_public(chapter_url(13)) is True
    _dns(monkeypatch, "10.0.0.5")
    assert imp._is_public(chapter_url(13)) is False


def test_follow_first_page_heading_falls_back_to_the_page_title(client, env):
    from services import sources_extraction_service as svc
    from sources import ai_extract, novel_follow
    url = _novel_page(env)
    did = db.create_drama(title_en="N", media_type="novel")
    html = env["fetch"].pages[url]
    data = ai_extract.deterministic_novel(ai_extract.PageModel(html, url))
    data["chapter_title"] = None
    later = novel_follow.FollowedPage(chapter_url(13), "第13章 重逢", "第13章第0段。" * 40)
    assert svc.open_review(did, "novel", url, html, data, None, svc.WHY_FOLLOWED,
                           chain=[later], follow_stop="cap")
    rv = _review(client, did)
    assert rv["follow"]["pages"][0]["title"] == "第12章 重逢 - 某某小说网"
    client.post(f"/api/sources/dramas/{did}/extraction/import", json={"revision": rv["revision"]})
    _wait(f"sourceimport_{did}")
    assert _raw(did).startswith("第12章 重逢 - 某某小说网")     # as a direct import would


def test_follow_cancelled_import_says_how_many_pages_were_appended(client, env, monkeypatch):
    from services import sources_extraction_service as svc
    did, _res = _follow_review(client, env)
    rv = _review(client, did)
    asked = []

    def cancel_after_first_page(job_id):
        asked.append(job_id)
        return len(asked) > 1               # the check before page 1 says no
    monkeypatch.setattr(svc.background_jobs, "is_cancel_requested", cancel_after_first_page)
    client.post(f"/api/sources/dramas/{did}/extraction/import", json={"revision": rv["revision"]})
    st = _wait(f"sourceimport_{did}")
    assert st["status"] == "cancelled"
    assert st["message"] == "Cancelled after appending 1 of 3 pages."
    assert st["result"]["pages_imported"] == 1 and st["result"]["cancelled"] is True
    raw = _raw(did)
    assert "第12章第0段" in raw and "第13章" not in raw
    assert client.get(f"/api/sources/dramas/{did}/extraction").status_code == 404   # not re-opened


def test_follow_keeps_the_pages_when_a_later_page_is_too_large(client, env, monkeypatch):
    from sources.http import ResponseTooLarge
    url = _chain(env, 12, 13)
    real = env["fetch"]

    def fetch(u, *a, **kw):
        if u == chapter_url(14):
            raise ResponseTooLarge()
        return real(u, *a, **kw)
    monkeypatch.setattr(generic_import, "fetch_page", fetch)
    did = db.create_drama(title_en="N", media_type="novel")
    r = _run(client, "/api/sources/url/import", {"url": url, "drama_id": did, "follow_pages": 5}, did)
    res = r.json()["result"]
    assert res["follow_stop"] == "unreachable" and res["pages_found"] == 2
    assert _review(client, did)["follow"]["stop"] == "unreachable" and _raw(did) == ""

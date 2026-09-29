"""Sources S-5: paste-a-URL preview (POST /api/sources/url/preview) and novel
text import (POST /api/sources/url/import). DNS and every fetch are faked;
no network."""
import os
import threading
import time
from types import SimpleNamespace

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
from services import sources_import_service as imp
from services import sources_url_service as url_svc
from sources import front_door, generic_import, pipeline, registry
from sources.generic_import import NoContentFound, NovelImportResult
from sources.ladder import LadderResult
from sources.models import AccessTier

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
PAGE = f"https://novel.example/book/1/ch2.html?token={SECRET}"
NOVEL_HTML = ("<html><head><title>第2章 春天</title></head><body><article>"
              + "<p>" + "这是一个很长的中文段落，用来测试小说正文的提取。" * 20 + "</p>" * 1
              + "".join("<p>" + "他走进房间，看着窗外的雨。" * 10 + "</p>" for _ in range(8))
              + "</article></body></html>")


def _dns(monkeypatch, ip="93.184.216.34"):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", (ip, port))])


class FakeFetch:
    """Stands in for generic_import.fetch_page: records each call's URL and
    allow_* flags; `gate` holds it; `handoff` makes it a challenge."""

    def __init__(self, html=NOVEL_HTML, handoff=None, gate=None):
        self.html, self.handoff, self.gate, self.calls = html, handoff, gate, []

    def __call__(self, url, client=None, rendered_fetch=None, user_html=None,
                 authenticated_fetch=None, allow_signed_in=True, allow_browser=True):
        self.calls.append({"url": url, "signed_in": allow_signed_in, "browser": allow_browser})
        if self.gate is not None:
            self.gate.wait(5)
        lr = LadderResult(url)
        if self.handoff:
            lr.handoff = self.handoff
        else:
            lr.tier, lr.html = AccessTier.STATIC_HTTP.value, self.html
        return lr


@pytest.fixture
def env(isolated_db, monkeypatch):
    monkeypatch.setattr(registry, "adapter_classes", lambda: {})
    _dns(monkeypatch)
    fetch = FakeFetch()
    monkeypatch.setattr(generic_import, "fetch_page", fetch)
    yield fetch
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


def _no_leak(r):
    assert SECRET not in r.text and "token" not in r.text and "?" not in r.text
    assert db.LIBRARY_DIR not in r.text and "<html" not in r.text


def _preview(client, url=PAGE):
    r = client.post("/api/sources/url/preview", json={"url": url})
    assert r.status_code == 200 and r.json() == {"job_id": "sources_url_preview"}
    _wait("sources_url_preview")
    return client.get("/api/sources/jobs/sources_url_preview/result")


# ---------------------------------------------------------------------------
# Public-address check (both routes), before any fetch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ip", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "::ffff:127.0.0.1",
                                "::1"])
def test_private_addresses_refused_without_fetch(client, env, monkeypatch, ip):
    _dns(monkeypatch, ip)
    did = db.create_drama(title_en="N", media_type="novel")
    for path, body in (("/api/sources/url/preview", {"url": PAGE}),
                       ("/api/sources/url/import", {"url": PAGE, "drama_id": did})):
        r = client.post(path, json=body)
        assert r.status_code == 422, (path, ip)
        _no_leak(r)
        assert "novel.example" not in r.text
    assert env.calls == []
    assert background_jobs.get_status("sources_url_preview") is None


def test_bad_urls_422_and_unresolvable_503(client, env, monkeypatch):
    for url in ("ftp://novel.example/x", "https://user:pw@novel.example/x", "javascript:alert(1)",
                "novel.example/x", "https://", "http://novel.example:99999/x",
                "https://novel.example/" + "a" * 2000):
        r = client.post("/api/sources/url/preview", json={"url": url})
        assert r.status_code == 422, url
        assert "novel.example" not in r.text
    for body in ({"url": PAGE, "html": "<p>x</p>"}, {"url": 5}, {}):
        assert client.post("/api/sources/url/preview", json=body).status_code == 422

    def fail(*a, **k):
        raise OSError("no such host")
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", fail)
    r = client.post("/api/sources/url/preview", json={"url": PAGE})
    assert r.status_code == 503 and "novel.example" not in r.text
    assert env.calls == []


# ---------------------------------------------------------------------------
# R1: preview
# ---------------------------------------------------------------------------

def test_preview_novel_page_shape_and_no_leaks(client, env):
    r = _preview(client)
    _no_leak(r)
    res = r.json()["result"]
    assert set(res) == {"kind", "content_type", "route", "platform", "title", "chapter",
                        "chapter_id", "language", "chapter_count", "adapter", "series_id",
                        "text_length", "image_count", "notes", "display_url"}
    assert res["kind"] == "url_preview" and res["content_type"] == "novel"
    assert res["route"] == "page" and res["language"] == "zh"
    assert res["display_url"] == "https://novel.example/book/1/ch2.html"
    assert res["text_length"] > 800 and res["chapter_id"] is None
    assert env.calls[0]["url"] == PAGE


def test_video_url_and_spoofed_host(client, env):
    res = _preview(client, "https://www.bilibili.com/video/BV1xx411c7mD").json()["result"]
    assert res["content_type"] == "video" and res["route"] == "video"
    assert env.calls == []   # a known video URL is not fetched for a preview
    res = _preview(client, "https://evil.example/?b23.tv/").json()["result"]
    assert res["content_type"] != "video" and res["route"] == "page"
    assert [c["url"] for c in env.calls] == ["https://evil.example/?b23.tv/"]


def test_second_preview_409(client, env):
    env.gate = threading.Event()
    assert client.post("/api/sources/url/preview", json={"url": PAGE}).status_code == 200
    r = client.post("/api/sources/url/preview", json={"url": PAGE})
    assert r.status_code == 409
    env.gate.set()
    _wait("sources_url_preview")


def test_preview_handoff_409(client, env):
    env.handoff = {"tier": "STATIC_HTTP", "reason": "CLOUDFLARE_CHALLENGE"}
    r = _preview(client)
    assert r.status_code == 409
    _no_leak(r)
    d = r.json()["error"]["details"]
    assert d == {"reason": "CLOUDFLARE_CHALLENGE", "handoff": True,
                 "open_url": "https://novel.example/book/1/ch2.html"}


def test_preview_error_never_names_the_url(client, env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(f"failed at {PAGE} and C:\\Users\\kae\\x")
    monkeypatch.setattr(front_door, "preview", boom)
    r = _preview(client)
    assert r.status_code == 500 and "novel.example" not in r.text and "kae" not in r.text
    st = background_jobs.get_status("sources_url_preview")
    assert "novel.example" not in (st.get("error") or "")


def test_remote_request_gets_static_only(env, monkeypatch):
    url_svc.start_preview(PAGE, local=False)
    _wait("sources_url_preview")
    assert env.calls[-1]["signed_in"] is False and env.calls[-1]["browser"] is False
    background_jobs.clear_job("sources_url_preview")
    url_svc.start_preview(PAGE, local=True)
    _wait("sources_url_preview")
    assert env.calls[-1]["signed_in"] is True and env.calls[-1]["browser"] is True


def test_fetch_page_tiers_follow_the_flags(isolated_db, monkeypatch):
    """The real generic_import.fetch_page: flags drop the signed-in and
    browser tiers."""
    seen = []
    monkeypatch.setattr(generic_import.auth_browser, "has_profile", lambda url, source: True)
    monkeypatch.setattr(generic_import.auth_browser, "profile_dir", lambda url, source: "/x")
    monkeypatch.setattr(generic_import.ladder, "run_ladder",
                        lambda url, tiers, source=None: seen.append(set(tiers)) or LadderResult(url))
    monkeypatch.setattr(generic_import.ladder, "record_ladder_result", lambda *a, **k: None)
    for kw in ({}, {"allow_signed_in": False}, {"allow_signed_in": False, "allow_browser": False}):
        generic_import.fetch_page("https://novel.example/x", **kw)
    assert seen == [{AccessTier.AUTHENTICATED_BROWSER},
                    {AccessTier.STATIC_HTTP, AccessTier.RENDERED_BROWSER},
                    {AccessTier.STATIC_HTTP}]


# ---------------------------------------------------------------------------
# R2: novel text import
# ---------------------------------------------------------------------------

def _fake_import(monkeypatch, text="正文" * 300, needs_review=False, exc=None, handoff=None,
                 calls=None):
    def fake(url, engine=None, client=None, rendered_fetch=None, user_html=None,
             use_cache=True, allow_signed_in=True, allow_browser=True, hold_profiles=False):
        if calls is not None:
            calls.append({"url": url, "engine": engine, "signed_in": allow_signed_in,
                          "browser": allow_browser})
        if exc is not None:
            raise exc
        lr = LadderResult(url)
        lr.handoff = handoff
        return (NovelImportResult(url, "第2章", "" if handoff else text, "heuristic", ladder=lr),
                SimpleNamespace(needs_review=needs_review, data=None))
    monkeypatch.setattr(imp.adaptive, "import_novel", fake)


def _raw(did):
    path = os.path.join(db.drama_dir(did), pipeline.RAW_NOVEL_FILENAME)
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


def _import(client, did, url=PAGE):
    r = client.post("/api/sources/url/import", json={"url": url, "drama_id": did})
    assert r.status_code == 200 and r.json() == {"job_id": f"sourceimport_{did}"}
    _wait(f"sourceimport_{did}")
    return client.get(f"/api/sources/jobs/sourceimport_{did}/result")


def test_url_import_appends_text(client, env, monkeypatch):
    calls = []
    _fake_import(monkeypatch, calls=calls)
    did = db.create_drama(title_en="N", media_type="novel")
    r = _import(client, did)
    _no_leak(r)
    assert r.json()["result"] == {"kind": "url_import", "needs_review": False, "char_count": 600,
                                  "review_open": False}
    assert calls == [{"url": PAGE, "engine": None, "signed_in": True, "browser": True}]
    assert "第2章" in _raw(did) and _raw(did).count("正文") == 300
    _import(client, did)
    assert _raw(did).count("正文") == 600   # appended, never replaced
    j = client.get(f"/api/jobs/sourceimport_{did}")
    assert j.status_code == 200 and SECRET not in j.text


def test_url_import_needs_review_writes_nothing(client, env, monkeypatch):
    _fake_import(monkeypatch, needs_review=True)
    did = db.create_drama(title_en="N", media_type="novel")
    r = _import(client, did)
    assert r.json()["result"]["needs_review"] is True
    assert _raw(did) == ""


def test_url_import_errors(client, env, monkeypatch):
    did = db.create_drama(title_en="N", media_type="novel")
    _fake_import(monkeypatch, exc=NoContentFound(f"nothing at {PAGE}"))
    r = _import(client, did)
    assert r.status_code == 422 and "novel.example" not in r.text
    _fake_import(monkeypatch, handoff={"tier": "STATIC_HTTP", "reason": "CAPTCHA"})
    r = _import(client, did)
    assert r.status_code == 409 and r.json()["error"]["details"]["handoff"] is True
    _no_leak(r)
    assert _raw(did) == ""


def test_url_import_validation(client, env, monkeypatch):
    _fake_import(monkeypatch)
    audio = db.create_drama(title_en="A")
    did = db.create_drama(title_en="N", media_type="novel")
    assert client.post("/api/sources/url/import",
                       json={"url": PAGE, "drama_id": audio}).status_code == 422
    assert client.post("/api/sources/url/import",
                       json={"url": PAGE, "drama_id": 99999}).status_code == 404
    assert client.post("/api/sources/url/import",
                       json={"url": PAGE, "drama_id": did, "html": "x"}).status_code == 422
    background_jobs.start_job(f"transcribe_{did}", lambda: time.sleep(0.5))
    assert client.post("/api/sources/url/import",
                       json={"url": PAGE, "drama_id": did}).status_code == 409
    _wait(f"transcribe_{did}")
    background_jobs.clear_job(f"transcribe_{did}")


def test_url_routes_do_not_shadow_a_source_named_url(client, env):
    # /url/import is its own route, never the chapter import of a source "url"
    r = client.post("/api/sources/url/import",
                    json={"series_id": "s1", "chapter_ids": ["c1"], "drama_id": 1})
    assert r.status_code == 422
    assert client.get("/api/sources/tracked").status_code == 200


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def test_auth_on(env, monkeypatch):
    _fake_import(monkeypatch)
    did = db.create_drama(title_en="N", media_type="novel")
    c = TestClient(create_app(ApiSettings(auth_mode="on")),
                   base_url="https://baihe.example.com", raise_server_exceptions=False)
    assert c.post("/api/sources/url/preview", json={"url": PAGE}).status_code == 401
    u = auth_service.add_user("kid@example.com")
    s = auth_service.create_session(u["id"])
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    assert c.post("/api/sources/url/preview", json={"url": PAGE}, headers=h).status_code == 403
    assert c.post("/api/sources/url/import", json={"url": PAGE, "drama_id": did},
                  headers=h).status_code == 403
    auth_service.grant_permission(u["id"], "sources.import")
    assert c.post("/api/sources/url/preview", json={"url": PAGE}, headers=h).status_code == 200
    _wait("sources_url_preview")
    assert env.calls[-1]["signed_in"] is False and env.calls[-1]["browser"] is False
    r = c.get("/api/sources/jobs/sources_url_preview/result", headers=h)
    assert r.status_code == 200
    _no_leak(r)
    assert c.post("/api/sources/url/import", json={"url": PAGE, "drama_id": did},
                  headers=h).status_code == 200

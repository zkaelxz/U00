"""Sources tools (feature inventory SO02 preflight, SO03 pasted page source,
SO08 identify media, SO16 pasted-URL diagnostics) and the Discover bulk
import's pasted-text fallback (DI07). DNS, every fetch and the LLM are
faked; no network."""
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import bulk_import
import db
import translate_engines
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, settings_service, url_guard
from services import discover_lookup_service as discover_svc
from services import sources_tools_service as svc
from sources import generic_import, ladder, pipeline, profiles, registry
from sources.ladder import LadderResult
from sources.models import AccessTier

_REAL_FETCH_PAGE = generic_import.fetch_page
SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
PAGE = f"https://novel.example/book/1/ch2.html?token={SECRET}"
NOVEL_HTML = ("<html><head><title>第2章 春天</title></head><body><article>"
              + "".join("<p>" + "他走进房间，看着窗外的雨。" * 10 + "</p>" for _ in range(10))
              + "</article></body></html>")
MEDIA_HTML = ("<html><head><title>Ep 1</title></head><body><h1>Episode one</h1>"
              "<video src='https://cdn.example/a.mp4?sig=abc123'>"
              "<track kind='subtitles' srclang='zh' src='https://cdn.example/a.vtt'></video>"
              "<video src='https://cdn.example/b.mp4'></video>"
              "</body></html>")


def _dns(monkeypatch, ip="93.184.216.34"):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", (ip, port))])


class FakeFetch:
    """Stands in for generic_import.fetch_page. A call with `user_html` is
    the pasted (user-assisted) tier: recorded, but it is not a request."""

    def __init__(self, html=NOVEL_HTML, handoff=None):
        self.html, self.handoff, self.calls, self.pasted = html, handoff, [], []

    def __call__(self, url, client=None, rendered_fetch=None, user_html=None,
                 authenticated_fetch=None, allow_signed_in=True, allow_browser=True,
                 record=True):
        lr = LadderResult(url)
        if user_html is not None:
            self.pasted.append(url)
            lr.tier, lr.html = AccessTier.USER_ASSISTED_BROWSER.value, user_html
            return lr
        self.calls.append({"url": url, "signed_in": allow_signed_in, "browser": allow_browser})
        if self.handoff:
            lr.handoff = self.handoff
        else:
            lr.tier, lr.html = AccessTier.STATIC_HTTP.value, self.html
        return lr


def _wait(job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if not st or st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture
def env(isolated_db, monkeypatch):
    monkeypatch.setattr(registry, "adapter_classes", lambda: {})
    _dns(monkeypatch)
    fetch = FakeFetch()
    monkeypatch.setattr(generic_import, "fetch_page", fetch)
    yield fetch
    for jid in list(background_jobs.list_all_jobs()):
        if jid.startswith(("sources_", "sourceimport_", "discover_")):
            _wait(jid)
            background_jobs.clear_job(jid)


@pytest.fixture
def client(env):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _no_leak(r):
    assert SECRET not in r.text and "token=" not in r.text and "sig=" not in r.text
    assert db.LIBRARY_DIR not in r.text and "<html" not in r.text and "<p>" not in r.text


def _result(client, job_id):
    _wait(job_id)
    return client.get(f"/api/sources/jobs/{job_id}/result")


# ---------------------------------------------------------------------------
# SO02: preflight
# ---------------------------------------------------------------------------

def test_preflight_novel_page(client, env):
    r = client.post("/api/sources/url/preflight", json={"url": PAGE})
    assert r.status_code == 200 and r.json() == {"job_id": "sources_url_preflight"}
    r = _result(client, "sources_url_preflight")
    assert r.status_code == 200
    _no_leak(r)
    res = r.json()["result"]
    assert res["kind"] == "url_preflight" and res["content_type"] == "novel"
    assert res["reachable"] is True and res["permitted"] is True
    assert res["display_url"] == "https://novel.example/book/1/ch2.html"
    assert res["verdict"] and isinstance(res["lines"], list)
    assert [c["url"] for c in env.calls] == [PAGE]


def test_preflight_private_address_refused_without_fetch(client, env, monkeypatch):
    _dns(monkeypatch, "10.0.0.5")
    r = client.post("/api/sources/url/preflight", json={"url": PAGE})
    assert r.status_code == 422 and "novel.example" not in r.text
    assert env.calls == [] and background_jobs.get_status("sources_url_preflight") is None


def test_preflight_handoff_is_a_finding(client, env):
    env.handoff = {"tier": "STATIC_HTTP", "reason": "CLOUDFLARE_CHALLENGE"}
    client.post("/api/sources/url/preflight", json={"url": PAGE})
    r = _result(client, "sources_url_preflight")
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["ok"] is False and res["reachable"] is False
    _no_leak(r)


def test_preflight_from_another_device_is_static_only(env):
    svc.start_preflight(PAGE, local=False)
    _wait("sources_url_preflight")
    assert env.calls[-1]["signed_in"] is False and env.calls[-1]["browser"] is False
    background_jobs.clear_job("sources_url_preflight")
    svc.start_preflight(PAGE, local=True)
    _wait("sources_url_preflight")
    assert env.calls[-1]["signed_in"] is True and env.calls[-1]["browser"] is True


# ---------------------------------------------------------------------------
# SO03: continue from pasted page source
# ---------------------------------------------------------------------------

def test_preview_pasted_parses_without_fetching(client, env):
    r = client.post("/api/sources/url/preview-pasted", json={"url": PAGE, "html": NOVEL_HTML})
    assert r.status_code == 200
    _no_leak(r)
    res = r.json()
    assert res["pasted"] is True and res["kind"] == "url_preview"
    assert res["content_type"] == "novel" and res["display_url"].endswith("/ch2.html")
    assert env.calls == []


def test_pasted_body_caps(client, env):
    big = "x" * (svc.MAX_PASTED_HTML_BYTES + 10)
    r = client.post("/api/sources/url/preview-pasted", json={"url": PAGE, "html": big})
    assert r.status_code == 422      # over the model's cap, under the body cap
    huge = "x" * int(svc.MAX_PASTED_HTML_BYTES * 1.3)
    for path in ("/api/sources/url/preview-pasted", "/api/sources/url/import-pasted",
                 "/api/sources/url/identify-media"):
        r = client.post(path, json={"url": PAGE, "html": huge, "drama_id": 1})
        assert r.status_code == 413, path
        assert r.json()["error"]["code"] == "too_large"
    for body in ({"url": PAGE}, {"url": PAGE, "html": ""}, {"url": PAGE, "html": 5},
                 {"url": PAGE, "html": "<p>x</p>", "extra": 1}):
        assert client.post("/api/sources/url/preview-pasted", json=body).status_code == 422
    r = client.post("/api/sources/url/preview-pasted", content=b"not json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert env.calls == []


def test_import_pasted_appends_text_without_fetching(client, env, monkeypatch):
    from types import SimpleNamespace

    from sources import adaptive, pipeline
    from sources.generic_import import NovelImportResult
    calls, saved = [], []

    def fake_import(url, engine=None, client=None, rendered_fetch=None, user_html=None,
                    use_cache=True, allow_signed_in=True, allow_browser=True, remember=True):
        calls.append({"url": url, "html": user_html, "engine": engine,
                      "signed_in": allow_signed_in, "browser": allow_browser,
                      "remember": remember})
        lr = generic_import.fetch_page(url, client, None, user_html)
        return (NovelImportResult(url, "第2章", "正文" * 300, "deterministic", ladder=lr),
                SimpleNamespace(needs_review=False))
    monkeypatch.setattr(adaptive, "import_novel", fake_import)
    monkeypatch.setattr(pipeline, "save_novel_text",
                        lambda did, text, append=True, heading="": saved.append((did, text)))
    did = db.create_drama(title_en="N", media_type="novel")
    r = client.post("/api/sources/url/import-pasted",
                    json={"url": PAGE, "html": NOVEL_HTML, "drama_id": did})
    assert r.status_code == 200 and r.json() == {"job_id": f"sourceimport_{did}"}
    r = _result(client, f"sourceimport_{did}")
    assert r.status_code == 200
    assert r.json()["result"] == {"kind": "url_import", "needs_review": False, "char_count": 600}
    assert saved == [(did, "正文" * 300)]
    # The test client is this PC: its paste may keep a site profile (Streamlit parity).
    assert calls == [{"url": PAGE, "html": NOVEL_HTML, "engine": None, "signed_in": False,
                      "browser": False, "remember": True}]
    assert env.calls == [] and env.pasted == [PAGE]


def test_import_pasted_refuses_a_non_novel_drama_and_a_missing_one(client, env):
    did = db.create_drama(title_en="A", media_type="audio_drama")
    r = client.post("/api/sources/url/import-pasted",
                    json={"url": PAGE, "html": NOVEL_HTML, "drama_id": did})
    assert r.status_code == 422
    r = client.post("/api/sources/url/import-pasted",
                    json={"url": PAGE, "html": NOVEL_HTML, "drama_id": 9999})
    assert r.status_code == 404


def test_remote_pasted_import_keeps_no_profile_or_capability_record(env, monkeypatch):
    """Page source pasted from another device chooses both the domain and the
    page, so it must not change that domain's site profile or the source's
    capability record; the same paste at the PC does (Streamlit parity)."""
    from tests.test_adaptive_extraction import DOMAIN, _page
    monkeypatch.setattr(generic_import, "fetch_page", _REAL_FETCH_PAGE)
    monkeypatch.setattr(pipeline, "save_novel_text", lambda *a, **k: None)
    recorded = []
    monkeypatch.setattr(ladder, "record_ladder_result", lambda *a, **k: recorded.append(a))
    html, url = _page(12)
    profiles.save_version(DOMAIN, "novel", {"content_selector": "div#gone"},
                          {"valid": True, "overall": {"bucket": "HIGH"}, "problems": []},
                          "correction", approved=True)
    before = profiles.versions(DOMAIN, "novel")

    did = db.create_drama(title_en="N", media_type="novel")
    svc.start_pasted_import(url, html, did, local=False)
    assert _wait(f"sourceimport_{did}")["status"] == "done"
    assert profiles.versions(DOMAIN, "novel") == before
    assert profiles.versions(DOMAIN, "novel")[0]["failures"] == 0
    assert recorded == [] and env.calls == []

    background_jobs.clear_job(f"sourceimport_{did}")
    svc.start_pasted_import(url, html, did, local=True)
    assert _wait(f"sourceimport_{did}")["status"] == "done"
    after = profiles.versions(DOMAIN, "novel")
    assert after != before and after[0]["failures"] == 1
    assert len(recorded) == 1 and env.calls == []


def test_pasted_preview_is_linear_on_hostile_markup(client, env):
    """Unclosed tags repeated across a large paste: each regex in
    classify_html stops at the next "<", so this takes about a second, not
    minutes (it was quadratic)."""
    for chunk in ("<video ", "<meta ", "<title>", '<meta property="og:title" '):
        html = chunk * (1_500_000 // len(chunk))
        start = time.monotonic()
        r = client.post("/api/sources/url/preview-pasted", json={"url": PAGE, "html": html})
        assert r.status_code == 200, chunk
        assert time.monotonic() - start < 15, chunk


def test_one_pasted_preview_at_a_time(env, monkeypatch):
    from services.service_errors import ConflictError
    assert svc._PASTED_PREVIEW_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ConflictError):
            svc.preview_pasted(PAGE, NOVEL_HTML)
    finally:
        svc._PASTED_PREVIEW_LOCK.release()
    assert svc.preview_pasted(PAGE, NOVEL_HTML)["pasted"] is True


# ---------------------------------------------------------------------------
# SO08: identify media
# ---------------------------------------------------------------------------

MEDIA_URL = "https://video.example/watch/1"


def test_identify_media_lists_resources_full_url_only_at_the_pc(client, env):
    env.html = MEDIA_HTML
    r = client.post("/api/sources/url/identify-media", json={"url": MEDIA_URL})
    assert r.status_code == 200 and r.json() == {"job_id": "sources_url_identify"}
    r = _result(client, "sources_url_identify")
    _no_leak(r)
    res = r.json()["result"]
    assert res["kind"] == "media_identify" and res["found"] is True
    videos = [x for x in res["resources"] if x["kind"] == "video"]
    assert len(videos) == 2 and res["needs_review"] is True   # two videos: pick one
    assert {v["display_url"] for v in videos} == {"https://cdn.example/a.mp4",
                                                 "https://cdn.example/b.mp4"}
    assert all(v["downloadable"] for v in videos)
    subs = [x for x in res["resources"] if x["kind"] == "subtitle"]
    assert subs and not subs[0]["downloadable"]
    assert [c["url"] for c in env.calls] == [MEDIA_URL]

    a = next(v for v in videos if v["display_url"].endswith("a.mp4"))
    got = client.get("/api/sources/url/identify-media/resource",
                     params={"run_id": res["run_id"], "index": a["index"]})
    assert got.status_code == 200
    assert got.json()["resource_url"] == "https://cdn.example/a.mp4?sig=abc123"
    for params in ({"run_id": "old", "index": a["index"]},
                   {"run_id": res["run_id"], "index": subs[0]["index"]}):
        assert client.get("/api/sources/url/identify-media/resource",
                          params=params).status_code == 404

    # A new run replaces the list: the old run id no longer resolves. A run
    # started from another device keeps no full URLs at all.
    background_jobs.clear_job("sources_url_identify")
    svc.start_identify_media(MEDIA_URL, local=False)
    remote = _wait("sources_url_identify")["result"]
    assert env.calls[-1]["signed_in"] is False and env.calls[-1]["browser"] is False
    assert client.get("/api/sources/url/identify-media/resource",
                      params={"run_id": res["run_id"], "index": a["index"]}).status_code == 404
    assert client.get("/api/sources/url/identify-media/resource",
                      params={"run_id": remote["run_id"], "index": a["index"]}).status_code == 404


def test_identify_media_from_pasted_source_does_not_fetch(client, env):
    r = client.post("/api/sources/url/identify-media", json={"url": MEDIA_URL, "html": MEDIA_HTML})
    assert r.status_code == 200
    res = _result(client, "sources_url_identify").json()["result"]
    assert res["found"] is True and env.calls == []


def test_identify_media_handoff_409(client, env):
    env.handoff = {"tier": "STATIC_HTTP", "reason": "CLOUDFLARE_CHALLENGE"}
    client.post("/api/sources/url/identify-media", json={"url": MEDIA_URL})
    r = _result(client, "sources_url_identify")
    assert r.status_code == 409 and r.json()["error"]["details"]["handoff"] is True


def test_identify_media_nothing_found(client, env):
    env.html = NOVEL_HTML
    client.post("/api/sources/url/identify-media", json={"url": MEDIA_URL})
    res = _result(client, "sources_url_identify").json()["result"]
    assert res["found"] is False and res["resources"] == [] and res["reason"]


# ---------------------------------------------------------------------------
# SO16: pasted-URL diagnostics
# ---------------------------------------------------------------------------

def test_recent_extractions_list(client, env):
    env.html = MEDIA_HTML
    client.post("/api/sources/url/identify-media", json={"url": MEDIA_URL + "?k=" + SECRET})
    _wait("sources_url_identify")
    r = client.get("/api/sources/url/extractions")
    assert r.status_code == 200
    _no_leak(r)
    rows = r.json()
    assert rows and rows[0]["url"] == MEDIA_URL and rows[0]["content_type"] == "video"
    assert rows[0]["headline"] and isinstance(rows[0]["lines"], list)
    for bad in (0, 51, "x"):
        assert client.get(f"/api/sources/url/extractions?limit={bad}").status_code == 422


def test_remote_permissions(env):
    """Auth on: a household account without the opt-ins gets 403 for the
    fetch routes and the admin diagnostics list; the pasted listing needs
    jobs.start (a household default) but a paid engine needs engines.paid."""
    app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
    u = auth_service.add_user("h@example.com")
    s = auth_service.create_session(u["id"])
    headers = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
               api_auth.CSRF_HEADER: s["csrf_token"]}
    c = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
    for path in ("/api/sources/url/preflight", "/api/sources/url/preview-pasted",
                 "/api/sources/url/import-pasted", "/api/sources/url/identify-media"):
        r = c.post(path, json={"url": PAGE, "html": NOVEL_HTML, "drama_id": 1}, headers=headers)
        assert r.status_code == 403, path
    assert c.get("/api/sources/url/extractions", headers=headers).status_code == 403
    r = c.get("/api/sources/url/identify-media/resource", params={"run_id": "x", "index": 0},
              headers=headers)
    assert r.status_code == 403
    r = c.post("/api/discover/bulk-extract/pasted", json={"text": "x", "engine": "claude"},
               headers=headers)
    assert r.status_code == 403
    assert env.calls == [] and env.pasted == []


# ---------------------------------------------------------------------------
# DI07: pasted listing text
# ---------------------------------------------------------------------------

class FakeEngine:
    supports_reference = True


@pytest.fixture
def discover(env, monkeypatch):
    background_jobs.clear_job(discover_svc.BULK_JOB_ID)
    monkeypatch.setattr(settings_service, "resolve_key", lambda name: SECRET)
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())
    seen = []

    def extract(text, engine, source_name=""):
        seen.append(text)
        return [{"title": "白鹤", "author": f"A {SECRET}", "tags": "百合",
                 "has_audio_drama": True}, {"title": "白鹤"}, {"title": ""}]
    monkeypatch.setattr(bulk_import, "extract_listing_entries_llm", extract)
    return seen


def test_bulk_pasted_extract_then_commit(client, discover):
    r = client.post("/api/discover/bulk-extract/pasted",
                    json={"text": "排行榜\n1. 白鹤 作者A", "source_label": "jj", "engine": "claude"})
    assert r.status_code == 200 and r.json()["job_id"] == discover_svc.BULK_JOB_ID
    _wait(discover_svc.BULK_JOB_ID)
    res = client.get("/api/discover/bulk-extract/result")
    assert SECRET not in res.text
    result = res.json()["result"]
    assert discover == ["排行榜\n1. 白鹤 作者A"]
    assert [e["title"] for e in result["entries"]] == ["白鹤"]
    assert result["pages"] == [{"url": "Pasted text", "ok": True, "needs_manual": False,
                                "count": 1, "message": ""}]
    assert "_source_urls" not in result
    c = client.post("/api/discover/bulk-commit",
                    json={"entries": result["entries"], "source_label": "jj"})
    assert c.status_code == 200 and c.json()["added"] == 1
    row = db.list_known_titles()[0]
    assert row["title_original"] == "白鹤" and (row.get("source_url") or "") == ""


def test_bulk_pasted_input_checks(client, discover):
    for body in ({"text": ""}, {"text": "   "}, {"text": 5}, {"text": "x", "engine": "nope"},
                 {"text": "x" * (discover_svc.MAX_PASTED_LISTING_CHARS + 1)},
                 {"text": "x", "extra": True}):
        r = client.post("/api/discover/bulk-extract/pasted", json=body)
        assert r.status_code == 422, body
    huge = "x" * (discover_svc.MAX_PASTED_LISTING_CHARS * 4 + 70_000)
    r = client.post("/api/discover/bulk-extract/pasted", json={"text": huge})
    assert r.status_code == 413
    assert discover == []

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
from sources.models import AccessTier

from .test_adaptive_extraction import (FakeEngine, _HTML_BY_URL, chapter_html, chapter_url,
                                       fake_llm, novel_answer)  # noqa: F401 (fixture)

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"


def _dns(monkeypatch, ip="93.184.216.34"):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", (ip, port))])


class FakeFetch:
    """Stands in for generic_import.fetch_page: serves `pages[url]`."""

    def __init__(self):
        self.pages, self.calls = {}, []

    def __call__(self, url, client=None, rendered_fetch=None, user_html=None,
                 authenticated_fetch=None, allow_signed_in=True, allow_browser=True):
        self.calls.append({"url": url, "signed_in": allow_signed_in, "browser": allow_browser})
        lr = LadderResult(url)
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
    assert "test_offline" not in body["engines"] and "claude" in body["engines"]
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
    for engine in ("test_offline", "google", "nope"):
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
    c = TestClient(create_app(ApiSettings(auth_mode="on")),
                   base_url="https://baihe.example.com", raise_server_exceptions=False)
    u = auth_service.add_user("kid@example.com")
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

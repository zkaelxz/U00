"""Sources search/series routes (API batch 1, spec S-3). Fake adapters on a
scripted transport (tests/test_sources_search_service.py); no network."""
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
from services import auth_service
from services import sources_search_service as svc
from sources import registry
from sources.models import ChallengeDetected, FailureReason, SourceUnavailable
from tests.test_sources_search_service import HOST, SECRET, _make, _ok_routes


@pytest.fixture
def fakes(isolated_db, monkeypatch):
    classes = {}
    monkeypatch.setattr(registry, "adapter_classes", lambda: dict(classes))
    for jid in (svc.SEARCH_JOB_ID, svc.SERIES_JOB_PREFIX + "alpha"):
        background_jobs.clear_job(jid)
    yield classes
    for jid in list(background_jobs.list_all_jobs()):
        if jid.startswith("sources_"):
            _wait(jid)
            background_jobs.clear_job(jid)


@pytest.fixture
def client(fakes):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _wait(job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if not st or st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _clean(r):
    assert SECRET not in r.text and db.LIBRARY_DIR not in r.text and "?" not in r.text
    return r.json()


def test_search_flow_partial_failure(client, fakes):
    fakes["alpha"] = _make("alpha", _ok_routes("alpha"))
    fakes["beta"] = _make("beta", search_exc=SourceUnavailable("down", retry_after=42.0))
    r = client.post("/api/sources/search", json={"query": "abc"})
    assert r.status_code == 200 and r.json() == {"job_id": "sources_search"}
    _wait("sources_search")
    g = client.get("/api/sources/jobs/sources_search/result")
    b = _clean(g)
    assert g.status_code == 200 and b["status"] == "done"
    assert b["result"]["per_source_counts"] == {"alpha": 2, "beta": 0}
    assert b["result"]["errors"]["beta"]["details"]["retry_after"] == 42.0
    assert f"{HOST}/alpha/series/1" in g.text


def test_series_flow_and_challenge_409(client, fakes):
    fakes["alpha"] = _make("alpha")
    r = client.post("/api/sources/alpha/series", json={"series_id": "s1"})
    assert r.status_code == 200 and r.json() == {"job_id": "sources_series_alpha"}
    _wait("sources_series_alpha")
    b = _clean(client.get("/api/sources/jobs/sources_series_alpha/result"))
    assert [c["chapter_id"] for c in b["result"]["chapters"]] == ["c1", "c2", "c10"]
    assert (b["source"], b["series_id"]) == ("alpha", "s1")
    fakes["alpha"] = _make("alpha", series_exc=ChallengeDetected(
        "challenge", f"{HOST}/p?cf_token={SECRET}", FailureReason.CLOUDFLARE_CHALLENGE))
    client.post("/api/sources/alpha/series", json={"series_id": "s1"})
    _wait("sources_series_alpha")
    e = client.get("/api/sources/jobs/sources_series_alpha/result")
    assert e.status_code == 409
    err = _clean(e)["error"]
    assert err["code"] == "conflict" and err["details"]["handoff"] is True
    assert err["details"]["open_url"] == f"{HOST}/p"


def test_second_search_409(client, fakes):
    gate = threading.Event()
    fakes["alpha"] = _make("alpha", _ok_routes("alpha"), on_request=lambda u: gate.wait(5))
    assert client.post("/api/sources/search", json={"query": "abc"}).status_code == 200
    r = client.post("/api/sources/search", json={"query": "abc"})
    assert r.status_code == 409
    gate.set()
    _wait("sources_search")


def test_404_422_400(client, fakes):
    fakes["alpha"] = _make("alpha", _ok_routes("alpha"))
    idle = client.get("/api/sources/jobs/sources_search/result")
    assert idle.status_code == 200
    assert idle.json()["status"] == "idle" and idle.json()["result"] is None
    assert client.get("/api/sources/jobs/translate_1/result").status_code == 404
    assert client.post("/api/sources/search",
                       json={"query": "abc", "sources": ["nope"]}).status_code == 404
    assert client.post("/api/sources/nope/series", json={"series_id": "s1"}).status_code == 404
    for body in ({"query": ""}, {"query": "https://fake.invalid/x"}, {"query": "a" * 201},
                 {"query": "abc", "url": "https://x"}, {}):
        assert client.post("/api/sources/search", json=body).status_code == 422, body
    for sid in ("http://evil/x", "//169.254.169.254/x", "a@evil.invalid", "", "a" * 201):
        r = client.post("/api/sources/alpha/series", json={"series_id": sid})
        assert r.status_code == 422, sid
    assert background_jobs.get_status("sources_series_alpha") is None
    registry.set_enabled("alpha", False)
    r = client.post("/api/sources/search", json={"query": "abc", "sources": ["alpha"]})
    assert r.status_code == 400


def test_does_not_shadow_catalog_routes(client, fakes):
    fakes["alpha"] = _make("alpha")
    assert client.get("/api/sources/alpha").status_code == 200
    assert client.get("/api/sources/tracked").status_code == 200


def test_auth_on(fakes):
    fakes["alpha"] = _make("alpha", _ok_routes("alpha"))
    c = TestClient(create_app(ApiSettings(auth_mode="on")),
                   base_url="https://baihe.example.com", raise_server_exceptions=False)
    assert c.post("/api/sources/search", json={"query": "abc"}).status_code == 401
    assert c.get("/api/sources/jobs/sources_search/result").status_code == 401
    u = auth_service.add_user("kid@example.com")
    s = auth_service.create_session(u["id"])
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    assert c.post("/api/sources/search", json={"query": "abc"}, headers=h).status_code == 200
    _wait("sources_search")
    assert c.get("/api/sources/jobs/sources_search/result", headers=h).status_code == 200
    auth_service.revoke_permission(u["id"], "library.read")
    assert c.post("/api/sources/alpha/series", json={"series_id": "s1"},
                  headers=h).status_code == 403
    assert c.get("/api/sources/jobs/sources_search/result", headers=h).status_code == 403

"""Diagnostics gaps routes (API batch 1, Streamlit retirement M1) over
services/diagnostics_gaps_service.py. Every check that could reach pip,
Hugging Face, ffmpeg or the library is faked; no network, no install."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import diagnostics
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import diagnostics_gaps_service as svc
from services import settings_service

SECRET = "sk-ant-api03-SECRETSECRETSECRET123456"
HF_TOKEN = "hf_AbCdEfGhIjKlMnOpQrStUvWxYz0123"
ABS_PATH = "/home/someone/private/library/drama.mp4"
DIRTY = f"failed with {SECRET} token {HF_TOKEN} at {ABS_PATH}"
REMOTE = "https://baihe.example.com"
RUNNING = {"on": False}   # what the faked "any job running here or elsewhere" check answers


def _clean(r):
    for bad in (SECRET, HF_TOKEN, ABS_PATH, "/home/someone", db.LIBRARY_DIR):
        assert bad not in r.text, bad
    return r.json()


@pytest.fixture
def fakes(isolated_db, monkeypatch):
    """Everything the service reaches outside the process, faked dirty."""
    monkeypatch.setattr(diagnostics, "check_python_version",
                        lambda: {"version": "3.11.0", "ok": True, "path": ABS_PATH})
    monkeypatch.setattr(diagnostics, "check_ffmpeg",
                        lambda: {"found": True, "version": f"ffmpeg 6 {ABS_PATH}", "path": ABS_PATH})
    monkeypatch.setattr(diagnostics, "check_js_runtime",
                        lambda: {"found": True, "name": "deno", "path": ABS_PATH})
    monkeypatch.setattr(diagnostics, "check_cuda",
                        lambda: {"torch_installed": False, "cuda_available": None})
    monkeypatch.setattr(diagnostics, "scan_hf_cache", lambda *a, **k: [
        {"repo_id": "org/model", "repo_type": "model", "revision": "abc123",
         "size_bytes": 10, "path": ABS_PATH}])
    monkeypatch.setattr(diagnostics, "scan_piper_voices", lambda *a, **k: [
        {"voice": "en_US-amy", "size_bytes": 5, "path": ABS_PATH}])
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda name: HF_TOKEN if name == "hf_token" else None)
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
    monkeypatch.setattr(diagnostics, "check_pyannote_gated_access", lambda token, api=None: [
        {"model": "pyannote/speaker-diarization-3.1", "accessible": True, "error": DIRTY}])
    monkeypatch.setattr(background_jobs, "list_all_jobs", lambda: {
        "emotion_999999": {"status": "error", "message": DIRTY, "error": DIRTY,
                           "description": DIRTY, "started_at": 10.0, "finished_at": 12.5},
        "custom_job": {"status": "done", "message": "ok", "started_at": 1.0,
                       "finished_at": 2.0}})
    from services import library_admin_service
    RUNNING["on"] = False
    monkeypatch.setattr(library_admin_service, "_any_job_running", lambda: RUNNING["on"])
    import applog
    monkeypatch.setattr(applog, "tail", lambda n: [f"INFO line {i}" for i in range(n - 1)]
                        + [f"ERROR {DIRTY}"])
    monkeypatch.setattr(svc, "build_support_report", lambda recent_error_lines=20:
                        diagnostics.redact_for_support(f"Report\nERROR {DIRTY}"))
    calls = []

    def fake_stream(cmd, timeout, cwd=None, env=None):
        calls.append(("pip", cmd))
        yield {"line": DIRTY}
        yield {"returncode": 0, "timed_out": False}

    monkeypatch.setattr(svc, "_stream_tree", fake_stream)
    monkeypatch.setattr(db, "reset_library", lambda: calls.append(("reset",)))
    monkeypatch.setattr(background_jobs, "clear_all_jobs", lambda: calls.append(("clear",)))
    return calls


@pytest.fixture
def client(fakes):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def test_reads(client):
    b = _clean(client.get("/api/diagnostics/setup-checks"))
    assert b["python"] == {"version": "3.11.0", "ok": True}
    assert b["ffmpeg"]["found"] is True and b["js_runtime"] == {"found": True, "name": "deno"}
    assert "path" not in json.dumps(b)
    m = _clean(client.get("/api/diagnostics/model-cache"))
    assert m["hf_total_bytes"] == 10 and m["piper_voices"] == [{"voice": "en_US-amy",
                                                                 "size_bytes": 5}]
    p = _clean(client.get("/api/diagnostics/pyannote"))
    assert p["hf_token_configured"] is True and p["models"] is None
    p = _clean(client.get("/api/diagnostics/pyannote?check_access=true"))
    assert p["models"] == [{"model": "pyannote/speaker-diarization-3.1", "accessible": True}]
    assert p["ready"] is True
    h = _clean(client.get("/api/diagnostics/job-history"))
    assert [j["job_id"] for j in h] == ["emotion_999999", "custom_job"]
    assert h[0]["duration_seconds"] == 2.5
    lg = _clean(client.get("/api/diagnostics/log?n=5&keyword=ERROR"))
    assert len(lg["lines"]) == 1 and lg["lines"][0].startswith("ERROR")
    assert len(_clean(client.get("/api/diagnostics/log"))["lines"]) == svc.LOG_TAIL_DEFAULT
    rep = _clean(client.get("/api/diagnostics/support-report"))
    assert rep["report"].startswith("Report")


def test_log_bounds_422(client):
    assert client.get("/api/diagnostics/log?n=201").status_code == 422
    assert client.get("/api/diagnostics/log?n=-1").status_code == 422
    assert client.get("/api/diagnostics/log?keyword=" + "a" * 101).status_code == 422


def test_install_and_upgrade(client, fakes):
    for action in ("install", "upgrade"):
        r = client.post(f"/api/diagnostics/dependencies/edge_tts/{action}", json={"confirm": True})
        b = _clean(r)
        assert r.status_code == 200 and b["ok"] is True and b["package"] == "edge_tts"
    assert len(fakes) == 2


def test_install_refusals(client, fakes, monkeypatch):
    url = "/api/diagnostics/dependencies/edge_tts/install"
    for body in ({}, {"confirm": False}, {"confirm": "yes"}, {"confirm": 1}):
        assert client.post(url, json=body).status_code == 422, body
    assert client.post(url, json={"confirm": True, "extra": 1}).status_code == 422
    r = client.post("/api/diagnostics/dependencies/streamlit/install", json={"confirm": True})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.post("/api/diagnostics/dependencies/evil;rm/install",
                       json={"confirm": True}).status_code in (404, 422)
    assert client.post("/api/diagnostics/dependencies/-e/install",
                       json={"confirm": True}).status_code == 422
    RUNNING["on"] = True
    r = client.post(url, json={"confirm": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    assert fakes == []


def test_reset_library(client, fakes, monkeypatch):
    url = "/api/diagnostics/reset-library"
    for body in ({"confirm": True}, {"confirm": True, "confirm_text": "reset"},
                 {"confirm": False, "confirm_text": "RESET"}, {"confirm_text": "RESET"}):
        assert client.post(url, json=body).status_code == 422, body
    RUNNING["on"] = True
    assert client.post(url, json={"confirm": True, "confirm_text": "RESET"}).status_code == 409
    assert fakes == []
    RUNNING["on"] = False
    r = client.post(url, json={"confirm": True, "confirm_text": "RESET"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert fakes == [("reset",), ("clear",)]


def _admin_session():
    u = auth_service.grant_admin_local("admin@example.com")
    return auth_service.create_session(u["id"])


def _h(s):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


READS = ("/api/diagnostics/setup-checks", "/api/diagnostics/model-cache",
         "/api/diagnostics/pyannote", "/api/diagnostics/job-history",
         "/api/diagnostics/log", "/api/diagnostics/support-report")
WRITES = (("/api/diagnostics/dependencies/edge_tts/install", {"confirm": True}),
          ("/api/diagnostics/dependencies/edge_tts/upgrade", {"confirm": True}),
          ("/api/diagnostics/reset-library", {"confirm": True, "confirm_text": "RESET"}))


def test_auth_on_reads_need_admin(fakes):
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                   raise_server_exceptions=False)
    for path in READS:
        assert c.get(path).status_code == 401, path
    u = auth_service.add_user("kid@example.com")
    for p in auth_service.PERMISSIONS:
        if not p.startswith("admin."):
            try:
                auth_service.grant_permission(u["id"], p)
            except Exception:
                pass
    kid = auth_service.create_session(u["id"])
    for path in READS:
        assert c.get(path, headers=_h(kid)).status_code == 403, path
    admin = _admin_session()
    for path in READS:
        assert c.get(path, headers=_h(admin)).status_code == 200, path


def test_auth_on_writes_are_pc_only(fakes):
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    admin = _admin_session()
    for path, body in WRITES:
        assert remote.post(path, json=body, headers=_h(admin)).status_code == 403, path
        assert remote.post(path, json=body).status_code == 403, path
    local = TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
    for path, body in WRITES:
        assert local.post(path, json=body, headers={"X-Forwarded-For": "1.2.3.4"}
                          ).status_code == 403, path
    assert fakes == []
    for path, body in WRITES:
        assert local.post(path, json=body).status_code == 200, path
    assert ("reset",) in fakes

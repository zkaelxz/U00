"""Diagnostics gaps routes (API batch 1, Streamlit retirement M1) over
services/diagnostics_gaps_service.py. Every check that could reach pip,
Hugging Face, ffmpeg or the library is faked; no network, no install."""
import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import diagnostics
import diagnostics_torch
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import diagnostics_gaps_service as svc
from services import diagnostics_installs_service as installs
from services import settings_service

SECRET = "sk-ant-api03-SECRETSECRETSECRET123456"
HF_TOKEN = "hf_AbCdEfGhIjKlMnOpQrStUvWxYz0123"
ABS_PATH = "/home/someone/private/library/drama.mp4"
DIRTY = f"failed with {SECRET} token {HF_TOKEN} at {ABS_PATH}"
REMOTE = "https://baihe.example.com"
RUNNING = {"on": False}   # what the faked "any job running here or elsewhere" check answers


def _wait_install_job(timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        job = background_jobs.get_status(installs.DEPENDENCY_JOB_ID)
        if job and job["status"] not in ("running", "queued"):
            return
        time.sleep(0.02)
    raise AssertionError("install job did not finish")


def _install_result(client):
    """Waits for the install job to end, then reads the install state."""
    _wait_install_job()
    return _clean(client.get("/api/diagnostics/dependency-install"))


def _clean(r):
    for bad in (SECRET, HF_TOKEN, ABS_PATH, "/home/someone", db.LIBRARY_DIR):
        assert bad not in r.text, bad
    return r.json()


_real_check_browser = diagnostics.check_browser


@pytest.fixture
def fakes(isolated_db, monkeypatch):
    """Everything the service reaches outside the process, faked dirty."""
    monkeypatch.setattr(diagnostics, "check_python_version",
                        lambda: {"version": "3.11.0", "ok": True, "path": ABS_PATH})
    monkeypatch.setattr(diagnostics, "check_ffmpeg",
                        lambda: {"found": True, "version": f"ffmpeg 6 {ABS_PATH}", "path": ABS_PATH})
    monkeypatch.setattr(diagnostics, "check_js_runtime",
                        lambda: {"found": True, "name": "deno", "path": ABS_PATH})
    monkeypatch.setattr(diagnostics, "check_browser",
                        lambda: {"found": True, "name": "Chrome", "path": ABS_PATH})
    monkeypatch.setattr(diagnostics, "check_cuda",
                        lambda: {"torch_installed": False, "cuda_available": None})
    monkeypatch.setattr(diagnostics, "scan_hf_cache", lambda *a, **k: [
        {"repo_id": "org/model", "repo_type": "model", "revision": "abc123",
         "size_bytes": 10, "path": ABS_PATH}])
    monkeypatch.setattr(diagnostics, "scan_model_folder", lambda kind, *a, **k: [
        {"name": f"{kind}.bin", "size_bytes": 3, "path": ABS_PATH}])
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda name: HF_TOKEN if name == "hf_token" else None)
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
    monkeypatch.setattr(diagnostics, "check_pyannote_gated_access", lambda token, api=None: [
        {"model": "pyannote/speaker-diarization-3.1", "accessible": True, "error": DIRTY}])
    monkeypatch.setattr(svc, "_hf_hub_importable", lambda: True)  # the check runs even without the hub installed
    monkeypatch.setattr(background_jobs, "list_all_jobs", lambda: {
        "emotion_999999": {"status": "error", "message": DIRTY, "error": DIRTY,
                           "description": DIRTY, "started_at": 10.0, "finished_at": 12.5},
        "custom_job": {"status": "done", "message": "ok", "started_at": 1.0,
                       "finished_at": 2.0}})
    from services import library_admin_service
    RUNNING["on"] = False
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: RUNNING["on"])
    import applog
    monkeypatch.setattr(applog, "tail", lambda n: [f"INFO line {i}" for i in range(n - 1)]
                        + [f"ERROR {DIRTY}"])
    monkeypatch.setattr(svc, "build_support_report", lambda recent_error_lines=20:
                        diagnostics.redact_for_support(f"Report\nERROR {DIRTY}"))
    calls = []

    def fake_stream(cmd, timeout, cwd=None, env=None, cancel=None, **_kw):
        calls.append(("pip", cmd))
        yield {"line": DIRTY}
        yield {"returncode": 0, "timed_out": False}

    monkeypatch.setattr(svc, "stream_tree", fake_stream)
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info",
                        lambda: {"gpu_name": "NVIDIA GeForce RTX 3080 Ti", "driver_version": "580.97"})
    monkeypatch.setattr(svc, "verify_torch", lambda blocking=True, cancel=None: {
        "torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128", "torchaudio": "2.11.0+cu128",
        "cuda_build": "12.8", "cuda_available": True, "device": "RTX", "error": None})
    monkeypatch.setattr(db, "reset_library", lambda: calls.append(("reset",)))
    monkeypatch.setattr(background_jobs, "clear_all_jobs", lambda: calls.append(("clear",)))
    background_jobs.clear_job(installs.DEPENDENCY_JOB_ID)
    installs._DEPENDENCY.update(kind=None, package=None, last=None)
    yield calls
    background_jobs.clear_job(installs.DEPENDENCY_JOB_ID)


@pytest.fixture
def client(fakes):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.mark.parametrize("installed", [True, False])
def test_setup_checks_report_the_playwright_package(client, monkeypatch, installed):
    # Through the real check_browser so a dropped key can't hide behind a fake.
    import browser_support
    import page_fetch
    monkeypatch.setattr(diagnostics, "check_browser", _real_check_browser)
    monkeypatch.setattr(page_fetch, "browser_status", lambda: {"found": True, "name": "Chrome"})
    monkeypatch.setattr(browser_support, "package_installed", lambda: installed)
    b = _clean(client.get("/api/diagnostics/setup-checks"))
    assert b["browser"]["package"] is installed


def test_reads(client):
    b = _clean(client.get("/api/diagnostics/setup-checks"))
    assert b["python"] == {"version": "3.11.0", "ok": True}
    assert b["ffmpeg"]["found"] is True and b["js_runtime"] == {"found": True, "name": "deno"}
    assert b["browser"] == {"found": True, "name": "Chrome", "package": False}
    assert "path" not in json.dumps(b)
    m = _clean(client.get("/api/diagnostics/model-cache"))
    assert m["hf_total_bytes"] == 10
    assert "piper_voices" not in m and "piper_total_bytes" not in m
    assert m["model_files"] == [
        {"folder": "torch", "name": "torch.bin", "size_bytes": 3},
        {"folder": "audio_separator", "name": "audio_separator.bin", "size_bytes": 3}]
    assert m["model_files_total_bytes"] == 6
    p = _clean(client.get("/api/diagnostics/pyannote"))
    assert p["hf_token_configured"] is True and p["models"] is None
    p = _clean(client.get("/api/diagnostics/pyannote?check_access=true"))
    assert p["models"] == [{"model": "pyannote/speaker-diarization-3.1", "accessible": True}]
    assert p["ready"] is True
    assert client.get("/api/diagnostics/job-history").status_code == 404
    lg = _clean(client.get("/api/diagnostics/log?n=5&keyword=ERROR"))
    assert len(lg["lines"]) == 1 and lg["lines"][0].startswith("ERROR")
    assert len(_clean(client.get("/api/diagnostics/log"))["lines"]) == svc.LOG_TAIL_DEFAULT
    rep = _clean(client.get("/api/diagnostics/support-report"))
    assert rep["report"].startswith("Report")


def test_install_presets(client):
    b = _clean(client.get("/api/diagnostics/install-presets"))
    ids = [t["id"] for t in b["tasks"]]
    assert "transcribe" in ids and "scanlate" in ids
    cv2 = b["packages"]["cv2"]
    assert cv2["dist"] == "opencv-python" and cv2["installed"] is True   # fakes: all installed
    assert cv2["source_url"] == "https://pypi.org/project/opencv-python/"
    assert b["packages"]["lightnovel-crawler"]["not_offered_reason"]


def test_install_failure_hint(client, monkeypatch):
    def fake_stream(cmd, timeout, cancel=None, **_kw):
        yield {"line": "ERROR: [Errno 13] Permission denied: "
                       "'C:\\users\\x\\appdata\\local\\pip\\cache\\wheels\\a.whl'"}
        yield {"returncode": 1, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(svc, "stream_tree", fake_stream)
    client.post("/api/diagnostics/dependencies/jieba/install", json={"confirm": True})
    b = _install_result(client)
    assert b["result"]["ok"] is False and "pip\\cache" in b["result"]["hint"]
    assert b["job"]["status"] == "error"


def test_log_bounds_422(client):
    assert client.get("/api/diagnostics/log?n=201").status_code == 422
    assert client.get("/api/diagnostics/log?n=-1").status_code == 422
    assert client.get("/api/diagnostics/log?keyword=" + "a" * 101).status_code == 422


def test_install_runs_as_a_job_and_upgrade_stays_synchronous(client, fakes):
    r = client.post("/api/diagnostics/dependencies/pydub/install", json={"confirm": True})
    assert r.status_code == 200 and r.json() == {"job_id": "dependency_install", "started": True}
    b = _install_result(client)
    assert b["kind"] == "package" and b["package"] == "pydub"
    assert b["result"]["ok"] is True and b["result"]["package"] == "pydub"
    assert b["job"]["status"] == "done" and not background_jobs.exclusive_active()
    r = client.post("/api/diagnostics/dependencies/pydub/upgrade", json={"confirm": True})
    b = _clean(r)
    assert r.status_code == 200 and b["ok"] is True and b["package"] == "pydub"
    assert len(fakes) == 2


def test_install_state_before_any_install(client):
    b = _clean(client.get("/api/diagnostics/dependency-install"))
    assert b["result"] is None and b["job"] is None and b["job_id"] == "dependency_install"


def test_install_refusals(client, fakes, monkeypatch):
    url = "/api/diagnostics/dependencies/pydub/install"
    for body in ({}, {"confirm": False}, {"confirm": "yes"}, {"confirm": 1}):
        assert client.post(url, json=body).status_code == 422, body
    assert client.post(url, json={"confirm": True, "extra": 1}).status_code == 422
    r = client.post("/api/diagnostics/dependencies/fastapi/install", json={"confirm": True})
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
         "/api/diagnostics/pyannote",
         "/api/diagnostics/log", "/api/diagnostics/support-report",
         "/api/diagnostics/install-presets", "/api/diagnostics/gpu-torch",
         "/api/diagnostics/dependency-install")
WRITES = (("/api/diagnostics/dependencies/pydub/install", {"confirm": True}),
          ("/api/diagnostics/gpu-torch/setup", {"confirm": True, "variant": "cu128"}),
          ("/api/diagnostics/dependencies/pydub/upgrade", {"confirm": True}),
          ("/api/diagnostics/reset-library", {"confirm": True, "confirm_text": "RESET"}))


def test_gpu_torch_status_and_setup(client, fakes):
    b = _clean(client.get("/api/diagnostics/gpu-torch"))
    assert b["nvidia"]["found"] is True and b["nvidia"]["status"] == "ok"
    assert b["recommended"]["variant"] == "cu128" and b["probe"] is None
    assert b["recommended"]["index_url"] == "https://download.pytorch.org/whl/cu128"
    assert _clean(client.get("/api/diagnostics/gpu-torch?probe=true"))["probe"] is None
    assert _clean(client.post("/api/diagnostics/gpu-torch/check", json={}))["probe"]["cuda_available"]
    RUNNING["on"] = True        # the CUDA check takes VRAM: not while a job runs
    assert client.post("/api/diagnostics/gpu-torch/check", json={}).status_code == 409
    RUNNING["on"] = False
    r = client.post("/api/diagnostics/gpu-torch/setup", json={"confirm": True})
    assert r.status_code == 200 and r.json()["started"] is True
    state = _install_result(client)
    out = state["result"]
    assert state["kind"] == "gpu_torch" and out["ok"] is True and out["variant"] == "cu128"
    assert out["verify"]["torch"] == "2.11.0+cu128"
    assert len(fakes) == 2        # force-reinstall --no-deps, then the deps pass


def test_gpu_torch_setup_refusals(client, fakes):
    url = "/api/diagnostics/gpu-torch/setup"
    for body in ({}, {"confirm": False}, {"confirm": True, "variant": "cu130"},
                 {"confirm": True, "index_url": "https://evil.example/simple"},
                 {"confirm": True, "variant": "cu128", "version": "2.14.0"}):
        assert client.post(url, json=body).status_code == 422, body
    RUNNING["on"] = True
    assert client.post(url, json={"confirm": True}).status_code == 409
    assert fakes == []


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
        if "/install" in path or "gpu-torch/setup" in path:
            _wait_install_job()      # one install at a time
    assert ("reset",) in fakes


def test_package_updates_check_needs_admin_and_asks_only_when_called(fakes, monkeypatch):
    asked = []
    monkeypatch.setattr(diagnostics, "pypi_release_versions",
                        lambda dist: asked.append(dist) or ["99.0.0"])
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda d: "1.0.0")
    svc._UPDATES.update(checked_at=None, packages={})
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                   raise_server_exceptions=False)
    url = "/api/diagnostics/package-updates/check"
    admin = _admin_session()
    c.get("/api/diagnostics/install-presets", headers=_h(admin))
    assert asked == []                                     # page load never asks PyPI
    assert c.post(url, json={}).status_code in (401, 403)
    r = c.post(url, json={}, headers=_h(admin))
    b = _clean(r)
    assert r.status_code == 200 and asked
    assert b["packages"]["pydub"]["status"] == "update"
    assert b["packages"]["pydub"]["target"] == "99.0.0"
    assert c.post("/api/diagnostics/gpu-torch/check", json={}).status_code in (401, 403)
    svc._UPDATES.update(checked_at=None, packages={})


def test_upgrade_binds_the_confirmed_target(client, fakes, monkeypatch):
    monkeypatch.setattr(diagnostics, "pypi_release_versions", lambda dist: ["1.0.0", "2.0.0"])
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda d: "1.0.0")
    svc._UPDATES.update(checked_at=None, packages={})
    assert client.post("/api/diagnostics/package-updates/check", json={}).status_code == 200
    url = "/api/diagnostics/dependencies/pydub/upgrade"
    for bad in ("--index-url=x", "1.0 --pre", "", "a" * 65):
        assert client.post(url, json={"confirm": True, "target": bad}).status_code == 422, bad
    r = client.post(url, json={"confirm": True, "target": "1.5.0"})
    assert r.status_code == 409 and fakes == []
    r = client.post(url, json={"confirm": True, "target": "2.0.0"})
    assert r.status_code == 200
    assert "pydub==2.0.0" in fakes[0][1] or "pydub==2.0.0" in fakes[0][1]
    svc._UPDATES.update(checked_at=None, packages={})

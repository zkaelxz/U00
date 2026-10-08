"""Diagnostics > "Install browser support" (services/browser_install_service.py,
browser_support.py, the page_fetch hand-off). The installer is a small local
Python script standing in for `playwright install chromium`: no network and
no real browser download."""
import os
import sys
import textwrap
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import browser_support
import page_fetch
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import browser_install_service as svc

REMOTE = "https://baihe.example.com"
SECRET = "sk-ant-api03-SECRETSECRETSECRET123456"
WANTED = ["chromium-1", "chromium_headless_shell-1"]

# Writes the files a finished install leaves, then optionally misbehaves.
INSTALLER = textwrap.dedent("""
    import os, sys, time
    root = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    mode = sys.argv[1]
    open(os.path.join(os.path.dirname(root), "seen_env.txt"), "w").write(
        root + "|" + str(os.environ.get("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD")))
    open(os.path.join(os.path.dirname(root), "pid.txt"), "w").write(str(os.getpid()))
    if mode == "hang":
        print("Downloading Chromium 10%", flush=True)
        time.sleep(60)
    if mode == "fail":
        os.makedirs(os.path.join(root, "chromium-1"))
        print("net::ERR_CONNECTION_RESET", flush=True)
        sys.exit(1)
    print("key " + os.environ["FAKE_SECRET"] + " in " + root + "/chromium-1", flush=True)
    for i in range(1, 31):
        print("progress", i * 3, "%", "x" * 400, flush=True)
    for name in ("chromium-1", "chromium_headless_shell-1"):
        d = os.path.join(root, name)
        os.makedirs(d, exist_ok=True)
        exe = os.path.join(d, "chrome")
        open(exe, "w").write("x")
        os.chmod(exe, 0o755)
    if mode == "nofiles":
        import shutil
        shutil.rmtree(root)
""")


def _wait(timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(svc.BROWSER_JOB_ID)
        if st and st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@pytest.fixture
def env(isolated_db, monkeypatch, tmp_path):
    from services import library_admin_service
    state = {"mode": "ok", "playwright": True, "system": False, "free": 5000}
    folder = tmp_path / "data" / "playwright_browsers"
    script = tmp_path / "installer.py"
    script.write_text(INSTALLER, encoding="utf-8")
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)
    monkeypatch.setattr(browser_support, "app_browsers_folder", lambda: str(folder))
    monkeypatch.setattr(browser_support, "wanted_browser_folders", lambda: list(WANTED))
    monkeypatch.setattr(svc, "_playwright_importable", lambda: state["playwright"])
    monkeypatch.setattr(svc, "_system_browser_found", lambda: state["system"])
    monkeypatch.setattr(svc, "_free_mb", lambda: state["free"])
    monkeypatch.setattr(svc, "_command", lambda: [sys.executable, str(script), state["mode"]])
    monkeypatch.setenv("FAKE_SECRET", SECRET)
    monkeypatch.setenv("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")
    background_jobs.clear_job(svc.BROWSER_JOB_ID)
    svc._RESULT["last"] = None
    state["folder"], state["tmp"] = folder, tmp_path
    yield state
    background_jobs.request_cancel(svc.BROWSER_JOB_ID)
    background_jobs.wait_for_job_threads(10, job_ids=[svc.BROWSER_JOB_ID])
    background_jobs.clear_job(svc.BROWSER_JOB_ID)


@pytest.fixture
def client(env):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _install(client):
    return client.post("/api/diagnostics/browser/install", json={"confirm": True})


def test_the_command_is_fixed():
    assert svc.__dict__["_command"]() == [sys.executable, "-m", "playwright", "install", "chromium"]


def test_status_is_booleans_sizes_and_text(client, env):
    b = client.get("/api/diagnostics/browser").json()
    assert b["playwright_installed"] is True and b["app_browser_installed"] is False
    assert b["system_browser_found"] is False and b["free_mb"] == 5000 and b["required_mb"] == 300
    assert b["refusal"] is None and b["job"] is None and b["last_result"] is None
    assert str(env["tmp"]) not in str(b)


def test_install_runs_into_the_app_folder_and_reports_redacted_output(client, env):
    r = _install(client)
    assert r.status_code == 200 and r.json() == {"job_id": "browser_install", "started": True}
    assert _wait()["status"] == "done"
    # The subprocess got the app folder (not a global cache) and no skip flag.
    seen = (env["folder"].parent / "seen_env.txt").read_text()
    assert seen == f"{env['folder']}|None"
    b = client.get("/api/diagnostics/browser").json()
    assert b["app_browser_installed"] is True and b["last_result"]["ok"] is True
    tail = b["last_result"]["output_tail"]
    assert 0 < len(tail) <= svc.OUTPUT_TAIL_LINES
    assert all(len(line) <= svc.OUTPUT_LINE_CHARS for line in tail)
    assert SECRET not in str(b) and str(env["tmp"]) not in str(b)
    # Redacted before it was ever stored, so the job record is clean too.
    assert SECRET not in str(background_jobs.get_status(svc.BROWSER_JOB_ID))


def test_secret_and_path_are_redacted_in_every_line():
    out = svc._clean(f"key {SECRET} in {os.path.expanduser('~')}/x/y")
    assert SECRET not in out and os.path.expanduser("~") not in out


def test_the_subprocess_is_not_a_shell_and_decodes_utf8(client, env, monkeypatch):
    seen = {}
    real = svc.subprocess.Popen

    def spy(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return real(cmd, **kw)
    monkeypatch.setattr(svc.subprocess, "Popen", spy)
    _install(client)
    _wait()
    assert isinstance(seen["cmd"], list) and not seen.get("shell")
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace" and seen["text"] is True


@pytest.mark.parametrize("setup, status, text", [
    ({"playwright": False}, 422, "Packages"),
    ({"system": True}, 409, "Chrome or Edge"),
    ({"free": 100}, 422, "disk space"),
])
def test_refusals_start_nothing(client, env, setup, status, text):
    env.update(setup)
    r = _install(client)
    assert r.status_code == status and text in r.json()["error"]["message"]
    assert background_jobs.get_status(svc.BROWSER_JOB_ID) is None
    assert client.get("/api/diagnostics/browser").json()["refusal"] is not None


def test_already_installed_is_409(client, env):
    _install(client)
    _wait()
    r = _install(client)
    assert r.status_code == 409 and "already installed" in r.json()["error"]["message"]


def test_unconfirmed_is_422(client, env):
    r = client.post("/api/diagnostics/browser/install", json={"confirm": False})
    assert r.status_code == 422 and background_jobs.get_status(svc.BROWSER_JOB_ID) is None
    assert client.post("/api/diagnostics/browser/install",
                       json={"confirm": True, "args": ["x"]}).status_code == 422


def test_failure_says_so_and_removes_the_partial_folder(client, env):
    env["mode"] = "fail"
    _install(client)
    assert _wait()["status"] == "error"
    last = client.get("/api/diagnostics/browser").json()["last_result"]
    assert last["ok"] is False and "failed" in last["message"]
    assert "ERR_CONNECTION_RESET" in " ".join(last["output_tail"])
    assert not env["folder"].exists()


def test_finishing_without_a_browser_is_a_failure(client, env):
    env["mode"] = "nofiles"
    _install(client)
    assert _wait()["status"] == "error"
    assert "no browser was found" in client.get("/api/diagnostics/browser").json()["last_result"]["message"]


def test_timeout_kills_the_process(client, env, monkeypatch):
    monkeypatch.setattr(svc, "INSTALL_TIMEOUT_SECONDS", 1)
    env["mode"] = "hang"
    _install(client)
    assert _wait()["status"] == "error"
    assert "took too long" in client.get("/api/diagnostics/browser").json()["last_result"]["message"]
    assert not _alive(int((env["folder"].parent / "pid.txt").read_text()))
    assert not env["folder"].exists()


def test_the_timeout_is_about_fifteen_minutes():
    assert svc.INSTALL_TIMEOUT_SECONDS == 15 * 60


def test_cancel_kills_the_process_and_cleans_up(client, env):
    env["mode"] = "hang"
    _install(client)
    for _ in range(200):
        if (env["folder"].parent / "pid.txt").exists():
            break
        time.sleep(0.02)
    pid = int((env["folder"].parent / "pid.txt").read_text())
    assert _alive(pid)
    background_jobs.request_cancel(svc.BROWSER_JOB_ID)
    assert _wait()["status"] == "cancelled"
    assert not _alive(pid) and not env["folder"].exists()
    assert client.get("/api/diagnostics/browser").json()["last_result"]["message"] == "Cancelled."


def _fake_playwright(monkeypatch):
    import types
    pkg = types.ModuleType("playwright")
    sync = types.ModuleType("playwright.sync_api")
    sync.sync_playwright = lambda: "sync"
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync)


def _put_browser(folder):
    for name in WANTED:
        d = folder / name
        d.mkdir(parents=True)
        exe = d / "chrome"
        exe.write_text("x")
        exe.chmod(0o755)


def test_page_fetch_launches_from_the_app_folder_when_present(env, monkeypatch, tmp_path):
    _fake_playwright(monkeypatch)
    monkeypatch.setenv(browser_support.BROWSERS_ENV, str(tmp_path / "global-cache"))
    monkeypatch.setattr(page_fetch, "_system_browser_candidates", lambda: [])
    _put_browser(env["folder"])
    assert page_fetch._require_playwright()() == "sync"
    assert os.environ[browser_support.BROWSERS_ENV] == str(env["folder"])
    assert page_fetch.browser_status() == {"found": True, "name": "Playwright Chromium"}


def test_page_fetch_leaves_the_environment_alone_without_an_app_browser(env, monkeypatch, tmp_path):
    _fake_playwright(monkeypatch)
    monkeypatch.setenv(browser_support.BROWSERS_ENV, str(tmp_path / "global-cache"))
    page_fetch._require_playwright()
    assert os.environ[browser_support.BROWSERS_ENV] == str(tmp_path / "global-cache")


def test_a_half_unpacked_build_is_not_present(env):
    (env["folder"] / "chromium-1").mkdir(parents=True)
    assert browser_support.app_chromium_present() is False


def _h(s):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_auth_on_read_is_admin_and_install_is_pc_only(env):
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    u = auth_service.add_user("kid@example.com")
    for p in auth_service.PERMISSIONS:
        if not p.startswith("admin."):
            try:
                auth_service.grant_permission(u["id"], p)
            except Exception:
                pass
    kid = auth_service.create_session(u["id"])
    admin = auth_service.create_session(auth_service.grant_admin_local("a@example.com")["id"])
    path = "/api/diagnostics/browser"
    assert remote.get(path).status_code == 401
    assert remote.get(path, headers=_h(kid)).status_code == 403
    assert remote.get(path, headers=_h(admin)).status_code == 200
    body = {"confirm": True}
    assert remote.post(path + "/install", json=body, headers=_h(admin)).status_code == 403
    local = TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
    assert local.post(path + "/install", json=body,
                      headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
    assert background_jobs.get_status(svc.BROWSER_JOB_ID) is None

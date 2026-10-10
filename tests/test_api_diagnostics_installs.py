"""Deno install (Q02) and "Test first" for an update (Q06) as background
jobs, over services/diagnostics_installs_service.py. Every download, winget
run and pip/pytest run is faked; no network, no install."""
import hashlib
import io
import time
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import diagnostics
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import diagnostics_gaps_service as gaps
from services import diagnostics_installs_service as svc

REMOTE = "https://baihe.example.com"
SECRET = "sk-ant-api03-SECRETSECRETSECRET123456"
ABS_PATH = "/home/someone/private/thing"
LATEST = svc.DENO_DOWNLOADS[("Linux", "x86_64")]
VERSIONED = ("https://github.com/denoland/deno/releases/download/v2.9.7/"
             "deno-x86_64-unknown-linux-gnu.zip")
ASSET_HOST = "https://release-assets.githubusercontent.com/github-production-release-asset/1/x"


def _zip(member="deno", data=b"#!deno-binary"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, data)
    return buf.getvalue()


class _Resp:
    def __init__(self, status=200, body=b"", location=None, length=True):
        self.status_code = status
        self.headers = {}
        if location:
            self.headers["Location"] = location
        if length:
            self.headers["Content-Length"] = str(len(body))
        self._body = body
        self.closed = False

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        self.closed = True


def _wait(job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if st and st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture
def env(isolated_db, monkeypatch, tmp_path):
    """Linux x86_64, no deno on PATH, nothing running; the download table is
    served by `routes` (URL -> response factory)."""
    from services import library_admin_service
    state = {"running": False, "which": None, "routes": {}, "requests": [], "timeouts": []}
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: state["running"])
    monkeypatch.setattr(svc.platform, "system", lambda: "Linux")
    monkeypatch.setattr(svc.platform, "machine", lambda: "x86_64")
    real_which = svc.shutil.which
    monkeypatch.setattr(svc.shutil, "which",
                        lambda n, *a, **k: state["which"] if n in ("deno", "winget")
                        else real_which(n, *a, **k))
    dest = tmp_path / "home" / ".deno" / "bin" / "deno"
    monkeypatch.setattr(diagnostics, "deno_default_install_path", lambda: str(dest))
    monkeypatch.setattr(diagnostics, "check_js_runtime",
                        lambda: {"found": False, "name": None, "path": None})
    import requests

    def fake_get(url, **kw):
        state["requests"].append(url)
        state["timeouts"].append(kw.get("timeout"))
        assert kw.get("allow_redirects") is False
        make = state["routes"].get(url)
        if make is None:
            return _Resp(404)
        return make()
    monkeypatch.setattr(requests, "get", fake_get)
    for jid in (svc.DENO_JOB_ID, svc.UPGRADE_CHECK_JOB_ID):
        background_jobs.clear_job(jid)
    svc._DENO_RESULT["last"] = None
    svc._UPGRADE_CHECK.update(package=None, target=None, tail=[], last=None)
    state["dest"] = dest
    yield state
    for jid in (svc.DENO_JOB_ID, svc.UPGRADE_CHECK_JOB_ID):
        background_jobs.clear_job(jid)
    gaps._UPDATES.update(checked_at=None, packages={})


def _serve_release(state, zip_bytes=None, checksum=None):
    z = _zip() if zip_bytes is None else zip_bytes
    digest = hashlib.sha256(z).hexdigest() if checksum is None else checksum
    state["routes"].update({
        LATEST: lambda: _Resp(302, location=VERSIONED),
        VERSIONED: lambda: _Resp(302, location=ASSET_HOST + "?zip"),
        ASSET_HOST + "?zip": lambda: _Resp(200, z),
        VERSIONED + ".sha256sum": lambda: _Resp(302, location=ASSET_HOST + "?sum"),
        ASSET_HOST + "?sum": lambda: _Resp(200, f"{digest}  deno.zip\n".encode()),
    })


@pytest.fixture
def client(env):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def test_deno_status_read(client, env):
    b = client.get("/api/diagnostics/deno").json()
    assert b["runtime_found"] is False and b["deno_on_path"] is False
    assert b["can_install"] is True and b["install_method"] == "download"
    assert b["job"] is None and b["last_result"] is None


def test_deno_download_installs_verified_binary(client, env):
    _serve_release(env)
    r = client.post("/api/diagnostics/deno/install", json={"confirm": True})
    assert r.status_code == 200 and r.json() == {"job_id": "deno_install", "started": True}
    st = _wait(svc.DENO_JOB_ID)
    assert st["status"] == "done", st
    assert env["dest"].read_bytes() == b"#!deno-binary"
    # Every request came from the static table or an allowlisted hop, with a timeout.
    assert env["requests"][0] == LATEST
    assert all(t == svc.REQUEST_TIMEOUT for t in env["timeouts"])
    b = client.get("/api/diagnostics/deno").json()
    last = b["last_result"]
    assert last["ok"] is True and last["needs_restart"] is True     # not on PATH yet
    assert b["deno_installed"] is True and b["job"]["status"] == "done"
    assert ABS_PATH not in str(b) and str(env["dest"]) not in str(b)


def test_deno_checksum_mismatch_installs_nothing(client, env):
    _serve_release(env, checksum="0" * 64)
    assert client.post("/api/diagnostics/deno/install", json={"confirm": True}).status_code == 200
    st = _wait(svc.DENO_JOB_ID)
    assert st["status"] == "error"
    assert not env["dest"].exists()
    last = client.get("/api/diagnostics/deno").json()["last_result"]
    assert last["ok"] is False and "checksum" in last["message"]


@pytest.mark.parametrize("hop", [
    "http://github.com/denoland/deno/releases/download/v2.9.7/deno-x86_64-unknown-linux-gnu.zip",
    "https://evil.example/deno.zip",
    "https://github.com.evil.example/x",
    "https://user:pw@github.com/x",
    "https://github.com:8443/denoland/deno/releases/download/v1/deno-x86_64-unknown-linux-gnu.zip",
])
def test_deno_refuses_unexpected_redirects(client, env, hop):
    _serve_release(env)
    env["routes"][LATEST] = lambda: _Resp(302, location=hop)
    client.post("/api/diagnostics/deno/install", json={"confirm": True})
    assert _wait(svc.DENO_JOB_ID)["status"] == "error"
    assert hop not in env["requests"]
    assert not env["dest"].exists()
    assert "evil" not in str(client.get("/api/diagnostics/deno").json())


def test_deno_redirect_loop_and_size_cap(client, env, monkeypatch):
    _serve_release(env)
    env["routes"][ASSET_HOST + "?zip"] = lambda: _Resp(302, location=ASSET_HOST + "?zip")
    client.post("/api/diagnostics/deno/install", json={"confirm": True})
    assert _wait(svc.DENO_JOB_ID)["status"] == "error"
    # the versioned URL is hop 1, then MAX_REDIRECTS more; never a 7th request
    assert env["requests"].count(ASSET_HOST + "?zip") == svc.MAX_REDIRECTS
    background_jobs.clear_job(svc.DENO_JOB_ID)
    _serve_release(env)
    monkeypatch.setattr(svc, "DENO_MAX_BYTES", 10)
    env["routes"][ASSET_HOST + "?zip"] = lambda: _Resp(200, _zip(), length=False)
    client.post("/api/diagnostics/deno/install", json={"confirm": True})
    assert _wait(svc.DENO_JOB_ID)["status"] == "error"
    assert not env["dest"].exists()


def test_deno_zip_without_binary(client, env):
    _serve_release(env, zip_bytes=_zip(member="../../evil"))
    client.post("/api/diagnostics/deno/install", json={"confirm": True})
    assert _wait(svc.DENO_JOB_ID)["status"] == "error"
    assert not env["dest"].exists()


def test_deno_refusals(client, env):
    url = "/api/diagnostics/deno/install"
    for body in ({}, {"confirm": False}, {"confirm": "yes"}, {"confirm": True, "url": "x"}):
        assert client.post(url, json=body).status_code == 422, body
    env["running"] = True
    r = client.post(url, json={"confirm": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    env["running"] = False
    env["which"] = "/usr/bin/deno"
    assert client.post(url, json={"confirm": True}).status_code == 409
    env["which"] = None
    assert env["requests"] == [] and background_jobs.get_status(svc.DENO_JOB_ID) is None


def test_deno_unsupported_platform(client, env, monkeypatch):
    monkeypatch.setattr(svc.platform, "machine", lambda: "riscv64")
    assert client.get("/api/diagnostics/deno").json()["can_install"] is False
    r = client.post("/api/diagnostics/deno/install", json={"confirm": True})
    assert r.status_code == 422


def test_deno_winget_on_windows(client, env, monkeypatch):
    monkeypatch.setattr(svc.platform, "system", lambda: "Windows")
    env["which"] = None
    monkeypatch.setattr(svc, "_use_winget", lambda: True)
    ran = []

    def fake_tree(cmd, timeout, cancel=None, **_kw):
        ran.append((cmd, timeout))
        yield {"line": f"Found Deno at {ABS_PATH} {SECRET}"}
        yield {"returncode": 0, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(svc.proc_run, "stream_tree", fake_tree)
    assert client.post("/api/diagnostics/deno/install", json={"confirm": True}).status_code == 200
    assert _wait(svc.DENO_JOB_ID)["status"] == "done"
    assert ran and ran[0][0][:5] == ["winget", "install", "-e", "--id", "DenoLand.Deno"]
    assert ran[0][1] == svc.WINGET_TIMEOUT_SECONDS
    b = client.get("/api/diagnostics/deno")
    assert SECRET not in b.text and ABS_PATH not in b.text
    assert b.json()["last_result"]["needs_restart"] is True and env["requests"] == []


def _cache_update(name="pydub", target="2.0.0"):
    gaps._UPDATES.update(checked_at=time.time(), packages={
        name: {"name": name, "dist": "pydub", "installed_version": "1.0.0",
               "status": "update", "latest": target, "target": target, "reason": ""}})


def test_upgrade_check_runs_the_cached_target(client, env, monkeypatch):
    calls = []

    def fake_check(pip_name, version=None, project_root=None, **kw):
        calls.append((pip_name, version))
        yield {"line": "Creating a throwaway environment -- your real install isn't touched."}
        yield {"line": f"Installing pydub=={version} into it... {SECRET} {ABS_PATH}"}
        yield {"line": "Running this app's test suite against pydub 2.0.0..."}
        yield {"done": True, "ok": True, "verdict": "safe", "reason": "every test passed",
               "version": version, "new_failures": [], "preexisting_failures": [],
               "conflicts": []}
    monkeypatch.setattr(diagnostics, "check_upgrade_candidate", fake_check)
    url = "/api/diagnostics/dependencies/pydub/test-upgrade"
    assert client.post(url, json={"confirm": True, "target": "2.0.0"}).status_code == 409
    _cache_update()
    assert client.post(url, json={"confirm": True, "target": "1.5.0"}).status_code == 409
    for bad in ("--index-url=x", "1.0 --pre", "", "a" * 65):
        assert client.post(url, json={"confirm": True, "target": bad}).status_code == 422, bad
    assert client.post(url, json={"target": "2.0.0"}).status_code == 422
    r = client.post(url, json={"confirm": True, "target": "2.0.0"})
    assert r.status_code == 200 and r.json()["job_id"] == "upgrade_check"
    assert _wait(svc.UPGRADE_CHECK_JOB_ID)["status"] == "done"
    assert calls == [("pydub", "2.0.0")]
    s = client.get("/api/diagnostics/upgrade-check")
    assert SECRET not in s.text and ABS_PATH not in s.text
    b = s.json()
    assert b["package"] == "pydub" and b["target"] == "2.0.0"
    assert b["result"]["verdict"] == "safe" and b["result"]["ok"] is True
    assert len(b["output_tail"]) == 3


def test_upgrade_check_refusals(client, env, monkeypatch):
    monkeypatch.setattr(diagnostics, "check_upgrade_candidate",
                        lambda *a, **k: iter(()))
    _cache_update()
    assert client.post("/api/diagnostics/dependencies/fastapi/test-upgrade",
                       json={"confirm": True, "target": "2.0.0"}).status_code == 404
    assert client.post("/api/diagnostics/dependencies/torch/test-upgrade",
                       json={"confirm": True, "target": "2.0.0"}).status_code == 422
    env["running"] = True
    r = client.post("/api/diagnostics/dependencies/pydub/test-upgrade",
                    json={"confirm": True, "target": "2.0.0"})
    assert r.status_code == 409
    assert background_jobs.get_status(svc.UPGRADE_CHECK_JOB_ID) is None


def test_upgrade_check_cancel_stops_the_run(client, env, monkeypatch):
    closed = []

    def fake_check(*a, **k):
        try:
            for i in range(10000):
                yield {"line": f"test {i}"}
                time.sleep(0.005)
        finally:
            closed.append(True)
    monkeypatch.setattr(diagnostics, "check_upgrade_candidate", fake_check)
    _cache_update()
    client.post("/api/diagnostics/dependencies/pydub/test-upgrade",
                json={"confirm": True, "target": "2.0.0"})
    background_jobs.request_cancel(svc.UPGRADE_CHECK_JOB_ID)
    assert _wait(svc.UPGRADE_CHECK_JOB_ID)["status"] == "cancelled"
    assert closed == [True]


def _h(s):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_auth_on_reads_admin_writes_pc_only(env):
    _cache_update()
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
    for path in ("/api/diagnostics/deno", "/api/diagnostics/upgrade-check"):
        assert remote.get(path).status_code == 401
        assert remote.get(path, headers=_h(kid)).status_code == 403
        assert remote.get(path, headers=_h(admin)).status_code == 200
    writes = (("/api/diagnostics/deno/install", {"confirm": True}),
              ("/api/diagnostics/dependencies/pydub/test-upgrade",
               {"confirm": True, "target": "2.0.0"}))
    for path, body in writes:
        assert remote.post(path, json=body, headers=_h(admin)).status_code == 403, path
    local = TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
    for path, body in writes:
        assert local.post(path, json=body,
                          headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403, path
    assert background_jobs.get_status(svc.DENO_JOB_ID) is None
    assert background_jobs.get_status(svc.UPGRADE_CHECK_JOB_ID) is None


def test_deno_installed_off_path_is_409_and_never_replaced(client, env):
    """Security review of #458: a Deno in ~/.deno/bin that isn't on PATH
    (needs a restart, or pinned by hand) is 'already installed'."""
    _serve_release(env)
    env["dest"].parent.mkdir(parents=True)
    env["dest"].write_bytes(b"PINNED")
    r = client.post("/api/diagnostics/deno/install", json={"confirm": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    assert env["requests"] == [] and background_jobs.get_status(svc.DENO_JOB_ID) is None
    assert env["dest"].read_bytes() == b"PINNED"


def test_deno_unpack_never_overwrites(env, tmp_path):
    """The job re-checks at write time: a binary that appeared after the
    start is left alone."""
    z = tmp_path / "d.zip"
    z.write_bytes(_zip())
    env["dest"].parent.mkdir(parents=True)
    env["dest"].write_bytes(b"PINNED")
    with pytest.raises(svc.DenoInstallFailed):
        svc._unpack_binary(str(z))
    assert env["dest"].read_bytes() == b"PINNED"


def test_upgrade_check_state_only_changes_when_the_job_starts(client, env, monkeypatch):
    """Security review of #458: a refused start must not relabel the
    running (or last) test with another package."""
    _cache_update()
    svc._UPGRADE_CHECK.update(package="jieba", target="1.0", tail=["a"],
                              last={"ok": True, "verdict": "safe"})
    monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: False)
    r = client.post("/api/diagnostics/dependencies/pydub/test-upgrade",
                    json={"confirm": True, "target": "2.0.0"})
    assert r.status_code == 409
    s = client.get("/api/diagnostics/upgrade-check").json()
    assert s["package"] == "jieba" and s["target"] == "1.0" and s["result"]["verdict"] == "safe"

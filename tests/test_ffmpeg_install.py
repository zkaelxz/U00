"""Diagnostics > "Install ffmpeg" (services/ffmpeg_install_service.py, app_tools.py).
A tiny zip and a fake HTTP response stand in for the vendor's 115 MB build:
no network and no real ffmpeg."""
import hashlib
import io
import os
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import app_tools
import background_jobs
import diagnostics
import portable
from api.api_config import ApiSettings
from api.server import create_app
from jobs import job_runner
from services import ffmpeg_install_service as svc
from tests.job_helpers import wait_for_job

TOP = "ffmpeg-9.0.2-essentials_build/"


def _zip_bytes(members=None):
    members = members if members is not None else {
        TOP + "bin/ffmpeg.exe": b"FFMPEG", TOP + "bin/ffprobe.exe": b"FFPROBE",
        TOP + "bin/ffplay.exe": b"PLAY", TOP + "LICENSE": b"GPL", TOP + "README.txt": b"x"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, body=b"", status=200, location=None):
        self.status_code, self.body = status, body
        self.headers = {"Location": location} if location else {"Content-Length": str(len(body))}

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def close(self):
        pass


@pytest.fixture
def env(isolated_db, monkeypatch, tmp_path):
    from services import library_admin_service
    data = tmp_path / "data"
    state = {"zip": _zip_bytes(), "calls": [], "routes": {}, "found": True, "free": 5000,
             "windows": True, "data": data}
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)
    monkeypatch.setattr(portable, "data_dir", lambda: str(data))
    monkeypatch.setattr(svc, "_is_windows", lambda: state["windows"])
    monkeypatch.setattr(svc, "_free_mb", lambda: state["free"])
    monkeypatch.setattr(svc, "_system_ffmpeg_found", lambda: False)
    monkeypatch.setattr(diagnostics, "check_ffmpeg",
                        lambda: {"found": state["found"], "libass": True, "path": None, "version": "x"})
    monkeypatch.setattr(svc, "FFMPEG_SIZE", len(state["zip"]))
    monkeypatch.setattr(svc, "FFMPEG_SHA256", hashlib.sha256(state["zip"]).hexdigest())

    class FakeSession:
        """Stands in for lib.http.session: runs the service's own guard on every
        hop, as the real one does, and follows redirects."""
        def __init__(self, **kw):
            assert kw["timeout"] and kw["max_bytes"] == svc.FFMPEG_SIZE
            self.guard = kw["guard"]

        def get(self, url, **kw):
            for _ in range(5):
                self.guard(url)
                state["calls"].append(url)
                reply = state["routes"].get(url)
                if isinstance(reply, Exception):
                    raise reply
                reply = reply or FakeResponse(state["zip"])
                if reply.status_code != 302:
                    return reply
                url = reply.headers["Location"]
            raise AssertionError("redirect loop")

        def close(self):
            pass

    monkeypatch.setattr(svc.http, "session", lambda **kw: FakeSession(**kw))
    monkeypatch.setattr(svc.http, "check_public", lambda url: "93.184.216.34")
    monkeypatch.setenv("PATH", "/usr/bin")
    background_jobs.clear_job(svc.FFMPEG_JOB_ID)
    svc._RESULT["last"] = None
    yield state
    job_runner.wait_for_job_threads(10, job_ids=[svc.FFMPEG_JOB_ID])
    background_jobs.clear_job(svc.FFMPEG_JOB_ID)


@pytest.fixture
def client(env):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _install(client):
    return client.post("/api/diagnostics/ffmpeg/install", json={"confirm": True})


def _run(client):
    assert _install(client).status_code == 200
    return wait_for_job(svc.FFMPEG_JOB_ID, timeout=15)["status"]


def test_install_unpacks_only_the_programs_and_the_licence(client, env):
    assert _run(client) == "done"
    folder = env["data"] / "tools" / "ffmpeg"
    assert sorted(p.name for p in folder.iterdir()) == ["LICENSE.txt", "ffmpeg.exe", "ffprobe.exe"]
    assert (folder / "ffmpeg.exe").read_bytes() == b"FFMPEG"
    assert not any(p.name.startswith("ffmpeg.partial") for p in (env["data"] / "tools").iterdir())
    assert svc._RESULT["last"]["ok"] is True
    assert str(folder) in os.environ["PATH"].split(os.pathsep)


def test_a_wrong_hash_installs_nothing(client, env, monkeypatch):
    monkeypatch.setattr(svc, "FFMPEG_SHA256", "0" * 64)
    assert _run(client) == "error"
    assert "SHA-256" in svc._RESULT["last"]["message"]
    assert list((env["data"] / "tools").iterdir()) == []
    assert len(env["calls"]) == 1   # a tampered file is not retried from another host


def test_a_wrong_size_is_refused(client, env, monkeypatch):
    monkeypatch.setattr(svc, "FFMPEG_SIZE", len(env["zip"]) - 1)
    assert _run(client) == "error"
    assert list((env["data"] / "tools").iterdir()) == []


def test_falls_back_to_the_second_host_when_the_first_is_unreachable(client, env):
    import requests
    env["routes"][svc.FFMPEG_URLS[0]] = requests.ConnectionError("down")
    assert _run(client) == "done"
    assert env["calls"] == list(svc.FFMPEG_URLS)


def test_a_redirect_off_the_allowed_hosts_is_refused(client, env):
    env["routes"][svc.FFMPEG_URLS[0]] = FakeResponse(status=302, location="https://evil.example.com/f.zip")
    env["routes"][svc.FFMPEG_URLS[1]] = FakeResponse(status=302, location="http://www.gyan.dev/f.zip")
    assert _run(client) == "error"
    assert all("evil" not in c and not c.startswith("http:") for c in env["calls"])
    assert list((env["data"] / "tools").iterdir()) == []


def test_redirects_between_allowed_hosts_are_followed(client, env):
    cdn = "https://release-assets.githubusercontent.com/x"
    env["routes"][svc.FFMPEG_URLS[0]] = FakeResponse(status=302, location=cdn)
    assert _run(client) == "done"
    assert env["calls"][:2] == [svc.FFMPEG_URLS[0], cdn]


def test_an_archive_without_ffmpeg_installs_nothing(client, env, monkeypatch):
    import hashlib as h
    body = _zip_bytes({TOP + "bin/ffplay.exe": b"PLAY", "../escape.exe": b"x"})
    env["zip"] = body
    monkeypatch.setattr(svc, "FFMPEG_SIZE", len(body))
    monkeypatch.setattr(svc, "FFMPEG_SHA256", h.sha256(body).hexdigest())
    assert _run(client) == "error"
    assert list((env["data"] / "tools").iterdir()) == []
    assert not (env["data"] / "escape.exe").exists()


def test_an_oversized_member_is_refused(client, env, monkeypatch):
    monkeypatch.setattr(svc, "MAX_MEMBER_BYTES", 3)
    assert _run(client) == "error"
    assert list((env["data"] / "tools").iterdir()) == []


def test_ffmpeg_that_will_not_start_is_removed_again(client, env):
    env["found"] = False
    assert _run(client) == "error"
    assert not app_tools.app_ffmpeg_present()


def test_unconfirmed_install_is_refused(client, env):
    assert client.post("/api/diagnostics/ffmpeg/install", json={"confirm": False}).status_code == 422
    assert env["calls"] == []


def test_not_windows_and_low_disk_are_refused_with_a_reason(client, env):
    env["windows"] = False
    assert _install(client).status_code == 422
    env["windows"], env["free"] = True, 10
    assert _install(client).status_code == 422
    assert env["calls"] == []


def test_already_installed_is_a_conflict(client, env):
    assert _run(client) == "done"
    assert _install(client).status_code == 409


def test_status_has_no_paths_or_urls(client, env):
    s = client.get("/api/diagnostics/ffmpeg").json()
    assert s["app_ffmpeg_installed"] is False and s["version"] == "9.0.2" and s["required_mb"] == 500
    assert str(env["data"]) not in str(s) and "http" not in str(s)


def test_activate_appends_so_a_system_ffmpeg_still_wins(env, monkeypatch):
    assert app_tools.activate() is False
    folder = env["data"] / "tools" / "ffmpeg"
    folder.mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        (folder / name).write_bytes(b"x")
    assert app_tools.activate() is True and app_tools.activate() is True
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts == ["/usr/bin", str(folder)]


def test_download_urls_are_https_on_allowed_hosts():
    from urllib.parse import urlsplit
    for url in svc.FFMPEG_URLS:
        p = urlsplit(url)
        assert p.scheme == "https" and p.hostname in svc.ALLOWED_HOSTS


def test_a_leftover_partial_folder_is_replaced(client, env):
    folder = env["data"] / "tools" / "ffmpeg"
    folder.mkdir(parents=True)
    (folder / "ffmpeg.exe").write_bytes(b"half")      # ffprobe.exe missing: not a complete install
    assert _run(client) == "done"
    assert (folder / "ffmpeg.exe").read_bytes() == b"FFMPEG"


def test_the_guard_refuses_other_hosts_and_plain_http():
    from lib.errors import DependencyUnavailableError
    for url in ("https://evil.example.com/f.zip", "http://www.gyan.dev/f.zip",
                "https://user:pw@www.gyan.dev/f.zip", "https://www.gyan.dev:8443/f.zip"):
        with pytest.raises(DependencyUnavailableError):
            svc._guard(url)

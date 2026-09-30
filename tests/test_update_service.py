"""
services/update_service.py: version comparison, the release check, the
verified download and the Setup hand-off. The HTTP layer (requests.get) is
faked; nothing touches the network or starts a process.
"""

import hashlib
import json
import os

import pytest
import requests

import db
from services import update_service as us
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     UnsupportedOperationError)

API_URL = "https://api.github.com/repos/zkaelxz/U00/releases/latest"
EXE_URL = "https://github.com/zkaelxz/U00/releases/download/v0.2.0/BaiheStudio-Setup-0.2.0.exe"
HASH_URL = EXE_URL + ".sha256"
CDN_URL = "https://release-assets.githubusercontent.com/x/BaiheStudio-Setup-0.2.0.exe"
PAYLOAD = b"MZ fake installer " * 100
NAME = "BaiheStudio-Setup-0.2.0.exe"


class FakeResp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self.headers = dict(headers or {})
        self._body = body
        self.closed = False

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True


class FakeHttp:
    def __init__(self):
        self.routes = {}
        self.calls = []

    def add(self, url, status=200, body=b"", headers=None):
        self.routes[url] = (status, body, headers)

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if url not in self.routes:
            raise requests.ConnectionError(f"no route to {url}")
        status, body, headers = self.routes[url]
        return FakeResp(status, body, headers)


def _release(tag="v0.2.0", prerelease=False, draft=False, size=len(PAYLOAD), with_hash=True,
             exe_url=EXE_URL, body="## What's new\n* Faster export in https://github.com/x/y/pull/1"):
    name = f"BaiheStudio-Setup-{tag.lstrip('v')}.exe"
    assets = [{"name": name, "size": size, "browser_download_url": exe_url}]
    if with_hash:
        assets.append({"name": name + ".sha256", "size": 90, "browser_download_url": HASH_URL})
    return json.dumps({"tag_name": tag, "prerelease": prerelease, "draft": draft,
                       "body": body, "assets": assets}).encode()


def _hash_file(data=PAYLOAD, name=NAME):
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


@pytest.fixture
def http(isolated_db, monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(us.requests, "get", fake.get)
    monkeypatch.setattr(us, "_state", dict(us._state, checked_at=None, check_error=None,
                                           latest=None, notes="", installer_name=None, size=None,
                                           download="idle", downloaded_bytes=0,
                                           download_error=None))
    monkeypatch.setattr(us, "_release", None)
    monkeypatch.setattr(us, "_verified", None)
    monkeypatch.setattr(us, "_is_installed", lambda: True)
    monkeypatch.setattr(us, "current_version", lambda: "0.1.0")
    monkeypatch.delenv(us.REPO_ENV, raising=False)
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_env_names", lambda names, env_path=None: None)
    return fake


def _serve_release(http, **kw):
    http.add(API_URL, body=_release(**kw))
    http.add(HASH_URL, body=_hash_file())
    http.add(EXE_URL, status=302, headers={"Location": CDN_URL})
    http.add(CDN_URL, body=PAYLOAD, headers={"Content-Length": str(len(PAYLOAD))})


# --- versions -----------------------------------------------------------------

@pytest.mark.parametrize("latest,current,newer", [
    ("0.2.0", "0.1.0", True),
    ("v1.0.0", "0.9.9", True),
    ("0.10.0", "0.9.0", True),
    ("0.1.0", "0.2.0", False),
    ("0.2.0", "0.2.0", False),
    ("0.2", "0.2.0", False),
    ("0.3.0-rc1", "0.2.0", False),     # a pre-release is never offered
    ("0.2.0", "0.2.0-rc1", True),      # the release after its own pre-release
    ("0.2.0+build5", "0.2.0", False),  # build metadata is ignored
    ("garbage", "0.1.0", False),
    ("0.2.0", None, False),
    ("0.2.0", "not-a-version", False),
])
def test_is_newer(latest, current, newer):
    assert us.is_newer(latest, current) is newer


# --- check --------------------------------------------------------------------

def test_check_reports_newer_release_without_urls_or_credentials(http):
    _serve_release(http)
    s = us.check()
    assert s["current"] == "0.1.0" and s["latest"] == "0.2.0" and s["update_available"]
    assert s["installer_name"] == NAME and s["size"] == len(PAYLOAD)
    assert "Faster export" in s["notes"] and "http" not in s["notes"]
    assert "http" not in json.dumps(s) and db.LIBRARY_DIR not in json.dumps(s)
    (url, kw), = http.calls
    assert url == API_URL
    assert kw["timeout"] and kw["allow_redirects"] is False and kw["auth"] is us._no_credentials
    assert "Authorization" not in kw["headers"]


def test_no_credentials_hook_strips_authorization():
    req = requests.Request("GET", API_URL, headers={"Authorization": "token x"}).prepare()
    assert "Authorization" not in us._no_credentials(req).headers


@pytest.mark.parametrize("tag,current", [("v0.1.0", "0.1.0"), ("v0.0.9", "0.1.0")])
def test_check_equal_or_older_is_no_update(http, monkeypatch, tag, current):
    monkeypatch.setattr(us, "current_version", lambda: current)
    http.add(API_URL, body=_release(tag=tag))
    s = us.check()
    assert s["latest"] == tag.lstrip("v") and s["update_available"] is False
    with pytest.raises(ConflictError):
        us.start_download()


@pytest.mark.parametrize("kw", [{"prerelease": True}, {"draft": True}, {"tag": "v0.3.0-rc1"}])
def test_check_ignores_prereleases_and_drafts(http, kw):
    http.add(API_URL, body=_release(**kw))
    s = us.check()
    assert s["latest"] is None and s["update_available"] is False


def test_no_release_yet_is_not_an_error(http):
    http.add(API_URL, status=404, body=b'{"message": "Not Found"}')
    s = us.check()
    assert s["latest"] is None and s["check_error"] is None and s["checked_at"]


def test_other_http_errors_are_reported(http):
    http.add(API_URL, status=403, body=b"rate limited")
    with pytest.raises(DependencyUnavailableError, match="HTTP 403"):
        us.check()
    assert us.status()["check_error"] == "GitHub answered HTTP 403."


def test_notes_are_truncated_plain_text(http):
    http.add(API_URL, body=_release(body="a\x00b\r\n" + "x" * 5000))
    notes = us.check()["notes"]
    assert "\x00" not in notes and "\r" not in notes
    assert len(notes) <= us.NOTES_MAX_CHARS + 1 and notes.endswith("…")


def test_check_redirect_to_other_host_refused(http):
    http.add(API_URL, status=302, headers={"Location": "https://evil.example.com/latest"})
    with pytest.raises(DependencyUnavailableError, match="doesn't trust"):
        us.check()
    assert [u for u, _ in http.calls] == [API_URL]


def test_check_oversized_answer_refused(http, monkeypatch):
    monkeypatch.setattr(us, "MAX_RELEASE_JSON_BYTES", 100)
    http.add(API_URL, body=_release())
    with pytest.raises(DependencyUnavailableError, match="larger than allowed"):
        us.check()


def test_network_error_message_has_no_url(http):
    with pytest.raises(DependencyUnavailableError) as exc:
        us.check()
    assert "http" not in exc.value.message and us.status()["check_error"] == "Couldn't reach GitHub."


def test_invalid_repo_setting_refused(http, monkeypatch):
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_env_names",
                        lambda names, env_path=None: "evil.example.com/../x")
    with pytest.raises(UnsupportedOperationError):
        us.check()
    assert http.calls == []


def test_repo_setting_changes_the_api_url(http, monkeypatch):
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_env_names",
                        lambda names, env_path=None: "someone/baihe-installers")
    http.add("https://api.github.com/repos/someone/baihe-installers/releases/latest",
             body=_release())
    assert us.check()["latest"] == "0.2.0"


# --- download -----------------------------------------------------------------

def test_download_verifies_and_keeps_the_installer(http):
    _serve_release(http)
    us.check()
    us.download()
    path = os.path.join(db.LIBRARY_DIR, "updates", NAME)
    with open(path, "rb") as fh:
        assert fh.read() == PAYLOAD
    assert os.listdir(os.path.join(db.LIBRARY_DIR, "updates")) == [NAME]
    s = us.status()
    assert s["verified"] is True and db.LIBRARY_DIR not in json.dumps(s)


def test_start_download_thread_reports_verified(http):
    _serve_release(http)
    us.check()
    assert us.start_download()["download"] == "downloading"
    us._download_thread.join(10)
    s = us.status()
    assert s["download"] == "verified" and s["verified"] and s["downloaded_bytes"] == len(PAYLOAD)


def _no_installer_left():
    folder = os.path.join(db.LIBRARY_DIR, "updates")
    return not os.path.isdir(folder) or os.listdir(folder) == []


def test_hash_mismatch_refuses_and_deletes(http):
    _serve_release(http)
    http.add(HASH_URL, body=_hash_file(data=b"something else"))
    us.check()
    with pytest.raises(ConflictError, match="doesn't match"):
        us.download()
    assert _no_installer_left() and us.status()["verified"] is False


def test_missing_hash_refuses_before_downloading(http):
    _serve_release(http, with_hash=False)
    us.check()
    with pytest.raises(ConflictError, match="no SHA-256"):
        us.download()
    assert [u for u, _ in http.calls] == [API_URL]
    assert _no_installer_left()


@pytest.mark.parametrize("text", [b"", b"nothex  " + NAME.encode(),
                                  _hash_file(name="Other-Setup.exe")])
def test_unusable_hash_file_refuses(http, text):
    _serve_release(http)
    http.add(HASH_URL, body=text)
    us.check()
    with pytest.raises(ConflictError, match="isn't usable"):
        us.download()
    assert _no_installer_left()


def test_failed_download_thread_reports_plain_error(http):
    _serve_release(http)
    http.add(HASH_URL, body=_hash_file(data=b"x"))
    us.check()
    us.start_download()
    us._download_thread.join(10)
    s = us.status()
    assert s["download"] == "failed" and "doesn't match" in s["download_error"]
    assert not s["verified"] and not s["can_install"]


def test_asset_on_disallowed_host_refused(http):
    _serve_release(http, exe_url="https://evil.example.com/BaiheStudio-Setup-0.2.0.exe")
    us.check()
    with pytest.raises(DependencyUnavailableError, match="doesn't trust"):
        us.download()
    assert "https://evil.example.com/BaiheStudio-Setup-0.2.0.exe" not in [u for u, _ in http.calls]


@pytest.mark.parametrize("location", ["https://evil.example.com/x.exe",
                                      "http://objects.githubusercontent.com/x.exe",
                                      "https://user:pw@objects.githubusercontent.com/x.exe"])
def test_download_redirect_to_disallowed_target_refused(http, location):
    _serve_release(http)
    http.add(EXE_URL, status=302, headers={"Location": location})
    us.check()
    with pytest.raises(DependencyUnavailableError, match="doesn't trust"):
        us.download()
    assert location not in [u for u, _ in http.calls] and _no_installer_left()


def test_too_many_redirects_refused(http):
    _serve_release(http)
    http.add(EXE_URL, status=302, headers={"Location": EXE_URL})
    us.check()
    with pytest.raises(DependencyUnavailableError, match="Too many redirects"):
        us.download()


def test_oversized_release_asset_refused_before_download(http, monkeypatch):
    monkeypatch.setattr(us, "MAX_INSTALLER_BYTES", 100)
    _serve_release(http)
    us.check()
    with pytest.raises(DependencyUnavailableError, match="larger than allowed"):
        us.download()
    assert EXE_URL not in [u for u, _ in http.calls]


def test_oversized_content_length_refused(http):
    _serve_release(http)
    http.add(CDN_URL, body=PAYLOAD, headers={"Content-Length": str(len(PAYLOAD) + 1)})
    us.check()
    with pytest.raises(DependencyUnavailableError, match="larger than allowed"):
        us.download()
    assert _no_installer_left()


def test_oversized_stream_without_length_refused(http):
    _serve_release(http)
    http.add(CDN_URL, body=PAYLOAD + b"extra")
    us.check()
    with pytest.raises(DependencyUnavailableError, match="larger than allowed"):
        us.download()
    assert _no_installer_left()


def test_short_download_refused(http):
    _serve_release(http)
    http.add(CDN_URL, body=PAYLOAD[:-10])
    us.check()
    with pytest.raises(DependencyUnavailableError, match="incomplete"):
        us.download()
    assert _no_installer_left()


def test_source_checkout_cannot_download(http, monkeypatch):
    _serve_release(http)
    us.check()
    monkeypatch.setattr(us, "_is_installed", lambda: False)
    with pytest.raises(UnsupportedOperationError, match="source checkout"):
        us.start_download()


# --- install ------------------------------------------------------------------

class FakePopen:
    calls = []

    def __init__(self, args, **kw):
        FakePopen.calls.append((args, kw))


@pytest.fixture
def verified(http, monkeypatch):
    _serve_release(http)
    us.check()
    us.download()
    FakePopen.calls = []
    monkeypatch.setattr(us.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(us.background_jobs, "active_job_ids", lambda: [])
    return os.path.join(db.LIBRARY_DIR, "updates", NAME)


def test_install_refused_off_windows(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: False)
    with pytest.raises(UnsupportedOperationError, match="only possible on Windows"):
        us.install()
    assert FakePopen.calls == []


def test_install_refused_when_not_verified(http, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    monkeypatch.setattr(us.subprocess, "Popen", FakePopen)
    FakePopen.calls = []
    with pytest.raises(ConflictError, match="Download and verify"):
        us.install()
    assert FakePopen.calls == []


def test_install_refused_for_source_checkout(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    monkeypatch.setattr(us, "_is_installed", lambda: False)
    with pytest.raises(UnsupportedOperationError):
        us.install()
    assert FakePopen.calls == []


def test_install_refused_while_jobs_run(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    monkeypatch.setattr(us.background_jobs, "active_job_ids", lambda: ["job-1"])
    with pytest.raises(ConflictError, match="running jobs"):
        us.install()
    assert FakePopen.calls == []


def test_install_refused_when_file_changed(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    with open(verified, "ab") as fh:
        fh.write(b"tampered")
    with pytest.raises(ConflictError, match="changed or is missing"):
        us.install()
    assert FakePopen.calls == [] and us.status()["verified"] is False


def test_install_starts_setup_with_fixed_args_outside_the_job(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    assert us.status()["can_install"] is True
    out = us.install()
    assert out == {"launched": True, "installer_name": NAME}
    (args, kw), = FakePopen.calls
    assert args == [verified]
    assert "shell" not in kw and kw["creationflags"] & us._CREATE_BREAKAWAY_FROM_JOB


def test_install_launch_failure_names_the_file_only(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)

    def refuse(*a, **kw):
        raise PermissionError(5, "Access is denied", verified)
    monkeypatch.setattr(us.subprocess, "Popen", refuse)
    with pytest.raises(DependencyUnavailableError) as exc:
        us.install()
    assert NAME in exc.value.message and db.LIBRARY_DIR not in exc.value.message


# --- version source, settings, daily check ---------------------------------------

def test_current_version_reads_the_install_manifest(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(us, "_APP_DIR", str(app))
    monkeypatch.setattr(us, "_is_installed", lambda: True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"product": "Baihe Studio", "app_version": "0.1.0"}))
    assert us.current_version() == "0.1.0"
    manifest.write_text(json.dumps({"product": "Other", "app_version": "9.9.9"}))
    assert us.current_version() is None
    manifest.write_text("{broken")
    assert us.current_version() is None
    monkeypatch.setattr(us, "_is_installed", lambda: False)
    assert us.current_version() is None


def test_auto_check_is_off_by_default_and_daily_when_on(http):
    _serve_release(http)
    assert us.status()["auto_check"] is False
    us.periodic_tick()
    assert http.calls == []
    us.set_auto_check(True)
    us.periodic_tick(now=lambda: 10 ** 10)
    assert len(http.calls) == 1
    us.periodic_tick(now=lambda: 10 ** 10 + 60)
    assert len(http.calls) == 1              # checked less than a day ago
    us.periodic_tick(now=lambda: 10 ** 10 + us.AUTO_CHECK_SECONDS + 1)
    assert len(http.calls) == 2
    # Never a download from the daily check.
    assert all(u == API_URL for u, _ in http.calls)

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

API_URL = "https://api.github.com/repos/zkaelxz/U00/releases?per_page=10"
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


def _release_obj(tag="v0.2.0", prerelease=False, draft=False, size=len(PAYLOAD), with_hash=True,
                 exe_url=EXE_URL, body="## What's new\n* Faster export in https://github.com/x/y/pull/1",
                 with_exe=True):
    name = f"BaiheStudio-Setup-{tag.lstrip('v')}.exe"
    assets = [{"name": name, "size": size, "browser_download_url": exe_url}] if with_exe else []
    if with_hash:
        assets.append({"name": name + ".sha256", "size": 90, "browser_download_url": HASH_URL})
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "body": body,
            "assets": assets}


def _release(**kw):
    """The releases list GitHub answers, with this one release in it."""
    return json.dumps([_release_obj(**kw)]).encode()


def _frontend_release(tag="frontend-v0.9.0"):
    return {"tag_name": tag, "prerelease": False, "draft": False, "body": "",
            "assets": [{"name": "baihe-frontend-0.9.0.zip", "size": 10,
                        "browser_download_url": "https://github.com/zkaelxz/U00/f.zip"}]}


def _hash_file(data=PAYLOAD, name=NAME):
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


@pytest.fixture
def http(isolated_db, monkeypatch, tmp_path):
    fake = FakeHttp()
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(us.tempfile, "tempdir", str(temp))
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


def test_a_404_is_reported_as_not_found_not_as_an_error(http):
    # A missing, renamed or private repository: GitHub answers 404 to an
    # unauthenticated list.
    http.add(API_URL, status=404, body=b'{"message": "Not Found"}')
    s = us.check()
    assert s["latest"] is None and s["check_error"] is None and s["checked_at"]
    assert s["release_lookup"] == "not_found"


def test_no_releases_at_all_is_no_installer_release(http):
    http.add(API_URL, body=b"[]")
    s = us.check()
    assert s["latest"] is None and s["release_lookup"] == "no_installer_release"


def test_newest_frontend_release_does_not_hide_an_older_installer_release(http):
    # The same repository publishes frontend-v* zips; GitHub's "latest" can
    # be one of those.
    http.add(API_URL, body=json.dumps([
        _frontend_release("frontend-v1.2.0"),
        _release_obj(tag="v0.3.0", with_exe=False),          # no installer asset
        _release_obj(tag="0.2.5"),                           # not a v<version> tag
        _release_obj(tag="v0.2.0"),
        _release_obj(tag="v0.1.5"),
    ]).encode())
    s = us.check()
    assert s["latest"] == "0.2.0" and s["update_available"] and s["release_lookup"] == "found"
    assert s["installer_name"] == NAME


def test_only_frontend_releases_is_no_installer_release(http):
    http.add(API_URL, body=json.dumps([_frontend_release(), _frontend_release("frontend-v0.8.0")]).encode())
    s = us.check()
    assert s["latest"] is None and s["release_lookup"] == "no_installer_release"
    assert s["check_error"] is None


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
    http.add("https://api.github.com/repos/someone/baihe-installers/releases?per_page=10",
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


def test_release_without_hash_is_never_offered(http):
    _serve_release(http, with_hash=False)
    s = us.check()
    assert s["latest"] is None and s["release_lookup"] == "no_installer_release"
    with pytest.raises(ConflictError):
        us.start_download()
    assert [u for u, _ in http.calls] == [API_URL]
    assert _no_installer_left()


def test_missing_hash_url_refuses_before_downloading(http):
    # Belt and braces: the download itself refuses without a hash too.
    _serve_release(http)
    us.check()
    us._release = dict(us._release, hash_url=None)
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


def _setup_copies():
    temp = us.tempfile.gettempdir()
    return [n for n in os.listdir(temp) if n.startswith(us.SETUP_TEMP_PREFIX)]


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
    assert _setup_copies() == []


def test_install_refused_while_downloading(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    us._state["download"] = "downloading"
    assert us.status()["can_install"] is False
    with pytest.raises(ConflictError, match="Wait for the download"):
        us.install()
    assert FakePopen.calls == []


def test_install_starts_a_private_copy_with_fixed_args_outside_the_job(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    kept = {"BAIHE_API_ALLOW_KEY_WRITES": "0", "HF_HOME": "/cache", "TORCH_HOME": "/torch",
            "HTTPS_PROXY": "http://proxy:3128", "REQUESTS_CA_BUNDLE": "/ca.pem",
            "BAIHE_API_AUTH": "on", "BAIHE_API_HOUSEHOLD_PORT": "8610", "BAIHE_DATA_DIR": "/data",
            "PATH": "/bin"}
    dropped = {"BAIHE_SHUTDOWN_TOKEN": "t" * 40, "BAIHE_PROCESS_GROUP_NAME": "Local\\x",
               "ANTHROPIC_API_KEY": "sk-ant-secret", "HF_TOKEN": "hf_x",
               "BAIHE_GOOGLE_CLIENT_SECRET": "s", "SMTP_PASSWORD": "p", "baihe_claude_key": "k"}
    for name, value in {**kept, **dropped}.items():
        monkeypatch.setenv(name, value)
    assert us.status()["can_install"] is True
    out = us.install()
    assert out == {"launched": True, "installer_name": NAME, "version": "0.2.0"}
    (args, kw), = FakePopen.calls
    (folder,) = _setup_copies()
    copy = os.path.join(us.tempfile.gettempdir(), folder, NAME)
    assert args == [copy] and kw["cwd"] == os.path.dirname(copy) and copy != verified
    with open(copy, "rb") as fh:
        assert fh.read() == PAYLOAD
    assert "shell" not in kw and kw["close_fds"] is True
    assert kw["creationflags"] & us._CREATE_BREAKAWAY_FROM_JOB
    # The user's settings reach Setup (and the server its "Start now" runs);
    # the old server's token and job name, and anything secret-named, don't.
    env = {k.upper(): v for k, v in kw["env"].items()}
    for name, value in kept.items():
        assert env[name] == value, name
    for name in dropped:
        assert name.upper() not in env, name


def test_setup_is_launched_only_inside_the_breakaway_window(verified, monkeypatch):
    import contextlib
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    seen = []

    @contextlib.contextmanager
    def window():
        seen.append("open")
        try:
            yield True
        finally:
            seen.append("closed")
    monkeypatch.setattr(us.process_guard, "breakaway_allowed", window)

    class Recording(FakePopen):
        def __init__(self, args, **kw):
            seen.append("popen")
            super().__init__(args, **kw)
    monkeypatch.setattr(us.subprocess, "Popen", Recording)
    us.install()
    assert seen == ["open", "popen", "closed"]

    def refuse(*a, **kw):
        seen.append("popen")
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(us.subprocess, "Popen", refuse)
    seen.clear()
    with pytest.raises(DependencyUnavailableError):
        us.install()
    assert seen == ["open", "popen", "closed"]


def test_install_never_stops_the_server(verified, monkeypatch):
    # Setup stops the server itself, and only once the user clicks Install
    # there; a cancelled Setup must leave Baihe running.
    from services import shutdown_service
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    for name in ("request_shutdown", "clean_shutdown", "stop_new_work", "cancel_all_jobs"):
        monkeypatch.setattr(shutdown_service, name, lambda *a, **k: pytest.fail("server stopped"))
    us.install()
    assert len(FakePopen.calls) == 1
    s = us.status()
    assert s["verified"] and s["can_install"]


def test_a_failed_setup_launch_changes_nothing(verified, monkeypatch):
    from services import shutdown_service
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    monkeypatch.setattr(shutdown_service, "request_shutdown",
                        lambda *a, **k: pytest.fail("server stopped"))
    before = us.status()

    def refuse(*a, **kw):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(us.subprocess, "Popen", refuse)
    with pytest.raises(DependencyUnavailableError):
        us.install()
    assert us.status() == before and _setup_copies() == []
    assert os.path.isfile(verified)


def test_check_finding_another_version_forgets_the_verified_download(verified, http, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    assert us.status()["verified_version"] == "0.2.0"
    us.check()                                   # same release: kept
    assert us.status()["verified_version"] == "0.2.0"
    http.add(API_URL, body=_release(tag="v0.3.0", exe_url=EXE_URL))
    s = us.check()
    assert s["latest"] == "0.3.0" and s["verified"] is False and s["verified_version"] is None
    assert s["download"] == "idle" and s["can_install"] is False
    with pytest.raises(ConflictError, match="Download and verify"):
        us.install()
    assert FakePopen.calls == []


def test_download_finished_after_a_newer_check_is_not_kept(http, monkeypatch):
    _serve_release(http)
    us.check()
    real = us._expected_hash

    def and_a_newer_release_appears(release):
        digest = real(release)
        us._release = dict(release, version="0.3.0")
        return digest
    monkeypatch.setattr(us, "_expected_hash", and_a_newer_release_appears)
    with pytest.raises(ConflictError, match="different version"):
        us.download()
    assert us.status()["verified"] is False


def test_second_download_start_is_refused(http, monkeypatch):
    _serve_release(http)
    us.check()
    monkeypatch.setattr(us.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    us.start_download()
    with pytest.raises(ConflictError, match="already running"):
        us.start_download()


def test_slow_download_hits_the_deadline_and_can_be_retried(http, monkeypatch):
    _serve_release(http)
    us.check()
    ticks = iter([0.0] + [us.DOWNLOAD_DEADLINE_SECONDS + 1.0] * 1000)
    monkeypatch.setattr(us, "_monotonic", lambda: next(ticks))
    us.start_download()
    us._download_thread.join(10)
    s = us.status()
    assert s["download"] == "failed" and "took too long" in s["download_error"]
    assert _no_installer_left()
    monkeypatch.setattr(us, "_monotonic", lambda: 0.0)
    us.start_download()
    us._download_thread.join(10)
    assert us.status()["verified"] is True


def test_custom_source_flag(http, monkeypatch):
    from services import settings_service
    assert us.status()["custom_source"] is False
    monkeypatch.setattr(settings_service, "resolve_env_names",
                        lambda names, env_path=None: "someone/baihe-installers")
    assert us.status()["custom_source"] is True
    monkeypatch.setattr(settings_service, "resolve_env_names",
                        lambda names, env_path=None: "not a repo")
    assert us.status()["custom_source"] is True


def test_cleanup_leftovers_after_an_upgrade(http, monkeypatch):
    folder = us.updates_dir()
    os.makedirs(folder)
    for name in ("BaiheStudio-Setup-0.1.0.exe", "BaiheStudio-Setup-0.2.0.exe",
                 "BaiheStudio-Setup-0.3.0.exe.part", "notes.txt"):
        open(os.path.join(folder, name), "wb").close()
    temp = us.tempfile.gettempdir()
    os.makedirs(os.path.join(temp, us.SETUP_TEMP_PREFIX + "abc"))
    os.makedirs(os.path.join(temp, "someone_else"))
    us.cleanup_leftovers()                        # current is 0.1.0
    assert sorted(os.listdir(folder)) == ["BaiheStudio-Setup-0.2.0.exe", "notes.txt"]
    assert os.listdir(temp) == ["someone_else"]
    monkeypatch.setattr(us, "current_version", lambda: "0.2.0")
    us.cleanup_leftovers()                        # upgraded: 0.2.0 is not newer any more
    assert os.listdir(folder) == ["notes.txt"]


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

"""
services/update_service.py: version comparison, the release check, the
verified download and the Setup hand-off. The HTTP layer (requests.get) is
faked; nothing touches the network or starts a process.
"""

import base64
import hashlib
import json
import threading
import os
import types

import pytest
import requests

import db
from lib import capped_body
from services import update_service as us
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     UnsupportedOperationError)

API_URL = "https://api.github.com/repos/zkaelxz/U00/releases?per_page=10"
EXE_URL = "https://github.com/zkaelxz/U00/releases/download/v0.2.0/BaiheStudio-Setup-0.2.0.exe"
HASH_URL = EXE_URL + ".sha256"
SIG_URL = EXE_URL + ".sig"
CDN_URL = "https://release-assets.githubusercontent.com/x/BaiheStudio-Setup-0.2.0.exe"
PAYLOAD = b"MZ fake installer " * 100
NAME = "BaiheStudio-Setup-0.2.0.exe"
DOWNLOAD_WAIT_SECONDS = 30


def _wait_for_download():
    # join() returns silently on timeout, so a download that outlived it would
    # fail the caller's next assertion (or a later test) with no hint why.
    thread = us._download_thread
    thread.join(DOWNLOAD_WAIT_SECONDS)
    assert not thread.is_alive(), "the download thread did not finish"


@pytest.fixture(autouse=True)
def _no_download_thread_outlives_its_test():
    # A leftover download thread writes into the module state and clears the temp
    # folder of whichever test runs next, so it must end inside its own test.
    yield
    stragglers = [t for t in threading.enumerate() if t.name == "update-download"]
    for t in stragglers:
        t.join(DOWNLOAD_WAIT_SECONDS)
    assert not any(t.is_alive() for t in stragglers), "a download thread outlived its test"


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
                 with_exe=True, with_sig=False):
    name = f"BaiheStudio-Setup-{tag.lstrip('v')}.exe"
    assets = [{"name": name, "size": size, "browser_download_url": exe_url}] if with_exe else []
    if with_hash:
        assets.append({"name": name + ".sha256", "size": 90, "browser_download_url": HASH_URL})
    if with_sig:
        assets.append({"name": name + ".sig", "size": 89, "browser_download_url": SIG_URL})
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
    monkeypatch.setattr(us, "_download_thread", None)
    monkeypatch.setattr(us, "_installing", False)
    monkeypatch.setattr(us, "_is_installed", lambda: True)
    monkeypatch.setattr(us, "current_version", lambda: "0.1.0")
    # Not the committed key file: these tests must not change when the owner adds a key.
    monkeypatch.setattr(us, "PUBLIC_KEY_PATH", str(tmp_path / "release_public_key.txt"))
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


@pytest.mark.parametrize("latest,current,newer", [
    ("0.1.0-beta.2", "0.1.0-beta.1", True),
    ("0.1.0-beta.10", "0.1.0-beta.9", True),
    ("0.1.0-beta.1", "0.1.0-beta.1", False),
    ("0.1.0-beta.1", "0.1.0-beta.2", False),   # never older than installed
    ("0.1.0-beta.1", "0.1.0", False),          # a beta is below its own release
    ("0.1.0", "0.1.0-beta.9", True),           # the release beats a same-base beta
    ("0.1.1-beta.1", "0.1.0", True),
    ("0.1.0-beta.3", "0.1.1", False),
    ("0.1.0-beta", "0.1.0-beta.1", False),
    ("0.1.0-rc.1", "0.1.0-beta.5", True),
])
def test_is_newer_with_prereleases(latest, current, newer):
    assert us.is_newer(latest, current, include_prereleases=True) is newer


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


def _beta_mode(monkeypatch, on=True, developer=True):
    monkeypatch.setattr(us, "current_version", lambda: "0.1.0-beta.1")
    if on:
        db.set_app_setting("update.include_prereleases", True)
    if developer:
        db.set_app_setting("assistant.developer_mode", True)


def _beta_list(*releases):
    return json.dumps([_release_obj(**r) for r in releases]).encode()


def test_prerelease_is_ignored_by_default(http, monkeypatch):
    _beta_mode(monkeypatch, on=False)
    http.add(API_URL, body=_release(tag="v0.1.0-beta.2", prerelease=True))
    s = us.check()
    assert s["latest"] is None and s["update_available"] is False


def test_prerelease_is_offered_when_the_option_is_on(http, monkeypatch):
    _beta_mode(monkeypatch)
    http.add(API_URL, body=_release(tag="v0.1.0-beta.2", prerelease=True))
    s = us.check()
    assert s["latest"] == "0.1.0-beta.2" and s["update_available"] is True
    assert "http" not in json.dumps(s)


def test_the_option_does_nothing_without_developer_mode(http, monkeypatch):
    _beta_mode(monkeypatch, developer=False)
    http.add(API_URL, body=_release(tag="v0.1.0-beta.2", prerelease=True))
    assert us.check()["latest"] is None


def test_prerelease_not_newer_than_installed_is_never_an_update(http, monkeypatch):
    _beta_mode(monkeypatch)
    http.add(API_URL, body=_release(tag="v0.1.0-beta.1", prerelease=True))
    assert us.check()["update_available"] is False
    with pytest.raises(ConflictError):
        us.start_download()
    monkeypatch.setattr(us, "current_version", lambda: "0.1.0-beta.5")
    http.add(API_URL, body=_release(tag="v0.1.0-beta.4", prerelease=True))
    s = us.check()
    assert s["latest"] == "0.1.0-beta.4" and s["update_available"] is False


def test_draft_is_ignored_even_with_the_option_on(http, monkeypatch):
    _beta_mode(monkeypatch)
    http.add(API_URL, body=_release(tag="v0.1.0-beta.2", prerelease=True, draft=True))
    assert us.check()["latest"] is None
    http.add(API_URL, body=_release(tag="v0.2.0", draft=True))
    assert us.check()["latest"] is None


def test_stable_release_is_preferred_over_a_same_base_beta(http, monkeypatch):
    _beta_mode(monkeypatch)
    # GitHub lists the newest first; the stable one is the newer of the two here.
    http.add(API_URL, body=_beta_list({"tag": "v0.1.0"},
                                      {"tag": "v0.1.0-beta.3", "prerelease": True}))
    s = us.check()
    assert s["latest"] == "0.1.0" and s["update_available"] is True


def test_the_highest_usable_release_is_offered_not_the_first_listed(http, monkeypatch):
    _beta_mode(monkeypatch)
    # A later-created older beta is listed first; it must not hide the newer ones.
    http.add(API_URL, body=_beta_list({"tag": "v0.1.0-beta.2", "prerelease": True},
                                      {"tag": "v0.2.0"},
                                      {"tag": "v0.1.0-beta.9", "prerelease": True}))
    assert us.check()["latest"] == "0.2.0"
    http.add(API_URL, body=_beta_list({"tag": "v0.1.0-beta.2", "prerelease": True},
                                      {"tag": "v0.1.0-beta.10", "prerelease": True},
                                      {"tag": "v0.1.0-beta.9", "prerelease": True}))
    assert us.check()["latest"] == "0.1.0-beta.10"


def test_a_newer_release_that_is_not_usable_does_not_win(http, monkeypatch):
    _beta_mode(monkeypatch)
    http.add(API_URL, body=_beta_list({"tag": "v0.9.0", "draft": True},
                                      {"tag": "v0.8.0", "with_hash": False},
                                      {"tag": "v0.2.0"}))
    assert us.check()["latest"] == "0.2.0"
    db.set_app_setting("update.include_prereleases", False)
    http.add(API_URL, body=_beta_list({"tag": "v0.3.0-beta.1", "prerelease": True},
                                      {"tag": "v0.2.0"}))
    assert us.check()["latest"] == "0.2.0"


def test_install_refuses_a_verified_beta_once_the_option_is_off(verified, http, monkeypatch):
    monkeypatch.setattr(us.sys, "platform", "win32")
    monkeypatch.setattr(us, "current_version", lambda: "0.1.0-beta.1")
    db.set_app_setting("update.include_prereleases", True)
    db.set_app_setting("assistant.developer_mode", True)
    name = "BaiheStudio-Setup-0.1.0-beta.2.exe"
    http.add(API_URL, body=_release(tag="v0.1.0-beta.2", prerelease=True))
    http.add(HASH_URL, body=_hash_file(name=name))
    us.check()
    us.download()
    db.set_app_setting("update.include_prereleases", False)
    with pytest.raises(ConflictError):
        us.install()
    assert FakePopen.calls == []


def test_turning_the_option_off_stops_an_offered_beta_from_downloading(http, monkeypatch):
    _beta_mode(monkeypatch)
    http.add(API_URL, body=_release(tag="v0.1.0-beta.2", prerelease=True))
    us.check()
    db.set_app_setting("update.include_prereleases", False)
    assert us.status()["update_available"] is False
    with pytest.raises(ConflictError):
        us.start_download()


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


@pytest.mark.parametrize("status", [403, 429])
def test_github_rate_limit_says_to_wait(http, status):
    http.add(API_URL, status=status, body=b"rate limited", headers={"X-RateLimit-Remaining": "0"})
    with pytest.raises(DependencyUnavailableError, match="hourly limit"):
        us.check()
    assert us.status()["check_error"].startswith("GitHub's hourly limit")


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
    _wait_for_download()
    s = us.status()
    assert s["download"] == "verified" and s["verified"] and s["downloaded_bytes"] == len(PAYLOAD)


def test_start_download_reports_downloading_even_if_the_thread_wins_the_race(http, monkeypatch):
    _serve_release(http)
    us.check()
    # A thread left by an earlier test would hide whether this call's own thread exists yet.
    monkeypatch.setattr(us, "_download_thread", None)
    reads = []
    thread_started_at_snapshot = []

    def slow_for_the_caller():
        # The first read is start_download's own; the second is the status() snapshot.
        reads.append(1)
        if len(reads) == 2:
            thread_started_at_snapshot.append(us._download_thread is not None)
        return "0.1.0"
    monkeypatch.setattr(us, "current_version", slow_for_the_caller)
    assert us.start_download()["download"] == "downloading"
    # The snapshot is taken before the thread exists, so a fast download can't make
    # the call that started it report "verified".
    assert thread_started_at_snapshot == [False]
    _wait_for_download()


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
    _wait_for_download()
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
        # A lower-case twin from the machine running the tests (https_proxy on
        # Linux) would fold onto the same name below and could win.
        for twin in {name.lower(), name.upper()} - {name}:
            monkeypatch.delenv(twin, raising=False)
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
    # Only update_service sees the fake: patching threading.Thread itself would also
    # replace the thread class for pytest-xdist and every other thread in the process.
    inert = type("T", (), {"start": lambda self: None})
    monkeypatch.setattr(us, "threading", types.SimpleNamespace(Thread=lambda **kw: inert()))
    us.start_download()
    with pytest.raises(ConflictError, match="already running"):
        us.start_download()


def test_no_download_or_second_install_starts_while_setup_is_being_checked(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    refused = []
    real_copy = us.shutil.copyfile

    def copy_while_clicking(src, dst):
        for click in (us.start_download, us.install):
            with pytest.raises(ConflictError, match="already starting") as exc:
                click()
            refused.append(str(exc.value))
        return real_copy(src, dst)
    monkeypatch.setattr(us.shutil, "copyfile", copy_while_clicking)
    assert us.install()["launched"] is True
    assert len(refused) == 2 and len(FakePopen.calls) == 1
    assert us.status()["download"] == "verified" and os.path.isfile(verified)
    # Once Setup has started, the guard is down again.
    monkeypatch.setattr(us.shutil, "copyfile", real_copy)
    assert us.install()["launched"] is True


def test_a_failed_install_leaves_a_download_that_replaced_its_own(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    newer = dict(us._verified, sha256="0" * 64)

    def replaced_then_missing(src, dst):
        us._verified = newer
        raise FileNotFoundError(src)
    monkeypatch.setattr(us.shutil, "copyfile", replaced_then_missing)
    with pytest.raises(ConflictError, match="changed or is missing"):
        us.install()
    assert us._verified is newer
    assert us.status()["download"] == "verified" and us.status()["download_error"] is None


def test_a_failed_install_never_marks_a_running_download_failed(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)

    def download_started_then_missing(src, dst):
        us._state["download"] = "downloading"
        raise FileNotFoundError(src)
    monkeypatch.setattr(us.shutil, "copyfile", download_started_then_missing)
    with pytest.raises(ConflictError, match="changed or is missing"):
        us.install()
    # "failed" here would let the next click start a second download writing the same file.
    assert us.status()["download"] == "downloading"


def test_slow_download_hits_the_deadline_and_can_be_retried(http, monkeypatch):
    _serve_release(http)
    us.check()
    ticks = iter([0.0] + [us.DOWNLOAD_DEADLINE_SECONDS + 1.0] * 1000)
    monkeypatch.setattr(us, "_monotonic", lambda: next(ticks))
    us.start_download()
    _wait_for_download()
    s = us.status()
    assert s["download"] == "failed" and "took too long" in s["download_error"]
    assert _no_installer_left()
    monkeypatch.setattr(us, "_monotonic", lambda: 0.0)
    us.start_download()
    _wait_for_download()
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


def test_small_body_read_is_closed_and_cut_off_by_the_deadline(monkeypatch):
    ticks = iter([0.0, 1.0, us.READ_DEADLINE_SECONDS + 1])
    monkeypatch.setattr(capped_body.time, "monotonic", lambda: next(ticks))
    resp = FakeResp(body=b"x" * (3 * us.CHUNK))
    with pytest.raises(DependencyUnavailableError, match="didn't answer in time"):
        us._read_capped(resp, 10 * us.CHUNK)
    assert resp.closed


def test_small_body_read_over_cap_is_closed():
    resp = FakeResp(body=b"x" * 50)
    with pytest.raises(DependencyUnavailableError, match="larger than allowed"):
        us._read_capped(resp, 10)
    assert resp.closed


# --- release signature ------------------------------------------------------------

rs = pytest.importorskip("lib.release_signature")
pytest.importorskip("cryptography")


def _keypair():
    private, public = rs.generate()
    return rs.parse_private_key(private), public


def _sig_file(seed, data=PAYLOAD, name=NAME):
    return rs.format_signature(rs.sign(seed, name, hashlib.sha256(data).hexdigest())).encode()


@pytest.fixture
def signed(http):
    """This copy has a release key; the release is signed with it."""
    seed, public = _keypair()
    with open(us.PUBLIC_KEY_PATH, "w", encoding="utf-8") as fh:
        fh.write("# release key\n" + public + "\n")
    _serve_release(http, with_sig=True)
    http.add(SIG_URL, body=_sig_file(seed))
    return types.SimpleNamespace(http=http, seed=seed, public=public)


def _updates_files():
    folder = us.updates_dir()
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def _refused(match, error=ConflictError):
    us.check()
    with pytest.raises(error, match=match) as info:
        us.download()
    message = us._plain_error(info.value)
    from translate_engines import redact_secrets
    assert message == redact_secrets(message)
    assert "http" not in message and "/" not in message and "\\" not in message
    assert us.status()["verified"] is False
    assert _updates_files() == []   # nothing kept: no installer, no partial download
    return message


def test_signed_release_verifies(signed):
    us.check()
    us.download()
    st = us.status()
    assert st["verified"] and st["signature_check"] and st["signature_verified"]
    assert _updates_files() == [NAME]


def test_tampered_installer_with_a_matching_hash_file_is_refused(signed):
    # A tampered release can carry a .sha256 that matches; only the signature catches it.
    bad = PAYLOAD.replace(b"fake", b"evil")
    signed.http.add(HASH_URL, body=_hash_file(bad))
    signed.http.add(CDN_URL, body=bad, headers={"Content-Length": str(len(bad))})
    _refused("signature doesn't match")


@pytest.mark.parametrize("sig", [
    "valid-for-other-bytes", "valid-for-other-version", "garbage", "too-short", "empty", "binary"])
def test_tampered_or_swapped_signature_is_refused(signed, sig):
    body = {"valid-for-other-bytes": lambda: _sig_file(signed.seed, data=b"other"),
            "valid-for-other-version": lambda: _sig_file(
                signed.seed, name="BaiheStudio-Setup-0.1.0.exe"),
            "garbage": lambda: b"not base64 at all!!",
            "too-short": lambda: base64.b64encode(b"x" * 63),
            "empty": lambda: b"",
            "binary": lambda: b"\xff" * 88}[sig]()
    signed.http.add(SIG_URL, body=body)
    _refused("signature")


def test_signature_from_another_key_is_refused(signed):
    other_seed, _ = _keypair()
    signed.http.add(SIG_URL, body=_sig_file(other_seed))
    _refused("signature doesn't match")


def test_missing_signature_is_refused_once_a_key_is_present(signed):
    # A release stripped of its .sig is a downgrade to SHA-256 only: refused,
    # and the installer is never fetched.
    signed.http.add(API_URL, body=_release(with_sig=False))
    _refused("isn't signed")
    assert not any(url in (EXE_URL, CDN_URL) for url, _ in signed.http.calls)


def test_oversized_signature_is_refused(signed):
    signed.http.add(SIG_URL, body=b"A" * (us.MAX_SIGNATURE_FILE_BYTES + 1))
    _refused("larger than allowed", DependencyUnavailableError)


@pytest.mark.parametrize("key_text", [
    "ed25519-public:not-base64", "ed25519-public:" + "A" * 10,
    "ed25519-private:" + "A" * 43 + "=", "something else",
    "ed25519-public:" + "A" * 43 + "=\ned25519-public:" + "B" * 43 + "="])
def test_a_damaged_key_file_refuses_updates_instead_of_skipping_the_check(signed, key_text):
    with open(us.PUBLIC_KEY_PATH, "w", encoding="utf-8") as fh:
        fh.write(key_text)
    assert us.status()["signature_check"] is True
    message = _refused("release key file")
    assert "ed25519" not in message


def test_no_key_file_keeps_sha256_only_and_says_so(http):
    assert not os.path.exists(us.PUBLIC_KEY_PATH)
    _serve_release(http)    # no .sig asset at all
    us.check()
    us.download()
    st = us.status()
    assert st["verified"] and st["signature_check"] is False and st["signature_verified"] is False
    assert not any(url == SIG_URL for url, _ in http.calls)


def test_a_key_file_with_only_comments_counts_as_no_key(http):
    with open(us.PUBLIC_KEY_PATH, "w", encoding="utf-8") as fh:
        fh.write("# no key yet\n\n")
    _serve_release(http)
    us.check()
    us.download()
    assert us.status()["signature_check"] is False and us.status()["verified"]


def test_missing_cryptography_fails_closed_once_a_key_is_present(signed, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_crypto(name, *a, **kw):
        if name.startswith("cryptography"):
            raise ImportError(name)
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_crypto)
    us.check()
    with pytest.raises(DependencyUnavailableError, match="cryptography"):
        us.download()
    assert _updates_files() == []


@pytest.fixture
def signed_verified(signed, monkeypatch):
    us.check()
    us.download()
    FakePopen.calls = []
    monkeypatch.setattr(us.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(us.background_jobs, "active_job_ids", lambda: [])
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    return signed


def test_install_checks_the_signature_of_the_copy_it_starts(signed_verified, monkeypatch):
    checked = []
    real = rs.verify

    def spy(public, signature, name, sha):
        checked.append(sha)
        return real(public, signature, name, sha)
    monkeypatch.setattr(us.release_signature, "verify", spy)
    us.install()
    (args, _), = FakePopen.calls
    assert checked == [us._file_sha256(args[0])]


def test_install_refuses_when_the_verified_signature_no_longer_matches(signed_verified):
    # The recorded signature is swapped after download: the check before Setup catches it.
    other_seed, _ = _keypair()
    us._verified["signature"] = rs.sign(other_seed, NAME, hashlib.sha256(PAYLOAD).hexdigest())
    with pytest.raises(ConflictError, match="isn't signed with this copy's release key"):
        us.install()
    assert FakePopen.calls == [] and _setup_copies() == [] and us.status()["verified"] is False


def test_install_refuses_an_unsigned_download_once_a_key_appears(http, monkeypatch):
    # Downloaded while this copy had no key; a key is now present: fail closed.
    _serve_release(http)
    us.check()
    us.download()
    seed, public = _keypair()
    with open(us.PUBLIC_KEY_PATH, "w", encoding="utf-8") as fh:
        fh.write(public)
    FakePopen.calls = []
    monkeypatch.setattr(us.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(us.background_jobs, "active_job_ids", lambda: [])
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    with pytest.raises(ConflictError, match="isn't signed"):
        us.install()
    assert FakePopen.calls == []


def test_install_refuses_a_swapped_file_even_when_signed(signed_verified):
    with open(us._verified["path"], "r+b") as fh:
        fh.write(b"XX")
    with pytest.raises(ConflictError, match="changed or is missing"):
        us.install()
    assert FakePopen.calls == []


def test_a_full_disk_during_the_download_says_so(http, monkeypatch):
    import errno
    _serve_release(http)
    us.check()
    real_open = open

    def full_disk_open(path, mode="r", *a, **kw):
        if str(path).endswith(".part"):
            raise OSError(errno.ENOSPC, "No space left on device", path)
        return real_open(path, mode, *a, **kw)
    monkeypatch.setattr(us, "open", full_disk_open, raising=False)
    us.start_download()
    _wait_for_download()
    assert us.status()["download_error"] == "The disk is full."


def test_a_full_disk_while_copying_setup_keeps_the_download(verified, monkeypatch):
    import errno
    monkeypatch.setattr(us, "_is_windows", lambda: True)

    def full(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device", dst)
    monkeypatch.setattr(us.shutil, "copyfile", full)
    with pytest.raises(DependencyUnavailableError, match="^The disk is full.$"):
        us.install()
    s = us.status()
    # Not "changed or is missing, download it again": the download is fine.
    assert s["verified"] is True and s["download"] == "verified" and s["download_error"] is None
    assert FakePopen.calls == [] and _setup_copies() == []


def test_a_setup_copy_held_by_another_program_says_so_and_keeps_the_download(verified, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)

    def held(src, dst):
        exc = PermissionError(13, "The process cannot access the file")
        exc.winerror = 32
        raise exc
    monkeypatch.setattr(us.shutil, "copyfile", held)
    with pytest.raises(DependencyUnavailableError, match="open in another program"):
        us.install()
    assert us.status()["verified"] is True

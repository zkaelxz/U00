"""
services/update_service.py -- check for, download and hand off to a newer
Windows installer published on the project's public GitHub Releases.

- Check: one unauthenticated GET of the repository's newest releases (the
  first page). The highest version among those that are a `v<version>` release (not a draft;
  not a pre-release unless Developer Mode's "include pre-releases" is on)
  with its BaiheStudio-Setup-<version>.exe and `.sha256` is offered: the same repository also publishes other releases (`frontend-v*`)
  that must not hide the installer. The repository is a setting,
  BAIHE_UPDATE_REPO in .env (default zkaelxz/U00), so installers can move to
  a separate public repository without a code change.
- Download: only on the user's click. The installer goes to
  `<library>/updates/`; it is kept only if its SHA-256 matches the release's
  `.sha256` (which catches a broken or truncated download, not a tampered
  release) and, when this copy has a release public key, its `.sig` from the
  same release verifies with that key (lib/release_signature.py: Ed25519 over
  the installer's name and SHA-256). The key is read from this copy's own
  folder (installer/release_public_key.txt), never fetched. Once a key is
  there, an unsigned, badly signed or wrongly signed release is refused; with
  no key line, updates are SHA-256 checked only and the status says so. The
  installer is not code-signed.
- Install: only on the user's click, only on Windows and only for an
  installed copy. The verified file is copied into a new private temp
  folder, the copy is hashed and its signature checked again, and that copy
  is started with a fixed argument list (no shell, no silent flags) and the server's environment
  minus the shutdown token, the job name and anything named like a secret
  (so "Start Baihe Studio now" after Setup keeps the user's settings), outside
  the server's kill-on-close Job Object so it outlives the server: the job
  allows breakaway only for the moment of this launch
  (process_guard.breakaway_allowed). The server is never stopped from here: when
  the user clicks Install in Setup, Setup's own upgrade step
  (`launcher.py --stop`) stops it cleanly, cancelling running jobs; a
  cancelled Setup leaves it running. The temp copy and installers that are
  no longer newer are removed at the next start and the next download.
- Private releases: nothing here authenticates. If the repository (or its
  releases) stops being public, GitHub answers the unauthenticated list with
  a 404, reported as `release_lookup="not_found"`; installers then have to be
  downloaded and checked by hand (docs/runbook.md).

Every request is https to an allowlisted host, redirects are followed by
hand and each hop is checked, and no credentials are sent (not even from
~/.netrc). Status holds names, numbers and booleans only: never a URL or a
filesystem path.
"""

import contextlib
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests

import background_jobs
import db
import env_reader
import portable
import process_guard
from lib import capped_body, desktop_session, release_signature
from lib import storage_errors
from lib.errors import (ConflictError, DependencyUnavailableError, ServiceError,
                                     UnsupportedOperationError)

REPO_ENV = "BAIHE_UPDATE_REPO"
DEFAULT_REPO = "zkaelxz/U00"
AUTO_CHECK_KEY = "update.auto_check"
AUTO_SETUP_TRIED_KEY = "update.auto_setup_tried"
LAST_CHECK_KEY = "update.last_check_at"
CHECK_FAILED_KEY = "update.last_check_failed"
FIRST_VERIFIED_INSTALLER_KEY = "update.installer_first_verified"
INCLUDE_PRERELEASES_KEY = "update.include_prereleases"
AUTO_CHECK_SECONDS = 24 * 3600
# A failed daily check (offline at that minute, GitHub's rate limit) is tried again
# this soon, not a day later.
CHECK_RETRY_SECONDS = 3600

API_HOSTS = frozenset({"api.github.com"})
DOWNLOAD_HOSTS = frozenset({"github.com", "objects.githubusercontent.com",
                            "release-assets.githubusercontent.com"})
TIMEOUT = (10, 30)              # connect, read (between chunks)
MAX_REDIRECTS = 5
MAX_RELEASE_JSON_BYTES = 2_000_000
RELEASES_PER_PAGE = 10
MAX_HASH_FILE_BYTES = 4096
MAX_SIGNATURE_FILE_BYTES = 1024
MAX_INSTALLER_BYTES = 600 * 1024 * 1024
NOTES_MAX_CHARS = 2000
CHUNK = 64 * 1024
# The whole installer download, however slowly the bytes trickle in; the
# per-read timeout alone would let a slow connection hold it for hours.
DOWNLOAD_DEADLINE_SECONDS = 30 * 60
# A flat 30 minutes fails a 600 MB installer on anything under ~340 KB/s even
# though the connection is alive, so the deadline grows with the size at this
# slowest accepted rate.
MIN_DOWNLOAD_BYTES_PER_SECOND = 100 * 1024
READ_DEADLINE_SECONDS = 60  # a small JSON/hash body, not the installer
# baihe_* in %TEMP% is also what a clean uninstall removes.
SETUP_TEMP_PREFIX = "baihe_setup_"
# Setup inherits the server's environment (so the server "Start Baihe Studio
# now" starts gets the same proxy, cache, port, auth and key-write settings)
# minus these: the old server's one-time shutdown token and job name (the
# launcher makes new ones), and anything named like a secret. Upper case:
# Windows environment names are case-insensitive.
_SETUP_ENV_DROP = frozenset({"BAIHE_SHUTDOWN_TOKEN", "BAIHE_PROCESS_GROUP_NAME"})
_SECRET_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")

_REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
_VERSION_RE = re.compile(r"^v?(\d{1,6})\.(\d{1,6})(?:\.(\d{1,6}))?(-[0-9A-Za-z.-]{1,40})?"
                         r"(\+[0-9A-Za-z.-]{1,40})?$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_URL_RE = re.compile(r"https?://\S+", re.I)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Setup (Windows) starts on its own, not inside the server's Job Object:
# Setup stops the server, and a Setup inside the job would be ended with it.
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
PUBLIC_KEY_PATH = os.path.join(_APP_DIR, "installer", "release_public_key.txt")

_lock = threading.Lock()
# lookup: "unchecked" | "found" | "no_installer_release" | "not_found" (404).
_state = {"checked_at": None, "check_error": None, "lookup": "unchecked",
          "latest": None, "notes": "",
          "installer_name": None, "size": None, "download": "idle",
          "downloaded_bytes": 0, "download_error": None}
_release = None     # internal: {"version", "exe_name", "exe_url", "exe_size", "hash_url", "sig_url"}
_verified = None    # internal: {"version", "name", "path", "sha256", "signature"}
_download_thread = None
_installing = False  # install() is copying and checking: a download now would delete its file
_setup = None  # the Setup process install() started, while it may still be open
_monotonic = time.monotonic


# --- versions -----------------------------------------------------------------

def _pre_key(pre):
    """Sort key for the part after `-`, by SemVer: numeric identifiers sort
    below alphanumeric ones and a shorter list below a longer one with the
    same start (beta.2 < beta.10, beta < beta.1)."""
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part)
                 for part in pre.split("."))


def parse_version(text):
    """(major, minor, patch, is_release, pre_key) or None. Build metadata
    (+...) is ignored. Tuples compare in version order: a pre-release (-beta.2)
    sorts before its own release and after the previous one."""
    m = _VERSION_RE.match(str(text or "").strip())
    if not m:
        return None
    pre = m.group(4)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), pre is None,
            _pre_key(pre[1:]) if pre else ())


def is_newer(latest, current, include_prereleases=False) -> bool:
    """True when `latest` sorts after `current`. A pre-release counts only
    with `include_prereleases`. Anything unparsable is never newer."""
    lv, cv = parse_version(latest), parse_version(current)
    return bool(lv and cv and (lv[3] or include_prereleases) and lv > cv)


def _is_installed() -> bool:
    return portable.is_installed()


def _is_windows() -> bool:
    return sys.platform == "win32"


def current_version():
    """The installed copy's version, from `<install dir>/manifest.json`
    (written by installer/build_installer.py); None for a source checkout
    or a manifest that isn't Baihe Studio's."""
    if not _is_installed():
        return None
    path = os.path.join(os.path.dirname(_APP_DIR), "manifest.json")
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.loads(fh.read(MAX_RELEASE_JSON_BYTES))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("product") != "Baihe Studio":
        return None
    version = manifest.get("app_version")
    return version if isinstance(version, str) and parse_version(version) else None


# Written into the app folder by installer/build_installer.write_release_stamp,
# so a full install and an app-only update (whose zip is that same folder)
# both carry it.
RELEASE_STAMP_NAME = "release.json"
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_commit_date_cache = {}


def _release_date(version):
    """The stamp's date (YYYY-MM-DD) when it names `version`, else None: a copy
    built before stamps existed has none, and a stamp for another version
    would put the wrong date beside this one."""
    try:
        with open(os.path.join(_APP_DIR, RELEASE_STAMP_NAME), encoding="utf-8") as fh:
            stamp = json.loads(fh.read(MAX_RELEASE_JSON_BYTES))
    except (OSError, ValueError):
        return None
    if not isinstance(stamp, dict) or stamp.get("version") != version:
        return None
    released = stamp.get("released")
    return released if isinstance(released, str) and _DATE_RE.fullmatch(released) else None


def _commit_date():
    """A source checkout's commit date (YYYY-MM-DD), or None without git. Read
    once: pulling a newer commit always restarts the server."""
    if "date" not in _commit_date_cache:
        # Imported here: source_update_service imports this module.
        from services import source_update_service
        code, out = source_update_service._git("log", "-1", "--format=%cs")
        _commit_date_cache["date"] = out if code == 0 and _DATE_RE.fullmatch(out) else None
    return _commit_date_cache["date"]


def version_info() -> dict:
    """The version line every device shows: an installed copy's version and
    release date, or a source copy's version and commit date. Version text
    and dates only."""
    if _is_installed():
        version = current_version()
        return {"version": version, "released": _release_date(version) if version else None,
                "source_copy": False, "commit_date": None}
    return {"version": portable.SOURCE_VERSION, "released": None, "source_copy": True,
            "commit_date": _commit_date()}


# --- settings -----------------------------------------------------------------

def releases_repo() -> str:
    repo = (env_reader.resolve_env_names((REPO_ENV,)) or DEFAULT_REPO).strip()
    if not _REPO_RE.match(repo) or ".." in repo:
        raise UnsupportedOperationError(f"{REPO_ENV} must look like owner/repository.")
    return repo


def custom_source() -> bool:
    """True when BAIHE_UPDATE_REPO names anything but the default repository
    (an invalid value counts: it is not the default)."""
    try:
        return releases_repo() != DEFAULT_REPO
    except UnsupportedOperationError:
        return True


def include_prereleases() -> bool:
    """Whether the App updates card's "Include pre-releases" switch is on."""
    from services import settings_service
    try:
        return settings_service.get(INCLUDE_PRERELEASES_KEY) is True
    except Exception:
        return False


def get_auto_check() -> bool:
    try:
        return db.get_app_setting(AUTO_CHECK_KEY, True) is True
    except Exception:
        return False


def set_auto_check(enabled: bool) -> dict:
    db.set_app_setting(AUTO_CHECK_KEY, bool(enabled))
    return status()


def set_include_prereleases(enabled: bool) -> dict:
    db.set_app_setting(INCLUDE_PRERELEASES_KEY, bool(enabled))
    return status()


# --- HTTP ---------------------------------------------------------------------

def _no_credentials(request):
    """Passed as `auth=` so requests never adds ~/.netrc credentials."""
    request.headers.pop("Authorization", None)
    return request


def _check_url(url, hosts):
    parts = urlsplit(url)
    if (parts.scheme != "https" or (parts.hostname or "").lower() not in hosts
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise DependencyUnavailableError("The update came from an address Baihe doesn't trust, "
                                         "so it was refused.")


def _open(url, hosts, accept):
    """GET `url`, following up to MAX_REDIRECTS redirects by hand; every hop
    must be https on one of `hosts`. Returns the streaming 200 response."""
    for _ in range(MAX_REDIRECTS + 1):
        _check_url(url, hosts)
        resp = requests.get(url, headers={"Accept": accept, "User-Agent": "BaiheStudio-updater"},
                            auth=_no_credentials, allow_redirects=False, stream=True,
                            timeout=TIMEOUT)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location") or ""
            resp.close()
            url = urljoin(url, location)
            continue
        if resp.status_code != 200:
            resp.close()
            # Unsigned API calls share 60 an hour per address; a bare "HTTP 403"
            # reads like a broken repository instead of "wait a while".
            if resp.status_code in (403, 429) and \
                    str(resp.headers.get("X-RateLimit-Remaining", "")).strip() == "0":
                message = ("GitHub's hourly limit on checks from this network was reached. "
                           "Try again later.")
            else:
                message = f"GitHub answered HTTP {resp.status_code}."
            raise DependencyUnavailableError(message, details={"http_status": resp.status_code})
        return resp
    raise DependencyUnavailableError("Too many redirects from GitHub.")


def _too_big():
    return DependencyUnavailableError("The download is larger than allowed, so it was refused.")


def _declared_length(resp):
    return capped_body.declared_length(resp)


def _too_slow():
    return DependencyUnavailableError("GitHub didn't answer in time.")


def _read_capped(resp, cap) -> bytes:
    return capped_body.read_capped(resp, cap, READ_DEADLINE_SECONDS, _too_big, chunk_size=CHUNK,
                                   make_deadline_error=_too_slow)


def read_small(url, cap, accept="application/octet-stream") -> bytes:
    """A small GitHub API answer or text asset, for callers outside this module
    that need the same host allowlist, redirect checks and size cap. The API's JSON
    endpoints answer HTTP 415 to the default asset Accept, so they pass their own."""
    return _read_capped(_open(url, API_HOSTS | DOWNLOAD_HOSTS, accept), cap)


def copy_problem(exc):
    """The fixed sentence for a full disk or a file another program (often an
    antivirus scan) holds, else None. storage_errors' other sentences name
    the library folder or the app log, which don't fit these files."""
    problem = storage_errors.classify(exc)
    if problem is not None and problem in (storage_errors.DISK_FULL, storage_errors.FILE_IN_USE):
        return problem.message
    return None


def _plain_error(exc) -> str:
    """User-facing text for a failure: our own messages as they are, and a
    generic line for network errors (whose text carries the URL)."""
    from engine_backends.shared import redact_secrets
    if isinstance(exc, ServiceError):
        return redact_secrets(exc.message)
    problem = copy_problem(exc)
    if problem is not None:
        return problem
    if isinstance(exc, requests.Timeout):
        return "GitHub didn't answer in time."
    if isinstance(exc, requests.RequestException):
        return "Couldn't reach GitHub."
    return "The update step failed."


# --- check --------------------------------------------------------------------

def _plain_notes(text) -> str:
    text = _URL_RE.sub("", str(text or ""))
    text = _CONTROL_RE.sub("", text.replace("\r\n", "\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= NOTES_MAX_CHARS else text[:NOTES_MAX_CHARS].rstrip() + "…"


def _parse_release(data, allow_prereleases=False):
    """The internal release record, or None when this release offers nothing
    usable (a draft, a pre-release unless allowed, a tag that isn't
    v<version>, no installer or no `.sha256`)."""
    if not isinstance(data, dict) or data.get("draft"):
        return None
    tag = data.get("tag_name")
    parsed = parse_version(tag)
    if not str(tag).startswith("v") or not parsed:
        return None
    if not allow_prereleases and (data.get("prerelease") or not parsed[3]):
        return None
    version = str(tag).strip().lstrip("v")
    exe_name = f"BaiheStudio-Setup-{version}.exe"
    assets = {a.get("name"): a for a in data.get("assets") or [] if isinstance(a, dict)}
    exe, sha = assets.get(exe_name), assets.get(exe_name + ".sha256")
    sig = assets.get(exe_name + ".sig")
    if not exe or not sha or not isinstance(exe.get("browser_download_url"), str) \
            or not isinstance(sha.get("browser_download_url"), str):
        return None
    size = exe.get("size")
    return {"version": version, "exe_name": exe_name, "exe_url": exe["browser_download_url"],
            "app": _parse_app_assets(assets, version),
            "exe_size": size if isinstance(size, int) and size > 0 else None,
            "hash_url": sha["browser_download_url"],
            # Missing is not refused here: whether a signature is needed is
            # this copy's decision (its key), taken at download.
            "sig_url": sig["browser_download_url"]
            if sig and isinstance(sig.get("browser_download_url"), str) else None,
            "notes": _plain_notes(data.get("body"))}


def _parse_app_assets(assets, version):
    """The app-only update's zip, `.sha256`, `.sig` and manifest copy
    (installer/build_app_zip.py), or None when the release has no usable
    zip: then only the full installer is offered."""
    name = f"BaiheStudio-App-{version}.zip"
    found = {key: assets.get(asset_name) for key, asset_name in (
        ("zip", name), ("hash", name + ".sha256"), ("sig", name + ".sig"),
        ("manifest", f"BaiheStudio-App-{version}.json"),
        ("manifest_sig", f"BaiheStudio-App-{version}.json.sig"))}
    urls = {key: a["browser_download_url"] for key, a in found.items()
            if a and isinstance(a.get("browser_download_url"), str)}
    if "zip" not in urls or "hash" not in urls or "manifest" not in urls:
        return None
    size = found["zip"].get("size")
    return {"name": name, "url": urls["zip"], "hash_url": urls["hash"],
            "sig_url": urls.get("sig"), "manifest_url": urls["manifest"],
            "manifest_sig_url": urls.get("manifest_sig"),
            "size": size if isinstance(size, int) and size > 0 else None}


def check() -> dict:
    """Asks GitHub for the newest installer release and records it. Never
    downloads. A verified download of any other version is forgotten, so
    Install can only ever start the version the card names."""
    global _release, _verified
    repo = releases_repo()
    allow_pre = include_prereleases()
    lookup = "no_installer_release"
    try:
        resp = _open(f"https://api.github.com/repos/{repo}/releases?per_page={RELEASES_PER_PAGE}",
                     API_HOSTS, "application/vnd.github+json")
        data = json.loads(_read_capped(resp, MAX_RELEASE_JSON_BYTES))
    except (ServiceError, requests.RequestException, ValueError) as exc:
        # A missing, renamed or private repository: not a failure to report
        # as an error, but not "no installer yet" either.
        if getattr(exc, "details", None) == {"http_status": 404}:
            data, lookup = [], "not_found"
        else:
            message = "GitHub's answer wasn't readable." if isinstance(exc, ValueError) \
                else _plain_error(exc)
            with _lock:
                _state.update(checked_at=time.time(), check_error=message)
            _remember_check_failed(True)
            raise DependencyUnavailableError(message) from None
    _remember_check_failed(False)
    # The highest version, not the first listed: GitHub lists by creation date,
    # so a later-created older beta would otherwise hide a newer release.
    # Whether it is newer than this copy is decided in status() and download().
    usable = [r for r in (_parse_release(d, allow_pre)
                          for d in (data if isinstance(data, list) else []))
              if r is not None]
    release = max(usable, key=lambda r: parse_version(r["version"]) or (), default=None)
    if release is not None:
        lookup = "found"
    with _lock:
        _release = release
        if _verified is not None and (release is None
                                      or release["version"] != _verified["version"]):
            _verified = None
            if _state["download"] != "downloading":
                _state.update(download="idle", downloaded_bytes=0, download_error=None)
        _state.update(checked_at=time.time(), check_error=None, lookup=lookup,
                      latest=release["version"] if release else None,
                      notes=release["notes"] if release else "",
                      installer_name=release["exe_name"] if release else None,
                      size=release["exe_size"] if release else None)
    from services import app_update_service
    app_update_service.refresh_offer(release)
    auto_install_tick(after_check=True)
    return status()


def periodic_tick(now=time.time) -> None:
    """At most one check a day, and only when the owner turned it on.
    Called from the API's background poller; a verified app update waiting
    for the quiet hours is applied from here."""
    if not get_auto_check():
        return
    from services import app_update_service
    app_update_service.auto_apply_tick()
    auto_install_tick()
    try:
        last = float(db.get_app_setting(LAST_CHECK_KEY, 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    if now() - last < AUTO_CHECK_SECONDS:
        return
    _scheduled_check(now)


def _scheduled_check(now) -> None:
    # Recorded first, so a failing check isn't retried on every tick.
    db.set_app_setting(LAST_CHECK_KEY, now())
    try:
        check()
    except ServiceError:
        db.set_app_setting(LAST_CHECK_KEY, now() - AUTO_CHECK_SECONDS + CHECK_RETRY_SECONDS)


def _remember_check_failed(failed: bool) -> None:
    # Losing it only costs one check at the next start, or skips one.
    with contextlib.suppress(sqlite3.Error):
        db.set_app_setting(CHECK_FAILED_KEY, failed)


def at_start() -> None:
    """At app start: the leftover sweep, then the check a failed last check asks for."""
    try:
        cleanup_leftovers()
    finally:
        check_at_start()


def check_at_start():
    """At app start: when the last check failed (the PC was offline, GitHub's
    rate limit), check again now rather than only on the hourly retry. After a
    good check the daily schedule stands. Runs in its own thread so startup
    doesn't wait on GitHub; returns that thread, or None when no check is due."""
    if not get_auto_check():
        return None
    try:
        failed = db.get_app_setting(CHECK_FAILED_KEY, False) is True
    except (sqlite3.Error, ValueError):
        return None
    if not failed:
        return None
    thread = threading.Thread(target=_scheduled_check, args=(time.time,), daemon=True,
                              name="update-check-at-start")
    thread.start()
    return thread


def _silent_setup_possible() -> bool:
    """Setup can run with no one there: the Start-menu app on a PC without
    the background service, whose Setup step would stop for an administrator
    prompt."""
    from services import app_update_service as aus
    if not (_is_windows() and _is_installed() and aus.launcher_started()
            and desktop_session.has_desktop()):
        return False
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return not os.path.isdir(os.path.join(program_files, aus.ADMIN_FOLDER))


def auto_install_tick(after_check=False, now=None) -> None:
    """The automatic update for a release the app-only update can't take (a
    new Python, Caddy or WinSW): after a check, download the installer; once
    it is verified and signed, run Setup silently at the app-only update's
    times. Each step that can't go ahead now waits for a later tick; nothing
    here raises."""
    from services import app_update_service as aus
    if not (aus.service_auto_apply() and _silent_setup_possible()):
        return
    if aus.status_fields()["app_update"] != "needs_full_installer":
        return
    with _lock:
        state, held = _state["download"], _verified
        newer = _release is not None and is_newer(_release["version"], current_version(),
                                                  include_prereleases())
    if not newer:
        return
    try:
        if after_check and state in ("idle", "failed") and held is None:
            start_download()
            return
        if (state != "verified" or held is None or held["signature"] is None
                or not aus._apply_time(held, now) or background_jobs.active_job_ids()):
            return
        if db.get_app_setting(AUTO_SETUP_TRIED_KEY, "") == held["version"]:
            return      # one silent try per version; a failed Setup waits for the owner
        try:
            aus.check_database_before_setup(held["version"])
        except ConflictError:
            from services import auto_backup_service
            auto_backup_service.start_now(include_media=False)
            return
        db.set_app_setting(AUTO_SETUP_TRIED_KEY, held["version"])
        install(silent=True)
    except (ServiceError, OSError):
        pass


# --- status -------------------------------------------------------------------

def status() -> dict:
    current = current_version()
    with _lock:
        s = dict(_state)
        held = _verified
        verified = held is not None
        verified_version = held["version"] if held is not None else None
        verified_name = held["name"] if held is not None else None
        verified_signed = held is not None and held["signature"] is not None
    installed = _is_installed()
    return {"current": current, "installed": installed, "latest": s["latest"],
            "update_available": bool(s["latest"] and current
                                     and is_newer(s["latest"], current, include_prereleases())),
            "notes": s["notes"], "installer_name": s["installer_name"], "size": s["size"],
            "checked_at": s["checked_at"], "check_error": s["check_error"],
            "release_lookup": s["lookup"],
            "download": s["download"], "downloaded_bytes": s["downloaded_bytes"],
            "download_error": s["download_error"], "verified": verified,
            "verified_version": verified_version, "verified_name": verified_name,
            "can_install": (verified and installed and _is_windows()
                            and s["download"] != "downloading"),
            "signature_check": signature_check_on(),
            "signature_verified": verified_signed,
            "auto_check": get_auto_check(),
            "include_prereleases": include_prereleases(), "custom_source": custom_source(),
            **_app_status()}


def _app_status() -> dict:
    from services import app_update_service
    return app_update_service.status_fields()


# --- download -----------------------------------------------------------------

def updates_dir() -> str:
    return os.path.join(db.LIBRARY_DIR, "updates")


def _clear_old_files(folder):
    """Removes earlier installers and partial downloads (our names only)."""
    for name in os.listdir(folder):
        if name.startswith("BaiheStudio-Setup-") and name.endswith((".exe", ".part")):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def _clear_setup_copies():
    """Removes the private temp copies Setup was started from. A Setup still
    running keeps its file locked on Windows; that folder goes next time."""
    temp = tempfile.gettempdir()
    try:
        names = os.listdir(temp)
    except OSError:
        return
    for name in names:
        path = os.path.join(temp, name)
        if name.startswith(SETUP_TEMP_PREFIX) and os.path.isdir(path) \
                and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)


def cleanup_leftovers() -> None:
    """At startup: the temp Setup and app-zip copies, partial downloads, and
    every downloaded installer or app zip once it is no longer newer than
    this copy (after an upgrade)."""
    _clear_setup_copies()
    from services import app_update_service
    app_update_service.cleanup_leftovers()
    current, allow_pre = current_version(), include_prereleases()
    for key in (FIRST_VERIFIED_INSTALLER_KEY, app_update_service.FIRST_VERIFIED_APP_KEY):
        _forget_stale_first_verified(key, current, allow_pre)
    folder = updates_dir()
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        m = re.match(r"^BaiheStudio-(?:Setup-(.+)\.exe|App-(.+)\.zip)(\.part)?$", name)
        if m and (m.group(3) or not is_newer(m.group(1) or m.group(2), current, allow_pre)):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def _forget_stale_first_verified(key, current, allow_pre) -> None:
    try:
        record = db.get_app_setting(key, None)
    except ValueError:
        record = {}         # unreadable: nothing in it can be used
    except sqlite3.Error:
        return
    if record is None:
        return
    version = record.get("version") if isinstance(record, dict) else None
    if not is_newer(version, current, allow_pre):
        with contextlib.suppress(sqlite3.Error):
            db.set_app_setting(key, None)


def _expected_hash(release) -> str:
    if not release["hash_url"]:
        raise ConflictError("The release has no SHA-256 file, so the installer can't be "
                            "checked. Nothing was installed.")
    text = _read_capped(_open(release["hash_url"], DOWNLOAD_HOSTS, "application/octet-stream"),
                        MAX_HASH_FILE_BYTES).decode("utf-8", "replace")
    parts = text.strip().split()
    digest = parts[0].lower() if parts else ""
    named = parts[1].lstrip("*") if len(parts) > 1 else release["exe_name"]
    if not _HEX64_RE.match(digest) or named != release["exe_name"]:
        raise ConflictError("The release's SHA-256 file isn't usable, so the installer can't "
                            "be checked. Nothing was installed.")
    return digest


# --- signature ----------------------------------------------------------------

_KEY_DAMAGED = ("This copy's release key file can't be read, so updates can't be checked. "
                "Nothing was installed. Reinstall Baihe Studio by hand from a trusted installer.")


def _release_public_key():
    """The raw release public key, or None when this copy has none (no key
    file, or one with no key line): then updates are SHA-256 checked only.
    A key file that exists but can't be used raises, never falls back to
    None: a damaged key must not switch signature checks off."""
    try:
        with open(PUBLIC_KEY_PATH, "rb") as fh:
            data = fh.read(release_signature.MAX_KEY_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError:
        raise ConflictError(_KEY_DAMAGED) from None
    try:
        if len(data) > release_signature.MAX_KEY_FILE_BYTES:
            raise release_signature.SignatureFormatError("too long")
        key = release_signature.parse_public_key(data.decode("utf-8"))
    except (UnicodeDecodeError, release_signature.SignatureFormatError):
        raise ConflictError(_KEY_DAMAGED) from None
    if key is not None:
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: F401
        except ImportError:
            raise DependencyUnavailableError(
                "This copy can't check release signatures: the cryptography package is "
                "missing. Nothing was installed.") from None
    return key


def signature_check_on() -> bool:
    """True when updates must carry a valid release signature: this copy has
    a release key, or a key file it can't use (which refuses every update)."""
    try:
        return _release_public_key() is not None
    except ServiceError:
        return True


def _release_signature(release) -> bytes:
    if not release.get("sig_url"):
        raise ConflictError("This release isn't signed, so the installer can't be trusted. "
                            "Nothing was downloaded or installed.")
    data = _read_capped(_open(release["sig_url"], DOWNLOAD_HOSTS, "application/octet-stream"),
                        MAX_SIGNATURE_FILE_BYTES)
    try:
        return release_signature.parse_signature(data)
    except release_signature.SignatureFormatError:
        raise ConflictError("The release's signature file isn't usable, so the installer "
                            "can't be trusted. Nothing was installed.") from None


def _signature_ok(public_key, signature, exe_name, sha256_hex) -> bool:
    """True when no key is set (SHA-256 only), else only for a signature by
    that key over this installer name and hash."""
    if public_key is None:
        return True
    return signature is not None and release_signature.verify(public_key, signature, exe_name,
                                                              sha256_hex)


def _progress(n):
    with _lock:
        _state["downloaded_bytes"] = n


def first_verified_at(record, version, name, sha256, now) -> float:
    """When this same file (version, name and SHA-256) was first verified, in this
    run or an earlier one, so an app start doesn't restart the day an automatic
    update waits for the quiet hours. Only the time carries over: the file is
    always downloaded and checked again."""
    if (isinstance(record, dict) and record.get("version") == version
            and record.get("name") == name and record.get("sha256") == sha256):
        at = record.get("at")
        # A clock set back, or a damaged row, must not make the update look older.
        if not isinstance(at, bool) and isinstance(at, (int, float)) and 0 < at <= now:
            return float(at)
    return now


def first_verified_record(verified) -> dict:
    return {k: verified[k] for k in ("version", "name", "sha256", "at")}


def download_deadline_seconds(cap: int) -> float:
    return max(DOWNLOAD_DEADLINE_SECONDS, cap / MIN_DOWNLOAD_BYTES_PER_SECOND)


def fetch_verified(url, final, size, max_bytes, expected, public_key, signature, name, progress,
                   what="installer") -> None:
    """Streams `url` to `final` and keeps it only when its size, SHA-256 and
    (with a key) signature over `name` all match."""
    part = final + ".part"
    cap = min(max_bytes, size or max_bytes)
    sha, total = hashlib.sha256(), 0
    deadline = _monotonic() + download_deadline_seconds(cap)
    resp = _open(url, DOWNLOAD_HOSTS, "application/octet-stream")
    try:
        declared = _declared_length(resp)
        if declared is not None and declared > cap:
            raise _too_big()
        with open(part, "wb") as fh:
            for chunk in resp.iter_content(CHUNK):
                total += len(chunk)
                if total > cap:
                    raise _too_big()
                if _monotonic() > deadline:
                    raise DependencyUnavailableError("The download took too long, so it was "
                                                     "stopped. Try again.")
                sha.update(chunk)
                fh.write(chunk)
                progress(total)
        if size and total != size:
            raise DependencyUnavailableError("The download was incomplete. Try again.")
        if not hmac.compare_digest(sha.hexdigest(), expected):
            raise ConflictError("The download doesn't match the release's SHA-256, so it was "
                                "deleted. Nothing was installed.")
        if not _signature_ok(public_key, signature, name, sha.hexdigest()):
            raise ConflictError(f"The {what}'s signature doesn't match this copy's release "
                                "key, so it was deleted. Nothing was installed.")
        os.replace(part, final)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    finally:
        resp.close()


def download() -> dict:
    """Downloads the checked release's installer and verifies it. Runs on
    the caller's thread (start_download runs it on its own)."""
    global _verified
    with _lock:
        release = _release
    if release is None or not is_newer(release["version"], current_version(),
                                       include_prereleases()):
        raise ConflictError("There is no newer version to download. Check for updates first.")
    if release["exe_size"] and release["exe_size"] > MAX_INSTALLER_BYTES:
        raise _too_big()
    public_key = _release_public_key()
    expected = _expected_hash(release)
    # Fetched before the installer: an unsigned release is refused without
    # downloading hundreds of MB.
    signature = _release_signature(release) if public_key is not None else None
    folder = updates_dir()
    os.makedirs(folder, exist_ok=True)
    _clear_old_files(folder)
    _clear_setup_copies()
    final = os.path.join(folder, release["exe_name"])
    fetch_verified(release["exe_url"], final, release["exe_size"], MAX_INSTALLER_BYTES, expected,
                   public_key, signature, release["exe_name"], _progress)
    try:
        record = db.get_app_setting(FIRST_VERIFIED_INSTALLER_KEY, None)
    except (sqlite3.Error, ValueError):
        record = None       # only the wait restarts; the verified download still counts
    at = first_verified_at(record, release["version"], release["exe_name"], expected, time.time())
    with _lock:
        if _release is None or _release["version"] != release["version"]:
            raise ConflictError("A different version was found while downloading. "
                                "Download again.")
        # Set in the same step: no reader sees a verified file the state still calls
        # downloading, and a check() can't clear it in between.
        _verified = {"version": release["version"], "name": release["exe_name"],
                     "path": final, "sha256": expected, "signature": signature, "at": at}
        _state.update(download="verified", download_error=None)
        held = _verified
    # Losing it only restarts the wait, so a failed write is ignored.
    with contextlib.suppress(sqlite3.Error):
        db.set_app_setting(FIRST_VERIFIED_INSTALLER_KEY, first_verified_record(held))
    return status()


def _run_download():
    try:
        download()
    except Exception as exc:
        with _lock:
            _state.update(download="failed", download_error=_plain_error(exc))


def start_download() -> dict:
    """Starts the download on its own thread; the status reports progress."""
    global _download_thread, _verified
    if not _is_installed():
        raise UnsupportedOperationError("Updates are for the installed app. A source checkout "
                                        "updates with git.")
    current, allow_pre = current_version(), include_prereleases()
    # Checked and set under one lock: two clicks can't start two downloads.
    with _lock:
        if _state["download"] == "downloading":
            raise ConflictError("A download is already running.")
        if _installing:
            raise ConflictError(_SETUP_STARTING)
        if _release is None or not is_newer(_release["version"], current, allow_pre):
            raise ConflictError("There is no newer version to download. Check for updates first.")
        _verified = None
        _state.update(download="downloading", downloaded_bytes=0, download_error=None)
    # Taken before the thread starts: a fast download could otherwise finish first and
    # the caller would be told "verified" by the call that started it. The state above
    # already refuses a second start, so nothing can slip in between.
    started = status()
    try:
        _download_thread = threading.Thread(target=_run_download, daemon=True,
                                            name="update-download")
        _download_thread.start()
    except BaseException:
        with _lock:
            _state.update(download="idle")
        raise
    return started


# --- install ------------------------------------------------------------------

def _file_sha256(path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _setup_env() -> dict:
    return {k: v for k, v in os.environ.items()
            if k.upper() not in _SETUP_ENV_DROP and not k.upper().endswith(_SECRET_SUFFIXES)}


# Same wording as the app-only update's refusal: no new text for the same situation.
_SETUP_STARTING = "An app update is already starting."
SETUP_OPEN = "The full installer is starting or open. Finish or close it, then try again."
_APP_UPDATE_APPLYING = ("The app-only update is being applied. Wait for Baihe Studio to "
                        "restart, then try again.")


NO_DESKTOP_FOR_SETUP = ("Baihe is running as a background service with no desktop, so Setup can't open "
                        "here. Run {name} from the library's updates folder on this PC yourself, or use "
                        "the app-only update if it is offered.")


def _verified_name() -> str:
    with _lock:
        return _verified["name"] if _verified is not None else "the downloaded installer"


def setup_busy() -> bool:
    """Whether install() is starting Setup or a Setup it started is still
    open: the app-only update must not swap the app folder Setup replaces."""
    with _lock:
        return _installing or (_setup is not None and _setup.poll() is None)


# An automatic full update: no window, no questions, the choices of the last
# install (folders and tasks), and Setup starts the app again once it is done.
SILENT_SETUP_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RESTARTAPP=1")


def install(silent=False) -> dict:
    """Starts the verified installer's Setup from a private copy, and
    nothing else: the server keeps running (Setup stops it). Visible unless
    `silent` (the automatic update). Refused off Windows,
    for a source checkout, without a verified download, while a download or
    any job is running, and while Setup or an app-only update is under way."""
    global _installing
    from services import app_update_service
    if not _is_windows():
        raise UnsupportedOperationError("Installing an update is only possible on Windows.")
    if not _is_installed():
        raise UnsupportedOperationError("Updates are for the installed app. A source checkout "
                                        "updates with git.")
    if not desktop_session.has_desktop():
        # A boot service runs in Session 0: Setup's window would open on no desktop and wait
        # forever, leaving "starting or open" stuck and the button unusable.
        raise UnsupportedOperationError(NO_DESKTOP_FOR_SETUP.format(name=_verified_name()))
    # Checked and set under one lock: a second click can't start a second Setup,
    # and a download can't start (and delete the file) while this one copies it.
    with _lock:
        if _state["download"] == "downloading":
            raise ConflictError("Wait for the download to finish.")
        if _installing or (_setup is not None and _setup.poll() is None):
            raise ConflictError(SETUP_OPEN)
        verified = _verified
        if verified is None:
            raise ConflictError("Download and verify the update first.")
        _installing = True
    try:
        # Outside _lock: the app-only update takes its own lock and then
        # calls setup_busy(), so holding both here could deadlock.
        if app_update_service.apply_busy():
            raise ConflictError(_APP_UPDATE_APPLYING)
        return _install(verified, silent)
    finally:
        with _lock:
            _installing = False


def _install(verified, silent=False) -> dict:
    global _verified, _setup
    # The option may have been switched off since the download; same rule as download().
    if not is_newer(verified["version"], current_version(), include_prereleases()):
        raise ConflictError("There is no newer version to install. Check for updates first.")
    if background_jobs.active_job_ids():
        raise ConflictError("Finish or cancel the running jobs first: Setup stops the app.")
    from services import app_update_service
    app_update_service.check_database_before_setup(verified["version"])
    public_key = _release_public_key()
    changed = "The downloaded installer changed or is missing. Download it again."
    # The copy, in a new folder only this user can reach, is what gets hashed,
    # signature-checked and started, so the file in the library can't be
    # swapped in between.
    folder = tempfile.mkdtemp(prefix=SETUP_TEMP_PREFIX)
    copy = os.path.join(folder, verified["name"])
    try:
        shutil.copyfile(verified["path"], copy)
        copy_sha = _file_sha256(copy)
        ok = hmac.compare_digest(copy_sha, verified["sha256"])
    except OSError as exc:
        problem = copy_problem(exc)
        if problem is not None:
            # The download itself is fine: keep it rather than say "download it again".
            shutil.rmtree(folder, ignore_errors=True)
            raise DependencyUnavailableError(problem) from None
        ok = False
    if ok and not _signature_ok(public_key, verified["signature"], verified["name"], copy_sha):
        ok, changed = False, ("The downloaded installer isn't signed with this copy's release "
                              "key. Download it again.")
    if not ok:
        shutil.rmtree(folder, ignore_errors=True)
        with _lock:
            # Only the download this call checked: one that replaced it meanwhile is
            # not this failure's to clear.
            if _verified is verified and _state["download"] != "downloading":
                _verified = None
                _state.update(download="failed", download_error=changed)
        raise ConflictError(changed)
    try:
        # The server's job lets a child out only for the length of this call.
        with process_guard.breakaway_allowed():
            setup = subprocess.Popen([copy, *(SILENT_SETUP_ARGS if silent else ())],
                                     cwd=folder, env=_setup_env(),
                                     creationflags=_CREATE_BREAKAWAY_FROM_JOB
                                     | _CREATE_NEW_PROCESS_GROUP, close_fds=True)
        with _lock:
            _setup = setup
    except (OSError, ValueError):
        shutil.rmtree(folder, ignore_errors=True)
        raise DependencyUnavailableError(
            f"Windows didn't let Baihe start Setup. Run {verified['name']} from the library's "
            "updates folder yourself.") from None
    return {"launched": True, "installer_name": verified["name"],
            "version": verified["version"]}

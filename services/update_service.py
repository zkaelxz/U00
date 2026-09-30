"""
services/update_service.py -- check for, download and hand off to a newer
Windows installer published on the project's public GitHub Releases.

- Check: one unauthenticated GET of the releases API's `latest` release
  (drafts and pre-releases are never offered). The repository is a setting,
  BAIHE_UPDATE_REPO in .env (default zkaelxz/U00), so installers can move to
  a separate public repository without a code change.
- Download: only on the user's click. The installer and its `.sha256` go to
  `<library>/updates/`; the installer is kept only if its SHA-256 matches.
  That hash comes from the same release, so it catches a broken or
  truncated download, not a tampered release: it is not a signature, and
  the installer is not code-signed.
- Install: only on the user's click, only on Windows and only for an
  installed copy. The verified file is hashed again and Setup is started
  with a fixed argument list (no shell, no silent flags), outside the
  server's kill-on-close Job Object so it outlives the server. The server
  keeps running: when the user clicks Install in Setup, Setup's own upgrade
  step (`launcher.py --stop`) stops it cleanly, cancelling running jobs.

Every request is https to an allowlisted host, redirects are followed by
hand and each hop is checked, and no credentials are sent (not even from
~/.netrc). Status holds names, numbers and booleans only: never a URL or a
filesystem path.
"""

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests

import background_jobs
import db
import portable
from services.service_errors import (ConflictError, DependencyUnavailableError, ServiceError,
                                     UnsupportedOperationError)

REPO_ENV = "BAIHE_UPDATE_REPO"
DEFAULT_REPO = "zkaelxz/U00"
AUTO_CHECK_KEY = "update.auto_check"
LAST_CHECK_KEY = "update.last_check_at"
AUTO_CHECK_SECONDS = 24 * 3600

API_HOSTS = frozenset({"api.github.com"})
DOWNLOAD_HOSTS = frozenset({"github.com", "objects.githubusercontent.com",
                            "release-assets.githubusercontent.com"})
TIMEOUT = (10, 30)              # connect, read (between chunks)
MAX_REDIRECTS = 5
MAX_RELEASE_JSON_BYTES = 1_000_000
MAX_HASH_FILE_BYTES = 4096
MAX_INSTALLER_BYTES = 600 * 1024 * 1024
NOTES_MAX_CHARS = 2000
CHUNK = 64 * 1024

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

_lock = threading.Lock()
_state = {"checked_at": None, "check_error": None, "latest": None, "notes": "",
          "installer_name": None, "size": None, "download": "idle",
          "downloaded_bytes": 0, "download_error": None}
_release = None     # internal: {"version", "exe_name", "exe_url", "exe_size", "hash_url"}
_verified = None    # internal: {"version", "name", "path", "sha256"}
_download_thread = None


# --- versions -----------------------------------------------------------------

def parse_version(text):
    """(major, minor, patch, is_release) or None. Build metadata (+...) is
    ignored; a pre-release (-rc1) sorts before its release."""
    m = _VERSION_RE.match(str(text or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), m.group(4) is None)


def is_newer(latest, current) -> bool:
    """True only when `latest` is a release (not a pre-release) and sorts
    after `current`. Anything unparsable is never newer."""
    lv, cv = parse_version(latest), parse_version(current)
    return bool(lv and cv and lv[3] and lv > cv)


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


# --- settings -----------------------------------------------------------------

def releases_repo() -> str:
    from services import settings_service
    repo = (settings_service.resolve_env_names((REPO_ENV,)) or DEFAULT_REPO).strip()
    if not _REPO_RE.match(repo) or ".." in repo:
        raise UnsupportedOperationError(f"{REPO_ENV} must look like owner/repository.")
    return repo


def get_auto_check() -> bool:
    try:
        return db.get_app_setting(AUTO_CHECK_KEY, False) is True
    except Exception:
        return False


def set_auto_check(enabled: bool) -> dict:
    db.set_app_setting(AUTO_CHECK_KEY, bool(enabled))
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
            raise DependencyUnavailableError(f"GitHub answered HTTP {resp.status_code}.",
                                             details={"http_status": resp.status_code})
        return resp
    raise DependencyUnavailableError("Too many redirects from GitHub.")


def _too_big():
    return DependencyUnavailableError("The download is larger than allowed, so it was refused.")


def _declared_length(resp):
    raw = resp.headers.get("Content-Length") or ""
    return int(raw) if raw.isdigit() else None


def _read_capped(resp, cap) -> bytes:
    try:
        declared = _declared_length(resp)
        if declared is not None and declared > cap:
            raise _too_big()
        out, total = [], 0
        for chunk in resp.iter_content(CHUNK):
            total += len(chunk)
            if total > cap:
                raise _too_big()
            out.append(chunk)
        return b"".join(out)
    finally:
        resp.close()


def _plain_error(exc) -> str:
    """User-facing text for a failure: our own messages as they are, and a
    generic line for network errors (whose text carries the URL)."""
    from translate_engines import redact_secrets
    if isinstance(exc, ServiceError):
        return redact_secrets(exc.message)
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


def _parse_release(data):
    """The internal release record, or None when the latest release offers
    nothing usable (a draft, a pre-release, an odd tag, no installer)."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    parsed = parse_version(tag)
    if not parsed or not parsed[3]:
        return None
    version = str(tag).strip().lstrip("v")
    exe_name = f"BaiheStudio-Setup-{version}.exe"
    assets = {a.get("name"): a for a in data.get("assets") or [] if isinstance(a, dict)}
    exe, sha = assets.get(exe_name), assets.get(exe_name + ".sha256")
    if not exe or not isinstance(exe.get("browser_download_url"), str):
        return None
    size = exe.get("size")
    return {"version": version, "exe_name": exe_name, "exe_url": exe["browser_download_url"],
            "exe_size": size if isinstance(size, int) and size > 0 else None,
            "hash_url": sha.get("browser_download_url") if sha else None,
            "notes": _plain_notes(data.get("body"))}


def check() -> dict:
    """Asks GitHub for the latest release and records it. Never downloads."""
    global _release
    repo = releases_repo()
    try:
        resp = _open(f"https://api.github.com/repos/{repo}/releases/latest", API_HOSTS,
                     "application/vnd.github+json")
        data = json.loads(_read_capped(resp, MAX_RELEASE_JSON_BYTES))
    except (ServiceError, requests.RequestException, ValueError) as exc:
        # No published release yet: GitHub's `latest` is a 404, not a failure.
        if getattr(exc, "details", None) == {"http_status": 404}:
            data = None
        else:
            message = "GitHub's answer wasn't readable." if isinstance(exc, ValueError) \
                else _plain_error(exc)
            with _lock:
                _state.update(checked_at=time.time(), check_error=message)
            raise DependencyUnavailableError(message) from None
    release = _parse_release(data)
    with _lock:
        _release = release
        _state.update(checked_at=time.time(), check_error=None,
                      latest=release["version"] if release else None,
                      notes=release["notes"] if release else "",
                      installer_name=release["exe_name"] if release else None,
                      size=release["exe_size"] if release else None)
    return status()


def periodic_tick(now=time.time) -> None:
    """At most one check a day, and only when the owner turned it on.
    Called from the API's background poller; never downloads."""
    if not get_auto_check():
        return
    try:
        last = float(db.get_app_setting(LAST_CHECK_KEY, 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    if now() - last < AUTO_CHECK_SECONDS:
        return
    # Recorded first, so a failing check isn't retried on every tick.
    db.set_app_setting(LAST_CHECK_KEY, now())
    try:
        check()
    except ServiceError:
        pass


# --- status -------------------------------------------------------------------

def status() -> dict:
    current = current_version()
    with _lock:
        s = dict(_state)
        verified = _verified is not None
    installed = _is_installed()
    return {"current": current, "installed": installed, "latest": s["latest"],
            "update_available": bool(s["latest"] and current and is_newer(s["latest"], current)),
            "notes": s["notes"], "installer_name": s["installer_name"], "size": s["size"],
            "checked_at": s["checked_at"], "check_error": s["check_error"],
            "download": s["download"], "downloaded_bytes": s["downloaded_bytes"],
            "download_error": s["download_error"], "verified": verified,
            "can_install": verified and installed and _is_windows(),
            "auto_check": get_auto_check()}


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


def _progress(n):
    with _lock:
        _state["downloaded_bytes"] = n


def download() -> dict:
    """Downloads the checked release's installer and verifies it. Runs on
    the caller's thread (start_download runs it on its own)."""
    global _verified
    with _lock:
        release = _release
    if release is None or not is_newer(release["version"], current_version()):
        raise ConflictError("There is no newer version to download. Check for updates first.")
    if release["exe_size"] and release["exe_size"] > MAX_INSTALLER_BYTES:
        raise _too_big()
    expected = _expected_hash(release)
    folder = updates_dir()
    os.makedirs(folder, exist_ok=True)
    _clear_old_files(folder)
    final = os.path.join(folder, release["exe_name"])
    part = final + ".part"
    cap = min(MAX_INSTALLER_BYTES, release["exe_size"] or MAX_INSTALLER_BYTES)
    sha, total = hashlib.sha256(), 0
    resp = _open(release["exe_url"], DOWNLOAD_HOSTS, "application/octet-stream")
    try:
        declared = _declared_length(resp)
        if declared is not None and declared > cap:
            raise _too_big()
        with open(part, "wb") as fh:
            for chunk in resp.iter_content(CHUNK):
                total += len(chunk)
                if total > cap:
                    raise _too_big()
                sha.update(chunk)
                fh.write(chunk)
                _progress(total)
        if release["exe_size"] and total != release["exe_size"]:
            raise DependencyUnavailableError("The download was incomplete. Try again.")
        if not hmac.compare_digest(sha.hexdigest(), expected):
            raise ConflictError("The download doesn't match the release's SHA-256, so it was "
                                "deleted. Nothing was installed.")
        os.replace(part, final)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    finally:
        resp.close()
    with _lock:
        _verified = {"version": release["version"], "name": release["exe_name"],
                     "path": final, "sha256": expected}
    return status()


def _run_download():
    try:
        download()
        with _lock:
            _state.update(download="verified", download_error=None)
    except Exception as exc:
        with _lock:
            _state.update(download="failed", download_error=_plain_error(exc))


def start_download() -> dict:
    """Starts the download on its own thread; the status reports progress."""
    global _download_thread, _verified
    if not _is_installed():
        raise UnsupportedOperationError("Updates are for the installed app. A source checkout "
                                        "updates with git.")
    with _lock:
        if _state["download"] == "downloading":
            raise ConflictError("A download is already running.")
        release = _release
    if release is None or not is_newer(release["version"], current_version()):
        raise ConflictError("There is no newer version to download. Check for updates first.")
    with _lock:
        _verified = None
        _state.update(download="downloading", downloaded_bytes=0, download_error=None)
        _download_thread = threading.Thread(target=_run_download, daemon=True,
                                            name="update-download")
        _download_thread.start()
    return status()


# --- install ------------------------------------------------------------------

def _file_sha256(path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            sha.update(chunk)
    return sha.hexdigest()


def install() -> dict:
    """Starts the verified installer's normal (visible) Setup. Refused off
    Windows, for a source checkout, without a verified download and while
    jobs are running."""
    global _verified
    if not _is_windows():
        raise UnsupportedOperationError("Installing an update is only possible on Windows.")
    if not _is_installed():
        raise UnsupportedOperationError("Updates are for the installed app. A source checkout "
                                        "updates with git.")
    with _lock:
        verified = _verified
    if verified is None:
        raise ConflictError("Download and verify the update first.")
    if background_jobs.active_job_ids():
        raise ConflictError("Finish or cancel the running jobs first: Setup stops the app.")
    try:
        ok = hmac.compare_digest(_file_sha256(verified["path"]), verified["sha256"])
    except OSError:
        ok = False
    if not ok:
        with _lock:
            _verified = None
            _state.update(download="failed",
                          download_error="The downloaded installer changed or is missing. "
                                         "Download it again.")
        raise ConflictError("The downloaded installer changed or is missing. Download it again.")
    try:
        subprocess.Popen([verified["path"]], cwd=os.path.dirname(verified["path"]),
                         creationflags=_CREATE_BREAKAWAY_FROM_JOB | _CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)
    except (OSError, ValueError):
        raise DependencyUnavailableError(
            f"Windows didn't let Baihe start Setup. Run {verified['name']} from the library's "
            "updates folder yourself.") from None
    return {"launched": True, "installer_name": verified["name"]}


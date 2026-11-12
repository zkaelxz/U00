"""services/ffmpeg_install_service.py -- Diagnostics "Install ffmpeg": downloads a pinned ffmpeg build into the app's own tools folder."""

import hashlib
import hmac
import os
import shutil
import sys
import threading
import time
import zipfile
from urllib.parse import urlsplit

import requests

import app_tools
import background_jobs
import diagnostics
from jobs import job_launch
from lib import capped_body, http
from lib.errors import ConflictError, DependencyUnavailableError
from services import diagnostics_admin_service as admin_svc

FFMPEG_JOB_ID = "ffmpeg_install"

# gyan.dev's "essentials" build: GPL v3 with libass and libx264, which burned-in
# subtitle export needs. It is downloaded from the vendor on the user's request
# and never bundled in the installer, so Baihe does not redistribute GPL code.
# Both URLs serve the same file; changing the version means new URLs, size and hash.
FFMPEG_VERSION = "9.0.2"
FFMPEG_ZIP_NAME = f"ffmpeg-{FFMPEG_VERSION}-essentials_build.zip"
FFMPEG_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
FFMPEG_SIZE = 114768076
FFMPEG_URLS = (
    f"https://github.com/GyanD/codexffmpeg/releases/download/{FFMPEG_VERSION}/{FFMPEG_ZIP_NAME}",
    f"https://www.gyan.dev/ffmpeg/builds/packages/{FFMPEG_ZIP_NAME}",
)
# Every hop of a download must be https on one of these; the SHA-256 is what
# vouches for the bytes, the allowlist only keeps the app from fetching elsewhere.
ALLOWED_HOSTS = frozenset({
    "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
    "www.gyan.dev", "gyan.dev",
})
MAX_REDIRECTS = 5
TIMEOUT = (10, 30)
CHUNK = 256 * 1024
DOWNLOAD_DEADLINE_SECONDS = 30 * 60
# The zip, then ffmpeg.exe and ffprobe.exe (about 105 MB each) unpacked next to it.
MIN_FREE_MB = 500
DOWNLOAD_MB = round(FFMPEG_SIZE / (1024 * 1024))

# Only these members are unpacked, by exact name, so a crafted archive can't
# write anywhere else. Their uncompressed sizes are capped as a bomb guard.
_WANTED = {"bin/ffmpeg.exe": "ffmpeg.exe", "bin/ffprobe.exe": "ffprobe.exe",
           "license": "LICENSE.txt"}
MAX_MEMBER_BYTES = 200 * 1024 * 1024

_STATE_LOCK = threading.Lock()
_RESULT: dict = {"last": None}


class AlreadyAvailable(admin_svc.AdminActionRefused, ConflictError):
    pass


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _system_ffmpeg_found() -> bool:
    """An ffmpeg on PATH that is not the app's own copy."""
    path = shutil.which("ffmpeg")
    if not path:
        return False
    own = os.path.normcase(os.path.abspath(app_tools.ffmpeg_folder()))
    return os.path.normcase(os.path.abspath(os.path.dirname(path))) != own


def _free_mb() -> int:
    folder = app_tools.tools_folder()
    while folder and not os.path.exists(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return int(shutil.disk_usage(folder).free // (1024 * 1024))


def _refusal():
    """Plain-English reason the install can't start now, or None."""
    if not _is_windows():
        return "This download is for Windows. Install ffmpeg with your system's package manager."
    if app_tools.app_ffmpeg_present():
        return "ffmpeg is already installed in the app's own folder."
    if _system_ffmpeg_found():
        return "ffmpeg is already installed on this PC, so this is not needed."
    if _free_mb() < MIN_FREE_MB:
        return f"Not enough free disk space (about {MIN_FREE_MB} MB needed)."
    return None


def _job_view(job_id: str):
    job = background_jobs.get_status(job_id)
    if not job:
        return None
    return {"status": job.get("status"), "progress": float(job.get("progress") or 0.0),
            "message": str(job.get("message") or "")[:200],
            "error": str(job["error"])[:200] if job.get("error") else None}


def get_status() -> dict:
    with _STATE_LOCK:
        last = dict(_RESULT["last"]) if _RESULT["last"] else None
    check = diagnostics.check_ffmpeg()
    return {"found": bool(check.get("found")), "libass": check.get("libass"),
            "app_ffmpeg_installed": app_tools.app_ffmpeg_present(),
            "version": FFMPEG_VERSION, "download_mb": DOWNLOAD_MB,
            "free_mb": _free_mb(), "required_mb": MIN_FREE_MB,
            "refusal": _refusal(), "job_id": FFMPEG_JOB_ID,
            "job": _job_view(FFMPEG_JOB_ID), "last_result": last}


def start_install(confirm: bool = False) -> dict:
    """PC only (the route is local_only()). 422 unconfirmed or not possible on
    this PC; 409 when any job is running or queued at the start (the install
    itself holds nothing exclusive, so a job started during it is allowed;
    the folder only takes its final name once fully unpacked) or when ffmpeg
    is already there."""
    admin_svc.guard(confirm)
    reason = _refusal()
    if reason:
        if reason.startswith(("ffmpeg is already",)):
            raise AlreadyAvailable(reason)
        raise admin_svc.AdminActionNotPossible(reason)
    if not job_launch.start_job(FFMPEG_JOB_ID, _install_job,
                                description="Installing ffmpeg", line_fields=()):
        raise admin_svc.AdminActionJobsRunning(
            "An ffmpeg install or a library restore is already running; try again when it ends.")
    return {"job_id": FFMPEG_JOB_ID, "started": True}


def _guard(url: str):
    """lib.http's per-hop guard: only https on the allowed hosts, then the
    usual public-address check that pins the connection to what it resolved."""
    parts = urlsplit(url)
    if (parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise DependencyUnavailableError("The download came from an address Baihe doesn't trust, "
                                         "so it was refused.")
    return http.check_public(url)


def _too_big():
    return DependencyUnavailableError("The download is larger than expected, so it was refused.")


def fetch_verified(urls, part: str, size: int, expected: str, progress, cancelled) -> None:
    """Streams the first URL that answers into `part`; keeps it only when its
    size and SHA-256 match. Raises, leaving no `part`, otherwise."""
    last_error = None
    for url in urls:
        try:
            _download_one(url, part, size, expected, progress, cancelled)
            return
        except (requests.RequestException, DependencyUnavailableError) as exc:
            last_error = exc
            if isinstance(exc, DependencyUnavailableError) and "SHA-256" in str(exc):
                raise
    if isinstance(last_error, DependencyUnavailableError):
        raise last_error
    raise DependencyUnavailableError("Couldn't reach the download server. Check your connection "
                                     "and try again.")


def _download_one(url, part, size, expected, progress, cancelled):
    sha, total = hashlib.sha256(), 0
    deadline = time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
    # The cap is the pinned size, so a longer body is cut off by lib.http itself.
    session = http.session(timeout=TIMEOUT, guard=_guard, max_bytes=size, max_redirects=MAX_REDIRECTS)
    try:
        resp = session.get(url, headers={"User-Agent": "BaiheStudio-tools"})
        try:
            if resp.status_code != 200:
                raise DependencyUnavailableError(
                    f"The download server answered HTTP {resp.status_code}.")
            declared = capped_body.declared_length(resp)
            if declared is not None and declared > size:
                raise _too_big()
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(CHUNK):
                    if cancelled():
                        raise background_jobs.JobCancelled()
                    total += len(chunk)
                    if total > size:
                        raise _too_big()
                    if time.monotonic() > deadline:
                        raise DependencyUnavailableError("The download took too long, so it was "
                                                         "stopped. Try again.")
                    sha.update(chunk)
                    fh.write(chunk)
                    progress(total)
            if total != size:
                raise DependencyUnavailableError("The download was incomplete. Try again.")
            if not hmac.compare_digest(sha.hexdigest(), expected):
                raise DependencyUnavailableError("The download doesn't match the expected SHA-256, "
                                                 "so it was deleted. Nothing was installed.")
        finally:
            resp.close()
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    finally:
        session.close()


def unpack(zip_path: str, dest: str) -> None:
    """Copies ffmpeg.exe, ffprobe.exe and the licence out of the build's zip
    into `dest` (created), by exact member name."""
    os.makedirs(dest, exist_ok=True)
    found = set()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            # The build's top folder is named after its version; match the rest.
            rest = info.filename.split("/", 1)[1].lower() if "/" in info.filename else ""
            target = _WANTED.get(rest)
            if not target or info.is_dir():
                continue
            if info.file_size > MAX_MEMBER_BYTES:
                raise DependencyUnavailableError("The download holds a file larger than expected, "
                                                 "so it was refused.")
            with zf.open(info) as src, open(os.path.join(dest, target), "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
            found.add(target)
    if not {"ffmpeg.exe", "ffprobe.exe"} <= found:
        raise DependencyUnavailableError("The download didn't contain ffmpeg, so nothing was installed.")


def _store(ok: bool, message: str) -> dict:
    result = {"ok": ok, "message": message}
    with _STATE_LOCK:
        _RESULT["last"] = result
    return result


def _install_job():
    final = app_tools.ffmpeg_folder()
    work = os.path.join(app_tools.tools_folder(), "ffmpeg.partial")
    part = work + ".zip"
    staging = work + ".new"

    def cleanup():
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.remove(part)
        except OSError:
            pass

    def progress(n):
        background_jobs.update_progress(FFMPEG_JOB_ID, min(n / FFMPEG_SIZE, 1.0) * 0.85,
                                        "Downloading ffmpeg…")

    background_jobs.update_progress(FFMPEG_JOB_ID, 0.02, "Starting the ffmpeg download…")
    try:
        os.makedirs(app_tools.tools_folder(), exist_ok=True)
        cleanup()
        fetch_verified(FFMPEG_URLS, part, FFMPEG_SIZE, FFMPEG_SHA256, progress,
                       lambda: background_jobs.is_cancel_requested(FFMPEG_JOB_ID))
        background_jobs.update_progress(FFMPEG_JOB_ID, 0.9, "Unpacking ffmpeg…")
        unpack(part, staging)
        os.remove(part)
        # Only a fully unpacked folder ever takes the final name.
        shutil.rmtree(final, ignore_errors=True)
        os.replace(staging, final)
    except background_jobs.JobCancelled:
        cleanup()
        _store(False, "Cancelled.")
        raise
    except DependencyUnavailableError as exc:
        cleanup()
        message = str(exc)
    except Exception:      # noqa: BLE001 -- never echo an exception (paths)
        cleanup()
        message = "The ffmpeg install failed. Check free disk space and try again."
    else:
        app_tools.activate()
        if diagnostics.check_ffmpeg().get("found"):
            _store(True, "ffmpeg installed.")
            background_jobs.set_result(FFMPEG_JOB_ID, {"status": "ok", "detail": "ffmpeg installed."})
            return
        shutil.rmtree(final, ignore_errors=True)
        message = "The download finished but ffmpeg would not start afterwards."
    _store(False, message)
    background_jobs.set_result(FFMPEG_JOB_ID, {"status": "failed", "detail": message})
    raise RuntimeError(message)

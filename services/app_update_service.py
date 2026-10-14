"""
services/app_update_service.py -- the app-only update: when a release's app
layer (BaiheStudio-App-<v>.zip) was built against this copy's own Python,
wheels, WinSW and Caddy, download just that zip and switch to it instead of
running the full installer (docs/windows-installer-design.md, "App-only
updates").

- Offer: after each check (services/update_service.check), the release's
  small manifest copy is read; "Update app only" is offered when its base
  equals this install's (installer/app_swap.base_fingerprint) and this
  install's base is at least its min_base_version. Otherwise the card says
  the full installer is needed, and that flow is unchanged.
- Download: on the user's click, size-capped, SHA-256 checked and, with a
  release key, signature checked exactly as the installer is
  (update_service.fetch_verified; the `.sig` is fetched first).
- Apply: on the user's click, only on Windows, only for a server the
  Start-menu launcher started (the boot service's account can't write the
  install folder, and stopping it needs an administrator), and never while a
  job runs. A private copy of the zip is re-hashed and signature-checked, its
  entries and manifest checked again (the signed zip's own manifest is the
  one that counts, not the copy read for the offer), and it is unpacked into
  a new `<install>\\app-<v>`. A copy of installer/app_swap.py then stops the
  app, switches, waits for /api/health and rolls back on any failure;
  `<install>\\app-update\\result.json` tells the next status what happened.
- Database: a version that changes the library database's tables (its
  fresh-database fingerprint differs from this library's) can't be rolled
  back cleanly, so it is refused unless a library backup finished within the
  last day.

Status holds names, numbers, booleans and fixed sentences: never a URL or a path.
"""

import contextlib
import datetime
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

import background_jobs
import db
import process_guard
from installer import app_swap
from services import update_service as us
from services.service_errors import (ConflictError, DependencyUnavailableError, ServiceError,
                                     UnsupportedOperationError)

MAX_APP_ZIP_BYTES = 400 * 1024 * 1024
MAX_MANIFEST_BYTES = app_swap.MAX_MANIFEST_BYTES
BACKUP_MAX_AGE_SECONDS = 24 * 3600
ZIP_TEMP_PREFIX = "baihe_appzip_"
# Only the Start-menu launcher gives the server this (installer/launcher.py
# start_server); the boot service never does.
LAUNCHER_TOKEN_ENV = "BAIHE_SHUTDOWN_TOKEN"

RESULT_MESSAGES = {
    "updated": "Updated to {new}.",
    "queued": ("A file in the app folder was in use, so the update to {new} will finish the "
               "next time Baihe Studio starts."),
    "not_started": "The update to {new} didn't start; {old} kept running.",
    "rolled_back_health": "Version {new} didn't start, so Baihe Studio went back to {old}.",
    "rolled_back_switch": ("The app folder couldn't be switched to {new}, so Baihe Studio "
                           "kept {old}."),
    "recovered": ("The update to {new} was interrupted, so Baihe Studio went back to {old} "
                  "when it started again."),
    "broken": ("The update to {new} couldn't be finished or undone. Run the Baihe Studio "
               "installer again to repair it; your library and settings are kept."),
}

_lock = threading.Lock()
# offer: "none" | "available" | "needs_full_installer"
_state = {"offer": "none", "offer_version": None, "size": None, "download": "idle",
          "downloaded_bytes": 0, "download_error": None}
_release = None     # internal: the release's "app" record plus "version"
_verified = None    # internal: {"version", "name", "path", "sha256", "signature"}
_download_thread = None


def install_root() -> str:
    return os.path.dirname(us._APP_DIR)


def _installed_manifest():
    return app_swap.installed_manifest(install_root())


def _base_matches(manifest) -> bool:
    installed = _installed_manifest()
    if installed is None:
        return False
    base, version = app_swap.base_fingerprint(installed), app_swap.base_version(installed)
    minimum = manifest.get("min_base_version")
    return (base is not None and manifest.get("base") == base and version is not None
            and isinstance(minimum, str) and us.parse_version(minimum) is not None
            and us.parse_version(version) is not None
            and us.parse_version(version) >= us.parse_version(minimum))


def _manifest_ok(manifest, version) -> bool:
    return (isinstance(manifest, dict) and manifest.get("product") == app_swap.PRODUCT
            and manifest.get("layer") == "app" and manifest.get("version") == version)


def refresh_offer(release) -> None:
    """Called after each check with the release it found (or None). Reads
    the release's manifest copy; any failure means no app-only offer."""
    global _release, _verified
    app = (release or {}).get("app")
    offer, size = "none", None
    if app is not None and us._is_installed():
        try:
            data = us._read_capped(us._open(app["manifest_url"], us.DOWNLOAD_HOSTS,
                                            "application/octet-stream"), MAX_MANIFEST_BYTES)
            manifest = json.loads(data.decode("utf-8"))
        except (ServiceError, ValueError, OSError, us.requests.RequestException):
            manifest = None
        if _manifest_ok(manifest, release["version"]):
            offer = "available" if _base_matches(manifest) else "needs_full_installer"
            size = app["size"]
    with _lock:
        _release = dict(app, version=release["version"]) if offer == "available" else None
        if _verified is not None and (_release is None
                                      or _release["version"] != _verified["version"]):
            _verified = None
            if _state["download"] != "downloading":
                _state.update(download="idle", downloaded_bytes=0, download_error=None)
        _state.update(offer=offer, offer_version=release["version"] if release else None,
                      size=size)


def launcher_started() -> bool:
    return bool(os.environ.get(LAUNCHER_TOKEN_ENV))


def last_result():
    """The last swap's outcome in plain words, or None."""
    result = app_swap.read_result(install_root()) if us._is_installed() else None
    if result is None:
        return None
    return {"outcome": result["code"], "at": result["at"],
            "message": RESULT_MESSAGES[result["code"]].format(old=result["old"], new=result["new"])}


def status_fields() -> dict:
    current = us.current_version()
    with _lock:
        s = dict(_state)
        verified = _verified is not None
        verified_version = _verified["version"] if verified else None
    newer = bool(s["offer_version"] and current
                 and us.is_newer(s["offer_version"], current, us.include_prereleases()))
    offer = s["offer"] if newer else "none"
    result = last_result()
    return {"app_update": offer,
            "app_update_here": us._is_windows() and launcher_started(),
            "app_size": s["size"] if offer != "none" else None,
            "app_download": s["download"], "app_downloaded_bytes": s["downloaded_bytes"],
            "app_download_error": s["download_error"], "app_verified": verified,
            "app_verified_version": verified_version,
            "can_apply_app": (verified and offer == "available" and us._is_windows()
                              and launcher_started() and s["download"] != "downloading"),
            "app_last_result": result["message"] if result else None,
            "app_last_result_at": result["at"] if result else None}


# --- download -----------------------------------------------------------------

def _clear_old_zips(folder) -> None:
    for name in os.listdir(folder):
        if name.startswith("BaiheStudio-App-") and name.endswith((".zip", ".part")):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(folder, name))


def _progress(n) -> None:
    with _lock:
        _state["downloaded_bytes"] = n


def download() -> dict:
    global _verified
    with _lock:
        release = _release
    if release is None or not us.is_newer(release["version"], us.current_version(),
                                          us.include_prereleases()):
        raise ConflictError("There is no app-only update to download. Check for updates first.")
    if release["size"] and release["size"] > MAX_APP_ZIP_BYTES:
        raise us._too_big()
    public_key = us._release_public_key()
    hash_release = {"hash_url": release["hash_url"], "exe_name": release["name"]}
    expected = us._expected_hash(hash_release)
    signature = (us._release_signature({"sig_url": release["sig_url"]})
                 if public_key is not None else None)
    folder = us.updates_dir()
    os.makedirs(folder, exist_ok=True)
    _clear_old_zips(folder)
    final = os.path.join(folder, release["name"])
    us.fetch_verified(release["url"], final, release["size"], MAX_APP_ZIP_BYTES, expected,
                      public_key, signature, release["name"], _progress, what="update")
    with _lock:
        if _release is None or _release["version"] != release["version"]:
            raise ConflictError("A different version was found while downloading. Download again.")
        _verified = {"version": release["version"], "name": release["name"], "path": final,
                     "sha256": expected, "signature": signature}
    return us.status()


def _run_download() -> None:
    try:
        download()
        with _lock:
            _state.update(download="verified", download_error=None)
    except Exception as exc:
        with _lock:
            _state.update(download="failed", download_error=us._plain_error(exc))


def start_download() -> dict:
    global _download_thread, _verified
    if not us._is_installed():
        raise UnsupportedOperationError("Updates are for the installed app. A source checkout "
                                        "updates with git.")
    current, allow_pre = us.current_version(), us.include_prereleases()
    with _lock:
        if _state["download"] == "downloading":
            raise ConflictError("A download is already running.")
        if _release is None or not us.is_newer(_release["version"], current, allow_pre):
            raise ConflictError("There is no app-only update to download. Check for updates "
                                "first.")
        _verified = None
        _state.update(download="downloading", downloaded_bytes=0, download_error=None)
    started = us.status()
    try:
        _download_thread = threading.Thread(target=_run_download, daemon=True,
                                            name="app-update-download")
        _download_thread.start()
    except BaseException:
        with _lock:
            _state.update(download="idle")
        raise
    return started


# --- apply --------------------------------------------------------------------

def _recent_backup(now=None) -> bool:
    from services import auto_backup_service
    try:
        last = auto_backup_service.settings_overview().get("last_run_at")
        when = datetime.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except (ServiceError, ValueError, TypeError, AttributeError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    now = now if now is not None else time.time()
    return 0 <= now - when.timestamp() <= BACKUP_MAX_AGE_SECONDS


def _library_schema() -> str:
    with contextlib.closing(db.get_conn()) as conn:
        return app_swap.schema_fingerprint(conn)


def _check_database(manifest) -> None:
    """The library's schema is one-way: once the new version adds a table or
    column, the old one can't be trusted to read it after a rollback."""
    wanted = manifest.get("db_schema_sha256")
    if isinstance(wanted, str) and wanted == _library_schema():
        return
    if _recent_backup():
        return
    raise ConflictError("This update changes the library database, and going back to the "
                        "current version afterwards would need a backup. Back up the library "
                        "first (Settings > Notifications and backups > Automatic backups > Back up now), then "
                        "update again.")


def _private_copy(verified, public_key) -> str:
    folder = tempfile.mkdtemp(prefix=ZIP_TEMP_PREFIX)
    copy = os.path.join(folder, verified["name"])
    try:
        shutil.copyfile(verified["path"], copy)
        sha = us._file_sha256(copy)
        ok = hmac.compare_digest(sha, verified["sha256"]) and us._signature_ok(
            public_key, verified["signature"], verified["name"], sha)
    except OSError:
        ok = False
    if not ok:
        shutil.rmtree(folder, ignore_errors=True)
        raise ConflictError("The downloaded update changed, is missing or isn't signed with this "
                            "copy's release key. Download it again.")
    return copy


def _helper_python() -> str:
    exe = os.path.dirname(sys.executable)
    windowed = os.path.join(exe, "pythonw.exe")
    return windowed if os.path.isfile(windowed) else sys.executable


def _start_helper(root) -> None:
    work = app_swap.work_dir(root)
    shutil.copyfile(os.path.join(us._APP_DIR, "installer", app_swap.HELPER_NAME),
                    work / app_swap.HELPER_NAME)
    command = app_swap.helper_command(root, "apply")
    command[0] = _helper_python()
    # Outside the server's Job Object: the helper stops this server.
    with process_guard.breakaway_allowed():
        subprocess.Popen(command, cwd=str(work), env=us._setup_env(),
                         creationflags=us._CREATE_BREAKAWAY_FROM_JOB | us._CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)


def apply() -> dict:
    """Checks and unpacks the verified app zip beside the running app, then
    hands over to the swap helper, which restarts Baihe Studio."""
    global _verified
    if not us._is_windows():
        raise UnsupportedOperationError("Updating the app is only possible on Windows.")
    if not us._is_installed():
        raise UnsupportedOperationError("Updates are for the installed app. A source checkout "
                                        "updates with git.")
    if not launcher_started():
        raise UnsupportedOperationError(
            "Baihe Studio runs as the boot service here, which can't replace its own files. "
            "Use the full installer for this update.")
    with _lock:
        verified = _verified
        downloading = _state["download"] == "downloading"
    if downloading:
        raise ConflictError("Wait for the download to finish.")
    if verified is None:
        raise ConflictError("Download and verify the update first.")
    old = us.current_version()
    if not us.is_newer(verified["version"], old, us.include_prereleases()):
        raise ConflictError("There is no newer version to install. Check for updates first.")
    if background_jobs.active_job_ids():
        raise ConflictError("Finish or cancel the running jobs first: the update restarts the app.")
    root = install_root()
    if app_swap.read_journal(root) is not None:
        raise ConflictError("An earlier app update is still being finished. Restart Baihe "
                            "Studio, then try again.")
    installed = _installed_manifest()
    base = app_swap.base_version(installed) if installed else None
    if base is None or not app_swap.valid_version(old):
        raise ConflictError("This install's manifest.json can't be read. Use the full installer.")
    copy = _private_copy(verified, us._release_public_key())
    dest = app_swap.app_folder(root, verified["version"])
    try:
        try:
            with zipfile.ZipFile(copy) as zf:
                app_swap.check_zip(zf)
                manifest = app_swap.read_manifest(zf)
        except zipfile.BadZipFile:
            raise app_swap.SwapError("The update isn't a readable zip.") from None
        if not _manifest_ok(manifest, verified["version"]) or not _base_matches(manifest):
            raise ConflictError("This update was built for a different Python, packages or "
                                "Caddy than this install has. Use the full installer.")
        _check_database(manifest)
        if dest.exists():
            app_swap.remove_folder(dest, root)
        app_swap.unpack(copy, dest)
    except app_swap.SwapError as exc:
        raise ConflictError(str(exc)) from None
    finally:
        shutil.rmtree(os.path.dirname(copy), ignore_errors=True)
    try:
        # INSTALLED names this PC's data folder; it never ships in the zip.
        shutil.copyfile(os.path.join(us._APP_DIR, "INSTALLED"), dest / "INSTALLED")
        app_swap.write_journal(root, app_swap.STAGED, old, verified["version"], base)
        _start_helper(root)
    except (OSError, ValueError, app_swap.SwapError):
        app_swap.clear_journal(root)
        with contextlib.suppress(OSError, app_swap.SwapError):
            app_swap.remove_folder(dest, root)
        raise DependencyUnavailableError("Baihe couldn't start the update. Nothing was changed; "
                                         "try again, or use the full installer.") from None
    app_swap.log(root, f"staged {verified['version']} over {old}")
    with _lock:
        _verified = None
        _state.update(download="idle", downloaded_bytes=0)
    return {"started": True, "version": verified["version"]}


def cleanup_leftovers() -> None:
    """At startup: private zip copies left by a crash."""
    temp = tempfile.gettempdir()
    with contextlib.suppress(OSError):
        for name in os.listdir(temp):
            path = os.path.join(temp, name)
            if name.startswith(ZIP_TEMP_PREFIX) and os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)

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
- Apply: on the user's click (or, with the opt-in setting, after a check),
  only on Windows, and never while a job runs. A server the Start-menu
  launcher started switches itself, as below. The boot service's account can't
  write the install folder or stop its own service, so there the zip and its
  signature are left in the library's updates folder with a request, and the
  BaiheUpdate service (installer/service_update.py, LocalSystem, started by
  this server and nothing else) checks the signature again against the admin
  folder's release key and does the switch. A private copy of the zip is re-hashed and signature-checked, its
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
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
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
from lib import release_signature
from lib.proc import run_captured
from services import shutdown_service
from services import update_service as us
from lib.errors import (ConflictError, DependencyUnavailableError, ServiceError,
                                     UnsupportedOperationError)

MAX_APP_ZIP_BYTES = 400 * 1024 * 1024
MAX_MANIFEST_BYTES = app_swap.MAX_MANIFEST_BYTES
BACKUP_MAX_AGE_SECONDS = 24 * 3600
ZIP_TEMP_PREFIX = "baihe_appzip_"
# Only the Start-menu launcher gives the server this (installer/launcher.py
# start_server); the boot service never does. The server removes it from its
# environment at startup (shutdown_service.take_token_from_environment), so
# whether it was given one is read from the shutdown service, not os.environ.
LAUNCHER_TOKEN_ENV = "BAIHE_SHUTDOWN_TOKEN"
UPDATER_SERVICE = "BaiheUpdate"      # installer/service_layout.UPDATE_SERVICE
REQUEST_NAME = "app-update-request.json"    # installer/service_update.REQUEST_NAME
SC_TIMEOUT_SECONDS = 20
ADMIN_FOLDER = "Baihe Studio Services"     # installer/service_layout.ADMIN_FOLDER_NAME
SERVICE_AUTO_APPLY_KEY = "update.service_auto_apply"

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

# The library as an app-only update found it, so the version a rollback puts
# back can tell whether the newer one changed the tables before it stopped.
BEFORE_UPDATE_KEY = "app_update.before"
ROLLED_BACK_CODES = ("rolled_back_health", "recovered")
LIBRARY_CHANGED = (" Version {new} had already changed the library's tables before it stopped, "
                   "and {old} may not read those changes correctly.")
LIBRARY_UNKNOWN = " Couldn't tell whether the newer version changed the library."
RESTORE_BACKUP = (" To be safe, restore the backup from before the update "
                  "(Library tools > Backup > Restore).")

_lock = threading.Lock()
# offer: "none" | "available" | "needs_full_installer"
_state = {"offer": "none", "offer_version": None, "size": None, "download": "idle",
          "downloaded_bytes": 0, "download_error": None}
_release = None     # internal: the release's "app" record plus "version"
# (version, db_schema_sha256) from the last manifest copy read, for the full
# installer's backup check: it has no manifest of its own.
_offer_schema = None
_verified = None    # internal: {"version", "name", "path", "sha256", "signature"}
_download_thread = None
_applying = False   # one apply at a time: a second would delete the first's staged folder


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
    if base is None or manifest.get("base") != base or version is None or not isinstance(minimum, str):
        return False
    have, need = us.parse_version(version), us.parse_version(minimum)
    return have is not None and need is not None and have >= need


def _manifest_ok(manifest, version) -> bool:
    return (isinstance(manifest, dict) and manifest.get("product") == app_swap.PRODUCT
            and manifest.get("layer") == "app" and manifest.get("version") == version)


def refresh_offer(release) -> None:
    """Called after each check with the release it found (or None). Reads
    the release's manifest copy; any failure means no app-only offer."""
    global _release, _verified, _offer_schema
    app = (release or {}).get("app")
    offer, size, schema = "none", None, None
    if app is not None and us._is_installed():
        try:
            data = us._read_capped(us._open(app["manifest_url"], us.DOWNLOAD_HOSTS,
                                            "application/octet-stream"), MAX_MANIFEST_BYTES)
            manifest = json.loads(data.decode("utf-8"))
        except (ServiceError, ValueError, OSError, us.requests.RequestException):
            manifest = None
        if isinstance(manifest, dict) and _manifest_ok(manifest, release["version"]):
            offer = "available" if _base_matches(manifest) else "needs_full_installer"
            size = app["size"]
            schema = (release["version"], manifest.get("db_schema_sha256"))
    with _lock:
        _offer_schema = schema
        _release = (dict(app, version=release["version"])
                    if offer == "available" and app is not None else None)
        if _verified is not None and (_release is None
                                      or _release["version"] != _verified["version"]):
            _verified = None
            if _state["download"] != "downloading":
                _state.update(download="idle", downloaded_bytes=0, download_error=None)
        _state.update(offer=offer, offer_version=release["version"] if release else None,
                      size=size)
    _auto_apply_step()


def launcher_started() -> bool:
    return shutdown_service.enabled()


def _system32(exe: str) -> str:
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", exe)


def updater_present() -> bool:
    """Whether this PC's boot service has the BaiheUpdate helper: Setup's
    service step (from this version on) puts its wrapper in the admin folder,
    which the service account can read. A file check, not a service query, so
    a status poll stays cheap; starting a missing service fails with a plain
    message."""
    if not us._is_windows() or launcher_started():
        return False
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return os.path.isfile(os.path.join(program_files, ADMIN_FOLDER, "service", UPDATER_SERVICE + ".exe"))


def service_auto_apply() -> bool:
    """The owner's opt-in (off by default) to download and apply app-only
    updates by themselves on the boot service."""
    from services import settings_service
    try:
        return settings_service.get(SERVICE_AUTO_APPLY_KEY) is True
    except Exception:
        return False


def set_service_auto_apply(enabled: bool) -> None:
    db.set_app_setting(SERVICE_AUTO_APPLY_KEY, bool(enabled))


def can_switch_here() -> bool:
    """This server can get an app update applied: it switches itself
    (launcher) or the BaiheUpdate helper does it for it (boot service)."""
    return launcher_started() or updater_present()


def last_result():
    """The last swap's outcome in plain words, or None."""
    result = app_swap.read_result(install_root()) if us._is_installed() else None
    if result is None:
        return None
    check = _rollback_library_check(result)
    message = RESULT_MESSAGES[result["code"]].format(old=result["old"], new=result["new"])
    if check["state"] == "changed":
        message += LIBRARY_CHANGED.format(old=result["old"], new=check["changed_by"])
    elif check["state"] == "unknown":
        message += LIBRARY_UNKNOWN
    if check["backup_at"]:
        message += RESTORE_BACKUP
    return {"outcome": result["code"], "at": result["at"],
            "library_changed": check["state"] == "changed",
            "library_unknown": check["state"] == "unknown",
            "backup_at": check["backup_at"], "message": message}


def _remember_library(old, new) -> None:
    previous = db.get_app_setting(BEFORE_UPDATE_KEY, None)
    result = app_swap.read_result(install_root()) if us._is_installed() else None
    if (isinstance(previous, dict) and result is not None
            and _rollback_library_check(result)["state"] == "changed"):
        # The library is still the one an earlier rolled-back version changed.
        # This attempt must not replace the record from before that change,
        # or a failure that writes no result of its own would hide the warning.
        previous["attempts"] = [*previous["attempts"], new]
        db.set_app_setting(BEFORE_UPDATE_KEY, previous)
        return
    library, sources = _library_layout()
    db.set_app_setting(BEFORE_UPDATE_KEY, {
        "old": old, "attempts": [new], "library": library, "sources": sources,
        **_newest_backup()})


def _newest_backup() -> dict:
    """The newest readable backup copy right now, by name, so a later check
    can tell whether rotation has since deleted it."""
    from services import auto_backup_service
    try:
        copies = auto_backup_service.settings_overview().get("copies") or []
    except (ServiceError, OSError, AttributeError):
        copies = []
    for c in copies:
        if _restorable_copy(c) and isinstance(c.get("name"), str):
            return {"backup_name": c["name"], "backup_at": c.get("created_at")}
    return {"backup_name": None, "backup_at": None}


def _backup_still_there(name) -> bool:
    from services import auto_backup_service
    try:
        copies = auto_backup_service.settings_overview().get("copies") or []
    except (ServiceError, OSError, AttributeError):
        return False
    return any(_restorable_copy(c) and c.get("name") == name for c in copies)


def _restorable_copy(c) -> bool:
    """A readable copy of this library itself: not another library's copy in a
    shared folder, nor one kept only for its sources.db."""
    return (isinstance(c, dict) and bool(c.get("readable")) and c.get("managed") is True
            and c.get("kept_as") != "sources")


def _library_layout():
    """(library, sources): hashes of every table, index and trigger definition
    in library.db and in sources.db, sources None when it can't be read.
    Stricter than the release fingerprint (names only), so a table rebuilt
    under the same column names, or a new index, still counts as a change.
    A migration that only rewrites rows is not seen."""
    from sources import store
    with contextlib.closing(db.get_conn()) as conn:
        library = _definitions(conn)
    # connect() creates and migrates sources.db on first use, so it exists by
    # the time either side of the comparison is taken; a damaged one makes the
    # answer unknown rather than blocking the update.
    try:
        with contextlib.closing(store.connect()) as conn:
            sources = _definitions(conn)
    except (sqlite3.Error, OSError):
        sources = None
    return library, sources


def _definitions(conn) -> str:
    rows = sorted(repr(tuple(r)) for r in conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master"))
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _rollback_library_check(result) -> dict:
    """{state, backup_at, changed_by} for a rolled-back update. state is
    "changed" when a version that was rolled back left the library different
    from how the first update attempt since found it, "unknown" when that
    can't be told (no record of that update, or a database that couldn't be
    read), else "unchanged"; None when the result isn't a rollback. When
    changed, backup_at is when the backup from before the update was taken,
    if that copy is still there, and changed_by is that first attempt's
    version."""
    none = {"state": None, "backup_at": None, "changed_by": None}
    if result["code"] not in ROLLED_BACK_CODES:
        return none
    try:
        before = db.get_app_setting(BEFORE_UPDATE_KEY, None)
        if not (isinstance(before, dict) and before.get("old") == result["old"]
                and result["new"] in (before.get("attempts") or [])
                and isinstance(before.get("library"), str)):
            return {**none, "state": "unknown"}
        library, sources = _library_layout()
    except sqlite3.Error:
        return {**none, "state": "unknown"}
    if library != before.get("library") or (
            sources is not None and before.get("sources") is not None
            and sources != before.get("sources")):
        backup_at = before.get("backup_at")
        if not (isinstance(backup_at, str) and _backup_still_there(before.get("backup_name"))):
            backup_at = None
        return {"state": "changed", "backup_at": backup_at, "changed_by": before["attempts"][0]}
    if sources is None or before.get("sources") is None:
        return {**none, "state": "unknown"}
    return none | {"state": "unchanged"}


def status_fields() -> dict:
    current = us.current_version()
    with _lock:
        s = dict(_state)
        verified = _verified is not None
        verified_version = _verified["version"] if _verified is not None else None
    newer = bool(s["offer_version"] and current
                 and us.is_newer(s["offer_version"], current, us.include_prereleases()))
    offer = s["offer"] if newer else "none"
    result = last_result()
    return {"app_update": offer,
            "app_update_here": us._is_windows() and can_switch_here(),
            "app_update_by_service": updater_present(),
            "app_auto_apply": service_auto_apply(),
            "app_size": s["size"] if offer != "none" else None,
            "app_download": s["download"], "app_downloaded_bytes": s["downloaded_bytes"],
            "app_download_error": s["download_error"], "app_verified": verified,
            "app_verified_version": verified_version,
            "can_apply_app": (verified and offer == "available" and us._is_windows()
                              and can_switch_here() and s["download"] != "downloading"),
            "app_last_result": result["message"] if result else None,
            "app_last_result_at": result["at"] if result else None,
            "app_rollback_library_changed": bool(result and result["library_changed"]),
            "app_rollback_library_unknown": bool(result and result["library_unknown"]),
            "app_rollback_backup_at": result["backup_at"] if result else None}


# --- download -----------------------------------------------------------------

def _clear_old_zips(folder) -> None:
    for name in os.listdir(folder):
        if name.startswith("BaiheStudio-App-") and name.endswith((".zip", ".part", ".sig")):
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
        return
    _auto_apply_step()


def _auto_apply_step() -> None:
    """With the opt-in on and the BaiheUpdate helper present: after a check,
    download the app-only update, and once it is verified apply it. Each step
    that can't go ahead now (a job is running, a backup is needed) is left
    for the next check, which runs this again; nothing here raises."""
    if not (service_auto_apply() and updater_present()):
        return
    with _lock:
        offered = _release is not None
        state, held = _state["download"], _verified is not None
    if not offered:
        return
    try:
        if state in ("idle", "failed") and not held:
            start_download()
        elif state == "verified" and held:
            apply()
    except ServiceError as exc:
        app_swap.log(install_root(), f"automatic update waits: {type(exc).__name__}")
    except OSError:
        pass


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
    raise ConflictError(_BACKUP_FIRST)


def check_database_before_setup(version) -> None:
    """The same rule for the full installer, which upgrades the same library.
    Without the release's manifest copy Baihe can't tell whether the schema
    changes, so it asks for the backup."""
    with _lock:
        known = _offer_schema
    if known is not None and known[0] == version:
        _check_database({"db_schema_sha256": known[1]})
    elif not _recent_backup():
        raise ConflictError(_BACKUP_FIRST_UNKNOWN)


_BACKUP_STEPS = ("Back up the library first (Settings > Notifications and backups > Automatic "
                 "backups > Back up now), then update again.")
_BACKUP_FIRST = ("This update changes the library database, and going back to the current version "
                 "afterwards would need a backup. " + _BACKUP_STEPS)
_BACKUP_FIRST_UNKNOWN = ("This update may change the library database, and going back to the "
                         "current version afterwards would need a backup. " + _BACKUP_STEPS)


def _private_copy(verified, public_key) -> str:
    folder = tempfile.mkdtemp(prefix=ZIP_TEMP_PREFIX)
    copy = os.path.join(folder, verified["name"])
    try:
        shutil.copyfile(verified["path"], copy)
        sha = us._file_sha256(copy)
        ok = hmac.compare_digest(sha, verified["sha256"]) and us._signature_ok(
            public_key, verified["signature"], verified["name"], sha)
    except OSError as exc:
        problem = us.copy_problem(exc)
        if problem is not None:
            shutil.rmtree(folder, ignore_errors=True)
            raise DependencyUnavailableError(problem) from None
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
    # Unlike Setup's, the helper's environment keeps the user's *_KEY and
    # *_TOKEN variables: the server it restarts would otherwise lose them.
    # Only this server's one-time shutdown token and job name are dropped.
    env = {k: v for k, v in os.environ.items() if k.upper() not in us._SETUP_ENV_DROP}
    # Outside the server's Job Object: the helper stops this server.
    with process_guard.breakaway_allowed():
        subprocess.Popen(command, cwd=str(work), env=env,
                         creationflags=us._CREATE_BREAKAWAY_FROM_JOB | us._CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)


def _server_port():
    try:
        port = int(os.environ.get("BAIHE_API_PORT", ""))
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def apply_busy() -> bool:
    """Whether an apply is under way or handed over and not yet finished (its
    journal is still there): the full Setup must not start meanwhile. A
    queued switch is the exception: the helper that queued it has exited,
    the launcher won't start another while Setup is open, and Setup deletes the journal, so a locked file must not leave the full
    installer refused."""
    with _lock:
        if _applying:
            return True
    journal = app_swap.read_journal(install_root())
    return journal is not None and journal["state"] != app_swap.QUEUED


def apply() -> dict:
    """Checks and unpacks the verified app zip beside the running app, then
    hands over to the swap helper, which restarts Baihe Studio. One at a
    time, and not while the full Setup is starting or open: a second click
    or tab is refused until the first has handed over or failed."""
    global _applying
    with _lock:
        if _applying:
            raise ConflictError("An app update is already starting.")
        _applying = True
    if us.setup_busy():
        with _lock:
            _applying = False
        raise ConflictError(us.SETUP_OPEN)
    try:
        return _apply()
    finally:
        with _lock:
            _applying = False


_START_FAILED = ("Baihe couldn't start the update. Nothing was changed; try again, or use the "
                 "full installer.")


def _apply() -> dict:
    global _verified
    if not us._is_windows():
        raise UnsupportedOperationError("Updating the app is only possible on Windows.")
    if not us._is_installed():
        raise UnsupportedOperationError("Updates are for the installed app. A source checkout "
                                        "updates with git.")
    by_service = not launcher_started()
    if by_service and not updater_present():
        raise UnsupportedOperationError(
            "Baihe Studio runs as the boot service here, which can't replace its own files, "
            "and this copy has no app-update helper yet. Run the full installer once (it adds "
            "the helper); app updates can then apply here.")
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
    if by_service:
        return _hand_to_updater(verified, installed)
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
    except OSError as exc:
        # unpack() already removed its partial folder. A file an antivirus
        # scan holds, or a full disk, is the usual cause.
        raise DependencyUnavailableError(us.copy_problem(exc) or _START_FAILED) from None
    finally:
        shutil.rmtree(os.path.dirname(copy), ignore_errors=True)
    try:
        # INSTALLED names this PC's data folder; it never ships in the zip.
        shutil.copyfile(os.path.join(us._APP_DIR, "INSTALLED"), dest / "INSTALLED")
        _remember_library(old, verified["version"])
        app_swap.write_journal(root, app_swap.STAGED, old, verified["version"], base,
                               port=_server_port())
        _start_helper(root)
    except (OSError, ValueError, sqlite3.Error, app_swap.SwapError):
        app_swap.clear_journal(root)
        with contextlib.suppress(OSError, app_swap.SwapError):
            app_swap.remove_folder(dest, root)
        raise DependencyUnavailableError(_START_FAILED) from None
    app_swap.log(root, f"staged {verified['version']} over {old}")
    with _lock:
        _verified = None
        _state.update(download="idle", downloaded_bytes=0)
    return {"started": True, "version": verified["version"]}


def _hand_to_updater(verified, installed) -> dict:
    """Boot service: checks what it can here for an early, plain refusal, then
    leaves the request and starts BaiheUpdate. It trusts none of this: it
    verifies the signature, the base and the version again, as LocalSystem."""
    global _verified
    if verified["signature"] is None:
        raise ConflictError("This update isn't signed, and the boot service only applies signed "
                            "updates. Use the full installer.")
    try:
        with zipfile.ZipFile(verified["path"]) as zf:
            app_swap.check_zip(zf)
            manifest = app_swap.read_manifest(zf)
    except (zipfile.BadZipFile, OSError):
        raise ConflictError("The downloaded update can't be read. Download it again.") from None
    except app_swap.SwapError as exc:
        raise ConflictError(str(exc)) from None
    if not _manifest_ok(manifest, verified["version"]) or not _base_matches(manifest):
        raise ConflictError("This update was built for a different Python, packages or "
                            "Caddy than this install has. Use the full installer.")
    _check_database(manifest)
    try:
        _remember_library(us.current_version(), verified["version"])
    except sqlite3.Error:
        raise DependencyUnavailableError(_START_FAILED) from None
    folder = us.updates_dir()
    try:
        with open(os.path.join(folder, verified["name"] + ".sig"), "w", encoding="ascii") as fh:
            fh.write(release_signature.format_signature(verified["signature"]))
        request = os.path.join(folder, REQUEST_NAME)
        with open(request + ".part", "w", encoding="utf-8") as fh:
            json.dump({"version": verified["version"], "name": verified["name"]}, fh)
        os.replace(request + ".part", request)
    except OSError as exc:
        raise DependencyUnavailableError(us.copy_problem(exc) or _START_FAILED) from None
    try:
        started = run_captured([_system32("sc.exe"), "start", UPDATER_SERVICE], SC_TIMEOUT_SECONDS)
    except OSError:
        started = None
    if started is None or started.returncode != 0:
        with contextlib.suppress(OSError):
            os.remove(request)
        raise DependencyUnavailableError(_START_FAILED)
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

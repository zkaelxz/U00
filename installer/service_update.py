"""The boot service's app-only update, run elevated by the BaiheUpdate service.

The server (NT SERVICE\\BaiheStudio) can neither write the install folder nor
stop its own service, so it only leaves a request in the data folder and starts
BaiheUpdate. This module trusts nothing in that request except the zip and
signature it names: the signature is checked here, against the release key in
the admin folder, with the pure-Python verifier, and the switch is
installer/app_swap.py driven through the service manager instead of the
Start-menu launcher.

Standard library only: this runs as LocalSystem from the admin folder.
"""

import base64
import binascii
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import zipfile
from pathlib import Path

from installer import app_swap, ed25519_verify
from installer.service_checks import health_ok, wait_until
from installer.service_lock import (ADMINISTRATORS_SID, OWNER_SIDS, SYSTEM_SID, hold_directory, locked_problem, owner_sid,
                                    security_sddl, write_access_problem)
from installer.service_control import query_state, start_service, stop_service
from installer.service_layout import APP_SERVICE, ServiceError, is_reparse_point, refuse_reparse_point

REQUEST_NAME = "app-update-request.json"
RESTART_REQUEST_NAME = "package-restart-request.json"
RESTART_RESULT_NAME = "package-restart-result.json"   # in the locked install's app-update folder
PACKAGE_WORK_NAME = "package-install"
# pending_install.py's files: a spent restart is only worth doing for a queued install.
PENDING_DIR, PENDING_FILE, PENDING_RESULT = "pending_install", "pending.json", "result.json"
MAX_ZIP_BYTES = 400 * 1024 * 1024
MAX_SMALL_FILE_BYTES = 4096
HEALTH_WAIT_SECONDS = 300      # a new version may compile its .pyc files first
STOP_WAIT_SECONDS = 120
CHUNK = 1024 * 1024
KEY_PREFIX = "ed25519-public:"
MESSAGE_HEADER = "baihe-studio-release-signature-v1"
_NAME_RE = re.compile(r"BaiheStudio-App-(.+)\.zip", re.DOTALL)
_VERSION_PARTS = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+.*)?")

OUTCOMES = {
    "not_started": "The update didn't start; the current version kept running.",
    "rolled_back_health": "The new version didn't start, so the previous version was put back.",
    "rolled_back_switch": "The app folder couldn't be switched; the previous version was kept.",
    "rolled_back_packages": ("The new version's Python packages couldn't be installed, so the "
                             "previous version was put back."),
    "broken": "The update couldn't be finished or undone. Run Setup again to repair it.",
}


def version_key(text):
    """Orders versions as services/update_service.parse_version does (a
    pre-release sorts before its release, beta.2 before beta.10); None for
    anything else."""
    m = _VERSION_PARTS.fullmatch(str(text or "").strip())
    if not m:
        return None
    pre = m.group(4)
    pre_key = tuple((0, int(part), "") if part.isdigit() else (1, 0, part)
                    for part in pre.split(".")) if pre else ()
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), pre is None, pre_key)


def signed_message(name: str, sha256_hex: str) -> bytes:
    """The text the release is signed over (lib/release_signature.signed_message);
    tests/test_service_update.py pins that the two agree."""
    return f"{MESSAGE_HEADER}\nname: {name}\nsha256: {sha256_hex}\n".encode("ascii")


def _b64(text: str, size: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise ServiceError(f"The release {what} isn't valid base64.") from None
    if len(raw) != size:
        raise ServiceError(f"The release {what} has the wrong length.")
    return raw


def read_release_key(path) -> bytes:
    """The admin copy's release key. Unlike the app, which accepts an unsigned
    release when it has no key, the elevated updater refuses without one."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ServiceError("This copy's release key can't be read, so it can't check "
                           "updates. Run Setup again.") from None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if len(lines) != 1 or not lines[0].startswith(KEY_PREFIX):
        raise ServiceError("This copy has no usable release key, so it can't check updates. "
                           "Use the full installer.")
    return _b64(lines[0][len(KEY_PREFIX):], 32, "key")


def _open_data_file(updates, name: str, cap: int):
    """Opens a regular, unlinked file in the data folder's updates folder.
    The service account can create links there, so a link anywhere on the way
    is refused, and the checks run on the open handle (size, a single hard
    link) so the file can't be swapped between checking and reading."""
    for part in (updates.parent, updates):
        if is_reparse_point(part):
            raise ServiceError("The update folder is a link; refusing to read through it.")
    path = updates / name
    if is_reparse_point(path):
        raise ServiceError(f"The update request names a file that isn't there ({name}).")
    try:
        fh = open(path, "rb")
    except OSError:
        raise ServiceError(f"The update request names a file that isn't there ({name}).") from None
    info = os.fstat(fh.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        fh.close()
        raise ServiceError(f"The update request names a file that isn't a plain file ({name}).")
    if info.st_size > cap:
        fh.close()
        raise ServiceError(f"The update request names a file that is too large ({name}).")
    return fh


def _read_data_file(updates, name: str, cap: int) -> bytes:
    with _open_data_file(updates, name, cap) as fh:
        return fh.read(cap + 1)[:cap]


def read_request(updates) -> tuple:
    """(version, zip name) from the request the server wrote."""
    try:
        doc = json.loads(_read_data_file(updates, REQUEST_NAME, MAX_SMALL_FILE_BYTES))
    except (OSError, ValueError):
        raise ServiceError("The update request can't be read.") from None
    name = doc.get("name") if isinstance(doc, dict) else None
    m = _NAME_RE.fullmatch(name) if isinstance(name, str) else None
    if m is None or not app_swap.valid_version(m.group(1)) or doc.get("version") != m.group(1):
        raise ServiceError("The update request doesn't name a release file.")
    return m.group(1), name


def _stage(updates, name: str, dest: Path) -> str:
    """Copies the zip into the admin-only folder, hashing as it goes; every
    later check and the unpack read this copy, so the service account
    can't change the file after it was checked."""
    sha, total = hashlib.sha256(), 0
    with _open_data_file(updates, name, MAX_ZIP_BYTES) as inp, open(dest, "wb") as out:
        while chunk := inp.read(CHUNK):
            total += len(chunk)
            if total > MAX_ZIP_BYTES:
                raise ServiceError("The update file is larger than an app update can be.")
            sha.update(chunk)
            out.write(chunk)
    return sha.hexdigest()


class ServiceLauncher:
    """The Launcher interface installer/app_swap.apply drives, but through
    the service manager: there is no window to open and no launcher to run."""

    def __init__(self, run, port, health=health_ok, sleep=time.sleep):
        self.run, self.port, self.health, self.sleep = run, port, health, sleep

    def stop(self) -> bool:
        try:
            stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        except ServiceError:
            return False
        # Nothing may answer on the port, so whatever answers after the
        # switch is the new server.
        return wait_until(lambda: not self.health(self.port), STOP_WAIT_SECONDS, sleep=self.sleep)

    def start_and_check(self) -> bool:
        try:
            start_service(APP_SERVICE, self.run, sleep=self.sleep)
        except ServiceError:
            return False
        return wait_until(lambda: self.health(self.port), HEALTH_WAIT_SECONDS, sleep=self.sleep)

    def start_quietly(self, defer=False) -> None:
        self.open_window(defer)

    def open_window(self, defer=False) -> None:
        """Called wherever the old version should be running again."""
        try:
            if query_state(APP_SERVICE, self.run) == "STOPPED":
                start_service(APP_SERVICE, self.run, sleep=self.sleep)
        except ServiceError:
            pass


def _check_zip(copy: Path, installed, version: str, key: bytes, sha: str, name: str,
               signature: bytes) -> dict:
    if not ed25519_verify.verify(key, signature, signed_message(name, sha)):
        raise ServiceError("The update isn't signed with this copy's release key. Nothing "
                           "was changed.")
    try:
        with zipfile.ZipFile(copy) as zf:
            app_swap.check_zip(zf)
            manifest = app_swap.read_manifest(zf)
    except zipfile.BadZipFile:
        raise ServiceError("The update isn't a readable zip.") from None
    except app_swap.SwapError as exc:
        raise ServiceError(str(exc)) from None
    minimum = version_key(manifest.get("min_base_version"))
    base = app_swap.base_version(installed)
    if (manifest.get("version") != version or not app_swap.base_accepts(manifest, installed)
            or minimum is None or version_key(base) is None or version_key(base) < minimum):
        raise ServiceError("This update was built for a different Python, WinSW or Caddy "
                           "than this install has. Use the full installer.")
    return manifest


def _settle_earlier_update(root, run, sleep) -> None:
    """The service never runs launcher.py, which is what settles an update
    that was cut short, so this does: done is finished, anything earlier is
    rolled back, and a queued switch is dropped. Refuses when the folders
    don't say what happened."""
    if app_swap.read_journal(root) is None:
        return
    code = app_swap.recover(root, stop=lambda: stop_service(APP_SERVICE, run, sleep=sleep),
                            sleep=sleep)
    if code == "queued":
        app_swap.clear_journal(root)
        code = "not_started"
    if code in ("busy", "broken") or app_swap.read_journal(root) is not None:
        raise ServiceError("An earlier app update is still unfinished and couldn't be settled. "
                           "Run Setup again to repair it.")
    try:
        if query_state(APP_SERVICE, run) == "STOPPED":
            start_service(APP_SERVICE, run, sleep=sleep)
    except ServiceError:
        pass


def apply_update(layout, run, port, health=health_ok, sleep=time.sleep, owner=owner_sid,
                 hold=hold_directory, sddl=None) -> str:
    """Verifies the requested app zip and switches the service to it. Raises
    ServiceError with the reason when nothing changed or the previous
    version was put back; the outcome is also in app-update\\result.json,
    which the app reads."""
    root, updates = layout.root, layout.data / "library" / "updates"
    if _restart_requested(layout) and _has_package_work(layout, owner, sddl):
        return restart_for_packages(layout, run, port, health, sleep, owner=owner, hold=hold,
                                    sddl=sddl)
    # Held first and for the whole update: the folder's parent may belong to
    # the user, who could otherwise rename the locked folder away and plant a
    # look-alike after the checks below.
    with hold(root):
        for path in (root, root / "app", root / "manifest.json", app_swap.work_dir(root)):
            refuse_reparse_point(path)
        # LocalSystem writes into this folder: only when Setup's lock holds can no
        # process running as the user be swapping it underneath.
        problem = locked_problem(layout, owner, sddl)
        if problem:
            raise ServiceError(problem)
        installed = app_swap.installed_manifest(root)
        old = installed.get("app_version") if installed else None
        if not app_swap.valid_version(old) or app_swap.base_version(installed) is None:
            raise ServiceError("This install's manifest.json can't be read. Run Setup again.")
        _settle_earlier_update(root, run, sleep)
        version, name = read_request(updates)
        return _apply_requested(layout, run, port, health, sleep, installed, old, version, name)


def _apply_requested(layout, run, port, health, sleep, installed, old, version, name) -> str:
    root, updates = layout.root, layout.data / "library" / "updates"
    request = updates / REQUEST_NAME
    # The request is spent whatever happens next, so a failure isn't retried
    # by a later start of the updater.
    try:
        request.unlink()
    except OSError:
        pass
    try:
        new_key, old_key = version_key(version), version_key(old)
        if new_key is None or old_key is None or new_key <= old_key:
            raise ServiceError(f"{version} isn't newer than the installed {old}.")
        if not new_key[3] and old_key[3]:
            # The service account can write a request itself, so the owner's
            # pre-release switch (app side) can't be what keeps a beta off a
            # stable install.
            raise ServiceError(f"{version} is a pre-release and the installed {old} isn't.")
        try:
            sig_text = "".join(_read_data_file(updates, name + ".sig", MAX_SMALL_FILE_BYTES)
                               .decode("ascii").split())
        except UnicodeDecodeError:
            raise ServiceError("The release signature isn't text.") from None
        signature = _b64(sig_text, 64, "signature")
        key = read_release_key(layout.public_key)
        staging = layout.staging
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        dest = app_swap.app_folder(root, version)
        try:
            copy = staging / name
            sha = _stage(updates, name, copy)
            _check_zip(copy, installed, version, key, sha, name, signature)
            if dest.exists() or app_swap.is_link(dest):
                app_swap.remove_folder(dest, root)
            app_swap.unpack(copy, dest)
            marker = root / "app" / "INSTALLED"
            if marker.is_file():
                shutil.copyfile(marker, dest / "INSTALLED")
            app_swap.work_dir(root).mkdir(parents=True, exist_ok=True)
            app_swap.write_journal(root, app_swap.STAGED, old, version,
                                   app_swap.base_version(installed), port=port)
        except app_swap.SwapError as exc:
            raise ServiceError(str(exc)) from None
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    except (ServiceError, OSError):
        # Nothing was switched: leave the app a line to show instead of silence.
        with contextlib.suppress(OSError, app_swap.SwapError):
            app_swap.work_dir(root).mkdir(parents=True, exist_ok=True)
            app_swap.write_result(root, "not_started", old, version)
        raise
    app_swap.log(root, f"service staged {version} over {old}")
    launcher = ServiceLauncher(run, port, health, sleep)
    code = app_swap.apply(root, launcher, sleep, notify=lambda text: run.log(text), recovery=False)
    if code == "queued":
        # A queued switch is finished by the Start-menu launcher's next start,
        # which the service never has; forget it (a crash before this line is
        # settled by _settle_earlier_update at the next run).
        app_swap.clear_journal(root)
        app_swap.write_result(root, "rolled_back_switch", old, version)
        code = "rolled_back_switch"
    if code == "updated":
        return f"Updated Baihe Studio's service from {old} to {version}."
    if code == "updated_unrecorded":
        return (f"Updated Baihe Studio's service from {old} to {version}, but the version "
                "could not be recorded.")
    raise ServiceError(OUTCOMES.get(code, f"The update ended with {code}."))


def record_refusal(layout, run) -> None:
    """The update was refused before it began (Setup's lock): spend the request
    and leave the app a "didn't start" line, as every other early refusal does,
    so the owner isn't left with a helper that exited and no word in the app."""
    root, updates = layout.root, layout.data / "library" / "updates"
    installed = app_swap.installed_manifest(root)
    old = installed.get("app_version") if installed else None
    try:
        version, _ = read_request(updates)
    except ServiceError:
        return
    with contextlib.suppress(OSError):
        (updates / REQUEST_NAME).unlink()
    if not app_swap.valid_version(old):
        return
    with contextlib.suppress(OSError, app_swap.SwapError):
        app_swap.work_dir(root).mkdir(parents=True, exist_ok=True)
        app_swap.write_result(root, "not_started", old, version)


MAX_QUEUED_KEYS = 40
INSTALL_SECONDS = 3600 + 300      # pending_install's own overall limit plus its watchdog margin
_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")


def _queued_install(layout):
    """(raw file bytes, keys, created) of the queued install, or None when
    nothing valid is queued. Only the count and shape are checked here:
    pending_install re-derives every pip argument from the app's registry, so
    nothing taken from this file reaches a command line. The bytes are copied
    into the admin-only work folder: the data folder is writable by the user,
    so LocalSystem never works in it."""
    data = layout.data
    folder = data / PENDING_DIR
    if any(is_reparse_point(p) for p in (data, data / "library", folder, folder / PENDING_FILE)):
        return None
    try:
        raw = _read_data_file(folder, PENDING_FILE, MAX_SMALL_FILE_BYTES * 16)
        doc = json.loads(raw)
    except (ServiceError, OSError, ValueError):
        return None
    payload = doc.get("payload") if isinstance(doc, dict) else None
    keys = payload.get("packages") if isinstance(payload, dict) else None
    created = payload.get("created") if isinstance(payload, dict) else None
    if (not isinstance(keys, list) or not 0 < len(keys) <= MAX_QUEUED_KEYS
            or not all(isinstance(k, str) and _KEY_RE.fullmatch(k) for k in keys)
            or not isinstance(created, int) or isinstance(created, bool)):
        return None
    return raw, keys, created


def _install_env(layout, state_root) -> dict:
    """Built from nothing: no PIP_*, PYTHONPATH or proxy setting of the user
    or the service account reaches pip. Wheels only, so no package runs a
    build script as LocalSystem."""
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    return {"SystemRoot": windir, "PATH": windir + r"\System32", "TEMP": windir + r"\Temp",
            "TMP": windir + r"\Temp", "BAIHE_DATA_DIR": str(state_root),
            # No machine-wide or user pip.ini (find-links, target=, log=) may steer a
            # LocalSystem pip; --isolated would also drop PIP_ONLY_BINARY.
            "PIP_CONFIG_FILE": os.devnull, "PYTHONNOUSERSITE": "1", "PIP_ONLY_BINARY": ":all:", "PIP_NO_INPUT": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONUTF8": "1"}


def _run_install_step(layout, run, argv, seconds, runner, state_root) -> int:
    try:
        done = runner([str(layout.python_exe), "-s", *argv], cwd=str(layout.app),
                      env=_install_env(layout, state_root), capture_output=True, text=True,
                      errors="replace", timeout=seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        run.log(f"package install step {argv[:2]}: {type(exc).__name__}")
        return -1
    tail = (done.stdout + done.stderr).strip()[-600:]
    run.log(f"package install step {' '.join(argv[:2])}: exit {done.returncode}"
            + (f": {tail}" if tail else ""))
    return done.returncode


def _restart_requested(layout) -> bool:
    """Whether a package-restart request is there (only looked at, never
    deleted: LocalSystem doesn't delete by path in a folder the user can
    redirect; the app removes its own request once the result names it).
    The path is used only when no folder on the way is a link."""
    updates = layout.data / "library" / "updates"
    path = updates / RESTART_REQUEST_NAME
    if any(is_reparse_point(p) for p in (layout.data, layout.data / "library", updates, path)):
        return False
    return path.is_file()


def _already_handled(layout, created) -> bool:
    """The last run was for this very queued install (its request is still lying
    there because the app hasn't removed it yet), so it isn't run again."""
    try:
        doc = json.loads((app_swap.work_dir(layout.root) / RESTART_RESULT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and doc.get("queued_created") == created


def _has_package_work(layout, owner, sddl) -> bool:
    """A valid queue the helper hasn't run yet, in an install folder it would be
    allowed to install into. Otherwise the start is for an app update (or nothing),
    so a request that can't be served never keeps an update from being applied."""
    queued = _queued_install(layout)
    return (queued is not None and not _already_handled(layout, queued[2])
            and _install_folder_problem(layout, owner, sddl) is None)


def _outcome_from(state_root, since) -> dict:
    """Counts from the result pending_install wrote in the admin-only work folder,
    when it finished during this run (an earlier run's result stays there on
    purpose: it is what tells the next run that a restore is owed)."""
    try:
        doc = json.loads((state_root / PENDING_DIR / PENDING_RESULT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    finished = doc.get("finished") if isinstance(doc, dict) else None
    if not isinstance(finished, int) or finished < since:
        return {}
    packages, restored, failed = doc.get("packages"), doc.get("restored"), doc.get("restore_failed")
    status = doc.get("status")
    return {"install_status": status if isinstance(status, str) and len(status) < 32 else None,
            "packages": len(packages) if isinstance(packages, list) else 0,
            "restored": len(restored) if isinstance(restored, list) else 0,
            "restore_failed": len(failed) if isinstance(failed, list) else 0}


def _write_restart_result(layout, outcome: str, seconds: int, created=None, **extra) -> None:
    """In the locked install's app-update folder (the app reads it there, like an
    update's result), never in the data folder the user can redirect."""
    work = app_swap.work_dir(layout.root)
    path = work / RESTART_RESULT_NAME
    try:
        work.mkdir(parents=True, exist_ok=True)
        with open(str(path) + ".part", "w", encoding="utf-8") as fh:
            json.dump({"outcome": outcome, "downtime_seconds": seconds, "at": int(time.time()),
                       "queued_created": created, **extra}, fh)
        os.replace(str(path) + ".part", path)
    except OSError as exc:
        # Unrecorded, the app would queue the same install again.
        raise ServiceError(f"The package install's outcome couldn't be recorded ({outcome}): {exc}") from None


def _install_folder_problem(layout, owner, sddl) -> str | None:
    """LocalSystem writes into the Python's package folder, so, as for an app
    update, only a folder Setup locked to administrators and that nobody has taken
    back is written to: otherwise the account that could swap its contents
    would run code as LocalSystem."""
    root = layout.root
    python = Path(root) / "python"
    site = python / "Lib" / "site-packages"
    for path in (root, root / "app", python, site, app_swap.work_dir(root)):
        if is_reparse_point(path):
            return f"{path} is a link; refusing to install packages through it."
    problem = locked_problem(layout, owner, sddl)
    if problem:
        return problem
    read = security_sddl if sddl is None else sddl
    # Not the app's own service account, which locked_problem trusts for reading.
    trusted = frozenset({"SY", "BA", SYSTEM_SID, ADMINISTRATORS_SID})
    for path in (python, site):
        if not os.path.lexists(path):
            continue
        if owner(path) not in OWNER_SIDS:
            return f"{path} is not owned by administrators. Run the full installer to lock it again."
        bad = write_access_problem(read(path), trusted, protected=False)
        if bad:
            return f"{path}: {bad}. Run the full installer to lock it again."
    return None


def restart_for_packages(layout, run, port, health=health_ok, sleep=time.sleep,
                         clock=time.monotonic, owner=owner_sid, hold=hold_directory, sddl=None,
                         runner=None) -> str:
    """Runs the install the app queued, as LocalSystem, while the app is
    stopped, then starts the app again. Fixed steps only: the module
    `pending_install` of the locked install, which re-derives the pip command
    from the app's registry and restores the earlier packages when pip fails.
    It works on a copy of the queue in the admin-only app-update folder, so
    nothing the user or the service account can change is read or written
    as LocalSystem. Nothing in the request is read. Raises ServiceError when
    nothing was installed or the app didn't come back; the outcome is also in
    app-update\\package-restart-result.json, which the app reads."""
    runner = runner or subprocess.run
    queued = _queued_install(layout)
    if queued is None:
        raise ServiceError("No valid package install is queued, so the app was not restarted.")
    raw, keys, created = queued
    started, since = clock(), int(time.time())
    # Held for the whole run, results included: the root's parent is the user's, and
    # a result written after the hold ended could land wherever a swapped folder points.
    with hold(layout.root):
        problem = _install_folder_problem(layout, owner, sddl)
        if problem:
            raise ServiceError(problem)
        if _already_handled(layout, created):
            raise ServiceError("This queued install was already run; the app will remove it.")
        state_root = app_swap.work_dir(layout.root) / PACKAGE_WORK_NAME
        (state_root / PENDING_DIR).mkdir(parents=True, exist_ok=True)
        (state_root / PENDING_DIR / PENDING_FILE).write_bytes(raw)
        run.log(f"package install: stopping the app for {len(keys)} queued packages")
        try:
            stop_service(APP_SERVICE, run, sleep=sleep)
        except ServiceError as exc:
            _write_restart_result(layout, "stop_failed", 0, created, packages=len(keys))
            raise ServiceError(f"The app couldn't be stopped, so nothing was installed: {exc}") from None
        wait_until(lambda: not health(port), STOP_WAIT_SECONDS, sleep=sleep)
        _run_install_step(layout, run, ["-m", "pending_install"], INSTALL_SECONDS, runner, state_root)
        outcome = _outcome_from(state_root, since)
        run.log(f"package install: status {outcome.get('install_status')}, "
                f"{outcome.get('packages', 0)} packages, {outcome.get('restored', 0)} restored")
        try:
            start_service(APP_SERVICE, run, sleep=sleep)
        except ServiceError as exc:
            _write_restart_result(layout, "start_failed", int(clock() - started), created, **outcome)
            raise ServiceError(f"The app didn't start again: {exc}") from None
        healthy = wait_until(lambda: health(port), HEALTH_WAIT_SECONDS, sleep=sleep)
        seconds = int(clock() - started)
        if not healthy:
            _write_restart_result(layout, "not_healthy", seconds, created, **outcome)
            run.log(f"package install: the app did not answer after {seconds} s")
            raise ServiceError("The app did not come back after the package install. Restart the PC, "
                               "or run Setup again to repair it.")
        _write_restart_result(layout, "ok" if outcome.get("install_status") == "ok" else "install_failed",
                          seconds, created, **outcome)
    run.log(f"package install: back after {seconds} s")
    return (f"Installed {outcome.get('packages', 0)} queued packages "
            f"({outcome.get('install_status')}); Baihe was back after {seconds} s.")

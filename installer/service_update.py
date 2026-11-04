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
import re
import shutil
import time
import zipfile
from pathlib import Path

from installer import app_swap, ed25519_verify
from installer.service_checks import health_ok, wait_until
from installer.service_control import query_state, start_service, stop_service
from installer.service_layout import APP_SERVICE, ServiceError, is_reparse_point, refuse_reparse_point

REQUEST_NAME = "app-update-request.json"
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


def _data_file(updates, name: str, cap: int) -> Path:
    """A regular file in the data folder's updates folder. The service
    account can create links there, so a link anywhere on the way is refused
    rather than read through."""
    for part in (updates.parent, updates):
        if is_reparse_point(part):
            raise ServiceError("The update folder is a link; refusing to read through it.")
    path = updates / name
    if is_reparse_point(path) or not path.is_file():
        raise ServiceError(f"The update request names a file that isn't there ({name}).")
    if path.stat().st_size > cap:
        raise ServiceError(f"The update request names a file that is too large ({name}).")
    return path


def read_request(updates) -> tuple:
    """(version, zip name) from the request the server wrote."""
    try:
        doc = json.loads(_data_file(updates, REQUEST_NAME, MAX_SMALL_FILE_BYTES)
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ServiceError("The update request can't be read.") from None
    name = doc.get("name") if isinstance(doc, dict) else None
    m = _NAME_RE.fullmatch(name) if isinstance(name, str) else None
    if m is None or not app_swap.valid_version(m.group(1)) or doc.get("version") != m.group(1):
        raise ServiceError("The update request doesn't name a release file.")
    return m.group(1), name


def _stage(src: Path, dest: Path) -> str:
    """Copies the zip into the admin-only folder, hashing as it goes; every
    later check and the unpack read this copy, so the service account
    can't change the file after it was checked."""
    sha, total = hashlib.sha256(), 0
    with open(src, "rb") as inp, open(dest, "wb") as out:
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
    if (manifest.get("version") != version or manifest.get("base") != app_swap.base_fingerprint(installed)
            or minimum is None or version_key(base) is None or version_key(base) < minimum):
        raise ServiceError("This update was built for a different Python, packages or Caddy "
                           "than this install has. Use the full installer.")
    return manifest


def apply_update(layout, run, port, health=health_ok, sleep=time.sleep) -> str:
    """Verifies the requested app zip and switches the service to it. Raises
    ServiceError with the reason when nothing changed or the previous
    version was put back; the outcome is also in app-update\\result.json,
    which the app reads."""
    root, updates = layout.root, layout.data / "library" / "updates"
    refuse_reparse_point(root)
    installed = app_swap.installed_manifest(root)
    old = installed.get("app_version") if installed else None
    if not app_swap.valid_version(old) or app_swap.base_version(installed) is None:
        raise ServiceError("This install's manifest.json can't be read. Run Setup again.")
    if app_swap.read_journal(root) is not None:
        raise ServiceError("An earlier app update is still unfinished. Run Setup again to "
                           "repair it.")
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
        signature = _b64("".join(_data_file(updates, name + ".sig", MAX_SMALL_FILE_BYTES)
                                 .read_text(encoding="ascii").split()), 64, "signature")
        key = read_release_key(layout.public_key)
        staging = layout.staging
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        dest = app_swap.app_folder(root, version)
        try:
            copy = staging / name
            sha = _stage(_data_file(updates, name, MAX_ZIP_BYTES), copy)
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
        # which the service never has; forget it rather than leave a journal
        # that blocks the next update.
        app_swap.clear_journal(root)
        app_swap.write_result(root, "rolled_back_switch", old, version)
        code = "rolled_back_switch"
    if code == "updated":
        return f"Updated Baihe Studio's service from {old} to {version}."
    raise ServiceError(OUTCOMES.get(code, f"The update ended with {code}."))

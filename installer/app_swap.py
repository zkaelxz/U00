"""
installer/app_swap.py -- installs an app-only update (BaiheStudio-App-<v>.zip)
next to the running app, switches to it, checks it starts, and puts the
previous version back when it doesn't. docs/windows-installer-design.md,
"App-only updates", has the whole flow.

The server (services/app_update_service.py) checks the download and calls
`check_zip` and `unpack` to build `<install dir>\\app-<new>`, writes the
journal and starts a copy of this file from `<install dir>\\app-update\\`:

    app_swap.py apply   --root <install dir>   stop, switch, check, keep or roll back
    app_swap.py recover --root <install dir>   finish or roll back after a crash
                                               (Windows' RunOnce runs this at the next sign-in)

`app\\` keeps its name for whatever version is current: python312._pth,
the Start-menu shortcuts, the boot service and Setup all name it, so the
switch is two renames (`app` -> `app-<old>`, `app-<new>` -> `app`) recorded
in a journal before each step. A crash at any point leaves a journal that
the next start (launcher.py) or the next sign-in finishes or rolls back.

Standard library only and no import of the app: the copy that runs this
must not load anything from the folder it renames.
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

APP_DIR_NAME = "app"
WORK_DIR_NAME = "app-update"
JOURNAL_NAME = "swap.json"
LOCK_NAME = "swap.lock"
RESULT_NAME = "result.json"
LOG_NAME = "swap.log"
HELPER_NAME = "app_swap.py"
APP_MANIFEST_NAME = "app-manifest.json"
PRODUCT = "Baihe Studio"
PARTIAL_SUFFIX = ".partial"

MAX_ENTRIES = 50_000
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_STATE_BYTES = 64 * 1024
CHUNK = 1024 * 1024

# Waiting for an antivirus scan or the search indexer to let go of a file
# before calling it locked.
RENAME_TRIES = 30
RENAME_WAIT_SECONDS = 1.0
STOP_SECONDS = 120
# launcher.py --no-browser may first apply a package install queued from
# Diagnostics (its own limit is an hour and five minutes), then waits 90 s
# for /api/health.
TRIAL_SECONDS = 3600 + 300 + 90 + 120

# The files every app layer must carry: without them the swapped-in app
# couldn't start, find its data, stop, or check the next update.
REQUIRED_ENTRIES = (APP_MANIFEST_NAME, "api/__main__.py", "portable.py", "process_guard.py",
                    "installer/launcher.py", "installer/app_swap.py",
                    "installer/release_public_key.txt", "frontend/dist/index.html")
# Never part of the app layer (installer/build_installer.check_payload's
# rules): data, keys and per-copy markers. INSTALLED is copied from the
# current app, since it names this PC's data folder.
# Lower case: Windows matches names without regard to case.
FORBIDDEN_DIR_NAMES = frozenset({"library", "model_cache", "venv", ".venv"})
FORBIDDEN_FILE_NAMES = frozenset({"installed", "portable"})
_WINDOWS_DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$",
                                   *(f"{p}{d}" for p in ("com", "lpt")
                                     for d in ("0123456789" + "\u00b9\u00b2\u00b3"))})
# A version as services/update_service.parse_version and lib/release_signature
# read it. Folder names built from it are what prune() and Setup may delete,
# so nothing else (app-backup, app-notes) ever matches.
_VERSION_RE = re.compile(r"\d{1,6}\.\d{1,6}(?:\.\d{1,6})?(?:-[0-9A-Za-z.-]{1,40})?"
                         r"(?:\+[0-9A-Za-z.-]{1,40})?")

# journal states, in order
STAGED, STOPPING, SWITCHING, CHECKING, DONE, QUEUED = (
    "staged", "stopping", "switching", "checking", "done", "queued")
STATES = (STAGED, STOPPING, SWITCHING, CHECKING, DONE, QUEUED)
# result codes the server turns into plain words
RESULT_CODES = ("updated", "queued", "not_started", "rolled_back_health", "rolled_back_switch",
                "recovered", "broken")

# Set for the start right after a switch was queued (Launcher.open_window).
DEFER_ENV = "BAIHE_APP_UPDATE_DEFER"

RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
# "!": Windows deletes a RunOnce value only after its command has finished,
# so a sign-in that ends before recovery does runs it again at the next one.
RUNONCE_VALUE = "!BaiheStudioAppUpdate"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class SwapError(Exception):
    """A plain-words reason the update can't go ahead. Nothing was switched."""


class Locked(Exception):
    """A file in the app folder is in use, so it can't be renamed now."""


# --- names and folders ------------------------------------------------------------

def valid_version(version) -> bool:
    return isinstance(version, str) and bool(_VERSION_RE.fullmatch(version))


def app_dir(root) -> Path:
    return Path(root) / APP_DIR_NAME


def app_folder(root, version) -> Path:
    if not valid_version(version):
        raise SwapError("The update's version isn't usable as a folder name.")
    return Path(root) / f"{APP_DIR_NAME}-{version}"


def work_dir(root) -> Path:
    return Path(root) / WORK_DIR_NAME


def is_link(path) -> bool:
    """A symlink or a Windows junction: never followed or deleted through."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0)
                                             & _FILE_ATTRIBUTE_REPARSE_POINT)


def remove_folder(path, root) -> None:
    """Deletes one of this module's own folders directly under the install
    folder (an `app-<v>` copy or a partial unpack), never a link."""
    path, root = Path(path), Path(root)
    if path.parent != root or not path.name.startswith(APP_DIR_NAME + "-") or is_link(path):
        raise SwapError("Refused to delete a folder this update didn't make.")
    if path.is_dir():
        shutil.rmtree(path)


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_STATE_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_STATE_BYTES:
        return None
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def log(root, text: str) -> None:
    """One line in <install dir>\\app-update\\swap.log, what the owner reads
    after a failed update. Best effort."""
    try:
        work_dir(root).mkdir(parents=True, exist_ok=True)
        with open(work_dir(root) / LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


# --- the base layer ---------------------------------------------------------------

def base_fingerprint(manifest) -> dict:
    """What an app layer needs from the installed base (the bundled Python, the
    pinned wheels, WinSW and Caddy), from a manifest.json written by
    installer/build_installer.write_manifest. None when it isn't one."""
    try:
        wheels = sorted((str(w["file"]), str(w["sha256"])) for w in manifest["wheels"])
        return {"python_sha256": str(manifest["python"]["embed_sha256"]),
                "wheels_sha256": hashlib.sha256(json.dumps(wheels).encode("utf-8")).hexdigest(),
                "caddy_sha256": str(manifest["services"]["caddy"]["sha256"]),
                "winsw_sha256": str(manifest["services"]["winsw"]["sha256"])}
    except (KeyError, TypeError, AttributeError):
        return None


def installed_manifest(root):
    doc = _read_json(Path(root) / "manifest.json")
    return doc if doc is not None and doc.get("product") == PRODUCT else None


def base_version(manifest):
    """The version of the full installer that put the base layer there. An
    app-only update changes app_version and records the base's own here."""
    version = manifest.get("base_version") or manifest.get("app_version")
    return version if valid_version(version) else None


def set_installed_version(root, version, base) -> None:
    """Writes the app version into <install dir>\\manifest.json, which is
    what the app reports and records in the library database."""
    path = Path(root) / "manifest.json"
    manifest = installed_manifest(root)
    if manifest is None:
        raise SwapError("The install folder's manifest.json can't be read.")
    if manifest.get("app_version") == version and manifest.get("base_version") == base:
        return
    manifest["app_version"] = version
    manifest["base_version"] = base
    _atomic_write(path, manifest)


def schema_fingerprint(conn) -> str:
    """The library database's tables and columns, order-free. The release
    build takes it from a fresh database, the app from its own: a difference
    means the new version changes the database."""
    names = []
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                 "AND name NOT LIKE 'sqlite_%'"):
        for row in conn.execute(f'PRAGMA table_info("{table}")'):
            names.append(f"{table}.{row[1]}")
    return hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()


def schema_fingerprint_of(path) -> str:
    with contextlib.closing(sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro",
                                            uri=True)) as conn:
        return schema_fingerprint(conn)


# --- the zip ----------------------------------------------------------------------

def _entry_problem(name: str):
    if not name or "\x00" in name or "\\" in name or ":" in name:
        return "an unusable name"
    if name.startswith("/"):
        return "an absolute path"
    parts = name.rstrip("/").split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return "a path that leaves the app folder"
        if part != part.rstrip(" .") or part.split(".")[0].lower() in _WINDOWS_DEVICE_NAMES:
            return "a name Windows can't hold"
        if part.lower() == ".env" or part.lower().startswith(".env."):
            return "a settings file"
    lower = [p.lower() for p in parts]
    if any(p in FORBIDDEN_DIR_NAMES for p in lower[:-1]) or (
            name.endswith("/") and lower[-1] in FORBIDDEN_DIR_NAMES):
        return "a data folder"
    if not name.endswith("/") and lower[-1] in FORBIDDEN_FILE_NAMES:
        return "a per-copy marker"
    return None


def check_zip(zf: zipfile.ZipFile) -> list:
    """The zip's entries, checked: relative paths inside the app folder only,
    regular files and folders only (no links), no data folders, keys or
    markers, no name twice (Windows ignores case), the required files present
    and the declared sizes within the cap. Raises SwapError."""
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise SwapError("The update has too many files.")
    seen, total = set(), 0
    for info in infos:
        problem = _entry_problem(info.filename)
        mode = (info.external_attr >> 16) & 0o170000
        if problem is None and mode not in (0, stat.S_IFREG, stat.S_IFDIR):
            problem = "a link or special file"
        if problem is None and info.flag_bits & 0x1:
            problem = "an encrypted file"
        if problem:
            raise SwapError(f"The update holds {problem}, so it was refused.")
        key = info.filename.rstrip("/").lower()
        if key in seen:
            raise SwapError("The update names a file twice, so it was refused.")
        seen.add(key)
        total += info.file_size
    if total > MAX_UNPACKED_BYTES:
        raise SwapError("The update is larger than allowed, so it was refused.")
    names = {i.filename for i in infos}
    missing = [r for r in REQUIRED_ENTRIES if r not in names]
    if missing:
        raise SwapError("The update is missing files the app needs, so it was refused.")
    return infos


def read_manifest(zf: zipfile.ZipFile) -> dict:
    info = zf.getinfo(APP_MANIFEST_NAME)
    if info.file_size > MAX_MANIFEST_BYTES:
        raise SwapError("The update's manifest is too large.")
    with zf.open(info) as fh:
        data = fh.read(MAX_MANIFEST_BYTES + 1)
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise SwapError("The update's manifest can't be read.") from None
    if (not isinstance(manifest, dict) or manifest.get("product") != PRODUCT
            or manifest.get("layer") != "app" or not valid_version(manifest.get("version"))
            or not isinstance(manifest.get("base"), dict)):
        raise SwapError("The update's manifest isn't a Baihe Studio app update.")
    return manifest


def unpack(zip_path, dest, cap: int = MAX_UNPACKED_BYTES) -> None:
    """Unpacks a checked zip into the new folder `dest` (which must not exist
    yet), through `<dest>.partial` so a half-written folder never looks
    ready. Counts the bytes actually written, not the sizes the zip claims."""
    dest = Path(dest)
    root = dest.parent
    if dest.exists() or is_link(dest):
        raise SwapError("The new version's folder already exists.")
    partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
    if partial.exists() or is_link(partial):
        remove_folder(partial, root)
    partial.mkdir()
    base = os.path.realpath(partial)
    written = 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in check_zip(zf):
                target = partial.joinpath(*PurePosixPath(info.filename.rstrip("/")).parts)
                if os.path.commonpath([base, os.path.realpath(target)]) != base:
                    raise SwapError("The update holds a path that leaves the app folder, "
                                    "so it was refused.")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "xb") as out:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > cap:
                            raise SwapError("The update is larger than allowed, so it was "
                                            "refused.")
                        out.write(chunk)
        os.rename(partial, dest)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise


# --- journal, result and lock -----------------------------------------------------

def read_journal(root):
    """The journal, or None when there is none. A journal that exists but
    can't be used reads as {"state": None}."""
    path = work_dir(root) / JOURNAL_NAME
    if not path.exists():
        return None
    doc = _read_json(path)
    port = doc.get("port") if doc else None
    step = doc.get("step", 0) if doc else None
    if (doc is None or doc.get("state") not in STATES or not valid_version(doc.get("old"))
            or not valid_version(doc.get("new")) or doc["old"] == doc["new"]
            or not valid_version(doc.get("base_version"))
            or not (type(step) is int and 0 <= step <= 2)
            or not (port is None or (type(port) is int and 1 <= port <= 65535))):
        return {"state": None}
    return {"state": doc["state"], "old": doc["old"], "new": doc["new"],
            "base_version": doc["base_version"], "port": port, "step": step}


def write_journal(root, state, old, new, base, port=None, step=0) -> None:
    """`port`: the running server's, so the helper can tell it has really
    stopped. `step`: how many of the switch's two renames were done."""
    _atomic_write(work_dir(root) / JOURNAL_NAME,
                  {"state": state, "old": old, "new": new, "base_version": base,
                   "port": port, "step": step})


def _record(root, journal, state, step=None) -> None:
    journal.update(state=state, step=journal.get("step", 0) if step is None else step)
    write_journal(root, state, journal["old"], journal["new"], journal["base_version"],
                  journal.get("port"), journal["step"])


def clear_journal(root) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(work_dir(root) / JOURNAL_NAME)


def write_result(root, code, old, new) -> None:
    _atomic_write(work_dir(root) / RESULT_NAME,
                  {"code": code, "old": old, "new": new, "at": time.time()})
    log(root, f"result {code} ({old} -> {new})")


def read_result(root):
    doc = _read_json(work_dir(root) / RESULT_NAME)
    if (doc is None or doc.get("code") not in RESULT_CODES or not valid_version(doc.get("old"))
            or not valid_version(doc.get("new")) or not isinstance(doc.get("at"), (int, float))):
        return None
    return {"code": doc["code"], "old": doc["old"], "new": doc["new"], "at": float(doc["at"])}


@contextlib.contextmanager
def swap_lock(root):
    """Held for as long as one process works on the swap. An OS lock, so a
    crash releases it; raises Locked when another process holds it."""
    work_dir(root).mkdir(parents=True, exist_ok=True)
    fh = open(work_dir(root) / LOCK_NAME, "a+b")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Locked() from None
        yield
    finally:
        fh.close()


# --- recovery at the next sign-in -------------------------------------------------

def _python(root, windowed=False) -> str:
    name = "pythonw.exe" if windowed else "python.exe"
    return str(Path(root) / "python" / name)


def helper_command(root, command) -> list:
    # -I: no PYTHON* variables, no user site, no script folder on sys.path.
    return [_python(root, windowed=True), "-I", str(work_dir(root) / HELPER_NAME), command,
            "--root", str(root)]


def register_recovery(root) -> None:
    """Asks Windows to run `recover` at the next sign-in, which covers a crash
    in the moment `app\\` doesn't exist (nothing in it could start then)."""
    if os.name != "nt":
        return
    import winreg
    command = subprocess.list2cmdline(helper_command(root, "recover"))
    with contextlib.suppress(OSError), winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUNONCE_KEY) as key:
        winreg.SetValueEx(key, RUNONCE_VALUE, 0, winreg.REG_SZ, command)


def unregister_recovery() -> None:
    if os.name != "nt":
        return
    import winreg
    with contextlib.suppress(OSError), winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUNONCE_KEY, 0,
                                                     winreg.KEY_SET_VALUE) as key:
        winreg.DeleteValue(key, RUNONCE_VALUE)


# --- switching --------------------------------------------------------------------

def _rename(src: Path, dst: Path, sleep, tries=RENAME_TRIES) -> None:
    for attempt in range(tries):
        try:
            os.rename(src, dst)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise Locked() from None
            sleep(RENAME_WAIT_SECONDS)


def switch_in(root, old, new, sleep=time.sleep, on_step=lambda step: None) -> None:
    """app -> app-<old>, then app-<new> -> app, calling on_step(1) and
    on_step(2) after each. When the second rename fails the first is undone,
    so a failure leaves the old version in place. app-<old> must not exist
    yet (apply removes a stale one before the journal says "switching")."""
    root = Path(root)
    current, previous, incoming = app_dir(root), app_folder(root, old), app_folder(root, new)
    if not incoming.is_dir() or is_link(incoming):
        raise SwapError("The new version's folder is missing.")
    if previous.exists() or is_link(previous):
        raise SwapError("The previous version's folder name is taken.")
    _rename(current, previous, sleep)
    on_step(1)
    try:
        _rename(incoming, current, sleep)
    except BaseException:
        _rename(previous, current, sleep)
        on_step(0)
        raise
    on_step(2)


def switch_back(root, journal, sleep=time.sleep) -> None:
    """Puts app-<old> back as app from the step the journal recorded. A
    crash can land between a rename and its journal entry, so the recorded
    step may be one behind; the folders tell which, and anything else (all
    three folders present) is refused rather than guessed at. Nothing is
    deleted here."""
    root = Path(root)
    old, new = journal["old"], journal["new"]
    current, previous, incoming = app_dir(root), app_folder(root, old), app_folder(root, new)
    step = journal.get("step", 0)
    if step == 0 and not previous.exists():
        return      # the first rename never happened: app\ is still the old version
    if step <= 1 and previous.is_dir() and not current.exists():
        _rename(previous, current, sleep)
        return
    if previous.is_dir() and current.is_dir() and not incoming.exists():
        _rename(current, incoming, sleep)
        _rename(previous, current, sleep)
        return
    raise SwapError("The app folders don't match any step of the update.")


def prune(root, keep) -> list:
    """Deletes every other app-<version> copy and partial unpack, keeping
    the folders named in `keep` (the previous version)."""
    root, removed = Path(root), []
    keep = {app_folder(root, v).name for v in keep}
    for child in sorted(root.iterdir()):
        name = child.name
        if not name.startswith(APP_DIR_NAME + "-") or name in keep or name == WORK_DIR_NAME:
            continue
        version = name[len(APP_DIR_NAME) + 1:].removesuffix(PARTIAL_SUFFIX)
        if valid_version(version) and child.is_dir() and not is_link(child):
            with contextlib.suppress(OSError, SwapError):
                remove_folder(child, root)
                removed.append(name)
    return removed


def finish(root, journal) -> None:
    """After the new version answered: record it, forget the journal, and
    delete the copies older than the one just replaced."""
    _record(root, journal, DONE)
    set_installed_version(root, journal["new"], journal["base_version"])
    clear_journal(root)
    unregister_recovery()
    prune(root, keep=[journal["old"]])
    write_result(root, "updated", journal["old"], journal["new"])


def roll_back(root, journal, sleep=time.sleep) -> None:
    switch_back(root, journal, sleep)
    set_installed_version(root, journal["old"], journal["base_version"])
    clear_journal(root)
    unregister_recovery()


# --- the launcher of whichever version is in app\ ---------------------------------

class Launcher:
    """Runs app\\installer\\launcher.py with the bundled Python, so stopping
    and starting work exactly as from the Start menu."""

    def __init__(self, root, port=None):
        self.root = Path(root)
        self.port = port

    def _script(self) -> str:
        return str(app_dir(self.root) / "installer" / "launcher.py")

    def _run(self, flag, timeout) -> bool:
        kwargs = {"cwd": str(work_dir(self.root)), "timeout": timeout,
                  "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            return subprocess.run([_python(self.root), "-s", self._script(), flag],
                                  **kwargs).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def stop(self) -> bool:
        """Stops the app, and with a known port waits until nothing answers
        there: then the server answering after the switch is the new one."""
        self._run("--stop", STOP_SECONDS)
        if self.port is None:
            return True
        deadline = time.monotonic() + STOP_SECONDS
        while _health_answers(self.port):
            if time.monotonic() > deadline:
                return False
            time.sleep(1)
        return True

    def start_and_check(self) -> bool:
        """Starts the server and waits for /api/health (launcher.py
        --no-browser exits 0 only once it answers)."""
        return self._run("--no-browser", TRIAL_SECONDS)

    def open_window(self, defer=False) -> None:
        """Starts the app (if needed) and opens its window. `defer`: this
        start leaves a queued switch for the next one, which would otherwise
        run it straight away and find the same file still in use."""
        env = dict(os.environ)
        env.pop(DEFER_ENV, None)
        if defer:
            env[DEFER_ENV] = "1"
        with contextlib.suppress(OSError):
            subprocess.Popen([_python(self.root, windowed=True), "-s", self._script()],
                             cwd=str(work_dir(self.root)), env=env, close_fds=True)


def _health_answers(port) -> bool:
    import urllib.request
    # Loopback never goes through a proxy (launcher.py does the same).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{int(port)}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _message(text: str) -> None:
    if os.name == "nt":
        with contextlib.suppress(Exception):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "Baihe Studio", 0x10)


BROKEN_MESSAGE = ("Baihe Studio couldn't finish or undo an app update. Run the Baihe Studio "
                  "installer again to repair it; your library and settings are kept.")


def apply(root, launcher=None, sleep=time.sleep) -> str:
    """Stops the running app, switches to the staged version and keeps it
    only if it answers; otherwise puts the previous version back and starts
    it. Returns the result code it recorded."""
    root = Path(root)
    journal = read_journal(root)
    if journal is None or journal["state"] not in (STAGED, QUEUED):
        return "not_started"
    launcher = launcher or Launcher(root, journal["port"])
    old, new = journal["old"], journal["new"]
    with swap_lock(root):
        log(root, f"apply {old} -> {new}")
        _record(root, journal, STOPPING, 0)
        if not launcher.stop():
            log(root, "the running app didn't stop")
            clear_journal(root)
            write_result(root, "not_started", old, new)
            launcher.open_window()
            return "not_started"
        try:
            previous = app_folder(root, old)
            if previous.exists() or is_link(previous):
                remove_folder(previous, root)      # a stale copy of this same version
        except (OSError, SwapError):
            clear_journal(root)
            write_result(root, "rolled_back_switch", old, new)
            launcher.open_window()
            return "rolled_back_switch"
        _record(root, journal, SWITCHING, 0)
        register_recovery(root)
        try:
            switch_in(root, old, new, sleep, on_step=lambda step: _record(root, journal,
                                                                          SWITCHING, step))
        except Locked:
            if not app_dir(root).is_dir():
                # Not even the undo could rename: sign-in's recovery tries again.
                write_result(root, "broken", old, new)
                _message(BROKEN_MESSAGE)
                return "broken"
            # Something still holds a file in app\: the next start finishes it.
            _record(root, journal, QUEUED, 0)
            unregister_recovery()
            write_result(root, "queued", old, new)
            launcher.open_window(defer=True)
            return "queued"
        except (OSError, SwapError) as exc:
            log(root, f"switch failed: {type(exc).__name__}")
            try:
                roll_back(root, journal, sleep)
            except (OSError, Locked, SwapError):
                write_result(root, "broken", old, new)
                _message(BROKEN_MESSAGE)
                return "broken"
            write_result(root, "rolled_back_switch", old, new)
            launcher.open_window()
            return "rolled_back_switch"
        _record(root, journal, CHECKING, 2)
        if launcher.start_and_check():
            finish(root, journal)
            launcher.open_window()
            return "updated"
        log(root, f"{new} didn't answer /api/health; going back to {old}")
        launcher.stop()
        try:
            roll_back(root, journal, sleep)
        except (OSError, Locked, SwapError):
            write_result(root, "broken", old, new)
            _message(BROKEN_MESSAGE)
            return "broken"
        if not launcher.start_and_check():
            write_result(root, "broken", old, new)
            _message(BROKEN_MESSAGE)
            return "broken"
        write_result(root, "rolled_back_health", old, new)
        launcher.open_window()
        return "rolled_back_health"


def recover(root, stop=None, sleep=time.sleep) -> str:
    """Run before anything starts from app\\ (launcher.py, and Windows'
    RunOnce at sign-in). Returns None when no update is unfinished, "busy"
    while the helper is still working, "queued" when a switch waits for the
    caller to run `apply`, else the result code it recorded."""
    root = Path(root)
    journal = read_journal(root)
    if journal is None:
        return None
    try:
        with swap_lock(root):
            state = journal["state"]
            if state is None:
                if app_dir(root).is_dir():
                    clear_journal(root)
                    unregister_recovery()
                    return None
                return "broken"
            old, new = journal["old"], journal["new"]
            if state == QUEUED:
                return "queued"
            if state in (STAGED, STOPPING):
                clear_journal(root)
                unregister_recovery()
                write_result(root, "not_started", old, new)
                return "not_started"
            if state == DONE:
                try:
                    finish(root, journal)
                except (OSError, SwapError):
                    # manifest.json unreadable or unwritable: only Setup can repair that.
                    return "broken"
                return "updated"
            if stop is not None:
                stop()
            try:
                roll_back(root, journal, sleep)
            except (OSError, Locked, SwapError):
                return "broken"
            write_result(root, "recovered", old, new)
            return "recovered"
    except Locked:
        return "busy"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Switch Baihe Studio to a staged app update.")
    parser.add_argument("command", choices=("apply", "recover"))
    parser.add_argument("--root", required=True, help="the install folder")
    args = parser.parse_args(argv)
    root = Path(args.root)
    # Out of app\: Windows won't rename a folder that is a process's current one.
    work_dir(root).mkdir(parents=True, exist_ok=True)
    os.chdir(work_dir(root))
    if args.command == "apply":
        code = apply(root)
    else:
        code = recover(root)
        if code == "broken":
            _message(BROKEN_MESSAGE)
    return 0 if code in (None, "updated", "queued", "recovered", "not_started", "busy") else 1


if __name__ == "__main__":
    sys.exit(main())

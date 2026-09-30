"""
installer/postinstall.py -- the installer's one post-install step (Step
80b). Inno Setup runs it with the bundled interpreter once the files are
copied:

    python\\python.exe -s app\\installer\\postinstall.py --wheels <dir> --data-dir <dir>

1. Writes app\\INSTALLED, whose first line is the per-user data folder
   (portable.data_dir() reads it), then creates that folder. The marker
   comes first; the launcher refuses to start an install without it, so
   even a half-finished install never puts the library in the program
   folder.
2. Bootstraps pip from the vendored pip wheel. The embeddable Python ships
   without pip; running pip as a module from its own wheel needs no network.
3. Checks every bundled wheel against wheels.lock.txt (its SHA-256 must be
   listed, and no wheel may be missing from or extra to the lock), then
   installs that lock from the bundled wheels only (`--no-index
   --require-hashes --no-deps`), so pip itself refuses a wheel whose hash
   isn't pinned. On an upgrade this only changes what changed; packages
   added later through Diagnostics stay.
4. Checks the core packages import, then runs check_setup.py (ffmpeg,
   JS runtime, CUDA) for the log only -- a missing optional tool is not
   an install failure.

Everything is logged to <data>\\launcher\\install.log. Exit codes: 0 ok,
2 bad arguments, 3 pip bootstrap failed, 4 core install failed, 5 the
installed packages don't import, 6 a bundled wheel failed the hash check.

Standard library only (nothing else is installed yet).
"""

import argparse
import datetime
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
MARKER_NAME = "INSTALLED"
LOG_NAME = "install.log"
# The imports that prove requirements-core.txt landed (start.bat checks
# the same list; this adds the sign-in packages core also carries).
CORE_IMPORTS = ("streamlit", "pandas", "requests", "urllib3", "bs4", "anthropic",
                "fastapi", "starlette", "multipart", "uvicorn", "authlib", "httpx")


# Pinned wheel hashes, next to the wheels. installer/build_installer.py
# copies the committed installer/wheels.lock.txt here.
LOCK_NAME = "wheels.lock.txt"


class PostInstallError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class LockError(Exception):
    """The wheel lock is malformed, or the wheels don't match it."""


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


_LOCK_REQUIREMENT = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9._+!-]+)")
_LOCK_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def parse_lock(text: str) -> dict:
    """{canonical name: (version, {sha256, ...})} from a pip-compile style
    lock: `name==version \\` then `--hash=sha256:<64 hex>` lines. Anything
    else (an unpinned line, a marker, another option, a package without a
    hash, a repeated package) raises LockError naming the line."""
    entries = {}
    current = None
    logical = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        continued = line.endswith("\\")
        logical.append((number, line[:-1].strip() if continued else line))
        if continued:
            continue
        joined = " ".join(part for _, part in logical)
        start = logical[0][0]
        logical = []
        tokens = joined.split()
        match = _LOCK_REQUIREMENT.fullmatch(tokens[0])
        if not match:
            raise LockError(f"wheels.lock.txt line {start}: {tokens[0]!r} isn't a pinned "
                            "`name==version` requirement.")
        name = canonical_name(match.group(1))
        hashes = set()
        for token in tokens[1:]:
            hash_match = _LOCK_HASH.fullmatch(token)
            if not hash_match:
                raise LockError(f"wheels.lock.txt line {start}: {name}: unexpected {token!r} "
                                "(only --hash=sha256:<64 hex digits> may follow the version).")
            hashes.add(hash_match.group(1))
        if not hashes:
            raise LockError(f"wheels.lock.txt line {start}: {name} has no --hash entry.")
        if name in entries:
            raise LockError(f"wheels.lock.txt line {start}: {name} is listed twice.")
        entries[name] = (match.group(2).lower(), hashes)
    if logical:
        raise LockError(f"wheels.lock.txt line {logical[0][0]}: a trailing backslash with nothing after it.")
    if not entries:
        raise LockError("wheels.lock.txt has no packages.")
    return entries


def wheel_identity(filename: str):
    """(canonical name, version) from a wheel's file name."""
    parts = filename[:-4].split("-") if filename.endswith(".whl") else []
    if len(parts) < 5:
        raise LockError(f"{filename} isn't a wheel file name.")
    return canonical_name(parts[0]), parts[1].lower()


def _sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_wheels(wheels_dir, lock_text: str) -> int:
    """Every *.whl in wheels_dir must be pinned in the lock (same name and
    version, SHA-256 listed) and every lock entry must have a wheel. Raises
    LockError naming every offender; returns the number of wheels checked.
    This checks the files against the hashes the build trusts, nothing more."""
    lock = parse_lock(lock_text)
    wheels = sorted(Path(wheels_dir).glob("*.whl"))
    problems = []
    seen = set()
    for whl in wheels:
        try:
            name, version = wheel_identity(whl.name)
        except LockError as e:
            problems.append(str(e))
            continue
        seen.add(name)
        if name not in lock:
            problems.append(f"{whl.name}: not in wheels.lock.txt (an unpinned wheel)")
        elif lock[name][0] != version:
            problems.append(f"{whl.name}: the lock pins {name}=={lock[name][0]}")
        elif _sha256_file(whl) not in lock[name][1]:
            problems.append(f"{whl.name}: SHA-256 {_sha256_file(whl)} isn't in wheels.lock.txt")
    for name in sorted(set(lock) - seen):
        problems.append(f"{name}: pinned in wheels.lock.txt but no wheel for it is bundled")
    if problems:
        raise LockError("Bundled wheels don't match wheels.lock.txt:\n  " + "\n  ".join(problems))
    return len(wheels)


def validate_data_dir(data_dir: str, app_root: Path) -> Path:
    """The data folder must be an absolute path, not a whole drive, and
    not inside the install folder (the uninstaller and upgrades delete
    that). The installer checks the same thing; this is the backstop."""
    if not data_dir or not os.path.isabs(data_dir):
        raise PostInstallError(f"The data folder must be a full path, got {data_dir!r}.", 2)
    path = Path(os.path.abspath(data_dir))
    if path.parent == path:
        raise PostInstallError("Choose a folder for your data, not a whole drive.", 2)
    root = Path(os.path.abspath(app_root))
    norm = lambda p: os.path.normcase(str(p))  # noqa: E731
    if norm(path) == norm(root) or norm(root) in [norm(p) for p in path.parents]:
        raise PostInstallError(
            "The data folder can't be inside the install folder: updates and "
            "uninstalling replace that folder.", 2)
    return path


CREATED_FLAG_LINE = "# created-by-setup"   # read by the uninstaller (baihe.iss)


def write_marker(app_dir: Path, data_dir: Path, created: bool = False) -> Path:
    """utf-8 with a BOM, so both portable.py (utf-8-sig) and the
    uninstaller's LoadStringsFromFile read a non-ASCII path correctly
    (checked against Inno Setup 6.7.3). `created`: Setup made the data
    folder, so a clean uninstall may remove the folder itself; otherwise
    it removes only the items Baihe Studio made inside it."""
    marker = app_dir / MARKER_NAME
    marker.write_text(
        f"{data_dir}\n"
        "# Written by the Baihe Studio installer: the line above is where this\n"
        "# copy keeps its library, settings/API keys and downloaded models.\n"
        + (CREATED_FLAG_LINE + "\n" if created else ""),
        encoding="utf-8-sig")
    return marker


def _inside(path: Path, parent) -> bool:
    if not parent:
        return False
    p = os.path.normcase(os.path.abspath(str(path)))
    q = os.path.normcase(os.path.abspath(str(parent)))
    return p == q or p.startswith(q.rstrip("\\/") + os.sep)


def _system32(exe: str) -> str:
    """Full path, so the bundled Python's folder or the working folder is
    never searched for a program of the same name first."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "System32", exe)


def current_user_sid(run=subprocess.run):
    """This account's SID (`whoami /user`), or None."""
    try:
        # errors="replace": the console code page can hold bytes the ANSI
        # code page can't decode (an account name with an umlaut).
        out = run([_system32("whoami.exe"), "/user", "/fo", "csv", "/nh"], capture_output=True,
                  text=True, errors="replace", timeout=30).stdout
    except Exception:
        return None
    sid = out.strip().rsplit(",", 1)[-1].strip().strip('"')
    return sid if sid.startswith("S-1-") else None


def lockdown_command(data_dir: Path, sid: str) -> list:
    """icacls: no inherited permissions; full control for this account,
    SYSTEM and Administrators only (by SID, so it works on any language of
    Windows)."""
    return [_system32("icacls.exe"), str(data_dir), "/inheritance:r",
            "/grant:r", f"*{sid}:(OI)(CI)F",
            "/grant:r", "*S-1-5-18:(OI)(CI)F",
            "/grant:r", "*S-1-5-32-544:(OI)(CI)F"]


def restrict_new_data_dir(data_dir: Path, new: bool, log, run=subprocess.run,
                          profile=None, is_windows=None) -> bool:
    """A data folder Setup created outside the user's profile (say
    D:\\Baihe) would otherwise inherit that drive's permissions, which
    often let every account on the PC read it -- and .env holds the API
    keys. Limits it to this account. A folder that already existed keeps
    its permissions (Setup warned about that on its data-folder page), and
    so does one limited by an earlier install (an upgrade doesn't redo it);
    one inside the profile is already private. Never fails the install."""
    is_windows = (os.name == "nt") if is_windows is None else is_windows
    profile = os.environ.get("USERPROFILE", "") if profile is None else profile
    if not is_windows or not new or _inside(data_dir, profile):
        return False
    sid = current_user_sid(run)
    if not sid:
        log.write("Couldn't read this account's SID; the data folder keeps its permissions.\n")
        return False
    try:
        result = run(lockdown_command(data_dir, sid), capture_output=True, text=True,
                     errors="replace", timeout=60)
    except Exception as e:
        log.write(f"Couldn't limit the data folder to this account: {e}\n")
        return False
    log.write(f"Limited the data folder to this account (icacls exit code {result.returncode}).\n")
    return result.returncode == 0


def pip_env(base=None) -> dict:
    """The install must use the bundled wheels and nothing else: every
    PIP_* setting from the user's environment is dropped, and pip.ini
    files are ignored (PIP_CONFIG_FILE=devnull), so a machine-wide
    find-links, user=true or target= can't redirect it."""
    env = {k: v for k, v in (os.environ if base is None else base).items()
           if not k.upper().startswith("PIP_")}
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PYTHONNOUSERSITE"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def find_pip_wheel(wheels_dir: Path) -> Path:
    found = sorted(wheels_dir.glob("pip-*.whl"))
    if not found:
        raise PostInstallError(f"No pip wheel in {wheels_dir}; the installer payload is incomplete.", 3)
    return found[-1]


# Runs pip as a module from its own wheel (argv[1]), the equivalent of
# `python -m pip` before pip is installed. Not `python <wheel>\\pip`: on
# Windows pip refuses to modify itself when argv[0] is named "pip"
# (protect_pip_from_modification_on_windows), which is exactly what
# installing pip is.
_RUN_PIP_FROM_WHEEL = ("import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); "
                       "runpy.run_module('pip', run_name='__main__', alter_sys=True)")


def bootstrap_pip_command(python_exe: str, wheels_dir: Path) -> list:
    pip_whl = find_pip_wheel(wheels_dir)
    return [python_exe, "-s", "-c", _RUN_PIP_FROM_WHEEL, str(pip_whl), "install", "--no-index",
            "--find-links", str(wheels_dir), "--no-warn-script-location", "--upgrade", "pip"]


def core_install_command(python_exe: str, wheels_dir: Path) -> list:
    """Installs exactly the pinned set: --require-hashes makes pip refuse
    any wheel whose hash isn't in the lock, --no-deps stops it resolving
    anything the lock doesn't name."""
    return [python_exe, "-s", "-m", "pip", "install", "--no-index",
            "--find-links", str(wheels_dir), "--no-warn-script-location",
            "--require-hashes", "--no-deps", "-r", str(wheels_dir / LOCK_NAME)]


def verify_command(python_exe: str) -> list:
    return [python_exe, "-s", "-c", "import " + ", ".join(CORE_IMPORTS)]


def _run(cmd, log, env, cwd):
    log.write(f"\n$ {subprocess.list2cmdline(cmd)}\n")
    log.flush()
    result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env,
                            cwd=str(cwd), timeout=1800)
    log.write(f"[exit code {result.returncode}]\n")
    log.flush()
    return result.returncode


def run(wheels_dir, data_dir, python_exe=None, app_dir=APP_DIR, runner=_run,
        created: bool = False, new: bool = False, restrict=restrict_new_data_dir) -> int:
    python_exe = python_exe or sys.executable
    wheels_dir = Path(wheels_dir)
    data = validate_data_dir(data_dir, app_dir.parent)
    # The marker first, before anything that can fail on the data folder.
    write_marker(app_dir, data, created)
    log_dir = data / "launcher"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise PostInstallError(f"Couldn't create the data folder {data}: {e}", 2)
    env = pip_env()
    lock_path = wheels_dir / LOCK_NAME
    with open(log_dir / LOG_NAME, "a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n=== Baihe Studio install step, {datetime.datetime.now().isoformat(timespec='seconds')} ===\n")
        log.write(f"app: {app_dir}\ndata: {data}\npython: {python_exe}\n")
        restrict(data, new, log)
        try:
            if not lock_path.is_file():
                raise LockError(f"{LOCK_NAME} isn't in {wheels_dir}; the installer payload is incomplete.")
            count = verify_wheels(wheels_dir, lock_path.read_text(encoding="utf-8"))
        except (LockError, OSError, UnicodeDecodeError) as e:
            log.write(f"FAILED: {e}\n")
            raise PostInstallError(f"{e} Details: {log_dir / LOG_NAME}", 6)
        log.write(f"Checked {count} bundled wheels against {LOCK_NAME}.\n")
        steps = (
            (bootstrap_pip_command(python_exe, wheels_dir), 3, "Setting up pip failed."),
            (core_install_command(python_exe, wheels_dir), 4,
             "Installing the app's Python packages failed."),
            (verify_command(python_exe), 5, "The installed Python packages don't import."),
        )
        for cmd, code, message in steps:
            if runner(cmd, log, env, app_dir) != 0:
                log.write(f"FAILED: {message}\n")
                raise PostInstallError(f"{message} Details: {log_dir / LOG_NAME}", code)
        # Plain-words ffmpeg/JS-runtime/CUDA report, for the log only.
        runner([python_exe, "-s", str(app_dir / "check_setup.py")], log, env, app_dir)
        log.write("Install finished OK.\n")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Baihe Studio's post-install step.")
    parser.add_argument("--wheels", required=True, help="folder with the bundled wheels")
    parser.add_argument("--data-dir", required=True, help="the per-user data folder")
    parser.add_argument("--data-dir-created", action="store_true",
                        help="Setup created the data folder (it's Baihe Studio's own)")
    parser.add_argument("--data-dir-new", action="store_true",
                        help="Setup created it just now: limit it to this account")
    args = parser.parse_args(argv)
    try:
        return run(args.wheels, args.data_dir, created=args.data_dir_created,
                   new=args.data_dir_new)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except PostInstallError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())

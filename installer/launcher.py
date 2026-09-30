"""
installer/launcher.py -- what the installed app's Start-menu shortcut runs
(Step 80b, the Windows installer).

The installed layout is `<install dir>\\python\\` (the bundled embeddable
Python), `<install dir>\\app\\` (the code, including the prebuilt React app
in `frontend\\dist`) and this file at `app\\installer\\launcher.py`. The
shortcut runs it with the bundled `pythonw.exe`, so there's no console
window of its own; errors are shown in a message box.

It does what start.bat does for a source checkout, minus the setup the
installer already did: if the app server already answers on
http://127.0.0.1:8600/api/health, it just opens a window on it; otherwise
it starts `python -m api` (loopback only) in its own minimized console
window -- closing that window stops the app -- waits for /api/health, and
opens the app in its own window (Edge app mode, then Chrome, then the
default browser).

    launcher.py               start (if needed) and open a window
    launcher.py --no-browser  start (if needed), wait for /api/health, exit;
                              the server runs with no window and logs to
                              <data>\\launcher\\server.log (the CI smoke test)
    launcher.py --stop        stop the server this launcher started (the
                              uninstaller and the upgrade path run this)

Standard library only: it runs before anything else is known to work.
"""

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import portable  # noqa: E402 -- needs APP_DIR on sys.path first

DEFAULT_PORT = 8600
HEALTH_TRIES = 90          # seconds; a first start compiles every .pyc
PID_FILE_NAME = "server.pid"
START_LOCK_NAME = "starting.lock"
START_LOCK_STALE_SECONDS = HEALTH_TRIES + 30
SERVER_LOG_NAME = "server.log"
WINDOW_TITLE = "Baihe Studio (server -- closing this window stops the app)"

# pip settings from the user's own environment that would make the
# bundled interpreter's installs (Diagnostics' Install buttons run
# `python -m pip` inside the server) fail or land somewhere else.
_PIP_ENV_TO_DROP = ("PIP_USER", "PIP_REQUIRE_VIRTUALENV", "PIP_TARGET", "PIP_PREFIX")


class LaunchError(Exception):
    """A plain-words reason the app couldn't be started."""


def launcher_dir() -> Path:
    """`<data>\\launcher`: the pid file, start lock and logs. Not user
    data; the uninstaller removes those files by name."""
    return Path(portable.data_dir()) / "launcher"


def server_env(base=None) -> dict:
    """The environment the server runs with. Loopback only, whatever the
    caller's environment says (no LAN access until remote access is set
    up on purpose, docs/remote-access-decision.md); the PC-only API-key
    form on unless BAIHE_API_ALLOW_KEY_WRITES was set; port 8600 unless
    BAIHE_API_PORT was set. Same as start.bat."""
    env = dict(os.environ if base is None else base)
    env["BAIHE_API_HOST"] = "127.0.0.1"
    env.setdefault("BAIHE_API_ALLOW_KEY_WRITES", "1")
    env.setdefault("BAIHE_API_PORT", str(DEFAULT_PORT))
    env["PYTHONNOUSERSITE"] = "1"
    for name in _PIP_ENV_TO_DROP:
        env.pop(name, None)
    return env


def port_from_env(env) -> int:
    try:
        port = int(str(env.get("BAIHE_API_PORT", DEFAULT_PORT)).strip())
    except ValueError:
        raise LaunchError("BAIHE_API_PORT must be a port number, like 8600.")
    if not 1 <= port <= 65535:
        raise LaunchError("BAIHE_API_PORT must be between 1 and 65535.")
    return port


def app_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def health_ok(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{app_url(port)}api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def console_python() -> str:
    """The bundled python.exe (a console interpreter, so the server gets
    a window), even when this launcher itself runs under pythonw.exe."""
    exe = Path(sys.executable)
    candidate = exe.with_name("python.exe")
    return str(candidate if candidate.is_file() else exe)


def server_command(python_exe: str) -> list:
    # -s: never pick up packages from the user's own Python install.
    return [python_exe, "-s", "-m", "api"]


def start_server(python_exe: str, env: dict, headless: bool):
    """Starts `python -m api` detached from this launcher and records its
    pid. Interactive: its own minimized console window. Headless: no
    window, output to <data>\\launcher\\server.log."""
    ldir = launcher_dir()
    ldir.mkdir(parents=True, exist_ok=True)
    kwargs = {"cwd": str(APP_DIR), "env": env, "close_fds": True}
    log = None
    if os.name == "nt":
        if headless:
            kwargs["creationflags"] = (subprocess.CREATE_NO_WINDOW
                                       | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 7   # SW_SHOWMINNOACTIVE
            si.lpTitle = WINDOW_TITLE
            kwargs["startupinfo"] = si
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    else:
        kwargs["start_new_session"] = True
    if headless or os.name != "nt":
        log = open(ldir / SERVER_LOG_NAME, "ab")
        kwargs["stdout"] = log
        kwargs["stderr"] = subprocess.STDOUT
        kwargs["stdin"] = subprocess.DEVNULL
    try:
        return subprocess.Popen(server_command(python_exe), **kwargs)
    finally:
        if log is not None:
            log.close()


def record_pid(proc) -> bool:
    """Records `proc`'s pid for --stop, but only while it's still running:
    if it already exited (another server won the port), the pid file keeps
    pointing at the server that's actually answering."""
    if proc is None or proc.poll() is not None:
        return False
    ldir = launcher_dir()
    ldir.mkdir(parents=True, exist_ok=True)
    (ldir / PID_FILE_NAME).write_text(str(proc.pid), encoding="ascii")
    return True


def acquire_start_lock(now=time.time) -> bool:
    """True if this launcher may start the server. A second click while a
    first start is still compiling gets False and just waits for health,
    rather than starting a second server that loses the port and leaves
    --stop pointing at the wrong pid. A lock older than the health wait is
    stale (a launcher that crashed) and is taken over."""
    ldir = launcher_dir()
    ldir.mkdir(parents=True, exist_ok=True)
    lock = ldir / START_LOCK_NAME
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if now() - lock.stat().st_mtime < START_LOCK_STALE_SECONDS:
                    return False
                lock.unlink()
            except FileNotFoundError:
                pass
    return False


def release_start_lock() -> None:
    try:
        (launcher_dir() / START_LOCK_NAME).unlink()
    except FileNotFoundError:
        pass


def wait_for_health(port: int, proc=None, tries: int = HEALTH_TRIES, sleep=time.sleep) -> bool:
    for _ in range(tries):
        if health_ok(port):
            return True
        if proc is not None and proc.poll() is not None:
            return False   # the server already exited; waiting longer won't help
        sleep(1)
    return health_ok(port)


def browser_candidates(env=None) -> list:
    env = os.environ if env is None else env
    pf = env.get("ProgramFiles", r"C:\Program Files")
    pf86 = env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = env.get("LOCALAPPDATA", "")
    edge = [rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
            rf"{pf}\Microsoft\Edge\Application\msedge.exe",
            rf"{local}\Microsoft\Edge\Application\msedge.exe"]
    chrome = [rf"{pf}\Google\Chrome\Application\chrome.exe",
              rf"{pf86}\Google\Chrome\Application\chrome.exe",
              rf"{local}\Google\Chrome\Application\chrome.exe"]
    return edge + chrome


def find_app_browser(candidates=None, exists=os.path.isfile):
    """The first Edge/Chrome that exists (Edge first, same order as
    start.bat), or None."""
    for path in (browser_candidates() if candidates is None else candidates):
        if path and exists(path):
            return path
    return None


def open_window(url: str) -> None:
    browser = find_app_browser()
    if browser:
        subprocess.Popen([browser, f"--app={url}"], close_fds=True)
        return
    import webbrowser
    webbrowser.open(url)


def _process_image(pid: int):
    """Full path of process `pid`'s executable, or None (not running, not
    ours to see, or not Windows)."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        kernel32.CloseHandle(handle)


def _same_file(a, b) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def stop_server(python_exe=None, image_of=_process_image, kill=None, sleep=time.sleep) -> str:
    """Stops the server this launcher started, if it's still running.
    Only kills the recorded pid when that process really is this install's
    own python.exe -- a stale pid file whose number Windows has since
    reused for something else is left alone. Returns a plain-words
    result; never raises for "nothing to stop"."""
    python_exe = python_exe or console_python()
    pid_file = launcher_dir() / PID_FILE_NAME
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return "Baihe Studio's server isn't running (no pid file)."
    image = image_of(pid)
    if not image or not _same_file(image, python_exe):
        pid_file.unlink(missing_ok=True)
        return "Baihe Studio's server isn't running."
    if kill is None:
        def kill(p):
            subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"],
                           capture_output=True, timeout=30)
    kill(pid)
    for _ in range(20):
        if not image_of(pid):
            break
        sleep(0.5)
    pid_file.unlink(missing_ok=True)
    return "Stopped Baihe Studio's server."


def show_message(text: str, error: bool = True, headless: bool = False) -> None:
    """A message box under pythonw.exe (no console to print to); stderr
    otherwise, and always when headless (a box nobody can click would
    hang an unattended run)."""
    if headless or (sys.stderr is not None and getattr(sys.stderr, "isatty", lambda: False)()):
        if sys.stderr is not None:
            print(text, file=sys.stderr)
        return
    if os.name == "nt":
        try:
            import ctypes
            flags = 0x10 if error else 0x40   # MB_ICONERROR / MB_ICONINFORMATION
            ctypes.windll.user32.MessageBoxW(None, text, "Baihe Studio", flags)
            return
        except Exception:
            pass
    if sys.stderr is not None:
        print(text, file=sys.stderr)


def launch(headless: bool = False) -> int:
    env = server_env()
    port = port_from_env(env)
    url = app_url(port)
    if not health_ok(port):
        if acquire_start_lock():
            try:
                if port_open(port):
                    raise LaunchError(
                        f"Something else is already using port {port}, and it isn't Baihe Studio "
                        f"({url}api/health doesn't answer). Close that program, or set "
                        "BAIHE_API_PORT to another port (for example 8601) and try again.")
                proc = start_server(console_python(), env, headless)
                healthy = wait_for_health(port, proc)
                if healthy:
                    record_pid(proc)
            finally:
                release_start_lock()
        else:
            # Another click is already starting it; wait for that one.
            healthy = wait_for_health(port)
        if not healthy:
            where = (launcher_dir() / SERVER_LOG_NAME) if headless else "the minimized server window"
            raise LaunchError(
                f"Baihe Studio's server didn't answer at {url}api/health. "
                f"Check {where} for the error. If the install looks incomplete, "
                "run the installer again to repair it.")
    if headless:
        print(f"/api/health is answering at {url}")
        return 0
    open_window(url)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start Baihe Studio and open its window.")
    parser.add_argument("--no-browser", action="store_true",
                        help="start the server if needed and wait for it, but open no window")
    parser.add_argument("--stop", action="store_true",
                        help="stop the server this launcher started")
    args = parser.parse_args(argv)
    if args.stop:
        print(stop_server())
        return 0
    try:
        return launch(headless=args.no_browser)
    except LaunchError as e:
        show_message(str(e), headless=args.no_browser)
        return 1


if __name__ == "__main__":
    sys.exit(main())

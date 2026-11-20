"""
tools/restart_after_update.py -- started by services/source_update_service.py
when an admin presses "Update and restart". Waits for the old server process
to exit and its port to be free, then runs start.bat, which does the update
itself (fetch, fast-forward, dependencies, screens) before starting the app.

Standard library only: it runs outside the server's process and virtual
environment checks.
"""

import argparse
import os
import socket
import subprocess
import sys
import time

EXIT_WAIT_SECONDS = 120
PORT_WAIT_SECONDS = 30
_SYNCHRONIZE = 0x00100000
LOG_NAME = "restart_after_update.log"


def process_gone(pid: int, seconds: float) -> bool:
    """True once `pid` has exited, False if it was still running after
    `seconds`. os.kill(pid, 0) would end the process on Windows, so that
    side waits on the process handle."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(_SYNCHRONIZE, 0, pid)
        if not handle:
            return True
        try:
            return kernel32.WaitForSingleObject(handle, int(seconds * 1000)) == 0
        finally:
            kernel32.CloseHandle(handle)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.25)
    return False


def port_free(port: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.5)
    return False


def _write_log(path: str, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as log:
            log.write(text + "\n")
    except OSError:
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    log_path = os.path.join(args.root, LOG_NAME)
    start_bat = os.path.join(args.root, "start.bat")
    if not os.path.isfile(start_bat):
        _write_log(log_path, "start.bat is missing from the Baihe folder; nothing was restarted.")
        return 2
    # Without a stopped server there is nothing to restart; start.bat would
    # only report "already running". The log is rewritten either way, so a
    # leftover from an earlier restart is never read as this one's outcome.
    if not process_gone(args.pid, EXIT_WAIT_SECONDS):
        _write_log(log_path, f"The old server (process {args.pid}) was still running after "
                             f"{EXIT_WAIT_SECONDS} s; nothing was restarted. Close it and run start.bat.")
        return 3
    port_note = b""
    if args.port and not port_free(args.port, PORT_WAIT_SECONDS):
        # start.bat will find whatever holds the port and stop there; say so first.
        port_note = (f"Port {args.port} was still in use {PORT_WAIT_SECONDS} s after the old server "
                     "exited.\r\n").encode()
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    # The console opens minimized and start.bat opens no app window of its own.
    startup = None
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=7)
    # Nobody is at this console to press a key, so a start.bat that fails must
    # exit (BAIHE_NOPAUSE) instead of waiting there while the browser shows
    # "reconnecting"; what it printed goes to a file the failure message names.
    env = dict(os.environ, BAIHE_NO_APP_WINDOW="1", BAIHE_NOPAUSE="1")
    with open(log_path, "wb") as log:
        log.write(port_note)
        log.flush()
        proc = subprocess.Popen(["cmd.exe", "/c", start_bat], cwd=args.root, creationflags=flags,
                                startupinfo=startup, env=env, close_fds=True,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    if args.port:
        _note_whether_it_stayed_up(proc, args.port, log_path)
    return 0


START_BAT_WAIT_SECONDS = 600
STAY_UP_SECONDS = 60


def _answers(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _note_whether_it_stayed_up(proc, port: int, log_path: str) -> None:
    """Once start.bat has reported the server up, watch the port for a minute and add one
    line to the log. A server that came up and then died left only start.bat's "answering"
    line before, which read as a good restart (seen once on a test copy, 2026-11-19)."""
    try:
        code = proc.wait(timeout=START_BAT_WAIT_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        return
    if code != 0 or not _answers(port):
        return
    started = time.monotonic()
    while time.monotonic() - started < STAY_UP_SECONDS:
        time.sleep(1)
        if not _answers(port):
            gone = round(time.monotonic() - started)
            _append_log(log_path, f"The restarted server stopped answering on port {port} about {gone} s "
                                  "after it came up. Run start.bat to start it again.")
            return
    _append_log(log_path, f"The restarted server was still answering on port {port} {STAY_UP_SECONDS} s later.")


def _append_log(path: str, text: str) -> None:
    try:
        with open(path, "a", encoding="utf-8") as log:
            log.write(text + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())

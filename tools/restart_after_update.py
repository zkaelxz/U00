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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    start_bat = os.path.join(args.root, "start.bat")
    if not os.path.isfile(start_bat):
        return 2
    # Without a stopped server there is nothing to restart; start.bat would
    # only report "already running".
    if not process_gone(args.pid, EXIT_WAIT_SECONDS):
        return 3
    if args.port:
        port_free(args.port, PORT_WAIT_SECONDS)
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    # start.bat's progress and any error stay readable in a console, but it opens minimized
    # and start.bat opens no app window of its own.
    startup = None
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=7)
    env = dict(os.environ, BAIHE_NO_APP_WINDOW="1")
    subprocess.Popen(["cmd.exe", "/c", start_bat], cwd=args.root, creationflags=flags,
                     startupinfo=startup, env=env, close_fds=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

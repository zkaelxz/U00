"""Asking what is listening (installer/service.py): the loopback health
checks, waiting for a condition, and whether this process is an
administrator.

Standard library only: this runs elevated from the admin folder.
"""

import json
import os
import socket
import time
import urllib.request

_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health_ok(port: int) -> bool:
    try:
        with _LOOPBACK_OPENER.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def household_is_baihe(port: int, domain: str) -> bool:
    """True if what listens on the household port is Baihe's household app:
    it answers /api/meta for the public name (any other Host is refused
    there) with app "Baihe Studio". A check against a wrong program, not
    proof against a deliberate impostor."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/meta", headers={"Host": domain})
    try:
        with _LOOPBACK_OPENER.open(req, timeout=2) as r:
            return r.status == 200 and json.loads(r.read(4096)).get("app") == "Baihe Studio"
    except Exception:
        return False


def port_answers(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_until(check, seconds: float, sleep=time.sleep) -> bool:
    for _ in range(int(seconds)):
        if check():
            return True
        sleep(1)
    return check()


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

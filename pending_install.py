"""
pending_install.py -- an install queued for the next start, and the apply step
that runs it before the server imports anything.

On Windows pip cannot replace a compiled file (cv2.pyd, numpy, torch, ...) that
the running server has loaded: it fails with "Access is denied" after it has
already removed the old version. So an install that would replace a loaded
package is written here instead, and `python -m pending_install` (run by
start.bat and the installed launcher before `python -m api`) performs it while
nothing is loaded.

Standard library only, and no import of the app: this process must not itself
load numpy or cv2, or it would hold the very files pip is about to replace. The
registry lookups and the redaction run in short-lived child processes
(pending_install_child.py) that exit before pip starts.

The pending file holds package keys only. The apply step re-derives the pip
command from the registry, so a tampered or hand-written file cannot add an
argument, an index URL or a version; the stored SHA-256 additionally catches a
damaged or edited file. It is not a signature: anyone who can write the data
folder could recompute it, which is why the file is never trusted for more than
names the registry already offers.
"""

import hashlib
import importlib.metadata
import json
import os
import re
import sys
import tempfile
import threading
import time

import portable
from lib.proc import run_captured, stream_tree

SCHEMA = 1
PIP_SECONDS = 40 * 60
RESTORE_SECONDS = 15 * 60
OVERALL_SECONDS = 60 * 60       # the server starts anyway after this
CHILD_SECONDS = 120
WATCHDOG_MARGIN = 90
TAIL_LINES = 40
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.!_-]{0,63}$")      # no "+": local builds aren't on PyPI
_FILES = {"pending": "pending.json", "result": "result.json", "lock": "apply.lock"}


def state_dir() -> str:
    return os.path.join(portable.data_dir(), "pending_install")


def _path(kind: str) -> str:
    return os.path.join(state_dir(), _FILES[kind])


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _write_json(kind: str, doc: dict) -> None:
    os.makedirs(state_dir(), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=state_dir(), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, sort_keys=True)
        os.replace(tmp, _path(kind))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _read_json(kind: str):
    try:
        with open(_path(kind), encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def _remove(kind: str) -> None:
    try:
        os.remove(_path(kind))
    except OSError:
        pass


def write_pending(keys, before: dict = None, now=time.time) -> dict:
    """Queue `keys` (registry package keys) for the next start. `before` maps
    dists the plan replaces to their current versions; it is shown to the
    owner, while the restore uses a fresh snapshot taken at apply time."""
    payload = {"schema": SCHEMA, "packages": list(keys), "created": int(now()),
               "before": dict(before or {})}
    _write_json("pending", {"payload": payload, "sha256": digest(payload)})
    _remove("result")
    return payload


def read_pending():
    """(payload, None) for a pending install that checks out, (None, None)
    when nothing is queued, (None, reason) for a file that must not run."""
    doc = _read_json("pending")
    if doc is None:
        return (None, None) if not os.path.exists(_path("pending")) else (None, "unreadable")
    payload = doc.get("payload")
    if not isinstance(payload, dict) or doc.get("sha256") != digest(payload):
        return None, "modified"
    keys = payload.get("packages")
    if (payload.get("schema") != SCHEMA or not isinstance(keys, list) or not keys
            or not all(isinstance(k, str) and _NAME_RE.match(k) for k in keys)):
        return None, "invalid"
    return payload, None


def cancel() -> bool:
    existed = os.path.exists(_path("pending"))
    _remove("pending")
    return existed


def read_result():
    return _read_json("result")


def clear_result() -> None:
    _remove("result")


def apply_running() -> bool:
    """True while a live apply holds the lock (a stale one doesn't count)."""
    try:
        return time.time() - os.path.getmtime(_path("lock")) < OVERALL_SECONDS + 120
    except OSError:
        return False


def status() -> dict:
    """What Diagnostics shows: the queued install, the last outcome, and
    whether an apply is running right now."""
    payload, problem = read_pending()
    result = read_result()
    if result and result.get("status") == "running" and not apply_running():
        result = {"status": "interrupted", "packages": result.get("packages", []),
                  "message": "The install did not finish: Baihe was closed while it ran.",
                  "tail": [], "restored": [], "restore_failed": []}
    return {"pending": payload, "problem": problem, "result": result,
            "applying": apply_running()}


# --- running things -------------------------------------------------------

# The watchdog sets "stop" so run_capture's cancel poll kills pip's whole tree.
_CURRENT = {"stop": False}


def run_capture(argv: list, timeout: float, echo: bool = False):
    """(returncode, last lines, timed_out). No shell; the tree is killed on
    timeout so pip's own children can't outlive the budget."""
    _CURRENT["stop"] = False
    lines, end = [], {"returncode": None, "timed_out": False}
    for event in stream_tree(argv, timeout=max(1.0, timeout), cancel=lambda: _CURRENT["stop"]):
        if "line" in event:
            lines.append(event["line"])
            del lines[:-200]
            if echo:
                print(event["line"], flush=True)
        else:
            end = event
    return end["returncode"], lines, end["timed_out"]


def _python() -> list:
    # -s as the launcher starts the server, so pip sees the same site-packages.
    return [sys.executable] + (["-s"] if sys.flags.no_user_site else [])


def child_json(*args, stdin_text: str = None):
    """Runs pending_install_child in a fresh interpreter and returns its last
    JSON line, or None. lib.proc gives a child no stdin, so `stdin_text`
    travels as a temp file whose path is the last argument."""
    text_path = None
    try:
        if stdin_text is not None:
            os.makedirs(state_dir(), exist_ok=True)
            fd, text_path = tempfile.mkstemp(prefix="redact-", suffix=".txt", dir=state_dir())
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(stdin_text)
        argv = _python() + ["-m", "pending_install_child", *args] + ([text_path] if text_path else [])
        proc = run_captured(argv, CHILD_SECONDS, cwd=os.path.dirname(os.path.abspath(__file__)))
    except OSError:
        return None
    finally:
        if text_path:
            try:
                os.remove(text_path)
            except OSError:
                pass
    for line in reversed(proc.stdout.splitlines()):
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        return doc if isinstance(doc, dict) else None
    return None


def snapshot() -> dict:
    """{canonical dist name: version} of everything installed (metadata only)."""
    out = {}
    for dist in importlib.metadata.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if name:
            out[re.sub(r"[-_.]+", "-", name).lower()] = dist.version
    return out


def _redacted_tail(lines: list) -> list:
    text = "\n".join(lines[-TAIL_LINES:])
    doc = child_json("redact", stdin_text=text)
    if doc and isinstance(doc.get("lines"), list):
        return [str(x) for x in doc["lines"]][-TAIL_LINES:]
    return ["(pip's output is hidden: it could not be cleaned of paths and names)"]


# --- the apply step -------------------------------------------------------

def _acquire_lock(wait_until: float, sleep=time.sleep) -> bool:
    os.makedirs(state_dir(), exist_ok=True)
    while True:
        try:
            fd = os.open(_path("lock"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(), "time": int(time.time())}, f)
            return True
        except FileExistsError:
            if not apply_running():
                _remove("lock")          # left behind by a crash
                continue
            if time.time() >= wait_until:
                return False
            sleep(1.0)


def _restore(before: dict, now_versions: dict, budget: float, echo: bool):
    """Puts back every package whose version changed or that went missing.
    Returns (restored, failed) dist names."""
    wanted = {n: v for n, v in before.items()
              if now_versions.get(n) != v}
    safe = {n: v for n, v in wanted.items() if _NAME_RE.match(n) and _VERSION_RE.match(v)}
    failed = sorted(set(wanted) - set(safe))
    if not safe:
        return [], failed
    argv = _python() + ["-m", "pip", "install", "--no-deps", "--no-cache-dir",
                        "--disable-pip-version-check",
                        *[f"{n}=={v}" for n, v in sorted(safe.items())]]
    rc, _lines, timed_out = run_capture(argv, budget, echo)
    after = snapshot()
    ok = [n for n, v in safe.items() if after.get(n) == v]
    return sorted(ok), sorted(failed + [n for n in safe if n not in ok])


def _finish(result: dict, lines: list) -> dict:
    result["tail"] = _redacted_tail(lines)
    result["finished"] = int(time.time())
    _write_json("result", result)
    return result


def apply(echo: bool = False, wait_seconds: float = OVERALL_SECONDS) -> dict:
    """Runs the queued install, if any. Never raises and never leaves the
    pending file behind; the outcome is in result.json. Returns a short
    summary dict for the caller and tests."""
    if not os.path.exists(_path("pending")):
        return {"ran": False}
    started = time.time()
    deadline = started + OVERALL_SECONDS
    if not _acquire_lock(started + wait_seconds):
        return {"ran": False, "locked": True}
    try:
        payload, problem = read_pending()
        if payload is None:
            _remove("pending")
            return {"ran": True, **_finish({
                "status": "refused", "packages": [], "restored": [], "restore_failed": [],
                "message": "A queued install was not run because its file did not check out. "
                           "Nothing was changed."}, [])}
        keys = payload["packages"]
        _remove("pending")        # a crash must not re-run it on every start
        _write_json("result", {"status": "running", "packages": keys, "tail": [],
                               "message": "Installing the packages you queued...",
                               "restored": [], "restore_failed": []})
        plan = child_json("derive", ",".join(keys))
        if not plan or not plan.get("ok"):
            return {"ran": True, **_finish({
                "status": "refused", "packages": keys, "restored": [], "restore_failed": [],
                "message": (plan or {}).get("message") or
                           "The queued install was not run: the package list was not accepted. "
                           "Nothing was changed."}, [])}
        before = snapshot()
        argv = plan["argv"]
        argv[1:1] = _python()[1:]
        if echo:
            print("Installing the packages you queued: " + ", ".join(keys), flush=True)
        try:
            rc, lines, timed_out = run_capture(argv, min(PIP_SECONDS, deadline - time.time()), echo)
        finally:
            for path in plan.get("temp_files", []):
                try:
                    os.remove(path)
                except OSError:
                    pass
        if rc == 0 and not timed_out:
            return {"ran": True, **_finish({
                "status": "ok", "packages": keys, "restored": [], "restore_failed": [],
                "message": "Installed " + ", ".join(keys) + " when Baihe started."}, lines)}
        restored, failed = _restore(before, snapshot(),
                                    min(RESTORE_SECONDS, max(60.0, deadline - time.time())), echo)
        if timed_out:
            head = "The install did not finish in time and was stopped. "
        else:
            head = "The install did not work. "
        if failed:
            tail = ("Some packages could not be put back: " + ", ".join(failed) +
                    ". Install them again from Packages in Diagnostics.")
        elif restored:
            tail = "Your earlier packages were put back."
        else:
            tail = "Nothing else was changed."
        return {"ran": True, **_finish({
            "status": "timed_out" if timed_out else "failed", "packages": keys,
            "restored": restored, "restore_failed": failed, "message": head + tail}, lines)}
    finally:
        _remove("lock")


def main(argv=None) -> int:
    """Always 0: a failed or slow install must not stop the server starting."""
    def watchdog():
        _CURRENT["stop"] = True
        time.sleep(2)       # the cancel poll runs about twice a second
        try:
            _write_json("result", {"status": "timed_out", "packages": [], "tail": [],
                                   "restored": [], "restore_failed": [],
                                   "message": "The install did not finish in time. Baihe "
                                              "started without waiting for it.",
                                   "finished": int(time.time())})
        except OSError:
            pass
        _remove("lock")
        os._exit(0)
    timer = threading.Timer(OVERALL_SECONDS + WATCHDOG_MARGIN, watchdog)
    timer.daemon = True
    timer.start()
    try:
        apply(echo=True)
    except Exception as exc:        # the server start comes first
        print(f"Queued install skipped: {type(exc).__name__}", flush=True)
        _remove("lock")
    finally:
        timer.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(main())

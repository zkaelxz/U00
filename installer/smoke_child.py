"""
installer/smoke_child.py -- CI only (the Windows installer workflow's smoke
test; not shipped). Stands in for a long-running child the server started
(an ffmpeg, a Chromium): starts `ping -n 300 127.0.0.1` and puts it into the
installed server's Job Object (process_guard.py), then prints its pid. The
smoke test then runs `launcher.py --stop` and checks the ping is gone.

    python installer/smoke_child.py <install dir>

With --breakaway-check it proves the server's Job Object rules instead: a
stand-in server (this file, --stand-in-server) calls
process_guard.contain_children() exactly as `python -m api` does and starts
an ordinary child, a child in its own nested kill-on-close job (as
lncrawl_service does), and a child with CREATE_BREAKAWAY_FROM_JOB inside
process_guard.breakaway_allowed() (how the update installer's Setup is
started, services/update_service.py). The same breakaway request just before
and just after that window must be refused. Ending the job the way
`launcher.py --stop` does must end the ordinary and nested children and leave
the Setup-style one running. Exit 0 only if all of that holds.

    python installer/smoke_child.py --breakaway-check
"""

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import process_guard  # noqa: E402

CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def main(argv) -> int:
    name = process_guard.group_name_for(Path(argv[1]))
    cmd = ["ping", "-n", "300", "127.0.0.1"]
    try:
        # Out of the CI step's own job first, so it can join the server's.
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not process_guard.add_process_to_group(name, proc.pid):
        proc.kill()
        print(f"Couldn't put the stand-in child into {name}", file=sys.stderr)
        return 1
    print(proc.pid)
    return 0


PING = ["ping", "-n", "300", "127.0.0.1"]


def _quiet(cmd, **kw):
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def _breakaway_child():
    try:
        return _quiet(PING, creationflags=CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        return None


def stand_in_server(name) -> int:
    if not process_guard.contain_children(name):
        print("fail no-job", flush=True)
        return 1
    ordinary = _quiet(PING)
    nested = _quiet(PING)
    nested_job = process_guard.create_kill_on_close_job(int(nested._handle))  # kept open
    before = _breakaway_child()
    with process_guard.breakaway_allowed() as opened:
        setup = _breakaway_child()
    after = _breakaway_child()
    problems = [text for text, bad in (
        ("breakaway-before-the-window", before is not None),
        ("breakaway-after-the-window", after is not None),
        ("window-not-opened", not opened),
        ("setup-launch-refused", setup is None),
        ("no-nested-job", not nested_job)) if bad]
    if problems:
        # Anything that got out would outlive the job: end it here.
        for proc in (before, after, setup):
            if proc is not None:
                proc.kill()
        print("fail " + " ".join(problems), flush=True)
        time.sleep(300)
        return 1
    print(f"ok {ordinary.pid} {nested.pid} {setup.pid}", flush=True)
    time.sleep(300)
    return 0


def _running(pid) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                         text=True, errors="replace", timeout=30).stdout
    return str(pid) in out.split()


def _kill(pid) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=30)


def breakaway_check() -> int:
    name = f"Local\\BaiheStudio-smoke-{os.getpid()}"
    cmd = [sys.executable, str(Path(__file__).resolve()), "--stand-in-server", name]
    try:
        # Out of the CI step's own job first, as the installed server is.
        server = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors="replace",
                                  creationflags=CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        server = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors="replace")
    words = (server.stdout.readline() or "").split()
    if not words or words[0] != "ok":
        process_guard.terminate_group(name)
        print(f"The stand-in server's job rules are wrong: {' '.join(words) or 'no answer'}",
              file=sys.stderr)
        return 1
    ordinary, nested, setup = int(words[1]), int(words[2]), int(words[3])
    process_guard.terminate_group(name)      # what launcher.py --stop does last
    time.sleep(3)
    left = {pid: _running(pid) for pid in (ordinary, nested, setup)}
    for pid in left:
        _kill(pid)
    ok = True
    if left[ordinary]:
        print("An ordinary child outlived the server's job.", file=sys.stderr)
        ok = False
    if left[nested]:
        print("A child in a nested job outlived the server's job.", file=sys.stderr)
        ok = False
    if not left[setup]:
        print("The Setup-style breakaway child was ended with the server's job.", file=sys.stderr)
        ok = False
    if not ok:
        return 1
    print("Ordinary and nested children end with the server's job; breakaway is let out only "
          "inside the Setup launch window.")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--stand-in-server"]:
        sys.exit(stand_in_server(sys.argv[2]))
    if sys.argv[1:2] == ["--breakaway-check"]:
        sys.exit(breakaway_check())
    sys.exit(main(sys.argv))

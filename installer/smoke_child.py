"""
installer/smoke_child.py -- CI only (the Windows installer workflow's smoke
test; not shipped). Stands in for a long-running child the server started
(an ffmpeg, a Chromium): starts `ping -n 300 127.0.0.1` and puts it into the
installed server's Job Object (process_guard.py), then prints its pid. The
smoke test then runs `launcher.py --stop` and checks the ping is gone.

    python installer/smoke_child.py <install dir>
"""

import os
import subprocess
import sys
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


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""lib/proc_kill.py -- killing a child process together with everything it started."""

import logging
import os
import subprocess

# Logs the exception type only: kill failures carry no detail worth the risk
# of a path or argument ending up in a log line.
_log = logging.getLogger(__name__)


def kill_tree(proc):
    """Kills proc and everything it started (it runs in its own process
    group/session -- see run_cancellable), so a wrapper script's ffmpeg
    grandchild can't keep the pipes open."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass   # the group already exited
    except Exception as exc:
        _log.warning("could not kill process tree %s: %s", proc.pid, type(exc).__name__)
    try:
        proc.kill()
    except Exception as exc:
        _log.warning("could not kill process %s: %s", proc.pid, type(exc).__name__)

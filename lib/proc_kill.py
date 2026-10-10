"""lib/proc_kill.py -- killing a child process together with everything it started."""

import logging
import os
import subprocess

# The app's logger by name (applog owns its file handler and secret filter,
# but lib can't import it). Only the exception type is logged.
_log = logging.getLogger("baihe")


def kill_tree(proc, warn=None):
    """Kills proc and everything it started (it runs in its own process
    group/session -- see run_cancellable), so a wrapper script's ffmpeg
    grandchild can't keep the pipes open. `warn(what, exc)` reports a swallowed
    failure; the default only logs the exception type."""
    warn = warn or (lambda what, exc: _log.warning("%s: %s", what, type(exc).__name__))
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
        warn(f"could not kill process tree {proc.pid}", exc)
    try:
        proc.kill()
    except Exception as exc:
        warn(f"could not kill process {proc.pid}", exc)

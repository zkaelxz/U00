"""jobs/job_settings.py -- the Settings-page knobs for background jobs.

The GPU guard toggle, how many GPU jobs may run at once, and whether a
finished job raises a desktop notice, all read fresh from db.app_settings so
another process sees the same value; plus the completion notice itself.
"""

from jobs.job_table import _warn
from lib import settings_schema


def _setting(key):
    import db
    return settings_schema.coerce(key, db.get_app_setting(key))


def get_gpu_limit_enabled() -> bool:
    try:
        return bool(_setting("gpu_limit_enabled"))
    except Exception as exc:
        # Never let a DB hiccup block a job from starting -- the GPU
        # guard is a soft, best-effort convenience, not a correctness
        # requirement. On a read error the limit stays on.
        _warn("could not read the GPU-limit setting; keeping the limit on", exc)
        return True


def set_gpu_limit_enabled(enabled: bool):
    import db
    db.set_app_setting("gpu_limit_enabled", bool(enabled))


# How many GPU-touching jobs may run at once while the GPU limit is on,
# across this process and any other process using the GPU slots. 1 (the default) is one at a time.
# The upper bound matches gpu_slots.GPU_LOCK_MAX_SLOTS (the gpu_lock table's CHECK).
GPU_MAX_PARALLEL_LIMIT = 4


def _clamp_gpu_max_parallel(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(n, GPU_MAX_PARALLEL_LIMIT))


def get_gpu_max_parallel() -> int:
    try:
        n = _setting("gpu_max_parallel")
        return n if isinstance(n, int) else 1
    except Exception as exc:
        # Fails safe: one GPU job at a time.
        _warn("could not read the GPU jobs-at-once setting; using 1", exc)
        return 1


def set_gpu_max_parallel(value: int):
    import db
    db.set_app_setting("gpu_max_parallel", _clamp_gpu_max_parallel(value))


# An optional local desktop notification when a
# background job finishes, so a long job (especially a bulk
# series-translate, which can run unattended for a while) doesn't
# require watching the page. Off by default -- a Settings toggle turns it
# on via set_notify_on_completion() below. Backed by db.app_settings, same
# reasoning as get/set_gpu_limit_enabled() above.


def get_notify_on_completion() -> bool:
    try:
        return bool(_setting("notify_on_completion"))
    except Exception:
        # _notify_job_finished's own contract is "never raises" -- a DB
        # hiccup here must not break the job it's reporting on. Fails
        # closed (no notification), the safer default.
        return False


def set_notify_on_completion(enabled: bool):
    import db
    db.set_app_setting("notify_on_completion", bool(enabled))


def _notify_job_finished(description, status, job_id=None, owner_user_id=None,
                         with_errors=False, reason=None):
    """Best-effort only -- never raises. A missing `plyer` install, or no
    notification daemon at all (common on a minimal Linux desktop), must
    never take down the job runner that calls this right after finishing
    the job's real work.

    Also queues a Discord/ntfy push when a channel is configured
    (services/notification_service; its own opt-in is configuring a
    channel, independent of the desktop toggle). That call only queues --
    the send happens on a timer thread -- and never raises. It also keeps
    the event for the in-app list, shown by `job_id`/`owner_user_id`
    visibility."""
    try:
        from services import notification_service
        notification_service.notify_job_finished(description, status, job_id=job_id,
                                                 owner_user_id=owner_user_id,
                                                 with_errors=with_errors, reason=reason)
    except Exception:
        pass
    if not get_notify_on_completion():
        return
    try:
        from plyer import notification
        notification.notify(
            title="Baihe Studio",
            message=(f"Finished with errors: {description}" if status == "done" and with_errors
                     and description else
                     f"Finished: {description}" if status == "done" and description else
                     "Finished: background job" if status == "done" else
                     f"Failed: {description}" if description else "Failed: background job"),
            timeout=10)
    except Exception:
        pass

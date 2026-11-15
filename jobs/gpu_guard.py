"""jobs/gpu_guard.py -- makes Baihe give way when the GPU runs short of memory or gets hot.

The PC is shared with programs Baihe cannot see or limit (Jellyfin's
transcodes are the case this was built for, and the owner does not want them
capped), so Baihe is the one that yields. `tick` runs on the API's GPU-queue
poller: it reads the card, and while free memory is under the reserve or the
card is past its temperature limit it
- holds queued GPU jobs (gpu_slots.acquire asks `hold_reason`),
- unloads idle models (Ollama's and this process's caches) when memory is short,
- after the set minutes, stops the running jobs that registered a restart
  (`allow_restart`) and starts them again through the same call the user
  made, so they queue, wait for the card to clear and pick up as a fresh start.

Only jobs whose re-start is known to be clean register (see docs/background-jobs.md,
"Giving way to other programs"). Nothing here raises into a caller, and a card
that can't be read never holds or stops anything.
"""

import contextvars
import threading
import time

from jobs.job_table import _jobs, _lock, _mirror_locked, _warn

ENABLED_KEY = "gpu_guard"
TEMP_KEY = "gpu_guard_temp_c"
STOP_MINUTES_KEY = "gpu_guard_stop_minutes"

# A hold ends only once the card has stayed this far clear for CLEAR_SECONDS,
# so a reading that hovers at the limit doesn't start and stop jobs in turn.
CLEAR_MARGIN_MB = 1024
CLEAR_MARGIN_C = 5
CLEAR_SECONDS = 60
# The poller ticks every 20 s; a state older than this means it stopped, and
# an old reading must not keep jobs held.
STALE_SECONDS = 90
# Ollama is asked again at most this often while memory stays short.
UNLOAD_EVERY_SECONDS = 60
# A job stopped this many times in a row is not restarted again: its own
# models may be what fills the card, and a loop would never finish it.
MAX_RESTARTS = 3
# A restart refused as "busy" (a stopped translation's last request still
# running at Ollama) is tried again on later ticks for this long.
RESTART_RETRY_SECONDS = 900
NVIDIA_SMI_TIMEOUT_SECONDS = 5

_guard_lock = threading.Lock()
_state: dict = {}
# job id -> {"run", "restart", "principal", "restarts"}: the run that may be
# stopped and how to start it again.
_restartable: dict = {}
# job id -> {"restart", "principal", "restarts", "why", "stopped_at", "run"}:
# stopped by the guard, not started again yet.
_pending: dict = {}


def _fresh_state() -> dict:
    return {"at": None, "short": False, "hot": False, "since": None, "clear_since": None,
            "free_mb": None, "min_free_mb": None, "temp_c": None, "temp_limit": None,
            "unloaded_at": None}


_state.update(_fresh_state())


# --- settings and readings ---------------------------------------------------

def settings() -> dict:
    """{"enabled", "temp_limit", "stop_minutes", "min_free_mb"}. A settings
    read problem keeps the defaults."""
    out = {"enabled": True, "temp_limit": 85, "stop_minutes": 2}
    try:
        from services import settings_service
        out["enabled"] = bool(settings_service.get(ENABLED_KEY))
        for name, key in (("temp_limit", TEMP_KEY), ("stop_minutes", STOP_MINUTES_KEY)):
            value = settings_service.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                out[name] = value
    except Exception as exc:
        _warn("GPU guard: settings could not be read; using the defaults", exc)
    out["min_free_mb"] = min_free_mb()
    return out


def min_free_mb() -> float:
    """The free graphics memory below which GPU jobs are held: the same
    number the job-start check uses."""
    import diagnostics_torch
    return diagnostics_torch.external_busy_min_free_mb()


def read_card():
    """(free_mb, temp_c) of the first GPU nvidia-smi lists, the one jobs run
    on (cuda:0) and the other GPU checks read; None when it can't be read.
    temp_c is None on a card that doesn't report it."""
    import shutil
    from lib.proc import run_captured
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = run_captured(["nvidia-smi", "--query-gpu=memory.used,memory.total,temperature.gpu",
                               "--format=csv,noheader,nounits"], timeout=NVIDIA_SMI_TIMEOUT_SECONDS)
        if result.timed_out or result.returncode != 0:
            return None
        fields = [x.strip() for x in result.stdout.strip().splitlines()[0].split(",")]
        used, total = float(fields[0]), float(fields[1])
        try:
            temp = float(fields[2])
        except (IndexError, ValueError):
            temp = None   # "[N/A]"
        return total - used, temp
    except Exception:
        return None


# --- the state ---------------------------------------------------------------

def _step(state: dict, reading, conf: dict, now: float) -> dict:
    """One reading through the hold state. Enters at once on a reading past a
    limit; leaves once every reading has been clear by the margins for
    CLEAR_SECONDS. No reading clears it: a guess must not hold jobs."""
    state = dict(state)
    if reading is None:
        return {**_fresh_state(), "unloaded_at": state.get("unloaded_at")}
    free, temp = reading
    floor, limit = conf["min_free_mb"], conf["temp_limit"]
    state.update(at=now, free_mb=free, temp_c=temp, min_free_mb=floor, temp_limit=limit)
    short_now = free < floor
    hot_now = temp is not None and temp >= limit
    if short_now or hot_now:
        if not (state["short"] or state["hot"]):
            state["since"] = now
        state["short"] = state["short"] or short_now
        state["hot"] = state["hot"] or hot_now
        state["clear_since"] = None
        return state
    if not (state["short"] or state["hot"]):
        return state
    clear = (free >= floor + CLEAR_MARGIN_MB) and (temp is None or temp <= limit - CLEAR_MARGIN_C)
    if not clear:
        state["clear_since"] = None
        return state
    state["clear_since"] = state["clear_since"] or now
    if now - state["clear_since"] >= CLEAR_SECONDS:
        state.update(short=False, hot=False, since=None, clear_since=None)
    return state


def _why(state: dict) -> str:
    if state["short"]:
        return (f"low GPU memory ({state['free_mb'] / 1024:.1f} GB free, "
                f"{state['min_free_mb'] / 1024:.1f} GB kept free)")
    return f"the GPU is hot ({state['temp_c']:.0f} °C, limit {state['temp_limit']} °C)"


def _held_text(why: str) -> str:
    return f"Held: {why}. Starts by itself when the GPU is clear."


def _stopped_text(why: str) -> str:
    return f"Stopped: {why}. Starts again by itself when the GPU is clear."


def hold_reason(now: float | None = None):
    """The queued-job message while GPU jobs are held, else None. Reads only
    the last tick's state (no nvidia-smi), so it is safe under job_table._lock."""
    now = time.time() if now is None else now
    with _guard_lock:
        state = dict(_state)
    if not (state["short"] or state["hot"]) or state["at"] is None or now - state["at"] > STALE_SECONDS:
        return None
    return _held_text(_why(state))


def status() -> dict:
    """What the guard sees, for Diagnostics and tests. Numbers only."""
    with _guard_lock:
        state = dict(_state)
        pending = len(_pending)
    return {"short": state["short"], "hot": state["hot"], "free_mb": state["free_mb"],
            "min_free_mb": state["min_free_mb"], "temp_c": state["temp_c"],
            "since": state["since"], "pending_restarts": pending}


# --- restartable jobs --------------------------------------------------------

def allow_restart(job_id: str, restart) -> None:
    """Lets the guard stop job_id's current run and start it again with
    `restart()` (called with the starter's principal, so ownership and caps
    are checked as they were). Call it right after a start returned True,
    only for a job whose stop-and-start again is clean."""
    from services import ownership_service
    with _lock:
        run = _jobs.get(job_id)
    if run is None or run.get("status") not in ("queued", "running"):
        return
    with _guard_lock:
        _restartable[job_id] = {"run": run, "restart": restart, "restarts": 0,
                                "principal": ownership_service.acting_principal()}


def _forget_ended_locked() -> None:
    """Caller holds both locks. Drops registrations of runs that ended or were
    replaced. A queued process job gets a new job dict when it is promoted
    (job_runner._register_process_job), which is the same run: the
    registration follows it. The queued dict is left behind as it was, so it
    still says "queued"; a run cancelled or cleared while queued doesn't."""
    for job_id, entry in list(_restartable.items()):
        run = _jobs.get(job_id)
        if run is not None and run is not entry["run"] and entry["run"].get("status") == "queued" \
                and run.get("kind") == "process" and run.get("status") == "running":
            entry["run"] = run
        if run is None or run is not entry["run"] or run.get("status") not in ("queued", "running"):
            del _restartable[job_id]


def _call_as(principal, fn):
    """fn() with `principal` as the acting principal, in a context of its own."""
    from services import ownership_service

    def run():
        token = ownership_service.bind_request()
        try:
            if principal is not None:
                ownership_service.note_acting_principal(principal)
            return fn()
        finally:
            ownership_service.unbind_request(token)
    return contextvars.Context().run(run)


# --- one tick ----------------------------------------------------------------

def _any_job_running() -> bool:
    with _lock:
        return any(j.get("status") == "running" for j in _jobs.values())


def _free_idle_models(state: dict, now: float) -> None:
    """Unloads what no running job needs. Only while nothing in Baihe runs: a
    running job may be using any of them, and the caches can't say which."""
    if state["unloaded_at"] is not None and now - state["unloaded_at"] < UNLOAD_EVERY_SECONDS:
        return
    if _any_job_running():
        return
    state["unloaded_at"] = now
    try:
        import whisper_models
        whisper_models.release_gpu_models()
    except Exception as exc:
        _warn("GPU guard: freeing the app's models failed", exc)
    try:
        import ollama_unload
        count = ollama_unload.unload_loaded_models()
        if count:
            _log(f"GPU guard: GPU memory is short; asked Ollama to unload {count} model(s)")
    except Exception as exc:
        _warn("GPU guard: unloading Ollama's models failed", exc)


def _stop_running(state: dict, now: float, stop_minutes: int) -> None:
    import background_jobs
    if stop_minutes <= 0 or state["since"] is None or now - state["since"] < stop_minutes * 60:
        return
    stop = []
    with _lock, _guard_lock:
        _forget_ended_locked()
        for job_id, entry in list(_restartable.items()):
            run = entry["run"]
            if run.get("status") != "running" or not run.get("gpu_touching") \
                    or run.get("cancel_requested") or job_id in _pending:
                continue
            stop.append(job_id)
            _pending[job_id] = {**entry, "why": _why(state), "stopped_at": now}
            # The message the run keeps once it is cancelled (job_runner._mark_cancelled_locked),
            # so a guard stop doesn't read like the user's own Cancel.
            run["stop_message"] = _stopped_text(_why(state))
            del _restartable[job_id]
    for job_id in stop:
        _log(f"GPU guard: stopping job {job_id}: {_why(state)}")
        background_jobs.request_cancel(job_id)
        _set_message(_pending[job_id]["run"], f"Stopping: {_pending[job_id]['why']}.")


def _set_message(run, text) -> None:
    with _lock:
        for job_id, job in _jobs.items():
            if job is run:
                job["message"] = text
                _mirror_locked(job_id)
                return


def _restart_pending(state: dict, now: float) -> None:
    from jobs import job_launch
    from jobs.job_settings import get_gpu_limit_enabled
    from lib.errors import ConflictError
    with _guard_lock:
        pending = list(_pending.items())
    for job_id, entry in pending:
        run = entry["run"]
        if run.get("status") in ("queued", "running"):
            continue   # still stopping
        if run.get("status") != "cancelled":
            # It finished (or failed) before the stop took: nothing to start again.
            with _guard_lock:
                _pending.pop(job_id, None)
            continue
        if job_launch.is_stopping():
            with _guard_lock:
                _pending.pop(job_id, None)
            continue
        if entry["restarts"] >= MAX_RESTARTS:
            with _guard_lock:
                _pending.pop(job_id, None)
            _set_message(run, f"Stopped: {entry['why']}. Stopped {entry['restarts'] + 1} times, "
                              "so it was not started again.")
            continue
        # With the GPU limit off a start is never queued, so it would load at
        # once on a card that is still short: wait for the card to clear.
        if (state["short"] or state["hot"]) and not get_gpu_limit_enabled():
            _set_message(run, _stopped_text(entry["why"]))
            continue
        try:
            _call_as(entry["principal"], entry["restart"])
        except ConflictError:
            if now - entry["stopped_at"] < RESTART_RETRY_SECONDS:
                _set_message(run, _stopped_text(entry["why"]))
                continue
            with _guard_lock:
                _pending.pop(job_id, None)
            _set_message(run, f"Stopped: {entry['why']}. It could not start again: it was still busy.")
            continue
        except Exception as exc:
            from engine_backends.redaction import redact_secrets
            with _guard_lock:
                _pending.pop(job_id, None)
            _warn(f"GPU guard: job {job_id} could not be started again", exc)
            _set_message(run, f"Stopped: {entry['why']}. It did not start again: "
                              + redact_secrets(str(exc))[:200])
            continue
        with _guard_lock:
            _pending.pop(job_id, None)
            again = _restartable.get(job_id)
            if again is not None:
                again["restarts"] = entry["restarts"] + 1
        _log(f"GPU guard: job {job_id} started again")


def tick(now: float | None = None, reading=...) -> dict:
    """One guard step (the API's GPU-queue poller, before it re-checks the
    queue). `reading` is for tests. Never raises; returns status()."""
    now = time.time() if now is None else now
    try:
        conf = settings()
        if not conf["enabled"]:
            with _guard_lock:
                _state.clear()
                _state.update(_fresh_state())
                _pending.clear()
            return status()
        reading = read_card() if reading is ... else reading
        with _guard_lock:
            state = _step(_state, reading, conf, now)
        if state["short"]:
            _free_idle_models(state, now)
        _stop_running(state, now, conf["stop_minutes"])
        with _guard_lock:
            _state.clear()
            _state.update(state)
        _restart_pending(state, now)
    except Exception as exc:
        _warn("GPU guard tick failed", exc)
    return status()


def _log(text: str) -> None:
    try:
        import applog
        applog.get_logger().warning(text)
    except Exception:
        pass


def reset_for_tests() -> None:
    with _guard_lock:
        _state.clear()
        _state.update(_fresh_state())
        _restartable.clear()
        _pending.clear()

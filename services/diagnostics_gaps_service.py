"""
services/diagnostics_gaps_service.py -- the parts of the Streamlit
Diagnostics tab that services/diagnostics_service.py's read-only overview
does not cover yet: setup checks (as check_setup.py reports them), model
versions, model-cache listing, pyannote/HF-token readiness, finished-job
history, a shareable support report, a capped log tail, and thin
confirm-gated wrappers over the admin actions that already exist as
non-UI functions (dependency install/upgrade, library reset).

No Streamlit import, no HTTP types. Every string that could carry a
secret, a token, the OS username or a local path goes through
diagnostics.redact_for_support (which applies
translate_engines.redact_secrets first); nothing here returns a path.

Not ported (Streamlit-only by decision, see
docs/streamlit-retirement-plan.md): accuracy benchmark, bug-reproduction
bundles, App Assistant, and the source-access tests.
"""

import os
import re
import shutil
import sys
import tempfile
import threading
import time

import background_jobs
import db
import diagnostics
from services.service_errors import ConflictError, InvalidInputError, NotFoundError, ServiceError

LOG_TAIL_DEFAULT = 50
LOG_TAIL_MAX = 200
_ADMIN_OUTPUT_TAIL = 40

# job_id prefixes this app actually uses (see background_jobs.start_job
# call sites) -- everything before the last "_<drama_id>" segment.
_JOB_LABELS = {
    "translate": "Translating",
    "transcribe": "Transcribing / reading captions",
    "flag": "Review queue (flagging lines)",
    "emotion": "Detecting emotional register",
    "consistency": "Checking translation consistency",
    "notes": "Generating translation notes",
    "diarize": "Detecting speakers",
}


def _redact(text) -> str:
    return diagnostics.redact_for_support("" if text is None else str(text))


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def describe_job(job_id: str) -> str:
    """Turns a raw job_id like 'emotion_42' into 'Detecting emotional
    register -- Some Drama Title', so the jobs list means something at a
    glance instead of showing internal id strings."""
    if job_id == "live_capture":
        return "🔴 Live capture"
    prefix, _, suffix = job_id.rpartition("_")
    if prefix in _JOB_LABELS and suffix.isdigit():
        drama = db.get_drama(int(suffix))
        title = (drama.get("title_en") or drama.get("title_zh") or f"drama #{suffix}") if drama else f"drama #{suffix} (deleted)"
        return f"{_JOB_LABELS[prefix]} -- {title}"
    return job_id


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_setup_checks(project_root: str = None, library_dir: str = None) -> dict:
    """Core requirements and file checks, the same facts check_setup.py
    prints at launch plus file completeness. Paths are never returned:
    ffmpeg/JS runtime report found/name/version only."""
    project_root = project_root or _project_root()
    library_dir = library_dir or db.LIBRARY_DIR
    py = diagnostics.check_python_version()
    ff = diagnostics.check_ffmpeg()
    js = diagnostics.check_js_runtime()
    cuda = diagnostics.check_cuda()
    files = diagnostics.check_file_completeness(project_root)
    return {
        "python": {"version": py.get("version"), "ok": bool(py.get("ok"))},
        "ffmpeg": {"found": bool(ff.get("found")),
                   "version": _redact(ff["version"]) if ff.get("version") else None},
        "js_runtime": {"found": bool(js.get("found")), "name": js.get("name")},
        "cuda": {"torch_installed": bool(cuda.get("torch_installed")),
                 "cuda_available": cuda.get("cuda_available")},
        "files": {"all_present": bool(files["all_present"]),
                  "missing_top_level": list(files["missing_top_level"]),
                  "missing_tabs": list(files["missing_tabs"])},
        "library_writable": bool(diagnostics.check_library_writable(library_dir)),
    }


def get_model_versions(ollama_model: str = None) -> list:
    """Model/engine version rows (local only, no network)."""
    return [{"name": m["name"], "version": _redact(m["version"]), "url": m["url"],
             "installed": bool(m["installed"]), "package": m.get("package"),
             "help": m.get("help", "")}
            for m in diagnostics.get_model_engine_versions(ollama_model)]


def get_model_cache(hf_cache_dir: str = None, piper_voices_dir: str = None) -> dict:
    """Hugging Face cache revisions and Piper voices by name and size --
    no directory is ever included."""
    hf = [{"repo_id": e["repo_id"], "repo_type": e["repo_type"],
           "revision": e["revision"], "size_bytes": int(e["size_bytes"])}
          for e in diagnostics.scan_hf_cache(hf_cache_dir)]
    piper = [{"voice": e["voice"], "size_bytes": int(e["size_bytes"])}
             for e in diagnostics.scan_piper_voices(piper_voices_dir)]
    return {
        "hf_cache": hf,
        "hf_total_bytes": sum(e["size_bytes"] for e in hf),
        "piper_voices": piper,
        "piper_total_bytes": sum(e["size_bytes"] for e in piper),
    }


def _hf_hub_importable() -> bool:
    """True when huggingface_hub can be imported. Without it the access
    check can't run, so models stays None (the UI's "can't check" case)
    instead of an empty list."""
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        return False
    return True


def get_pyannote_readiness(check_access: bool = False, api=None) -> dict:
    """Booleans only: is pyannote.audio installed, is an HF token
    configured, and (only when check_access=True -- this reaches the
    network) can that token open each gated model. models stays None when
    the check wasn't asked for or huggingface_hub isn't installed. The
    token and any raw error text are never returned."""
    from services import settings_service
    token = settings_service.resolve_key("hf_token")
    out = {
        "pyannote_installed": bool(diagnostics.check_dependency("pyannote.audio")),
        "hf_token_configured": bool(token),
        "models": None,
    }
    if check_access and (api is not None or _hf_hub_importable()):
        results = diagnostics.check_pyannote_gated_access(token, api=api)
        out["models"] = [{"model": r["model"], "accessible": bool(r["accessible"])}
                         for r in results]
    out["ready"] = bool(out["pyannote_installed"] and out["hf_token_configured"]
                        and (out["models"] is None or all(m["accessible"] for m in out["models"])))
    return out


def get_job_history() -> list:
    """Finished jobs still in this process's memory, newest first, with
    redacted message/error and total duration."""
    finished = {jid: j for jid, j in background_jobs.list_all_jobs().items()
                if j.get("status") != "running"}
    out = []
    for jid in sorted(finished, key=lambda j: finished[j].get("finished_at") or 0, reverse=True):
        job = finished[jid]
        started, ended = job.get("started_at"), job.get("finished_at")
        out.append({
            "job_id": jid,
            "label": _redact(describe_job(jid)),
            "status": job.get("status"),
            "description": _redact(job.get("description")) or None,
            "message": _redact(job.get("message")),
            "error": _redact(job.get("error")) or None,
            "gpu_touching": bool(job.get("gpu_touching")),
            "started_at": started,
            "finished_at": ended,
            "duration_seconds": (ended - started) if started and ended else None,
        })
    return out


def get_log_tail(n: int = LOG_TAIL_DEFAULT, keyword: str = "") -> list:
    """The last n (capped at LOG_TAIL_MAX) redacted log lines, optionally
    filtered by keyword."""
    import applog
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = LOG_TAIL_DEFAULT
    n = max(0, min(n, LOG_TAIL_MAX))
    if n == 0:
        return []
    # Redact first, then filter: filtering raw lines would let a keyword
    # probe for text that redaction hides (a path, a user name, a key).
    return applog.filter_lines([_redact(ln) for ln in applog.tail(n)], keyword or "")


def build_support_report(recent_error_lines: int = 20) -> str:
    """Plain-text summary safe to share: versions, OS, dependency status,
    model versions, cache totals, which keys are set (never values), and
    recent log errors -- all passed through redact_for_support."""
    import platform
    from services import settings_service
    results = diagnostics.run_full_diagnostics(
        _project_root(), db.LIBRARY_DIR, settings_service.key_status())
    cache = diagnostics.scan_hf_cache()
    report = diagnostics.format_diagnostics_report(
        results, cache, diagnostics.get_model_engine_versions())
    extra = [f"OS: {platform.system()} {platform.release()} ({platform.machine()})"]
    errors = [ln for ln in get_log_tail(LOG_TAIL_MAX)
              if "ERROR" in ln or "Traceback" in ln][-max(0, int(recent_error_lines)):]
    extra.append("Recent errors:" if errors else "Recent errors: none")
    extra.extend(f"  {ln}" for ln in errors)
    return _redact(report + "\n" + "\n".join(extra))


# ---------------------------------------------------------------------------
# Admin actions (router: local_only). Each requires confirm=True and
# refuses while any background job runs.
# ---------------------------------------------------------------------------

RESET_CONFIRM_TEXT = "RESET"


class AdminActionRefused(ServiceError):
    """Raised when an admin action is not confirmed, targets an unknown
    package, or jobs are running. The subclasses below carry the HTTP
    mapping (422 / 404 / 409); callers can keep catching this one."""


class AdminActionUnconfirmed(AdminActionRefused, InvalidInputError):
    pass


class AdminActionUnknownPackage(AdminActionRefused, NotFoundError):
    pass


class AdminActionJobsRunning(AdminActionRefused, ConflictError):
    pass


class AdminActionStale(AdminActionRefused, ConflictError):
    """The confirmed version is no longer what the last update check found,
    or a check/probe is already running (409)."""


class AdminActionNotPossible(AdminActionRefused, InvalidInputError):
    """The action can't work on this machine (no NVIDIA GPU, a driver too
    old for CUDA 12, an unsupported Python) or goes through another action
    (torch's Upgrade is the GPU PyTorch setup)."""


def _maintenance_active() -> bool:
    # background_jobs has no public reader for the counter that
    # enter_maintenance()/exit_maintenance() keep.
    return bool(getattr(background_jobs, "_maintenance_count", 0))


PIP_TIMEOUT_SECONDS = diagnostics.UPGRADE_CHECK_PIP_TIMEOUT       # 900 s
# The CUDA torch wheels are about 2.5 GB; allow a slow link far longer.
GPU_TORCH_TIMEOUT_SECONDS = 3600


def _guard(confirm: bool):
    """confirm=True; no exclusive hold (a library restore or reset) and no
    maintenance operation (bulk delete, storage cleanup) in progress; and
    no job running or queued here or (fresh job_records rows) in another
    process -- the same rule as the Library admin actions
    (library_admin_service._any_job_running)."""
    if confirm is not True:
        raise AdminActionUnconfirmed("Confirmation required.")
    from services import library_admin_service
    if background_jobs.exclusive_active() or _maintenance_active():
        raise AdminActionJobsRunning(
            "A library restore, reset or cleanup is in progress; try again when it ends.")
    if library_admin_service._any_job_running():
        raise AdminActionJobsRunning(
            "A background job is running or queued; wait for it to finish.")


def installable_packages() -> set:
    """Package names an install/upgrade wrapper accepts: optional
    dependencies in an installable tier plus model-registry packages."""
    names = {k for k, (_imp, _f, tier) in diagnostics.OPTIONAL_DEPENDENCIES.items()
             if tier in diagnostics.INSTALLABLE_TIERS}
    names |= {e["package"] for e in diagnostics.MODEL_ENGINE_REGISTRY if e.get("package")}
    return {n for n in names
            if diagnostics.canonical_dist(diagnostics.pip_install_name(n))
            not in diagnostics.NOT_OFFERED_FOR_INSTALL}


def _pip(command: str, *args) -> list:
    return [sys.executable, "-m", "pip", command, *diagnostics.PIP_INSTALL_FLAGS, *args]


def _install_commands(name: str) -> list:
    """(command, timeout) pairs for an install. torch, torchvision or
    torchaudio on a machine with an NVIDIA GPU installs the whole matched
    CUDA triple (diagnostics.torch_setup_pip_args), never one of the three
    alone: a plain `pip install torchaudio` resolves its own torch and can
    swap a CUDA build for a CPU one. `--force-reinstall --no-deps` first
    downloads every wheel before replacing anything, so a timeout during
    the (~2.5 GB) download leaves the old torch in place; a second plain
    install then adds any missing dependencies (e.g. the nvidia-* wheels
    on Linux). Everything else is a plain install."""
    if name in diagnostics.TORCH_FAMILY and shutil.which("nvidia-smi"):
        return _torch_setup_commands(diagnostics.TORCH_RECOMMENDED_VARIANT_GPU)
    return [(_pip("install", diagnostics.pip_install_name(name)), PIP_TIMEOUT_SECONDS)]


def _torch_setup_commands(variant: str) -> list:
    return [(_pip("install", *args), GPU_TORCH_TIMEOUT_SECONDS)
            for args in diagnostics.torch_setup_pip_args(variant, _project_root())]


KILL_DRAIN_SECONDS = 5.0


def _stream_tree(cmd: list, timeout: float, drain_seconds: float = KILL_DRAIN_SECONDS):
    """Like diagnostics._stream_process ({"line"} per output line, then
    {"returncode", "timed_out"}), but pip runs in its own process group and
    on timeout (or if the caller stops early) the whole tree is killed
    with background_jobs._kill_tree, not only pip itself.

    Every wait is bounded, like background_jobs.run_cancellable's
    kill_timeout: output is read on a helper thread, so a grandchild that
    survives the kill (or outlives pip) and keeps the pipe open can hold
    this for at most `drain_seconds` after the kill or after pip exits,
    never forever. returncode is None if pip could not be reaped."""
    import queue
    import subprocess
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, **group)
    lines, eof = queue.Queue(), object()

    def _reader():
        try:
            for line in proc.stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(eof)
    threading.Thread(target=_reader, daemon=True, name="pip-output").start()

    deadline = time.monotonic() + timeout
    timed_out, stop_by = False, None
    try:
        while True:
            now = time.monotonic()
            if stop_by is None:
                if now >= deadline:
                    timed_out = True
                    background_jobs._kill_tree(proc)
                    stop_by = now + drain_seconds
                elif proc.poll() is not None:
                    stop_by = now + drain_seconds     # pip exited; finish reading
            if stop_by is not None and now >= stop_by:
                break
            limit = deadline if stop_by is None else stop_by
            try:
                item = lines.get(timeout=max(0.01, min(0.5, limit - now)))
            except queue.Empty:
                continue
            if item is eof:
                break
            yield {"line": item.rstrip("\n")}
    finally:
        if proc.poll() is None:
            background_jobs._kill_tree(proc)
        try:
            returncode = proc.wait(timeout=drain_seconds)
        except subprocess.TimeoutExpired:
            returncode = None
    yield {"returncode": returncode, "timed_out": timed_out}


_TORCH_CONSTRAINT_RE = re.compile(r"\(constraint\)\s+(torch|torchvision|torchaudio)==",
                                  re.IGNORECASE)


def torch_conflict_hint(lines, pins: list) -> str:
    """A plain-English refusal when pip stopped because the package needs a
    different torch than the pinned one (pip names the constraint in its
    "The conflict is caused by" block). pip resolves before it changes
    anything, so nothing was installed or removed."""
    for line in lines:
        if _TORCH_CONSTRAINT_RE.search(line or ""):
            have = ", ".join(p.replace("==", " ") for p in pins)
            return ("This package needs a different PyTorch than the one installed "
                    f"({have}), so nothing was changed: installing it would have replaced "
                    "your PyTorch (and a CUDA build with a CPU one). Leave it, or set up a "
                    "PyTorch version it supports under GPU PyTorch first.")
    return None


def _run_commands(cmds: list, torch_pins: list = None) -> dict:
    """Runs each (command, timeout) through _stream_tree; ok only if every
    one exits 0 in time. Stops at the first failure."""
    tail, ok, hint, raw = [], True, None, []
    for cmd, timeout in cmds:
        for item in _stream_tree(cmd, timeout):
            if "line" in item:
                # Checked on the raw line: redaction rewrites the cache path.
                hint = hint or diagnostics.pip_cache_permission_hint([item["line"]])
                if torch_pins:
                    raw = (raw + [item["line"]])[-200:]
                tail = (tail + [_redact(item["line"])])[-_ADMIN_OUTPUT_TAIL:]
            elif "returncode" in item:
                ok = ok and item["returncode"] == 0 and not item.get("timed_out")
                if item.get("timed_out"):
                    tail = (tail + ["(stopped: pip took too long)"])[-_ADMIN_OUTPUT_TAIL:]
        if not ok:
            break
    if not ok and torch_pins:
        hint = torch_conflict_hint(raw, torch_pins) or hint
    return {"ok": ok, "output_tail": tail, "hint": None if ok else hint}


def _write_torch_pins(pins: list) -> str:
    """A temporary constraints file holding `pins`; the caller removes it."""
    fd, path = tempfile.mkstemp(prefix="baihe-torch-pins-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(pins) + "\n")
    return path


def _under_install_hold(fn):
    """Holds the library exclusively while fn() runs, so no job, restore,
    reset, cleanup or second install can start in this API process
    mid-install (409 if the hold can't be taken). Jobs in another process
    are re-checked under the hold, as reset_library does."""
    if not background_jobs.acquire_exclusive("Dependency install"):
        raise AdminActionJobsRunning(
            "A job, restore, cleanup or another install is in progress; try again when it ends.")
    try:
        from services import library_admin_service
        if library_admin_service._any_job_running():     # re-check under the hold
            raise AdminActionJobsRunning(
                "A background job is running or queued; wait for it to finish.")
        return fn()
    finally:
        background_jobs.release_exclusive()


def _run_pip(name: str, confirm, cmds_for) -> dict:
    """One whitelisted package's pip run under the install hold. Unless the
    package is itself torch/torchvision/torchaudio, every command also gets
    a constraints file pinning the installed torch family exactly, so pip
    refuses (before changing anything) a package that needs another torch
    instead of replacing a CUDA torch with a CPU one or moving torchvision."""
    _guard(confirm)
    if name not in installable_packages():
        raise AdminActionUnknownPackage("Unknown or non-installable package.")

    def run():
        cmds = cmds_for(name)
        pins = [] if name in diagnostics.TORCH_FAMILY else diagnostics.torch_pin_lines()
        if not pins:
            return _run_commands(cmds)
        path = _write_torch_pins(pins)
        try:
            return _run_commands([(cmd + ["-c", path], t) for cmd, t in cmds], torch_pins=pins)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    result = _under_install_hold(run)
    result["package"] = name
    return result


def install_dependency(name: str, confirm: bool = False) -> dict:
    try:
        return _run_pip(name, confirm, _install_commands)
    finally:
        _clear_update_cache()      # a new package can hold back (or allow) others


def upgrade_dependency(name: str, confirm: bool = False, target: str = None) -> dict:
    """Upgrades to the version the last "Check for updates" found allowed
    (pinned exactly, with constraints.txt); refuses when that check found
    no allowed update, or (409) when `target`, the version the user
    confirmed, isn't that check's target any more. Without a check (and no
    target), `--upgrade` with constraints.txt as before."""
    if name in diagnostics.TORCH_FAMILY:
        _guard(confirm)
        raise AdminActionNotPossible(
            "torch, torchvision and torchaudio are upgraded together: use GPU PyTorch setup.")
    checked = _cached_update(name)
    if checked is not None and target is None:
        _guard(confirm)
        raise AdminActionStale("Say which version to update to (the update check's target).")
    if target is not None and (checked is None or checked["status"] != "update"
                               or checked["target"] != target):
        _guard(confirm)
        raise AdminActionStale("The update check has changed since; check for updates again.")
    if checked is not None and checked["status"] != "update":
        _guard(confirm)
        raise AdminActionNotPossible(
            "The last update check found no update allowed for this package; check again.")

    def cmds(n):
        dist = diagnostics.pip_install_name(n)
        if checked is not None:
            args = [f"{checked['dist']}=={checked['target']}"]
            constraints = os.path.join(_project_root(), "constraints.txt")
            if os.path.exists(constraints):
                args += ["-c", constraints]
        else:
            args = diagnostics.upgrade_pip_args(dist, _project_root())
        return [(_pip("install", *args), PIP_TIMEOUT_SECONDS)]
    try:
        return _run_pip(name, confirm, cmds)
    finally:
        # pip may have moved other packages too (a dependency pulled up or
        # capped), so no cached target is safe any more: the next Update
        # needs a new check, as after an install.
        _clear_update_cache()


# ---------------------------------------------------------------------------
# "Check for updates": PyPI, only on an explicit click (admin.diagnostics),
# cached in this process so the Upgrade route installs exactly the version
# the check showed. A repeat within UPDATE_CHECK_MIN_INTERVAL returns the
# cache instead of asking PyPI again.
# ---------------------------------------------------------------------------

UPDATE_CHECK_MIN_INTERVAL = 60
UPDATE_CHECK_WORKERS = 8
_UPDATES_LOCK = threading.Lock()
_UPDATES_FETCH = threading.Lock()      # one PyPI fan-out at a time
_UPDATES = {"checked_at": None, "packages": {}, "generation": 0}


def _clear_update_cache() -> None:
    """After an install, an upgrade or a PyTorch setup the installed set changed, so a
    cached target may no longer be safe: the next Update needs a new check.
    The generation bump stops a check that started before from storing its
    (now stale) answer."""
    with _UPDATES_LOCK:
        _UPDATES.update(checked_at=None, packages={},
                        generation=_UPDATES.get("generation", 0) + 1)


def _cached_update(name: str):
    with _UPDATES_LOCK:
        entry = _UPDATES["packages"].get(name)
        return dict(entry) if entry else None



def check_package_updates(force: bool = False) -> dict:
    """For every installed package the Upgrade route accepts: installed
    version, newest PyPI release, and the newest one allowed by
    constraints.txt, the installed packages that depend on it and the
    known limitations (diagnostics.classify_update). torch/torchvision/
    torchaudio are "managed" (GPU PyTorch setup), never an update here.
    One PyPI request per distribution, in parallel, each with a timeout."""
    def cached():
        with _UPDATES_LOCK:
            if _UPDATES["checked_at"] is None:
                return None
            return {"checked_at": _UPDATES["checked_at"],
                    "packages": {k: dict(v) for k, v in _UPDATES["packages"].items()}}
    with _UPDATES_LOCK:
        recent = (_UPDATES["checked_at"] is not None and
                  time.time() - _UPDATES["checked_at"] < UPDATE_CHECK_MIN_INTERVAL)
    if recent and not force:
        return cached()
    if not _UPDATES_FETCH.acquire(blocking=False):
        # Another check is asking PyPI right now: never a second fan-out.
        last = cached()
        if last is not None:
            return last
        raise AdminActionStale("An update check is already running; try again in a moment.")
    try:
        with _UPDATES_LOCK:      # one may have finished while we waited for the lock
            recent = (_UPDATES["checked_at"] is not None and
                      time.time() - _UPDATES["checked_at"] < UPDATE_CHECK_MIN_INTERVAL)
        if recent and not force:
            return cached()
        return _check_package_updates_now()
    finally:
        _UPDATES_FETCH.release()


def _check_package_updates_now() -> dict:
    from concurrent.futures import ThreadPoolExecutor
    with _UPDATES_LOCK:
        generation = _UPDATES.get("generation", 0)
    names = sorted(n for n in installable_packages() if _package_installed(n))
    installed = {n: diagnostics.installed_dist_version(n) for n in names}
    to_fetch = sorted({dist for n, (dist, v) in installed.items()
                       if v and n not in diagnostics.TORCH_FAMILY})
    with ThreadPoolExecutor(max_workers=UPDATE_CHECK_WORKERS) as pool:
        releases = dict(zip(to_fetch, pool.map(diagnostics.pypi_release_versions, to_fetch)))
    constraints = diagnostics.constraint_specifiers(_project_root())
    required_by = diagnostics.installed_requirements_on()
    packages = {}
    for n in names:
        dist, version = installed[n]
        if n in diagnostics.TORCH_FAMILY:
            info = {"status": "managed", "latest": None, "target": None,
                    "reason": "set up together with torchvision/torchaudio under GPU PyTorch"}
        else:
            info = diagnostics.classify_update(n, version, releases.get(dist), constraints,
                                               required_by)
        packages[n] = {"name": n, "dist": dist, "installed_version": version, **info}
        if packages[n]["reason"]:
            packages[n]["reason"] = _redact(packages[n]["reason"])[:300]
    checked_at = time.time()
    with _UPDATES_LOCK:
        # An install finished mid-check: keep the cache empty rather than
        # store answers computed against the old set of packages.
        if _UPDATES.get("generation", 0) == generation:
            _UPDATES.update(checked_at=checked_at, packages=packages)
    return {"checked_at": checked_at, "packages": {k: dict(v) for k, v in packages.items()}}


# ---------------------------------------------------------------------------
# GPU PyTorch: status (read) and the matched-triple setup (local_only).
# ---------------------------------------------------------------------------

_VERIFY_LOCK = threading.Lock()


def verify_torch(blocking: bool = True) -> dict:
    """Imports torch/torchvision/torchaudio in a fresh interpreter (this
    process may hold an older torch) and reports versions and whether CUDA
    works. Error text is redacted. One check at a time: blocking=False
    raises AdminActionStale (409) instead of queueing behind another."""
    if not _VERIFY_LOCK.acquire(blocking=blocking):
        raise AdminActionStale("A CUDA check is already running; try again in a moment.")
    try:
        return _verify_torch_once()
    finally:
        _VERIFY_LOCK.release()


def _verify_torch_once() -> dict:
    import subprocess
    try:
        proc = subprocess.run([sys.executable, "-c", diagnostics.TORCH_VERIFY_SCRIPT],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=diagnostics.TORCH_VERIFY_TIMEOUT_SECONDS)
        data = diagnostics.parse_torch_verify_output(proc.stdout)
    except subprocess.TimeoutExpired:
        data = {"error": "importing torch took too long"}
    except OSError as e:
        data = {"error": type(e).__name__}
    out = {k: data.get(k) for k in ("torch", "torchvision", "torchaudio", "cuda_build",
                                    "device")}
    out = {k: (_redact(v)[:120] if v is not None else None) for k, v in out.items()}
    out["cuda_available"] = data.get("cuda_available") if isinstance(
        data.get("cuda_available"), bool) else None
    errors = [data[k] for k in ("error", "torchvision_error", "torchaudio_error") if data.get(k)]
    out["error"] = _redact(" | ".join(str(e) for e in errors))[:600] if errors else None
    return out


def _variant_row(variant: str) -> dict:
    spec = diagnostics.TORCH_VARIANTS[variant]
    return {"variant": variant, "label": spec["label"], "index_url": spec["index_url"],
            "versions": dict(spec["versions"]), "needs_nvidia": spec["needs_nvidia"]}


def _python_supported() -> bool:
    lo, hi = diagnostics.TORCH_SUPPORTED_PYTHON
    return lo <= tuple(sys.version_info[:2]) <= hi


def check_gpu_torch() -> dict:
    """get_gpu_torch_status plus a CUDA check in a fresh interpreter. It
    takes a CUDA context (VRAM), so it refuses (409) while a job, restore,
    cleanup or install runs, or while another check does."""
    from services import library_admin_service
    if (background_jobs.exclusive_active() or _maintenance_active()
            or library_admin_service._any_job_running()):
        raise AdminActionJobsRunning(
            "Wait for running jobs (or the install) to finish before checking CUDA.")
    return get_gpu_torch_status(probe=True)


def get_gpu_torch_status(probe: bool = False) -> dict:
    """NVIDIA GPU and driver (nvidia-smi), the installed torch family and
    its build, mismatches, and the recommended matched triple. probe=True
    (check_gpu_torch) also imports torch in a subprocess to report
    torch.cuda.is_available() (a few seconds); otherwise "probe" is None."""
    nvidia = diagnostics.nvidia_driver_info()
    versions = diagnostics.torch_family_versions()
    problems = diagnostics.torch_family_problems(versions)
    variant = diagnostics.TORCH_RECOMMENDED_VARIANT_GPU if nvidia else "cpu"
    recommended = _variant_row(variant)
    driver = diagnostics.driver_check(nvidia["driver_version"]) if nvidia else None
    installed = {n: versions[n]["version"] for n in diagnostics.TORCH_FAMILY}
    if not installed["torch"]:
        state = "missing"
    elif problems:
        state = "mismatched"
    elif nvidia and versions["torch"]["build"] == "cpu":
        state = "cpu_on_gpu"
    elif all(installed[n] == recommended["versions"][n] for n in ("torch", "torchaudio")) and \
            installed["torchvision"] in (None, recommended["versions"]["torchvision"]):
        state = "recommended"
    else:
        state = "different"
    return {
        "nvidia": ({"found": True, "gpu_name": _redact(nvidia["gpu_name"]),
                    "driver_version": nvidia["driver_version"], **driver}
                   if nvidia else {"found": False, "gpu_name": None, "driver_version": None,
                                   "status": "unknown", "recommended": None, "minimum": None}),
        "installed": [{"name": n, "version": versions[n]["version"],
                       "build": versions[n]["build"]} for n in diagnostics.TORCH_FAMILY],
        "problems": problems,
        "state": state,
        "python_supported": _python_supported(),
        "recommended": recommended,
        "variants": [_variant_row(v) for v in diagnostics.TORCH_VARIANTS],
        "probe": verify_torch(blocking=False) if probe else None,
    }


def setup_gpu_torch(variant: str = None, confirm: bool = False) -> dict:
    """Installs the matched torch/torchvision/torchaudio triple for
    `variant` (a diagnostics.TORCH_VARIANTS key; default: CUDA when an
    NVIDIA GPU answers, else CPU) from its fixed index, then verifies it in
    a fresh interpreter. Refuses (422) without an NVIDIA GPU for a CUDA
    variant, with a driver too old for CUDA 12, or on a Python the wheels
    don't cover. ok only when pip succeeded and the new torch imports as
    the expected version (and, for CUDA, sees the GPU)."""
    _guard(confirm)
    nvidia = diagnostics.nvidia_driver_info()
    if variant is None:
        variant = diagnostics.TORCH_RECOMMENDED_VARIANT_GPU if nvidia else "cpu"
    if variant not in diagnostics.TORCH_VARIANTS:
        raise AdminActionNotPossible("Unknown PyTorch variant.")
    spec = diagnostics.TORCH_VARIANTS[variant]
    if not _python_supported():
        lo, hi = diagnostics.TORCH_SUPPORTED_PYTHON
        raise AdminActionNotPossible(
            f"PyTorch {spec['versions']['torch']} has wheels for Python "
            f"{lo[0]}.{lo[1]} to {hi[0]}.{hi[1]} only.")
    if spec["needs_nvidia"]:
        if not nvidia:
            raise AdminActionNotPossible(
                "No NVIDIA GPU answered (nvidia-smi not found or failed). Install or update "
                "the NVIDIA driver first, or choose the CPU version.")
        drv = diagnostics.driver_check(nvidia["driver_version"])
        if drv["status"] == "too_old":
            raise AdminActionNotPossible(
                f"NVIDIA driver {nvidia['driver_version']} is too old for CUDA 12.8 "
                f"(needs at least {drv['minimum']}, recommended {drv['recommended']}). "
                "Update the driver from nvidia.com, then try again.")

    def run():
        result = _run_commands(_torch_setup_commands(variant))
        result["verify"] = verify_torch() if result["ok"] else None
        return result
    try:
        result = _under_install_hold(run)
    finally:
        _clear_update_cache()
    verify = result["verify"]
    if verify is not None:
        good = verify.get("torch") == spec["versions"]["torch"] and not verify.get("error")
        if spec["needs_nvidia"]:
            good = good and verify.get("cuda_available") is True
        if not good:
            result["ok"] = False
            result["hint"] = ("pip finished, but the new PyTorch didn't pass the check "
                              "(see below). Restart the app and open this section again; if "
                              "CUDA still isn't available, update the NVIDIA driver.")
    result.update(package="torch", variant=variant)
    return result


def _package_installed(name: str) -> bool:
    dep = diagnostics.OPTIONAL_DEPENDENCIES.get(name)
    if dep:
        return bool(diagnostics.check_dependency(dep[0]))
    return diagnostics.get_installed_version(diagnostics.pip_install_name(name)) is not None


def _package_info(name: str, installed: bool, offered: set, mins: dict = None) -> dict:
    dist = diagnostics.pip_install_name(name)
    installed_version = diagnostics.installed_dist_version(name)[1] if installed else None
    min_version = (mins or {}).get(diagnostics.canonical_dist(dist))
    dep = diagnostics.OPTIONAL_DEPENDENCIES.get(name)
    reason = diagnostics.NOT_OFFERED_FOR_INSTALL.get(diagnostics.canonical_dist(dist))
    # A Python-version limitation (e.g. audio-separator on 3.14) is a warning,
    # not a refusal: the install route still accepts it.
    limitation = None if reason else diagnostics.known_install_limitation_reason(name)
    return {
        "name": name,
        "dist": dist,
        "installed": installed,
        "installed_version": installed_version,
        # The app's minimum (the requirements files' `>=`), and whether the
        # installed version is below it.
        "min_version": min_version,
        "below_min": diagnostics.below_min_version(installed_version, min_version),
        "installable": name in offered and not installed and reason is None,
        "powers": dep[1] if dep else "",
        "approx_mb": diagnostics.approx_download_mb(name),
        "pulls_torch": diagnostics.canonical_dist(dist) in diagnostics.PULLS_TORCH,
        "source_url": diagnostics.pypi_url(name),
        "not_offered_reason": reason,
        "warning": None if installed else (limitation
                                           or diagnostics.install_downgrade_warning(name)),
    }


def get_install_presets() -> dict:
    """{"tasks": [...], "packages": {name: info}} for the Packages section:
    install presets by task (diagnostics.INSTALL_TASKS), each package's
    role for the task (required / recommended / optional) and, for every
    package, its pip name, installed version, the app's minimum version,
    approx. download size, PyPI link, and any reason not to offer it or
    warning before installing it. "to_install" is what "Install for this
    task" installs: missing, offered, required or recommended packages;
    missing optional ones are listed in "optional_missing". Local checks
    only (import specs, installed metadata, requirements files), no network."""
    offered = installable_packages()
    mins = diagnostics.required_min_versions(_project_root())
    names = set(offered) | {n for t in diagnostics.INSTALL_TASKS for n in t["packages"]}
    names |= set(diagnostics.OPTIONAL_DEPENDENCIES)    # versions for required ones too
    packages = {n: _package_info(n, _package_installed(n), offered, mins) for n in sorted(names)}
    tasks = []
    for t in diagnostics.INSTALL_TASKS:
        roles = {n: diagnostics.task_package_role(t, n) for n in t["packages"]}
        rows = [packages[n] for n in t["packages"]]
        missing = [r for r in rows if not r["installed"]]
        to_install = [r for r in missing if r["installable"] and roles[r["name"]] != "optional"]
        tasks.append({
            "id": t["id"], "group": t["group"], "label": t["label"], "help": t["help"],
            "packages": list(t["packages"]),
            "roles": roles,
            "installed_count": len(rows) - len(missing),
            "required_missing": [r["name"] for r in missing if roles[r["name"]] == "required"],
            "to_install": [r["name"] for r in to_install],
            "optional_missing": [r["name"] for r in missing
                                 if r["installable"] and roles[r["name"]] == "optional"],
            "approx_mb": sum(r["approx_mb"] or 0 for r in to_install),
        })
    return {"tasks": tasks, "packages": packages}


def reset_library(confirm: bool = False, confirm_text: str = None) -> dict:
    """Irreversible. Unlike the Streamlit button it does not cancel
    running jobs; it refuses instead, so a caller must stop them first.
    `confirm_text`, when given (the API always gives it), must be exactly
    "RESET", the word the Streamlit button made the user type."""
    if confirm_text is not None and confirm_text != RESET_CONFIRM_TEXT:
        raise AdminActionUnconfirmed(
            f'Resetting the library needs confirm=true and confirm_text "{RESET_CONFIRM_TEXT}".')
    _guard(confirm)
    # Hold the library exclusively for the reset, as a restore does, so no
    # job can start mid-reset (start_job refuses while the hold is taken).
    if not background_jobs.acquire_exclusive("Library reset"):
        raise AdminActionJobsRunning(
            "A job, restore or cleanup is in progress; try again when it ends.")
    try:
        from services import library_admin_service
        if library_admin_service._any_job_running():     # re-check under the hold
            raise AdminActionJobsRunning(
                "A background job is running or queued; wait for it to finish.")
        db.reset_library()
        background_jobs.clear_all_jobs()
    finally:
        background_jobs.release_exclusive()
    return {"ok": True, "reset_at": time.time()}

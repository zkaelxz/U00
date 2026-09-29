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
docs/streamlit-retirement-plan.md): accuracy benchmark, bug-bundle replay
(the saved bundles are listed, and deleted through delete_service), App
Assistant, and the source-access tests.
"""

import os
import shutil
import sys
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
                   "version": _redact(ff["version"]) if ff.get("version") else None,
                   "libass": ff.get("libass") if ff.get("found") else None},
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
    return names


def _pip(*args) -> list:
    return [sys.executable, "-m", "pip", *args]


def _install_commands(name: str) -> list:
    """(command, timeout) pairs for an install. torch (or torchaudio, which
    must match it) on a machine with an
    NVIDIA GPU gets the CUDA build from PyTorch's index, like
    diagnostics.stream_gpu_torch_reinstall, but without uninstalling first:
    `--force-reinstall --no-deps` downloads both wheels before replacing
    anything, so a timeout during the (~2.5 GB) download leaves the old
    torch in place; a second plain install then adds any missing
    dependencies (e.g. the nvidia-* wheels on Linux). Everything else is a
    plain install."""
    if name in ("torch", "torchaudio") and shutil.which("nvidia-smi"):
        index = ["--index-url",
                 f"https://download.pytorch.org/whl/{diagnostics.gpu_torch_cuda_index()}"]
        constraints = os.path.join(_project_root(), "constraints.txt")
        if os.path.exists(constraints):
            index += ["-c", constraints]
        return [(_pip("install", "--force-reinstall", "--no-deps", "torch", "torchaudio",
                      *index), GPU_TORCH_TIMEOUT_SECONDS),
                (_pip("install", "torch", "torchaudio", *index), GPU_TORCH_TIMEOUT_SECONDS)]
    return [(_pip("install", name), PIP_TIMEOUT_SECONDS)]


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


def _run_commands(cmds: list) -> dict:
    """Runs each (command, timeout) through _stream_tree; ok only if every
    one exits 0 in time. Stops at the first failure."""
    tail, ok = [], True
    for cmd, timeout in cmds:
        for item in _stream_tree(cmd, timeout):
            if "line" in item:
                tail = (tail + [_redact(item["line"])])[-_ADMIN_OUTPUT_TAIL:]
            elif "returncode" in item:
                ok = ok and item["returncode"] == 0 and not item.get("timed_out")
                if item.get("timed_out"):
                    tail = (tail + ["(stopped: pip took too long)"])[-_ADMIN_OUTPUT_TAIL:]
        if not ok:
            break
    return {"ok": ok, "output_tail": tail}


def _run_pip(name: str, confirm, cmds_for) -> dict:
    """Holds the library exclusively for the whole pip run, so no job,
    restore, reset, cleanup or second install can start in this API
    process mid-upgrade (409 if the hold can't be taken). Jobs in another
    process are re-checked under the hold, as reset_library does."""
    _guard(confirm)
    if name not in installable_packages():
        raise AdminActionUnknownPackage("Unknown or non-installable package.")
    if not background_jobs.acquire_exclusive("Dependency install"):
        raise AdminActionJobsRunning(
            "A job, restore, cleanup or another install is in progress; try again when it ends.")
    try:
        from services import library_admin_service
        if library_admin_service._any_job_running():     # re-check under the hold
            raise AdminActionJobsRunning(
                "A background job is running or queued; wait for it to finish.")
        result = _run_commands(cmds_for(name))
    finally:
        background_jobs.release_exclusive()
    result["package"] = name
    return result


def install_dependency(name: str, confirm: bool = False) -> dict:
    return _run_pip(name, confirm, _install_commands)


def upgrade_dependency(name: str, confirm: bool = False) -> dict:
    return _run_pip(name, confirm, lambda n: [
        (_pip("install", *diagnostics.upgrade_pip_args(n, _project_root())),
         PIP_TIMEOUT_SECONDS)])


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


# ---------------------------------------------------------------------------
# Model cache delete (Q14) and saved bug bundles (list only; delete is
# services/delete_service.delete_bug_bundle)
# ---------------------------------------------------------------------------

def delete_hf_revision(revision: str, confirm: bool = False) -> dict:
    """Deletes one cached Hugging Face revision, named by a commit hash the
    cache scan lists (anything else is NotFoundError, so a caller can only
    ever delete what the scan shows). Refuses while a job runs, since a
    job may be loading that model."""
    if not isinstance(revision, str) or not any(
            e["revision"] == revision for e in diagnostics.scan_hf_cache()):
        raise NotFoundError("No cached model with that revision.")
    _guard(confirm)
    if not diagnostics.delete_hf_cache_revision(revision):
        raise ServiceError("Couldn't delete that model; see the log for details.")
    return {"deleted": True, "name": revision}


def delete_piper_voice(voice: str, confirm: bool = False) -> dict:
    """Deletes one downloaded Piper voice (its .onnx and .onnx.json). Only a
    name the scan lists is accepted, so no path can be built from input."""
    if not isinstance(voice, str) or not any(
            e["voice"] == voice for e in diagnostics.scan_piper_voices()):
        raise NotFoundError("No downloaded voice with that name.")
    _guard(confirm)
    if not diagnostics.delete_piper_voice(voice):
        raise ServiceError("Couldn't delete that voice; see the log for details.")
    return {"deleted": True, "name": voice}


def list_bug_bundles() -> list:
    """Saved bug-reproduction bundles (a line's "What happened here?"
    snapshot), newest first. The frozen input is not returned; outputs are
    redacted."""
    out = []
    for b in db.list_bug_reports():
        drama = db.get_drama(b["drama_id"])
        title = (drama.get("title_en") or drama.get("title_zh")) if drama else None
        out.append({
            "id": b["id"], "drama_id": b["drama_id"], "drama_title": title,
            "line_id": b.get("line_id"), "label": _redact(b.get("label") or ""),
            "engine": b.get("engine"), "model": b.get("model"),
            "produced_output": _redact(b.get("produced_output") or ""),
            "replayed": bool(b.get("replayed")),
            "replay_output": _redact(b["replay_output"]) if b.get("replay_output") else None,
            "reproduced": bool(b.get("reproduced")) if b.get("replayed") else None,
            "created_at": b.get("created_at"),
        })
    return out

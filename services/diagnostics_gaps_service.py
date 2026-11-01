"""
services/diagnostics_gaps_service.py -- the read-only Diagnostics reports
services/diagnostics_service.py's overview does not cover: setup checks (as
check_setup.py reports them), model versions, model-cache listing,
pyannote/HF-token readiness, finished-job history, a shareable support
report and a capped log tail.

No HTTP types. Every string that could carry a
secret, a token, the OS username or a local path goes through
diagnostics_report.redact_for_support (which applies
engine_backends.shared.redact_secrets first); nothing here returns a path.

The confirm-gated admin actions live in diagnostics_admin_service (guard,
library reset, model-cache delete), diagnostics_packages_service
(dependency install/upgrade, update check, install presets) and
diagnostics_gpu_torch_service (GPU PyTorch status and setup). The benchmark,
the maintenance assistant and the source-access tests have their own
services (benchmark_lab_service, maintenance_assistant_service,
sources_signin_service).
"""

import os

import background_jobs
import db
import diagnostics
import diagnostics_report
from diagnostics_report import redact_for_support
import diagnostics_torch as gpu_torch

LOG_TAIL_DEFAULT = 50


LOG_TAIL_MAX = 200


# job_id prefixes this app actually uses (see job_launch.start_job
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


def default_project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def describe_job(job_id: str) -> str:
    """Turns a raw job_id like 'emotion_42' into 'Detecting emotional
    register -- Some Drama Title', so the jobs list means something at a
    glance instead of showing internal id strings."""
    prefix, _, suffix = job_id.rpartition("_")
    if prefix in _JOB_LABELS and suffix.isdigit():
        drama = db.get_drama(int(suffix))
        title = (drama.get("title_en") or drama.get("title_zh") or f"title #{suffix}") if drama else f"title #{suffix} (deleted)"
        return f"{_JOB_LABELS[prefix]} -- {title}"
    return job_id


# Reads

def get_setup_checks(project_root: str | None = None, library_dir: str | None = None) -> dict:
    """Core requirements and file checks, the same facts check_setup.py
    prints at launch plus file completeness. Paths are never returned:
    ffmpeg/JS runtime report found/name/version only."""
    project_root = project_root or default_project_root()
    library_dir = library_dir or db.LIBRARY_DIR
    py = diagnostics.check_python_version()
    ff = diagnostics.check_ffmpeg()
    js = diagnostics.check_js_runtime()
    cuda = gpu_torch.check_cuda_isolated()
    browser = diagnostics.check_browser()
    files = diagnostics.check_file_completeness(project_root)
    writable = bool(diagnostics.check_library_writable(library_dir))
    warnings = diagnostics.startup_warnings()
    if not writable:
        warnings.append("The library folder is read-only, so nothing can be saved.")
    return {
        "python": {"version": py.get("version"), "ok": bool(py.get("ok"))},
        "ffmpeg": {"found": bool(ff.get("found")),
                   "version": redact_for_support(ff["version"]) if ff.get("version") else None,
                   "libass": ff.get("libass") if ff.get("found") else None},
        "js_runtime": {"found": bool(js.get("found")), "name": js.get("name")},
        "browser": {"found": bool(browser.get("found")), "name": browser.get("name"),
                    "package": bool(browser.get("package"))},
        "cuda": {"torch_installed": bool(cuda.get("torch_installed")),
                 "cuda_available": cuda.get("cuda_available")},
        "files": {"all_present": bool(files["all_present"]),
                  "missing_top_level": list(files["missing_top_level"])},
        "library_writable": writable,
        "warnings": warnings,
    }


def get_model_versions(ollama_model: str | None = None) -> list:
    """Model/engine version rows (local only, no network)."""
    return [{"name": m["name"], "version": redact_for_support(m["version"]), "url": m["url"],
             "installed": bool(m["installed"]), "package": m.get("package"),
             "help": m.get("help", "")}
            for m in diagnostics_report.get_model_engine_versions(ollama_model)]


def get_model_cache(hf_cache_dir: str | None = None) -> dict:
    """Hugging Face cache revisions and the files in the other
    model folders (torch.hub checkpoints under TORCH_HOME, the
    audio-separator models) by name and size -- no directory is ever
    included."""
    hf = [{"repo_id": e["repo_id"], "repo_type": e["repo_type"],
           "revision": e["revision"], "size_bytes": int(e["size_bytes"])}
          for e in diagnostics.scan_hf_cache(hf_cache_dir)]
    files = [{"folder": kind, "name": e["name"], "size_bytes": int(e["size_bytes"])}
             for kind in diagnostics.MODEL_FOLDERS
             for e in diagnostics.scan_model_folder(kind)]
    return {
        "hf_cache": hf,
        "hf_total_bytes": sum(e["size_bytes"] for e in hf),
        "model_files": files,
        "model_files_total_bytes": sum(e["size_bytes"] for e in files),
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
        results = diagnostics_report.check_pyannote_gated_access(token, api=api)
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
            "label": redact_for_support(describe_job(jid)),
            "status": job.get("status"),
            "description": redact_for_support(job.get("description")) or None,
            "message": redact_for_support(job.get("message")),
            "error": redact_for_support(job.get("error")) or None,
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
    return applog.filter_lines([redact_for_support(ln) for ln in applog.tail(n)], keyword or "")


def build_support_report(recent_error_lines: int = 20) -> str:
    """Plain-text summary safe to share: versions, OS, dependency status,
    model versions, cache totals, which keys are set (never values), and
    recent log errors -- all passed through redact_for_support."""
    import platform
    from services import settings_service
    results = diagnostics_report.run_full_diagnostics(
        default_project_root(), db.LIBRARY_DIR, settings_service.key_status())
    cache = diagnostics.scan_hf_cache()
    report = diagnostics_report.format_diagnostics_report(
        results, cache, diagnostics_report.get_model_engine_versions())
    extra = [f"OS: {platform.system()} {platform.release()} ({platform.machine()})"]
    errors = [ln for ln in get_log_tail(LOG_TAIL_MAX)
              if "ERROR" in ln or "Traceback" in ln][-max(0, int(recent_error_lines)):]
    extra.append("Recent errors:" if errors else "Recent errors: none")
    extra.extend(f"  {ln}" for ln in errors)
    return redact_for_support(report + "\n" + "\n".join(extra))

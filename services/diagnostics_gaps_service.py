"""
services/diagnostics_gaps_service.py -- the read-only Diagnostics reports
services/diagnostics_service.py's overview does not cover: setup checks (as
check_setup.py reports them), model versions, model-cache listing,
pyannote/HF-token readiness, finished-job history, a shareable support
report, the Send logs zip and a capped log tail.

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

import io
import json
import os
import time
import zipfile

import background_jobs
import db
import diagnostics
import diagnostics_report
from diagnostics_report import redact_for_support
import diagnostics_torch as gpu_torch
from sources import store as sources_store

LOG_TAIL_DEFAULT = 50


LOG_TAIL_MAX = 200
# A filter looks this far back, not only at the lines a plain view would show.
LOG_FILTER_SCAN = 5000

# Send logs: app.log and its rotated copies (applog keeps 3 of 5 MB each), newest
# first, up to this many bytes in all; the oldest part is what gets cut.
BUNDLE_LOG_BYTES = 5_000_000
BUNDLE_JOBS = 50


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


def _browser_needed():
    """None when the sources database can't be read: unknown reads as needed in the UI."""
    try:
        return sources_store.browser_source_in_use()
    except Exception:
        return None


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
                    "package": bool(browser.get("package")),
                    "needed": _browser_needed()},
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
    """The last n (capped at LOG_TAIL_MAX) redacted log lines; with a keyword,
    the newest n matches in the last LOG_FILTER_SCAN lines."""
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
    scan = LOG_FILTER_SCAN if (keyword or "").strip() else n
    return applog.filter_lines([redact_for_support(ln) for ln in applog.tail(scan)], keyword or "")[-n:]


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


def _log_files() -> list:
    """app.log, app.log.1, ... newest first, as applog's rotation names them."""
    log_dir = os.path.join(db.LIBRARY_DIR, "logs")
    paths = [os.path.join(log_dir, "app.log" + (f".{i}" if i else "")) for i in range(10)]
    return [p for p in paths if os.path.isfile(p)]


def _read_tail(path: str, max_bytes: int) -> str:
    with open(path, "rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        start = max(0, size - max_bytes)
        fh.seek(start)
        data = fh.read(max_bytes)
    if start:
        # Drop the partial first line rather than ship half a record.
        data = data.split(b"\n", 1)[1] if b"\n" in data else b""
    return data.decode("utf-8", errors="replace")


def _bundle_jobs() -> list:
    out = []
    for r in db.list_job_records(finished_limit=BUNDLE_JOBS)[:BUNDLE_JOBS]:
        started, ended = r.get("started_at"), r.get("finished_at")
        out.append({
            # job_id is "<kind>_<drama id>" or a fixed name: no title, nothing
            # the person typed.
            "job_id": diagnostics_report.redact_for_bundle(r.get("job_id")),
            "status": r.get("status"),
            "progress": r.get("progress"),
            "gpu_touching": bool(r.get("gpu_touching")),
            "started_at": started,
            "finished_at": ended,
            "duration_seconds": (ended - started) if started and ended else None,
            "message": diagnostics_report.redact_for_bundle(r.get("message")) or None,
            "error": diagnostics_report.redact_for_bundle(r.get("error")) or None,
        })
    return out


def _bundle_system() -> dict:
    import platform
    import sys
    from services import diagnostics_service, settings_service, update_service
    settings = settings_service.get_settings_overview()
    settings.pop("choices", None)
    # Endpoint URLs can name a machine on the home network: configured or not only.
    settings["endpoints"] = {k: bool(v) for k, v in (settings.get("endpoints") or {}).items()}
    try:
        gpu = diagnostics_service.get_gpu_reading()
    except Exception:
        gpu = {"found": None}
    return {
        "app": update_service.version_info(),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": sys.version.split()[0],
        "gpu": {k: gpu.get(k) for k in ("found", "name", "vram_total_gb")},
        "optional_dependencies": {k: bool(v.get("installed"))
                                  for k, v in diagnostics.check_all_dependencies().items()},
        "settings": settings,
    }


def build_support_bundle() -> bytes:
    """The Send logs zip: the support report, system info and settings,
    the last BUNDLE_JOBS job records and up to BUNDLE_LOG_BYTES of app.log.
    Every text in it goes through redact_for_bundle on the way in."""
    scrub = diagnostics_report.redact_for_bundle
    buf = io.BytesIO()
    with diagnostics_report.reusing_account_names(), \
            zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("support-report.txt", scrub(build_support_report()))
        zf.writestr("system.json", scrub(json.dumps(_bundle_system(), indent=2, default=str)))
        zf.writestr("jobs.json", json.dumps(_bundle_jobs(), indent=2, default=str))
        budget = BUNDLE_LOG_BYTES
        for path in _log_files():
            if budget <= 0:
                break
            cut = os.path.getsize(path) > budget
            text = _read_tail(path, budget)
            budget = 0 if cut else budget - len(text.encode("utf-8"))
            zf.writestr("logs/" + os.path.basename(path),
                        "\n".join(scrub(ln) for ln in text.splitlines()) + "\n")
    return buf.getvalue()


def support_bundle_filename(now: float | None = None) -> str:
    return time.strftime("baihe-logs-%Y%m%d-%H%M%S.zip", time.localtime(now))

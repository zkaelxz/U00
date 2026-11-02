"""services/voice_engine_install_service.py -- Diagnostics' install of a voice engine into a venv of its own (voice_venv)."""

import os
import shutil
import sys
import threading
from collections import deque

import background_jobs
import diagnostics
import diagnostics_report
import diagnostics_torch
import job_process_kill
import portable
import voice_venv
from engine_backends import shared
from jobs import job_launch
from lib.errors import ConflictError, InvalidInputError
from lib.proc import stream_tree
from services import diagnostics_admin_service as admin_svc

# Only these engines, with these exact packages, ever reach pip: nothing in a request
# names a package or a folder.
ENGINES = {
    "qwen3_tts": {"label": "Qwen3-TTS", "packages": ("qwen-tts==0.1.1",),
                  "check": "import torch, torchaudio, qwen_tts",
                  # CUDA PyTorch (about 2.5 GB), qwen-tts and its dependencies, with room
                  # left for the model the first narration downloads.
                  "required_mb": 6000},
    # chatterbox-tts 0.1.7 pins torch 2.6.0, which has no CUDA 12.8 build and no wheels
    # for Python 3.14 or for cards newer than compute 9.0 (RTX 50 series).
    "chatterbox": {"label": "Chatterbox", "packages": ("chatterbox-tts==0.1.7",),
                   "check": "import torch, torchaudio, chatterbox.mtl_tts",
                   "torch": {"cu126": "2.6.0+cu126", "cpu": "2.6.0+cpu"},
                   "max_python": (3, 13), "max_compute": 9.0,
                   # Gradio and the rest of its pins come on top of PyTorch, plus the
                   # multilingual model the first narration downloads.
                   "required_mb": 9000},
}
VENV_TIMEOUT_SECONDS = 300
# The CUDA torch wheels are about 2.5 GB; allow a slow link far longer.
PIP_TIMEOUT_SECONDS = 3600
VERIFY_TIMEOUT_SECONDS = 300    # a cold `import torch` from a fresh venv can be slow
OUTPUT_TAIL_LINES = 12
OUTPUT_LINE_CHARS = 200

_STATE_LOCK = threading.Lock()
_RESULTS: dict[str, dict] = {}


class AlreadyInstalled(admin_svc.AdminActionRefused, ConflictError):
    pass


def job_id(engine: str) -> str:
    return f"voice_engine_install_{engine}"


def remove_job_id(engine: str) -> str:
    return f"voice_engine_remove_{engine}"


def _spec(engine: str) -> dict:
    if engine not in ENGINES:
        raise InvalidInputError("Unknown voice engine.")
    return ENGINES[engine]


def _clean(text) -> str:
    """Secrets out first, then filesystem paths (pip prints the venv's own)."""
    out = shared.redact_secrets("" if text is None else str(text))
    return diagnostics_report.redact_for_support(out)[:OUTPUT_LINE_CHARS]


def _job_view(jid: str):
    job = background_jobs.get_status(jid)
    if not job:
        return None
    return {"status": job.get("status"), "progress": float(job.get("progress") or 0.0),
            "message": _clean(job.get("message") or ""),
            "error": _clean(job.get("error")) if job.get("error") else None}


def _current_job(engine: str) -> dict:
    """The install or the removal, whichever is running (else the install), so the card
    polls and cancels the right one."""
    remove = _job_view(remove_job_id(engine))
    if remove and remove["status"] in ("running", "queued"):
        return {"job_id": remove_job_id(engine), "job": remove}
    return {"job_id": job_id(engine), "job": _job_view(job_id(engine))}


def _free_mb() -> int:
    folder = os.path.join(portable.data_dir(), voice_venv.VENVS_DIRNAME)
    while folder and not os.path.exists(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return int(shutil.disk_usage(folder).free // (1024 * 1024))


def _refusal(engine: str):
    """Plain reason the install can't start now, or None."""
    spec = _spec(engine)
    if voice_venv.is_ready(engine):
        return f"{spec['label']} is already installed."
    if portable.is_installed():
        # The installed app's embeddable Python has no venv launcher, and its ._pth would
        # put the app's own packages ahead of the venv's: the clash this folder avoids.
        return (f"{spec['label']} can't be installed in this copy yet: it needs a copy of "
                "Baihe that runs from start.bat.")
    lo, hi = diagnostics_torch.TORCH_SUPPORTED_PYTHON
    hi = min(hi, spec.get("max_python", hi))
    if not lo <= sys.version_info[:2] <= hi:
        return f"PyTorch has wheels for Python {lo[0]}.{lo[1]} to {hi[0]}.{hi[1]} only."
    nvidia = diagnostics_torch.nvidia_driver_info()
    if nvidia:
        cap = diagnostics_torch.lowest_compute_capability()
        if "max_compute" in spec and cap is not None and cap > spec["max_compute"]:
            return f"{spec['label']} does not support this graphics card yet."
        drv = diagnostics_torch.driver_check(nvidia["driver_version"],
                                             variant=_torch_variant(engine))
        if drv["status"] == "too_old":
            return (f"NVIDIA driver {nvidia['driver_version']} is too old for CUDA 12 "
                    f"(needs at least {drv['minimum']}, recommended {drv['recommended']}). "
                    "Update the driver from nvidia.com, then try again.")
    if _free_mb() < spec["required_mb"]:
        return f"Not enough free disk space (about {spec['required_mb'] // 1000} GB needed)."
    return None


def get_status() -> dict:
    """Each voice engine that installs into a venv of its own: whether it is ready, the
    install job and the last result. No path."""
    free = _free_mb()
    engines = []
    for engine, spec in ENGINES.items():
        with _STATE_LOCK:
            last = dict(_RESULTS[engine]) if engine in _RESULTS else None
        engines.append({"engine": engine, "label": spec["label"],
                        "installed": voice_venv.is_ready(engine),
                        "folder_present": os.path.lexists(voice_venv.venv_dir(engine)),
                        "required_mb": spec["required_mb"], "refusal": _refusal(engine),
                        **_current_job(engine), "last_result": last})
    return {"free_mb": free, "engines": engines}


def start_install(engine: str, confirm: bool = False) -> dict:
    """PC only (the route is local_only()). 422 unconfirmed, for an unknown engine or with
    too little disk; 409 when it is installed or its install is already running."""
    admin_svc.guard(confirm)
    reason = _refusal(engine)
    if reason:
        if voice_venv.is_ready(engine):
            raise AlreadyInstalled(reason)
        raise admin_svc.AdminActionNotPossible(reason)
    if not job_launch.start_job(job_id(engine), lambda: _install_job(engine),
                                description=f"Installing {ENGINES[engine]['label']}",
                                line_fields=()):
        raise admin_svc.AdminActionJobsRunning(
            f"The {ENGINES[engine]['label']} install is already running.")
    return {"job_id": job_id(engine), "started": True}


def _torch_variant(engine: str) -> str:
    if diagnostics_torch.nvidia_driver_info() is None:
        return "cpu"
    if "torch" in ENGINES[engine]:
        return "cu126"
    return diagnostics_torch.recommended_gpu_variant()[0]


def _torch_step(engine: str) -> list:
    """The torch and torchaudio pins and their fixed index: the app's own versions, or
    the engine's when its package pins another torch."""
    variant = _torch_variant(engine)
    torch_spec = diagnostics_torch.TORCH_VARIANTS[variant]
    own = ENGINES[engine].get("torch")
    pins = [f"{n}=={own[variant] if own else torch_spec['versions'][n]}"
            for n in ("torch", "torchaudio")]
    return [*pins, "--index-url", torch_spec["index_url"]]


def _commands(engine: str) -> list:
    """(progress, message, argv, timeout) per step. pip runs from the app's own Python
    with --python, so the venv needs no pip of its own (an installed copy's embedded
    Python has no ensurepip). PyTorch comes first from its fixed CUDA index, matched to
    the card, so the engine's own dependencies find it already satisfied."""
    venv_dir = voice_venv.venv_dir(engine)
    python = voice_venv.venv_python(engine)
    pip = [sys.executable, "-m", "pip", "--python", python, "install",
           *diagnostics.PIP_INSTALL_FLAGS]
    label = ENGINES[engine]["label"]
    return [
        (0.02, f"Creating {label}'s folder…",
         [sys.executable, "-m", "venv", "--without-pip", venv_dir], VENV_TIMEOUT_SECONDS),
        (0.05, "Installing PyTorch…",
         [*pip, *_torch_step(engine)], PIP_TIMEOUT_SECONDS),
        (0.6, f"Installing {label}…", [*pip, *ENGINES[engine]["packages"]], PIP_TIMEOUT_SECONDS),
        # A venv that can't import its engine must not count as ready.
        (0.95, f"Checking {label}…", [python, "-I", "-c", ENGINES[engine]["check"]],
         VERIFY_TIMEOUT_SECONDS),
    ]


def _store(engine: str, ok: bool, message: str, tail) -> None:
    with _STATE_LOCK:
        _RESULTS[engine] = {"ok": ok, "message": message, "output_tail": list(tail)}


def _remove_partial(engine: str) -> None:
    # The install is refused while a ready venv exists, so whatever is in the folder now
    # is this run's leftovers or an earlier interrupted run's.
    shutil.rmtree(voice_venv.venv_dir(engine), ignore_errors=True)


def _install_job(engine: str):
    jid = job_id(engine)
    label = ENGINES[engine]["label"]
    tail = deque(maxlen=OUTPUT_TAIL_LINES)
    try:
        _remove_partial(engine)
        os.makedirs(os.path.dirname(voice_venv.venv_dir(engine)), exist_ok=True)
        for progress, message, argv, timeout in _commands(engine):
            background_jobs.update_progress(jid, progress, message)
            end = {"returncode": None, "timed_out": False, "cancelled": False}
            for item in stream_tree(argv, timeout, cancel=lambda: background_jobs.is_cancel_requested(jid),
                                    warn=job_process_kill._warn_via_jobs):
                if "line" in item:
                    line = _clean(item["line"].strip())
                    if line:
                        tail.append(line)
                else:
                    end = item
            if end["cancelled"]:
                _remove_partial(engine)
                _store(engine, False, "Cancelled.", tail)
                raise background_jobs.JobCancelled()
            if end["timed_out"] or end["returncode"] != 0:
                message = ("The install took too long and was stopped. Check your connection "
                           "and try again." if end["timed_out"]
                           else f"The {label} install failed; see the output.")
                _remove_partial(engine)
                _store(engine, False, message, tail)
                background_jobs.set_result(jid, {"status": "failed", "detail": message})
                raise RuntimeError(message)
        with open(os.path.join(voice_venv.venv_dir(engine), voice_venv.READY_MARKER), "w",
                  encoding="utf-8"):
            pass
    except (background_jobs.JobCancelled, RuntimeError):
        raise
    except Exception:      # noqa: BLE001 -- never echo an exception (paths)
        _remove_partial(engine)
        _store(engine, False, f"The {label} install could not start.", tail)
        raise RuntimeError(f"The {label} install could not start.") from None
    _store(engine, True, f"{label} installed.", tail)
    background_jobs.set_result(jid, {"status": "ok", "detail": f"{label} installed."})


def start_remove(engine: str, confirm: bool = False) -> dict:
    """PC only (the route is local_only()). Deletes the engine's own folder,
    voice_engines/<engine>, and nothing else, as a background job. 422 unconfirmed, for an
    unknown engine or when there is no folder; 409 while any job runs (a narration may be
    using the venv)."""
    label = _spec(engine)["label"]
    admin_svc.guard(confirm)
    if not os.path.lexists(voice_venv.venv_dir(engine)):
        raise admin_svc.AdminActionNotPossible(f"{label} is not installed.")
    if not job_launch.start_job(remove_job_id(engine), lambda: _remove_job(engine),
                                description=f"Removing {label}", line_fields=()):
        raise admin_svc.AdminActionJobsRunning(f"{label} is already being removed.")
    return {"job_id": remove_job_id(engine), "started": True}


def _remove_job(engine: str):
    label = ENGINES[engine]["label"]
    folder = voice_venv.venv_dir(engine)
    background_jobs.update_progress(remove_job_id(engine), 0.1, f"Removing {label}…")
    if os.path.islink(folder):
        # Never follow a link out of the data folder: only the link itself goes.
        os.unlink(folder)
    else:
        # The marker goes first, so a removal cut short never leaves a venv that counts
        # as ready.
        marker = os.path.join(folder, voice_venv.READY_MARKER)
        if os.path.exists(marker):
            os.remove(marker)
        shutil.rmtree(folder, ignore_errors=True)
    if os.path.lexists(folder):
        message = (f"Some of {label}'s files could not be removed. Close anything using them "
                   "and press Remove again.")
        _store(engine, False, message, [])
        raise RuntimeError(message)
    _store(engine, True, f"{label} removed.", [])
    background_jobs.set_result(remove_job_id(engine), {"status": "ok", "detail": f"{label} removed."})

"""services/voice_engine_install_service.py -- Diagnostics' install of a voice engine into a venv of its own (voice_venv)."""

import os
import shutil
import sys
import threading
import time
from collections import deque
from typing import Any

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
from lib.proc import run_captured, secretless_env, stream_tree
from services import diagnostics_admin_service as admin_svc

# Only these engines, with these exact packages, ever reach pip: nothing in a request
# names a package or a folder.
ENGINES = {
    # Pulls its own huggingface-hub and pillow pins, which the app's own Python can't take.
    "omnivoice": {"label": "OmniVoice", "packages": ("omnivoice==0.2.1",),
                  "check": "import torch, torchaudio, omnivoice",
                  # CUDA PyTorch (about 2.5 GB) and the package, with room left for the
                  # checkpoint and Whisper model the first narration downloads.
                  "required_mb": 8000},
    "qwen3_tts": {"label": "Qwen3-TTS", "packages": ("qwen-tts==0.1.1",),
                  "check": "import torch, torchaudio, qwen_tts",
                  # CUDA PyTorch (about 2.5 GB), qwen-tts and its dependencies, with room
                  # left for the model the first narration downloads.
                  "required_mb": 6000},
    # Below Python 3.14 chatterbox-tts 0.1.7 pins torch 2.6.0, which has no CUDA 12.8
    # build and no kernels for cards newer than compute 9.0 (RTX 50 series); on 3.14 it
    # takes the app's own torch. Its watermarker (resemble-perth) needs pkg_resources,
    # which setuptools 81 dropped, and without it the model fails to load while the
    # package still imports, so the check looks for the watermarker.
    "chatterbox": {"label": "Chatterbox",
                   "packages": ("chatterbox-tts==0.1.7", "setuptools<81"),
                   "check": ("import torch, torchaudio, chatterbox.mtl_tts, perth; "
                             "assert perth.PerthImplicitWatermarker is not None"),
                   "torch": {"cu126": "2.6.0+cu126", "cpu": "2.6.0+cpu"},
                   "torch_max_python": (3, 13), "torch_max_compute": 9.0,
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

# An installed copy's own Python is the embeddable kind, which has no `venv` module, so
# the engine's folder is built from a full Python on the PC instead. It must be one every
# account can run: the boot service's account can't read a Python kept in a user's profile.
FULL_PYTHON_RANGE = ((3, 12), (3, 13))
FULL_PYTHON_SECONDS = 30       # status polls ask for it once per engine, every few seconds
PROBE_TIMEOUT_SECONDS = 20
_PROBE = "import sys, venv; print(sys.version_info[0], sys.version_info[1]); print(sys.executable)"
_FULL_PYTHON: dict[str, Any] = {"at": None, "found": None, "probing": False}

_STATE_LOCK = threading.Lock()
# admin_svc.guard() and start_job() are separate steps, so two requests could both pass the
# guard before either job exists: an install then ran beside a Remove of the same folder.
_LAUNCH_LOCK = threading.Lock()
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


def _gpu():
    """{"driver_version", "caps"} of the NVIDIA GPUs, or None without one; read once
    per status, as each read starts nvidia-smi."""
    nvidia = diagnostics_torch.nvidia_driver_info()
    if not nvidia:
        return None
    return {"driver_version": nvidia["driver_version"],
            "caps": diagnostics_torch.compute_capabilities()}


def _probe_python(command: list):
    """(path, (major, minor)) of the Python that `command` starts when it is a full 3.12 or
    3.13 with `venv` that every account can run, else None."""
    try:
        result = run_captured([*command, "-I", "-c", _PROBE], PROBE_TIMEOUT_SECONDS,
                              env=secretless_env())
    except OSError:
        return None
    if result.timed_out or result.returncode != 0:
        return None
    lines = (result.stdout or "").splitlines()
    if len(lines) < 2:
        return None
    try:
        major, minor = (int(part) for part in lines[0].split())
    except ValueError:
        return None
    path = lines[1].strip()
    lo, hi = FULL_PYTHON_RANGE
    if not (lo <= (major, minor) <= hi) or not os.path.isfile(path):
        return None
    home = os.path.normcase(os.path.expanduser("~")).rstrip("\\/") + os.sep
    if os.path.normcase(path).startswith(home):
        return None
    return path, (major, minor)


def _find_full_python():
    launcher = shutil.which("py")
    candidates = [[launcher, "-3.13"], [launcher, "-3.12"]] if launcher else []
    for name in ("python", "python3"):
        exe = shutil.which(name)
        if exe:
            candidates.append([exe])
    for command in candidates:
        found = _probe_python(command)
        if found:
            return found
    return None


def _refresh_full_python():
    found = None
    try:
        found = _find_full_python()
    finally:
        # A crashed probe must not leave `probing` set, or no later poll would retry.
        with _STATE_LOCK:
            _FULL_PYTHON.update(at=time.monotonic(), found=found, probing=False)
    return found


def _spawn(fn) -> None:
    threading.Thread(target=fn, name="voice-python-probe", daemon=True).start()


def _full_python(fresh: bool = False):
    """(found, checking): found is (path, (major, minor)) of the Python an installed copy
    builds a voice engine's folder from, or None. The app's own interpreter when this copy
    is not an installed one.

    Probes can take 20 s each, so a status poll never runs them: it gets the last answer
    and starts a refresh in the background. A missing Python is re-probed on every poll,
    so the page notices an install within one poll. `fresh` probes now, for acts that
    must not rely on an old answer (starting an install)."""
    if not portable.is_installed():
        return (sys.executable, sys.version_info[:2]), False
    if fresh:
        return _refresh_full_python(), False
    with _STATE_LOCK:
        probed = _FULL_PYTHON["at"] is not None
        stale = probed and time.monotonic() - _FULL_PYTHON["at"] >= FULL_PYTHON_SECONDS
        found = _FULL_PYTHON["found"]
        start = not _FULL_PYTHON["probing"] and (not probed or stale or found is None)
        if start:
            _FULL_PYTHON["probing"] = True
    if start:
        _spawn(_refresh_full_python)
    return found, not probed


def _engine_python_version() -> tuple:
    """The version of the Python the engine's folder is built from. PyTorch wheels are
    chosen by the folder's Python, not the app's own."""
    found, _checking = _full_python()
    return found[1] if found else sys.version_info[:2]


def _own_torch(engine: str):
    """The engine's own torch pins ({variant: version}) when its package needs another
    torch on this Python, else None."""
    spec = ENGINES[engine]
    if "torch" in spec and _engine_python_version() <= spec["torch_max_python"]:
        return spec["torch"]
    return None


def _refusal(engine: str, gpu: dict | None):
    """Plain reason the install can't start now, or None. `gpu` is _gpu()."""
    spec = _spec(engine)
    if voice_venv.is_ready(engine):
        return f"{spec['label']} is already installed."
    found, checking = _full_python()
    if checking:
        return "Looking for Python 3.12 or 3.13 on this PC…"
    if found is None:
        return (f"{spec['label']} needs Python 3.12 or 3.13 installed for all users on this PC "
                "(from python.org, with \"Install for all users\" ticked). Install it, then "
                "try again.")
    lo, hi = diagnostics_torch.TORCH_SUPPORTED_PYTHON
    if not lo <= found[1] <= hi:
        return f"PyTorch has wheels for Python {lo[0]}.{lo[1]} to {hi[0]}.{hi[1]} only."
    if gpu:
        # Any card too new counts: the worker may run on whichever is device 0.
        if (_own_torch(engine) and gpu["caps"]
                and max(gpu["caps"]) > spec["torch_max_compute"]):
            return f"{spec['label']} does not support this graphics card yet."
        drv = diagnostics_torch.driver_check(gpu["driver_version"],
                                             variant=_torch_variant(engine, gpu))
        if drv["status"] == "too_old":
            return (f"NVIDIA driver {gpu['driver_version']} is too old for CUDA 12 "
                    f"(needs at least {drv['minimum']}, recommended {drv['recommended']}). "
                    "Update the driver from nvidia.com, then try again.")
    if _free_mb() < spec["required_mb"]:
        return f"Not enough free disk space (about {spec['required_mb'] // 1000} GB needed)."
    return None


def get_status() -> dict:
    """Each voice engine that installs into a venv of its own: whether it is ready, the
    install job and the last result. No path."""
    free = _free_mb()
    gpu = _gpu()
    engines = []
    for engine, spec in ENGINES.items():
        with _STATE_LOCK:
            last = dict(_RESULTS[engine]) if engine in _RESULTS else None
        engines.append({"engine": engine, "label": spec["label"],
                        "installed": voice_venv.is_ready(engine),
                        "folder_present": os.path.lexists(voice_venv.venv_dir(engine)),
                        "required_mb": spec["required_mb"], "refusal": _refusal(engine, gpu),
                        **_current_job(engine), "last_result": last})
    return {"free_mb": free, "engines": engines}


def start_install(engine: str, confirm: bool = False) -> dict:
    """PC only (the route is local_only()). 422 unconfirmed, for an unknown engine or with
    too little disk; 409 when it is installed or its install is already running."""
    reason = None
    with _LAUNCH_LOCK:
        admin_svc.guard(confirm)
        _full_python(fresh=True)
        reason = _refusal(engine, _gpu())
        if not reason and not job_launch.start_job(
                job_id(engine), lambda: _install_job(engine),
                description=f"Installing {ENGINES[engine]['label']}", line_fields=()):
            raise admin_svc.AdminActionJobsRunning(
                f"The {ENGINES[engine]['label']} install is already running.")
    if reason:
        if voice_venv.is_ready(engine):
            raise AlreadyInstalled(reason)
        raise admin_svc.AdminActionNotPossible(reason)
    return {"job_id": job_id(engine), "started": True}


def _torch_variant(engine: str, gpu: dict | None) -> str:
    if gpu is None:
        return "cpu"
    if _own_torch(engine):
        return "cu126"
    return diagnostics_torch.gpu_variant_for(min(gpu["caps"]) if gpu["caps"] else None)[0]


def _torch_step(engine: str) -> list:
    """The torch and torchaudio pins and their fixed index: the app's own versions, or
    the engine's when its package pins another torch."""
    variant = _torch_variant(engine, _gpu())
    torch_spec = diagnostics_torch.TORCH_VARIANTS[variant]
    own = _own_torch(engine)
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
    full = _full_python(fresh=True)[0]
    pip = [sys.executable, "-m", "pip", "--python", python, "install",
           *diagnostics.PIP_INSTALL_FLAGS]
    label = ENGINES[engine]["label"]
    return [
        (0.02, f"Creating {label}'s folder…",
         [full[0] if full else sys.executable, "-m", "venv", "--without-pip", venv_dir],
         VENV_TIMEOUT_SECONDS),
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
            for item in stream_tree(argv, timeout, env=secretless_env(),
                                    cancel=lambda: background_jobs.is_cancel_requested(jid),
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
    with _LAUNCH_LOCK:
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
    # os.path.islink is False for a Windows junction, so the marker delete and rmtree below
    # would act on the junction's target.
    if getattr(os.path, "isjunction", lambda _p: False)(folder):
        os.rmdir(folder)
    elif os.path.islink(folder):
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

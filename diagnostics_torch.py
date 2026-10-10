"""
diagnostics_torch.py -- is the GPU usable, and is the torch family
(torch, torchvision, torchaudio) installed right: the live GPU readout,
nvidia-smi load and driver probes, and the matched-triple install and
verify helpers behind Diagnostics > "GPU PyTorch".
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time

import diagnostics


def get_gpu_status() -> dict:
    """A live GPU/VRAM readout for Diagnostics' routine view -- {"available": bool, "name", "vram_used_gb", "vram_total_gb",
    "torch_cuda_version", "message"}. "available" is False, with a plain
    "message" (never an exception), for every case that isn't a real,
    torch-visible CUDA device: torch not installed, torch installed but
    can't see a GPU with no NVIDIA GPU on the machine, and torch installed
    but CPU-only despite a real NVIDIA GPU being present (the same
    footgun gpu_torch_mismatch() already detects, worded here as a plain
    status message rather than a warning+action). "torch_cuda_version"
    (torch.version.cuda -- what torch was built against, distinct from
    whether a GPU is actually available right now) is included whenever
    torch is installed, even when no GPU is available, since it's useful
    context either way. Never imports torch if it isn't installed."""
    if not diagnostics.check_dependency("torch"):
        return {"available": False, "message": "PyTorch isn't installed -- GPU info unavailable."}
    try:
        import torch
    except Exception:
        return {"available": False, "message": "GPU info unavailable."}
    torch_cuda_version = getattr(torch.version, "cuda", None)
    try:
        cuda_available = bool(torch.cuda.is_available())
    except Exception:
        cuda_available = False
    if not cuda_available:
        if shutil.which("nvidia-smi"):
            message = ("A real NVIDIA GPU is on this machine, but the installed PyTorch build "
                       "is CPU-only -- reinstall following pytorch.org's own selector for your "
                       "driver (or use the Install GPU PyTorch button below).")
        else:
            message = "GPU info unavailable -- no CUDA-capable GPU detected."
        return {"available": False, "torch_cuda_version": torch_cuda_version, "message": message}
    try:
        device_index = torch.cuda.current_device()
        props = torch.cuda.get_device_properties(device_index)
        return {
            "available": True,
            "name": props.name,
            "vram_used_gb": torch.cuda.memory_allocated(device_index) / (1024 ** 3),
            "vram_total_gb": props.total_memory / (1024 ** 3),
            "torch_cuda_version": torch_cuda_version,
        }
    except Exception:
        return {"available": False, "torch_cuda_version": torch_cuda_version,
                "message": "GPU info unavailable."}


# Importing torch or ctranslate2 in the server opens a CUDA context that keeps
# their DLLs locked until the process exits, so GPU PyTorch setup can't replace
# them (WinError 5). The overview asks a short-lived child instead.
_GPU_STATUS_SCRIPT = (
    "import json, diagnostics_torch, whisper_models\n"
    "out = dict(diagnostics_torch.get_gpu_status())\n"
    "out['whisper'] = whisper_models.gpu_status()\n"
    "print(json.dumps(out))\n")
GPU_STATUS_CHILD_TIMEOUT_SECONDS = 60
# Diagnostics re-polls; a cold `import torch` takes seconds, so reuse a recent answer.
GPU_STATUS_CACHE_SECONDS = 30
_gpu_status_lock = threading.Lock()
_gpu_status_cache = {"at": None, "value": None}


def _gpu_status_from_child() -> dict:
    from lib import proc as proc_run
    unavailable = {"available": False, "message": "GPU info unavailable.",
                   "whisper": {"ctranslate2_cuda_devices": None, "torch_cuda_available": None,
                               "errors": []}}
    try:
        proc = proc_run.run_captured(
            [sys.executable, "-c", _GPU_STATUS_SCRIPT], GPU_STATUS_CHILD_TIMEOUT_SECONDS,
            cwd=os.path.dirname(os.path.abspath(__file__)))
    except OSError:
        return unavailable
    if proc.timed_out:
        return dict(unavailable, message="GPU info unavailable -- the GPU check took too long.")
    data = parse_torch_verify_output(proc.stdout)
    if "error" in data or not isinstance(data.get("whisper"), dict):
        return unavailable
    return data


def get_gpu_status_isolated() -> dict:
    """get_gpu_status() plus whisper_models.gpu_status() under "whisper",
    measured in a child process so this process never loads torch or
    ctranslate2. Cached for GPU_STATUS_CACHE_SECONDS; one child at a time."""
    with _gpu_status_lock:
        at = _gpu_status_cache["at"]
        if at is None or time.monotonic() - at > GPU_STATUS_CACHE_SECONDS:
            _gpu_status_cache["value"] = _gpu_status_from_child()
            _gpu_status_cache["at"] = time.monotonic()
        value = _gpu_status_cache["value"]
    return json.loads(json.dumps(value))


def gpu_torch_mismatch() -> bool:
    """True only when a real NVIDIA GPU is on this machine (nvidia-smi on
    PATH) but the installed torch build can't see it -- the exact
    CPU-only-wheel footgun traced to a bare `pip install
    torch` always resolving to PyPI's default (non-CUDA) wheel. A
    minimal, self-contained version of the same nvidia-smi-on-PATH
    detection a fuller GPU/VRAM display will also use --
    that display doesn't exist yet, but this button needs the
    same signal regardless of which of the two lands first."""
    if not shutil.which("nvidia-smi"):
        return False
    cuda = diagnostics.check_cuda()
    return bool(cuda["torch_installed"]) and cuda["cuda_available"] is False


# How busy the GPU actually is, straight from the driver --
# independent of anything Baihe itself is tracking. background_jobs.py's
# in-process guard and db.py's cross-process gpu_lock both only
# know about GPU-touching work Baihe itself started; neither can see a
# completely different application (Jellyfin doing hardware-accelerated
# transcoding on the same card, say) using the same physical GPU. This is
# the only signal that can.
EXTERNAL_GPU_BUSY_UTIL_PERCENT = 50
EXTERNAL_GPU_BUSY_MIN_FREE_MB = 1024


def external_gpu_load() -> dict | None:
    """Real utilization/VRAM for the first GPU nvidia-smi reports, or None
    if nvidia-smi isn't on PATH or the query fails for any reason --
    best-effort, same as the rest of this module's GPU detection, never
    raises. Deliberately reads the driver directly rather than anything
    torch-based, since torch may not even be installed/loaded at the
    point this gets called (background_jobs.py checks this before a job
    that would import torch has started)."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, errors="replace", timeout=5, check=True)
        line = result.stdout.strip().splitlines()[0]
        util_percent, used_mb, total_mb = (float(x.strip()) for x in line.split(","))
        return {"utilization_percent": util_percent, "memory_used_mb": used_mb,
                "memory_total_mb": total_mb, "memory_free_mb": total_mb - used_mb}
    except Exception:
        return None


def external_gpu_is_busy(load=...):
    """True if the GPU looks meaningfully loaded by *something* right now,
    per nvidia-smi -- whether or not Baihe itself started it. False (never
    blocks a job) if nvidia-smi isn't available: this is a belt-and-suspenders
    check layered on top of Baihe's own two GPU locks, not a replacement for
    either, so its absence shouldn't be treated as "GPU busy" any more than
    it already is today."""
    load = external_gpu_load() if load is ... else load
    if load is None:
        return False
    return (load["utilization_percent"] >= EXTERNAL_GPU_BUSY_UTIL_PERCENT or
            load["memory_free_mb"] < EXTERNAL_GPU_BUSY_MIN_FREE_MB)


# ---------------------------------------------------------------------------
# GPU PyTorch setup (Diagnostics > "GPU PyTorch"). torch, torchvision and
# torchaudio are built against each other: each torchvision/torchaudio
# release requires one exact torch release, and pip resolving any of the
# three on its own is how a CUDA torch gets swapped for a CPU one or a
# torchvision ends up requiring a torch that isn't installed ("torchvision
# 0.29.0 requires torch==2.14.0, but you have torch 2.11.0+cu128"). So the
# app installs a matched triple, pinned exactly, from one fixed index, and
# every other install/upgrade pins whatever torch family is installed.
#
# Sources (checked 2026-09-29):
# - torch <-> torchvision pairs: the compatibility table in
#   https://github.com/pytorch/vision/blob/main/README.md
#   (2.13/0.28, 2.12/0.27, 2.11/0.26, 2.10/0.25, 2.9/0.24, 2.8/0.23).
#   torchaudio's version equals torch's (https://pytorch.org/audio/main/installation.html).
# - wheels actually published: https://download.pytorch.org/whl/cu128/torch/
#   (and /torchvision/, /torchaudio/): 2.11.0+cu128 / 0.26.0+cu128 /
#   2.11.0+cu128 is the newest cu128 triple, for CPython 3.10-3.14 on
#   Windows and Linux; https://download.pytorch.org/whl/cpu/ has the same
#   versions as +cpu.
# - driver floor: NVIDIA's CUDA Toolkit release notes, "CUDA Toolkit and
#   Corresponding Driver Versions" -- CUDA 12.8 GA needs >= 570.65 on
#   Windows, >= 570.26 on Linux; minor-version compatibility lets CUDA 12.x
#   run (without newer-GPU support or PTX JIT) from 525.60.13 / 528.33.
#   https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html
# Update this table (and constraints.txt's comment) when moving to a newer
# CUDA index; nothing here is ever taken from a request.
# ---------------------------------------------------------------------------

TORCH_FAMILY = ("torch", "torchvision", "torchaudio")

# variant -> the fixed index and the exact triple installed from it.
TORCH_VARIANTS = {
    "cu128": {
        "label": "NVIDIA GPU (CUDA 12.8)",
        "index_url": "https://download.pytorch.org/whl/cu128",
        "versions": {"torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128",
                     "torchaudio": "2.11.0+cu128"},
        "needs_nvidia": True,
    },
    "cpu": {
        "label": "CPU only (no NVIDIA GPU)",
        "index_url": "https://download.pytorch.org/whl/cpu",
        "versions": {"torch": "2.11.0+cpu", "torchvision": "0.26.0+cpu",
                     "torchaudio": "2.11.0+cpu"},
        "needs_nvidia": False,
    },
}
TORCH_RECOMMENDED_VARIANT_GPU = "cu128"
# CPython versions the triple above has wheels for (inclusive).
TORCH_SUPPORTED_PYTHON = ((3, 10), (3, 14))

# torch major.minor -> the torchvision major.minor built for it (README table above).
TORCHVISION_FOR_TORCH = {"2.8": "0.23", "2.9": "0.24", "2.10": "0.25", "2.11": "0.26",
                         "2.12": "0.27", "2.13": "0.28", "2.14": "0.29"}

# NVIDIA driver needed by the cu128 wheels, per OS: "recommended" is CUDA
# 12.8's own requirement; below "minimum" CUDA 12 can't run at all.
NVIDIA_DRIVER_FOR_CU128 = {
    "Windows": {"recommended": "570.65", "minimum": "528.33"},
    "Linux": {"recommended": "570.26", "minimum": "525.60.13"},
}

TORCH_SETUP_TIMEOUT_SECONDS = 3600    # ~2.5 GB of CUDA wheels on a slow link
TORCH_VERIFY_TIMEOUT_SECONDS = 180    # a cold `import torch` can take a while

_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+!_-]{0,63}$")


def _mm(version: str) -> str:
    """"2.11.0+cu128" -> "2.11"."""
    return ".".join(re.split(r"[.+]", version or "")[:2])


def nvidia_driver_info():
    """{"gpu_name", "driver_version"} for the first GPU nvidia-smi lists, or
    None when nvidia-smi isn't on PATH or fails. Never raises; bounded by a
    timeout like external_gpu_load."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, errors="replace", timeout=5, check=True)
        name, driver = (x.strip() for x in result.stdout.strip().splitlines()[0].rsplit(",", 1))
        return {"gpu_name": name[:120], "driver_version": driver[:40]}
    except Exception:
        return None


def driver_check(driver_version, system: str = None) -> dict:
    """{"status": "ok"|"old"|"too_old"|"unknown", "recommended", "minimum"}
    for the cu128 wheels on this OS. "old" works through CUDA's
    minor-version compatibility (a warning); "too_old" can't run CUDA 12."""
    system = system or platform.system()
    need = NVIDIA_DRIVER_FOR_CU128.get(system, NVIDIA_DRIVER_FOR_CU128["Linux"])
    out = {"recommended": need["recommended"], "minimum": need["minimum"]}
    if not driver_version:
        return {"status": "unknown", **out}
    have = diagnostics._version_sort_key(driver_version)
    if have < diagnostics._version_sort_key(need["minimum"]):
        return {"status": "too_old", **out}
    if have < diagnostics._version_sort_key(need["recommended"]):
        return {"status": "old", **out}
    return {"status": "ok", **out}


def _build_of(version):
    """"cuda" for a +cuXXX local tag, "cpu" for +cpu, None when the version
    carries no build tag (e.g. PyPI's Linux wheels) or isn't installed."""
    if not version:
        return None
    tag = version.partition("+")[2].lower()
    if tag.startswith("cu") or tag.startswith("rocm"):
        return "cuda"
    if tag == "cpu":
        return "cpu"
    return None


def torch_family_versions() -> dict:
    """{name: {"version", "build"}} for torch/torchvision/torchaudio, from
    installed metadata only (no import, so it's right even after an install
    in this same process)."""
    out = {}
    for name in TORCH_FAMILY:
        version = diagnostics.get_installed_version(name)
        out[name] = {"version": version, "build": _build_of(version)}
    return out


def torch_family_problems(versions: dict) -> list:
    """Plain-English mismatches between installed torch, torchvision and
    torchaudio: a torchvision/torchaudio built for another torch, or CUDA
    and CPU builds mixed."""
    torch_v = (versions.get("torch") or {}).get("version")
    if not torch_v:
        return [f"{n} is installed without torch." for n in TORCH_FAMILY[1:]
                if (versions.get(n) or {}).get("version")]
    problems = []
    tv = (versions.get("torchvision") or {}).get("version")
    want_tv = TORCHVISION_FOR_TORCH.get(_mm(torch_v))
    if tv and want_tv and _mm(tv) != want_tv:
        problems.append(f"torchvision {tv} doesn't match torch {torch_v} "
                        f"(torch {_mm(torch_v)} needs torchvision {want_tv}.x).")
    ta = (versions.get("torchaudio") or {}).get("version")
    if ta and _mm(ta) != _mm(torch_v):
        problems.append(f"torchaudio {ta} doesn't match torch {torch_v} "
                        f"(it must be {_mm(torch_v)}.x).")
    builds = {(versions.get(n) or {}).get("build") for n in TORCH_FAMILY} - {None}
    if len(builds) > 1:
        problems.append("CUDA and CPU builds are mixed; reinstall all three together.")
    return problems


def torch_pin_lines() -> list:
    """Exact pins ("torch==2.11.0+cu128") for each installed torch-family
    package, for a constraints file every other install/upgrade passes to
    pip, so a package that depends on torch can't swap a CUDA build for a
    CPU one or move torchvision off its torch. Versions come from local
    metadata and are checked against a strict pattern before use."""
    lines = []
    for name in TORCH_FAMILY:
        version = diagnostics.get_installed_version(name)
        if version and _VERSION_RE.match(version):
            lines.append(f"{name}=={version}")
    return lines


def torch_setup_pip_args(variant: str, project_root: str = None) -> list:
    """Two pip argument lists (after `install`) for the matched triple of
    `variant` (a TORCH_VARIANTS key): first `--force-reinstall --no-deps`
    of all three pinned together, so pip downloads every wheel before it
    replaces anything and torchvision/torchaudio can't resolve against
    another torch; then the same pins without --force-reinstall to add
    any missing dependency (nvidia-* wheels, sympy, pillow, ...). Both from
    the variant's fixed index, with constraints.txt's caps. KeyError for an
    unknown variant."""
    spec = TORCH_VARIANTS[variant]
    pins = [f"{n}=={spec['versions'][n]}" for n in TORCH_FAMILY]
    tail = ["--index-url", spec["index_url"]]
    tail += diagnostics.constraints_pip_args(project_root)
    return [["--force-reinstall", "--no-deps", *pins, *tail], [*pins, *tail]]


# Run by `python -c` after a setup, so the check sees the new wheels and not
# the torch this process may already have imported. Prints one JSON line.
TORCH_VERIFY_SCRIPT = """
import json
out = {}
try:
    import torch
    out["torch"] = torch.__version__
    out["cuda_build"] = torch.version.cuda
    out["cuda_available"] = bool(torch.cuda.is_available())
    if out["cuda_available"]:
        out["device"] = torch.cuda.get_device_name(0)
        torch.zeros(1, device="cuda")
except Exception as e:
    out["error"] = type(e).__name__ + ": " + str(e)[:300]
for name in ("torchvision", "torchaudio"):
    try:
        out[name] = __import__(name).__version__
    except Exception as e:
        out[name + "_error"] = type(e).__name__ + ": " + str(e)[:300]
print(json.dumps(out))
"""


def parse_torch_verify_output(stdout: str) -> dict:
    """The JSON the verify script printed (its last line), or {"error"}."""
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                break
            return data if isinstance(data, dict) else {"error": "unexpected output"}
    return {"error": "the check printed nothing usable"}

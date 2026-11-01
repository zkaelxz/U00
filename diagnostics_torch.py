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
from typing import Optional

from jobs import job_launch
import dependency_updates
import diagnostics
from lib.proc import run_captured


def get_gpu_status() -> dict:
    """A live GPU/VRAM readout for Diagnostics' routine view -- {"available": bool, "name", "vram_used_gb", "vram_total_gb",
    "torch_cuda_version", "message"}. "available" is False, with a plain
    "message" (never an exception), for every case that isn't a real,
    torch-visible CUDA device: torch not installed, torch installed but
    can't see a GPU with no NVIDIA GPU on the machine, and torch installed
    but CPU-only despite a real NVIDIA GPU being present (the same
    footgun, worded as a plain status message rather than a warning+action). "torch_cuda_version"
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
        name, total_gb = props.name, props.total_memory / (1024 ** 3)
    except Exception:
        return {"available": False, "torch_cuda_version": torch_cuda_version,
                "message": "GPU info unavailable."}
    return {"available": True, "name": name, "vram_used_gb": None, "vram_total_gb": total_gb,
            "torch_cuda_version": torch_cuda_version}


# Diagnostics polls while a GPU job runs; a short cache (failures included) keeps
# a wedged nvidia-smi from collecting one stuck child per poll.
DRIVER_LOAD_CACHE_SECONDS = 2.0
_driver_load_lock = threading.Lock()
_driver_load_cache = {"at": None, "value": None}


def forget_driver_load() -> None:
    with _driver_load_lock:
        _driver_load_cache.update(at=None, value=None)


def _recent_driver_load() -> dict | None:
    # Held across the probe so concurrent readers share one run.
    with _driver_load_lock:
        at = _driver_load_cache["at"]
        if at is None or time.monotonic() - at > DRIVER_LOAD_CACHE_SECONDS:
            _driver_load_cache.update(value=external_gpu_load())
            _driver_load_cache["at"] = time.monotonic()
        return _driver_load_cache["value"]


def with_driver_vram_used(gpu: dict) -> dict:
    """Fills vram_used_gb and vram_total_gb from nvidia-smi on every call,
    outside the hour-long cache of get_gpu_status_isolated.
    torch.cuda.memory_allocated() only sees the process that asks, and
    transcription runs in a child, so it read 0 during real work; the driver
    counts every program. Both numbers come from nvidia-smi's first GPU so
    they describe one card; torch's current_device can be another one, so the
    name is taken from that same row too."""
    if not gpu.get("available"):
        return gpu
    load = _recent_driver_load()
    if load is None:
        gpu["vram_used_gb"] = None
        gpu["message"] = "GPU memory in use: not available (nvidia-smi could not be read)."
    else:
        gpu["vram_used_gb"] = load["memory_used_mb"] / 1024
        gpu["vram_total_gb"] = load["memory_total_mb"] / 1024
        if load.get("name"):
            gpu["name"] = load["name"]
    return gpu


def gpu_reading() -> dict:
    """Name and memory of the first nvidia-smi GPU, read in one query so they
    describe one card (no torch import, no full diagnostics)."""
    load = _recent_driver_load()
    if load is None:
        return {"found": False, "name": None, "vram_used_gb": None, "vram_total_gb": None}
    return {"found": True, "name": load.get("name"),
            "vram_used_gb": load["memory_used_mb"] / 1024,
            "vram_total_gb": load["memory_total_mb"] / 1024}


# Importing torch or ctranslate2 in the server opens a CUDA context that keeps
# their DLLs locked until the process exits, so GPU PyTorch setup can't replace
# them (WinError 5). The overview asks a short-lived child instead.
_GPU_STATUS_SCRIPT = (
    "import json, diagnostics_torch, whisper_models\n"
    "out = dict(diagnostics_torch.get_gpu_status())\n"
    "out['whisper'] = whisper_models.gpu_status()\n"
    "out['gpu_runtime'] = whisper_models.whisper_gpu_runtime()\n"
    "print(json.dumps(out))\n")
# A cold `import torch` on Windows with antivirus scanning the DLLs can take
# minutes, as the post-install verify allows for.
GPU_STATUS_CHILD_TIMEOUT_SECONDS = 180
# The answer only changes when the torch family or the driver does, so a
# good one is kept until an install (forget_gpu_status) or an hour passes; a
# failed probe is retried sooner.
GPU_STATUS_CACHE_SECONDS = 3600
GPU_STATUS_RETRY_SECONDS = 30
GPU_CHECK_PAUSED_MESSAGE = "GPU check paused while an install runs."
_gpu_status_lock = threading.Lock()
_gpu_status_cache = {"at": None, "value": None}


def _gpu_status_unavailable(message: str = "GPU info unavailable.") -> dict:
    return {"available": False, "message": message,
            "whisper": {"ctranslate2_cuda_devices": None, "torch_cuda_available": None,
                        "errors": []},
            "gpu_runtime": {"status": "unknown", "reason": None}}


def _gpu_status_from_child() -> dict:
    from lib import proc as proc_run
    try:
        proc = proc_run.run_captured(
            [sys.executable, "-c", _GPU_STATUS_SCRIPT], GPU_STATUS_CHILD_TIMEOUT_SECONDS,
            cwd=os.path.dirname(os.path.abspath(__file__)))
    except OSError:
        return _gpu_status_unavailable()
    if proc.timed_out:
        return _gpu_status_unavailable("GPU info unavailable -- the GPU check took too long.")
    data = parse_torch_verify_output(proc.stdout)
    if ("error" in data or not isinstance(data.get("whisper"), dict)
            or not isinstance(data.get("gpu_runtime"), dict)):
        return _gpu_status_unavailable()
    data["probed"] = True
    return data


def get_gpu_status_isolated() -> dict:
    """get_gpu_status() plus whisper_models.gpu_status() under "whisper" and
    whisper_models.whisper_gpu_runtime() under "gpu_runtime", measured in a
    child process so this process never loads torch or ctranslate2. Cached (see GPU_STATUS_CACHE_SECONDS); one child at a time."""
    with _gpu_status_lock:
        at, value = _gpu_status_cache["at"], _gpu_status_cache["value"]
        ttl = GPU_STATUS_CACHE_SECONDS if (value or {}).get("probed") else GPU_STATUS_RETRY_SECONDS
        if at is None or time.monotonic() - at > ttl:
            # A child importing torch while GPU PyTorch setup replaces its
            # files can lock them (WinError 5) or read a half-written install.
            if job_launch.exclusive_active():
                value = value if at is not None else _gpu_status_unavailable(
                    GPU_CHECK_PAUSED_MESSAGE)
            else:
                value = _gpu_status_from_child()
                _gpu_status_cache.update(at=time.monotonic(), value=value)
    out = json.loads(json.dumps(value))
    out.pop("probed", None)
    return out


def forget_gpu_status() -> None:
    """Drops the cached answer after an install, so the page shows the new
    torch rather than the one before it."""
    with _gpu_status_lock:
        _gpu_status_cache.update(at=None, value=None)


def check_cuda_isolated() -> dict:
    """diagnostics.check_cuda()'s answer, read through
    get_gpu_status_isolated() so the server never imports torch.
    "cuda_available" is None when torch couldn't be imported or the check
    didn't finish."""
    if not diagnostics.check_dependency("torch"):
        return {"torch_installed": False, "cuda_available": None}
    gpu = get_gpu_status_isolated()
    if gpu.get("available"):
        return {"torch_installed": True, "cuda_available": True}
    # get_gpu_status() sets torch_cuda_version only once torch imported.
    return {"torch_installed": True,
            "cuda_available": False if "torch_cuda_version" in gpu else None}


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
    """Real utilization/VRAM (and name) for the first GPU nvidia-smi reports, or None
    if nvidia-smi isn't on PATH or the query fails for any reason --
    best-effort, same as the rest of this module's GPU detection, never
    raises. Deliberately reads the driver directly rather than anything
    torch-based, since torch may not even be installed/loaded at the
    point this gets called (background_jobs.py checks this before a job
    that would import torch has started)."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = run_captured(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,name",
             "--format=csv,noheader,nounits"], timeout=5)
        if result.timed_out or result.returncode != 0:
            return None
        line = result.stdout.strip().splitlines()[0]
        # Name last: it is the one text column and may hold a comma.
        fields = [x.strip() for x in line.split(",", 3)]
        util_percent, used_mb, total_mb = (float(x) for x in fields[:3])
        load = {"utilization_percent": util_percent, "memory_used_mb": used_mb,
                "memory_total_mb": total_mb, "memory_free_mb": total_mb - used_mb}
        if len(fields) == 4 and fields[3]:
            load["name"] = fields[3][:120]
        return load
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
#   Windows, >= 570.26 on Linux (CUDA 12.6 GA: >= 560.76 / >= 560.28.03,
#   not used: see NVIDIA_DRIVER_FOR_CUDA); minor-version compatibility lets CUDA 12.x run (without newer-GPU
#   support or PTX JIT) from 525.60.13 / 528.33.
#   https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html
# Update this table (and constraints.txt's comment) when moving to a newer
# CUDA index; nothing here is ever taken from a request.
# ---------------------------------------------------------------------------

TORCH_FAMILY = ("torch", "torchvision", "torchaudio")

# variant -> the fixed index and the exact triple installed from it.
TORCH_VARIANTS = {
    "cu128": {
        "label": "NVIDIA GPU (CUDA 12.8)",
        "cuda": "12.8",
        "index_url": "https://download.pytorch.org/whl/cu128",
        "versions": {"torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128",
                     "torchaudio": "2.11.0+cu128"},
        "needs_nvidia": True,
    },
    # Same versions as cu128, for cards older than compute 7.5 (Volta, e.g. a V100):
    # from PyTorch 2.11 the cu128 wheels no longer contain sm_70 kernels.
    "cu126": {
        "label": "NVIDIA GPU, older cards (CUDA 12.6)",
        "cuda": "12.6",
        "index_url": "https://download.pytorch.org/whl/cu126",
        "versions": {"torch": "2.11.0+cu126", "torchvision": "0.26.0+cu126",
                     "torchaudio": "2.11.0+cu126"},
        "needs_nvidia": True,
    },
    "cpu": {
        "label": "CPU only (no NVIDIA GPU)",
        "cuda": None,
        "index_url": "https://download.pytorch.org/whl/cpu",
        "versions": {"torch": "2.11.0+cpu", "torchvision": "0.26.0+cpu",
                     "torchaudio": "2.11.0+cpu"},
        "needs_nvidia": False,
    },
}
TORCH_RECOMMENDED_VARIANT_GPU = "cu128"
TORCH_OLD_GPU_VARIANT = "cu126"
# Cards below this compute capability have no kernels in the cu128 wheels.
CU128_MIN_COMPUTE = 7.5
# CPython versions the triple above has wheels for (inclusive).
TORCH_SUPPORTED_PYTHON = ((3, 10), (3, 14))

# torch major.minor -> the torchvision major.minor built for it (README table above).
TORCHVISION_FOR_TORCH = {"2.8": "0.23", "2.9": "0.24", "2.10": "0.25", "2.11": "0.26",
                         "2.12": "0.27", "2.13": "0.28", "2.14": "0.29"}

# NVIDIA driver needed per CUDA version of a variant, per OS: "recommended"
# is that CUDA release's own requirement; below "minimum" CUDA 12 can't run
# at all. The 12.6 build is for cards below compute 7.5 (a V100), whose
# kernels ship native, so minor-version compatibility is all it needs; its
# "recommended" is that floor, because on some of those cards newer drivers
# fail to start (a V100 held at 536.25 after 537.13+ gave Code 10), and the
# app must not nudge them up.
NVIDIA_DRIVER_FOR_CUDA = {
    "12.8": {"Windows": {"recommended": "570.65", "minimum": "528.33"},
             "Linux": {"recommended": "570.26", "minimum": "525.60.13"}},
    "12.6": {"Windows": {"recommended": "528.33", "minimum": "528.33"},
             "Linux": {"recommended": "525.60.13", "minimum": "525.60.13"}},
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, check=True)
        name, driver = (x.strip() for x in result.stdout.strip().splitlines()[0].rsplit(",", 1))
        return {"gpu_name": name[:120], "driver_version": driver[:40]}
    except Exception:
        return None


def lowest_compute_capability():
    """The lowest compute capability among the NVIDIA GPUs (7.0 for a V100),
    or None when nvidia-smi is missing, fails (drivers too old for the
    compute_cap query) or prints something else. Never raises."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, check=True)
        caps = [float(x) for x in result.stdout.split() if re.fullmatch(r"\d+\.\d+", x)]
    except Exception:
        return None
    return min(caps) if caps else None


def recommended_gpu_variant():
    """(variant, reason) for a machine with an NVIDIA GPU. reason is plain
    words, or None for the default; an unknown compute capability gets the
    default."""
    cap = lowest_compute_capability()
    if cap is not None and cap < CU128_MIN_COMPUTE:
        new, old = (TORCH_VARIANTS[v]["cuda"] for v in (TORCH_RECOMMENDED_VARIANT_GPU,
                                                         TORCH_OLD_GPU_VARIANT))
        return TORCH_OLD_GPU_VARIANT, (
            f"Your GPU has compute capability {cap:.1f}, and the CUDA {new} PyTorch no longer "
            f"includes support for cards that old, so the CUDA {old} build is recommended.")
    return TORCH_RECOMMENDED_VARIANT_GPU, None


def driver_check(driver_version, system: str | None = None,
                 variant: str = TORCH_RECOMMENDED_VARIANT_GPU) -> dict:
    """{"status": "ok"|"old"|"too_old"|"unknown", "recommended", "minimum",
    "cuda"} for `variant`'s wheels (a CUDA one; the CPU one is judged as the
    default GPU build) on this OS. "old" works through CUDA's minor-version
    compatibility (a warning); "too_old" can't run CUDA 12."""
    system = system or platform.system()
    cuda = (TORCH_VARIANTS.get(variant) or {}).get("cuda") \
        or TORCH_VARIANTS[TORCH_RECOMMENDED_VARIANT_GPU]["cuda"]
    table = NVIDIA_DRIVER_FOR_CUDA[cuda]
    need = table.get(system, table["Linux"])
    out = {"recommended": need["recommended"], "minimum": need["minimum"], "cuda": cuda}
    if not driver_version:
        return {"status": "unknown", **out}
    have = diagnostics._version_sort_key(driver_version)
    if have < diagnostics._version_sort_key(need["minimum"]):
        return {"status": "too_old", **out}
    if have < diagnostics._version_sort_key(need["recommended"]):
        return {"status": "old", **out}
    return {"status": "ok", **out}


def installed_cuda_variant(versions) -> Optional[str]:
    """The TORCH_VARIANTS key of the installed torch's CUDA build ("cu126",
    "cu128"), from its local version tag; None for a CPU build, an untagged
    PyPI wheel or a tag the app doesn't install."""
    tag = ((versions.get("torch") or {}).get("version") or "").partition("+")[2].lower()
    spec = TORCH_VARIANTS.get(tag)
    return tag if spec and spec["cuda"] else None


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


def torch_setup_pip_args(variant: str, project_root: str | None = None) -> list:
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
    tail += dependency_updates.constraints_pip_args(project_root)
    return [["--force-reinstall", "--no-deps", *pins, *tail], [*pins, *tail]]


# Run by `python -c` after a setup, so the check sees the new wheels and not
# the torch this process may already have imported. Prints one JSON line.
TORCH_VERIFY_SCRIPT = """
import json
import re
out = {}
try:
    import torch
    out["torch"] = torch.__version__
    out["cuda_build"] = torch.version.cuda
    out["cuda_available"] = bool(torch.cuda.is_available())
    if out["cuda_available"]:
        out["device"] = torch.cuda.get_device_name(0)
        archs = torch.cuda.get_arch_list()
        out["devices"] = []
        for i in range(torch.cuda.device_count()):
            major, minor = torch.cuda.get_device_capability(i)
            # Native kernels, or PTX built for this arch or an older one (JIT-compiled).
            ptx = [int(m.group(1)) for m in (re.match(r"compute_([0-9]+)", a) for a in archs) if m]
            supported = ("sm_%d%d" % (major, minor)) in archs or any(
                p <= major * 10 + minor for p in ptx)
            out["devices"].append({"index": i, "name": torch.cuda.get_device_name(i)[:120],
                                   "capability": "%d.%d" % (major, minor), "supported": supported})
            if supported:
                torch.zeros(1, device="cuda:%d" % i)
except Exception as e:
    out["error"] = type(e).__name__ + ": " + str(e)[:300]
for name in ("torchvision", "torchaudio"):
    try:
        out[name] = __import__(name).__version__
    except Exception as e:
        out[name + "_error"] = type(e).__name__ + ": " + str(e)[:300]
print(json.dumps(out))
"""


NO_KERNELS_PREFIX = "This PyTorch build has no kernels for your GPU"


def unsupported_gpu_message(devices, torch_version=None):
    """Plain words for the GPUs the verify script found without kernels in
    this build, or None when every GPU is covered (or none were listed).
    Already on the cu126 build there is no other build to suggest."""
    bad = [d for d in devices or [] if d.get("supported") is False]
    if not bad:
        return None
    cards = ", ".join(f"{d.get('name') or 'GPU ' + str(d.get('index'))} (compute {d.get('capability')})"
                      for d in bad)
    if (torch_version or "").endswith("+" + TORCH_OLD_GPU_VARIANT):
        return f"{NO_KERNELS_PREFIX} {cards}."
    return f"{NO_KERNELS_PREFIX} {cards}; install the {TORCH_OLD_GPU_VARIANT} build."


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

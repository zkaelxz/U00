"""
cuda_runtime_path.py -- makes the CUDA 12 libraries faster-whisper needs
findable on Windows.

CTranslate2 (CUDA 12 build, cuDNN 9) loads cublas64_12.dll and
cudnn64_9.dll through the plain Windows DLL search path. The GPU PyTorch
wheel ships those DLLs in site-packages/torch/lib, and the NVIDIA wheels
in site-packages/nvidia/<lib>/bin, neither of which ctranslate2 searches.
ensure_cuda_runtime_on_path() adds whichever of those folders holds them,
so Whisper can use the GPU without the user touching PATH.

It only looks at folders: it never imports torch or ctranslate2 (importing
torch first would load its own copy of the DLLs and defeat the point of
fixing the search path for ctranslate2). Nothing here returns a path.
"""

import importlib.util
import os
import sys
import sysconfig

CUBLAS_DLL = "cublas64_12.dll"
CUDNN_DLL = "cudnn64_9.dll"
# cudnn64_9.dll loads its sibling DLLs at run time, so a folder that has
# only the front one is not enough.
_CUDNN_COMPANION = "cudnn_ops64_9.dll"

# Folder labels are what the result reports instead of paths.
_NVIDIA_SUBDIRS = (("nvidia-cublas", ("cublas", "bin")), ("nvidia-cudnn", ("cudnn", "bin")))

# Kept alive on purpose: a handle that is garbage-collected removes its
# directory from the search path again.
_dll_handles: list = []
_added_dirs: set = set()


def _site_roots() -> list:
    roots = []
    for key in ("purelib", "platlib"):
        path = sysconfig.get_paths().get(key)
        if path and path not in roots:
            roots.append(path)
    for name in ("torch", "nvidia"):
        # find_spec on a top-level name only reads the folder layout; it
        # does not run the package.
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            continue
        for location in (spec.submodule_search_locations or []) if spec else []:
            parent = os.path.dirname(location)
            if parent and parent not in roots:
                roots.append(parent)
    return roots


def _has(folder: str, *names: str) -> bool:
    return all(os.path.isfile(os.path.join(folder, n)) for n in names)


def _candidates() -> list:
    """(label, folder) in search order: torch's folder, the NVIDIA wheels'
    folders, then any PATH entry that already has the libraries."""
    found = []
    for root in _site_roots():
        found.append(("torch", os.path.join(root, "torch", "lib")))
        for label, parts in _NVIDIA_SUBDIRS:
            found.append((label, os.path.join(root, "nvidia", *parts)))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry:
            found.append(("PATH", entry))
    return found


def _holder(folder: str):
    """Which of the two libraries this folder holds, as short names."""
    names = []
    if _has(folder, CUBLAS_DLL):
        names.append("cublas")
    if _has(folder, CUDNN_DLL, _CUDNN_COMPANION):
        names.append("cudnn")
    return names


def _register(folder: str) -> bool:
    """Puts the folder on the DLL search path and at the front of PATH.
    Both: Python's own loader honours add_dll_directory, but ctranslate2
    loads by plain PATH search (see whisper_models._load_windows_dll)."""
    changed = False
    if folder not in _added_dirs:
        try:
            _dll_handles.append(os.add_dll_directory(folder))
        except (OSError, AttributeError):
            pass
        _added_dirs.add(folder)
        changed = True
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if folder not in parts:
        os.environ["PATH"] = os.pathsep.join([folder, *[p for p in parts if p]])
        changed = True
    return changed


def ensure_cuda_runtime_on_path() -> dict:
    """{"status": "ok" | "missing" | "unknown", "found": [where they were found, by short label],
    "added": bool}. "unknown" off Windows (nothing is changed); "missing"
    when cuBLAS or cuDNN is in none of the searched folders. Idempotent and
    never raises; "added" is True only when this call changed the search
    path. The dict holds no path."""
    if sys.platform != "win32":
        return {"status": "unknown", "found": [], "added": False}
    try:
        libs, sources, added = set(), [], False
        for label, folder in _candidates():
            have = set(_holder(folder)) - libs
            if not have:
                continue
            added = _register(folder) or added
            libs |= have
            if label not in sources:
                sources.append(label)
        status = "ok" if libs == {"cublas", "cudnn"} else "missing"
        return {"status": status, "found": sources, "added": added}
    except Exception:
        return {"status": "unknown", "found": [], "added": False}

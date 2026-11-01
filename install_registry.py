"""
install_registry.py -- which package keys a Diagnostics install may name, and
the exact pip argv for them.

Kept apart from the services so the start-up apply step can re-derive an
install from the registry alone, without importing the API or the database.
Only keys from the registry ever reach pip: no request text becomes an
argument, an index URL or a version.
"""

import os
import shutil
import sys
import tempfile

import dependency_updates
import diagnostics
import diagnostics_report
import diagnostics_torch

ROOT = os.path.dirname(os.path.abspath(__file__))

# Plain OpenCV builds that may be removed so a contrib build can take their
# place; pending_install.py repeats this tuple (it cannot import the app) and
# a test keeps the two equal. Nothing outside it ever reaches `pip uninstall`.
CV2_SWAP_UNINSTALLABLE = ("opencv-python", "opencv-python-headless")
CV2_CONTRIB = "opencv-contrib-python"
# Keys whose install pulls the exact contrib build (PaddleX pins it).
NEEDS_CV2_CONTRIB = frozenset({"paddleocr"})
# RapidOCR declares opencv-python-headless, which would share the 'cv2' folder with
# the build the app already has; its other requirements are keys of their own.
INSTALL_WITHOUT_DEPS = frozenset({"rapidocr"})
# The rest of its requirements, installed beside it because --no-deps skips them.
RAPIDOCR_KEYS = frozenset({"rapidocr", "onnxruntime", "shapely", "pyclipper", "omegaconf", "colorlog",
                           "six", "tqdm", "PyYAML"})


def installable_packages() -> set:
    """Package names an install/upgrade wrapper accepts: optional
    dependencies in an installable tier plus model-registry packages."""
    names = {k for k, (_imp, _f, tier) in diagnostics.OPTIONAL_DEPENDENCIES.items()
             if tier in diagnostics.INSTALLABLE_TIERS}
    names |= {e["package"] for e in diagnostics_report.MODEL_ENGINE_REGISTRY if e.get("package")}
    return {n for n in names
            if diagnostics.canonical_dist(diagnostics.pip_install_name(n))
            not in diagnostics.NOT_OFFERED_FOR_INSTALL}


def unqueueable_reason(keys) -> str | None:
    """Why these keys can't be planned or queued, else None. The CUDA torch
    triple comes from a fixed index through its own GPU PyTorch setup; a plain
    `pip install torch` here would swap it for the CPU wheel."""
    known = installable_packages()
    for key in keys:
        if key not in known:
            return "Unknown or non-installable package."
    if INSTALL_WITHOUT_DEPS & set(keys) and set(keys) - RAPIDOCR_KEYS:
        # --no-deps covers the whole command, so other packages would lose their own requirements.
        return "RapidOCR is installed on its own, without other packages in the same install."
    if any(k in diagnostics_torch.TORCH_FAMILY for k in keys) and shutil.which("nvidia-smi"):
        return ("PyTorch on an NVIDIA PC is set up under GPU PyTorch, not here, so it keeps "
                "its CUDA build.")
    return None


def dependency_flags(keys) -> list:
    """`--no-deps` for the keys that must not pull their own requirements, else nothing."""
    return ["--no-deps"] if INSTALL_WITHOUT_DEPS & set(keys) else []


def install_dists(keys) -> list:
    """Dist names for `keys`. The contrib build already provides cv2, so a plain
    opencv-python asked for beside a key that pulls contrib would only make the
    plan refuse the pair as two OpenCV flavours."""
    skip_plain_cv2 = bool(NEEDS_CV2_CONTRIB & set(keys))
    return [diagnostics.pip_install_name(k) for k in keys if not (skip_plain_cv2 and k == "cv2")]


def install_argv(keys, python: str | None = None, torch_pins_path: str | None = None,
                 project_root: str | None = None, specs: list | None = None) -> list:
    """The literal `pip install` argv for these keys: the registry's dist
    names, constraints.txt's caps, and a pin file holding the installed torch
    family so a dependent package can't swap a CUDA torch for a CPU one.
    `specs` replaces the dist names when an update asks for something else
    (`dist==target`, or `--upgrade dist`)."""
    reqs = install_dists(keys) if specs is None else specs
    argv = [python or sys.executable, "-m", "pip", "install", *diagnostics.PIP_INSTALL_FLAGS,
            *reqs, *dependency_updates.constraints_pip_args(project_root or ROOT)]
    argv[argv.index("install") + 1:argv.index("install") + 1] = dependency_flags(keys)
    if torch_pins_path:
        argv += ["-c", torch_pins_path]
    return argv


def cv2_swap_names(keys, installed: dict) -> list:
    """Plain OpenCV dists to remove before installing `keys`, from {canonical
    dist: version}. Only when a key needs the contrib build and it is not
    installed yet: plain and contrib share one 'cv2' folder, so the plain one
    must go first or the two break each other."""
    if not NEEDS_CV2_CONTRIB & set(keys) or CV2_CONTRIB in installed:
        return []
    return [n for n in CV2_SWAP_UNINSTALLABLE if n in installed]


def uninstall_argv(name: str, python: str | None = None) -> list:
    """The literal `pip uninstall -y NAME` argv. No flag and no name beyond
    CV2_SWAP_UNINSTALLABLE can reach it."""
    if name not in CV2_SWAP_UNINSTALLABLE:
        raise ValueError("not an uninstallable package")
    return [python or sys.executable, "-m", "pip", "uninstall", "-y", name]


def preflight_argv(install: list) -> list:
    """The install argv resolved without changing anything. It runs before an
    uninstall: the uninstall is local and always works, but the restore after
    a failed install needs the index, so the swap must not start unless pip
    can already resolve the install."""
    return [*install, "--dry-run", "--quiet"]


def write_torch_pins(keys):
    """Path of a temporary constraints file pinning the installed torch family
    (the caller removes it), or None when there is nothing to pin or the keys
    are themselves torch packages."""
    if any(k in diagnostics_torch.TORCH_FAMILY for k in keys):
        return None
    pins = diagnostics_torch.torch_pin_lines()
    if not pins:
        return None
    fd, path = tempfile.mkstemp(prefix="baihe-torch-pins-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(pins) + "\n")
    return path

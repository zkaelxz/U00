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

import diagnostics
import diagnostics_report
import diagnostics_torch

ROOT = os.path.dirname(os.path.abspath(__file__))


def installable_packages() -> set:
    """Package names an install/upgrade wrapper accepts: optional
    dependencies in an installable tier plus model-registry packages."""
    names = {k for k, (_imp, _f, tier) in diagnostics.OPTIONAL_DEPENDENCIES.items()
             if tier in diagnostics.INSTALLABLE_TIERS}
    names |= {e["package"] for e in diagnostics_report.MODEL_ENGINE_REGISTRY if e.get("package")}
    return {n for n in names
            if diagnostics.canonical_dist(diagnostics.pip_install_name(n))
            not in diagnostics.NOT_OFFERED_FOR_INSTALL}


def unqueueable_reason(keys) -> str:
    """Why these keys can't be planned or queued, else None. The CUDA torch
    triple comes from a fixed index through its own GPU PyTorch setup; a plain
    `pip install torch` here would swap it for the CPU wheel."""
    known = installable_packages()
    for key in keys:
        if key not in known:
            return "Unknown or non-installable package."
    if any(k in diagnostics_torch.TORCH_FAMILY for k in keys) and shutil.which("nvidia-smi"):
        return ("PyTorch on an NVIDIA PC is set up under GPU PyTorch, not here, so it keeps "
                "its CUDA build.")
    return None


def install_dists(keys) -> list:
    return [diagnostics.pip_install_name(k) for k in keys]


def install_argv(keys, python: str = None, torch_pins_path: str = None,
                 project_root: str = None) -> list:
    """The literal `pip install` argv for these keys: the registry's dist
    names, constraints.txt's caps, and a pin file holding the installed torch
    family so a dependent package can't swap a CUDA torch for a CPU one."""
    argv = [python or sys.executable, "-m", "pip", "install", *diagnostics.PIP_INSTALL_FLAGS,
            *install_dists(keys), *diagnostics.constraints_pip_args(project_root or ROOT)]
    if torch_pins_path:
        argv += ["-c", torch_pins_path]
    return argv


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

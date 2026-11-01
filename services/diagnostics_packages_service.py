"""
services/diagnostics_packages_service.py -- the Diagnostics Packages
section: the pip install and upgrade of whitelisted dependencies, the
"Check for updates" PyPI lookup and its in-process cache, and the install
presets by task.

Every pip run goes through _run_commands (one process group per command,
whole tree killed on timeout) under the library's exclusive hold, so no job,
restore, reset, cleanup or second install can start mid-install. The GPU
PyTorch triple has its own flow in diagnostics_gpu_torch_service, which reuses
_run_commands.
"""

import importlib
import os
import re
import sys
import tempfile
import threading
import time
from typing import Any

import background_jobs
import job_process_kill
import dependency_updates
import diagnostics
import diagnostics_torch as gpu_torch
import install_registry
import pending_install
import upgrade_check
from lib.proc import stream_tree
from diagnostics_report import redact_for_support
from services.diagnostics_gaps_service import default_project_root
from services.diagnostics_admin_service import (
    AdminActionNotPossible, AdminActionStale, AdminActionUnknownPackage, _cancel_probe,
    _under_install_hold, guard,
)

_ADMIN_OUTPUT_TAIL = 40


PIP_TIMEOUT_SECONDS = upgrade_check.UPGRADE_CHECK_PIP_TIMEOUT       # 900 s


def installable_packages() -> set:
    return install_registry.installable_packages()


def _pip(command: str, *args) -> list:
    return [sys.executable, "-m", "pip", command, *diagnostics.PIP_INSTALL_FLAGS, *args]


def _install_commands(name: str) -> list:
    """(command, timeout) pairs for a plain install. torch, torchvision or
    torchaudio on an NVIDIA PC never reaches here: the install plan refuses
    them (install_registry.unqueueable_reason) for the GPU PyTorch setup."""
    # Fresh snapshot: the plan the owner confirmed may be minutes old.
    install = _pip("install", diagnostics.pip_install_name(name),
                   *dependency_updates.constraints_pip_args(default_project_root()))
    swap = install_registry.cv2_swap_names([name], pending_install.snapshot())
    if not swap:
        return [(install, PIP_TIMEOUT_SECONDS)]
    # The preview comes first: the uninstall always works, but putting the
    # package back after a failed install needs the index.
    return [(install_registry.preflight_argv(install), PIP_TIMEOUT_SECONDS),
            *[(install_registry.uninstall_argv(n), PIP_TIMEOUT_SECONDS) for n in swap],
            (install, PIP_TIMEOUT_SECONDS)]


_TORCH_CONSTRAINT_RE = re.compile(r"\(constraint\)\s+(torch|torchvision|torchaudio)==",
                                  re.IGNORECASE)


def torch_conflict_hint(lines, pins: list, changed: bool = False) -> str | None:
    """A plain-English refusal when pip stopped because the package needs a
    different torch than the pinned one (pip names the constraint in its
    "The conflict is caused by" block). pip resolves before it changes
    anything, so nothing was installed or removed, unless an earlier command of
    the same run (`changed`) already removed a package."""
    for line in lines:
        if _TORCH_CONSTRAINT_RE.search(line or ""):
            have = ", ".join(p.replace("==", " ") for p in pins)
            return ("This package needs a different PyTorch than the one installed "
                    f"({have}), so " + ("it was not installed" if changed else "nothing was changed")
                    + ": installing it would have replaced "
                    "your PyTorch (and a CUDA build with a CPU one). Leave it, or set up a "
                    "PyTorch version it supports under GPU PyTorch first.")
    return None


def _run_commands(cmds: list, torch_pins: list | None = None, job_id: str | None = None) -> dict:
    """Runs each (command, timeout) through stream_tree; ok only if every
    one exits 0 in time. Stops at the first failure. With a `job_id`,
    Cancel is honoured before each command and while one runs (the whole
    pip process tree is killed; raises JobCancelled), and each redacted
    output line becomes the job's message."""
    cancel = _cancel_probe(job_id)
    stream_kwargs: dict[str, Any] = {"warn": job_process_kill._warn_via_jobs}
    if cancel is not None:
        stream_kwargs["cancel"] = cancel
    tail, ok, hint, raw, removed = [], True, None, [], False
    for n, (cmd, timeout) in enumerate(cmds):
        if cancel is not None and cancel():
            raise background_jobs.JobCancelled(job_id)
        frac = 0.05 + 0.85 * n / len(cmds)
        for item in stream_tree(cmd, timeout, **stream_kwargs):
            if "line" in item:
                # Checked on the raw line: redaction rewrites the cache path.
                hint = hint or diagnostics.pip_cache_permission_hint([item["line"]])
                if torch_pins:
                    raw = (raw + [item["line"]])[-200:]
                shown = redact_for_support(item["line"])
                tail = (tail + [shown])[-_ADMIN_OUTPUT_TAIL:]
                if job_id is not None and shown.strip():
                    background_jobs.update_progress(job_id, frac, shown[:200])
            elif "returncode" in item:
                if item.get("cancelled"):
                    raise background_jobs.JobCancelled(job_id)
                ok = ok and item["returncode"] == 0 and not item.get("timed_out")
                removed = removed or (ok and cmd[3:4] == ["uninstall"])
                if item.get("timed_out"):
                    tail = (tail + ["(stopped: pip took too long)"])[-_ADMIN_OUTPUT_TAIL:]
        if not ok:
            break
    if not ok and torch_pins:
        hint = torch_conflict_hint(raw, torch_pins, changed=removed) or hint
    return {"ok": ok, "output_tail": tail, "hint": None if ok else hint}


def _write_torch_pins(pins: list) -> str:
    """A temporary constraints file holding `pins`; the caller removes it."""
    fd, path = tempfile.mkstemp(prefix="baihe-torch-pins-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(pins) + "\n")
    return path


def _run_pip(name: str, confirm, cmds_for, job_id: str | None = None) -> dict:
    """One whitelisted package's pip run under the install hold (a job holds
    it itself and was guarded when it started: pass its `job_id`). Unless the
    package is itself torch/torchvision/torchaudio, every command also gets
    a constraints file pinning the installed torch family exactly, so pip
    refuses (before changing anything) a package that needs another torch
    instead of replacing a CUDA torch with a CPU one or moving torchvision."""
    if job_id is None:
        guard(confirm)
    if name not in installable_packages():
        raise AdminActionUnknownPackage("Unknown or non-installable package.")

    def run():
        cmds = cmds_for(name)
        pins = [] if name in gpu_torch.TORCH_FAMILY else gpu_torch.torch_pin_lines()
        if not pins:
            return _run_commands(cmds, job_id=job_id)
        path = _write_torch_pins(pins)
        try:
            # Only an install reads a constraints file; `pip uninstall` has no -c.
            return _run_commands([(cmd + ["-c", path] if cmd[3] == "install" else cmd, t)
                                  for cmd, t in cmds],
                                 torch_pins=pins, job_id=job_id)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    result = run() if job_id is not None else _under_install_hold(run)
    result["package"] = name
    return result


def install_dependency(name: str, confirm: bool = False, job_id: str | None = None,
                       timeout: int | None = None) -> dict:
    """Runs the install and returns its result. `job_id`: running as that
    background job, which already holds the library and passed the start
    checks (see diagnostics_installs_service.start_dependency_install).
    `timeout` replaces the default pip allowance for a very large wheel."""
    def cmds(n):
        return [(cmd, timeout or limit) for cmd, limit in _install_commands(n)]
    try:
        return _run_pip(name, confirm, cmds, job_id=job_id)
    finally:
        _clear_update_cache()      # a new package can hold back (or allow) others


def upgrade_specs(name: str, target: str | None = None) -> list:
    """The pip requirements an update runs: the version the last "Check for
    updates" found allowed (pinned exactly); refuses when that check found no
    allowed update, or (409) when `target`, the version the user confirmed,
    isn't that check's target any more. Without a check (and no target),
    `--upgrade`. Its plan and its install both use this list, so the preview
    is of exactly what pip will run."""
    if name in gpu_torch.TORCH_FAMILY:
        raise AdminActionNotPossible(
            "torch, torchvision and torchaudio are upgraded together: use GPU PyTorch setup.")
    checked = cached_update(name)
    if checked is not None and target is None:
        raise AdminActionStale("Say which version to update to (the update check's target).")
    if target is not None and (checked is None or checked["status"] != "update"
                               or checked["target"] != target):
        raise AdminActionStale("The update check has changed since; check for updates again.")
    if checked is not None and checked["status"] != "update":
        raise AdminActionNotPossible(
            "The last update check found no update allowed for this package; check again.")
    if checked is not None:
        return [f"{checked['dist']}=={checked['target']}"]
    return ["--upgrade", diagnostics.pip_install_name(name)]


def _outdated_names(packages: dict, offered: set) -> list:
    """Installed packages below the app's minimum that the update route accepts
    (torch, torchvision and torchaudio move together through GPU PyTorch setup).
    One key per distribution: faster-whisper and faster_whisper are the same
    package."""
    out, seen = [], set()
    for n, p in sorted(packages.items()):
        dist = diagnostics.canonical_dist(diagnostics.pip_install_name(n))
        if (p["installed"] and p["below_min"] and n in offered and dist not in seen
                and n not in gpu_torch.TORCH_FAMILY):
            seen.add(dist)
            out.append(n)
    return out


def outdated_packages() -> list:
    offered = installable_packages()
    mins = dependency_updates.required_min_versions(default_project_root())
    return _outdated_names({n: _package_info(n, True, offered, mins)
                            for n in offered if _package_installed(n)}, offered)


def outdated_upgrade_specs() -> tuple:
    """(names, pip requirements) for updating the packages below the app's
    minimum in one pip run. Each is pinned to what dependency_updates.
    classify_update allows (constraints.txt, the installed packages that
    need it, known limitations), so a package whose newer releases are all
    held back is left out instead of being pulled past its cap by a bare
    `--upgrade`. Refuses (422) when nothing is below the minimum, or when
    every one is held back or PyPI can't be asked. The names come from the
    installed set, never from a request."""
    from concurrent.futures import ThreadPoolExecutor
    names = outdated_packages()
    if not names:
        raise AdminActionNotPossible("No installed package is older than the app needs.")
    installed = {n: dependency_updates.installed_dist_version(n) for n in names}
    dists = sorted({dist for dist, _v in installed.values()})
    with ThreadPoolExecutor(max_workers=UPDATE_CHECK_WORKERS) as pool:
        releases = dict(zip(dists, pool.map(dependency_updates.pypi_release_versions, dists)))
    constraints = dependency_updates.constraint_specifiers(default_project_root())
    required_by = dependency_updates.installed_requirements_on()
    keep, specs, held = [], [], []
    for n in names:
        dist, version = installed[n]
        info = dependency_updates.classify_update(n, version, releases.get(dist), constraints,
                                                  required_by)
        if info["status"] == "update":
            keep.append(n)
            specs.append(f"{dist}=={info['target']}")
        else:
            held.append(n)
    if not keep:
        raise AdminActionNotPossible(
            "No update the app can use is allowed for " + ", ".join(held)
            + " right now (held back, or PyPI could not be reached).")
    return keep, specs


def upgrade_dependency(name: str, specs: list, job_id: str | None = None) -> dict:
    """Runs the update as the install job `job_id`, which already holds the
    library and passed the start checks (see
    diagnostics_installs_service.start_dependency_upgrade). `specs` goes to
    pip verbatim, so it must come from upgrade_specs, never from a request."""
    def cmds(_n):
        args = [*specs, *dependency_updates.constraints_pip_args(default_project_root())]
        return [(_pip("install", *args), PIP_TIMEOUT_SECONDS)]
    try:
        return _run_pip(name, True, cmds, job_id=job_id)
    finally:
        # pip may have moved other packages too (a dependency pulled up or
        # capped), so no cached target is safe any more: the next Update
        # needs a new check, as after an install.
        _clear_update_cache()


# ---------------------------------------------------------------------------
# "Check for updates": PyPI, only on an explicit click (admin.diagnostics),
# cached in this process so the Upgrade route installs exactly the version
# the check showed. A repeat within UPDATE_CHECK_MIN_INTERVAL returns the
# cache instead of asking PyPI again.
# ---------------------------------------------------------------------------

UPDATE_CHECK_MIN_INTERVAL = 60


UPDATE_CHECK_WORKERS = 8


_UPDATES_LOCK = threading.Lock()


_UPDATES_FETCH = threading.Lock()      # one PyPI fan-out at a time


_UPDATES = {"checked_at": None, "packages": {}, "generation": 0}


def _clear_update_cache() -> None:
    """After an install, an upgrade or a PyTorch setup the installed set changed, so a
    cached target may no longer be safe: the next Update needs a new check.
    The generation bump stops a check that started before from storing its
    (now stale) answer."""
    # find_spec answers from finder caches taken before pip wrote the new files,
    # so a package installed just now would read as missing until a restart.
    importlib.invalidate_caches()
    with _UPDATES_LOCK:
        _UPDATES.update(checked_at=None, packages={},
                        generation=_UPDATES.get("generation", 0) + 1)
    gpu_torch.forget_gpu_status()


def cached_update(name: str):
    with _UPDATES_LOCK:
        entry = _UPDATES["packages"].get(name)
        return dict(entry) if entry else None


def check_package_updates(force: bool = False) -> dict | None:
    """For every installed package the Upgrade route accepts: installed
    version, newest PyPI release, and the newest one allowed by
    constraints.txt, the installed packages that depend on it and the
    known limitations (dependency_updates.classify_update). torch/torchvision/
    torchaudio are "managed" (GPU PyTorch setup), never an update here.
    One PyPI request per distribution, in parallel, each with a timeout."""
    def cached():
        with _UPDATES_LOCK:
            if _UPDATES["checked_at"] is None:
                return None
            return {"checked_at": _UPDATES["checked_at"],
                    "packages": {k: dict(v) for k, v in _UPDATES["packages"].items()}}
    with _UPDATES_LOCK:
        recent = (_UPDATES["checked_at"] is not None and
                  time.time() - _UPDATES["checked_at"] < UPDATE_CHECK_MIN_INTERVAL)
    if recent and not force:
        return cached()
    if not _UPDATES_FETCH.acquire(blocking=False):
        # Another check is asking PyPI right now: never a second fan-out.
        last = cached()
        if last is not None:
            return last
        raise AdminActionStale("An update check is already running; try again in a moment.")
    try:
        with _UPDATES_LOCK:      # one may have finished while we waited for the lock
            recent = (_UPDATES["checked_at"] is not None and
                      time.time() - _UPDATES["checked_at"] < UPDATE_CHECK_MIN_INTERVAL)
        if recent and not force:
            return cached()
        return _check_package_updates_now()
    finally:
        _UPDATES_FETCH.release()


def _check_package_updates_now() -> dict:
    from concurrent.futures import ThreadPoolExecutor
    with _UPDATES_LOCK:
        generation = _UPDATES.get("generation", 0)
    names = sorted(n for n in installable_packages() if _package_installed(n))
    installed = {n: dependency_updates.installed_dist_version(n) for n in names}
    to_fetch = sorted({dist for n, (dist, v) in installed.items()
                       if v and n not in gpu_torch.TORCH_FAMILY})
    with ThreadPoolExecutor(max_workers=UPDATE_CHECK_WORKERS) as pool:
        releases = dict(zip(to_fetch, pool.map(dependency_updates.pypi_release_versions, to_fetch)))
    constraints = dependency_updates.constraint_specifiers(default_project_root())
    required_by = dependency_updates.installed_requirements_on()
    packages = {}
    for n in names:
        dist, version = installed[n]
        if n in gpu_torch.TORCH_FAMILY:
            info = {"status": "managed", "latest": None, "target": None,
                    "reason": "set up together with torchvision/torchaudio under GPU PyTorch"}
        else:
            info = dependency_updates.classify_update(n, version, releases.get(dist), constraints,
                                               required_by)
        packages[n] = {"name": n, "dist": dist, "installed_version": version, **info}
        if packages[n]["reason"]:
            packages[n]["reason"] = redact_for_support(packages[n]["reason"])[:300]
    checked_at = time.time()
    with _UPDATES_LOCK:
        # An install finished mid-check: keep the cache empty rather than
        # store answers computed against the old set of packages.
        if _UPDATES.get("generation", 0) == generation:
            _UPDATES.update(checked_at=checked_at, packages=packages)
    return {"checked_at": checked_at, "packages": {k: dict(v) for k, v in packages.items()}}


def _package_installed(name: str) -> bool:
    dep = diagnostics.OPTIONAL_DEPENDENCIES.get(name)
    if dep:
        return bool(diagnostics.check_dependency(dep[0]))
    return diagnostics.get_installed_version(diagnostics.pip_install_name(name)) is not None


def _package_info(name: str, installed: bool, offered: set, mins: dict | None = None) -> dict:
    dist = diagnostics.pip_install_name(name)
    installed_version = dependency_updates.installed_dist_version(name)[1] if installed else None
    min_version = (mins or {}).get(diagnostics.canonical_dist(dist))
    dep = diagnostics.OPTIONAL_DEPENDENCIES.get(name)
    reason = diagnostics.NOT_OFFERED_FOR_INSTALL.get(diagnostics.canonical_dist(dist))
    # A Python-version limitation (e.g. audio-separator on 3.14) is a warning,
    # not a refusal: the install route still accepts it.
    limitation = None if reason else dependency_updates.known_install_limitation_reason(name)
    return {
        "name": name,
        "dist": dist,
        "installed": installed,
        "installed_version": installed_version,
        # The app's minimum (the requirements files' `>=`), and whether the
        # installed version is below it.
        "min_version": min_version,
        "below_min": diagnostics.below_min_version(installed_version, min_version),
        "installable": name in offered and not installed and reason is None,
        "powers": dep[1] if dep else "",
        "approx_mb": diagnostics.approx_download_mb(name),
        "pulls_torch": diagnostics.canonical_dist(dist) in diagnostics.PULLS_TORCH,
        "source_url": diagnostics.package_source_url(name),
        "not_offered_reason": reason,
        "warning": None if installed else limitation,
    }


def get_install_presets() -> dict:
    """{"tasks": [...], "packages": {name: info}} for the Packages section:
    install presets by task (diagnostics.INSTALL_TASKS), each package's
    role for the task (required / recommended / optional) and, for every
    package, its pip name, installed version, the app's minimum version,
    approx. download size, PyPI link, and any reason not to offer it or
    warning before installing it. "to_install" is what "Install for this
    task" installs: missing, offered, required or recommended packages;
    missing optional ones are listed in "optional_missing". Local checks
    only (import specs, installed metadata, requirements files), no network."""
    offered = installable_packages()
    mins = dependency_updates.required_min_versions(default_project_root())
    names = set(offered) | {n for t in diagnostics.INSTALL_TASKS for n in t["packages"]}
    names |= set(diagnostics.OPTIONAL_DEPENDENCIES)    # versions for required ones too
    packages = {n: _package_info(n, _package_installed(n), offered, mins) for n in sorted(names)}
    tasks = []
    outdated = _outdated_names(packages, offered)
    for t in diagnostics.INSTALL_TASKS:
        roles = {n: dependency_updates.task_package_role(t, n) for n in t["packages"]}
        rows = [packages[n] for n in t["packages"]]
        missing = [r for r in rows if not r["installed"]]
        to_install = [r for r in missing if r["installable"] and roles[r["name"]] != "optional"]
        tasks.append({
            "id": t["id"], "group": t["group"], "label": t["label"], "help": t["help"],
            "packages": list(t["packages"]),
            "roles": roles,
            "installed_count": len(rows) - len(missing),
            "required_missing": [r["name"] for r in missing if roles[r["name"]] == "required"],
            "to_install": [r["name"] for r in to_install],
            "optional_missing": [r["name"] for r in missing
                                 if r["installable"] and roles[r["name"]] == "optional"],
            "approx_mb": sum(r["approx_mb"] or 0 for r in to_install),
        })
    return {"tasks": tasks, "packages": packages, "outdated": outdated}

"""
install_plan.py -- what a Diagnostics install would change, and whether it is
safe to run inside the running server.

`pip install --dry-run --report` resolves the exact requirement list without
touching anything. From its report and the installed versions this works out
what is installed, upgraded or downgraded, refuses a plan that would put two
OpenCV flavours side by side, one that would leave a package the app needs
below its minimum, or one that would move PyTorch or pull it from PyPI onto an
NVIDIA PC, flags any other downgrade of something the app itself requires, and
decides whether any package pip would replace has compiled files this process
has loaded (Windows cannot replace those).
"""

import functools
import json
import os
import re
import shutil
import sys
import tempfile

import dependency_updates
import diagnostics
import diagnostics_report
import diagnostics_torch
import install_registry
import pending_install
from lib.proc import run_captured

PLAN_TIMEOUT_SECONDS = 300
COMPILED_SUFFIXES = (".pyd", ".so", ".dll", ".dylib")
CV2_FLAVOURS = ("opencv-python", "opencv-python-headless", "opencv-contrib-python",
                "opencv-contrib-python-headless")
_METADATA_DIRS = (".dist-info", ".egg-info", ".data")
UNCHECKED_RISK = ("Baihe couldn't check this install for clashes or downgrades of packages it "
                  "needs.")


def _canon(name: str) -> str:
    return diagnostics.canonical_dist(name)


def _vkey(version: str):
    version_mod, _s, _r = diagnostics._packaging()
    try:
        return version_mod.Version(version)
    except Exception:
        return None          # unknown order: neither an upgrade nor a downgrade


def parse_report(doc) -> list:
    """[(canonical name, version)] pip would install, from a pip report."""
    out = []
    for item in (doc or {}).get("install") or []:
        meta = (item or {}).get("metadata") or {}
        name, version = meta.get("name"), meta.get("version")
        if isinstance(name, str) and isinstance(version, str):
            out.append((_canon(name), version))
    return out


def app_required_names(project_root: str = None) -> set:
    """Canonical names of every package the requirements files list."""
    _v, _s, requirements = diagnostics._packaging()
    root = project_root or install_registry.ROOT
    names = set()
    for fname in dependency_updates.REQUIREMENTS_FILES:
        for spec in diagnostics.parse_requirements_file(os.path.join(root, fname)):
            try:
                names.add(_canon(requirements.Requirement(spec).name))
            except Exception:
                continue
    return names


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _top_levels(files) -> set:
    tops = set()
    for f in files:
        if not f.parts:
            continue
        part = f.parts[0]
        if part.endswith(_METADATA_DIRS) or part == "..":
            continue
        tops.add(re.sub(r"\.libs$", "", part.split(".")[0]) if len(f.parts) > 1
                 else part.split(".")[0])
    return tops


def dist_in_use(name: str, modules=None, lookup=None, norm=_norm) -> bool:
    """True when replacing this package in place could fail on a held file.

    A distribution that ships compiled files counts as in use when one of them
    is loaded, or when any of its top-level modules is imported. The second
    test is conservative on purpose: Windows keeps a loaded extension's helper
    DLLs open, and sys.modules doesn't list them, so an imported top level is
    the only sign they may be loaded. The cost is that upgrading such a
    package, numpy for one, while this process has it imported is queued as an
    install at restart. The benefit is that pip is never run in place against
    a locked file and left half-way. Pure-Python distributions hold nothing
    open and never count."""
    import importlib.metadata as md
    modules = sys.modules if modules is None else modules
    try:
        dist = (lookup or md.distribution)(name)
    except md.PackageNotFoundError:
        return False
    files = list(dist.files or [])
    compiled = [f for f in files if f.suffix.lower() in COMPILED_SUFFIXES or ".so." in f.name]
    if not compiled:
        return False
    loaded = set()
    for mod in list(modules.values()):
        path = getattr(mod, "__file__", None)
        if isinstance(path, str):
            loaded.add(norm(path))
    if any(norm(str(dist.locate_file(f))) in loaded for f in compiled):
        return True
    return any(top in modules for top in _top_levels(files))


def cv2_clash(installed: dict, changes: list):
    """A plain sentence when the result would hold two OpenCV flavours, else None."""
    new = sorted(c["name"] for c in changes if c["name"] in CV2_FLAVOURS and c["name"] not in installed)
    if not new:
        return None          # an upgrade in place, or two flavours the owner already had
    final = {n for n in CV2_FLAVOURS if n in installed} | set(new)
    if len(final) < 2:
        return None
    return ("This would put two OpenCV packages side by side (" + " and ".join(sorted(final))
            + "). They share one 'cv2' folder and break each other, so nothing was queued; "
            + ", ".join(new) + " comes in as a dependency. Remove the one you don't need first.")


def _classify(report: list, installed: dict) -> list:
    changes = []
    for name, version in report:
        have = installed.get(name)
        if have is None:
            kind = "install"
        else:
            new, old = _vkey(version), _vkey(have)
            if new is None or old is None:
                if version == have:
                    continue
                # pip's report lists only what it will install, so pip replaces
                # this even though neither direction can be told.
                kind = "change"
            elif new == old:
                continue
            else:
                kind = "upgrade" if new > old else "downgrade"
        changes.append({"name": name, "from": have, "to": version, "kind": kind})
    return changes


def _risks(changes: list, required: set, mins: dict) -> list:
    out = []
    for c in changes:
        if c["kind"] != "downgrade":
            continue
        if c["name"] in required or c["name"] in mins:
            out.append(f"{c['name']} {c['from']} -> {c['to']} is a downgrade of a package "
                       "Baihe itself needs.")
    return out


def _under_floor(change: dict, mins: dict) -> bool:
    if change["kind"] not in ("downgrade", "install") or change["name"] not in mins:
        return False
    new, floor = _vkey(change["to"]), _vkey(mins[change["name"]])
    return new is not None and floor is not None and new < floor


def _below_min(changes: list, mins: dict) -> list:
    """Downgrades that leave a package under the minimum the app's requirements ask for.
    Unlike other risks these are blocked: a package pinned below its floor by a dependency
    (PaddleX caps numpy under the 2.5.4 floor) breaks the app itself."""
    out = []
    for c in changes:
        if not _under_floor(c, mins):
            continue
        moved = (f"{c['name']} {c['from']} -> {c['to']}" if c["kind"] == "downgrade"
                 else f"Installing {c['name']} {c['to']}")
        out.append(f"{moved} would leave it older than the {mins[c['name']]} Baihe needs, "
                   "so nothing was changed.")
    return out


def _torch_blocks(keys: list, changes: list, nvidia: bool) -> list:
    """An install of anything but the torch family itself never moves PyTorch:
    the installed family is pinned, and on an NVIDIA PC a torch it would pull
    in fresh comes from PyPI (the CPU build on Windows) instead of the CUDA
    build GPU PyTorch installs for the card (cu126 for a V100)."""
    family = diagnostics_torch.TORCH_FAMILY
    if any(k in family for k in keys):
        return []
    out = []
    fresh = [f"{c['name']} {c['to']}" for c in changes
             if c["name"] in family and c["kind"] == "install"]
    if fresh and nvidia:
        out.append(f"This would install {', '.join(fresh)} from PyPI, not the build GPU "
                   "PyTorch picks for your NVIDIA GPU, so nothing was changed. Set up GPU "
                   "PyTorch first, then install this.")
    for c in changes:
        if c["name"] in family and c["kind"] != "install":
            out.append(f"This would replace {c['name']} {c['from']} with {c['to']}, so nothing "
                       "was changed. PyTorch is changed only under GPU PyTorch.")
    return out


def _summary(changes: list) -> list:
    new = [f"{c['name']} {c['to']}" for c in changes if c["kind"] == "install"]
    moved = [f"{c['name']} {c['from']} -> {c['to']}" + (" (downgrade)" if c["kind"] == "downgrade" else "")
             for c in changes if c["kind"] != "install"]
    lines = []
    if new:
        lines.append("Install " + ", ".join(new) + ".")
    if moved:
        lines.append("Change " + ", ".join(moved) + ".")
    return lines


def _swap_line(swap: list) -> str:
    return ("Remove " + ", ".join(swap) + " first: PaddleOCR needs opencv-contrib-python, and "
            "the two cannot share one 'cv2' folder. Baihe previews the install before it "
            "removes anything.")


CONFLICT = "conflict"
UNAVAILABLE = "unavailable"
_CAUSE_RE = re.compile(r"^\s*(The user requested \(constraint\)|The user requested"
                       r"|\S+ \S+ depends on)\s+(.+?)\s*$")
_NO_DIST_RE = re.compile(r"No matching distribution found for ([A-Za-z0-9._-]+)")
# The legacy resolver's wording, and the line the current one prints before "No matching
# distribution" when every release it skipped needs another Python.
_PYTHON_RE = re.compile(r"Package '([A-Za-z0-9._-]+)' requires a different Python: (\S+) "
                        r"not in '([^']{1,80})'")
_IGNORED_PYTHON_RE = re.compile(r"require a different python version: (.+)$", re.MULTILINE)
# pip reports an index it couldn't reach as "No matching distribution" too, after its
# retries, so any of these means the answer says nothing about the package.
_OFFLINE_MARKERS = ("Retrying (Retry(", "NewConnectionError",
                    "Failed to establish a new connection", "Temporary failure in name resolution", "getaddrinfo failed",
                    "Could not fetch URL", "ConnectTimeoutError", "ReadTimeoutError",
                    "Read timed out", "Max retries exceeded", "ProxyError", "SSLError",
                    "HTTPError", "too many 5")


def _conflict_causes(text: str) -> list:
    """pip's "The conflict is caused by" lines, reworded: the request and
    constraints are Baihe's own, the rest name the package that needs it."""
    out, inside = [], False
    for line in text.splitlines():
        if "The conflict is caused by" in line:
            inside = True
            continue
        if not inside:
            continue
        m = _CAUSE_RE.match(line)
        if not m:
            if out:
                break
            continue
        who, spec = m.groups()
        if who == "The user requested (constraint)":
            out.append(f"Baihe keeps {spec}")
        elif who == "The user requested":
            out.append(f"this install asks for {spec}")
        else:
            out.append(f"{who} {spec}")
    return list(dict.fromkeys(out))[:8]


def pip_failure(text: str):
    """(kind, reason) for a failed pip resolve. kind CONFLICT or UNAVAILABLE
    is certain to fail again, so the plan refuses it; None is a failure that a
    retry (online, at restart) may not repeat. reason is one sentence or None."""
    text = text or ""
    causes = _conflict_causes(text)
    if causes or "ResolutionImpossible" in text or "conflicting dependencies" in text:
        torch = [c for c in causes if c.startswith("Baihe keeps torch")]
        if torch:
            return CONFLICT, (torch[0].replace("Baihe keeps ", "It needs a different PyTorch than "
                                               "the one installed (") + "): installing it would "
                              "replace your PyTorch. Set up a PyTorch it supports under GPU "
                              "PyTorch first, or leave it.")
        return CONFLICT, ("pip's reason: " + "; ".join(causes) + ".") if causes else None
    if any(marker in text for marker in _OFFLINE_MARKERS):
        return None, "pip couldn't reach the package index (are you online?)."
    m = _PYTHON_RE.search(text)
    if m:
        return UNAVAILABLE, (f"{m.group(1)} needs Python {m.group(3)}, and Baihe runs on "
                             f"{m.group(2)}.")
    m = _NO_DIST_RE.search(text)
    if m:
        python = f"{sys.version_info[0]}.{sys.version_info[1]}"
        ignored = _IGNORED_PYTHON_RE.search(text)
        if ignored:
            newest = ignored.group(1).split(";")[-1].strip()[:80]
            return UNAVAILABLE, (f"pip found no release of {m.group(1)} that runs on Python "
                                 f"{python} (newest: {newest}).")
        return UNAVAILABLE, (f"pip found no release of {m.group(1)} for this Python "
                             f"({python}) and system.")
    return None, None


def explain_pip_failure(text: str):
    """pip_failure with its reason redacted for showing and storing."""
    kind, reason = pip_failure(text)
    return kind, (diagnostics_report.redact_for_support(reason)[:600] if reason else None)


def _dry_run(argv: list, runner):
    fd, report_path = tempfile.mkstemp(prefix="baihe-plan-", suffix=".json")
    os.close(fd)
    try:
        # Not --quiet: it hides pip's "The conflict is caused by" lines.
        argv = argv + ["--dry-run", "--report", report_path]
        try:
            proc = runner(argv, PLAN_TIMEOUT_SECONDS)
        except OSError:
            return None, "pip could not be run to preview the change."
        if getattr(proc, "cancelled", False):
            return None, "cancelled"
        if proc.timed_out:
            return None, "pip could not be run to preview the change."
        if proc.returncode is None:
            return None, "pip did not stop in time."
        if proc.returncode != 0:
            kind, reason = explain_pip_failure((proc.stdout or "") + (proc.stderr or ""))
            if kind:
                return None, kind + ("\n" + reason if reason else "")
            return None, reason or "pip could not work out the change (are you online?)."
        try:
            with open(report_path, encoding="utf-8") as f:
                return json.load(f), None
        except (OSError, ValueError):
            return None, "this pip cannot preview an install."
    finally:
        try:
            os.remove(report_path)
        except OSError:
            pass


def run_dry_run(keys: list, runner=run_captured, specs: list | None = None):
    """(report doc or None, why) from `pip install --dry-run --report`."""
    pins = install_registry.write_torch_pins(keys)
    try:
        return _dry_run(install_registry.install_argv(keys, torch_pins_path=pins, specs=specs),
                        runner)
    finally:
        if pins:
            try:
                os.remove(pins)
            except OSError:
                pass


def torch_setup_blocks(variant: str, runner=run_captured, installed: dict = None,
                       mins: dict = None, required: set = None, cancel=None) -> list:
    """Why GPU PyTorch setup of `variant` must not start, from a dry run of its
    dependency step: that step resolves against the PyTorch index, which holds
    its own (often older) copies of numpy, pillow and the like, so it is held to
    the same floors as any other install. The torch family itself may move
    either way: that is the point of the setup. A preview that couldn't run is
    no reason to stop: the setup's own pip run reports that failure."""
    args = diagnostics_torch.torch_setup_pip_args(variant, install_registry.ROOT)[1]
    argv = [sys.executable, "-m", "pip", "install", *diagnostics.PIP_INSTALL_FLAGS, *args]
    if cancel is not None:
        runner = functools.partial(runner, cancel=cancel)
    report, why = _dry_run(argv, runner)
    kind, _nl, detail = (why or "").partition("\n")
    if kind == CONFLICT:
        return [" ".join(filter(None, (
            "pip says this PyTorch can't be installed together with what you have, so nothing "
            "was changed.", detail)))]
    if kind == UNAVAILABLE:
        return [detail + " Nothing was changed."]
    if report is None:
        return []
    installed = pending_install.snapshot() if installed is None else installed
    changes = [c for c in _classify(parse_report(report), installed)
               if c["name"] not in diagnostics_torch.TORCH_FAMILY]
    mins = dependency_updates.required_min_versions() if mins is None else mins
    required = app_required_names() if required is None else required
    return _below_min(changes, mins) + [
        f"{c['name']} {c['from']} -> {c['to']} would downgrade a package Baihe itself needs, "
        "so nothing was changed." for c in changes
        if c["kind"] == "downgrade" and not _under_floor(c, mins)
        and (c["name"] in required or c["name"] in mins)]


def _is_windows() -> bool:
    return os.name == "nt"


def replaced_names(plan: dict) -> set:
    """The installed dists a plan's pip run would replace or remove."""
    return {c["name"] for c in plan["changes"] if c["kind"] != "install"} | set(plan["swap"])


def loaded_now(names, windows: bool = None, in_use=None) -> list:
    """Which of `names` Windows couldn't replace right now; none elsewhere."""
    windows = _is_windows() if windows is None else windows
    in_use = dist_in_use if in_use is None else in_use
    return sorted(n for n in names if windows and in_use(n))


def in_use_note(loaded: list, lead: str = "Baihe is using {} right now") -> str:
    return lead.format(", ".join(loaded)) + ", and Windows can't replace files in use."


def apply_in_use(plan: dict, jobs_running: bool, windows: bool = None, in_use=None) -> dict:
    """Sets `loaded`, `note` and `mode` of a previewed plan from what is in use
    right now. Also re-run on a cached plan, whose answer goes stale as soon as
    a job imports one of the packages it would replace."""
    if not plan["available"]:
        return plan     # no preview: its restart mode and note don't depend on what is in use
    windows = _is_windows() if windows is None else windows
    plan["loaded"] = loaded_now(replaced_names(plan), windows, in_use)
    plan["note"] = None
    plan["mode"] = "now"
    if plan["loaded"]:
        plan["note"] = in_use_note(plan["loaded"])
    elif windows and jobs_running:
        plan["note"] = "A job is running and may load these packages while pip works."
    if windows and (plan["loaded"] or jobs_running):
        plan["mode"] = "restart"
    return plan


def build_plan(keys: list, jobs_running: bool = False, windows: bool = None, report=None,
               installed: dict = None, in_use=dist_in_use, mins: dict = None,
               required: set = None, specs: list | None = None, nvidia: bool = None) -> dict:
    """The plan shown before the owner confirms, and the install-now/
    install-at-restart decision. `specs`: the pip requirements an update
    really runs (see install_registry.install_argv), so its preview is of
    the update and not of an install that would change nothing.

    mode "restart": a package pip would replace is in use here (Windows only),
    a job is running, or no preview was possible on Windows. blocked: reasons
    the plan can't run at all. needs_confirm: risks the owner must accept
    explicitly, including running now with no preview."""
    windows = (os.name == "nt") if windows is None else windows
    keys = list(keys)
    plan = {"keys": keys, "available": False, "mode": "now", "installs": [], "changes": [],
            "loaded": [], "blocked": [], "needs_confirm": [], "summary": [], "before": {},
            "swap": [], "note": None}
    reason = install_registry.unqueueable_reason(keys)
    if reason:
        plan["blocked"].append(reason)
        return plan
    why = None
    if report is None:
        report, why = run_dry_run(keys, specs=specs)
    kind, _nl, detail = (why or "").partition("\n")
    if kind == CONFLICT:
        plan["blocked"].append(" ".join(filter(None, (
            "pip says these packages can't be installed together with what you have, so "
            "nothing was changed.", detail))))
        return plan
    if kind == UNAVAILABLE:
        plan["blocked"].append(detail + " Nothing was changed.")
        return plan
    # Only a plain install runs the OpenCV swap (_install_commands); an update
    # runs `specs` alone, so its clash check must see the real installed set.
    def swap_for(installed_now):
        return [] if specs is not None else install_registry.cv2_swap_names(keys, installed_now)
    if report is None:
        swap = swap_for(pending_install.snapshot() if installed is None else installed)
        plan["swap"] = swap
        if swap:
            plan["summary"].append(_swap_line(swap))
        plan["note"] = ("Couldn't preview what will change: " + (why or "no preview.")
                        + (" It will install when you restart Baihe." if windows else ""))
        plan["mode"] = "restart" if windows else "now"
        if not windows:
            # Running now without a preview skips the clash and downgrade checks.
            plan["needs_confirm"].append(UNCHECKED_RISK)
        return plan
    installed = pending_install.snapshot() if installed is None else installed
    changes = _classify(parse_report(report), installed)
    plan.update(available=True, changes=changes, summary=_summary(changes),
                installs=[c for c in changes if c["kind"] == "install"])
    swap = swap_for(installed)
    plan["swap"] = swap
    if swap:
        plan["summary"].insert(0, _swap_line(swap))
    # The swap removes the plain flavour before the install, so the clash check
    # sees the environment as it will be then.
    clash = cv2_clash({n: v for n, v in installed.items() if n not in swap}, changes)
    if clash:
        plan["blocked"].append(clash)
    mins = dependency_updates.required_min_versions() if mins is None else mins
    required = app_required_names() if required is None else required
    plan["blocked"].extend(_below_min(changes, mins))
    nvidia = bool(shutil.which("nvidia-smi")) if nvidia is None else nvidia
    plan["blocked"].extend(_torch_blocks(keys, changes, nvidia))
    plan["needs_confirm"] = _risks(changes, required, mins)
    replaced = [c for c in changes if c["kind"] != "install"]
    plan["before"] = {**{n: installed[n] for n in swap}, **{c["name"]: c["from"] for c in replaced}}
    apply_in_use(plan, jobs_running, windows, in_use)
    return plan

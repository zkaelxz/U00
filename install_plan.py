"""
install_plan.py -- what a Diagnostics install would change, and whether it is
safe to run inside the running server.

`pip install --dry-run --report` resolves the exact requirement list without
touching anything. From its report and the installed versions this works out
what is installed, upgraded or downgraded, refuses a plan that would put two
OpenCV flavours side by side, flags a downgrade of something the app itself
requires, and decides whether any package pip would replace has compiled files
this process has loaded (Windows cannot replace those).
"""

import json
import os
import re
import sys
import tempfile

import diagnostics
import install_registry
import pending_install
from lib.proc import run_captured

PLAN_TIMEOUT_SECONDS = 300
COMPILED_SUFFIXES = (".pyd", ".so", ".dll", ".dylib")
CV2_FLAVOURS = ("opencv-python", "opencv-python-headless", "opencv-contrib-python",
                "opencv-contrib-python-headless")
_METADATA_DIRS = (".dist-info", ".egg-info", ".data")


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
    for fname in diagnostics.REQUIREMENTS_FILES:
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
    """True when this process has the package's compiled files loaded, or has
    imported a top-level module of a distribution that ships compiled files
    (its helper DLLs are then loaded too, though sys.modules doesn't list
    them). Pure-Python files aren't held open, so they never count."""
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
            if new is None or old is None or new == old:
                continue
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


def run_dry_run(keys: list, runner=run_captured):
    """(report doc or None, why) from `pip install --dry-run --report`."""
    fd, report_path = tempfile.mkstemp(prefix="baihe-plan-", suffix=".json")
    os.close(fd)
    pins = install_registry.write_torch_pins(keys)
    try:
        argv = install_registry.install_argv(keys, torch_pins_path=pins)
        argv += ["--dry-run", "--quiet", "--report", report_path]
        try:
            proc = runner(argv, PLAN_TIMEOUT_SECONDS)
        except OSError:
            return None, "pip could not be run to preview the change."
        if proc.timed_out:
            return None, "pip could not be run to preview the change."
        if proc.returncode is None:
            return None, "pip did not stop in time."
        if proc.returncode != 0:
            text = (proc.stdout or "") + (proc.stderr or "")
            if "conflict" in text.lower() or "ResolutionImpossible" in text:
                return None, "conflict"
            return None, "pip could not work out the change (are you online?)."
        try:
            with open(report_path, encoding="utf-8") as f:
                return json.load(f), None
        except (OSError, ValueError):
            return None, "this pip cannot preview an install."
    finally:
        for path in (report_path, pins):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass


def build_plan(keys: list, jobs_running: bool = False, windows: bool = None, report=None,
               installed: dict = None, in_use=dist_in_use, mins: dict = None,
               required: set = None) -> dict:
    """The plan shown before the owner confirms, and the install-now/
    install-at-restart decision.

    mode "restart": a package pip would replace is in use here (Windows only),
    a job is running, or no preview was possible on Windows. blocked: reasons
    the plan can't run at all. needs_confirm: risks the owner must accept
    explicitly."""
    windows = (os.name == "nt") if windows is None else windows
    keys = list(keys)
    plan = {"keys": keys, "available": False, "mode": "now", "installs": [], "changes": [],
            "loaded": [], "blocked": [], "needs_confirm": [], "summary": [], "before": {},
            "note": None}
    reason = install_registry.unqueueable_reason(keys)
    if reason:
        plan["blocked"].append(reason)
        return plan
    why = None
    if report is None:
        report, why = run_dry_run(keys)
    if why == "conflict":
        plan["blocked"].append("pip says these packages can't be installed together with what "
                               "you have, so nothing was changed.")
        return plan
    if report is None:
        plan["note"] = ("Couldn't preview what will change: " + (why or "no preview.")
                        + (" It will install when you restart Baihe." if windows else ""))
        plan["mode"] = "restart" if windows else "now"
        return plan
    installed = pending_install.snapshot() if installed is None else installed
    changes = _classify(parse_report(report), installed)
    plan.update(available=True, changes=changes, summary=_summary(changes),
                installs=[c for c in changes if c["kind"] == "install"])
    clash = cv2_clash(installed, changes)
    if clash:
        plan["blocked"].append(clash)
    mins = diagnostics.required_min_versions() if mins is None else mins
    required = app_required_names() if required is None else required
    plan["needs_confirm"] = _risks(changes, required, mins)
    replaced = [c for c in changes if c["kind"] != "install"]
    plan["before"] = {c["name"]: c["from"] for c in replaced}
    plan["loaded"] = sorted(c["name"] for c in replaced if windows and in_use(c["name"]))
    if plan["loaded"]:
        plan["note"] = ("Baihe is using " + ", ".join(plan["loaded"]) + " right now, and "
                        "Windows can't replace files in use.")
    elif windows and jobs_running:
        plan["note"] = "A job is running and may load these packages while pip works."
    if windows and (plan["loaded"] or jobs_running):
        plan["mode"] = "restart"
    return plan

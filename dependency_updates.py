"""
dependency_updates.py -- installed versions against PyPI: the "is there an
update I may install?" classification, the constraints.txt and requirement
minimums it respects, and the known reasons an upgrade or install can't work.
"""

import importlib.metadata
import json
import os
import re
import sys

import diagnostics

# ---------------------------------------------------------------------------
# "Is this dependency outdated?" + an Upgrade action. Like
# check_pyannote_gated_access above, this reaches the network (PyPI's own
# public JSON API, a plain unauthenticated GET) -- so it must only ever run
# from an explicit button click, never automatically on page load, and the
# caller must cache the result rather than re-querying on every request.
# ---------------------------------------------------------------------------


def get_latest_pypi_version(pip_name: str, timeout: float = 10.0):
    """The latest version PyPI's public JSON API reports for `pip_name`, or
    None on any failure (network error, non-200 for a name PyPI doesn't
    recognize, unexpected JSON shape) -- never raises, since one
    dependency's lookup failing shouldn't break the whole check. Makes a
    real network call every time it's called; callers gate this behind an
    explicit button and cache the result (see check_dependency_versions)."""
    try:
        from lib import http
        resp = http.get(f"https://pypi.org/pypi/{pip_name}/json", timeout=timeout,
                        max_bytes=PYPI_JSON_MAX_BYTES, guard=None)
        if resp.status != 200:
            return None
        return (json.loads(resp.body).get("info") or {}).get("version") or None
    except Exception:
        return None


def check_dependency_versions(deps: dict, timeout: float = 10.0) -> dict:
    """For every dependency in `deps` (as returned by
    check_all_dependencies()) that's actually installed, looks up its
    latest PyPI version and compares it to the installed one. Returns
    {name: {"installed_version", "latest_version", "outdated"}} --
    "outdated" is None (not guessed) when either version couldn't be
    determined, True/False otherwise. Makes one real PyPI request per
    installed dependency -- call this only from an explicit button click,
    never automatically."""
    results = {}
    for name, info in deps.items():
        # "experimental" entries aren't on PyPI: a lookup by their name could
        # hit an unrelated package registered under it.
        if not info.get("installed") or info.get("tier") == "experimental":
            continue
        installed_version = diagnostics.get_installed_version(diagnostics.pip_install_name(name))
        latest_version = get_latest_pypi_version(diagnostics.pip_install_name(name), timeout=timeout)
        outdated = None
        if installed_version and latest_version:
            outdated = diagnostics._version_sort_key(installed_version) < diagnostics._version_sort_key(latest_version)
        results[name] = {
            "installed_version": installed_version,
            "latest_version": latest_version,
            "outdated": outdated,
        }
    return results


def constraints_pip_args(project_root: str = None) -> list:
    """`["-c", <constraints.txt>]` when the file exists, else []. Every pip
    install that resolves dependencies passes this so a transitive pull
    can't cross a cap (e.g. av 19 breaking faster-whisper). Portable and
    installer layouts may ship without the file, so absence is not an error."""
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    constraints_path = os.path.join(project_root, "constraints.txt")
    return ["-c", constraints_path] if os.path.exists(constraints_path) else []


def upgrade_pip_args(pip_name: str, project_root: str = None) -> list:
    """pip args for `python -m pip install --upgrade <pip_name>`, adding
    constraints.txt's existing version caps (pyannote.audio<5,
    transformers<6, torch<3, faster-whisper<2, ...) via pip's
    own `-c` flag whenever the file exists -- the same mechanism
    the GPU PyTorch setup already uses for torch/torchaudio,
    generalized here since an Upgrade click can just as easily target any
    of constraints.txt's other pinned packages (e.g. transformers). A
    constraint for a package not named in the file is a no-op, so passing
    it unconditionally is always safe."""
    return ["--upgrade", pip_name, *constraints_pip_args(project_root)]


# ---------------------------------------------------------------------------
# When an "Upgrade" action can't actually reach the latest release for a
# real, known reason (a constraints.txt cap, or a package with no published
# wheel for the running Python version), say so instead of silently
# offering an upgrade that would fail, or offering nothing with no
# explanation. Only confirmed cases go here, not hypothetical ones.
# ---------------------------------------------------------------------------

KNOWN_UPGRADE_LIMITATIONS = {
    "audio-separator": {
        "python_version": (3, 14),
        "reason": "its diffq-fixed sub-dependency has wheels only through cp313, and its "
                  "sdist build also fails independently; Demucs, this app's "
                  "default vocal-separation backend, is unaffected.",
    },
    # Reproduced for real -- with huggingface_hub 2.0.0 installed
    # next to transformers 5.17.0, `import transformers` raises
    # "ImportError: huggingface-hub>=1.5.0,<2.0 is required ... but found
    # huggingface-hub==2.0.0", taking Scanlate's ML bubble
    # detector and the speech models down with it. This app's mocked test suite never
    # imports the real transformers, so only pip's own conflict report
    # caught it. Applies only while the installed transformers still
    # declares that cap, so it lifts itself once a transformers release
    # accepts huggingface_hub 2.x: transformers 5.15.0 declares
    # huggingface-hub<2.0,>=1.5 and 5.19.0 declares <3.0,>=1.31 (wheel METADATA).
    "huggingface-hub": {
        "blocked_from": 2,
        "while_required_below_by": "transformers",
        "reason": "the installed transformers (Scanlate's ML bubble detector, "
                  "speech models) requires huggingface_hub below 2.0 and refuses to import "
                  "with 2.x -- upgrade transformers (5.19 or newer accepts it) first.",
    },
}


def _constraints_cap(pip_name: str, project_root: str = None):
    """The raw constraint line (e.g. "torch<3") capping `pip_name` in
    constraints.txt, or None if it isn't capped there. Matches on the
    package name before the operator, normalizing "_"/"-" the same way
    pip itself treats them as equivalent."""
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(project_root, "constraints.txt")
    if not os.path.exists(path):
        return None
    target = pip_name.replace("_", "-").lower()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.match(r"([A-Za-z0-9_.\-]+)\s*<\s*([0-9]+)", line)
            if m and m.group(1).replace("_", "-").lower() == target:
                return line, int(m.group(2))
    return None


def _known_python_version_limitation(pip_name: str):
    """The KNOWN_UPGRADE_LIMITATIONS entry for `pip_name`, if one exists AND
    this process is actually running the affected Python version -- shared
    by upgrade_blocked_reason (an installed package that can't go further)
    and known_install_limitation_reason (the same package failing
    to install in the first place, same root cause, different moment)."""
    known = KNOWN_UPGRADE_LIMITATIONS.get(pip_name.replace("_", "-").lower())
    if known and "python_version" in known and sys.version_info[:2] == known["python_version"]:
        return known
    return None


def _declared_major_cap(requirer: str, pip_name: str):
    """The major version `requirer`'s installed metadata caps `pip_name`
    below (e.g. 2 for "huggingface-hub<2.0,>=1.5.0"), or None if it isn't
    installed or declares no such cap."""
    try:
        requirements = importlib.metadata.requires(requirer) or []
    except importlib.metadata.PackageNotFoundError:
        return None
    target = re.sub(r"[-_.]+", "-", pip_name).lower()
    for req in requirements:
        m = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*\(?([^;]*)", req)
        if not m or re.sub(r"[-_.]+", "-", m.group(1)).lower() != target:
            continue
        cap = re.search(r"<\s*([0-9]+)", m.group(2))
        if cap:
            return int(cap.group(1))
    return None


def _known_dependent_limitation(pip_name: str, latest_version: str):
    """The KNOWN_UPGRADE_LIMITATIONS entry for `pip_name` if it's a
    "blocked from version N while <requirer> caps it" entry that applies
    right now: `latest_version` reaches N and the installed requirer still
    declares a cap at or below N."""
    known = KNOWN_UPGRADE_LIMITATIONS.get(pip_name.replace("_", "-").lower())
    if not known or "blocked_from" not in known or not latest_version:
        return None
    if diagnostics._version_sort_key(latest_version)[:1] < [known["blocked_from"]]:
        return None
    cap = _declared_major_cap(known["while_required_below_by"], pip_name)
    if cap is not None and cap <= known["blocked_from"]:
        return known
    return None


def known_install_limitation_reason(pip_name: str) -> str:
    """None, or a short, plain-English reason `pip_name` is known to fail
    to install at all on this Python version -- shown next to a
    "not installed" row before the user ever clicks Install, and again if
    they click it anyway and it fails, so a raw pip/Cython traceback is
    never the only signal. Also covers NOT_OFFERED_FOR_INSTALL (a package
    known not to work with this app at all, on any Python)."""
    not_offered = diagnostics.NOT_OFFERED_FOR_INSTALL.get(diagnostics.canonical_dist(diagnostics.pip_install_name(pip_name)))
    if not_offered:
        return not_offered
    known = _known_python_version_limitation(pip_name)
    if not known:
        return None
    py = ".".join(str(p) for p in known["python_version"])
    return f"known not to install on Python {py} -- {known['reason']}"


def upgrade_blocked_reason(pip_name: str, latest_version: str = None,
                           project_root: str = None) -> str:
    """None if a normal "Upgrade" should be offered for `pip_name`.
    Otherwise a short, plain-English reason the row should show INSTEAD
    of the button, so a known-doomed upgrade never just looks like a real
    option with no explanation."""
    known = _known_python_version_limitation(pip_name)
    if known:
        py = ".".join(str(p) for p in known["python_version"])
        return f"latest available for Python {py} -- {known['reason']}"
    known = _known_dependent_limitation(pip_name, latest_version)
    if known:
        return f"{latest_version} is known to break this app -- {known['reason']}"
    cap = _constraints_cap(pip_name, project_root)
    if cap and latest_version and diagnostics._version_sort_key(latest_version)[:1] >= [cap[1]]:
        return f"capped at `{cap[0]}` in constraints.txt (see its own comment for why)"
    return None


# ---------------------------------------------------------------------------
# Installed versions and an honest "is there an update I may install?"
# (Diagnostics > Packages). The PyPI read is a network call: callers run it
# only from an explicit "Check for updates" click and cache the result. The
# URL is built from the static dist name only (pypi_url's pattern).
# ---------------------------------------------------------------------------

# Other distributions that provide the same import (key -> dists), tried
# when the main one isn't installed, so a headless OpenCV still shows its
# version.
PIP_DIST_ALTERNATES = {
    "cv2": ("opencv-python-headless", "opencv-contrib-python",
            "opencv-contrib-python-headless"),
}

PYPI_JSON_TIMEOUT = 10.0
PYPI_JSON_MAX_BYTES = 20 * 1024 * 1024   # the largest project JSON is a few MB
_DIST_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def installed_dist_version(name: str):
    """(dist, version) actually installed for a package key: its PyPI dist
    (pip_install_name), else a known alternate dist; (dist, None) if none."""
    dist = diagnostics.pip_install_name(name)
    for candidate in (dist, *PIP_DIST_ALTERNATES.get(name, ())):
        version = diagnostics.get_installed_version(candidate)
        if version:
            return candidate, version
    return dist, None


def pypi_release_versions(dist: str, timeout: float = PYPI_JSON_TIMEOUT):
    """Final (non-pre-release), non-yanked releases PyPI lists for `dist`
    that have at least one file, as version strings; None on any failure.
    One GET to https://pypi.org/pypi/<dist>/json, with a timeout, no
    redirects and at most PYPI_JSON_MAX_BYTES read."""
    if not _DIST_NAME_RE.fullmatch(dist or ""):
        return None
    version_mod, _s, _r = diagnostics._packaging()
    try:
        from lib import http
        resp = http.get(f"https://pypi.org/pypi/{diagnostics.canonical_dist(dist)}/json", timeout=timeout,
                        headers={"Accept": "application/json"}, max_bytes=PYPI_JSON_MAX_BYTES,
                        guard=None)
        if resp.status != 200:
            return None
        releases = json.loads(resp.body).get("releases") or {}
    except Exception:
        return None
    out = []
    for text, files in releases.items():
        if not isinstance(files, list) or not files or all(f.get("yanked") for f in files):
            continue
        try:
            v = version_mod.Version(text)
        except Exception:
            continue
        if not (v.is_prerelease or v.is_devrelease):
            out.append(text)
    return out


def constraint_specifiers(project_root: str = None) -> dict:
    """{canonical dist: (SpecifierSet, raw line)} from constraints.txt."""
    _v, specifiers, requirements = diagnostics._packaging()
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(project_root, "constraints.txt")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                req = requirements.Requirement(line)
            except Exception:
                continue
            out[diagnostics.canonical_dist(req.name)] = (req.specifier, line)
    return out


# Installed packages whose declared requirements never hold another package
# back: qwen-asr pins transformers==4.57.6, and the app no longer uses it
# (the startup warning tells the user to uninstall it).
IGNORED_REQUIRERS = {"qwen-asr"}


def installed_requirements_on() -> dict:
    """{canonical dist: [(requirer dist, SpecifierSet)]} from every
    installed distribution's own requirements (markers evaluated for this
    interpreter, extras off): what an upgrade must keep satisfied so it
    doesn't break a package that's already installed."""
    _v, _s, requirements = diagnostics._packaging()
    out = {}
    for d in importlib.metadata.distributions():
        requirer = (d.metadata or {}).get("Name")
        if not requirer:
            continue
        if diagnostics.canonical_dist(requirer) in IGNORED_REQUIRERS:
            continue
        for text in d.requires or []:
            try:
                req = requirements.Requirement(text)
                if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                    continue
            except Exception:
                continue
            if str(req.specifier):
                out.setdefault(diagnostics.canonical_dist(req.name), []).append(
                    (requirer, req.specifier))
    return out


def classify_update(name: str, installed_version: str, releases, constraints: dict,
                    required_by: dict) -> dict:
    """{"status", "latest", "target", "reason"} for one installed package:
    "update" (target = newest release allowed by constraints.txt, the
    installed packages that depend on it and the known limitations),
    "held_back" (a newer release exists but none of the newer ones is
    allowed; reason says by what), "up_to_date", or "unknown" (PyPI didn't
    answer, or the version can't be read). Offline: releases come from
    pypi_release_versions."""
    version_mod, _s, _r = diagnostics._packaging()
    dist = diagnostics.canonical_dist(diagnostics.pip_install_name(name))
    empty = {"latest": None, "target": None, "reason": None}
    if not installed_version or not releases:
        return {"status": "unknown", **empty}
    try:
        have = version_mod.Version(installed_version)
    except Exception:
        return {"status": "unknown", **empty}
    versions = set()
    for r in releases:
        try:
            versions.add(version_mod.Version(r))
        except Exception:
            continue          # never a pip argument: only str(Version) is used below
    if not versions:
        return {"status": "unknown", **empty}
    versions = sorted(versions)
    latest = versions[-1]
    # Compare on the public version: 2.11.0+cu128 is not "older" than 2.11.0.
    newer = [v for v in versions if v > version_mod.Version(have.public)]
    if not newer:
        return {"status": "up_to_date", "latest": str(latest), "target": None, "reason": None}

    reasons = []
    known = _known_python_version_limitation(dist)
    if known:
        py = ".".join(str(p) for p in known["python_version"])
        return {"status": "held_back", "latest": str(latest), "target": None,
                "reason": f"no newer release installs on Python {py} -- {known['reason']}"}
    allowed = newer
    spec = constraints.get(dist)
    if spec:
        kept = [v for v in allowed if spec[0].contains(v, prereleases=True)]
        if len(kept) < len(allowed):
            reasons.append(f"constraints.txt ({spec[1]})")
        allowed = kept
    for requirer, spec_set in required_by.get(dist, []):
        if diagnostics.canonical_dist(requirer) == dist:
            continue
        kept = [v for v in allowed if spec_set.contains(v, prereleases=True)]
        if len(kept) < len(allowed):
            reasons.append(f"{requirer} (needs {name} {spec_set})")
        allowed = kept
    kept = [v for v in allowed if not _known_dependent_limitation(dist, str(v))]
    if len(kept) < len(allowed):
        reasons.append(KNOWN_UPGRADE_LIMITATIONS[dist]["reason"])
    allowed = kept
    reason = ("held back by " + "; ".join(reasons)) if reasons else None
    if allowed:
        return {"status": "update", "latest": str(latest), "target": str(allowed[-1]),
                "reason": reason if allowed[-1] != latest else None}
    return {"status": "held_back", "latest": str(latest), "target": None, "reason": reason}


def task_package_role(task: dict, name: str) -> str:
    """"required", "recommended" or "optional" for a package in an
    INSTALL_TASKS entry (unlisted packages are required)."""
    for role in ("optional", "recommended"):
        if name in task.get(role, ()):
            return role
    return "required"


# The requirements files whose `name>=X` lines are the app's minimums.
REQUIREMENTS_FILES = ("requirements-core.txt", "requirements-media.txt",
                      "requirements-optional.txt")


def required_min_versions(project_root: str = None) -> dict:
    """{canonical dist: minimum version} from the requirements files'
    active `>=` lines (commented-out lines are skipped, as pip would)."""
    _v, _s, requirements = diagnostics._packaging()
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    out = {}
    for fname in REQUIREMENTS_FILES:
        for spec in diagnostics.parse_requirements_file(os.path.join(project_root, fname)):
            try:
                req = requirements.Requirement(spec)
            except Exception:
                continue
            for s in req.specifier:
                if s.operator == ">=":
                    out[diagnostics.canonical_dist(req.name)] = s.version
    return out

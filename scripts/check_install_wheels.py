"""scripts/check_install_wheels.py -- does every exact optional-install pin have a wheel the shipped Python can use?

The installer ships 64-bit Windows CPython 3.13, and an app install runs with --only-binary-style
resolution, so a pin whose newest-at-pin-time wheels skip cp313 fails on the owner's PC with a
misleading "conflict". This asks PyPI's JSON API (no downloads) for each `==` pin the app installs
by name (install_registry) and fails when none of the files fits win_amd64 + cp313.

    python scripts/check_install_wheels.py [--python 3.13]

Exit 0 when every pin has a fitting wheel, 1 with the list of pins that don't, 2 when PyPI
could not be reached (a CI retry, not a finding).
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement
from packaging.tags import Tag
from packaging.utils import parse_wheel_filename

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def exact_pins(python_version: str) -> list:
    """The `name==version` strings the app installs by name, for one Python."""
    import install_registry

    pins = list(install_registry._PINS.values())
    ort_gpu_pin = getattr(install_registry, "ort_gpu_pin", None)
    if ort_gpu_pin:
        # A per-Python pin replaces the static one.
        pins = [p for p in pins if not p.startswith("onnxruntime-gpu")] + [ort_gpu_pin(python_version)]
    for names in install_registry.PADDLE_REQUIREMENTS.values():
        pins += [n for n in names if "==" in n]
    return sorted(set(pins))


def wheel_fits(filename: str, python_version: str) -> bool:
    """True when a wheel file name has a tag for win_amd64 on this CPython."""
    try:
        _, _, _, tags = parse_wheel_filename(filename)
    except Exception:
        return False
    py = "cp" + python_version.replace(".", "")
    wanted = set()
    for platform in ("win_amd64", "any"):
        wanted |= {Tag(py, py, platform), Tag(py, "none", platform), Tag("py3", "none", platform)}
    for tag in tags:
        if tag in wanted:
            return True
        # abi3 wheels built for an older CPython run on later ones.
        if tag.abi == "abi3" and tag.platform == "win_amd64" and tag.interpreter.startswith("cp"):
            try:
                if int(tag.interpreter[2:]) <= int(py[2:]):
                    return True
            except ValueError:
                pass
    return False


def files_for(pin: str) -> list:
    req = Requirement(pin)
    version = next(iter(req.specifier)).version
    url = f"https://pypi.org/pypi/{req.name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (fixed https host)
        data = json.load(response)
    return [f["filename"] for f in data.get("urls", []) if not f.get("yanked")]


def check(pins, python_version, fetch=files_for) -> list:
    """The pins with no fitting wheel."""
    return [pin for pin in pins if not any(wheel_fits(f, python_version) for f in fetch(pin))]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default="3.13")
    args = parser.parse_args(argv)
    pins = exact_pins(args.python)
    try:
        bad = check(pins, args.python)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"could not reach PyPI: {type(exc).__name__}")
        return 2
    for pin in bad:
        print(f"no win_amd64 cp{args.python.replace('.', '')} wheel for {pin}")
    print(f"{len(pins)} pins checked, {len(bad)} without a wheel")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

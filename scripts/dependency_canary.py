"""
scripts/dependency_canary.py -- does upgrading ONE package break the app?

Builds a throwaway venv in a temp dir, installs requirements-core.txt (plus
requirements-optional.txt with --with-optional) under constraints.txt, then
upgrades just the named package and runs the offline test suite. Your real
environment and library are never touched, and no API keys reach the child
processes.

    python scripts/dependency_canary.py requests
    python scripts/dependency_canary.py pandas 3.0.0 --quick
    python scripts/dependency_canary.py transformers latest --write-pin

Exit code: 0 PASS, 1 FAIL, 2 ERROR (setup/timeout), 3 PREEXISTING (the same
tests fail on the known-good version too, so the package isn't to blame).

--write-pin appends `pkg<FAILING_VERSION` to constraints.txt (the last
known-good version stays allowed) only when the failing version is newer than
the known-good one. It never edits requirements files or
installer/wheels.lock.txt: refresh the installer lock separately (see
docs/windows-installer-design.md). Pinning your real environment back is a
manual step; the pip command is printed.

Standard library only.
"""

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
VERSION_RE = re.compile(r"^[A-Za-z0-9.+!_-]+$")

VENV_TIMEOUT = 300
INSTALL_TIMEOUT = 3600
FULL_TEST_TIMEOUT = 3600
QUICK_TEST_TIMEOUT = 900
TAIL_LINES = 40
MAX_RERUN_IDS = 50

PASS, FAIL, ERROR, PREEXISTING = "PASS", "FAIL", "ERROR", "PREEXISTING"
EXIT_CODES = {PASS: 0, FAIL: 1, ERROR: 2, PREEXISTING: 3}

# Only these variables are passed to child processes; nothing else (API keys,
# tokens, proxies with credentials) is inherited.
ENV_ALLOWLIST = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "WINDIR",
                 "LANG", "LC_ALL", "TZ", "LD_LIBRARY_PATH")

_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:hf|ghp|gho|github_pat|xox[a-z])_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|authorization)\s*[=:]\s*)\S{8,}"),
    re.compile(r"(?i)(://[^/\s:@]+:)[^@\s]+(@)"),
)


class CanaryError(Exception):
    pass


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        if pat.groups:
            text = pat.sub(lambda m: m.group(1) + "***" + (m.group(2) if pat.groups > 1 else ""), text)
        else:
            text = pat.sub("***", text)
    return text


def validate_name(name: str) -> str:
    if not name or not NAME_RE.match(name) or name.startswith("-"):
        raise CanaryError("Package name may only contain letters, digits, '.', '_' and '-'.")
    return name


def validate_target(target: str) -> str:
    if target != "latest" and not VERSION_RE.match(target or ""):
        raise CanaryError("Target must be 'latest' or a plain version like 2.1.0.")
    return target


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def version_key(version: str):
    """A comparable tuple of the leading numeric release segments, or None."""
    m = re.match(r"^(?:\d+!)?(\d+(?:\.\d+)*)", version or "")
    if not m:
        return None
    parts = [int(p) for p in m.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(candidate: str, known_good: str) -> bool:
    a, b = version_key(candidate), version_key(known_good)
    return a is not None and b is not None and a > b


def compute_pin(package: str, failing_version: str) -> str:
    """The upper-bound requirement: strictly below the failing version."""
    return f"{package}<{failing_version}"


def constraint_line(package, failing_version, first_failure, today=None):
    today = today or datetime.date.today().isoformat()
    what = f" broke {first_failure}" if first_failure else " broke the test suite"
    text = f"{compute_pin(package, failing_version):<22}# canary {today}: {failing_version}{what}"
    return " ".join(text.split("\n"))[:300]


def write_pin(constraints_path, package, failing_version, first_failure, today=None) -> str:
    """Appends the pin; returns 'written', or 'exists' / 'bounded' when the
    file already caps this package (nothing changes; comments are kept)."""
    path = Path(constraints_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    wanted = normalize(package)
    for raw in text.splitlines():
        code = raw.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9._-]+)\s*(.*)$", code)
        if not m or normalize(m.group(1)) != wanted:
            continue
        spec = m.group(2).replace(" ", "")
        if f"<{failing_version}" in spec.split(","):
            return "exists"
        if "<" in spec:
            return "bounded"
    if text and not text.endswith("\n"):
        text += "\n"
    text += constraint_line(package, failing_version, first_failure, today) + "\n"
    path.write_text(text, encoding="utf-8")
    return "written"


def parse_failures(output: str) -> list:
    """Node ids from pytest's `FAILED x::y` / `ERROR x` summary lines."""
    seen = []
    for line in output.splitlines():
        m = re.match(r"^(FAILED|ERROR)\s+(\S+)", line)
        if m and m.group(2) not in seen:
            seen.append(m.group(2))
    return seen


def parse_verdict(returncode: int, output: str):
    """('PASS'|'FAIL'|'ERROR', failures). pytest exits 0 on pass, 1 on test
    failures; anything else (usage error, no tests, timeout) is not a verdict
    about the package."""
    failures = parse_failures(output)
    if returncode == 0:
        return PASS, []
    if returncode == 1 and failures:
        return FAIL, failures
    return ERROR, failures


def quick_test_files(tests_dir, package: str) -> list:
    """test_static_analysis.py plus tests whose file name or imports mention
    the package, as paths relative to the repo (posix style)."""
    tests_dir = Path(tests_dir)
    module = package.replace("-", "_")
    name_bits = {module.lower(), module.lower().replace(".", "_")}
    import_re = re.compile(r"^\s*(?:import|from)\s+" + re.escape(module) + r"(?![A-Za-z0-9_])",
                           re.MULTILINE | re.IGNORECASE)
    chosen = []
    for f in sorted(tests_dir.glob("test_*.py")):
        stem = f.stem.lower()
        hit = f.name == "test_static_analysis.py" or any(b in stem for b in name_bits)
        if not hit:
            try:
                hit = bool(import_re.search(f.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                hit = False
        if hit:
            chosen.append(f"tests/{f.name}")
    return chosen


def clean_env(data_dir: str) -> dict:
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    env.update(BAIHE_DATA_DIR=data_dir, HOME=data_dir, USERPROFILE=data_dir,
               APPDATA=data_dir, LOCALAPPDATA=data_dir, TEMP=data_dir, TMP=data_dir,
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1",
               PIP_NO_INPUT="1")
    return env


def venv_python(venv_dir) -> str:
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return str(Path(venv_dir, *sub))


def _run(cmd, timeout, env, cwd=None):
    """(returncode, output). A timeout returns 124 with what was captured."""
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
        return 124, out + f"\n[timed out after {timeout}s]"
    except OSError as e:
        return 127, str(e)


def _pip(py, *args):
    return [py, "-m", "pip", *args]


def installed_version(py, package, env):
    code = "import importlib.metadata as m,sys;print(m.version(sys.argv[1]))"
    rc, out = _run([py, "-c", code, package], 60, env)
    return out.strip().splitlines()[-1] if rc == 0 and out.strip() else None


def _pytest_cmd(py, files=None, full=False):
    cmd = [py, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", "-o", "addopts="]
    if full:
        cmd += ["-n", "auto"]
    return cmd + list(files or [])


def run_canary(package, target, quick, with_optional, root=REPO_ROOT, log=print):
    """Returns a result dict: verdict, known_good, version, failures, tail, reason."""
    root = Path(root)
    result = dict(verdict=ERROR, known_good=None, version=None, failures=[], tail="", reason="")
    tmp = tempfile.mkdtemp(prefix="baihe-canary-")
    try:
        venv_dir, data_dir = os.path.join(tmp, "venv"), os.path.join(tmp, "data")
        os.makedirs(data_dir)
        env = clean_env(data_dir)
        log("Creating throwaway venv (your real environment is not touched)...")
        rc, out = _run([sys.executable, "-m", "venv", venv_dir], VENV_TIMEOUT, env)
        if rc != 0:
            result["reason"], result["tail"] = "couldn't create the venv", out
            return result
        py = venv_python(venv_dir)
        constraints = str(root / "constraints.txt")
        install = _pip(py, "install", "-r", str(root / "requirements-core.txt"))
        if with_optional:
            install += ["-r", str(root / "requirements-optional.txt")]
        install += ["pytest", "pytest-xdist", "httpx", "-c", constraints]
        log("Installing the project's requirements with constraints.txt...")
        rc, out = _run(install, INSTALL_TIMEOUT, env, cwd=str(root))
        if rc != 0:
            result["reason"], result["tail"] = "installing the project requirements failed", out
            return result
        result["known_good"] = installed_version(py, package, env)
        log(f"Known-good {package}: {result['known_good'] or 'not installed'}")
        spec = package if target == "latest" else f"{package}=={target}"
        log(f"Upgrading {package} to {target}...")
        rc, out = _run(_pip(py, "install", "--upgrade", spec, "-c", constraints),
                       INSTALL_TIMEOUT, env, cwd=str(root))
        if rc != 0:
            result["reason"], result["tail"] = f"pip couldn't install {spec}", out
            return result
        result["version"] = installed_version(py, package, env)
        log(f"Now testing {package} {result['version']}...")
        if quick:
            files = quick_test_files(root / "tests", package)
            log("Quick subset: " + ", ".join(files))
            rc, out = _run(_pytest_cmd(py, files), QUICK_TEST_TIMEOUT, env, cwd=str(root))
        else:
            rc, out = _run(_pytest_cmd(py, full=True), FULL_TEST_TIMEOUT, env, cwd=str(root))
        verdict, failures = parse_verdict(rc, out)
        result.update(verdict=verdict, failures=failures, tail=out)
        if verdict == ERROR:
            result["reason"] = "the test run didn't finish cleanly"
        if verdict == FAIL and result["known_good"] and result["version"] != result["known_good"]:
            log("Re-running the failed tests on the known-good version...")
            rc, _ = _run(_pip(py, "install", f"{package}=={result['known_good']}", "-c", constraints),
                         INSTALL_TIMEOUT, env, cwd=str(root))
            if rc == 0:
                ids = failures[:MAX_RERUN_IDS]
                rc, out2 = _run(_pytest_cmd(py, ids), QUICK_TEST_TIMEOUT, env, cwd=str(root))
                if rc != 0 and set(parse_failures(out2)) >= set(ids):
                    result["verdict"] = PREEXISTING
                    result["reason"] = "the same tests also fail on the known-good version"
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def report(result, package, write_pin_flag, constraints_path, out=print):
    v = result["verdict"]
    tail = "\n".join(redact(result["tail"]).splitlines()[-TAIL_LINES:])
    out(f"\n===== {v}: {package} {result['version'] or ''} (known-good {result['known_good'] or '?'}) =====")
    if result["reason"]:
        out(result["reason"])
    if result["failures"]:
        out("Failing tests:")
        for f in result["failures"][:MAX_RERUN_IDS]:
            out("  " + redact(f))
    if tail:
        out("--- output tail ---\n" + tail)
    if v != FAIL:
        return
    good, bad = result["known_good"], result["version"]
    if write_pin_flag:
        if not (good and bad and is_newer(bad, good)):
            out("Not writing a pin: the failing version isn't newer than the known-good one.")
        else:
            first = result["failures"][0] if result["failures"] else ""
            state = write_pin(constraints_path, package, bad, first)
            out({"written": f"Appended {compute_pin(package, bad)} to constraints.txt.",
                 "exists": "constraints.txt already has this pin; unchanged.",
                 "bounded": "constraints.txt already caps this package; edit it by hand."}[state])
        out("The installer lock (installer/wheels.lock.txt) is NOT changed: refresh it "
            "separately (docs/windows-installer-design.md).")
    if good:
        out("Manual step, if you already upgraded your real environment, to go back:")
        out(f'  pip install "{package}=={good}"')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Test one package upgrade against the offline suite.")
    ap.add_argument("package")
    ap.add_argument("target", nargs="?", default="latest", help="version or 'latest' (default)")
    ap.add_argument("--quick", action="store_true",
                    help="static analysis + tests that mention the package")
    ap.add_argument("--with-optional", action="store_true", help="also install requirements-optional.txt")
    ap.add_argument("--write-pin", action="store_true", help="on FAIL, append an upper bound to constraints.txt")
    args = ap.parse_args(argv)
    try:
        validate_name(args.package)
        validate_target(args.target)
    except CanaryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    result = run_canary(args.package, args.target, args.quick, args.with_optional)
    report(result, args.package, args.write_pin, REPO_ROOT / "constraints.txt")
    return EXIT_CODES[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())

"""
upgrade_check.py -- "if I upgrade this package, will it break the app?",
answered by trying it in a throwaway venv and running this app's tests there
before the real environment is touched.
"""

import os
import re
import shutil
import sys
import tempfile

import diagnostics
import job_process_kill
from lib import proc as proc_run

# ---------------------------------------------------------------------------
# "If I upgrade this, will it break the app?" -- answered by
# actually trying it, not by guessing from version numbers: install the
# candidate into a throwaway venv that otherwise sees this environment's
# own packages, run this app's own test suite there, and re-run anything
# that failed in a second throwaway venv WITHOUT the candidate, so a test
# that already fails today isn't blamed on the upgrade. The real
# environment is never modified -- pip refuses to uninstall anything that
# lives outside the throwaway venv's own prefix, and the candidate lands
# only inside it. Four outcomes, never a guess: "safe" (no new failures,
# no conflicts), "broken" (named tests that pass today fail with the
# candidate), "conflict" (tests pass, but pip reports an installed package
# that declares it won't work with the candidate), or "incomplete"
# (couldn't finish -- no network, no disk space, a timeout).
# No result at all means "untested", never "safe".
# ---------------------------------------------------------------------------

UPGRADE_CHECK_PIP_TIMEOUT = 900
UPGRADE_CHECK_TEST_TIMEOUT = 1800


def _env_package_dirs() -> list:
    """This process's own site-packages/dist-packages directories -- what a
    throwaway venv's .pth file points back at, so it sees exactly the
    packages this app is really running with."""
    return [p for p in sys.path if p and os.path.isdir(p)
            and os.path.basename(os.path.normpath(p)) in ("site-packages", "dist-packages")]


def _venv_python(venv_dir: str) -> str:
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _make_throwaway_venv(base_dir: str, name: str, python_executable: str, parent_dirs: list,
                         cancel=None):
    """(venv_python, None) on success, (None, reason) otherwise. No pip
    inside it -- installs go through the real pip's own --python flag, so
    this works even where ensurepip isn't available."""
    venv_dir = os.path.join(base_dir, name)
    try:
        proc = proc_run.run_captured(
            [python_executable, "-m", "venv", "--without-pip", venv_dir], 300, cancel=cancel)
        if proc.timed_out or proc.cancelled:
            return None, "cancelled" if proc.cancelled else "creating it took too long"
        if proc.returncode != 0:
            return None, (proc.stderr or proc.stdout).strip() or f"exit code {proc.returncode}"
        venv_py = _venv_python(venv_dir)
        purelib = proc_run.run_captured(
            [venv_py, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            60, cancel=cancel).stdout.strip()
        os.makedirs(purelib, exist_ok=True)
        with open(os.path.join(purelib, "_baihe_parent_env.pth"), "w", encoding="utf-8") as f:
            f.write("\n".join(parent_dirs) + "\n")
        return venv_py, None
    except OSError as exc:
        return None, str(exc)


def _stream_process(cmd: list, timeout: float, cwd: str = None, env: dict = None, cancel=None):
    return proc_run.stream_tree(cmd, timeout, cwd=cwd, env=env, cancel=cancel,
                                warn=job_process_kill._warn_via_jobs)


def _parse_pytest_failures(lines: list) -> list:
    """Node ids from pytest's -rfE short summary ("FAILED path::test - msg",
    "ERROR path - msg" for a collection error), in order, de-duplicated."""
    ids = []
    for line in lines:
        for prefix in ("FAILED ", "ERROR "):
            if line.startswith(prefix):
                node_id = line[len(prefix):].split(" - ", 1)[0].strip()
                if node_id and node_id not in ids:
                    ids.append(node_id)
    return ids


_PIP_CONFLICT_RE = re.compile(
    r"^(\S+) (\S+) requires (.+), but you have (\S+) (\S+) which is incompatible\.?$")


def _parse_pip_conflicts(lines: list) -> list:
    """pip's own post-install "X requires Y, but you have Z which is
    incompatible" lines, keeping only the ones this install caused -- a
    newly installed distribution (from pip's "Successfully installed ..."
    line) on either side. A conflict that already existed in the
    environment before (e.g. an unrelated package pinning numpy) isn't the
    candidate's fault and isn't reported."""
    installed = set()
    for line in lines:
        if line.startswith("Successfully installed "):
            for token in line[len("Successfully installed "):].split():
                installed.add(diagnostics.canonical_dist(token.rsplit("-", 1)[0]))
    conflicts = []
    for line in lines:
        m = _PIP_CONFLICT_RE.match(line.strip())
        if not m:
            continue
        if diagnostics.canonical_dist(m.group(1)) in installed or diagnostics.canonical_dist(m.group(4)) in installed:
            if line.strip() not in conflicts:
                conflicts.append(line.strip())
    return conflicts


def _dist_version_in(venv_py: str, pip_name: str, cancel=None):
    try:
        out = proc_run.run_captured(
            [venv_py, "-c", "import importlib.metadata, sys; "
                            "print(importlib.metadata.version(sys.argv[1]))", pip_name],
            60, cancel=cancel)
    except OSError:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _ensure_pytest(venv_py: str, python_executable: str, timeout: float, cancel=None):
    """Yields {"line"} items, then {"ok"}. The throwaway environment only sees
    the real one's packages, and pytest is an optional install there, so it
    is added to the throwaway environment itself when it can't be imported."""
    has = _can_import(venv_py, "pytest", cancel)
    if not has:
        yield {"line": "pytest isn't installed in your environment; adding it to the throwaway one..."}
        end = None
        for item in _stream_process([python_executable, "-m", "pip", "--python", venv_py,
                                     "install", "pytest>=7.4"], timeout, cancel=cancel):
            if "line" in item:
                yield item
            else:
                end = item
        if end["returncode"] != 0 or end["timed_out"]:
            yield {"ok": False}
            return
    # pytest-xdist only makes the run faster, so a failed install is not an error.
    if not _can_import(venv_py, "xdist", cancel):
        yield {"line": "Adding pytest-xdist so the tests can run on every CPU core..."}
        for item in _stream_process([python_executable, "-m", "pip", "--python", venv_py,
                                     "install", "pytest-xdist"], timeout, cancel=cancel):
            if "line" in item:
                yield item
    yield {"ok": True}


def _can_import(venv_py: str, module: str, cancel=None) -> bool:
    try:
        return proc_run.run_captured([venv_py, "-c", f"import {module}"], 60,
                                            cancel=cancel).returncode == 0
    except OSError:
        return False


def _flag_conflicts(result: dict) -> dict:
    """A "safe" test result that pip itself reports conflicts for becomes
    "conflict" -- tests passing doesn't outweigh an installed package
    declaring it won't work with the candidate."""
    if result["verdict"] == "safe" and result["conflicts"]:
        n = len(result["conflicts"])
        result.update(ok=False, verdict="conflict",
                      reason=f"{result['reason']}, but pip reports {n} installed package(s) "
                             f"that declare they don't support it -- this app's tests don't "
                             f"load those libraries for real, so they can't rule this out")
    return result


def check_upgrade_candidate(pip_name: str, version: str = None, project_root: str = None,
                            test_args: list = None, python_executable: str = None,
                            parent_dirs: list = None, pip_extra_args: list = None,
                            pip_timeout: float = UPGRADE_CHECK_PIP_TIMEOUT,
                            test_timeout: float = UPGRADE_CHECK_TEST_TIMEOUT, cancel=None):
    """Yields {"line"} as it goes, then a final {"done": True, "ok",
    "verdict", "reason", "version", "new_failures", "preexisting_failures",
    "conflicts"} -- "ok" is True only for verdict "safe". "conflicts" is
    pip's own "X requires Y, but you have Z" report for conflicts this
    install caused; a passing test run with conflicts reads "conflict",
    not "safe", since this app's mocked tests never import real ML
    libraries (huggingface_hub 2.0 breaking `import transformers` passed
    every test and was caught only this way). `version` pins the candidate
    (Diagnostics passes the latest release the real Upgrade would install);
    constraints.txt's caps apply exactly as they would to that real Upgrade.
    Runs this app's whole test suite by default, so it takes minutes, not
    seconds. `test_args`/`parent_dirs`/`pip_extra_args` exist so a test can
    point this at a tiny offline suite and local wheels. `cancel` (a
    callable) kills the running pip/pytest tree as soon as it turns true;
    the generator then ends without a final item (the caller knows why)."""
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    python_executable = python_executable or sys.executable
    parent_dirs = _env_package_dirs() if parent_dirs is None else list(parent_dirs)
    test_args = list(test_args or [os.path.join(project_root, "tests")])
    spec = f"{pip_name}=={version}" if version else pip_name
    pip_args = ["--upgrade", spec] + diagnostics.upgrade_pip_args(pip_name, project_root)[2:] + list(pip_extra_args or [])
    test_env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    result = {"done": True, "ok": False, "verdict": "incomplete", "reason": "",
              "version": version, "new_failures": [], "preexisting_failures": [],
              "conflicts": []}

    def _pytest(venv_py, args):
        cmd = [venv_py, "-m", "pytest", "-o", "addopts=", "-q", "-rfE", "-p", "no:cacheprovider"]
        if args == test_args and _can_import(venv_py, "xdist", cancel):
            cmd += ["-n", "auto"]
        cmd += args
        return _stream_process(cmd, test_timeout, cwd=project_root, env=test_env, cancel=cancel)

    work = tempfile.mkdtemp(prefix="baihe_upgrade_check_")
    try:
        yield {"line": "Creating a throwaway environment -- your real install isn't touched."}
        trial_py, err = _make_throwaway_venv(work, "trial", python_executable, parent_dirs, cancel)
        if err:
            if cancel is not None and cancel():
                return
            result["reason"] = f"couldn't create a throwaway environment: {err}"
            yield result
            return

        yield {"line": f"Installing {spec} into it..."}
        pip_lines, end = [], None
        for item in _stream_process([python_executable, "-m", "pip", "--python", trial_py,
                                     "install"] + pip_args, pip_timeout, cancel=cancel):
            if "line" in item:
                pip_lines.append(item["line"])
                yield item
            else:
                end = item
        if end.get("cancelled"):
            return
        if end["timed_out"]:
            result["reason"] = f"installing {spec} took longer than {int(pip_timeout // 60)} minutes"
            yield result
            return
        if end["returncode"] != 0:
            if any("No space left on device" in line for line in pip_lines):
                result["reason"] = "not enough disk space for the throwaway environment"
            else:
                result["reason"] = (f"{spec} couldn't be installed (no network, no such version, or "
                                    f"a build failure -- see the output above)")
            yield result
            return
        installed = _dist_version_in(trial_py, pip_name, cancel)
        if not installed:
            result["reason"] = f"{spec} reported success but isn't importable afterward"
            yield result
            return
        result["version"] = installed
        result["conflicts"] = _parse_pip_conflicts(pip_lines)

        pytest_ok = True
        for item in _ensure_pytest(trial_py, python_executable, pip_timeout, cancel):
            if "line" in item:
                yield item
            else:
                pytest_ok = item["ok"]
        if cancel is not None and cancel():
            return
        if not pytest_ok:
            result["reason"] = "pytest couldn't be added to the throwaway environment (no network?)"
            yield result
            return

        yield {"line": f"Running this app's test suite against {pip_name} {installed}..."}
        test_lines, end = [], None
        for item in _pytest(trial_py, test_args):
            if "line" in item:
                test_lines.append(item["line"])
                yield item
            else:
                end = item
        failures = _parse_pytest_failures(test_lines)
        if end.get("cancelled"):
            return
        if end["timed_out"]:
            result["reason"] = f"the test suite took longer than {int(test_timeout // 60)} minutes"
            yield result
            return
        if end["returncode"] == 0:
            result.update(ok=True, verdict="safe",
                          reason=f"every test passed against {pip_name} {installed}")
            yield _flag_conflicts(result)
            return
        if end["returncode"] not in (1, 2) or not failures:
            result["reason"] = (f"the test run itself didn't complete (pytest exit code "
                                f"{end['returncode']}) -- see the output above")
            yield result
            return

        yield {"line": f"{len(failures)} test(s) failed -- re-running them without {pip_name} "
                       f"{installed} to see which already fail on the current version..."}
        base_py, err = _make_throwaway_venv(work, "baseline", python_executable, parent_dirs, cancel)
        if err:
            if cancel is not None and cancel():
                return
            result["reason"] = (f"{len(failures)} test(s) failed, but a comparison environment "
                                f"couldn't be created to rule out already-failing ones: {err}")
            result["new_failures"] = failures
            yield result
            return
        pytest_ok = True
        for item in _ensure_pytest(base_py, python_executable, pip_timeout, cancel):
            if "line" in item:
                yield item
            else:
                pytest_ok = item["ok"]
        if not pytest_ok:
            result["reason"] = "pytest couldn't be added to the throwaway environment (no network?)"
            result["new_failures"] = failures
            yield result
            return

        base_lines, end = [], None
        for item in _pytest(base_py, failures):
            if "line" in item:
                base_lines.append(item["line"])
                yield item
            else:
                end = item
        if end.get("cancelled"):
            return
        if end["timed_out"] or end["returncode"] not in (0, 1, 2):
            result["reason"] = (f"{len(failures)} test(s) failed, but re-running them on the "
                                f"current version didn't complete -- see the output above")
            result["new_failures"] = failures
            yield result
            return
        baseline = set(_parse_pytest_failures(base_lines))
        new = [f for f in failures if f not in baseline]
        pre = [f for f in failures if f in baseline]
        result.update(new_failures=new, preexisting_failures=pre)
        if new:
            result.update(verdict="broken",
                          reason=f"{len(new)} test(s) that pass on the current version fail "
                                 f"against {pip_name} {installed}")
        else:
            result.update(ok=True, verdict="safe",
                          reason=f"no new failures against {pip_name} {installed} ({len(pre)} "
                                 f"test(s) already fail on the current version too)")
        yield _flag_conflicts(result)
    finally:
        shutil.rmtree(work, ignore_errors=True)

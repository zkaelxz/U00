"""The red-main alert workflow and the Android APK workflow's PR build. The alert's step runs for real with a
fake `gh` on PATH; the Android checks read the YAML."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _on(wf):
    return wf.get("on", wf.get(True))   # PyYAML reads a bare `on:` key as True


# --- red-main alert -----------------------------------------------------------------

FAKE_GH = """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$GH_LOG"
case "$1 $2" in
  "issue list") printf '%s\\n' "$GH_OPEN" ;;
  "issue create"|"issue comment"|"issue close") ;;
  *) exit 1 ;;
esac
"""


def _alert(tmp_path, conclusion, open_issue="", event="push"):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("needs bash")
    wf = _load("main-red-alert.yml")
    assert _on(wf)["workflow_run"] == {"workflows": ["Tests"], "types": ["completed"], "branches": ["baihe-subtitler"]}
    assert wf["permissions"] == {"issues": "write"}
    job = wf["jobs"]["report"]
    assert not any("uses" in s for s in job["steps"])   # nothing from the repository runs with the issues token
    (step,) = job["steps"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    log = tmp_path / "gh.log"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "GH_LOG": str(log),
           "GH_OPEN": open_issue, "GITHUB_REPOSITORY": "o/r", "CONCLUSION": conclusion,
           "RUN_URL": "https://github.com/o/r/actions/runs/1", "SHA": "abcdef1234567890", "EVENT": event}
    proc = subprocess.run([bash, "-c", step["run"]], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return [line for line in log.read_text(encoding="utf-8").splitlines() if not line.startswith("issue list")]


def test_a_red_run_opens_the_issue_with_its_link(tmp_path):
    (call,) = _alert(tmp_path, "failure")
    assert call.startswith("issue create --repo o/r --title Tests are red on baihe-subtitler")
    assert "abcdef1" in call and "https://github.com/o/r/actions/runs/1" in call


def test_a_later_red_run_comments_on_the_open_issue(tmp_path):
    (call,) = _alert(tmp_path, "timed_out", open_issue="42", event="schedule")
    assert call.startswith("issue comment 42 --repo o/r") and "schedule run" in call


def test_a_green_run_closes_the_open_issue(tmp_path):
    (call,) = _alert(tmp_path, "success", open_issue="42")
    assert call.startswith("issue close 42 --repo o/r") and "Green again" in call


@pytest.mark.parametrize("conclusion,open_issue", [("success", ""), ("cancelled", ""), ("cancelled", "42")])
def test_nothing_is_written_otherwise(tmp_path, conclusion, open_issue):
    assert _alert(tmp_path, conclusion, open_issue) == []


def test_only_push_and_nightly_runs_are_reported():
    cond = _load("main-red-alert.yml")["jobs"]["report"]["if"]
    assert "'push'" in cond and "'schedule'" in cond and "pull_request" not in cond


# --- Android APK on PRs ----------------------------------------------------------------

def test_an_android_change_builds_the_apk_on_its_pr():
    wf = _load("android-apk.yml")
    assert "android/**" in _on(wf)["pull_request"]["paths"]
    assert "if" not in wf["jobs"]["apk"]


def test_a_pr_build_never_reads_the_signing_key_or_publishes():
    wf = _load("android-apk.yml")
    steps = wf["jobs"]["apk"]["steps"]
    (key,) = [s for s in steps if "ANDROID_DEBUG_KEYSTORE_BASE64" in str(s.get("env", ""))]
    assert key["if"] == "github.event_name == 'workflow_dispatch'"
    staged = [s for s in steps if "baihe-android-publish" in str(s) or s.get("name") == "Stage the files the server fetches"]
    assert staged and all(s["if"] == "steps.key.outputs.publish == 'true'" for s in staged)
    assert wf["jobs"]["publish"]["if"].startswith("github.event_name == 'workflow_dispatch'")

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


# --- phone smoke test before a phone build is published or a release promoted ----------

def test_the_apk_is_published_only_after_the_emulator_smoke_test_passes():
    jobs = _load("android-apk.yml")["jobs"]
    smoke = jobs["smoke"]
    assert smoke["name"] == "Phone smoke test"  # Promote Release looks the job up by this name
    assert smoke["needs"] == "apk"
    assert "if" not in smoke  # a PR that changes android/ gets it too
    assert set(jobs["publish"]["needs"]) == {"apk", "smoke"}
    steps = [str(s) for s in smoke["steps"]]
    assert any("name': 'baihe-android-release-apk'" in s for s in steps)
    assert any("android/smoke/run_smoke.py --apk apk/app-release.apk" in s for s in steps)
    assert not any("secrets." in s for s in steps)


def test_promoting_needs_a_passing_phone_smoke_test_at_the_tags_commit():
    jobs = _load("promote-release.yml")["jobs"]
    assert "phone-check" in jobs["promote"]["needs"]
    check = jobs["phone-check"]
    assert all("uses" not in s for s in check["steps"])  # nothing from the repository runs
    assert check["permissions"] == {"contents": "read", "actions": "read"}
    (step,) = check["steps"]
    for needle in ("android-apk.yml/runs", "baihe-subtitler", "Phone smoke test", "Nothing was promoted"):
        assert needle in step["run"], needle


# --- dependency audit before a release is promoted ----------------------------------

def test_promoting_a_release_waits_for_an_audit_of_the_tags_code():
    promote = _load("promote-release.yml")["jobs"]
    assert "audit" in promote["promote"]["needs"]
    audit = promote["audit"]
    assert audit["uses"] == "./.github/workflows/dependency-audit-code.yml"
    assert audit["with"]["ref"] == "refs/tags/${{ inputs.tag }}"


# --- a recent release check before a release is promoted ---------------------------

def _release_check_step():
    wf = _load("promote-release.yml")
    assert _on(wf)["workflow_dispatch"]["inputs"]["release_check"]["required"] is True
    jobs = wf["jobs"]
    assert "release-check" in jobs["promote"]["needs"]
    (step,) = jobs["release-check"]["steps"]
    assert "uses" not in step  # nothing from the repository runs
    return step


def _run_release_check(stamp):
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("needs bash and GNU date (the job runs on ubuntu)")
    step = _release_check_step()
    env = {**os.environ, **{k: v for k, v in step["env"].items() if k != "STAMP"}, "STAMP": stamp}
    return subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True, timeout=30)


def test_promoting_needs_a_release_check_from_the_last_seven_days():
    import datetime

    today = datetime.datetime.now(datetime.timezone.utc).date()
    for days, ok in ((0, True), (7, True), (-1, True), (8, False), (-2, False)):
        stamp = (today - datetime.timedelta(days=days)).strftime("%Y%m%d") + "-101500"
        proc = _run_release_check(stamp)
        assert (proc.returncode == 0) is ok, (days, proc.stdout)
    for bad in ("", "20261124", "20261340-101500", "20261124-101500; echo hi"):
        proc = _run_release_check(bad)
        assert proc.returncode != 0 and "Nothing was promoted" in proc.stdout, bad


def test_the_audit_checks_out_the_given_commit_with_a_read_only_token():
    code = _load("dependency-audit-code.yml")
    assert "workflow_call" in _on(code)
    assert code["permissions"] == {"contents": "read"}
    for name, job in code["jobs"].items():
        assert "permissions" not in job, name
        checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
        assert [c["with"]["ref"] for c in checkouts] == ["${{ inputs.ref }}"], name
    assert set(code["jobs"]) == {"python", "npm", "go"}


def test_the_weekly_audit_still_runs_the_same_checks():
    weekly = _load("dependency-audit.yml")
    assert weekly["jobs"]["code"]["uses"] == "./.github/workflows/dependency-audit-code.yml"
    assert "schedule" in _on(weekly)

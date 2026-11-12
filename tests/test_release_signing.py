"""
lib/release_signature.py, tools/release_signing.py, the release job's
signing step in .github/workflows/windows-installer.yml and the promote step
in .github/workflows/promote-release.yml. Keys are throwaway ones made here;
the workflow steps run for real with a fake `gh` on PATH.
"""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from lib import release_signature as rs  # noqa: E402
from tools import release_signing as tool  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NAME = "BaiheStudio-Setup-0.2.0.exe"
SHA = hashlib.sha256(b"installer").hexdigest()


@pytest.fixture
def keys():
    private, public = rs.generate()
    return rs.parse_private_key(private), rs.parse_public_key(public), private, public


# --- format -------------------------------------------------------------------

def test_round_trip(keys):
    seed, public, _, public_text = keys
    sig = rs.parse_signature(rs.format_signature(rs.sign(seed, NAME, SHA)).encode())
    assert rs.verify(public, sig, NAME, SHA)
    assert rs.public_key_for(seed) == public_text


def test_signature_binds_name_and_hash(keys):
    seed, public, _, _ = keys
    sig = rs.sign(seed, NAME, SHA)
    assert not rs.verify(public, sig, "BaiheStudio-Setup-0.1.0.exe", SHA)
    assert not rs.verify(public, sig, NAME, hashlib.sha256(b"other").hexdigest())
    other_public = rs.parse_public_key(rs.generate()[1])
    assert not rs.verify(other_public, sig, NAME, SHA)


def test_an_installer_signature_never_verifies_an_app_zip_or_back(keys):
    seed, public, _, _ = keys
    app = "BaiheStudio-App-0.2.0.zip"
    assert rs.verify(public, rs.sign(seed, app, SHA), app, SHA)
    assert not rs.verify(public, rs.sign(seed, NAME, SHA), app, SHA)
    assert not rs.verify(public, rs.sign(seed, app, SHA), NAME, SHA)


@pytest.mark.parametrize("name,sha", [
    ("BaiheStudio-Setup-0.2.0.exe\nsha256: x", SHA), ("other.exe", SHA),
    (NAME, SHA.upper()), (NAME, SHA[:-1]),
    # Only the two release file names, each with a version.
    ("BaiheStudio-App-0.2.0.exe", SHA), ("BaiheStudio-Setup-0.2.0.zip", SHA),
    ("BaiheStudio-App-latest.zip", SHA), ("BaiheStudio-App-.zip", SHA),
    ("BaiheStudio-App-0.2.0.zip.sig", SHA), ("BaiheStudio-Other-0.2.0.zip", SHA)])
def test_message_parts_cannot_add_lines(name, sha):
    with pytest.raises(rs.SignatureFormatError):
        rs.signed_message(name, sha)


def test_message_is_the_documented_text():
    assert rs.signed_message(NAME, SHA) == (
        f"baihe-studio-release-signature-v1\nname: {NAME}\nsha256: {SHA}\n").encode()


@pytest.mark.parametrize("text", ["", "   \n", "# only a comment\n\n"])
def test_no_key_line_means_no_key(text):
    assert rs.parse_public_key(text) is None


def test_a_private_key_is_never_taken_as_public(keys):
    _, _, private, _ = keys
    with pytest.raises(rs.SignatureFormatError, match="private key"):
        rs.parse_public_key(private)


def test_errors_never_echo_the_key(keys):
    _, _, private, _ = keys
    for bad in (private + "x", "ed25519-public:" + private.split(":")[1] + "!"):
        with pytest.raises(rs.SignatureFormatError) as info:
            rs.parse_public_key(bad)
        assert private.split(":")[1] not in str(info.value)


def test_committed_public_key_file_is_usable_and_holds_no_private_key():
    text = (ROOT / "installer" / "release_public_key.txt").read_text(encoding="utf-8")
    assert rs.PRIVATE_PREFIX not in text
    rs.parse_public_key(text)   # raises if the committed line is malformed


# --- tools/release_signing.py -------------------------------------------------------

def test_tool_generate_refuses_to_print_the_key_into_a_pipe(capsys):
    # pytest's captured stdout is not a terminal, like a pipe, a log or an agent.
    assert tool.main(["generate"]) == 1
    out, err = capsys.readouterr()
    assert rs.PRIVATE_PREFIX not in out + err and "--force" in err


def test_tool_generate_sign_verify(tmp_path, monkeypatch, capsys):
    assert tool.main(["generate", "--force"]) == 0
    out, err = capsys.readouterr()
    private = next(line for line in out.splitlines() if line.startswith(rs.PRIVATE_PREFIX))
    public = next(line for line in out.splitlines() if line.startswith(rs.PUBLIC_PREFIX))
    assert "Never commit" in err and private not in err
    exe = tmp_path / NAME
    exe.write_bytes(b"installer bytes")
    key_file = tmp_path / "release_public_key.txt"
    key_file.write_text(public + "\n", encoding="utf-8")
    monkeypatch.setenv(tool.SECRET_NAME, private)
    assert tool.main(["sign", str(exe)]) == 0
    out, _ = capsys.readouterr()
    assert private not in out and public in out
    assert tool.main(["verify", str(exe), "--public-key", str(key_file)]) == 0
    exe.write_bytes(b"installer bytes, tampered")
    assert tool.main(["verify", str(exe), "--public-key", str(key_file)]) == 1


def test_tool_refuses_a_public_key_as_the_signing_key(tmp_path, monkeypatch, keys):
    _, _, _, public = keys
    exe = tmp_path / NAME
    exe.write_bytes(b"x")
    monkeypatch.setenv(tool.SECRET_NAME, public)
    assert tool.main(["sign", str(exe)]) == 1
    assert not (tmp_path / (NAME + ".sig")).exists()


# --- the release job's signing step -------------------------------------------------

def _release_script() -> str:
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "windows-installer.yml")
                        .read_text(encoding="utf-8"))
    # Lets the owner keep the key as a `release` environment secret behind a
    # required reviewer; a repository secret still reaches the job.
    assert wf["jobs"]["release"]["environment"] == "release"
    # A later step uploads the signed files as an artifact; only the one that runs the script is checked.
    (step,) = [s for s in wf["jobs"]["release"]["steps"] if s.get("name", "").startswith("Attach")]
    assert step["env"]["BAIHE_RELEASE_SIGNING_KEY"] == "${{ secrets.BAIHE_RELEASE_SIGNING_KEY }}"
    assert "uses" not in step
    return step["run"]


FAKE_GH = """#!/usr/bin/env bash
set -eu
if [ "$1 $2" = "run download" ]; then
  while [ $# -gt 0 ]; do case "$1" in --name) n="$2"; shift;; --dir) d="$2"; shift;; esac; shift; done
  mkdir -p "$d/$n"
  printf 'MZ fake installer' > "$d/$n/$n.exe"
elif [ "$1" = "api" ]; then
  printf '# comment\\n%s\\n' "$GH_COMMITTED_PUBLIC_KEY"
elif [ "$1 $2" = "release view" ]; then
  exit 1
elif [ "$1 $2" = "release create" ]; then
  printf '%s\\n' "$@" > "$GH_LOG"
fi
"""


FAKE_GH_WITH_APP_ZIP = FAKE_GH.replace(
    "printf 'MZ fake installer' > \"$d/$n/$n.exe\"",
    "printf 'MZ fake installer' > \"$d/$n/$n.exe\"\n"
    "  printf 'PK fake app' > \"$d/$n/BaiheStudio-App-0.2.0.zip\"\n"
    "  printf '{}' > \"$d/$n/BaiheStudio-App-0.2.0.json\"")


def _run_release(tmp_path, key, fake_gh=FAKE_GH, committed_public=None,
                 ref="v0.2.0"):
    if os.name == "nt" or not (shutil.which("bash") and shutil.which("openssl")
                               and shutil.which("base64")):
        pytest.skip("needs bash, openssl and coreutils (the release job runs on ubuntu)")
    script = _release_script()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(fake_gh, encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "GITHUB_REF_NAME": ref,
           "GITHUB_RUN_ID": "1", "GITHUB_REPOSITORY": "o/r", "GH_TOKEN": "t",
           "GH_LOG": str(tmp_path / "gh.log"), "TMPDIR": str(tmp_path),
           "GITHUB_SHA": "0" * 40,
           "GH_COMMITTED_PUBLIC_KEY": committed_public or ""}
    if key is not None:
        env["BAIHE_RELEASE_SIGNING_KEY"] = key
    proc = subprocess.run(["bash", "-c", script], cwd=work, env=env, capture_output=True,
                          text=True, timeout=60)
    log = tmp_path / "gh.log"
    # On GitHub the runner consumes ::add-mask:: lines; they never reach the log.
    proc.stdout = "\n".join(line for line in proc.stdout.splitlines()
                             if not line.startswith("::add-mask::"))
    return proc, (log.read_text().splitlines() if log.exists() else None), work


def _ci_check_script() -> str:
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "windows-installer.yml")
                        .read_text(encoding="utf-8"))
    steps = wf["jobs"]["release"]["steps"]
    # First, so a red commit never reaches the signing key.
    assert steps[0]["name"] == "Check that Tests passed at the tagged commit"
    return steps[0]["run"]


FAKE_GH_CHECKS = """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" > "$GH_LOG"
printf '%s\\n' "$GH_CONCLUSION"
"""


@pytest.mark.parametrize("conclusion,ok", [
    ("success", True), ("failure", False), ("cancelled", False), ("null", False),
    ("missing", False)])
def test_release_job_publishes_only_when_tests_passed_at_the_commit(tmp_path, conclusion, ok):
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("needs bash (the release job runs on ubuntu)")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH_CHECKS, encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "GITHUB_REPOSITORY": "o/r",
           "GITHUB_SHA": "a" * 40, "GH_TOKEN": "t", "GH_LOG": str(tmp_path / "gh.log"),
           "GH_CONCLUSION": conclusion}
    proc = subprocess.run(["bash", "-c", _ci_check_script()], env=env, capture_output=True,
                          text=True, timeout=60)
    assert (proc.returncode == 0) is ok, proc.stdout + proc.stderr
    assert f"repos/o/r/commits/{'a' * 40}/check-runs?check_name=test" in (
        tmp_path / "gh.log").read_text()
    if not ok:
        assert "Nothing was published" in proc.stdout


def test_release_job_signs_what_the_app_verifies(tmp_path, keys):
    seed, public, private, public_text = keys
    proc, gh_args, work = _run_release(tmp_path, private, committed_public=public_text)
    assert proc.returncode == 0, proc.stderr
    exe = work / "out" / "BaiheStudio-Setup-0.2.0" / NAME
    assets = [a for a in gh_args if a.startswith("out/")]
    assert [Path(a).name for a in assets] == [NAME, NAME + ".sha256", NAME + ".sig"]
    sig = rs.parse_signature((exe.parent / (NAME + ".sig")).read_bytes())
    assert rs.verify(public, sig, NAME, hashlib.sha256(exe.read_bytes()).hexdigest())
    seed_text = private.split(":", 1)[1]
    assert seed_text not in proc.stdout and seed_text not in proc.stderr
    assert rs.public_key_for(seed) in proc.stdout
    assert f"{NAME} sha256 {hashlib.sha256(exe.read_bytes()).hexdigest()}" in proc.stdout
    # The key file is removed when the step ends.
    assert not any(p.name == "key.der" for p in tmp_path.rglob("*"))


def test_release_job_without_the_secret_publishes_unsigned_and_says_so(tmp_path):
    proc, gh_args, _ = _run_release(tmp_path, None, ref="v0.2.0-beta.1")
    assert proc.returncode == 0, proc.stderr
    name = "BaiheStudio-Setup-0.2.0-beta.1.exe"
    assert [Path(a).name for a in gh_args if a.startswith("out/")] == [name, name + ".sha256"]
    assert "--prerelease" in gh_args
    assert "UNSIGNED" in proc.stdout
    sha = hashlib.sha256(b"MZ fake installer").hexdigest()
    assert f"{name} sha256 {sha}" in proc.stdout


def test_release_job_without_the_secret_refuses_a_stable_tag(tmp_path):
    proc, gh_args, _ = _run_release(tmp_path, None)
    assert proc.returncode != 0 and gh_args is None
    assert "Nothing was published" in proc.stdout


def test_release_job_refuses_a_key_that_is_not_the_shipped_one(tmp_path, keys):
    _, _, private, _ = keys
    other_public = rs.generate()[1]
    proc, gh_args, _ = _run_release(tmp_path, private, committed_public=other_public)
    assert proc.returncode != 0 and gh_args is None
    assert "Nothing was published" in proc.stdout


@pytest.mark.parametrize("bad", ["not-a-key", "ed25519-private:AAAA", "ed25519-private:!!!"])
def test_release_job_with_a_broken_secret_publishes_nothing(tmp_path, bad):
    proc, gh_args, _ = _run_release(tmp_path, bad)
    assert proc.returncode != 0 and gh_args is None
    assert bad not in proc.stdout


def test_release_job_signs_the_app_zip_too(tmp_path, keys):
    seed, public, private, public_text = keys
    proc, gh_args, work = _run_release(tmp_path, private, FAKE_GH_WITH_APP_ZIP,
                                       committed_public=public_text)
    assert proc.returncode == 0, proc.stderr
    out = work / "out" / "BaiheStudio-Setup-0.2.0"
    app = "BaiheStudio-App-0.2.0.zip"
    assert sorted(Path(a).name for a in gh_args if a.startswith("out/")) == sorted([
        NAME, NAME + ".sha256", NAME + ".sig", app, app + ".sha256", app + ".sig",
        "BaiheStudio-App-0.2.0.json"])
    for name in (NAME, app):
        data = (out / name).read_bytes()
        sig = rs.parse_signature((out / (name + ".sig")).read_bytes())
        assert rs.verify(public, sig, name, hashlib.sha256(data).hexdigest())
        assert (out / (name + ".sha256")).read_text().split() == [
            hashlib.sha256(data).hexdigest(), name]
    assert private.split(":", 1)[1] not in proc.stdout + proc.stderr


def test_a_stable_tag_is_published_as_a_pre_release_first(tmp_path, keys):
    # Copies with "Include pre-releases" off are offered it only once
    # Promote Release has run.
    _, _, private, public_text = keys
    proc, gh_args, _ = _run_release(tmp_path, private, committed_public=public_text)
    assert proc.returncode == 0, proc.stderr
    assert "--prerelease" in gh_args


# --- the promote job -------------------------------------------------------------

def _promote_job():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "promote-release.yml")
                        .read_text(encoding="utf-8"))
    job = wf["jobs"]["promote"]
    assert job["environment"] == "release-main"
    assert not any("uses" in s for s in job["steps"])   # nothing from the repository runs
    (step,) = job["steps"]
    return step["run"]


FAKE_GH_PROMOTE = """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$GH_LOG"
case "$1 $2" in
  "api repos/o/r/environments/release-main") printf '%s\\n' "$GH_RULES" ;;
  "release view")
    case "$*" in
      *isDraft*) printf '%s\\n' "$GH_STATE" ;;
      *assets*) printf '%s\\n' $GH_ASSETS ;;
    esac ;;
  "release edit") ;;
  *) exit 1 ;;
esac
"""

SIGNED = f"{NAME} {NAME}.sha256 {NAME}.sig"


def _run_promote(tmp_path, tag="v0.2.0", rules="1", state="false true", assets=SIGNED):
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("needs bash (the promote job runs on ubuntu)")
    script = _promote_job()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH_PROMOTE, encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    log = tmp_path / "gh.log"
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "TAG": tag,
           "GITHUB_REPOSITORY": "o/r", "GH_TOKEN": "t", "GH_LOG": str(log),
           "GH_RULES": rules, "GH_STATE": state, "GH_ASSETS": assets}
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True,
                          timeout=60)
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, [c for c in calls if c.startswith("release edit")]


def test_promote_makes_the_tested_pre_release_the_full_release(tmp_path):
    proc, edits = _run_promote(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert edits == ["release edit v0.2.0 --repo o/r --prerelease=false --latest"]


APP = "BaiheStudio-App-0.2.0.zip"


def test_promote_checks_the_app_zip_files_when_the_release_has_one(tmp_path):
    proc, edits = _run_promote(tmp_path, assets=f"{SIGNED} {APP} {APP}.sha256")
    assert proc.returncode != 0 and edits == []
    assert f"no {APP}.sig" in proc.stdout


def test_promote_takes_a_release_with_a_signed_app_zip(tmp_path):
    proc, edits = _run_promote(tmp_path, assets=f"{SIGNED} {APP} {APP}.sha256 {APP}.sig "
                                                "BaiheStudio-App-0.2.0.json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(edits) == 1


@pytest.mark.parametrize("change,says", [
    ({"rules": "0"}, "no required reviewer"),
    ({"state": "false false"}, "not a published pre-release"),
    ({"state": "true true"}, "not a published pre-release"),
    ({"tag": "v0.2.0-beta.1"}, "stays a pre-release"),
    ({"tag": "v0.2.0; rm -rf /"}, "not a release tag"),
    ({"assets": f"{NAME} {NAME}.sha256"}, f"no {NAME}.sig"),
])
def test_promote_refuses_and_changes_nothing(tmp_path, change, says):
    proc, edits = _run_promote(tmp_path, **change)
    assert proc.returncode != 0 and edits == []
    assert says in proc.stdout and "Nothing was promoted" in proc.stdout

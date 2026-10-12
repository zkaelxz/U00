"""
lib/release_signature.py, tools/release_signing.py and the release job's
signing step in .github/workflows/windows-installer.yml. Keys are throwaway
ones made here; the workflow step runs for real with a fake `gh` on PATH.
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


@pytest.mark.parametrize("name,sha", [
    ("BaiheStudio-Setup-0.2.0.exe\nsha256: x", SHA), ("other.exe", SHA),
    (NAME, SHA.upper()), (NAME, SHA[:-1])])
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

def test_tool_generate_sign_verify(tmp_path, monkeypatch, capsys):
    assert tool.main(["generate"]) == 0
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
    (step,) = wf["jobs"]["release"]["steps"]
    assert step["env"]["BAIHE_RELEASE_SIGNING_KEY"] == "${{ secrets.BAIHE_RELEASE_SIGNING_KEY }}"
    assert "uses" not in step
    return step["run"]


FAKE_GH = """#!/usr/bin/env bash
set -eu
if [ "$1 $2" = "run download" ]; then
  while [ $# -gt 0 ]; do case "$1" in --name) n="$2"; shift;; --dir) d="$2"; shift;; esac; shift; done
  mkdir -p "$d/$n"
  printf 'MZ fake installer' > "$d/$n/$n.exe"
elif [ "$1 $2" = "release create" ]; then
  printf '%s\\n' "$@" > "$GH_LOG"
fi
"""


def _run_release(tmp_path, key):
    if os.name == "nt" or not (shutil.which("bash") and shutil.which("openssl")
                               and shutil.which("base64")):
        pytest.skip("needs bash, openssl and coreutils (the release job runs on ubuntu)")
    script = _release_script()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH, encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "GITHUB_REF_NAME": "v0.2.0",
           "GITHUB_RUN_ID": "1", "GITHUB_REPOSITORY": "o/r", "GH_TOKEN": "t",
           "GH_LOG": str(tmp_path / "gh.log"), "TMPDIR": str(tmp_path)}
    if key is not None:
        env["BAIHE_RELEASE_SIGNING_KEY"] = key
    proc = subprocess.run(["bash", "-c", script], cwd=work, env=env, capture_output=True,
                          text=True, timeout=60)
    log = tmp_path / "gh.log"
    # On GitHub the runner consumes ::add-mask:: lines; they never reach the log.
    proc.stdout = "\n".join(line for line in proc.stdout.splitlines()
                             if not line.startswith("::add-mask::"))
    return proc, (log.read_text().splitlines() if log.exists() else None), work


def test_release_job_signs_what_the_app_verifies(tmp_path, keys):
    seed, public, private, _ = keys
    proc, gh_args, work = _run_release(tmp_path, private)
    assert proc.returncode == 0, proc.stderr
    exe = work / "out" / "BaiheStudio-Setup-0.2.0" / NAME
    assets = [a for a in gh_args if a.startswith("out/")]
    assert [Path(a).name for a in assets] == [NAME, NAME + ".sha256", NAME + ".sig"]
    sig = rs.parse_signature((exe.parent / (NAME + ".sig")).read_bytes())
    assert rs.verify(public, sig, NAME, hashlib.sha256(exe.read_bytes()).hexdigest())
    seed_text = private.split(":", 1)[1]
    assert seed_text not in proc.stdout and seed_text not in proc.stderr
    assert rs.public_key_for(seed) in proc.stdout
    # The key file is removed when the step ends.
    assert not any(p.name == "key.der" for p in tmp_path.rglob("*"))


def test_release_job_without_the_secret_publishes_unsigned_and_says_so(tmp_path):
    proc, gh_args, _ = _run_release(tmp_path, None)
    assert proc.returncode == 0, proc.stderr
    assert [Path(a).name for a in gh_args if a.startswith("out/")] == [NAME, NAME + ".sha256"]
    assert "UNSIGNED" in proc.stdout


@pytest.mark.parametrize("bad", ["not-a-key", "ed25519-private:AAAA", "ed25519-private:!!!"])
def test_release_job_with_a_broken_secret_publishes_nothing(tmp_path, bad):
    proc, gh_args, _ = _run_release(tmp_path, bad)
    assert proc.returncode != 0 and gh_args is None
    assert bad not in proc.stdout

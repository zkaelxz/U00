"""extension/background.js reloads itself once when the app ships a newer copy of
its files, so a copy loaded from Baihe's install folder picks up an app update.
Run in Node with a fake `chrome` (tests/js/extension_harness.mjs)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "js" / "extension_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def run(shipped, mine, reloaded_for=None):
    scenario = json.dumps({"shipped": shipped, "mine": mine, "reloadedFor": reloaded_for})
    out = subprocess.run(["node", str(HARNESS), "reload_after_update", scenario],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_newer_app_copy_reloads_the_extension():
    got = run("1.2.13", "1.2.12")
    assert got["ok"] is True and got["reloads"] == 1 and got["startupListener"] is True


@pytest.mark.parametrize("shipped", ["1.2.12", "1.2.11", "1.10.0-x", None])
def test_the_same_or_an_older_copy_does_not(shipped):
    assert run(shipped, "1.10.0" if shipped == "1.10.0-x" else "1.2.12")["reloads"] == 0


def test_one_try_per_version_when_the_folder_isnt_baihes_own():
    assert run("1.2.13", "1.2.12", reloaded_for="1.2.13")["reloads"] == 0

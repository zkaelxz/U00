"""
extension/shared.js (status line and error wording shared by the popup and
options pages) and options.js, run against a fake page and browser by
tests/js/shared_harness.mjs: no network, no real browser.
"""
import json
import os
import shutil
import subprocess

import pytest

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "js", "shared_harness.mjs")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(kind, scenario):
    done = subprocess.run([NODE, HARNESS, kind, scenario], capture_output=True, text=True,
                          timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


class TestSharedHelpers:
    def test_pluralize(self):
        assert _run("shared", "pluralize") == ["0 pages", "1 page", "2 pages"]

    def test_a_missing_or_not_ok_reply_is_a_failure(self):
        assert _run("shared", "failure_detection") == [True, True, True, True, False]

    def test_failure_message_prefers_the_workers_error(self):
        assert _run("shared", "failure_message") == [
            "That didn't work.", "That didn't work.", "boom", "Couldn't reach the app.", "boom"]

    def test_show_status_sets_and_clears_the_bad_flag(self):
        out = _run("shared", "show_status")
        assert out == {"first": ["bad one", True], "second": ["fine", False]}


class TestOptionsPage:
    def test_a_stored_token_is_prefilled(self):
        assert _run("options", "prefills_stored_token")["token"] == "abc"

    def test_an_empty_token_is_refused_without_saving_or_asking_the_worker(self):
        out = _run("options", "empty_token_refused")
        assert out["status"] == "Paste the token from Baihe's Settings first."
        assert out["bad"] is True
        assert out["set"] == [] and out["sent"] == []

    def test_save_trims_the_token_and_reports_the_drama_count(self):
        out = _run("options", "connected_one_drama")
        assert out["set"] == [{"token": "tok"}]
        assert out["sent"] == [{"type": "health"}]
        assert out["status"] == "Connected to Baihe. 1 drama available."
        assert out["bad"] is False

    def test_plural_dramas(self):
        assert _run("options", "connected_two_dramas")["status"] == "Connected to Baihe. 2 dramas available."

    def test_missing_engine_is_called_out(self):
        status = _run("options", "connected_no_engine")["status"]
        assert status.startswith("Connected to Baihe. 1 drama available. No translation engine is set")

    def test_the_workers_error_is_shown(self):
        out = _run("options", "worker_error_shown")
        assert (out["status"], out["bad"]) == ("Bridge is off.", True)

    def test_no_reply_falls_back_to_the_unreachable_message(self):
        out = _run("options", "no_reply_fallback")
        assert (out["status"], out["bad"]) == ("Couldn't reach the app.", True)

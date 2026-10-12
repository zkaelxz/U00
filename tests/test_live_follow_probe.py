"""The followed-channel live check in a real child process
(services/ytdlp_child.run_live_probe through live_follow_poller.probe). The
child imports a fake `yt_dlp` from a temp folder first on PYTHONPATH:
nothing is fetched and the real yt-dlp is never loaded."""
import json
import os
import time

import pytest

from services import live_follow_poller as poller
from services import settings_service, ytdlp_child

FAKE_YT_DLP = '''
import json, os, sys, time

class YoutubeDL:
    def __init__(self, opts):
        self.opts = opts
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def extract_info(self, url, download=True):
        with open(os.environ["FAKE_YTDLP_SEEN"], "w") as f:
            json.dump({"url": url, "download": download,
                       "opts": {k: v for k, v in self.opts.items() if k != "cookiesfrombrowser"},
                       "browser": list(self.opts.get("cookiesfrombrowser") or [])}, f)
        mode = os.environ["FAKE_YTDLP_MODE"]
        if mode == "live":
            return {"id": "abcdefghijk", "live_status": "is_live", "title": "Now", "channel": "Ch",
                    "url": "https://manifest.example/secret.m3u8", "formats": [{"x": 1}]}
        if mode == "upcoming":
            return {"id": "upcomingid1", "live_status": "is_upcoming", "channel": "Ch",
                    "release_timestamp": 1800000000}
        if mode == "channel_page":
            return {"_type": "playlist", "id": "UCxxxxxxxxxxxxxxxxxxxxxx", "entries": []}
        if mode == "not_live":
            raise RuntimeError("ERROR: [youtube:tab] @x: The channel is not currently live")
        if mode == "sign_in":
            raise RuntimeError("ERROR: Sign in to confirm you are not a bot " + url)
        if mode == "hang":
            time.sleep(120)
        if mode == "spam":
            for _ in range(10000):
                sys.stdout.write("x" * 100 + "\\n")
            sys.stdout.flush()
            time.sleep(120)
        raise RuntimeError("unexpected")
'''


@pytest.fixture
def child(tmp_path, monkeypatch, isolated_db):
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "yt_dlp.py").write_text(FAKE_YT_DLP, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        [str(fake)] + [p for p in [os.environ.get("PYTHONPATH")] if p]))
    seen = tmp_path / "seen.json"
    monkeypatch.setenv("FAKE_YTDLP_SEEN", str(seen))
    monkeypatch.setattr(settings_service, "get_cookie_settings",
                        lambda: {"cookies_browser": "firefox", "cookies_file": None})
    poller.reset_for_tests()

    def run(mode, pc_owner=0):
        monkeypatch.setenv("FAKE_YTDLP_MODE", mode)
        row = {"id": 1, "channel_url": "https://www.youtube.com/@name/live", "pc_owner": pc_owner}
        result = poller.probe(row)
        return result, (json.loads(seen.read_text()) if seen.exists() else None)
    yield run
    poller.reset_for_tests()


def test_live_metadata_only_through_the_proxy(child):
    result, seen = child("live")
    assert result == {"live": {"status": "is_live", "id": "abcdefghijk", "title": "Now",
                               "channel": "Ch", "release_timestamp": None}}
    assert seen["url"] == "https://www.youtube.com/@name/live" and seen["download"] is False
    opts = seen["opts"]
    assert opts["skip_download"] is True and opts["ignore_no_formats_error"] is True
    assert opts["proxy"].startswith("http://") and "127.0.0.1" in opts["proxy"]
    assert "cookiefile" not in opts and seen["browser"] == []
    assert poller.classify(result)["state"] == "live"


def test_cookies_only_for_the_pc_owners_follow(child):
    _, seen = child("live", pc_owner=1)
    assert seen["browser"] == ["firefox"]


def test_upcoming_returns_its_start_time(child):
    result, _ = child("upcoming")
    c = poller.classify(result)
    assert (c["state"], c["stream_id"], c["starts_at"]) == ("upcoming", "upcomingid1", 1800000000.0)


@pytest.mark.parametrize("mode, state, status", [
    ("channel_page", "not_live", poller.NOT_LIVE),
    ("not_live", "not_live", poller.NOT_LIVE),
    ("sign_in", "error", "YouTube asked for a sign-in check."),
])
def test_not_live_and_errors(child, mode, state, status):
    result, _ = child(mode)
    c = poller.classify(result)
    assert (c["state"], c["status"], c["stream_id"]) == (state, status, "")


def test_a_hung_check_is_killed_at_the_time_limit(child, monkeypatch):
    monkeypatch.setattr(poller, "PROBE_TIMEOUT_SECONDS", 2)
    start = time.monotonic()
    result, _ = child("hang")
    assert result == {"timed_out": True}
    assert time.monotonic() - start < 15
    assert poller.classify(result)["status"] == poller.TIMED_OUT


def test_output_past_the_cap_ends_the_check(child, monkeypatch):
    monkeypatch.setattr(poller, "PROBE_OUTPUT_CHARS", 50_000)
    start = time.monotonic()
    result, _ = child("spam")
    assert result == {"error": ""}
    assert time.monotonic() - start < 15
    assert poller.classify(result)["state"] == "error"


def test_a_stop_cancels_a_running_check(child):
    import threading
    threading.Timer(0.5, poller._stop.set).start()
    start = time.monotonic()
    result, _ = child("hang")
    assert result == {"cancelled": True}
    assert time.monotonic() - start < 15


def test_the_temp_folder_and_spec_are_removed(child, isolated_db):
    import storage
    before = set(os.listdir(storage.temp_root()))
    child("live")
    assert set(os.listdir(storage.temp_root())) == before


def test_live_fields_shape_checks_site_values():
    out = ytdlp_child.live_fields({"live_status": "weird", "is_live": True, "id": "a/b",
                                   "title": "t" * 500, "release_timestamp": True})
    assert out == {"status": "is_live", "id": "", "title": "t" * 200, "channel": "",
                   "release_timestamp": None}
    assert ytdlp_child.live_fields(None)["status"] == "not_live"
    assert ytdlp_child.live_fields({"live_status": "is_upcoming", "id": "x",
                                    "_type": "playlist"})["status"] == "not_live"

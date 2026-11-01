"""Tests for GET /api/media/dramas/{id}/shot-changes: ffmpeg is faked. Isolated library."""

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import video_export
from lib import proc
from api.api_config import ApiSettings
from api.server import create_app
from services import shot_changes_service as svc

SHOWINFO = (
    "[Parsed_showinfo_1 @ 0x1] n:   0 pts:  48 pts_time:2.0 pos:1 fmt:yuv420p\n"
    "[Parsed_showinfo_1 @ 0x1] n:   1 pts: 120 pts_time:5.25 pos:2 fmt:yuv420p\n"
    "[Parsed_showinfo_1 @ 0x1] n:   2 pts: 120 pts_time:5.25 pos:2 fmt:yuv420p\n"
    "[Parsed_showinfo_1 @ 0x1] n:   3 pts: 900 pts_time:99.0 pos:3 fmt:yuv420p\n"
)


class FakeFfmpeg:
    def __init__(self, stderr=SHOWINFO, returncode=0, timed_out=False):
        self.stderr, self.returncode, self.timed_out, self.calls = stderr, returncode, timed_out, []

    def __call__(self, cmd, timeout, **kw):
        self.calls.append((cmd, {"timeout": timeout, **kw}))
        return proc.CapturedRun(self.returncode, "", self.stderr, self.timed_out, False)


@pytest.fixture
def client(isolated_db):
    svc._cache.clear()
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def ffmpeg(monkeypatch):
    fake = FakeFfmpeg()
    monkeypatch.setattr(svc.proc, "run_captured", fake)
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    return fake


@pytest.fixture
def did(isolated_db):
    import db
    d = db.create_drama(title_en="D")
    with open(os.path.join(db.drama_dir(d), "source.mp4"), "wb") as f:
        f.write(b"not really video")
    db.update_drama(d, source_video_filename="source.mp4")
    return d


def _url(d, **q):
    q = {"start": 10, "end": 20, **q}
    return f"/api/media/dramas/{d}/shot-changes?" + "&".join(f"{k}={v}" for k, v in q.items())


def test_cuts_are_offset_by_the_window_start_deduped_and_clipped(client, did, ffmpeg):
    r = client.get(_url(did))
    assert r.status_code == 200
    assert r.json() == {"start": 10, "end": 20, "cuts": [12.0, 15.25]}


def test_ffmpeg_gets_the_window_a_timeout_and_no_shell(client, did, ffmpeg):
    client.get(_url(did, start=12.5, end=20))
    cmd, kw = ffmpeg.calls[0]
    assert cmd[cmd.index("-ss") + 1] == "12.500" and cmd[cmd.index("-t") + 1] == "7.500"
    assert kw["timeout"] == svc.DECODE_TIMEOUT_SECONDS
    assert cmd.index("-protocol_whitelist") < cmd.index("-i")
    assert cmd[cmd.index("-protocol_whitelist"):][:4] == video_export.local_input()


def test_second_request_for_the_same_window_is_cached(client, did, ffmpeg):
    client.get(_url(did))
    client.get(_url(did))
    assert len(ffmpeg.calls) == 1


def test_no_video_is_404(client, isolated_db):
    import db
    d = db.create_drama(title_en="NoVideo")
    assert client.get(_url(d)).status_code == 404


def test_bad_window_is_422(client, did, ffmpeg):
    assert client.get(_url(did, start=0, end=500)).status_code == 422
    assert client.get(_url(did, start=5, end=5.1)).status_code == 422


def test_ffmpeg_failure_is_503_without_a_path(client, did, monkeypatch):
    monkeypatch.setattr(svc.proc, "run_captured", FakeFfmpeg(returncode=1))
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    r = client.get(_url(did))
    assert r.status_code == 503 and "source.mp4" not in r.text


def test_timeout_is_503(client, did, monkeypatch):
    monkeypatch.setattr(svc.proc, "run_captured", FakeFfmpeg(timed_out=True))
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert client.get(_url(did)).status_code == 503


def test_busy_server_is_429(client, did, ffmpeg):
    assert svc._slots.acquire(blocking=False)
    try:
        assert client.get(_url(did)).status_code == 429
    finally:
        svc._slots.release()

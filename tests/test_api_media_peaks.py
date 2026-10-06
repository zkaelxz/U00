"""Tests for GET /api/media/dramas/{id}/peaks: ffmpeg is faked, so no real
audio is decoded. Isolated library."""

import array
import os
import subprocess
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app
from services import media_peaks_service as svc
from services.service_errors import DependencyUnavailableError, RateLimitedError


def _pcm(*amps):
    return array.array("h", amps).tobytes()


class FakeFfmpeg:
    def __init__(self, pcm=b"", error=None):
        self.pcm, self.error, self.calls = pcm, error, []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(cmd, 0, stdout=self.pcm, stderr=b"")


@pytest.fixture
def client(isolated_db):
    svc._cache.clear()
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def ffmpeg(monkeypatch):
    fake = FakeFfmpeg(_pcm(*([0] * 100 + [32767] * 100 + [-16384] * 200)))
    monkeypatch.setattr(svc.subprocess, "run", fake)
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    return fake


@pytest.fixture
def did(isolated_db):
    import db
    d = db.create_drama(title_en="D")
    with open(os.path.join(db.drama_dir(d), "source.mp3"), "wb") as f:
        f.write(b"not really audio")
    db.update_drama(d, audio_filename="source.mp3")
    return d


def _url(d, **q):
    q = {"start": 10, "end": 20, "buckets": 16, **q}
    return f"/api/media/dramas/{d}/peaks?" + "&".join(f"{k}={v}" for k, v in q.items())


def test_peaks_per_bucket(client, did, ffmpeg):
    r = client.get(_url(did, buckets=16))
    assert r.status_code == 200
    body = r.json()
    assert (body["start"], body["end"], body["buckets"]) == (10, 20, 16)
    assert len(body["peaks"]) == 16
    assert body["peaks"][0] == 0 and body["peaks"][4] == 255 and body["peaks"][-1] == 128


def test_ffmpeg_gets_the_window_and_a_timeout_and_no_shell(client, did, ffmpeg):
    client.get(_url(did, start=12.5, end=20))
    cmd, kw = ffmpeg.calls[0]
    assert cmd[cmd.index("-ss") + 1] == "12.500" and cmd[cmd.index("-t") + 1] == "7.500"
    assert kw["timeout"] == svc.DECODE_TIMEOUT_SECONDS and not kw.get("shell")
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file"
    assert cmd.index("-protocol_whitelist") < cmd.index("-i")
    assert cmd.count("-t") == 2 and cmd.index("-t", cmd.index("-i")) > cmd.index("-i")


def test_oversize_ffmpeg_output_is_503(client, did, monkeypatch):
    monkeypatch.setattr(svc.subprocess, "run", FakeFfmpeg(b"\0" * (11 * svc.SAMPLE_RATE * 2 + 2)))
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    r = client.get(_url(did))
    assert r.status_code == 503 and "source.mp3" not in r.text


def test_result_is_cached_per_window(client, did, ffmpeg):
    client.get(_url(did))
    client.get(_url(did))
    assert len(ffmpeg.calls) == 1
    client.get(_url(did, start=11))
    assert len(ffmpeg.calls) == 2


def test_cache_is_dropped_when_the_file_changes(client, did, ffmpeg):
    import db
    client.get(_url(did))
    path = os.path.join(db.drama_dir(did), "source.mp3")
    with open(path, "ab") as f:
        f.write(b"more")
    client.get(_url(did))
    assert len(ffmpeg.calls) == 2


def test_cache_is_capped(client, did, ffmpeg, monkeypatch):
    monkeypatch.setattr(svc, "CACHE_ENTRIES", 3)
    for i in range(6):
        client.get(_url(did, start=i))
    assert len(svc._cache) == 3


def test_silence_past_the_end(client, did, monkeypatch):
    monkeypatch.setattr(svc.subprocess, "run", FakeFfmpeg(b""))
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert client.get(_url(did, buckets=16)).json()["peaks"] == [0] * 16


@pytest.mark.parametrize("q", [
    {"start": -1}, {"end": 10, "start": 10}, {"start": 20, "end": 10},
    {"start": 0, "end": 121}, {"start": 0, "end": 0.1}, {"buckets": 15}, {"buckets": 2001},
    {"start": "nan"}, {"end": "inf"}])
def test_bad_window_is_422(client, did, ffmpeg, q):
    assert client.get(_url(did, **q)).status_code == 422
    assert not ffmpeg.calls


def test_a_busy_server_answers_429_at_once(client, did, ffmpeg, monkeypatch):
    monkeypatch.setattr(svc, "_slots", threading.BoundedSemaphore(1))
    svc._slots.acquire()
    r = client.get(_url(did))
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


def test_the_client_rounded_max_window_is_accepted(client, did, ffmpeg):
    # 320.1 - 200.1 is 120.00000000000003 in floats.
    assert client.get(_url(did, start=200.1, end=320.1)).status_code == 200


def test_every_max_span_position_is_accepted(did, ffmpeg):
    for i in range(0, 5000, 7):
        start = round(i / 10 + 0.1, 3)
        assert svc.get_peaks(did, start, round(start + 120, 3), 16)["end"] == round(start + 120, 3)


def test_cached_window_needs_no_slot(client, did, ffmpeg, monkeypatch):
    client.get(_url(did))
    monkeypatch.setattr(svc, "_slots", threading.BoundedSemaphore(1))
    svc._slots.acquire()
    assert client.get(_url(did)).status_code == 200


def test_one_decode_in_flight_per_caller(did, monkeypatch):
    svc._cache.clear()
    release = threading.Event()

    entered = threading.Semaphore(0)

    def slow(cmd, **kw):
        entered.release()
        # Hold both decodes open until the second caller has also entered.
        assert release.wait(5)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(svc.subprocess, "run", slow)
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    first = threading.Thread(target=svc.get_peaks, args=(did, 10, 20, 16, "user:1"))
    first.start()
    assert entered.acquire(timeout=5)
    try:
        with pytest.raises(RateLimitedError):
            svc.get_peaks(did, 11, 21, 16, "user:1")
        # Another caller still gets the second slot; and the slot is freed afterwards.
        other = threading.Thread(target=svc.get_peaks, args=(did, 12, 22, 16, "user:2"))
        other.start()
        # user:2 got its own slot: both decodes are running at once.
        assert entered.acquire(timeout=5)
    finally:
        release.set()
    first.join(5)
    other.join(5)
    assert svc.get_peaks(did, 13, 23, 16, "user:1")["buckets"] == 16


def test_a_slot_is_freed_after_a_failing_decode(did, monkeypatch):
    monkeypatch.setattr(svc, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(svc.subprocess, "run", FakeFfmpeg(error=OSError("boom")))
    with pytest.raises(DependencyUnavailableError):
        svc.get_peaks(did, 10, 20, 16, "user:1")
    monkeypatch.setattr(svc.subprocess, "run", FakeFfmpeg(_pcm(1000)))
    assert svc.get_peaks(did, 10, 20, 16, "user:1")["buckets"] == 16


def test_no_audio_is_404(client, isolated_db, ffmpeg):
    import db
    d = db.create_drama(title_en="Empty")
    assert client.get(_url(d)).status_code == 404
    assert client.get(_url(9999)).status_code == 404


def test_video_only_title_uses_the_video(client, isolated_db, ffmpeg):
    import db
    d = db.create_drama(title_en="V")
    with open(os.path.join(db.drama_dir(d), "source.mp4"), "wb") as f:
        f.write(b"x")
    db.update_drama(d, source_video_filename="source.mp4")
    assert client.get(_url(d)).status_code == 200
    assert ffmpeg.calls[0][0][ffmpeg.calls[0][0].index("-i") + 1].endswith("source.mp4")


def test_symlink_out_of_the_folder_is_404(client, isolated_db, ffmpeg, tmp_path):
    import db
    d = db.create_drama(title_en="L")
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"x")
    try:
        os.symlink(outside, os.path.join(db.drama_dir(d), "source.mp3"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    db.update_drama(d, audio_filename="source.mp3")
    assert client.get(_url(d)).status_code == 404
    assert not ffmpeg.calls


@pytest.mark.parametrize("error", [
    subprocess.CalledProcessError(1, ["ffmpeg", "-i", "/secret/dir/source.mp3"]),
    subprocess.TimeoutExpired(["ffmpeg", "-i", "/secret/dir/source.mp3"], 30),
    OSError("/secret/dir/source.mp3")])
def test_ffmpeg_failure_is_503_without_paths(client, did, monkeypatch, error):
    monkeypatch.setattr(svc.subprocess, "run", FakeFfmpeg(error=error))
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    r = client.get(_url(did))
    assert r.status_code == 503
    assert "secret" not in r.text and "source.mp3" not in r.text


def test_missing_ffmpeg_is_503(client, did, monkeypatch):
    monkeypatch.setattr(svc.shutil, "which", lambda name: None)
    r = client.get(_url(did))
    assert r.status_code == 503 and "not installed" in r.json()["error"]["message"]


def test_response_names_no_path(client, did, ffmpeg):
    import db
    assert db.drama_dir(did) not in client.get(_url(did)).text


def test_requires_media_stream(isolated_db, did, ffmpeg):
    from tests.test_api_permissions import _app, _h, _remote, _user_named
    c = _remote(_app())
    _, plain = _user_named("a@example.com", "lines.read")
    assert c.get(_url(did), headers=_h(plain)).status_code == 403
    _, streamer = _user_named("b@example.com", "media.stream")
    assert c.get(_url(did), headers=_h(streamer)).status_code == 200

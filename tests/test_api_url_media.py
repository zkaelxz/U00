"""Workspace video-URL download: POST /api/media/dramas/{id}/download-url
(local_only). yt_dlp is a fake module in sys.modules, ffmpeg is a fake
run_cancellable and DNS is patched: no network, no real yt-dlp or ffmpeg."""
import importlib.machinery
import os
import sys
import threading
import time
import types

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import storage
import video_export
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, jobs_service, media_upload_service, url_guard
from services import url_media_service as svc

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
URL = f"https://video.example/watch?v=abc&sig={SECRET}"
LOCAL_HEADERS = {"X-Baihe-Local": "1"}


class FakeYDL:
    """yt_dlp.YoutubeDL stand-in. `script` (class attr) decides what one
    extract_info does: info fields, hook events to emit, and whether a
    file is written."""
    script = {}
    last_opts = None
    calls = []

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.last_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def prepare_filename(self, info):
        return self.opts["outtmpl"].replace("%(ext)s", info["ext"])

    def extract_info(self, url, download=True):
        FakeYDL.calls.append(url)
        s = FakeYDL.script
        info = {"title": s.get("title", "Clip Title"), "ext": s.get("ext", "mp4"),
                "duration": s.get("duration", 60), "is_live": s.get("is_live", False)}
        if s.get("_type"):
            info["_type"] = s["_type"]
        if s.get("before"):
            s["before"]()
        if self.opts["match_filter"](info, incomplete=False):
            return info   # filtered out: nothing downloaded
        for event in s.get("events", [{"status": "downloading", "downloaded_bytes": 10,
                                       "total_bytes": 100},
                                      {"status": "finished", "downloaded_bytes": 100}]):
            for hook in self.opts["progress_hooks"]:
                try:
                    hook(event)
                except Exception:
                    if s.get("wrap"):   # yt-dlp style: DownloadError(exc_info=...)
                        err = RuntimeError("ERROR: wrapped")
                        err.exc_info = sys.exc_info()
                        raise err
                    raise
        path = self.prepare_filename(info)
        if self.opts.get("postprocessors"):
            path = os.path.splitext(path)[0] + ".wav"
        with open(path, "wb") as f:
            f.write(b"x" * s.get("size", 100))
        return info


@pytest.fixture
def env(isolated_db, monkeypatch):
    fake = types.ModuleType("yt_dlp")
    fake.__spec__ = importlib.machinery.ModuleSpec("yt_dlp", None)
    fake.YoutubeDL = FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])
    monkeypatch.setattr(svc.shutil, "disk_usage",
                        lambda path: types.SimpleNamespace(total=10**13, used=0, free=10**13))
    ffmpeg = []

    def fake_run(job_id, cmd, cwd=None, timeout=None, **kw):
        ffmpeg.append(cmd)
        with open(cmd[-1], "wb") as f:
            f.write(b"RIFFwav")
    monkeypatch.setattr(background_jobs, "run_cancellable", fake_run)
    writes = []
    real_update = db.update_drama
    monkeypatch.setattr(db, "update_drama",
                        lambda did, **kw: (writes.append(dict(kw)), real_update(did, **kw)))
    FakeYDL.script, FakeYDL.last_opts, FakeYDL.calls = {}, None, []
    yield types.SimpleNamespace(ffmpeg=ffmpeg, writes=writes)
    for jid in list(background_jobs.list_all_jobs()):
        if jid.startswith("urlmedia_"):
            _wait(jid)
            background_jobs.clear_job(jid)


@pytest.fixture
def client(env):
    return TestClient(create_app(ApiSettings()), base_url="http://127.0.0.1:8600",
                      client=("127.0.0.1", 5000), raise_server_exceptions=False,
                      headers=LOCAL_HEADERS)


def _wait(job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if not st or st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _drama(**kw):
    return db.create_drama(content_mode=kw.pop("content_mode", "audio_drama"), **kw)


def _start(client, did, **body):
    return client.post(f"/api/media/dramas/{did}/download-url",
                       json={"url": URL, "audio_only": True, **body})


def _run(client, did, **body):
    r = _start(client, did, **body)
    assert r.status_code == 200 and r.json() == {"job_id": f"urlmedia_{did}"}, r.text
    return _wait(f"urlmedia_{did}")


def _no_tmp(did):
    assert os.listdir(storage.temp_root()) == []


def test_audio_only_download(client, env):
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "done", st
    ddir = db.drama_dir(did)
    assert os.path.exists(os.path.join(ddir, "source.wav"))
    assert env.writes == [{"audio_filename": "source.wav", "source_url": URL,
                           "title_zh": "Clip Title"}]
    assert env.ffmpeg == []
    _no_tmp(did)
    # the job view carries no URL, title or path
    j = client.get(f"/api/jobs/urlmedia_{did}")
    assert j.status_code == 200
    assert SECRET not in j.text and "video.example" not in j.text and "Clip" not in j.text
    assert jobs_service.project_result(st["result"]) == {}


def test_video_download_extracts_audio_and_keeps_title(client, env):
    did = _drama(title_en="Mine")
    st = _run(client, did, audio_only=False)
    assert st["status"] == "done", st
    ddir = db.drama_dir(did)
    assert os.path.exists(os.path.join(ddir, "source.mp4"))
    assert os.path.exists(os.path.join(ddir, "audio.wav"))
    assert env.writes == [{"audio_filename": "audio.wav", "source_video_filename": "source.mp4",
                           "source_url": URL}]
    assert db.get_drama(did)["title_en"] == "Mine" and not db.get_drama(did)["title_zh"]
    cmd = env.ffmpeg[0]
    assert cmd[0] == "ffmpeg" and os.path.dirname(os.path.dirname(cmd[-1])) == storage.temp_root()
    i = cmd.index("-i")
    assert cmd[i - 4:i] == video_export.local_input()
    _no_tmp(did)


def test_ydl_options_caps_filters_and_no_cookies(client, env):
    did = _drama()
    _run(client, did)
    o = FakeYDL.last_opts
    assert o["noplaylist"] is True and o["playlistend"] == 1
    assert "max_filesize" not in o
    assert o["socket_timeout"] == 30 and o["retries"] == 3
    assert o["concurrent_fragment_downloads"] == 1
    assert o["external_downloader"] == {"default": "native"}
    assert o["restrictfilenames"] is True
    assert not [k for k in o if "cookie" in k.lower()]
    tmp = o["paths"]["home"]
    assert o["paths"]["temp"] == tmp and os.path.dirname(tmp) == storage.temp_root()
    assert os.path.basename(tmp).startswith("urlmedia_")
    assert os.path.dirname(o["outtmpl"]) == tmp
    assert o["allowed_extractors"] == ["default", "-generic"]
    mf = o["match_filter"]
    assert mf({"duration": 60}) is None
    assert mf({"is_live": True}) and mf({"live_status": "is_upcoming"})
    assert mf({"_type": "playlist"}) and mf({"duration": 6 * 3600 + 1})
    assert len(o["progress_hooks"]) == 2


@pytest.mark.parametrize("script", [{"is_live": True}, {"_type": "playlist"},
                                    {"duration": 7 * 3600}])
def test_filtered_out_links_fail_with_fixed_text(client, env, script):
    FakeYDL.script = script
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "error" and "live stream" in st["error"]
    assert SECRET not in st["error"] and "video.example" not in st["error"]
    assert env.writes == []
    _no_tmp(did)


def test_a_large_download_is_not_stopped_for_its_size(client, env, monkeypatch):
    monkeypatch.setattr(media_upload_service, "max_upload_bytes", lambda: 1000)
    FakeYDL.script = {"events": [{"status": "downloading", "downloaded_bytes": 5 * 10**9,
                                  "total_bytes_estimate": 9 * 10**9}]}
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "done", st
    _no_tmp(did)


@pytest.mark.parametrize("wrap", [False, True])
def test_download_stops_before_filling_the_drive(client, env, monkeypatch, wrap):
    monkeypatch.setattr(svc.shutil, "disk_usage",
                        lambda path: types.SimpleNamespace(total=10**12, used=0, free=svc.MIN_FREE_BYTES - 1))
    FakeYDL.script = {"events": [{"status": "downloading", "downloaded_bytes": 10}], "wrap": wrap}
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "error" and "drive is almost full" in st["error"]
    assert env.writes == [] and not os.path.exists(os.path.join(db.drama_dir(did), "source.wav"))
    _no_tmp(did)


def test_time_cap_aborts(client, env, monkeypatch):
    monkeypatch.setattr(svc, "MAX_WALL_SECONDS", -1)
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "error" and "2 hours" in st["error"]
    _no_tmp(did)


def test_cancel_aborts(client, env):
    did = _drama()
    FakeYDL.script = {"before": lambda: background_jobs.request_cancel(f"urlmedia_{did}")}
    st = _run(client, did)
    assert st["status"] == "cancelled"
    assert env.writes == []
    _no_tmp(did)


def test_download_failure_hides_raw_text(client, env, monkeypatch):
    def boom(self, url, download=True):
        raise RuntimeError(f"HTTP 403 for {URL} at C:\\Users\\kae\\x")
    monkeypatch.setattr(FakeYDL, "extract_info", boom)
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "error"
    assert SECRET not in st["error"] and "video.example" not in st["error"]
    assert "kae" not in st["error"] and "403" not in st["error"]
    assert SECRET not in (st.get("traceback") or "")
    _no_tmp(did)


def test_validation(client, env, monkeypatch):
    did = _drama()
    novel = _drama(content_mode="novel_narration")
    r = client.post(f"/api/media/dramas/{novel}/download-url",
                    json={"url": URL, "audio_only": True})
    assert r.status_code == 422
    assert client.post("/api/media/dramas/99999/download-url",
                       json={"url": URL, "audio_only": True}).status_code == 404
    for body in ({"url": URL}, {"url": URL, "audio_only": "yes"},
                 {"url": URL, "audio_only": True, "confirm_replace_audio": 1},
                 {"url": URL, "audio_only": True, "cookies_file": "/x"},
                 {"url": "ftp://video.example/x", "audio_only": True},
                 {"url": "https://u:p@video.example/x", "audio_only": True},
                 {"url": "https://video.example/" + "a" * 2000, "audio_only": True}):
        r = client.post(f"/api/media/dramas/{did}/download-url", json=body)
        assert r.status_code == 422, body
        assert SECRET not in r.text
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("::ffff:10.0.0.1", port))])
    r = _start(client, did)
    assert r.status_code == 422 and "video.example" not in r.text
    assert FakeYDL.calls == []


def test_replace_audio_needs_confirm(client, env):
    did = _drama()
    with open(os.path.join(db.drama_dir(did), "old.wav"), "wb") as f:
        f.write(b"old")
    db.update_drama(did, audio_filename="old.wav")
    env.writes.clear()
    r = _start(client, did)
    assert r.status_code == 422
    assert r.json()["error"]["details"]["reason"] == "confirm_replace_audio"
    assert FakeYDL.calls == []
    st = _run(client, did, confirm_replace_audio=True)
    assert st["status"] == "done"
    assert db.get_drama(did)["audio_filename"] == "source.wav"


def test_an_existing_source_video_also_needs_confirm(client, env):
    # The same rule as an upload: a video without audio is still media to replace.
    did = _drama()
    with open(os.path.join(db.drama_dir(did), "source.mp4"), "wb") as f:
        f.write(b"old video")
    db.update_drama(did, source_video_filename="source.mp4")
    r = _start(client, did)
    assert r.status_code == 422
    assert r.json()["error"]["details"]["reason"] == "confirm_replace_audio"
    assert FakeYDL.calls == []


def test_ytdlp_missing_503(client, env, monkeypatch):
    monkeypatch.setattr(svc, "_yt_dlp_installed", lambda: False)
    assert _start(client, _drama()).status_code == 503


def test_one_download_at_a_time_and_delete_refused(client, env):
    from services import drama_service
    from services.service_errors import ConflictError
    gate = threading.Event()
    FakeYDL.script = {"before": lambda: gate.wait(5)}
    a, b = _drama(), _drama()
    assert _start(client, a).status_code == 200
    assert _start(client, a).status_code == 409
    assert _start(client, b).status_code == 409
    with pytest.raises(ConflictError):
        drama_service.delete_drama(a, confirm=True, confirm_text="DELETE")
    gate.set()
    _wait(f"urlmedia_{a}")
    assert "urlmedia_" in background_jobs.DRAMA_JOB_PREFIXES


def test_remote_and_cross_site_refused(env):
    did = _drama()
    body = {"url": URL, "audio_only": True}
    path = f"/api/media/dramas/{did}/download-url"
    remote = TestClient(create_app(ApiSettings(auth_mode="on")),
                        base_url="https://baihe.example.com", raise_server_exceptions=False)
    u = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(u["id"])
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    assert remote.post(path, json=body, headers=h).status_code == 403
    local = TestClient(create_app(ApiSettings()), base_url="http://127.0.0.1:8600",
                       client=("127.0.0.1", 5000), raise_server_exceptions=False)
    other = {"Origin": "http://127.0.0.1:5173"}
    assert local.post(path, content=b'{"url": "x"}',
                      headers={**other, "Content-Type": "text/plain"}).status_code == 403
    assert local.post(path, json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert FakeYDL.calls == []


# ---------------------------------------------------------------------------
# Security review LOW-1: no generic extractor; direct media links are
# fetched by our own guarded downloader instead
# ---------------------------------------------------------------------------

DIRECT = f"https://cdn.example/media/ep1.mp3?sig={SECRET}"


class _Raw:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read1(self, n, decode_content=True):
        return self.chunks.pop(0) if self.chunks else b""


class _Resp:
    def __init__(self, status=200, headers=None, chunks=(b"ID3data",)):
        self.status_code, self.headers, self.raw = status, dict(headers or {}), _Raw(chunks)
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def direct(env, monkeypatch):
    from services import metadata_service
    hops, sent = [], []
    script = {}

    def fake_get(url, ip, headers):
        hops.append((url, ip))
        sent.append(dict(headers))
        return script.get(url) or _Resp()
    monkeypatch.setattr(metadata_service, "pinned_get", fake_get)
    return types.SimpleNamespace(hops=hops, script=script, sent=sent)


def test_direct_audio_link_skips_ytdlp(client, env, direct, monkeypatch):
    monkeypatch.delitem(sys.modules, "yt_dlp")   # not needed for a direct link
    monkeypatch.setattr(svc, "_yt_dlp_installed", lambda: False)
    did = _drama()
    r = client.post(f"/api/media/dramas/{did}/download-url",
                    json={"url": DIRECT, "audio_only": True})
    assert r.status_code == 200, r.text
    st = _wait(f"urlmedia_{did}")
    assert st["status"] == "done", st
    assert FakeYDL.calls == [] and direct.hops == [(DIRECT, "93.184.216.34")]
    cmd = env.ffmpeg[0]
    i = cmd.index("-i")
    assert cmd[i - 4:i] == video_export.local_input()
    assert os.path.exists(os.path.join(db.drama_dir(did), "source.wav"))
    assert env.writes == [{"audio_filename": "source.wav", "source_url": DIRECT}]
    _no_tmp(did)


def test_direct_video_link_keeps_video(client, env, direct):
    url = "https://cdn.example/v/clip.mp4"
    did = _drama()
    r = client.post(f"/api/media/dramas/{did}/download-url",
                    json={"url": url, "audio_only": False})
    assert r.status_code == 200
    st = _wait(f"urlmedia_{did}")
    assert st["status"] == "done", st
    ddir = db.drama_dir(did)
    assert os.path.exists(os.path.join(ddir, "source.mp4"))
    assert os.path.exists(os.path.join(ddir, "audio.wav"))
    assert FakeYDL.calls == []


def test_direct_link_redirect_to_private_address_refused(client, env, direct, monkeypatch):
    direct.script[DIRECT] = _Resp(302, {"Location": "http://inside.example:8756/x.mp3"})

    def dns(host, port, **kw):
        ip = "127.0.0.1" if host == "inside.example" else "93.184.216.34"
        return [(2, 1, 6, "", (ip, port))]
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", dns)
    did = _drama()
    client.post(f"/api/media/dramas/{did}/download-url", json={"url": DIRECT, "audio_only": True})
    st = _wait(f"urlmedia_{did}")
    assert st["status"] == "error" and st["error"].endswith(svc._FAILED)
    assert [h[0] for h in direct.hops] == [DIRECT]   # never connected to the private hop
    assert env.writes == [] and env.ffmpeg == []
    _no_tmp(did)


def test_direct_link_byte_cap(client, env, direct, monkeypatch):
    monkeypatch.setattr(media_upload_service, "max_upload_bytes", lambda: 100)
    direct.script[DIRECT] = _Resp(chunks=[b"x" * 60] * 5)
    did = _drama()
    client.post(f"/api/media/dramas/{did}/download-url", json={"url": DIRECT, "audio_only": True})
    st = _wait(f"urlmedia_{did}")
    assert st["status"] == "error" and st["error"].endswith(svc._TOO_LARGE)
    direct.script[DIRECT] = _Resp(headers={"Content-Length": "101"})
    client.post(f"/api/media/dramas/{did}/download-url", json={"url": DIRECT, "audio_only": True})
    st = _wait(f"urlmedia_{did}")
    assert st["status"] == "error" and st["error"].endswith(svc._TOO_LARGE)
    assert env.writes == []
    _no_tmp(did)


def test_direct_media_ext():
    assert svc.direct_media_ext("https://a.example/x/ep.MP3?t=1") == ".mp3"
    assert svc.direct_media_ext("https://a.example/watch?v=x.mp4") is None
    assert svc.direct_media_ext("https://a.example/page.html") is None


# ---------------------------------------------------------------------------
# Security review M-1/L-3: the direct download reads raw bytes and decodes
# them itself, so cancel, the wall clock and both byte caps are checked
# even on a deflate body of endless empty blocks
# ---------------------------------------------------------------------------

import gzip  # noqa: E402
import io  # noqa: E402

EMPTY_BLOCK = b"\x00\x00\x00\xff\xff"


class _EndlessEmptyBlocks(io.RawIOBase):
    def __init__(self):
        self.pos = 0

    def readable(self):
        return True

    def readinto(self, b):
        n = min(len(b), 8192)
        k = self.pos % len(EMPTY_BLOCK)
        b[:n] = (EMPTY_BLOCK * (n // len(EMPTY_BLOCK) + 2))[k:k + n]
        self.pos += n
        return n


class _RealRaw:
    """A real urllib3 HTTPResponse over an endless empty-block stream."""

    def __init__(self, on_read=None):
        import urllib3
        self.resp = urllib3.HTTPResponse(body=io.BufferedReader(_EndlessEmptyBlocks()),
                                         headers={"Content-Encoding": "deflate"},
                                         preload_content=False, decode_content=False)
        self.on_read, self.reads = on_read, 0

    def read1(self, n, decode_content=None):
        assert decode_content is False
        self.reads += 1
        if self.on_read:
            self.on_read(self)
        return self.resp.read1(n, decode_content=decode_content)


def _endless(on_read=None):
    r = _Resp(headers={"Content-Encoding": "deflate"})
    r.raw = _RealRaw(on_read)
    return r


def test_direct_link_cancelled_mid_stream(client, env, direct, monkeypatch):
    monkeypatch.setattr(media_upload_service, "max_upload_bytes", lambda: 10**12)
    did = _drama()

    def on_read(raw):
        if raw.reads == 3:
            background_jobs.request_cancel(f"urlmedia_{did}")
        time.sleep(0.002)
    resp = _endless(on_read)
    direct.script[DIRECT] = resp
    client.post(f"/api/media/dramas/{did}/download-url", json={"url": DIRECT, "audio_only": True})
    st = _wait(f"urlmedia_{did}")
    assert st["status"] == "cancelled", st
    assert resp.closed and resp.raw.reads < 10
    assert direct.sent[0]["Accept-Encoding"] == "identity"
    assert env.writes == [] and env.ffmpeg == []
    _no_tmp(did)


def test_direct_link_wall_clock_limit(client, env, direct, monkeypatch):
    monkeypatch.setattr(media_upload_service, "max_upload_bytes", lambda: 10**12)
    monkeypatch.setattr(svc, "MAX_WALL_SECONDS", 0.2)
    resp = _endless(lambda raw: time.sleep(0.01))
    direct.script[DIRECT] = resp
    did = _drama()
    client.post(f"/api/media/dramas/{did}/download-url", json={"url": DIRECT, "audio_only": True})
    st = _wait(f"urlmedia_{did}")
    assert st["status"] == "error" and st["error"].endswith(svc._TOO_SLOW), st
    assert resp.closed and env.writes == []
    _no_tmp(did)


def test_direct_link_endless_empty_blocks_hit_the_raw_cap(client, env, direct, monkeypatch):
    monkeypatch.setattr(media_upload_service, "max_upload_bytes", lambda: 100_000)
    resp = _endless()
    direct.script[DIRECT] = resp
    did = _drama()
    client.post(f"/api/media/dramas/{did}/download-url", json={"url": DIRECT, "audio_only": True})
    st = _wait(f"urlmedia_{did}")
    assert st["status"] == "error" and st["error"].endswith(svc._TOO_LARGE), st
    assert resp.closed and resp.raw.reads <= 100_000 // 8192 + 2
    _no_tmp(did)


def test_direct_link_compressed_body_decoded_bytes_capped(client, env, direct, monkeypatch):
    monkeypatch.setattr(media_upload_service, "max_upload_bytes", lambda: 10_000)
    bomb = gzip.compress(b"\0" * 2_000_000)
    assert len(bomb) < 10_000
    direct.script[DIRECT] = _Resp(headers={"Content-Encoding": "gzip"}, chunks=[bomb])
    did = _drama()
    client.post(f"/api/media/dramas/{did}/download-url", json={"url": DIRECT, "audio_only": True})
    st = _wait(f"urlmedia_{did}")
    assert st["status"] == "error" and st["error"].endswith(svc._TOO_LARGE), st
    assert env.writes == []
    _no_tmp(did)


def test_direct_link_gzip_decoded_and_other_encoding_refused(client, env, direct):
    direct.script[DIRECT] = _Resp(headers={"Content-Encoding": "gzip"},
                                  chunks=[gzip.compress(b"ID3data")])
    did = _drama()
    client.post(f"/api/media/dramas/{did}/download-url", json={"url": DIRECT, "audio_only": True})
    assert _wait(f"urlmedia_{did}")["status"] == "done"
    background_jobs.clear_job(f"urlmedia_{did}")
    direct.script[DIRECT] = _Resp(headers={"Content-Encoding": "br"})
    did2 = _drama()
    client.post(f"/api/media/dramas/{did2}/download-url", json={"url": DIRECT, "audio_only": True})
    st = _wait(f"urlmedia_{did2}")
    assert st["status"] == "error" and st["error"].endswith(svc._FAILED), st
    _no_tmp(did2)


def test_direct_link_more_than_five_redirects_refused(client, env, direct):
    chain = [f"https://cdn.example/r{i}/ep.mp3" for i in range(8)]
    direct.script[DIRECT] = _Resp(302, {"Location": chain[0]})
    for a, b in zip(chain, chain[1:]):
        direct.script[a] = _Resp(302, {"Location": b})
    did = _drama()
    client.post(f"/api/media/dramas/{did}/download-url", json={"url": DIRECT, "audio_only": True})
    st = _wait(f"urlmedia_{did}")
    assert st["status"] == "error" and st["error"].endswith(svc._FAILED), st
    assert len(direct.hops) == svc.MAX_DIRECT_REDIRECTS + 1
    assert env.writes == [] and env.ffmpeg == []
    _no_tmp(did)


def _caused_by(text):
    try:
        try:
            raise Exception(text)
        except Exception as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as e:
        return e


@pytest.mark.parametrize("raw, shown", [
    ("ERROR: [youtube] R4s4PY92bMM: Sign in to confirm you're not a bot.", "sign in"),
    ("Requested format is not available. Use --list-formats", "No downloadable format"),
    ("HTTP Error 429: Too Many Requests", "rate-limiting"),
    ("Unsupported URL: https://example.test/x", "doesn't support"),
    ("This video is not available in your country", "region"),
])
def test_failure_reason_is_a_fixed_sentence_for_known_causes(raw, shown):
    reason = svc._failure_reason(_caused_by(raw))
    assert shown.lower() in reason.lower()
    assert reason.endswith(".") and "R4s4PY92bMM" not in reason and "example.test" not in reason


def test_failure_reason_is_empty_for_anything_unrecognised_and_never_echoes_it():
    assert svc._failure_reason(_caused_by("boom at C:\\Users\\kae\\x with sk-ant-api03-" + "a" * 40)) == ""
    assert svc._failure_reason(RuntimeError("")) == ""


def test_a_recognised_failure_shows_the_sentence_before_the_generic_text(client, env, monkeypatch):
    def boom(self, url, download=True):
        raise RuntimeError("ERROR: [youtube] abc: Sign in to confirm you're not a bot.")
    monkeypatch.setattr(FakeYDL, "extract_info", boom)
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "error" and st["error"].endswith(svc._FAILED)
    assert "sign in" in st["error"].lower() and "abc" not in st["error"]
    _no_tmp(did)


def _kept(did):
    kept = os.path.join(db.drama_dir(did), "kept_media")
    out = {}
    for name in sorted(os.listdir(kept)) if os.path.isdir(kept) else []:
        with open(os.path.join(kept, name), "rb") as f:
            out[name] = f.read()
    return out


def test_a_confirmed_video_download_keeps_the_old_originals(client, env):
    did = _drama()
    ddir = db.drama_dir(did)
    for name, data in (("source.mp4", b"old video"), ("audio.wav", b"old wav")):
        with open(os.path.join(ddir, name), "wb") as f:
            f.write(data)
    db.update_drama(did, audio_filename="audio.wav", source_video_filename="source.mp4")
    st = _run(client, did, audio_only=False, confirm_replace_audio=True)
    assert st["status"] == "done", st
    drama = db.get_drama(did)
    assert (drama["source_video_filename"], drama["audio_filename"]) == ("source-2.mp4", "audio-2.wav")
    kept = _kept(did)
    assert sorted(kept.values()) == [b"old video", b"old wav"]
    assert all(n.startswith("replaced-") for n in kept)
    _no_tmp(did)


def test_a_download_that_cannot_be_saved_leaves_the_title_as_it_was(client, env, monkeypatch):
    did = _drama()
    ddir = db.drama_dir(did)
    with open(os.path.join(ddir, "source.wav"), "wb") as f:
        f.write(b"old")
    db.update_drama(did, audio_filename="source.wav")
    env.writes.clear()

    def no_names(*a):
        return iter([])
    monkeypatch.setattr(media_upload_service, "_in_place_names", no_names)
    st = _run(client, did, confirm_replace_audio=True)
    assert st["status"] == "error" and st["error"].endswith(svc._SAVE_FAILED)
    assert env.writes == [] and db.get_drama(did)["audio_filename"] == "source.wav"
    assert sorted(os.listdir(ddir)) == ["source.wav"]
    _no_tmp(did)

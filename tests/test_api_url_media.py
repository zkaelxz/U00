"""Workspace video-URL download: POST /api/media/dramas/{id}/download-url
(local_only). yt_dlp is a fake module in sys.modules, ffmpeg is a fake
run_cancellable and DNS is patched: no network, no real yt-dlp or ffmpeg."""
from lib import http
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
from jobs import job_launch
import db
import storage
import video_export
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, jobs_service, media_upload_service
from lib import url_guard
from services import url_media_service as svc
from services import ytdlp_child

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
        for key in ("live_status", "formats", "channel", "upload_date"):
            if s.get(key):
                info[key] = s[key]
        if s.get("before"):
            s["before"]()
        if self.opts["match_filter"](info, incomplete=False):
            return info   # filtered out: nothing downloaded
        for event in s.get("events", [{"status": "downloading", "downloaded_bytes": 10,
                                       "total_bytes": 100},
                                      {"status": "finished", "downloaded_bytes": 100}]):
            for hook in self.opts["progress_hooks"]:
                hook(event)
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
    fake.utils = types.ModuleType("yt_dlp.utils")
    fake.utils.download_range_func = lambda chapters, ranges, from_info=False: ("ranges", list(ranges))
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", fake.utils)
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])
    monkeypatch.setattr(svc.shutil, "disk_usage",
                        lambda path: types.SimpleNamespace(total=10**13, used=0, free=10**13))
    # No stagger unless a test asks for one; module state must not leak between tests.
    monkeypatch.setattr(svc, "START_SPACING_SECONDS", 0.0)
    monkeypatch.setattr(svc, "RETRY_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(svc, "_last_start", float("-inf"))
    monkeypatch.setattr(svc, "_waiting", [])
    monkeypatch.setattr(svc, "_running", set())
    ffmpeg = []

    def fake_run(job_id, cmd, cwd=None, timeout=None, **kw):
        ffmpeg.append(cmd)
        with open(cmd[-1], "wb") as f:
            f.write(b"RIFFwav")
    monkeypatch.setattr(background_jobs, "run_cancellable", fake_run)

    def fake_child(tmp_dir, spec, timeout, cancel, on_start=None):
        """ytdlp_child.run_download without the process: the real worker runs
        in-process against FakeYDL and its events are replayed. The real
        process is covered by tests/test_ytdlp_child.py."""
        if timeout <= 0:
            yield {"returncode": None, "timed_out": True, "cancelled": False}
            return
        events = []
        ytdlp_child.run_worker(dict(spec, tmp=tmp_dir), events.append)
        for event in events:
            if cancel():
                yield {"returncode": None, "timed_out": False, "cancelled": True}
                return
            yield {"event": event}
        yield {"returncode": 0, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(ytdlp_child, "run_download", fake_child)
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
    assert env.writes == [{"audio_filename": "source.wav", "source_video_filename": None,
                           "title_zh": "Clip Title"}]
    # The link is stored cleaned; the raw one (it needs ?v=) only in the PC-only column.
    stored = db.get_drama(did)
    assert stored["source_url"] == "https://video.example/watch"
    assert stored["source_url_full"] == URL
    assert env.ffmpeg == []
    _no_tmp(did)
    # the job view carries no URL, title or path
    j = client.get(f"/api/jobs/urlmedia_{did}")
    assert j.status_code == 200
    assert SECRET not in j.text and "video.example" not in j.text and "Clip" not in j.text
    assert jobs_service.project_result(st["result"]) == {}


def test_a_download_fills_blank_author_and_date_from_the_video(client, env):
    FakeYDL.script = {"channel": "Some Channel", "upload_date": "20260301"}
    typed = _drama(title_en="Mine", author="Typed")
    blank = _drama()
    for did in (typed, blank):
        st = _run(client, did)
        assert st["status"] == "done", st
    assert (db.get_drama(typed)["author"], db.get_drama(typed)["release_date"]) == \
        ("Typed", "2026-03-01")
    assert (db.get_drama(blank)["author"], db.get_drama(blank)["release_date"]) == \
        ("Some Channel", "2026-03-01")


def test_video_download_extracts_audio_and_keeps_title(client, env):
    did = _drama(title_en="Mine")
    st = _run(client, did, audio_only=False)
    assert st["status"] == "done", st
    ddir = db.drama_dir(did)
    assert os.path.exists(os.path.join(ddir, "source.mp4"))
    assert os.path.exists(os.path.join(ddir, "audio.wav"))
    assert env.writes == [{"audio_filename": "audio.wav", "source_video_filename": "source.mp4"}]
    assert db.get_drama(did)["source_url"] == "https://video.example/watch"
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


@pytest.mark.parametrize("script, shown", [
    ({"is_live": True}, "still live"),
    ({"live_status": "is_live"}, "still live"),
    ({"live_status": "is_upcoming"}, "hasn't started yet"),
    ({"_type": "playlist"}, "playlist or channel"),
    ({"duration": 7 * 3600 + 42 * 60}, "This video is 7 h 42 m; the limit is 6 h."),
])
def test_filtered_out_links_say_which_rule_applied(client, env, script, shown):
    FakeYDL.script = script
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "error" and shown in st["error"], st["error"]
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


def test_download_stops_before_filling_the_drive(client, env, monkeypatch):
    calls = []

    def usage(path):
        calls.append(path)  # the first look is the check before the start
        free = 10**13 if len(calls) == 1 else svc.MIN_FREE_BYTES - 1
        return types.SimpleNamespace(total=10**13, used=0, free=free)
    monkeypatch.setattr(svc.shutil, "disk_usage", usage)
    FakeYDL.script = {"events": [{"status": "downloading", "downloaded_bytes": 10}]}
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


def test_the_download_child_is_noted_in_a_held_folder_for_the_next_start(client, env, monkeypatch):
    # A server killed mid-download leaves yt-dlp writing; the next start can
    # only stop it (storage.stop_orphaned_job_children) through this note.
    from lib import proc_kill
    if proc_kill.process_identity(os.getpid()) is None:
        pytest.skip("this platform can't identify a process")
    fake = ytdlp_child.run_download
    seen = []

    def noting(tmp_dir, spec, timeout, cancel, on_start=None):
        on_start(os.getpid())
        seen.append({n for n in os.listdir(tmp_dir) if n.startswith(".baihe-")})
        yield from fake(tmp_dir, spec, timeout, cancel)
    monkeypatch.setattr(ytdlp_child, "run_download", noting)
    did = _drama()
    assert _run(client, did)["status"] == "done"
    assert seen == [{storage.HOLD_MARKER, f"{storage.CHILD_MARKER_PREFIX}{os.getpid()}"}]
    _no_tmp(did)


def test_the_download_goes_through_the_guarded_egress_proxy(client, env):
    # yt-dlp resolves DNS itself after check_public_url, so its requests must
    # reach the network only through the proxy that re-checks every connect.
    did = _drama()
    assert _run(client, did)["status"] == "done"
    proxy_url = FakeYDL.last_opts["proxy"]
    assert proxy_url.startswith("http://baihe:") and "@127.0.0.1:" in proxy_url
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
    monkeypatch.setattr(svc, "yt_dlp_installed", lambda: False)
    assert _start(client, _drama()).status_code == 503


def test_a_second_press_for_the_same_title_and_delete_are_refused(client, env):
    from services import drama_service
    from services.service_errors import ConflictError
    gate = threading.Event()
    FakeYDL.script = {"before": lambda: gate.wait(5)}
    a = _drama()
    assert _start(client, a).status_code == 200
    assert _start(client, a).status_code == 409
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
    monkeypatch.setattr(http, "pinned_get", fake_get)
    return types.SimpleNamespace(hops=hops, script=script, sent=sent)


def test_direct_audio_link_skips_ytdlp(client, env, direct, monkeypatch):
    monkeypatch.delitem(sys.modules, "yt_dlp")   # not needed for a direct link
    monkeypatch.setattr(svc, "yt_dlp_installed", lambda: False)
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
    assert env.writes == [{"audio_filename": "source.wav", "source_video_filename": None}]
    assert db.get_drama(did)["source_url"] == "https://cdn.example/media/ep1.mp3"
    assert db.get_drama(did)["source_url_full"] == DIRECT
    _no_tmp(did)


def test_direct_audio_link_with_audio_only_off_still_installs_audio_only(client, env, direct):
    did = _drama()
    r = client.post(f"/api/media/dramas/{did}/download-url",
                    json={"url": DIRECT, "audio_only": False})
    assert r.status_code == 200, r.text
    assert _wait(f"urlmedia_{did}")["status"] == "done"
    assert env.writes == [{"audio_filename": "source.wav", "source_video_filename": None}]
    assert db.get_drama(did)["source_url"] == "https://cdn.example/media/ep1.mp3"


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
    # The reason is in the server log, scrubbed: no link or address of the hop.
    import applog
    for h in applog.get_logger().handlers:
        h.flush()
    with open(os.path.join(db.LIBRARY_DIR, "logs", "app.log"), encoding="utf-8") as f:
        log_text = f.read()
    assert f"job urlmedia_{did}: download failed:" in log_text
    assert "inside.example:8756" not in log_text


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


def test_a_temporary_refusal_is_retried_once_and_then_succeeds(client, env, monkeypatch):
    real = FakeYDL.extract_info
    attempts = []

    def flaky(self, url, download=True):
        attempts.append(url)
        if len(attempts) == 1:
            raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
        return real(self, url, download)
    monkeypatch.setattr(FakeYDL, "extract_info", flaky)
    did = _drama()
    st = _run(client, did)
    assert st["status"] != "error", st
    assert len(attempts) >= 2


def test_a_temporary_refusal_that_keeps_failing_says_to_try_again(client, env, monkeypatch):
    attempts = []

    def always(self, url, download=True):
        attempts.append(url)
        raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    monkeypatch.setattr(FakeYDL, "extract_info", always)
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "error" and "try again" in st["error"].lower()
    assert len(attempts) == 2
    _no_tmp(did)


def test_a_permanent_failure_is_not_retried(client, env, monkeypatch):
    attempts = []

    def private(self, url, download=True):
        attempts.append(url)
        raise RuntimeError("ERROR: [youtube] abc: Private video. Sign in if you've been granted access")
    monkeypatch.setattr(FakeYDL, "extract_info", private)
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "error" and len(attempts) == 1


def test_error_snippet_can_keep_the_end_where_ffmpeg_names_the_failure():
    stderr = "ffmpeg version 7 banner " * 40 + "\nin.part: Invalid data found when processing input"
    snippet = svc._error_snippet(stderr, keep_end=True)
    assert snippet.endswith("Invalid data found when processing input")
    assert len(snippet) <= svc.ERROR_SNIPPET_MAX


def test_error_snippet_scrubs_links_paths_and_tokens_and_is_capped():
    hostile = ("ERROR: [youtube] abc: unable to open https://rr1.googlevideo.com/v?sig=SECRETSIG&key=xyz "
               "at C:\\Users\\kae\\AppData\\Local\\Temp\\x.part with sk-ant-api03-" + "a" * 40
               + " \x1b[31mred\x1b[0m\n" + "long " * 200)
    snippet = svc._error_snippet(hostile)
    assert 0 < len(snippet) <= svc.ERROR_SNIPPET_MAX
    for leaked in ("googlevideo", "SECRETSIG", "kae", "sk-ant", "\x1b", "\n", "ERROR:"):
        assert leaked not in snippet
    assert svc._error_snippet(None) == "" and svc._error_snippet("  ") == ""


def test_a_failed_download_shows_the_scrubbed_yt_dlp_error_in_details_only(client, env, monkeypatch):
    def boom(self, url, download=True):
        raise RuntimeError("ERROR: [youtube] abc: HTTP Error 403: Forbidden for https://x.test/v?sig=SECRETSIG")
    monkeypatch.setattr(FakeYDL, "extract_info", boom)
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "error" and "403" not in st["error"] and "SECRETSIG" not in st["error"]
    detail = st["result"]["error_detail"]
    assert "403" in detail and "SECRETSIG" not in detail and "x.test" not in detail


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


def test_an_audio_only_download_sets_an_old_video_aside(client, env):
    did = _drama()
    ddir = db.drama_dir(did)
    for name, data in (("source.mp4", b"old video"), ("audio.wav", b"old wav")):
        with open(os.path.join(ddir, name), "wb") as f:
            f.write(data)
    db.update_drama(did, audio_filename="audio.wav", source_video_filename="source.mp4")
    st = _run(client, did, confirm_replace_audio=True)
    assert st["status"] == "done", st
    drama = db.get_drama(did)
    assert (drama["audio_filename"], drama["source_video_filename"]) == ("source.wav", None)
    assert sorted(_kept(did).values()) == [b"old video", b"old wav"]
    assert sorted(os.listdir(ddir)) == ["kept_media", "source.wav"]
    _no_tmp(did)


# ---- queue: up to 3 at once, spaced starts, cancel, re-checks -------------------

def _link(n):
    return f"https://video.example/watch?v={n}"


def _until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def _message(did):
    return (background_jobs.get_status(f"urlmedia_{did}") or {}).get("message")


class Gate:
    """Holds every FakeYDL download until opened and records when each began."""

    def __init__(self):
        self.event = threading.Event()
        self.starts = []
        FakeYDL.script = {"before": self.hold}

    def hold(self):
        self.starts.append((time.monotonic(), FakeYDL.calls[-1]))
        self.event.wait(10)

    def open(self):
        self.event.set()


def test_up_to_three_run_at_once_and_the_fourth_waits_in_line(client, env):
    gate = Gate()
    ids = [_drama() for _ in range(5)]
    for n, did in enumerate(ids):
        assert _start(client, did, url=_link(n)).status_code == 200
    _until(lambda: len(gate.starts) == 3)
    _until(lambda: _message(ids[3]) == "Queued: 1st in line" and _message(ids[4]) == "Queued: 2nd in line")
    time.sleep(0.2)
    assert len(gate.starts) == 3 and svc._queued_total() == 5
    assert _message(ids[1]) != "Queued: 1st in line"
    gate.open()
    for did in ids:
        assert _wait(f"urlmedia_{did}")["status"] == "done"
    # Slots are taken first come, first served; the order the fake yt-dlp is
    # reached in after that depends on thread scheduling, so only the split holds.
    assert set(FakeYDL.calls[:3]) == {_link(n) for n in range(3)}
    assert set(FakeYDL.calls[3:]) == {_link(3), _link(4)}
    assert svc._queued_total() == 0


def test_starts_are_spaced_apart(client, env, monkeypatch):
    monkeypatch.setattr(svc, "START_SPACING_SECONDS", 0.5)
    gate = Gate()
    a, b = _drama(), _drama()
    assert _start(client, a, url=_link(1)).status_code == 200
    assert _start(client, b, url=_link(2)).status_code == 200
    _until(lambda: len(gate.starts) == 1)
    _until(lambda: _message(b) == "Queued: 1st in line")
    _until(lambda: len(gate.starts) == 2)
    assert gate.starts[1][0] - gate.starts[0][0] >= 0.45
    gate.open()
    assert _wait(f"urlmedia_{b}")["status"] == "done"


def test_cancelling_a_queued_download_never_starts_it_and_moves_the_line_up(client, env, monkeypatch):
    monkeypatch.setattr(svc, "MAX_PARALLEL_DOWNLOADS", 1)
    gate = Gate()
    a, b, c = _drama(), _drama(), _drama()
    for n, did in enumerate((a, b, c)):
        assert _start(client, did, url=_link(n)).status_code == 200
    _until(lambda: _message(c) == "Queued: 2nd in line")
    r = client.post(f"/api/jobs/urlmedia_{b}/cancel")
    assert r.status_code == 200, r.text
    assert _wait(f"urlmedia_{b}")["status"] == "cancelled"
    _until(lambda: _message(c) == "Queued: 1st in line")
    gate.open()
    assert _wait(f"urlmedia_{c}")["status"] == "done"
    assert _link(1) not in FakeYDL.calls
    assert _wait(f"urlmedia_{a}")["status"] == "done"


def test_a_failed_download_does_not_block_the_line(client, env, monkeypatch):
    monkeypatch.setattr(svc, "MAX_PARALLEL_DOWNLOADS", 1)
    real = FakeYDL.extract_info
    seen = []

    def flaky(self, url, download=True):
        seen.append(url)
        if len(seen) == 1:
            raise RuntimeError("ERROR: [youtube] abc: Private video. Sign in if you have been granted access")
        return real(self, url, download)
    monkeypatch.setattr(FakeYDL, "extract_info", flaky)
    a, b = _drama(), _drama()
    assert _start(client, a, url=_link(1)).status_code == 200
    assert _start(client, b, url=_link(2)).status_code == 200
    assert _wait(f"urlmedia_{a}")["status"] == "error"
    assert _wait(f"urlmedia_{b}")["status"] == "done"


def test_the_same_title_cannot_queue_twice_but_another_title_can(client, env, monkeypatch):
    monkeypatch.setattr(svc, "MAX_PARALLEL_DOWNLOADS", 1)
    gate = Gate()
    a, b = _drama(), _drama()
    assert _start(client, a, url=_link(1)).status_code == 200
    assert _start(client, b, url=_link(2)).status_code == 200
    assert _start(client, b, url=_link(3)).status_code == 409
    gate.open()
    _wait(f"urlmedia_{a}")
    _wait(f"urlmedia_{b}")


def test_the_queue_has_a_size_limit(client, env, monkeypatch):
    monkeypatch.setattr(svc, "MAX_PARALLEL_DOWNLOADS", 1)
    monkeypatch.setattr(svc, "MAX_QUEUED_DOWNLOADS", 2)
    gate = Gate()
    ids = [_drama() for _ in range(3)]
    assert _start(client, ids[0], url=_link(0)).status_code == 200
    assert _start(client, ids[1], url=_link(1)).status_code == 200
    assert _start(client, ids[2], url=_link(2)).status_code == 409
    gate.open()
    for did in ids[:2]:
        _wait(f"urlmedia_{did}")


def test_a_queued_download_fails_with_the_busy_text_when_its_title_got_a_job(client, env, monkeypatch):
    monkeypatch.setattr(svc, "MAX_PARALLEL_DOWNLOADS", 1)
    gate = Gate()
    a, b = _drama(), _drama()
    assert _start(client, a, url=_link(1)).status_code == 200
    assert _start(client, b, url=_link(2)).status_code == 200
    # b's own download counts as its job; this stands in for a job that begins after it queued.
    other = threading.Event()
    job_launch.start_job(f"translate_{b}", lambda: other.wait(10))
    try:
        gate.open()
        st = _wait(f"urlmedia_{b}")
        assert st["status"] == "error" and st["error"].endswith(svc._BUSY)
        assert _link(2) not in FakeYDL.calls
    finally:
        other.set()
        _wait(f"translate_{b}")
        background_jobs.clear_job(f"translate_{b}")


def test_a_queued_download_rechecks_replace_confirmation_at_start(client, env, monkeypatch):
    monkeypatch.setattr(svc, "MAX_PARALLEL_DOWNLOADS", 1)
    gate = Gate()
    a, b = _drama(), _drama()
    assert _start(client, a, url=_link(1)).status_code == 200
    assert _start(client, b, url=_link(2)).status_code == 200
    monkeypatch.setattr(media_upload_service, "has_media", lambda drama, did: did == b)
    gate.open()
    st = _wait(f"urlmedia_{b}")
    assert st["status"] == "error" and st["error"].endswith(svc._NOW_BUSY)


def test_the_free_disk_floor_counts_the_downloads_already_running(client, env, monkeypatch):
    gate = Gate()
    monkeypatch.setattr(svc.shutil, "disk_usage", lambda path: types.SimpleNamespace(
        total=10**13, used=0, free=int(svc.MIN_FREE_BYTES * 1.5)))
    a, b = _drama(), _drama()
    assert _start(client, a, url=_link(1)).status_code == 200
    _until(lambda: len(gate.starts) == 1)
    assert _start(client, b, url=_link(2)).status_code == 200
    st = _wait(f"urlmedia_{b}")
    assert st["status"] == "error" and st["error"].endswith(svc._NO_ROOM)
    assert FakeYDL.calls == [_link(1)]
    gate.open()
    assert _wait(f"urlmedia_{a}")["status"] == "done"


@pytest.mark.parametrize("n, word", [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"),
                                     (12, "12th"), (21, "21st"), (22, "22nd")])
def test_ordinals(n, word):
    assert svc._ordinal(n) == word


# ---- long videos and parts ------------------------------------------------------

LONG = 7 * 3600 + 42 * 60
FORMATS = [{"vcodec": "avc1", "tbr": 2000}, {"vcodec": "none", "abr": 128, "tbr": 128}]


def test_a_refused_long_video_says_its_length_the_size_and_the_options(client, env):
    FakeYDL.script = {"duration": LONG, "formats": FORMATS}
    did = _drama()
    st = _run(client, did, audio_only=False)
    assert st["status"] == "error"
    assert "7 h 42 m; the limit is 6 h" in st["error"] and "GB of free disk" in st["error"]
    assert "only part of it, or all of it anyway" in st["error"]
    assert st["result"] == {"kind": "url_media", "failed_reason": "too_long"}


def test_download_anyway_raises_the_cap_for_that_download_only(client, env):
    FakeYDL.script = {"duration": LONG, "formats": FORMATS}
    did = _drama()
    st = _run(client, did, allow_long=True)
    assert st["status"] == "done", st
    background_jobs.clear_job(f"urlmedia_{did}")
    other = _drama()
    assert _run(client, other)["status"] == "error"   # the next one is capped again


def test_even_download_anyway_stops_at_twelve_hours(client, env):
    FakeYDL.script = {"duration": 13 * 3600, "formats": FORMATS}
    did = _drama()
    st = _run(client, did, allow_long=True)
    assert st["status"] == "error" and "13 h; the limit is 12 h" in st["error"]
    assert "Download only part" in st["error"] and "anyway" not in st["error"]
    assert st["result"]["failed_reason"] == "too_long_max"


def test_download_anyway_checks_free_disk_against_the_estimate_first(client, env, monkeypatch):
    FakeYDL.script = {"duration": LONG, "formats": FORMATS}
    monkeypatch.setattr(svc.shutil, "disk_usage", lambda path: types.SimpleNamespace(
        total=10**13, used=0, free=svc.MIN_FREE_BYTES * 3))
    did = _drama()
    st = _run(client, did, audio_only=False, allow_long=True)
    assert st["status"] == "error" and st["error"].endswith(svc._NO_ROOM)
    assert env.writes == []
    _no_tmp(did)


def test_a_part_downloads_only_that_section_with_a_fast_copy_by_default(client, env):
    FakeYDL.script = {"duration": 3 * 3600}
    did = _drama()
    st = _run(client, did, audio_only=False, part_start="1:00:00", part_end="1:30:00")
    assert st["status"] == "done", st
    opts = FakeYDL.last_opts
    assert opts["download_ranges"] == ("ranges", [(3600, 5400)])
    assert opts["force_keyframes_at_cuts"] is False
    assert opts["format"].startswith("bestvideo[vcodec^=avc1]")


def test_exact_cuts_is_opt_in_and_allows_the_longer_wall_clock(client, env, monkeypatch):
    seen = []
    real = ytdlp_child.run_download
    monkeypatch.setattr(ytdlp_child, "run_download",
                        lambda tmp, spec, timeout, cancel, on_start=None: (seen.append(timeout), real(tmp, spec, timeout, cancel))[1])
    did = _drama()
    st = _run(client, did, audio_only=False, part_start="0:10", part_end="0:40", exact_cuts=True)
    assert st["status"] == "done", st
    assert FakeYDL.last_opts["force_keyframes_at_cuts"] is True
    assert "avc1" not in FakeYDL.last_opts.get("format", "")
    assert seen == [svc.MAX_WALL_SECONDS_LONG]


def test_a_part_of_a_long_video_is_measured_by_the_part(client, env):
    FakeYDL.script = {"duration": 10 * 3600}
    did = _drama()
    assert _run(client, did, part_start="2:00:00", part_end="3:00:00")["status"] == "done"
    background_jobs.clear_job(f"urlmedia_{did}")
    other = _drama()
    st = _run(client, other, part_start="0:00:00")   # to the end: 10 h
    assert st["status"] == "error" and "This part is 10 h; the limit is 6 h" in st["error"]


def test_a_part_starting_after_the_video_ends_says_how_long_the_video_is(client, env):
    FakeYDL.script = {"duration": 3600}
    did = _drama()
    st = _run(client, did, part_start="2:00:00")
    assert st["status"] == "error" and "which is 1 h long" in st["error"]


@pytest.mark.parametrize("body", [
    {"part_start": "abc"}, {"part_start": "1:75:00"}, {"part_start": "5", "part_end": "5"},
    {"part_start": "10:00", "part_end": "5:00"}, {"part_start": "0:00", "part_end": "13:00:01"},
    {"exact_cuts": True}, {"part_start": "x" * 13},
])
def test_bad_part_times_are_refused_before_anything_starts(client, env, body):
    did = _drama()
    r = _start(client, did, **body)
    assert r.status_code == 422, r.text
    assert background_jobs.get_status(f"urlmedia_{did}") is None


def test_a_part_of_a_direct_file_link_is_refused(client, env):
    did = _drama()
    r = _start(client, did, url="https://cdn.example/a.mp3", part_start="1:00")
    assert r.status_code == 422


def test_parse_clock():
    assert svc.parse_clock("1:02:30", "start") == 3750
    assert svc.parse_clock("2:30", "start") == 150
    assert svc.parse_clock("45", "start") == 45
    assert svc.parse_clock("  ", "start") is None and svc.parse_clock(None, "end") is None


# ---- DNS-blocked ----------------------------------------------------------------

def test_a_blocked_video_server_says_so_and_never_shows_the_link(client, env, monkeypatch):
    raw = ("ERROR: unable to download video data: <urllib3.connection.HTTPSConnection object>: "
           "Failed to resolve 'rr3---sn-abc.googlevideo.com' ([Errno -3] Temporary failure in "
           "name resolution) https://rr3---sn-abc.googlevideo.com/videoplayback?expire=1&ip=203.0.113.7"
           f"&sig={SECRET}")

    def boom(self, url, download=True):
        raise RuntimeError(raw)
    monkeypatch.setattr(FakeYDL, "extract_info", boom)
    did = _drama()
    st = _run(client, did)
    assert st["status"] == "error" and st["error"].endswith(svc._DNS_BLOCKED)
    assert "Pi-hole" in st["error"] and "googlevideo.com" in st["error"]
    assert "203.0.113.7" not in st["error"] and SECRET not in st["error"] and "videoplayback" not in st["error"]


def test_another_host_that_does_not_resolve_gets_the_general_sentence():
    reason = svc._failure_reason(_caused_by("[Errno -2] Name or service not known: cdn.other.example"))
    assert reason == svc._DNS_BLOCKED_OTHER and "other.example" not in reason


# ---- review fixes: parts need no cookies, idle disk floor, narrow long clock ----------

def test_a_part_is_refused_while_cookie_login_is_on(client, env, monkeypatch):
    monkeypatch.setattr(svc.settings_service, "get_cookie_settings",
                        lambda: {"cookies_browser": "firefox", "cookies_file": None})
    did = _drama()
    r = _start(client, did, part_start="1:00", part_end="2:00")
    assert r.status_code == 422 and "cookie login" in r.text
    assert background_jobs.get_status(f"urlmedia_{did}") is None
    assert _run(client, did)["status"] == "done"   # the whole video still works with cookies


def test_cookies_turned_on_while_a_part_waits_stop_it_at_start(client, env, monkeypatch):
    monkeypatch.setattr(svc, "MAX_PARALLEL_DOWNLOADS", 1)
    gate = Gate()
    a, b = _drama(), _drama()
    assert _start(client, a, url=_link(1)).status_code == 200
    assert _start(client, b, url=_link(2), part_start="1:00", part_end="2:00").status_code == 200
    monkeypatch.setattr(svc.settings_service, "get_cookie_settings",
                        lambda: {"cookies_browser": None, "cookies_file": "cookies.txt"})
    gate.open()
    st = _wait(f"urlmedia_{b}")
    assert st["status"] == "error" and st["error"].endswith(svc._PART_NO_COOKIES)
    assert _link(2) not in FakeYDL.calls


def test_a_section_with_no_progress_events_still_stops_at_the_floor(client, env, monkeypatch):
    FakeYDL.script = {"duration": 3 * 3600, "events": []}
    calls = []

    def usage(path):
        calls.append(path)
        return types.SimpleNamespace(total=10**13, used=0,
                                     free=10**13 if len(calls) == 1 else svc.MIN_FREE_BYTES - 1)
    monkeypatch.setattr(svc.shutil, "disk_usage", usage)
    did = _drama()
    st = _run(client, did, audio_only=False, part_start="0:10", part_end="0:40")
    assert st["status"] == "error" and "drive is almost full" in st["error"]
    assert env.writes == []
    _no_tmp(did)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_the_wall_clock_is_long_only_for_real_long_work():
    clock = FakeClock()
    normal = svc._WallClock(extended=False, may_extend=False, clock=clock)
    assert normal.ceiling == svc.MAX_WALL_SECONDS and normal.allowed == svc.MAX_WALL_SECONDS
    clock.now = svc.MAX_WALL_SECONDS + 1
    assert normal.expired()
    # "Download anyway" may extend, but only once the child reports a clip past the cap.
    clock = FakeClock()
    maybe = svc._WallClock(extended=False, may_extend=True, clock=clock)
    assert maybe.ceiling == svc.MAX_WALL_SECONDS_LONG and maybe.allowed == svc.MAX_WALL_SECONDS
    clock.now = svc.MAX_WALL_SECONDS + 1
    assert maybe.expired()
    maybe.extend()
    assert not maybe.expired() and maybe.allowed == svc.MAX_WALL_SECONDS_LONG


@pytest.mark.parametrize("body, ceiling", [
    ({"part_start": "0", "exact_cuts": True}, "normal"),            # no end: length unknown
    ({"part_start": "0:10", "part_end": "0:40"}, "normal"),         # fast copy
    ({"part_start": "0:10", "part_end": "0:40", "exact_cuts": True}, "long"),
    ({"allow_long": True}, "long"),                                  # may extend once a long clip is reported
])
def test_the_long_wall_clock_is_not_given_to_every_request(client, env, monkeypatch, body, ceiling):
    seen = []
    real = ytdlp_child.run_download
    monkeypatch.setattr(ytdlp_child, "run_download",
                        lambda tmp, spec, timeout, cancel, on_start=None: (seen.append(timeout), real(tmp, spec, timeout, cancel))[1])
    did = _drama()
    FakeYDL.script = {"duration": 3600}
    assert _run(client, did, audio_only=False, **body)["status"] == "done"
    assert seen == [svc.MAX_WALL_SECONDS_LONG if ceiling == "long" else svc.MAX_WALL_SECONDS]


def test_a_clip_past_the_normal_cap_extends_the_wall_clock(client, env, monkeypatch):
    extended = []
    real = svc._WallClock.extend
    monkeypatch.setattr(svc._WallClock, "extend", lambda self: (extended.append(1), real(self))[1])
    FakeYDL.script = {"duration": 7 * 3600, "formats": FORMATS}
    did = _drama()
    assert _run(client, did, allow_long=True)["status"] == "done"
    assert extended == [1]
    background_jobs.clear_job(f"urlmedia_{did}")
    extended.clear()
    FakeYDL.script = {"duration": 600}
    other = _drama()
    assert _run(client, other, allow_long=True)["status"] == "done"
    assert extended == []   # asked for "anyway" on a short video: still the normal clock

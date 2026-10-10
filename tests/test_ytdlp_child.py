"""services/ytdlp_child.py with a real child process. The child imports a fake
`yt_dlp` module from a temp folder put first on PYTHONPATH, so nothing is
downloaded and the real yt-dlp is never loaded."""
import os
import sys
import time

import pytest

import background_jobs
import db
import storage
from services import url_media_service as svc
from services import ytdlp_child

FAKE_YT_DLP = '''
import os, sys, time

class YoutubeDL:
    def __init__(self, opts):
        self.opts = opts
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def prepare_filename(self, info):
        return self.opts["outtmpl"].replace("%(ext)s", "m4a")
    def extract_info(self, url, download=True):
        mode = os.environ["FAKE_YTDLP_MODE"]
        for hook in self.opts["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
        if mode == "hang":
            with open(os.environ["FAKE_YTDLP_PIDFILE"], "w") as f:
                f.write(str(os.getpid()))
            time.sleep(120)
        if mode == "error":
            raise RuntimeError("HTTP Error 429: Too Many Requests for " + url)
        if mode == "crash":
            os._exit(3)
        info = {"title": "Clip", "ext": "m4a"}
        path = self.prepare_filename(info)
        path = os.path.splitext(path)[0] + (".wav" if self.opts.get("postprocessors") else ".m4a")
        with open(path, "wb") as f:
            f.write(b"x")
        return info
'''
URL = "https://video.example/watch?v=abc&sig=sk-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture
def child_env(tmp_path, monkeypatch):
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "yt_dlp.py").write_text(FAKE_YT_DLP, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        [str(fake)] + [p for p in [os.environ.get("PYTHONPATH")] if p]))
    pidfile = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_YTDLP_PIDFILE", str(pidfile))
    monkeypatch.setattr(svc.settings_service, "get_cookie_settings", lambda: {})
    work = tmp_path / "work"
    work.mkdir()
    return type("Env", (), {"work": str(work), "pidfile": pidfile, "mode":
                            staticmethod(lambda m: monkeypatch.setenv("FAKE_YTDLP_MODE", m))})


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_for(pred, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_progress_title_and_path_events_come_back(child_env):
    child_env.mode("ok")
    items = list(ytdlp_child.run_download(
        child_env.work, {"url": URL, "audio_only": True}, 60, lambda: False))
    events = [i["event"] for i in items if "event" in i]
    assert {"progress": 0.5} in events and {"title": "Clip"} in events
    path = next(e["path"] for e in events if "path" in e)
    assert os.path.dirname(path) == child_env.work and os.path.basename(path).startswith("downloaded_audio")
    assert items[-1]["returncode"] == 0 and not items[-1]["timed_out"]


def test_download_returns_path_and_title_and_reports_progress(child_env, isolated_db, monkeypatch):
    child_env.mode("ok")
    seen = []
    monkeypatch.setattr(background_jobs, "update_progress", lambda jid, f, m="": seen.append(f))
    path, title = svc._download("urlmedia_1", URL, child_env.work, True)
    assert os.path.isfile(path) and title == "Clip"
    assert seen == [pytest.approx(0.05 + 0.8 * 0.5)]


def _run_job(job_id, tmp):
    def target():
        svc._download(job_id, URL, tmp, True)
    assert background_jobs.start_job(job_id, target, description="test")
    return job_id


def _finish(job_id, timeout=20.0):
    assert _wait_for(lambda: (background_jobs.get_status(job_id) or {}).get("status")
                     not in ("running", "queued"), timeout)
    return background_jobs.get_status(job_id)


def test_cancel_mid_download_kills_the_child_and_ends_the_job_cancelled(child_env, isolated_db):
    child_env.mode("hang")
    job_id = _run_job("urlmedia_cancel", child_env.work)
    try:
        assert _wait_for(child_env.pidfile.exists)
        assert _wait_for(lambda: child_env.pidfile.read_text().strip() != "")
        pid = int(child_env.pidfile.read_text())
        assert _alive(pid)
        started = time.monotonic()
        background_jobs.request_cancel(job_id)
        st = _finish(job_id, 15)
        assert st["status"] == "cancelled"
        assert time.monotonic() - started < 15
        assert _wait_for(lambda: not _alive(pid), 5)
    finally:
        background_jobs.clear_job(job_id)


def test_the_time_cap_kills_the_child(child_env, isolated_db, monkeypatch):
    child_env.mode("hang")
    monkeypatch.setattr(svc, "MAX_WALL_SECONDS", 3)
    job_id = _run_job("urlmedia_slow", child_env.work)
    try:
        st = _finish(job_id)
        assert st["status"] == "error" and svc._TOO_SLOW in st["error"]
        assert not _alive(int(child_env.pidfile.read_text()))
    finally:
        background_jobs.clear_job(job_id)


@pytest.mark.parametrize("mode, sentence", [("error", "rate-limiting"), ("crash", None)])
def test_a_failing_child_gives_a_plain_failed_reason(child_env, isolated_db, mode, sentence):
    child_env.mode(mode)
    job_id = _run_job(f"urlmedia_{mode}", child_env.work)
    try:
        st = _finish(job_id)
        assert st["status"] == "error" and st["error"].endswith(svc._FAILED)
        if sentence:
            assert sentence in st["error"]
        for leak in ("video.example", "sk-abc", "429", "Traceback", child_env.work):
            assert leak not in st["error"]
    finally:
        background_jobs.clear_job(job_id)

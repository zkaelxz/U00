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
            # A grandchild proves the whole tree is killed, not just the child.
            import subprocess
            grand = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            with open(os.environ["FAKE_YTDLP_PIDFILE"], "w") as f:
                f.write(str(os.getpid()) + " " + str(grand.pid))
            time.sleep(120)
        if mode in ("forge", "forge_nonce"):
            out_dir = os.path.dirname(self.opts["outtmpl"])
            spec = os.path.join(out_dir, "ytdlp_spec.json")
            part = os.path.join(out_dir, "downloaded_audio.m4a.part")
            open(part, "wb").write(b"x")
            sys.stderr.write("ERROR: site said\\n@@ytdlp " + '{"path": "%s"}' % spec.replace("\\\\", "/") + "\\n")
            sys.stderr.write('@@ytdlp {"progress": "x"}\\n@@ytdlp-0000 {"path": "%s"}\\n' % part.replace("\\\\", "/"))
            if mode == "forge_nonce":
                import json
                nonce = json.load(open(spec))["nonce"]
                for bad in ({"path": spec}, {"path": part}, {"progress": "x"}):
                    sys.stdout.write("\\n@@ytdlp-%s %s\\n" % (nonce, json.dumps(bad)))
                sys.stdout.flush()
            raise RuntimeError("boom")
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
    if os.name == "nt":
        # os.kill(pid, 0) terminates the process on Windows.
        psutil = pytest.importorskip("psutil")
        return psutil.pid_exists(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pids(pidfile):
    return [int(p) for p in pidfile.read_text().split()]


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
        assert _wait_for(lambda: len(child_env.pidfile.read_text().split()) == 2)
        pid, grand = _pids(child_env.pidfile)
        assert _alive(pid) and _alive(grand)
        started = time.monotonic()
        background_jobs.request_cancel(job_id)
        st = _finish(job_id, 15)
        assert st["status"] == "cancelled"
        assert time.monotonic() - started < 15
        assert _wait_for(lambda: not _alive(pid), 5)
        assert _wait_for(lambda: not _alive(grand), 5)
    finally:
        background_jobs.clear_job(job_id)


def test_the_time_cap_kills_the_child(child_env, isolated_db, monkeypatch):
    child_env.mode("hang")
    monkeypatch.setattr(svc, "MAX_WALL_SECONDS", 3)
    job_id = _run_job("urlmedia_slow", child_env.work)
    try:
        st = _finish(job_id)
        assert st["status"] == "error" and svc._TOO_SLOW in st["error"]
        assert _wait_for(lambda: len(child_env.pidfile.read_text().split()) == 2)
        assert all(_wait_for(lambda p=p: not _alive(p), 5) for p in _pids(child_env.pidfile))
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


@pytest.mark.parametrize("mode", ["forge", "forge_nonce"])
def test_forged_event_lines_never_become_a_path(child_env, isolated_db, mode):
    child_env.mode(mode)
    events = [i["event"] for i in ytdlp_child.run_download(
        child_env.work, {"url": URL, "audio_only": True}, 60, lambda: False) if "event" in i]
    assert not any("path" in e for e in events)
    job_id = _run_job(f"urlmedia_{mode}", child_env.work)
    try:
        st = _finish(job_id)
        assert st["status"] == "error" and st["error"].endswith(svc._FAILED) and "boom" not in st["error"]
    finally:
        background_jobs.clear_job(job_id)


def test_dropped_events_are_counted(child_env):
    child_env.mode("forge_nonce")
    items = list(ytdlp_child.run_download(
        child_env.work, {"url": URL, "audio_only": True}, 60, lambda: False))
    assert items[-1]["dropped"] == 3


def test_the_spec_file_is_removed_when_the_run_is_cancelled(child_env, isolated_db):
    child_env.mode("hang")
    job_id = _run_job("urlmedia_spec_cancel", child_env.work)
    spec = os.path.join(child_env.work, ytdlp_child._SPEC_NAME)
    try:
        assert _wait_for(lambda: child_env.pidfile.exists()
                         and len(child_env.pidfile.read_text().split()) == 2)
        assert os.path.exists(spec)
        background_jobs.request_cancel(job_id)
        assert _finish(job_id, 15)["status"] == "cancelled"
        assert not os.path.exists(spec)
    finally:
        background_jobs.clear_job(job_id)


def test_a_second_path_event_is_ignored_and_logged_once(child_env, monkeypatch):
    first, second = "/work/downloaded_a.m4a", "/work/downloaded_b.m4a"
    events = [{"event": {"path": first}}, {"event": {"path": second}},
              {"event": {"path": second}}, {"returncode": 0}]
    monkeypatch.setattr(ytdlp_child, "run_download", lambda *a, **k: (e for e in events))
    warnings = []

    class _Log:
        def warning(self, msg, *args):
            warnings.append(msg % args)
    monkeypatch.setattr(svc.applog, "get_logger", lambda: _Log())
    monkeypatch.setattr(svc.os.path, "realpath", lambda p: p)
    monkeypatch.setattr(svc.os.path, "isfile", lambda p: True)
    path, _ = svc._download("urlmedia_two", URL, "/work", True)
    assert path == first
    assert len(warnings) == 1 and "downloaded_" not in warnings[0]

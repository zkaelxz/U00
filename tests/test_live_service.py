"""Tests for services/live_service.py (Live capture L-1). Fully mocked:
no ffmpeg, yt-dlp, Whisper or network."""
import os
import socket
import time

import pytest

import background_jobs
import live_translate
from services import live_service, translate_service
from services.service_errors import (DependencyUnavailableError, InvalidInputError,
                                     NotFoundError)

_REAL_CAPTURE = live_translate.start_segment_capture
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


class FakeProc:
    def __init__(self):
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None


@pytest.fixture
def live(monkeypatch, isolated_db):
    background_jobs.clear_all_jobs()
    live_service._sessions.clear()
    monkeypatch.setattr(background_jobs, "get_gpu_limit_enabled", lambda: False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: PUBLIC)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name: "sk-test")
    monkeypatch.setattr(live_translate, "resolve_stream_url", lambda url, **k: "http://media")
    calls = {"process": [], "procs": []}

    def fake_capture(source_url, out_dir, segment_seconds, protocol_whitelist=None):
        calls["out_dir"] = out_dir
        calls["protocol_whitelist"] = protocol_whitelist
        p = FakeProc()
        calls["procs"].append(p)
        return p

    monkeypatch.setattr(live_translate, "start_segment_capture", fake_capture)
    monkeypatch.setattr(live_translate, "stop_capture", lambda proc: setattr(proc, "stopped", True))
    yield calls
    for sid in list(live_service._sessions):
        live_translate.bump_generation(sid)
        background_jobs.request_cancel(sid)
    _wait(lambda: not any(background_jobs.is_running(s) for s in live_service._sessions))
    background_jobs.clear_all_jobs()
    live_service._sessions.clear()


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _start(**kw):
    args = dict(url="https://www.youtube.com/watch?v=abc", engine="fake")
    args.update(kw)
    return live_service.start_session(**args)["session_id"]


def _terminal(sid):
    return _wait(lambda: live_service.get_session(sid)["status"] in ("done", "error", "cancelled")
                 and not background_jobs.is_running(sid))


# --- URL / input validation ------------------------------------------------

@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x",
                                 "javascript:alert(1)", "", "https://user:pw@example.com/"])
def test_bad_urls_rejected(live, url):
    with pytest.raises(InvalidInputError):
        live_service.start_session(url, engine="fake")
    assert live_service._sessions == {}


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"])
def test_private_hosts_rejected(live, monkeypatch, ip):
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(fam, socket.SOCK_STREAM, 6, "", (ip, 443))])
    with pytest.raises(InvalidInputError):
        live_service.start_session("https://internal.example/", engine="fake")


def test_url_check_does_not_fetch(live, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("fetched"))
    _start()


@pytest.mark.parametrize("kw", [{"source_language": "xx"}, {"whisper_size": "huge"},
                                {"segment_seconds": "abc"}, {"max_minutes": None},
                                {"engine": "nope"}])
def test_bad_options_rejected(live, kw):
    with pytest.raises(InvalidInputError):
        _start(**kw)


def test_missing_key_is_dependency_error(live, monkeypatch):
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name: None)
    with pytest.raises(DependencyUnavailableError):
        _start(engine="claude")


def test_numbers_clamped(live, monkeypatch):
    seen = {}
    monkeypatch.setattr(live_translate, "run_live_job", lambda *a, **k: seen.update(a=a, k=k))
    sid = _start(segment_seconds=999, overlap_seconds=50, max_minutes=100000)
    assert _terminal(sid)
    assert seen["a"][3] == 60
    assert seen["k"]["overlap_seconds"] == 8
    assert seen["k"]["max_seconds"] == live_service.MAX_MINUTES_RANGE[1] * 60


# --- sessions, temp dirs, use_gpu -------------------------------------------

def test_each_start_gets_own_session_and_dir(live, monkeypatch):
    seen = []
    monkeypatch.setattr(live_translate, "run_live_job",
                        lambda *a, **k: seen.append((a[0], a[2], os.path.isdir(a[2]))))
    a = _start()
    assert _terminal(a)   # one session at a time: the second starts after the first ends
    b = _start()
    assert a != b and a.startswith("live_") and b.startswith("live_")
    assert _terminal(b)
    assert {s[0] for s in seen} == {a, b}
    assert seen[0][1] != seen[1][1] and all(s[2] for s in seen)
    assert all(not os.path.exists(s[1]) for s in seen)  # removed on finish


def test_second_start_while_one_runs_is_conflict(live):
    from services.service_errors import ConflictError
    a = _start()
    assert _wait(lambda: "out_dir" in live)
    with pytest.raises(ConflictError):
        _start()
    assert list(live_service._sessions) == [a]
    assert live["protocol_whitelist"] == live_service.FFMPEG_PROTOCOL_WHITELIST


def test_refused_second_start_builds_no_engine(live, monkeypatch):
    from services.service_errors import ConflictError
    _start()
    assert _wait(lambda: "out_dir" in live)
    built = []
    monkeypatch.setattr(live_service, "_build_engine", lambda *a: built.append(a))
    with pytest.raises(ConflictError):
        _start()
    assert built == []


def test_dir_removed_on_error_and_message_clean(live, monkeypatch):
    dirs = []

    def boom(job_id, url, out_dir, *a, **k):
        dirs.append(out_dir)
        raise RuntimeError(f"ffmpeg failed reading {out_dir}/chunk_00001.wav key sk-ant-abcdefghijklmnopqrstu")

    monkeypatch.setattr(live_translate, "run_live_job", boom)
    sid = _start()
    assert _terminal(sid)
    s = live_service.get_session(sid)
    assert s["status"] == "error"
    assert not os.path.exists(dirs[0])
    blob = repr(s)
    assert dirs[0] not in blob and "sk-ant" not in blob and "Traceback" not in blob
    assert "/tmp" not in blob and "chunk_00001" not in blob


def test_dir_removed_on_cancel_while_running(live):
    sid = _start()
    assert _wait(lambda: "out_dir" in live)
    out_dir = live["out_dir"]
    assert os.path.isdir(out_dir)
    live_service.stop_session(sid)
    assert _terminal(sid)
    assert live_service.get_session(sid)["status"] == "cancelled"
    assert not os.path.exists(out_dir)


def test_dir_removed_on_cancel_while_queued(live, monkeypatch):
    monkeypatch.setattr(background_jobs, "get_gpu_limit_enabled", lambda: True)
    monkeypatch.setattr(background_jobs, "_gpu_slot_available_locked", lambda *a: False)
    sid = _start()
    out_dir = live_service._sessions[sid]["dir"]
    assert background_jobs.get_status(sid)["status"] == "queued"
    live_service.stop_session(sid)
    assert not os.path.exists(out_dir)
    assert live_service.get_session(sid)["status"] == "cancelled"


def test_use_gpu_reaches_pipeline(live, monkeypatch, tmp_path):
    seen = []

    def fake_process(path, idx, seg, lang, size, engine, use_gpu=False, **k):
        seen.append(use_gpu)
        return []

    monkeypatch.setattr(live_translate, "process_chunk", fake_process)
    sid = _start(use_gpu=True, overlap_seconds=0)
    assert _wait(lambda: "out_dir" in live)
    for i in range(2):
        open(os.path.join(live["out_dir"], f"chunk_{i:05d}.wav"), "wb").close()
    assert _wait(lambda: seen, timeout=8)
    assert seen[0] is True
    live_service.stop_session(sid)


def test_job_is_gpu_touching(live):
    sid = _start()
    assert background_jobs.get_status(sid)["gpu_touching"] is True


# --- live_translate fixes ---------------------------------------------------

def test_stale_chunks_never_processed(live, monkeypatch, tmp_path):
    for i in range(3):
        (tmp_path / f"chunk_{i:05d}.wav").write_bytes(b"old")
    (tmp_path / "padded_00001.wav").write_bytes(b"old")
    seen = []
    monkeypatch.setattr(live_translate, "process_chunk",
                        lambda path, idx, *a, **k: seen.append(idx) or [])
    # The real capture starter (it does the clearing), with ffmpeg mocked.
    monkeypatch.setattr(live_translate.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(live_translate, "start_segment_capture", _REAL_CAPTURE)
    background_jobs.start_job("live_stale", live_translate.run_live_job, "live_stale", "u",
                              str(tmp_path), 10, "zh", "tiny", object(), poll_interval=0.01,
                              overlap_seconds=0, max_seconds=0.2)
    assert _wait(lambda: background_jobs.get_status("live_stale")["status"] != "running")
    assert seen == []
    assert not list(tmp_path.glob("*.wav"))


def test_max_minutes_stops_job(live, monkeypatch):
    monkeypatch.setattr(live_service, "_num",
                        lambda name, v, lo, hi, cast=float: 0.001 if name == "max_minutes"
                        else max(lo, min(hi, cast(v))))
    sid = _start()
    assert _terminal(sid)
    s = live_service.get_session(sid)
    assert s["status"] == "done"
    assert live["procs"][0].stopped


def test_stop_bumps_generation_before_cancel(live, monkeypatch):
    sid = _start()
    # run_live_job bumps first thing in its worker thread; let that land so
    # only stop_session's bump is recorded.
    assert _wait(lambda: live_service.get_session(sid)["message"] != "Starting...")
    order = []
    real_bump = live_translate.bump_generation
    monkeypatch.setattr(live_translate, "bump_generation",
                        lambda j: order.append("bump") or real_bump(j))
    real_cancel = background_jobs.request_cancel
    monkeypatch.setattr(background_jobs, "request_cancel",
                        lambda j: order.append("cancel") or real_cancel(j))
    monkeypatch.setattr(background_jobs, "cancel_queued",
                        lambda j: order.append("cancel_queued") or False)
    live_service.stop_session(sid)
    assert order == ["bump", "cancel_queued", "cancel"]


# --- get / list ----------------------------------------------------------------

def test_cue_slicing(live, monkeypatch):
    cues = [{"start": i, "end": i + 1, "text": f"t{i}", "translated": f"e{i}"} for i in range(5)]
    monkeypatch.setattr(live_translate, "run_live_job",
                        lambda job_id, *a, **k: background_jobs.set_result(job_id, cues))
    sid = _start()
    assert _terminal(sid)
    s = live_service.get_session(sid, after=3)
    assert [c["text"] for c in s["cues"]] == ["t3", "t4"] and s["next_index"] == 5
    assert live_service.get_session(sid)["next_index"] == 5
    assert live_service.get_session(sid, after=9)["cues"] == []
    with pytest.raises(InvalidInputError):
        live_service.get_session(sid, after="x")


def test_translation_failure_cue_is_cleaned(live, monkeypatch):
    cues = [{"start": 0, "end": 1, "text": "and/or 你好",
             "translated": "[translation failed: /home/k/.env sk-ant-abcdefghijklmnopqrstu]"}]
    monkeypatch.setattr(live_translate, "run_live_job",
                        lambda job_id, *a, **k: background_jobs.set_result(job_id, cues))
    sid = _start()
    assert _terminal(sid)
    c = live_service.get_session(sid)["cues"][0]
    assert c["text"] == "and/or 你好"
    assert "/home" not in c["translated"] and "sk-ant" not in c["translated"]


def test_unknown_session_404(live):
    with pytest.raises(NotFoundError):
        live_service.get_session("live_nope")
    with pytest.raises(NotFoundError):
        live_service.stop_session("live_nope")
    with pytest.raises(NotFoundError):
        live_service.get_session("diarize_1")  # a non-live job id is not a session


def test_list_sessions(live, monkeypatch):
    monkeypatch.setattr(live_translate, "run_live_job", lambda *a, **k: None)
    sid = _start()
    assert _terminal(sid)
    listed = live_service.list_sessions()
    assert [s["session_id"] for s in listed] == [sid]
    assert set(listed[0]) == {"session_id", "status", "engine", "cue_count"}


def test_url_guard_is_the_one_policy(live, monkeypatch):
    """Both the typed URL and the resolved stream URL go through
    services.url_guard.resolve_public (the B-25 policy)."""
    from services import url_guard
    called = []
    monkeypatch.setattr(url_guard, "resolve_public", lambda u: called.append(u) or "93.184.216.34")
    sid = _start()
    assert _wait(lambda: len(called) >= 2)
    assert called[0] == "https://www.youtube.com/watch?v=abc" and called[1] == "http://media"
    live_service.stop_session(sid)


def test_reap_keeps_a_session_that_is_still_starting(live, monkeypatch):
    """L3: between the reservation and start_job there is no job record;
    a concurrent _reap must not remove that session's directory."""
    real_start = background_jobs.start_job
    seen = {}

    def start_after_reap(job_id, *a, **k):
        live_service._reap()                       # another request's reap, mid-start
        seen["dir"] = live_service._sessions[job_id].get("dir")
        return real_start(job_id, *a, **k)
    monkeypatch.setattr(background_jobs, "start_job", start_after_reap)
    sid = _start()
    assert seen["dir"] and _wait(lambda: "out_dir" in live)
    assert "starting" not in live_service._sessions[sid]

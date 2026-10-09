"""
Parity audit B1 (inventory R23): re-transcribe one line.
transcribe_service.start_retranscribe_line / _run_retranscribe_line_job and
POST /api/transcribe/dramas/{id}/lines/{line_id}/retranscribe. ffmpeg and
Whisper are faked -- no audio model, no network.
"""

import inspect
import os
import subprocess
import time

import pytest

import background_jobs
import core
from core import Line
from services import bounded_whisper, jobs_service, transcribe_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)

SECRET = "sk-ant-api03-SECRETSECRETSECRETSECRET"
_PREFIXES = ("retranscribe_", "transcribe_", "fixflag_", "resegment_", "narration_",
             "translate_")


def _clear_jobs():
    with background_jobs._lock:
        for jid in [j for j in background_jobs._jobs if j.startswith(_PREFIXES)]:
            background_jobs._jobs.pop(jid, None)


@pytest.fixture(autouse=True)
def _no_leftover_jobs():
    _clear_jobs()
    yield
    _clear_jobs()


def _put_job(job_id, status="running"):
    with background_jobs._lock:
        background_jobs._jobs[job_id] = {
            "status": status, "progress": 0.0, "message": "", "result": None, "error": None,
            "started_at": time.time(), "finished_at": None, "cancel_requested": False}


def _drama(db, audio=True, content_mode=None, glossary=True):
    sid = db.get_or_create_series("S")
    if glossary:
        db.upsert_glossary_term(sid, "苏杉", "Su Shan")
    kw = {"content_mode": content_mode} if content_mode else {}
    did = db.create_drama(title_en="D", series_id=sid, audio_filename="audio.wav",
                          whisper_size="small", beam_size=7, source_language="zh", **kw)
    if audio:
        with open(os.path.join(db.drama_dir(did), "audio.wav"), "wb") as f:
            f.write(b"x")
    db.save_lines(did, [
        Line(idx=0, start=0.0, end=1.5, zh="你好", en="Hello", speaker="A",
             flag="unsure", flag_note="check"),
        Line(idx=1, start=1.5, end=3.0, zh="错字", en="Typo", speaker="B"),
    ])
    ids = [ln.id for ln in db.load_line_objects(did)]
    return did, ids


@pytest.fixture
def fake_asr(monkeypatch):
    """Fakes ffmpeg + Whisper; records the calls. `text` is what Whisper hears."""
    calls = {"text": "新的文字", "slices": [], "transcribe": [], "released": 0}

    def fake_slice(audio_path, start, end, out_path, timeout=None):
        calls["slices"].append((start, end))
        calls["slice_timeout"] = timeout
        with open(out_path, "wb") as f:
            f.write(b"slice")

    def fake_transcribe(path, model_size="medium", **kw):
        assert os.path.exists(path)
        calls["transcribe"].append({"model_size": model_size, **kw})
        t = calls["text"]
        return [{"start": 0.0, "end": 1.0, "text": t}] if t else []

    monkeypatch.setattr(core, "extract_audio_slice", fake_slice)
    monkeypatch.setattr(core, "transcribe_for_timing", fake_transcribe)
    monkeypatch.setattr(core, "is_whisper_model_cached", lambda size: True)
    monkeypatch.setattr(core, "release_gpu_models",
                        lambda: calls.__setitem__("released", calls["released"] + 1))
    return calls


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def fake_start_job(job_id, target, *a, **k):
        seen.update(job_id=job_id, target=target, kwargs=k,
                    args=dict(zip(list(inspect.signature(target).parameters), a)))
        return True
    monkeypatch.setattr(background_jobs, "start_job", fake_start_job)
    return seen


def _run_with_result(seen):
    """Runs the captured job body synchronously and returns its result."""
    results = {}
    orig = background_jobs.set_result
    background_jobs.set_result = lambda jid, r, **kw: results.__setitem__(jid, r)
    try:
        seen["target"](**seen["args"])
    finally:
        background_jobs.set_result = orig
    return results[seen["job_id"]]


# ----- service --------------------------------------------------------------

class TestStart:
    def test_starts_gpu_job_with_full_transcribe_settings(self, isolated_db, captured):
        did, ids = _drama(isolated_db)
        out = transcribe_service.start_retranscribe_line(did, ids[1], extra_names="沈清疑")
        assert out == {"job_id": f"retranscribe_{did}", "drama_id": did, "line_id": ids[1]}
        assert captured["kwargs"]["gpu_touching"] is True
        a = captured["args"]
        assert a["line_id"] == ids[1]
        assert (a["start"], a["end"], a["zh_before"]) == (1.5, 3.0, "错字")
        assert a["whisper_size"] == "small" and a["beam_size"] == 7
        assert a["source_language"] == "zh"
        # _resolve_initial_prompt: automatic prompt plus extra names (#393)
        assert a["initial_prompt"] == "苏杉、沈清疑。"

    def test_planted_whisper_size_falls_back_to_default(self, isolated_db, captured):
        import applog
        planted = "someone/evil-repo"
        did, ids = _drama(isolated_db)
        isolated_db.update_drama(did, whisper_size=planted)
        transcribe_service.start_retranscribe_line(did, ids[1])
        assert captured["args"]["whisper_size"] == core.DEFAULT_WHISPER_SIZE
        logged = "\n".join(applog.tail(20))
        assert "whisper_size" in logged and planted not in logged

    @pytest.mark.parametrize("stored,expected", [
        (None, "large-v3-turbo"), ("", "large-v3-turbo"),
        ("small", "small"), ("../models/x", "large-v3-turbo"),
        ("org/model", "large-v3-turbo"), ("medium", "medium"), ("large-v3", "large-v3")])
    def test_stored_whisper_size(self, isolated_db, stored, expected):
        assert transcribe_service.stored_whisper_size(
            {"id": 1, "whisper_size": stored}) == expected

    def test_default_is_turbo_on_cpu_and_gpu_and_saved_choice_never_changes(self, isolated_db, monkeypatch):
        for gpu in (True, False):
            monkeypatch.setattr(transcribe_service.settings_service, "get_use_gpu", lambda gpu=gpu: gpu)
            assert transcribe_service.stored_whisper_size({"id": 1, "whisper_size": ""}) == "large-v3-turbo"
            assert transcribe_service.stored_whisper_size({"id": 1, "whisper_size": "medium"}) == "medium"

    def test_explicit_prompt_overrides(self, isolated_db, captured):
        did, ids = _drama(isolated_db)
        transcribe_service.start_retranscribe_line(did, ids[0], initial_prompt="人名")
        assert captured["args"]["initial_prompt"] == "人名"

    def test_unknown_drama_and_foreign_line_404(self, isolated_db, captured):
        did, ids = _drama(isolated_db)
        other, other_ids = _drama(isolated_db)
        with pytest.raises(NotFoundError):
            transcribe_service.start_retranscribe_line(999999, ids[0])
        with pytest.raises(NotFoundError):
            transcribe_service.start_retranscribe_line(did, other_ids[0])
        with pytest.raises(NotFoundError):
            transcribe_service.start_retranscribe_line(did, 999999)
        assert "job_id" not in captured

    def test_no_audio_or_no_pipeline_or_no_window(self, isolated_db, captured):
        did, ids = _drama(isolated_db, audio=False)
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_retranscribe_line(did, ids[0])
        did2, ids2 = _drama(isolated_db, content_mode="novel_narration")
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_retranscribe_line(did2, ids2[0])
        did3, _ = _drama(isolated_db)
        isolated_db.save_lines(did3, [Line(idx=5, start=2.0, end=2.0, zh="x")])
        zero = [ln.id for ln in isolated_db.load_line_objects(did3) if ln.idx == 5][0]
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_retranscribe_line(did3, zero)
        assert "job_id" not in captured

    def test_non_text_prompt_422(self, isolated_db, captured):
        did, ids = _drama(isolated_db)
        with pytest.raises(InvalidInputError):
            transcribe_service.start_retranscribe_line(did, ids[0], initial_prompt=None)

    @pytest.mark.parametrize("prefix", ["transcribe_", "fixflag_", "resegment_", "narration_"])
    @pytest.mark.parametrize("status", ["running", "queued"])
    def test_409_while_another_line_writing_job_runs(self, isolated_db, captured, prefix,
                                                     status):
        did, ids = _drama(isolated_db)
        _put_job(f"{prefix}{did}", status)
        with pytest.raises(ConflictError):
            transcribe_service.start_retranscribe_line(did, ids[0])
        assert "job_id" not in captured

    def test_en_only_jobs_do_not_block(self, isolated_db, captured):
        did, ids = _drama(isolated_db)
        _put_job(f"translate_{did}")
        transcribe_service.start_retranscribe_line(did, ids[0])
        assert captured["job_id"] == f"retranscribe_{did}"

    def test_409_while_already_retranscribing(self, isolated_db):
        did, ids = _drama(isolated_db)
        _put_job(f"retranscribe_{did}")
        with pytest.raises(ConflictError):
            transcribe_service.start_retranscribe_line(did, ids[0])

    def test_is_a_line_writing_job(self):
        # a full transcription cancels it (cancel_line_jobs), a drama delete waits for it
        assert "retranscribe_" in background_jobs.LINE_WRITING_JOB_PREFIXES
        assert "retranscribe_" in background_jobs.DRAMA_JOB_PREFIXES


class TestJobBody:
    def test_proposes_and_writes_nothing(self, isolated_db, captured, fake_asr):
        did, ids = _drama(isolated_db)
        before = [(ln.id, ln.zh, ln.en, ln.start, ln.end, ln.flag)
                  for ln in isolated_db.load_line_objects(did)]
        transcribe_service.start_retranscribe_line(did, ids[0])
        result = _run_with_result(captured)
        assert result == {"line_id": ids[0], "proposed_zh": "新的文字", "base_zh": "你好",
                          "base_start": 0.0, "base_end": 1.5}
        after = [(ln.id, ln.zh, ln.en, ln.start, ln.end, ln.flag)
                 for ln in isolated_db.load_line_objects(did)]
        assert after == before
        assert isolated_db.list_line_history(did) == []
        assert fake_asr["slices"] == [(0.0, 1.5)]
        assert fake_asr["slice_timeout"] == 120
        t = fake_asr["transcribe"][0]
        assert t["model_size"] == "small" and t["beam_size"] == 7
        assert t["initial_prompt"] == "苏杉。" and t["language"] == "zh"
        assert fake_asr["released"] == 1
        assert not [f for f in os.listdir(isolated_db.drama_dir(did)) if "slice" in f]

    def test_line_language_wins_over_title_language(self, isolated_db, captured, fake_asr):
        did, ids = _drama(isolated_db)
        lines = isolated_db.load_line_objects(did)
        lines[0].lang = "en"
        isolated_db.save_lines(did, lines, fields=("lang",))
        transcribe_service.start_retranscribe_line(did, ids[0])
        _run_with_result(captured)
        transcribe_service.start_retranscribe_line(did, ids[1])
        _run_with_result(captured)
        assert [t["language"] for t in fake_asr["transcribe"]] == ["en", "zh"]

    def test_empty_proposes_nothing(self, isolated_db, captured, fake_asr):
        fake_asr["text"] = "  "
        did, ids = _drama(isolated_db)
        transcribe_service.start_retranscribe_line(did, ids[0])
        assert _run_with_result(captured) == {"line_id": ids[0], "failed_reason": "empty"}
        assert isolated_db.load_line_objects(did)[0].zh == "你好"

    def test_line_gone(self, isolated_db, captured, fake_asr):
        did, ids = _drama(isolated_db)
        transcribe_service.start_retranscribe_line(did, ids[0])
        isolated_db.save_lines(did, [ln for ln in isolated_db.load_line_objects(did)
                                     if ln.id != ids[0]])
        assert _run_with_result(captured)["failed_reason"] == "line_gone"
        assert [ln.id for ln in isolated_db.load_line_objects(did)] == [ids[1]]

    def test_cancelled(self, isolated_db, captured, fake_asr, monkeypatch):
        did, ids = _drama(isolated_db)
        transcribe_service.start_retranscribe_line(did, ids[0])
        monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda jid: True)
        assert _run_with_result(captured) == {"line_id": ids[0], "failed_reason": "cancelled"}

    def test_cancel_ends_a_transcribe_call_that_never_returns(self, isolated_db, fake_asr,
                                                              monkeypatch):
        import threading
        entered, release = threading.Event(), threading.Event()

        def hung(path, model_size="medium", **kw):
            entered.set()
            release.wait(30)
            return []
        monkeypatch.setattr(core, "transcribe_for_timing", hung)
        did, ids = _drama(isolated_db)
        out = transcribe_service.start_retranscribe_line(did, ids[0])
        try:
            assert entered.wait(5)
            background_jobs.request_cancel(out["job_id"])
            deadline = time.time() + 3
            while background_jobs.get_status(out["job_id"])["status"] == "running":
                assert time.time() < deadline, "Cancel did not end the job"
                time.sleep(0.02)
            status = background_jobs.get_status(out["job_id"])
            assert status["status"] == "cancelled"
            # The slot is free: the next re-transcription can start.
            assert transcribe_service.start_retranscribe_line(did, ids[1])["job_id"] == out["job_id"]
        finally:
            release.set()
            background_jobs.wait_for_job_threads(5)

    def test_hung_transcribe_times_out_with_a_reason(self, isolated_db, fake_asr, monkeypatch):
        import threading
        release = threading.Event()

        def hung(path, model_size="medium", **kw):
            release.wait(30)
            return []
        monkeypatch.setattr(core, "transcribe_for_timing", hung)
        monkeypatch.setattr(bounded_whisper, "timeout_s", lambda window, cached: 0.3)
        did, ids = _drama(isolated_db)
        out = transcribe_service.start_retranscribe_line(did, ids[0])
        try:
            deadline = time.time() + 5
            while background_jobs.get_status(out["job_id"])["status"] == "running":
                assert time.time() < deadline, "the call was never bounded"
                time.sleep(0.02)
            result = background_jobs.get_status(out["job_id"])["result"]
            assert result["failed_reason"] == "timeout"
            assert result["line_id"] == ids[0]
        finally:
            release.set()
            background_jobs.wait_for_job_threads(5)

    def test_timeout_scales_with_the_window_and_a_cold_model(self):
        fn = bounded_whisper.timeout_s
        assert fn(60.0, True) > fn(3.0, True)
        assert fn(3.0, False) > fn(3.0, True)

    def test_model_download_error_redacted(self, isolated_db, captured, fake_asr, monkeypatch):
        def boom(*a, **k):
            raise core.ModelDownloadError(f"download failed token={SECRET}")
        monkeypatch.setattr(core, "transcribe_for_timing", boom)
        did, ids = _drama(isolated_db)
        transcribe_service.start_retranscribe_line(did, ids[0])
        result = _run_with_result(captured)
        assert result["failed_reason"] == "model_download"
        assert SECRET not in result["detail"]
        assert fake_asr["released"] == 1

    def test_slice_failure_has_no_path(self, isolated_db, captured, fake_asr, monkeypatch):
        did, ids = _drama(isolated_db)

        def bad_slice(audio_path, *a, **k):
            raise RuntimeError(f"ffmpeg failed on {audio_path}")
        monkeypatch.setattr(core, "extract_audio_slice", bad_slice)
        transcribe_service.start_retranscribe_line(did, ids[0])
        result = _run_with_result(captured)
        assert result["failed_reason"] == "audio_slice"
        assert isolated_db.drama_dir(did) not in str(result)

    def test_ffmpeg_timeout_ends_the_job(self, isolated_db, fake_asr, monkeypatch):
        seen = {}

        def slow_slice(audio_path, start, end, out_path, timeout=None):
            seen["timeout"] = timeout
            raise subprocess.TimeoutExpired(["ffmpeg", audio_path], timeout)
        monkeypatch.setattr(core, "extract_audio_slice", slow_slice)
        did, ids = _drama(isolated_db)
        out = _finish(transcribe_service.start_retranscribe_line(did, ids[0]))
        assert seen["timeout"] == 120
        rec = jobs_service.get_job(out["job_id"])
        assert rec["result"]["failed_reason"] == "audio_slice"
        assert rec["outcome"] == "failed"
        assert isolated_db.drama_dir(did) not in str(rec)
        assert fake_asr["transcribe"] == []
        assert fake_asr["released"] == 1

    def test_line_id_is_visible_while_running(self, isolated_db, fake_asr, monkeypatch):
        import threading
        started, release = threading.Event(), threading.Event()

        def held_slice(audio_path, start, end, out_path, timeout=None):
            started.set()
            assert release.wait(5)
            with open(out_path, "wb") as f:
                f.write(b"slice")
        monkeypatch.setattr(core, "extract_audio_slice", held_slice)
        did, ids = _drama(isolated_db)
        out = transcribe_service.start_retranscribe_line(did, ids[1])
        try:
            assert started.wait(5)
            rec = jobs_service.get_job(out["job_id"])
            assert rec["status"] == "running"
            assert rec["result"] == {"line_id": ids[1]}
        finally:
            release.set()
        _finish(out)

    def test_real_thread_projection(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        out = _finish(transcribe_service.start_retranscribe_line(did, ids[1]))
        rec = jobs_service.get_job(out["job_id"])
        assert rec["outcome"] == "ok"
        # Only line_id goes out; the line text is read with GET .../retranscribe.
        assert rec["result"] == {"line_id": ids[1]}
        assert "新的文字" not in str(rec) and "错字" not in str(rec)
        assert isolated_db.drama_dir(did) not in str(rec)
        assert isolated_db.load_line_objects(did)[1].zh == "错字"


def _finish(out):
    deadline = time.time() + 5
    while background_jobs.get_status(out["job_id"])["status"] in ("running", "queued"):
        assert time.time() < deadline
        time.sleep(0.01)
    assert background_jobs.get_status(out["job_id"])["status"] == "done"
    return out


def _proposed(db, fake_asr, n=0, **kw):
    did, ids = _drama(db, **kw)
    out = _finish(transcribe_service.start_retranscribe_line(did, ids[n]))
    return did, ids, out["job_id"]


class TestJobsProjection:
    def test_line_text_is_never_in_a_job_record(self):
        p = jobs_service.project_result({"line_id": 4, "proposed_zh": "字", "base_zh": "字",
                                         "base_start": 1.0, "base_end": 2.0})
        assert p == {"line_id": 4}


class TestGetResult:
    def test_returns_raw_proposal(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        lines = isolated_db.load_line_objects(did)
        lines[0].zh = "见 /home/x/y/z 文件"
        isolated_db.save_lines(did, lines)
        job_id = _finish(transcribe_service.start_retranscribe_line(did, ids[0]))["job_id"]
        assert transcribe_service.get_retranscribe_result(did, ids[0]) == {
            "job_id": job_id, "line_id": ids[0], "status": "done",
            "proposed_zh": "新的文字", "base_zh": "见 /home/x/y/z 文件"}

    def test_404_for_other_line_running_failed_or_none(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        with pytest.raises(NotFoundError):
            transcribe_service.get_retranscribe_result(did, ids[0])
        _put_job(f"retranscribe_{did}")
        with pytest.raises(NotFoundError):
            transcribe_service.get_retranscribe_result(did, ids[0])
        _clear_jobs()
        _finish(transcribe_service.start_retranscribe_line(did, ids[0]))
        with pytest.raises(NotFoundError):
            transcribe_service.get_retranscribe_result(did, ids[1])
        with pytest.raises(NotFoundError):
            transcribe_service.get_retranscribe_result(999999, ids[0])
        fake_asr["text"] = ""
        _clear_jobs()
        _finish(transcribe_service.start_retranscribe_line(did, ids[0]))
        with pytest.raises(NotFoundError):
            transcribe_service.get_retranscribe_result(did, ids[0])


class TestApply:
    def test_writes_only_zh_of_that_line_by_id_with_history(self, isolated_db, fake_asr):
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        out = transcribe_service.apply_retranscribe_line(did, ids[0], job_id, "你好", "新的文字")
        assert out == {"drama_id": did, "line_id": ids[0], "zh": "新的文字"}
        lines = {ln.id: ln for ln in isolated_db.load_line_objects(did)}
        assert lines[ids[0]].zh == "新的文字"
        # everything else untouched: en, speaker, flag, timing, the other line
        assert (lines[ids[0]].en, lines[ids[0]].speaker) == ("Hello", "A")
        assert (lines[ids[0]].flag, lines[ids[0]].flag_note) == ("unsure", "check")
        assert (lines[ids[0]].start, lines[ids[0]].end) == (0.0, 1.5)
        assert lines[ids[1]].zh == "错字"
        hist = isolated_db.list_line_history(did)
        assert len(hist) == 1 and "re-transcrib" in hist[0]["label"]
        # applied once: a second apply sees changed text
        with pytest.raises(ConflictError):
            transcribe_service.apply_retranscribe_line(did, ids[0], job_id, "你好", "新的文字")

    def test_matched_by_id_after_lines_move(self, isolated_db, fake_asr):
        did, ids, job_id = _proposed(isolated_db, fake_asr, n=1)
        lines = isolated_db.load_line_objects(did)
        for ln in lines:
            ln.idx += 1
        lines.insert(0, Line(idx=0, start=0.0, end=0.1, zh="新行"))
        isolated_db.save_lines(did, lines)
        transcribe_service.apply_retranscribe_line(did, ids[1], job_id, "错字", "新的文字")
        by_id = {ln.id: ln.zh for ln in isolated_db.load_line_objects(did)}
        assert by_id[ids[1]] == "新的文字" and by_id[ids[0]] == "你好"

    def test_expected_mismatch_409(self, isolated_db, fake_asr):
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        with pytest.raises(ConflictError):
            transcribe_service.apply_retranscribe_line(did, ids[0], job_id, "别的", "新的文字")
        assert isolated_db.load_line_objects(did)[0].zh == "你好"
        assert isolated_db.list_line_history(did) == []

    @pytest.mark.parametrize("change", ["zh", "end"])
    def test_line_changed_since_job_409_user_edit_wins(self, isolated_db, fake_asr, change):
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        lines = isolated_db.load_line_objects(did)
        if change == "zh":
            lines[0].zh = "用户改的"
        else:
            lines[0].end = 1.2
        isolated_db.save_lines(did, lines)
        with pytest.raises(ConflictError):
            transcribe_service.apply_retranscribe_line(did, ids[0], job_id, "你好", "新的文字")
        assert isolated_db.load_line_objects(did)[0].zh == ("用户改的" if change == "zh" else "你好")

    def test_race_between_check_and_write_409(self, isolated_db, fake_asr, monkeypatch):
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        real = isolated_db.update_line_fields_if

        def edit_first(drama_id, line_id, values, expected):
            lines = isolated_db.load_line_objects(drama_id)
            lines[0].zh = "抢先"
            isolated_db.save_lines(drama_id, lines, fields=("zh",))
            return real(drama_id, line_id, values, expected)
        monkeypatch.setattr(isolated_db, "update_line_fields_if", edit_first)
        with pytest.raises(ConflictError):
            transcribe_service.apply_retranscribe_line(did, ids[0], job_id, "你好", "新的文字")
        assert isolated_db.load_line_objects(did)[0].zh == "抢先"

    def test_proposal_mismatch_409(self, isolated_db, fake_asr):
        """An apply is tied to the run the user saw: another proposal is refused."""
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        with pytest.raises(ConflictError):
            transcribe_service.apply_retranscribe_line(did, ids[0], job_id, "你好", "旧的提议")
        assert isolated_db.load_line_objects(did)[0].zh == "你好"

    def test_raw_text_with_a_path_applies_exactly(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        fake_asr["text"] = "在 /home/x/y 里"
        lines = isolated_db.load_line_objects(did)
        lines[0].zh = "见 /home/x/y/z 文件"
        isolated_db.save_lines(did, lines)
        job_id = _finish(transcribe_service.start_retranscribe_line(did, ids[0]))["job_id"]
        shown = transcribe_service.get_retranscribe_result(did, ids[0])
        transcribe_service.apply_retranscribe_line(did, ids[0], job_id, shown["base_zh"],
                                                   shown["proposed_zh"])
        assert isolated_db.load_line_objects(did)[0].zh == "在 /home/x/y 里"

    def test_404s(self, isolated_db, fake_asr):
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        with pytest.raises(NotFoundError):
            transcribe_service.apply_retranscribe_line(999999, ids[0], job_id, "你好", "新的文字")
        with pytest.raises(NotFoundError):
            transcribe_service.apply_retranscribe_line(did, 999999, job_id, "你好", "新的文字")
        # a finished job for another line of this drama
        with pytest.raises(NotFoundError):
            transcribe_service.apply_retranscribe_line(did, ids[1], job_id, "错字", "新的文字")
        _clear_jobs()
        with pytest.raises(NotFoundError):
            transcribe_service.apply_retranscribe_line(did, ids[0], job_id, "你好", "新的文字")

    def test_running_or_failed_job_404(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _put_job(f"retranscribe_{did}")
        with pytest.raises(NotFoundError):
            transcribe_service.apply_retranscribe_line(did, ids[0], f"retranscribe_{did}", "你好", "新的文字")
        fake_asr["text"] = ""
        _clear_jobs()
        job_id = _finish(transcribe_service.start_retranscribe_line(did, ids[0]))["job_id"]
        with pytest.raises(NotFoundError):
            transcribe_service.apply_retranscribe_line(did, ids[0], job_id, "你好", "新的文字")

    def test_other_drama_job_id_422(self, isolated_db, fake_asr):
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        with pytest.raises(InvalidInputError):
            transcribe_service.apply_retranscribe_line(did, ids[0], "translate_1", "你好", "新的文字")
        with pytest.raises(InvalidInputError):
            transcribe_service.apply_retranscribe_line(did, ids[0], job_id, "你好", None)


# ----- API ------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from api import auth as api_auth  # noqa: E402
from api.api_config import ApiSettings  # noqa: E402
from api.server import create_app  # noqa: E402
from services import auth_service  # noqa: E402

REMOTE = "https://baihe.example.com"


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})  # as the React client sends


def _path(did, lid):
    return f"/api/transcribe/dramas/{did}/lines/{lid}/retranscribe"


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    return body["error"]


class TestRoute:
    def test_success_with_and_without_body(self, client, isolated_db, captured):
        did, ids = _drama(isolated_db)
        r = client.post(_path(did, ids[0]))
        assert r.status_code == 200, r.text
        assert r.json() == {"job_id": f"retranscribe_{did}", "drama_id": did, "line_id": ids[0]}
        r = client.post(_path(did, ids[0]), json={"extra_names": "沈清疑"})
        assert r.status_code == 200, r.text
        assert captured["args"]["initial_prompt"] == "苏杉、沈清疑。"
        assert isolated_db.drama_dir(did) not in r.text
        assert "audio.wav" not in r.text

    def test_404s(self, client, isolated_db, captured):
        did, ids = _drama(isolated_db)
        assert client.post(_path(999999, ids[0])).status_code == 404
        r = client.post(_path(did, 999999))
        assert r.status_code == 404
        assert _error(r)["code"] == "not_found"

    def test_409_while_transcribing(self, client, isolated_db, captured):
        did, ids = _drama(isolated_db)
        _put_job(f"transcribe_{did}")
        r = client.post(_path(did, ids[0]))
        assert r.status_code == 409
        assert _error(r)

    def test_400_without_audio(self, client, isolated_db, captured):
        did, ids = _drama(isolated_db, audio=False)
        r = client.post(_path(did, ids[0]))
        assert r.status_code == 400
        assert isolated_db.drama_dir(did) not in r.text

    @pytest.mark.parametrize("body", [{"bogus": 1}, {"initial_prompt": "x" * 1001},
                                      {"extra_names": 5}])
    def test_422_bad_body(self, client, isolated_db, captured, body):
        did, ids = _drama(isolated_db)
        assert client.post(_path(did, ids[0]), json=body).status_code == 422
        assert "job_id" not in captured

    def test_422_bad_ids(self, client, isolated_db):
        assert client.post(_path(0, 1)).status_code == 422
        assert client.post(_path(1, 0)).status_code == 422


def _apply_path(did, lid):
    return _path(did, lid) + "/apply"


class TestApplyRoute:
    def test_success_then_409(self, client, isolated_db, fake_asr):
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        body = {"job_id": job_id, "expected_zh": "你好", "expected_proposed": "新的文字"}
        r = client.post(_apply_path(did, ids[0]), json=body)
        assert r.status_code == 200, r.text
        assert r.json() == {"drama_id": did, "line_id": ids[0], "zh": "新的文字"}
        assert isolated_db.drama_dir(did) not in r.text
        r = client.post(_apply_path(did, ids[0]), json=body)
        assert r.status_code == 409
        assert _error(r)["code"] == "conflict"

    def test_expected_mismatch_409(self, client, isolated_db, fake_asr):
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        r = client.post(_apply_path(did, ids[0]), json={"job_id": job_id, "expected_zh": "x", "expected_proposed": "新的文字"})
        assert r.status_code == 409
        assert isolated_db.load_line_objects(did)[0].zh == "你好"

    def test_404_and_422(self, client, isolated_db, fake_asr):
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        assert client.post(_apply_path(did, 999999),
                           json={"job_id": job_id, "expected_zh": "你好", "expected_proposed": "新的文字"}).status_code == 404
        assert client.post(_apply_path(did, ids[0]),
                           json={"job_id": "translate_1", "expected_zh": "你好", "expected_proposed": "新的文字"}).status_code == 422
        for body in ({}, {"job_id": job_id}, {"job_id": job_id, "expected_zh": "你好"},
                     {"job_id": job_id, "expected_zh": "你好", "expected_proposed": "新的文字", "x": 1},
                     {"job_id": "", "expected_zh": "你好", "expected_proposed": "新的文字"},
                     {"job_id": job_id, "expected_zh": "你好", "expected_proposed": ""}):
            assert client.post(_apply_path(did, ids[0]), json=body).status_code == 422
        assert isolated_db.load_line_objects(did)[0].zh == "你好"


class TestGetRoute:
    def test_success_and_404(self, client, isolated_db, fake_asr):
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        r = client.get(_path(did, ids[0]))
        assert r.status_code == 200, r.text
        assert r.json() == {"job_id": job_id, "line_id": ids[0], "status": "done",
                            "proposed_zh": "新的文字", "base_zh": "你好"}
        assert isolated_db.drama_dir(did) not in r.text
        assert client.get(_path(did, ids[1])).status_code == 404
        assert client.get(_path(did, 999999)).status_code == 404
        assert client.get(_path(0, ids[0])).status_code == 422


class TestWhisperSizeAllowlist:
    @pytest.mark.parametrize("size", ["tiny", "base", "small", "medium", "large-v3",
                                      "large-v3-turbo"])
    def test_picker_sizes_accepted(self, client, isolated_db, size):
        did, _ = _drama(isolated_db)
        r = client.post(f"/api/transcribe/dramas/{did}/config", json={"whisper_size": size})
        assert r.status_code == 200, r.text
        assert r.json()["whisper_size"] == size

    @pytest.mark.parametrize("size", ["someone/evil-repo", "../x", "large-v9", ""])
    def test_other_values_422(self, client, isolated_db, size):
        did, _ = _drama(isolated_db)
        r = client.post(f"/api/transcribe/dramas/{did}/config", json={"whisper_size": size})
        assert r.status_code == 422
        assert isolated_db.get_drama(did)["whisper_size"] == "small"
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, whisper_size=size)


def _remote():
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                      raise_server_exceptions=False)


def _headers(u):
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


class TestAuthOn:
    def test_no_session_401(self, isolated_db, captured):
        did, ids = _drama(isolated_db)
        assert _remote().post(_path(did, ids[0])).status_code == 401
        assert "job_id" not in captured

    def test_without_jobs_start_403_with_it_200(self, isolated_db, captured):
        did, ids = _drama(isolated_db)
        c = _remote()
        u = auth_service.add_user("bare@example.com")
        for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
            auth_service.revoke_permission(u["id"], p)
        h = _headers(u)
        assert c.post(_path(did, ids[0]), headers=h).status_code == 403
        assert "job_id" not in captured
        auth_service.grant_permission(u["id"], "jobs.start")
        r = c.post(_path(did, ids[0]), headers=h)
        assert r.status_code == 200, r.text
        assert captured["job_id"] == f"retranscribe_{did}"

    def test_apply_needs_lines_edit(self, isolated_db, fake_asr):
        did, ids, job_id = _proposed(isolated_db, fake_asr)
        c = _remote()
        body = {"job_id": job_id, "expected_zh": "你好", "expected_proposed": "新的文字"}
        assert c.post(_apply_path(did, ids[0]), json=body).status_code == 401
        u = auth_service.add_user("bare2@example.com")
        for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
            auth_service.revoke_permission(u["id"], p)
        auth_service.grant_permission(u["id"], "jobs.start")
        h = _headers(u)
        assert c.post(_apply_path(did, ids[0]), json=body, headers=h).status_code == 403
        assert isolated_db.load_line_objects(did)[0].zh == "你好"
        auth_service.grant_permission(u["id"], "lines.edit")
        r = c.post(_apply_path(did, ids[0]), json=body, headers=h)
        assert r.status_code == 200, r.text
        assert isolated_db.load_line_objects(did)[0].zh == "新的文字"

    def test_get_needs_lines_read(self, isolated_db, fake_asr):
        did, ids, _job = _proposed(isolated_db, fake_asr)
        c = _remote()
        assert c.get(_path(did, ids[0])).status_code == 401
        u = auth_service.add_user("bare3@example.com")
        for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
            auth_service.revoke_permission(u["id"], p)
        h = _headers(u)
        assert c.get(_path(did, ids[0]), headers=h).status_code == 403
        auth_service.grant_permission(u["id"], "lines.read")
        r = c.get(_path(did, ids[0]), headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["proposed_zh"] == "新的文字"

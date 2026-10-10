"""
Review "Check timing": line times against detected speech, the timing_drift
flag, snap to speech, dismissals and the automatic run. The detector and
ffmpeg are faked with fixed speech spans -- no audio, no model.
"""
import json
import os
import subprocess
import time

import pytest

import background_jobs
import timing_drift
from core import Line
from services import speech_coverage_service, timing_check_service as svc
from services.service_errors import ConflictError, InvalidInputError, NotFoundError, UnsupportedOperationError

FLAG = timing_drift.TIMING_DRIFT_FLAG


def L(i, start, end, **kw):
    return Line(idx=i, start=start, end=end, zh="x", id=i + 1, **kw)


class TestFindDrift:
    SPEECH = [(10.0, 14.0), (20.0, 24.0), (30.0, 34.0)]

    def notes(self, lines, spans=None, **kw):
        report = timing_drift.find_drift(lines, spans or self.SPEECH, 100.0, **kw)
        return {f.line_id: f for f in report.findings}

    def test_line_matching_speech_is_not_flagged(self):
        assert self.notes([L(0, 9.8, 14.3), L(1, 20.0, 24.0)]) == {}

    def test_starts_before_speech_with_snap_suggestion(self):
        found = self.notes([L(0, 8.0, 14.0)])
        assert found[1].note.startswith("Starts 2.0 s before speech.")
        assert found[1].suggestion == (9.9, 14.0)

    def test_suggestion_stays_inside_the_line_after_rounding(self):
        found = self.notes([L(0, 8.0, 14.237)], spans=[(10.0, 14.5)])
        start, end = found[1].suggestion
        assert (start, end) == (9.9, 14.237)

    def test_rounding_cannot_grow_a_line_or_pass_the_shift_cap(self):
        found = self.notes([L(0, 8.001, 14.0)], spans=[(10.0, 14.0)])
        start, end = found[1].suggestion
        assert start >= 8.001 and start - 8.001 <= timing_drift.SNAP_MAX_SHIFT_S

    def test_a_shift_just_under_the_cap_stays_within_it_once_rounded(self):
        # Wanted start is 10.997, a 2.996 s shift that rounds to 11.0 (2.999 s).
        line = L(0, 8.001, 14.0)
        suggestion = timing_drift._suggest(line, [(11.097, 14.0)])
        assert suggestion and suggestion[0] - line.start <= timing_drift.SNAP_MAX_SHIFT_S
        assert suggestion[0] >= line.start

    def test_small_lead_is_tolerated(self):
        assert self.notes([L(0, 9.4, 14.0)]) == {}

    def test_ends_after_speech(self):
        found = self.notes([L(0, 10.0, 16.0)])
        assert "Ends 2.0 s after speech" in found[1].note
        assert found[1].suggestion == (10.0, 14.1)

    def test_mostly_silence(self):
        found = self.notes([L(0, 10.0, 20.0)], spans=[(10.0, 12.0), (30.0, 34.0)])
        assert "mostly silence (20% speech)" in found[1].note

    def test_fully_in_silence_has_no_suggestion(self):
        found = self.notes([L(0, 15.0, 18.0)])
        assert "Sits in silence" in found[1].note and found[1].suggestion is None

    def test_overlap_is_blamed_on_the_earlier_line(self):
        found = self.notes([L(0, 10.0, 14.0), L(1, 12.5, 14.0)])
        assert set(found) == {1}
        assert "Overlaps the next line by 1.5 s" in found[1].note

    def test_sfx_lines_are_not_judged(self):
        assert self.notes([L(0, 15.0, 18.0, sfx=True)]) == {}

    def test_dismissed_lines_are_skipped(self):
        assert self.notes([L(0, 15.0, 18.0)], dismissed={1}) == {}

    def test_music_caveat_only_when_conservative_and_tolerances_widen(self):
        line = L(0, 9.0, 14.0)
        assert "Speech detection can mistake music" not in self.notes([line])[1].note
        assert self.notes([line], conservative=True) == {}
        far = self.notes([L(0, 8.0, 14.0)], conservative=True)
        assert "mistake music for speech" in far[1].note

    def test_no_speech_in_the_file_is_one_notice_not_flags(self):
        report = timing_drift.find_drift([L(0, 1.0, 2.0)], [], 100.0)
        assert report.broken_audio and report.findings == []
        barely = timing_drift.find_drift([L(0, 1.0, 2.0)], [(5.0, 5.5)], 100.0)
        assert barely.broken_audio

    def test_snap_never_grows_a_line_or_moves_far(self):
        lines = [L(0, 1.0, 14.0)]
        found = self.notes(lines, spans=[(10.0, 14.0)])
        # 9 s lead is beyond the safe window: the start stays, only a person can place it.
        assert found[1].suggestion is None


@pytest.fixture
def drama(isolated_db):
    did = isolated_db.create_drama(title_en="D", audio_filename="audio.wav")
    open(os.path.join(isolated_db.drama_dir(did), "audio.wav"), "wb").close()
    return did


@pytest.fixture(autouse=True)
def _no_leftover_jobs():
    def clear():
        with background_jobs._lock:
            for jid in [j for j in background_jobs._jobs if j.startswith("timingchk_")]:
                background_jobs._jobs.pop(jid, None)
    clear()
    yield
    clear()


@pytest.fixture
def fake_speech(monkeypatch):
    state = {"spans": [(10.0, 14.0), (20.0, 24.0)], "total": 100.0}
    monkeypatch.setattr(speech_coverage_service, "_audio_seconds", lambda path: state["total"])
    monkeypatch.setattr(speech_coverage_service, "_scan_speech",
                        lambda job_id, path, total: state["spans"])
    return state


def _seed(db, did, lines):
    db.save_lines(did, lines)
    return [ln.id for ln in db.load_line_objects(did)]


def _wait(drama_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        status = svc.get_timing_check(drama_id)
        if status["status"] in ("done", "error", "cancelled"):
            return status
        time.sleep(0.02)
    raise AssertionError("timing check did not finish")


def _lines(db, did):
    return {ln.idx: ln for ln in db.load_line_objects(did)}


class TestJob:
    def test_flags_drifting_lines_and_clears_ones_that_are_fine_again(self, isolated_db, drama, fake_speech):
        _seed(isolated_db, drama, [
            Line(idx=0, start=8.0, end=14.0, zh="a"),
            Line(idx=1, start=20.0, end=24.0, zh="b", flag=FLAG, flag_note="old")])
        svc.start_timing_check(drama)
        status = _wait(drama)
        assert status["status"] == "done", status["message"]
        assert status["result"]["flagged"] == 1 and status["result"]["cleared"] == 1
        lines = _lines(isolated_db, drama)
        assert lines[0].flag == FLAG and "Starts 2.0 s before speech" in lines[0].flag_note
        assert lines[1].flag is None and lines[1].flag_note == ""
        assert [s["line_id"] for s in status["suggestions"]] == [lines[0].id]
        assert status["last_check"]["flagged"] == 1

    def test_writes_only_flag_fields_and_keeps_other_flags(self, isolated_db, drama, fake_speech, monkeypatch):
        _seed(isolated_db, drama, [
            Line(idx=0, start=8.0, end=14.0, zh="a", en="keep", flag="name_uncertain", flag_note="n"),
            Line(idx=1, start=15.0, end=18.0, zh="b", en="mine")])
        saved = []
        real = isolated_db.save_lines
        monkeypatch.setattr(svc.db, "save_lines", lambda *a, **k: saved.append(k) or real(*a, **k))
        svc.start_timing_check(drama)
        result = _wait(drama)["result"]
        assert result["skipped_flagged"] == 1 and result["flagged"] == 1
        assert [k["fields"] for k in saved] == [("flag", "flag_note")]
        assert saved[0]["only_if_unchanged"] is True
        lines = _lines(isolated_db, drama)
        assert lines[0].flag == "name_uncertain" and lines[0].en == "keep"
        assert lines[1].flag == FLAG and lines[1].en == "mine"

    def test_user_edit_during_the_run_is_kept(self, isolated_db, drama, fake_speech, monkeypatch):
        ids = _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])

        def scan(job_id, path, total):
            isolated_db.update_line_fields_if(drama, ids[0], {"start": 9.9}, {"start": 8.0})
            return fake_speech["spans"]
        monkeypatch.setattr(speech_coverage_service, "_scan_speech", scan)
        svc.start_timing_check(drama)
        _wait(drama)
        line = _lines(isolated_db, drama)[0]
        assert line.start == 9.9 and line.flag is None

    def test_broken_audio_gives_a_notice_and_no_flags(self, isolated_db, drama, fake_speech):
        _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a",
                                        flag=FLAG, flag_note="old")])
        fake_speech["spans"] = []
        svc.start_timing_check(drama)
        status = _wait(drama)
        assert status["result"]["notice"] == timing_drift.BROKEN_AUDIO_NOTICE
        assert status["last_check"]["notice"] == timing_drift.BROKEN_AUDIO_NOTICE
        assert _lines(isolated_db, drama)[0].flag == FLAG

    def test_streamer_vod_is_conservative(self, isolated_db, fake_speech):
        did = isolated_db.create_drama(title_en="V", audio_filename="audio.wav", content_mode="streamer_vod")
        open(os.path.join(isolated_db.drama_dir(did), "audio.wav"), "wb").close()
        _seed(isolated_db, did, [Line(idx=0, start=8.0, end=14.0, zh="a")])
        svc.start_timing_check(did)
        _wait(did)
        assert "mistake music for speech" in _lines(isolated_db, did)[0].flag_note

    def test_missing_detector_package_and_unreadable_audio(self, isolated_db, drama, fake_speech, monkeypatch):
        _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])

        def boom(*a):
            raise ImportError("faster_whisper")
        monkeypatch.setattr(speech_coverage_service, "_scan_speech", boom)
        svc.start_timing_check(drama)
        assert _wait(drama)["status"] == "error"
        with background_jobs._lock:
            background_jobs._jobs.pop(f"timingchk_{drama}", None)
        fake_speech["total"] = None
        monkeypatch.setattr(speech_coverage_service, "_audio_seconds", lambda path: None)
        svc.start_timing_check(drama)
        assert _wait(drama)["result"] == {"failed_reason": "unreadable"}


class TestDismiss:
    def test_dismissed_line_is_not_flagged_again(self, isolated_db, drama, fake_speech):
        from services import lines_service
        ids = _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])
        svc.start_timing_check(drama)
        _wait(drama)
        lines_service.dismiss_flag(drama, ids[0])
        assert svc.get_timing_check(drama)["suggestions"] == []
        with background_jobs._lock:
            background_jobs._jobs.pop(f"timingchk_{drama}", None)
        svc.start_timing_check(drama)
        _wait(drama)
        assert _lines(isolated_db, drama)[0].flag is None

    def test_dismissing_another_flag_records_nothing(self, isolated_db, drama):
        from services import lines_service
        ids = _seed(isolated_db, drama, [Line(idx=0, start=1, end=2, zh="a", flag="slang_idiom")])
        lines_service.dismiss_flag(drama, ids[0])
        assert not os.path.exists(svc._state_path(drama))


class TestSnap:
    @pytest.fixture
    def flagged(self, isolated_db, drama, fake_speech):
        ids = _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a"),
                                         Line(idx=1, start=18.0, end=24.0, zh="b")])
        svc.start_timing_check(drama)
        _wait(drama)
        return ids

    def test_snap_all_applies_after_a_history_snapshot(self, isolated_db, drama, flagged):
        out = svc.snap_to_speech(drama)
        assert out["snapped"] == 2 and out["stale_ids"] == [] and out["history_id"]
        lines = _lines(isolated_db, drama)
        assert (lines[0].start, lines[0].end) == (9.9, 14.0)
        assert (lines[1].start, lines[1].end) == (19.9, 24.0)
        assert lines[0].flag is None and lines[0].flag_note == ""
        snap = isolated_db.get_line_history_snapshot(out["history_id"])
        assert any(abs(r["start"] - 8.0) < 1e-6 for r in snap)
        assert svc.get_timing_check(drama)["suggestions"] == []

    def test_snap_one_line_only(self, isolated_db, drama, flagged):
        out = svc.snap_to_speech(drama, [flagged[1]])
        assert out["snapped"] == 1
        lines = _lines(isolated_db, drama)
        assert lines[0].start == 8.0 and lines[0].flag == FLAG
        assert lines[1].start == 19.9

    def test_user_edit_since_the_check_wins(self, isolated_db, drama, flagged):
        isolated_db.update_line_fields_if(drama, flagged[0], {"start": 8.5}, {"start": 8.0})
        out = svc.snap_to_speech(drama)
        assert out["snapped"] == 1 and out["stale_ids"] == [flagged[0]]
        line = _lines(isolated_db, drama)[0]
        assert line.start == 8.5 and line.flag == FLAG
        assert svc.snap_to_speech(drama)["snapped"] == 0

    def test_only_timing_columns_and_flags_change(self, isolated_db, drama, flagged):
        before = _lines(isolated_db, drama)[0]
        svc.snap_to_speech(drama)
        after = _lines(isolated_db, drama)[0]
        assert (after.zh, after.en, after.speaker, after.idx) == (before.zh, before.en, before.speaker, before.idx)

    def test_validation(self, isolated_db, drama):
        for bad in ("1", [True], [1.5], [1] * 6000):
            with pytest.raises(InvalidInputError):
                svc.snap_to_speech(drama, bad)
        with pytest.raises(NotFoundError):
            svc.snap_to_speech(999)
        assert svc.snap_to_speech(drama)["snapped"] == 0


class TestStart:
    def test_refusals(self, isolated_db, drama):
        with pytest.raises(NotFoundError):
            svc.start_timing_check(999)
        bare = isolated_db.create_drama(title_en="B")
        with pytest.raises(UnsupportedOperationError):
            svc.start_timing_check(bare)
        with pytest.raises(UnsupportedOperationError):
            svc.start_timing_check(drama)  # audio but no lines

    def test_refused_while_transcribing_and_while_running(self, isolated_db, drama, monkeypatch):
        _seed(isolated_db, drama, [Line(idx=0, start=1, end=2, zh="a")])
        with background_jobs._lock:
            background_jobs._jobs[f"transcribe_{drama}"] = {"status": "running", "progress": 0.1,
                                                            "message": "", "started_at": time.time()}
        try:
            with pytest.raises(ConflictError):
                svc.start_timing_check(drama)
        finally:
            with background_jobs._lock:
                background_jobs._jobs.pop(f"transcribe_{drama}", None)
        monkeypatch.setattr(svc.background_jobs, "start_job", lambda *a, **k: False)
        with pytest.raises(ConflictError):
            svc.start_timing_check(drama)

    def test_timing_check_job_is_a_line_writing_review_job(self):
        from services import jobs_service
        assert "timingchk_" in background_jobs.LINE_WRITING_JOB_PREFIXES
        assert jobs_service.JOB_KIND_BY_PREFIX["timingchk_"] == "review"


class TestAutoRun:
    @pytest.mark.parametrize("backend,expected", [
        ("qwen3_asr_vad", True), ("qwen3_asr_long", True),
        ("whisper", False), ("qwen3_asr", False), (None, False)])
    def test_only_qwen_only_backends_start_it(self, isolated_db, drama, fake_speech, backend, expected):
        _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])
        assert svc.after_run(drama, {"raw_backend": backend}) is expected
        if expected:
            assert _wait(drama)["status"] == "done"
        else:
            assert svc.get_timing_check(drama)["status"] == "idle"

    def test_runs_while_the_transcription_job_is_still_open_and_resets_old_state(self, isolated_db, drama, fake_speech):
        _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])
        svc.note_dismissed(drama, 1)
        with background_jobs._lock:
            background_jobs._jobs[f"transcribe_{drama}"] = {"status": "running", "progress": 0.9,
                                                            "message": "", "started_at": time.time()}
        try:
            assert svc.after_run(drama, {"raw_backend": "qwen3_asr_long"}) is True
            _wait(drama)
        finally:
            with background_jobs._lock:
                background_jobs._jobs.pop(f"transcribe_{drama}", None)
        assert _lines(isolated_db, drama)[0].flag == FLAG

    def test_never_raises(self, isolated_db):
        assert svc.after_run(999, {"raw_backend": "qwen3_asr_vad"}) is False


class TestApi:
    @pytest.fixture
    def client(self, isolated_db):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                          headers={"X-Baihe-Local": "1"})

    def test_run_status_and_snap(self, client, isolated_db, drama, fake_speech):
        _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])
        r = client.post(f"/api/timing-check/dramas/{drama}/run")
        assert r.status_code == 200 and r.json() == {"job_id": f"timingchk_{drama}"}
        _wait(drama)
        body = client.get(f"/api/timing-check/dramas/{drama}").json()
        assert body["status"] == "done" and body["result"]["flagged"] == 1
        assert body["suggestions"][0]["new_start"] == 9.9
        text = json.dumps(body)
        assert "audio.wav" not in text and os.path.dirname(os.path.dirname(__file__)) not in text
        snap = client.post(f"/api/timing-check/dramas/{drama}/snap", json={}).json()
        assert snap["snapped"] == 1 and snap["stale_ids"] == []

    def test_errors(self, client, drama):
        assert client.get("/api/timing-check/dramas/999").status_code == 404
        assert client.post("/api/timing-check/dramas/999/snap", json={}).status_code == 404
        assert client.post(f"/api/timing-check/dramas/{drama}/snap", json={"line_ids": ["x"]}).status_code == 422
        assert client.post(f"/api/timing-check/dramas/{drama}/run").status_code == 400


class TestCli:
    def test_command_checks_and_snaps_through_the_service(self, isolated_db, drama, fake_speech, capsys):
        import argparse
        import cli_timing
        _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])
        sub = argparse.ArgumentParser().add_subparsers()

        def wait(job_id, label):
            return "ok", "", _wait(drama)["result"]
        cli_timing.register(sub, wait)
        args = argparse.Namespace(id=drama, snap=True)
        cli_timing.cmd_timing_check(args, wait)
        out = capsys.readouterr().out
        assert "1 flagged" in out and "snapped 1 line(s)" in out
        assert _lines(isolated_db, drama)[0].start == 9.9

    def test_waits_only_for_a_check_that_is_running(self, isolated_db, drama):
        import cli_timing
        cli_timing.wait_after_transcribe(drama, "#1", lambda *a: pytest.fail("no job to wait for"))


WIN_PATH = r"C:\Users\someone\AppData\baihe\dramas\7\audio.wav"


def _write_sidecar(did, payload, raw=None):
    path = svc._state_path(did)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(raw if raw is not None else json.dumps(payload))


class TestNoPathsInResponses:
    def test_decode_failure_detail_is_fixed_text(self, isolated_db, drama, fake_speech, monkeypatch):
        _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])

        def boom(job_id, path, total):
            raise subprocess.CalledProcessError(1, ["ffmpeg", "-i", WIN_PATH])
        monkeypatch.setattr(speech_coverage_service, "_scan_speech", boom)
        svc.start_timing_check(drama)
        status = _wait(drama)
        assert status["result"]["failed_reason"] == "decode"
        assert "Users" not in json.dumps(status) and "audio.wav" not in json.dumps(status)

    def test_write_failure_message_is_scrubbed(self, isolated_db, drama, fake_speech, monkeypatch):
        _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])

        def denied(did, state):
            raise PermissionError(5, "Access is denied", WIN_PATH + ".tmp")
        monkeypatch.setattr(svc, "_write_state", denied)
        svc.start_timing_check(drama)
        status = _wait(drama)
        assert status["status"] == "error"
        assert "someone" not in status["message"] and "AppData" not in status["message"]


class TestHostileSidecar:
    @pytest.mark.parametrize("payload", [
        {"dismissed": 5}, {"dismissed": [True, "1", 2.5]}, {"suggestions": []},
        {"suggestions": {"1": {"start": 1, "end": 2}}},
        {"suggestions": {"\u00b2": {"start": 1, "end": 3, "new_start": 1.5, "new_end": 2.5}}},
        {"suggestions": {"1": {"start": 1, "end": 3, "new_start": 2.5, "new_end": 1.5}}},
        {"suggestions": {"1": {"start": 1, "end": 3, "new_start": 0.0, "new_end": 2}}},
        {"suggestions": {"1": {"start": 1, "end": 90, "new_start": 50, "new_end": 60}}},
        {"suggestions": {"1": {"start": 1, "end": 3, "new_start": "1.5", "new_end": 2}}},
        {"last_check": {"checked_at": 5, "flagged": "x"}}, {"last_check": []}, [1, 2], "x"])
    def test_invalid_content_is_dropped(self, isolated_db, drama, payload):
        _write_sidecar(drama, payload)
        state = svc._read_state(drama)
        assert state["suggestions"] == {} and state["last_check"] is None
        status = svc.get_timing_check(drama)
        assert status["suggestions"] == [] and status["last_check"] is None
        assert svc.snap_to_speech(drama)["snapped"] == 0

    def test_non_finite_and_oversize_files_read_as_empty(self, isolated_db, drama, monkeypatch):
        _write_sidecar(drama, None, '{"dismissed": [1], "suggestions": {"1": '
                                    '{"start": 1, "end": 3, "new_start": NaN, "new_end": 2}}}')
        assert svc._read_state(drama)["dismissed"] == []
        _write_sidecar(drama, {"dismissed": [1]})
        monkeypatch.setattr(svc, "_MAX_STATE_BYTES", 5)
        assert svc._read_state(drama)["dismissed"] == []

    def test_numeric_and_key_extremes_read_as_empty(self, isolated_db, drama):
        huge = "9" * 400
        _write_sidecar(drama, None, '{"suggestions": {"1": {"start": 1, "end": 3, "new_start": '
                                    + huge + ', "new_end": 2}}}')
        assert svc._read_state(drama)["suggestions"] == {}
        good = {"start": 8.0, "end": 14.0, "new_start": 9.9, "new_end": 14.0}
        _write_sidecar(drama, {"suggestions": {"7" * 5000: good, "2": good}})
        assert list(svc._read_state(drama)["suggestions"]) == ["2"]

    def test_a_suggestion_the_check_wrote_survives_the_reader(self, isolated_db, drama, fake_speech):
        fake_speech["spans"] = [(10.0, 14.5)]
        _seed(isolated_db, drama, [Line(idx=0, start=8.001, end=14.237, zh="a")])
        svc.start_timing_check(drama)
        _wait(drama)
        assert len(svc._read_state(drama)["suggestions"]) == 1

    def test_snap_all_with_a_huge_hostile_sidecar_is_fast(self, isolated_db, drama):
        _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])
        entry = {"start": 8.0, "end": 14.0, "new_start": 9.9, "new_end": 14.0}
        _write_sidecar(drama, {"suggestions": {str(i): entry for i in range(1, 60001)}})
        began = time.monotonic()
        out = svc.snap_to_speech(drama)
        assert time.monotonic() - began < 10 and out["snapped"] == 0

    def test_valid_entries_survive_beside_bad_ones(self, isolated_db, drama):
        good = {"start": 8.0, "end": 14.0, "new_start": 9.9, "new_end": 14.0}
        _write_sidecar(drama, {"dismissed": [3, True], "suggestions": {"1": good, "2": {"start": 1}}})
        state = svc._read_state(drama)
        assert state["dismissed"] == [3] and list(state["suggestions"]) == ["1"]

    def test_dismiss_survives_a_sidecar_write_failure(self, isolated_db, drama, monkeypatch):
        from services import lines_service
        ids = _seed(isolated_db, drama, [Line(idx=0, start=1, end=2, zh="a", flag=FLAG)])
        monkeypatch.setattr(svc, "_write_state", lambda *a: (_ for _ in ()).throw(OSError("disk")))
        out = lines_service.dismiss_flag(drama, ids[0])
        assert not out.get("flag")


class TestWriteState:
    def test_failed_write_leaves_no_temp_file(self, isolated_db, drama, monkeypatch):
        monkeypatch.setattr(svc.json, "dump", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
        with pytest.raises(ValueError):
            svc._write_state(drama, {})
        assert not any(n.endswith(".tmp") for n in os.listdir(os.path.dirname(svc._state_path(drama))))


class TestBlockingAndGeneration:
    @pytest.mark.parametrize("prefix", ["transcribe_", "fixflag_", "resegment_", "narration_",
                                        "comparetx_", "retranscribe_", "retime_"])
    def test_refused_while_a_line_changing_job_runs(self, isolated_db, drama, prefix):
        _seed(isolated_db, drama, [Line(idx=0, start=1, end=2, zh="a")])
        key = f"{prefix}{drama}"
        with background_jobs._lock:
            background_jobs._jobs[key] = {"status": "queued", "progress": 0.0, "message": "",
                                          "started_at": time.time()}
        try:
            with pytest.raises(ConflictError):
                svc.start_timing_check(drama)
        finally:
            with background_jobs._lock:
                background_jobs._jobs.pop(key, None)

    def test_old_run_cannot_write_after_a_new_transcription(self, isolated_db, drama, fake_speech):
        _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])
        gen = svc._generation.get(drama, 0)
        svc.after_run(drama, {"raw_backend": "whisper"})
        svc.apply_spans(drama, fake_speech["spans"], 100.0, False, gen)
        state = svc._read_state(drama)
        assert state["suggestions"] == {} and state["last_check"] is None

    def test_any_transcription_forgets_dismissals(self, isolated_db, drama):
        svc.note_dismissed(drama, 1)
        assert svc.after_run(drama, {"raw_backend": "whisper"}) is False
        assert svc._read_state(drama)["dismissed"] == []


class TestSnapSnapshots:
    def test_all_stale_takes_no_snapshot(self, isolated_db, drama, fake_speech, monkeypatch):
        ids = _seed(isolated_db, drama, [Line(idx=0, start=8.0, end=14.0, zh="a")])
        svc.start_timing_check(drama)
        _wait(drama)
        isolated_db.update_line_fields_if(drama, ids[0], {"start": 8.5}, {"start": 8.0})
        monkeypatch.setattr(isolated_db, "save_line_history_snapshot",
                            lambda *a, **k: pytest.fail("snapshot taken"))
        out = svc.snap_to_speech(drama)
        assert out["snapped"] == 0 and out["stale_ids"] == [ids[0]] and out["history_id"] is None


class TestSchema:
    def test_line_ids_are_capped(self):
        from api.schemas.timing_check import TimingSnapRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TimingSnapRequest(line_ids=list(range(5001)))

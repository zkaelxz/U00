"""
Speech coverage check: speech the detector finds that no line covers, and
whether the raw transcript had text there. The detector, ffmpeg and ffprobe
are faked -- no audio model, no real audio.
"""
import json
import os
import time

import pytest

import background_jobs
import raw_transcript
from core import Line
from services import speech_coverage_service as svc
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)


def L(i, start, end, zh="x"):
    return Line(idx=i, start=start, end=end, zh=zh, id=i + 1)


class TestFindGaps:
    def test_uncovered_speech_is_listed_and_totals_add_up(self):
        speech = [(0.0, 10.0), (20.0, 30.0)]
        lines = [L(0, 0.0, 10.0), L(1, 20.0, 22.0)]
        report = svc.find_gaps(speech, lines, None, 2.0)
        assert [(g["start"], g["end"]) for g in report["gaps"]] == [(22.3, 30.0)]
        assert report["speech_seconds"] == 20.0
        assert report["covered_seconds"] == pytest.approx(12.3, abs=0.1)
        assert report["covered_percent"] == pytest.approx(61.5, abs=0.5)
        gap = report["gaps"][0]
        assert gap["after_line_id"] == 2 and gap["before_line_id"] is None

    def test_short_gaps_and_blank_lines(self):
        speech = [(0.0, 10.0)]
        # The blank line covers nothing; the 1 s hole beside the real line is under the threshold.
        lines = [L(0, 0.0, 8.5), L(1, 8.5, 10.0, zh="  ")]
        assert svc.find_gaps(speech, lines, None, 2.0)["gaps"] == []
        assert len(svc.find_gaps(speech, lines, None, 0.5)["gaps"]) == 1

    def test_raw_text_tells_lost_from_never_heard(self):
        speech = [(0.0, 5.0), (10.0, 15.0)]
        lines = []
        raw = [{"start": 10.2, "end": 14.0, "text": "你好吗"},
               {"start": 0.0, "end": 5.0, "text": "   "}]
        gaps = svc.find_gaps(speech, lines, raw, 2.0)["gaps"]
        by_start = {g["start"]: g for g in gaps}
        assert by_start[0.0]["raw_status"] == "none"
        assert by_start[10.0]["raw_status"] == "lost_after"
        assert by_start[10.0]["raw_text"] == "你好吗"

    def test_no_raw_transcript_is_unknown_not_none(self):
        gaps = svc.find_gaps([(0.0, 5.0)], [], None, 2.0)
        assert gaps["raw_available"] is False
        assert gaps["gaps"][0]["raw_status"] == "unknown"

    def test_no_speech_means_no_percent(self):
        report = svc.find_gaps([], [L(0, 0.0, 1.0)], [], 2.0)
        assert report["covered_percent"] is None and report["gaps"] == []

    def test_close_pieces_merge_into_one_stretch(self):
        speech = [(0.0, 1.5), (2.0, 3.5)]
        gaps = svc.find_gaps(speech, [], [], 2.0)["gaps"]
        assert [(g["start"], g["end"], g["speech_seconds"]) for g in gaps] == [(0.0, 3.5, 3.0)]


@pytest.fixture
def drama(isolated_db):
    did = isolated_db.create_drama(title_en="D", audio_filename="audio.wav")
    open(os.path.join(isolated_db.drama_dir(did), "audio.wav"), "wb").close()
    return did


@pytest.fixture(autouse=True)
def _no_leftover_jobs():
    def clear():
        with background_jobs._lock:
            for jid in [j for j in background_jobs._jobs if j.startswith("speechcov_")]:
                background_jobs._jobs.pop(jid, None)
    clear()
    yield
    clear()


def _wait(drama_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        status = svc.get_speech_coverage(drama_id)
        if status["status"] in ("done", "error", "cancelled"):
            return status
        time.sleep(0.02)
    raise AssertionError("coverage job did not finish")


@pytest.fixture
def fake_audio(monkeypatch):
    calls = []
    monkeypatch.setattr(svc, "_audio_seconds", lambda path: 1500.0)
    monkeypatch.setattr(svc, "_decode_chunk", lambda path, start, seconds: calls.append(start) or [0.0])
    # Per chunk (chunk-relative): speech 5-15 s in chunk 0, and one that crosses a chunk edge.
    spans = {0.0: [(5.0, 15.0), (595.0, 600.0)], 600.0: [(0.0, 4.0)], 1200.0: []}
    state = {"i": 0}

    def detect(chunk):
        start = [0.0, 600.0, 1200.0][state["i"]]
        state["i"] += 1
        return spans[start]
    monkeypatch.setattr(svc, "_detect_speech", detect)
    return calls


class TestJob:
    def test_runs_in_chunks_and_reports_gaps_with_raw_evidence(self, isolated_db, drama, fake_audio):
        isolated_db.save_lines(drama, [Line(idx=0, start=5.0, end=15.0, zh="covered")])
        raw_transcript.write_raw_transcript(
            isolated_db.drama_dir(drama),
            [{"start": 596.0, "end": 603.0, "text": "lost words"}], [], "whisper")
        svc.start_speech_coverage(drama, min_gap_seconds=2.0)
        status = _wait(drama)
        assert status["status"] == "done", status["message"]
        assert fake_audio == [0.0, 600.0, 1200.0]
        result = status["result"]
        # The span cut at the chunk edge (595-604) is one gap, not two.
        assert [(g["start"], g["end"]) for g in result["gaps"]] == [(595.0, 604.0)]
        assert result["gaps"][0]["raw_status"] == "lost_after"
        assert result["vad_threshold"] == 0.3 and result["audio_seconds"] == 1500.0
        assert result["raw_available"] is True
        # Read-only: the saved line is untouched.
        assert [ln.zh for ln in isolated_db.load_line_objects(drama)] == ["covered"]

    def test_unreadable_audio_fails_softly(self, isolated_db, drama, monkeypatch):
        monkeypatch.setattr(svc, "_audio_seconds", lambda path: None)
        svc.start_speech_coverage(drama)
        status = _wait(drama)
        assert status["status"] == "done" and status["result"] == {"failed_reason": "unreadable"}

    def test_missing_detector_package(self, isolated_db, drama, monkeypatch):
        monkeypatch.setattr(svc, "_audio_seconds", lambda path: 10.0)
        monkeypatch.setattr(svc, "_decode_chunk", lambda *a: [0.0])

        def boom(chunk):
            raise ImportError("faster_whisper")
        monkeypatch.setattr(svc, "_detect_speech", boom)
        svc.start_speech_coverage(drama)
        status = _wait(drama)
        assert status["status"] == "error" and "faster-whisper" in status["message"]

    def test_decode_failure_is_redacted(self, isolated_db, drama, monkeypatch):
        monkeypatch.setattr(svc, "_audio_seconds", lambda path: 10.0)

        def boom(*a):
            raise OSError("ffmpeg failed key=sk-ant-api03-SECRETSECRETSECRETSECRET")
        monkeypatch.setattr(svc, "_decode_chunk", boom)
        svc.start_speech_coverage(drama)
        result = _wait(drama)["result"]
        assert result["failed_reason"] == "decode"
        assert "SECRETSECRET" not in json.dumps(result)

    def test_idle_before_any_run(self, isolated_db, drama):
        assert svc.get_speech_coverage(drama)["status"] == "idle"


class TestStartRefusals:
    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.start_speech_coverage(999)
        with pytest.raises(NotFoundError):
            svc.get_speech_coverage(999)

    def test_no_stored_audio(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(UnsupportedOperationError):
            svc.start_speech_coverage(did)

    def test_gap_length_bounds(self, drama):
        for bad in (0.1, 31, True, "2"):
            with pytest.raises(InvalidInputError):
                svc.start_speech_coverage(drama, min_gap_seconds=bad)

    def test_refused_while_transcribing_and_while_running(self, isolated_db, drama, monkeypatch):
        with background_jobs._lock:
            background_jobs._jobs[f"transcribe_{drama}"] = {"status": "running", "progress": 0.1,
                                                            "message": "", "started_at": time.time()}
        try:
            with pytest.raises(ConflictError):
                svc.start_speech_coverage(drama)
        finally:
            with background_jobs._lock:
                background_jobs._jobs.pop(f"transcribe_{drama}", None)
        monkeypatch.setattr(svc.background_jobs, "start_job", lambda *a, **k: False)
        with pytest.raises(ConflictError):
            svc.start_speech_coverage(drama)


class TestApi:
    @pytest.fixture
    def client(self, isolated_db):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                          headers={"X-Baihe-Local": "1"})

    def test_start_and_read_back(self, client, drama, fake_audio):
        r = client.post(f"/api/transcribe/dramas/{drama}/speech-coverage", json={"min_gap_seconds": 2})
        assert r.status_code == 200 and r.json() == {"job_id": f"speechcov_{drama}"}
        _wait(drama)
        body = client.get(f"/api/transcribe/dramas/{drama}/speech-coverage").json()
        assert body["status"] == "done" and body["result"]["gaps_total"] >= 1
        text = json.dumps(body)
        assert "audio.wav" not in text and os.path.dirname(os.path.dirname(__file__)) not in text

    def test_rejects_unknown_fields_and_bad_values(self, client, drama):
        url = f"/api/transcribe/dramas/{drama}/speech-coverage"
        assert client.post(url, json={"min_gap_seconds": 0.1}).status_code == 422
        assert client.post(url, json={"path": "/etc/passwd"}).status_code == 422
        assert client.get("/api/transcribe/dramas/999/speech-coverage").status_code == 404

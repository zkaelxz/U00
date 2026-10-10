"""Step 105 follow-up: a speaker-count range for "Detect speakers after
transcribing" (TranscribeRunRequest min_speakers/max_speakers). The range
reaches the chained diarization job's worker options and its on_done hook,
and bad combinations are refused before anything starts. No model runs."""
import whisper_models
import os

import pytest

import background_jobs
from services import diarization_service, transcribe_pipeline, transcribe_service
from services.service_errors import InvalidInputError


def _drama(isolated_db, **fields):
    did = isolated_db.create_drama(title_en="D", audio_filename="audio.wav",
                                   transcript_mode="whisper", **fields)
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, "audio.wav"), "wb").close()
    return did, ddir


@pytest.fixture
def captured(monkeypatch):
    """Captures the transcribe worker's arguments and its on_done options."""
    from tests.test_transcribe_service import _capture_worker_start
    return _capture_worker_start(monkeypatch)


def test_start_passes_the_range_to_the_job(isolated_db, captured):
    did, _ = _drama(isolated_db)
    transcribe_service.start_transcribe_run(did, run_diarize=True, min_speakers=2, max_speakers=4)
    assert (captured["min_speakers"], captured["max_speakers"]) == (2, 4)
    assert captured["expected_speakers"] is None


def test_zero_means_unset_and_an_exact_count_is_unchanged(isolated_db, captured):
    did, _ = _drama(isolated_db)
    transcribe_service.start_transcribe_run(did, run_diarize=True, expected_speakers=3,
                                            min_speakers=0, max_speakers=0)
    assert (captured["min_speakers"], captured["max_speakers"]) == (None, None)
    assert captured["expected_speakers"] == 3


@pytest.mark.parametrize("kw,msg", [
    ({"min_speakers": 4, "max_speakers": 2}, "more than maximum"),
    ({"expected_speakers": 3, "min_speakers": 2}, "not both"),
])
def test_bad_ranges_are_refused_before_starting(isolated_db, captured, kw, msg):
    did, _ = _drama(isolated_db)
    with pytest.raises(InvalidInputError, match=msg):
        transcribe_service.start_transcribe_run(did, run_diarize=True, **kw)
    with pytest.raises(InvalidInputError, match=msg):
        transcribe_service.validate_transcribe_options(did, **kw)
    assert captured == {}


def test_the_chained_diarization_gets_the_range(isolated_db, monkeypatch):
    did, ddir = _drama(isolated_db)
    job_id = f"transcribe_{did}"
    background_jobs.clear_job(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
    monkeypatch.setattr(whisper_models, "load_whisper_model",
                        lambda *a, **k: object())  # never a real model
    on_done_args = []
    monkeypatch.setattr(diarization_service, "make_apply_on_done",
                        lambda drama_id, expected=None, **k: on_done_args.append((expected, k)))
    calls = []
    monkeypatch.setattr(background_jobs, "start_process_job",
                        lambda job_id, target, args=(), **k: calls.append(args) or True)
    audio = os.path.join(ddir, "audio.wav")
    transcribe_service._run_transcribe_and_apply_job(
        job_id, did, audio, "whisper", None, "zh", "simplified", "medium", 5, 300, 0.5,
        False, "auto", False, False, False, None, "hf-token", None,
        diarize_audio_path=audio, min_speakers=2, max_speakers=4)
    ((_, _, num, options),) = calls
    assert num is None
    assert (options["min_speakers"], options["max_speakers"]) == (2, 4)
    assert on_done_args == [(None, {"min_speakers": 2, "max_speakers": 4})]
    assert background_jobs.get_status(job_id)["result"]["diarize_started"] is True
    background_jobs.clear_job(job_id)


# --- the route --------------------------------------------------------------

@pytest.fixture
def client(isolated_db):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def test_run_route_accepts_a_range(client, isolated_db, captured):
    did, _ = _drama(isolated_db)
    r = client.post(f"/api/transcribe/dramas/{did}/run",
                    json={"run_diarize": True, "min_speakers": 2, "max_speakers": 5})
    assert r.status_code == 200, r.text
    assert (captured["min_speakers"], captured["max_speakers"]) == (2, 5)


@pytest.mark.parametrize("body,status", [
    ({"run_diarize": True, "min_speakers": 5, "max_speakers": 2}, 422),
    ({"run_diarize": True, "expected_speakers": 3, "max_speakers": 4}, 422),
    ({"run_diarize": True, "min_speakers": 21}, 422),
])
def test_run_route_refuses_bad_ranges(client, isolated_db, captured, body, status):
    did, _ = _drama(isolated_db)
    r = client.post(f"/api/transcribe/dramas/{did}/run", json=body)
    assert r.status_code == status, r.text
    assert captured == {}

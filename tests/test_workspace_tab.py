"""
tests/test_workspace_tab.py -- background-thread job targets for the
Workspace tab's "Transcribe & Align" / "Read Captions from Video" buttons.

Regression coverage for a real bug: the Whisper pass used to run as a
blocking call directly inside the button's click handler, with only a
static st.spinner (no progress) and no way to use the rest of the app
while a long-form file (3+ hours) was processing. This mirrors the
run_translate_job pattern already used for the Translate button -- the
transcription pass now runs in a background thread with real progress,
and the two known/expected failure modes (model download failure, no
audio detected) are recorded on the job result instead of raised, so the
UI can keep showing its existing specific, actionable messages instead of
a generic error+traceback. run_hardsub_ocr_job follows the exact same
pattern for the burned-in-caption OCR path.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs
from tabs.workspace_tab import run_transcribe_job, run_hardsub_ocr_job
import core as core_module


def _clear(job_id):
    background_jobs.clear_job(job_id)


def test_success_stores_segments_and_no_gpu_fallback(monkeypatch):
    job_id = "test_transcribe_ok"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}

    fake_segments = [{"start": 0.0, "end": 1.0, "text": "hi"}]
    monkeypatch.setattr(
        "tabs.workspace_tab.transcribe_for_timing",
        lambda *a, **k: fake_segments)

    run_transcribe_job(job_id, "/fake.wav", "medium", "zh", False, None, None, "", 5, 2000)

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"segments": fake_segments, "gpu_fallback": None}
    _clear(job_id)


def test_gpu_fallback_message_is_captured(monkeypatch):
    job_id = "test_transcribe_gpu_fallback"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}

    def fake_transcribe(*a, **k):
        k["on_gpu_fallback"]("CUDA out of memory")
        return [{"start": 0.0, "end": 1.0, "text": "hi"}]

    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", fake_transcribe)

    run_transcribe_job(job_id, "/fake.wav", "medium", "zh", True, None, None, "", 5, 2000)

    result = background_jobs.get_status(job_id)["result"]
    assert result["gpu_fallback"] == "CUDA out of memory"
    _clear(job_id)


def test_progress_cb_is_wired_to_update_progress(monkeypatch):
    job_id = "test_transcribe_progress"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}

    def fake_transcribe(*a, **k):
        k["progress_cb"](0.5)
        return [{"start": 0.0, "end": 1.0, "text": "hi"}]

    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", fake_transcribe)

    run_transcribe_job(job_id, "/fake.wav", "medium", "zh", False, None, None, "", 5, 2000)

    status = background_jobs.get_status(job_id)
    assert status["progress"] == 0.5
    assert "50%" in status["message"]
    _clear(job_id)


def test_model_download_failure_is_recorded_not_raised(monkeypatch):
    job_id = "test_transcribe_download_fail"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}

    def fake_transcribe(*a, **k):
        raise core_module.ModelDownloadError("network unreachable")

    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", fake_transcribe)

    run_transcribe_job(job_id, "/fake.wav", "medium", "zh", False, None, None, "", 5, 2000)  # must not raise

    result = background_jobs.get_status(job_id)["result"]
    assert result["failed_reason"] == "model_download"
    assert "network unreachable" in result["detail"]
    _clear(job_id)


def test_empty_segments_is_recorded_not_raised(monkeypatch):
    job_id = "test_transcribe_empty"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}

    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", lambda *a, **k: [])

    run_transcribe_job(job_id, "/fake.wav", "medium", "zh", False, None, None, "", 5, 2000)

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"failed_reason": "empty"}
    _clear(job_id)


def test_unexpected_exception_still_propagates(monkeypatch):
    """Anything other than the two known/expected failure modes is a real
    bug and must still surface as a job error (via background_jobs.start_job's
    own runner), not be silently swallowed here."""
    job_id = "test_transcribe_unexpected"
    _clear(job_id)

    def fake_transcribe(*a, **k):
        raise RuntimeError("something genuinely broke")

    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", fake_transcribe)

    try:
        run_transcribe_job(job_id, "/fake.wav", "medium", "zh", False, None, None, "", 5, 2000)
        assert False, "unexpected exceptions must propagate"
    except RuntimeError as e:
        assert "something genuinely broke" in str(e)
    _clear(job_id)


def test_hardsub_ocr_success_stores_segments(monkeypatch):
    job_id = "test_hardsub_ok"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}

    fake_cues = [{"start": 0.0, "end": 2.0, "text": "你好"}]
    import hardsub_ocr
    monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", lambda *a, **k: fake_cues)

    run_hardsub_ocr_job(job_id, "/fake.mp4", "zh", 1.0, "tesseract")

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"segments": fake_cues}
    _clear(job_id)


def test_hardsub_ocr_progress_cb_is_wired(monkeypatch):
    job_id = "test_hardsub_progress"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}

    def fake_extract(video_path, language, sample_interval, ocr_backend, progress_cb=None):
        progress_cb(0.4)
        return [{"start": 0.0, "end": 1.0, "text": "hi"}]

    import hardsub_ocr
    monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", fake_extract)

    run_hardsub_ocr_job(job_id, "/fake.mp4", "zh", 1.0, "tesseract")

    status = background_jobs.get_status(job_id)
    assert status["progress"] == 0.4
    assert "40%" in status["message"]
    _clear(job_id)


def test_hardsub_ocr_empty_cues_is_recorded_not_raised(monkeypatch):
    job_id = "test_hardsub_empty"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}

    import hardsub_ocr
    monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", lambda *a, **k: [])

    run_hardsub_ocr_job(job_id, "/fake.mp4", "zh", 1.0, "tesseract")

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"failed_reason": "empty"}
    _clear(job_id)


def test_hardsub_ocr_unexpected_exception_still_propagates(monkeypatch):
    job_id = "test_hardsub_unexpected"
    _clear(job_id)

    import hardsub_ocr
    def fake_extract(*a, **k):
        raise RuntimeError("ffmpeg not found")
    monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", fake_extract)

    try:
        run_hardsub_ocr_job(job_id, "/fake.mp4", "zh", 1.0, "tesseract")
        assert False, "unexpected exceptions must propagate"
    except RuntimeError as e:
        assert "ffmpeg not found" in str(e)
    _clear(job_id)

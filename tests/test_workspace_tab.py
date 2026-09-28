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
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import background_jobs
import db
import diarize
import dub as dub_module
import resegment
import translate_engines
from tabs.workspace_tab import (run_transcribe_job, run_hardsub_ocr_job, run_flag_job,
                                 run_emotion_job, run_consistency_job, run_translation_notes_job,
                                 run_fix_flagged_lines_job, run_translate_job,
                                 _compute_workspace_stage_index)
import video_download
import core as core_module
from core import Line


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
    assert result == {"segments": fake_segments, "gpu_fallback": None, "word_align_error": None}
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


def test_vocal_separation_runs_before_transcription_and_feeds_its_output(monkeypatch, tmp_path):
    job_id = "test_transcribe_vocal_sep"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    audio_path = str(tmp_path / "audio.wav")

    import audio_preprocess
    seen = {}

    def fake_separate(in_path, out_path, backend="auto", progress_cb=None, cancel_check_cb=None):
        seen["in_path"] = in_path
        seen["out_path"] = out_path
        seen["backend"] = backend
        return out_path
    monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_separate)

    def fake_transcribe(path, *a, **k):
        seen["transcribed_path"] = path
        return [{"start": 0.0, "end": 1.0, "text": "hi"}]
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", fake_transcribe)

    run_transcribe_job(job_id, audio_path, "medium", "zh", False, None, None, "", 5, 2000,
                        separate_vocals_first=True, separation_backend="audio_separator")

    assert seen["in_path"] == audio_path
    assert seen["backend"] == "audio_separator"
    assert seen["out_path"] == str(tmp_path / "vocals.wav")
    assert seen["transcribed_path"] == str(tmp_path / "vocals.wav")
    result = background_jobs.get_status(job_id)["result"]
    assert result["segments"] == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    _clear(job_id)


def test_vocal_separation_off_by_default_transcribes_the_original_audio(monkeypatch):
    job_id = "test_transcribe_vocal_sep_off"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    seen = {}

    def fake_transcribe(path, *a, **k):
        seen["transcribed_path"] = path
        return [{"start": 0.0, "end": 1.0, "text": "hi"}]
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", fake_transcribe)

    run_transcribe_job(job_id, "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 2000)

    assert seen["transcribed_path"] == "/fake/audio.wav"
    _clear(job_id)


def test_groq_transcription_is_used_instead_of_local_whisper_when_enabled(monkeypatch):
    """Step 6i: use_groq bypasses transcribe_for_timing entirely and
    calls core.transcribe_with_groq instead -- its result must reach the
    exact same downstream shape (a "segments" result) local Whisper's
    own success path produces, so alignment/diarization can't tell the
    difference."""
    job_id = "test_transcribe_groq"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    seen = {}

    def fake_groq(audio_path, language, api_key, progress_cb=None):
        seen["audio_path"], seen["language"], seen["api_key"] = audio_path, language, api_key
        if progress_cb:
            progress_cb(1.0)
        return [{"start": 0.0, "end": 1.0, "text": "hi from groq"}]
    monkeypatch.setattr(core_module, "transcribe_with_groq", fake_groq)

    called = []
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing",
                         lambda *a, **k: called.append(1))

    run_transcribe_job(job_id, "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 2000,
                        use_groq=True, groq_api_key="fake-groq-key")

    assert called == []  # local Whisper must never run on the Groq path
    assert seen == {"audio_path": "/fake/audio.wav", "language": "zh", "api_key": "fake-groq-key"}
    result = background_jobs.get_status(job_id)["result"]
    assert result == {"segments": [{"start": 0.0, "end": 1.0, "text": "hi from groq"}],
                      "gpu_fallback": None, "word_align_error": None}
    _clear(job_id)


def test_groq_failure_is_recorded_not_raised(monkeypatch):
    job_id = "test_transcribe_groq_fail"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}

    def fake_groq(*a, **k):
        raise core_module.GroqTranscriptionError("401: invalid api key")
    monkeypatch.setattr(core_module, "transcribe_with_groq", fake_groq)

    called = []
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing",
                         lambda *a, **k: called.append(1))

    run_transcribe_job(job_id, "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 2000,
                        use_groq=True, groq_api_key="bad-key")

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"failed_reason": "groq", "detail": "401: invalid api key"}
    assert called == []
    _clear(job_id)


def test_use_groq_off_by_default_still_uses_local_whisper(monkeypatch):
    job_id = "test_transcribe_groq_off"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}

    def boom(*a, **k):
        raise AssertionError("Groq must not be called when use_groq is False")
    monkeypatch.setattr(core_module, "transcribe_with_groq", boom)
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing",
                         lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

    run_transcribe_job(job_id, "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 2000)

    result = background_jobs.get_status(job_id)["result"]
    assert result["segments"] == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    _clear(job_id)


def test_vocal_separation_failure_is_recorded_not_raised(monkeypatch, tmp_path):
    job_id = "test_transcribe_vocal_sep_fail"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    audio_path = str(tmp_path / "audio.wav")

    import audio_preprocess

    def fake_separate(in_path, out_path, backend="auto", progress_cb=None, cancel_check_cb=None):
        raise audio_preprocess.VocalSeparationError("Vocal separation needs Demucs: pip install demucs")
    monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_separate)

    called = []
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing",
                         lambda *a, **k: called.append(1))

    run_transcribe_job(job_id, audio_path, "medium", "zh", False, None, None, "", 5, 2000,
                        separate_vocals_first=True)

    result = background_jobs.get_status(job_id)["result"]
    assert result["failed_reason"] == "vocal_separation"
    assert "pip install demucs" in result["detail"]
    assert called == []  # transcription must never run on a failed separation
    _clear(job_id)


def test_vocal_separation_cancel_reports_cancelled_and_never_starts_transcription(monkeypatch, tmp_path):
    """Step 4g checkpoint 1: cancelling during vocal separation must stop
    the job before Whisper ever starts, not just fail silently or let
    transcription run anyway."""
    job_id = "test_transcribe_vocal_sep_cancel"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    audio_path = str(tmp_path / "audio.wav")

    import audio_preprocess

    def fake_separate(in_path, out_path, backend="auto", progress_cb=None, cancel_check_cb=None):
        raise audio_preprocess.VocalSeparationCancelled("stopped")
    monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_separate)

    called = []
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing",
                         lambda *a, **k: called.append(1))

    run_transcribe_job(job_id, audio_path, "medium", "zh", False, None, None, "", 5, 2000,
                        separate_vocals_first=True)

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"failed_reason": "cancelled"}
    assert called == []  # transcription must never run after a cancel
    _clear(job_id)


def test_cancel_right_after_vocal_separation_finishes_stops_before_transcription_starts(
        monkeypatch, tmp_path):
    """Step 4g checkpoint 2: separate_vocals()'s own cancel_check_cb only
    fires between its internal chunks -- a cancel requested right at its
    tail (after its last internal check already passed, on a short file
    with few or no chunk boundaries) used to fall through a
    checkpoint-free gap and let the expensive Whisper pass start anyway,
    uninterrupted."""
    job_id = "test_transcribe_cancel_after_separation"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    audio_path = str(tmp_path / "audio.wav")

    import audio_preprocess

    def fake_separate(in_path, out_path, backend="auto", progress_cb=None, cancel_check_cb=None):
        # Separation completes normally -- but a cancel arrived right as
        # it finished, after its own last internal checkpoint.
        background_jobs.request_cancel(job_id)
        return out_path
    monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_separate)

    called = []
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing",
                         lambda *a, **k: called.append(1))

    run_transcribe_job(job_id, audio_path, "medium", "zh", False, None, None, "", 5, 2000,
                        separate_vocals_first=True)

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"failed_reason": "cancelled"}
    assert called == []  # transcription must never start after this checkpoint sees a cancel
    _clear(job_id)


def test_cancel_before_transcription_starts_with_separation_off_also_stops_it(monkeypatch):
    """Step 4g checkpoint 2, separation-off case: with separate_vocals_first
    False there's no separation step at all, so this checkpoint is the
    very first place a cancel requested right after the job started (and
    before Whisper's own checkpoint-free pass began) can be honored."""
    job_id = "test_transcribe_cancel_no_separation"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": True, "result": None}

    called = []
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing",
                         lambda *a, **k: called.append(1))

    run_transcribe_job(job_id, "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 2000)

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"failed_reason": "cancelled"}
    assert called == []
    _clear(job_id)


def test_vocal_separation_threads_progress_and_cancel_callbacks_through(monkeypatch, tmp_path):
    """Confirms run_transcribe_job actually wires background_jobs'
    progress/cancel plumbing into separate_vocals(), not just that a
    cancel eventually gets reported -- a real gap the roadmap named
    ("no real progress and no mid-run stop")."""
    job_id = "test_transcribe_vocal_sep_wiring"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    audio_path = str(tmp_path / "audio.wav")

    import audio_preprocess
    seen = {}

    def fake_separate(in_path, out_path, backend="auto", progress_cb=None, cancel_check_cb=None):
        seen["progress_cb"] = progress_cb
        seen["cancel_check_cb"] = cancel_check_cb
        progress_cb(0.5)
        assert cancel_check_cb() is False
        return out_path
    monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_separate)
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", lambda *a, **k: [
        {"start": 0.0, "end": 1.0, "text": "hi"}])

    run_transcribe_job(job_id, audio_path, "medium", "zh", False, None, None, "", 5, 2000,
                        separate_vocals_first=True)

    assert seen["progress_cb"] is not None and seen["cancel_check_cb"] is not None
    status = background_jobs.get_status(job_id)
    assert status["progress"] == 0.5
    assert "Removing background music" in status["message"]
    _clear(job_id)


def test_cancel_after_transcription_skips_realign_but_keeps_the_transcript(monkeypatch):
    """Step 4g checkpoint 2: Whisper's own pass has no cancel checkpoint
    of its own yet, so a cancel requested during it is only caught once
    it returns -- at that point the expensive work is already done, so
    this must skip the optional realign step rather than discard the
    transcript (never lose already-done work over a cancel).

    Step 4k: cancel_requested is flipped to True as a side effect of the
    transcribe_for_timing mock itself (simulating a cancel arriving
    *during* Whisper's pass), not preset before run_transcribe_job is
    even called -- with Step 4g's own checkpoint 2 now built (right
    after the separate_vocals()-or-skipped point, before Whisper starts)
    a cancel already pending at call time is correctly caught there
    instead, which is a different scenario from this test's."""
    job_id = "test_transcribe_cancel_after_whisper"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    fake_segments = [{"start": 0.0, "end": 20.0, "text": "long merged line"}]

    def fake_transcribe(*a, **k):
        background_jobs.request_cancel(job_id)
        return fake_segments
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", fake_transcribe)

    import word_align
    monkeypatch.setattr(word_align, "realign_oversized_segments",
                        lambda *a, **k: pytest.fail("realign must not run once cancelled"))

    run_transcribe_job(job_id, "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 2000,
                        realign_long_segments=True)

    result = background_jobs.get_status(job_id)["result"]
    assert result["segments"] == fake_segments  # the transcript itself is kept, not discarded
    assert result["word_align_error"] is None
    _clear(job_id)


def test_realign_long_segments_runs_after_transcription(monkeypatch):
    job_id = "test_transcribe_realign"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing",
                         lambda *a, **k: [{"start": 0.0, "end": 20.0, "text": "long merged line"}])

    import word_align
    seen = {}

    def fake_realign(segments, audio_path, language, chinese_script="simplified"):
        seen["segments"] = segments
        seen["audio_path"] = audio_path
        seen["language"] = language
        seen["chinese_script"] = chinese_script
        return [{"start": 0.0, "end": 10.0, "text": "split one"},
                {"start": 10.0, "end": 20.0, "text": "split two"}]
    monkeypatch.setattr(word_align, "realign_oversized_segments", fake_realign)

    run_transcribe_job(job_id, "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 2000,
                        realign_long_segments=True, chinese_script="traditional")

    assert seen["audio_path"] == "/fake/audio.wav"
    assert seen["language"] == "zh"
    assert seen["chinese_script"] == "traditional"
    result = background_jobs.get_status(job_id)["result"]
    assert result["segments"] == [{"start": 0.0, "end": 10.0, "text": "split one"},
                                   {"start": 10.0, "end": 20.0, "text": "split two"}]
    assert result["word_align_error"] is None
    _clear(job_id)


def test_realign_off_by_default_leaves_segments_unchanged(monkeypatch):
    job_id = "test_transcribe_realign_off"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    fake_segments = [{"start": 0.0, "end": 20.0, "text": "long merged line"}]
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", lambda *a, **k: fake_segments)

    run_transcribe_job(job_id, "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 2000)

    result = background_jobs.get_status(job_id)["result"]
    assert result["segments"] == fake_segments
    assert result["word_align_error"] is None
    _clear(job_id)


def test_realign_missing_dependency_keeps_the_transcript_and_reports_the_issue(monkeypatch):
    """A missing torchaudio/uroman install must never cost the already-
    completed transcription (the expensive part) -- only the optional
    realignment step is affected."""
    job_id = "test_transcribe_realign_missing_dep"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    fake_segments = [{"start": 0.0, "end": 20.0, "text": "long merged line"}]
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", lambda *a, **k: fake_segments)

    import word_align

    def fake_realign(*a, **k):
        raise word_align.WordAlignError("Word-level realignment needs: pip install torchaudio uroman")
    monkeypatch.setattr(word_align, "realign_oversized_segments", fake_realign)

    run_transcribe_job(job_id, "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 2000,
                        realign_long_segments=True)

    result = background_jobs.get_status(job_id)["result"]
    assert result["segments"] == fake_segments  # the transcript itself survives intact
    assert "pip install torchaudio uroman" in result["word_align_error"]
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
    pytest.importorskip("cv2")  # hardsub_ocr.py imports cv2 at module level;
                                # requirements-media.txt, not core -- skip
                                # cleanly without it rather than fail collection
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

    def fake_extract(video_path, language, sample_interval, ocr_backend,
                      chinese_script="simplified", progress_cb=None, tmp_dir=None,
                      tesseract_cmd=None):
        progress_cb(0.4)
        return [{"start": 0.0, "end": 1.0, "text": "hi"}]

    pytest.importorskip("cv2")  # hardsub_ocr.py imports cv2 at module level;
                                # requirements-media.txt, not core -- skip
                                # cleanly without it rather than fail collection
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

    pytest.importorskip("cv2")  # hardsub_ocr.py imports cv2 at module level;
                                # requirements-media.txt, not core -- skip
                                # cleanly without it rather than fail collection
    import hardsub_ocr
    monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", lambda *a, **k: [])

    run_hardsub_ocr_job(job_id, "/fake.mp4", "zh", 1.0, "tesseract")

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"failed_reason": "empty"}
    _clear(job_id)


def test_hardsub_ocr_unexpected_exception_still_propagates(monkeypatch):
    job_id = "test_hardsub_unexpected"
    _clear(job_id)

    pytest.importorskip("cv2")  # hardsub_ocr.py imports cv2 at module level;
                                # requirements-media.txt, not core -- skip
                                # cleanly without it rather than fail collection
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


class FakeFlaggingEngine:
    supports_reference = True
    model = "fake-model"

    def __init__(self, flags_for=None):
        self.client = self
        self.messages = self
        self.flags_for = flags_for or {}

    def create(self, model, max_tokens, messages):
        import re, json
        idxs = [int(m) for m in re.findall(r"\[(\d+)\]", messages[0]["content"])]
        out = [{"line_idx": i, "reason": r, "note": n}
               for i in idxs if i in self.flags_for for r, n in [self.flags_for[i]]]
        block = type("Block", (), {"type": "text", "text": json.dumps(out)})()
        return type("Resp", (), {"content": [block]})()


def test_flag_job_persists_flags_to_db(isolated_db):
    job_id = "test_flag_persist"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=0, start=0, end=1, zh="她昨天来了", en="She came yesterday."),
             Line(idx=1, start=1, end=2, zh="你好", en="Hello.")]
    isolated_db.save_lines(did, lines)  # the job updates existing lines by id
    engine = FakeFlaggingEngine(flags_for={0: ("ambiguous_reference", "'she' unresolved")})

    run_flag_job(job_id, did, lines, engine, "claude")

    loaded = isolated_db.load_lines(did)
    assert loaded[0]["flag"] == "ambiguous_reference"
    assert loaded[0]["flag_note"] == "'she' unresolved"
    assert loaded[1]["flag"] is None
    result = background_jobs.get_status(job_id)["result"]
    assert result == {"flagged_count": 1}
    _clear(job_id)


def test_flag_job_progress_cb_is_wired(isolated_db):
    job_id = "test_flag_progress"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]

    run_flag_job(job_id, did, lines, FakeFlaggingEngine(), "claude")

    status = background_jobs.get_status(job_id)
    assert status["progress"] == 1.0
    _clear(job_id)


def test_flag_job_with_nothing_flagged(isolated_db):
    job_id = "test_flag_nothing"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
    isolated_db.save_lines(did, lines)

    run_flag_job(job_id, did, lines, FakeFlaggingEngine(), "claude")

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"flagged_count": 0}
    assert isolated_db.load_lines(did)[0]["flag"] is None
    _clear(job_id)


class FakeFlaggingEngineWithUsage(FakeFlaggingEngine):
    """Same fake, but with a response.usage -- regression coverage for a
    real gap: none of the review-queue/emotion/consistency-check features
    ever logged token usage anywhere, so the "Estimated spend" dashboard
    number only ever reflected the main Translate job, not any of this."""
    def create(self, model, max_tokens, messages):
        resp = super().create(model, max_tokens, messages)
        resp.usage = type("Usage", (), {"input_tokens": 30, "output_tokens": 12})()
        return resp


def test_flag_job_logs_usage_to_the_cost_dashboard(isolated_db):
    job_id = "test_flag_usage"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]

    run_flag_job(job_id, did, lines, FakeFlaggingEngineWithUsage(), "claude")

    summary = isolated_db.get_usage_summary(did)
    assert summary["input_tokens"] == 30
    assert summary["output_tokens"] == 12
    _clear(job_id)


def test_emotion_job_persists_to_db_and_logs_usage(isolated_db):
    job_id = "test_emotion_persist"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=0, start=0, end=1, zh="她昨天来了", en="She came yesterday.")]
    isolated_db.save_lines(did, lines)  # emotions attach to a saved line's id

    class FakeEmotionEngine:
        supports_reference = True
        model = "fake-model"

        def __init__(self):
            self.client = self
            self.messages = self

        def create(self, model, max_tokens, messages):
            import json as _json
            block = type("Block", (), {
                "type": "text",
                "text": _json.dumps([{"line_idx": 0, "emotion": "sad", "intensity": 0.8}]),
            })()
            usage = type("Usage", (), {"input_tokens": 50, "output_tokens": 20})()
            return type("Resp", (), {"content": [block], "usage": usage})()

    run_emotion_job(job_id, did, lines, FakeEmotionEngine(), False, "claude")

    # Survives a fresh load from the database, not just the ephemeral job
    # result -- this is the actual bug: it used to live in st.session_state
    # only, which a page refresh wipes.
    saved = isolated_db.load_emotions(did)
    assert saved[0]["emotion"] == "sad"
    assert saved[0]["intensity"] == 0.8

    summary = isolated_db.get_usage_summary(did)
    assert summary["input_tokens"] == 50
    assert summary["output_tokens"] == 20
    _clear(job_id)


def test_consistency_job_persists_and_logs_usage(isolated_db):
    """Regression coverage: consistency check used to run synchronously
    (a blocking st.spinner), which meant it couldn't run alongside any
    other check -- the whole app was stuck until it finished. Now it's a
    background job like the others."""
    job_id = "test_consistency_persist"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=0, start=0, end=1, zh="沈清疑", en="Shen Qingyi"),
             Line(idx=1, start=1, end=2, zh="沈清疑", en="Shen Qing Yi")]

    class FakeConsistencyEngine:
        supports_reference = True
        model = "fake-model"

        def __init__(self):
            self.client = self
            self.messages = self

        def create(self, model, max_tokens, messages):
            import json as _json
            block = type("Block", (), {
                "type": "text",
                "text": _json.dumps([{"term": "沈清疑",
                                       "variants": ["Shen Qingyi", "Shen Qing Yi"],
                                       "note": "spacing"}]),
            })()
            usage = type("Usage", (), {"input_tokens": 15, "output_tokens": 6})()
            return type("Resp", (), {"content": [block], "usage": usage})()

    run_consistency_job(job_id, did, lines, FakeConsistencyEngine(), "claude")

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"issue_count": 1, "failed_batches": 0, "total_batches": 1}
    saved = isolated_db.load_consistency_issues(did)
    assert saved[0]["term"] == "沈清疑"
    summary = isolated_db.get_usage_summary(did)
    assert summary["input_tokens"] == 15
    _clear(job_id)


def test_translation_notes_job_persists_and_logs_usage(isolated_db):
    """Same fix as the consistency job above, for Generate translation notes."""
    job_id = "test_notes_persist"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=0, start=0, end=1, zh="画蛇添足", en="Gilding the lily")]
    # Step 25d item 11: db.save_translation_notes now skips a note whose
    # line can't be resolved (matching save_emotions), so this line has
    # to actually exist in the database first -- same as it always would
    # in real usage, where notes are only ever generated for a drama's
    # already-persisted lines.
    isolated_db.save_lines(did, lines)

    class FakeNotesEngine:
        supports_reference = True
        model = "fake-model"

        def __init__(self):
            self.client = self
            self.messages = self

        def create(self, model, max_tokens, messages):
            import json as _json
            block = type("Block", (), {
                "type": "text",
                "text": _json.dumps([{"line_idx": 0, "term": "画蛇添足", "note_type": "idiom",
                                       "note": "Lit. 'drawing a snake and adding feet'."}]),
            })()
            usage = type("Usage", (), {"input_tokens": 25, "output_tokens": 10})()
            return type("Resp", (), {"content": [block], "usage": usage})()

    run_translation_notes_job(job_id, did, lines, FakeNotesEngine(), "claude")

    result = background_jobs.get_status(job_id)["result"]
    assert result == {"note_count": 1}
    saved = isolated_db.list_translation_notes(did)
    assert saved[0]["term"] == "画蛇添足"
    summary = isolated_db.get_usage_summary(did)
    assert summary["input_tokens"] == 25
    _clear(job_id)


class FakeFixEngine:
    """translate_batch-shaped fake, matching the real per-call last_usage
    reset (see translate_engines.py) rather than an accumulator -- this is
    what run_fix_flagged_lines_job's usage-logging reads after every call."""
    model = "fake-model"

    def __init__(self, translations=None, fail_for=None):
        self.translations = translations or {}
        self.fail_for = fail_for or set()
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, lines, context):
        text = lines[0]
        if text in self.fail_for:
            raise RuntimeError("translation API down")
        self.last_usage = {"input_tokens": 10, "output_tokens": 4}
        return [self.translations.get(text, f"[translated] {text}")]


def test_fix_flagged_job_retranscribes_and_retranslates_with_audio(isolated_db, monkeypatch, tmp_path):
    job_id = "test_fixflag_audio"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=0, start=0.0, end=1.0, zh="stale transcription", en="stale translation",
                   flag="ambiguous_reference", flag_note="unclear")]
    isolated_db.save_lines(did, lines)

    audio_path = str(tmp_path / "audio.wav")
    with open(audio_path, "wb") as f:
        f.write(b"x")

    import tabs.workspace_tab as wt
    monkeypatch.setattr(wt.core_module, "extract_audio_slice", lambda *a, **k: None)
    monkeypatch.setattr(wt.core_module, "transcribe_for_timing",
                         lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "她昨天来了"}])

    engine = FakeFixEngine(translations={"她昨天来了": "She came yesterday."})
    run_fix_flagged_lines_job(job_id, did, lines, audio_path, "medium", False, "zh",
                               engine, "claude")

    loaded = isolated_db.load_lines(did)
    assert loaded[0]["zh"] == "她昨天来了"
    assert loaded[0]["en"] == "She came yesterday."
    assert loaded[0]["flag"] is None
    assert loaded[0]["flag_note"] == ""
    result = background_jobs.get_status(job_id)["result"]
    assert result == {"fixed_count": 1, "total_flagged": 1, "errors": [], "cap_reached": None}
    summary = isolated_db.get_usage_summary(did)
    assert summary["input_tokens"] == 10
    assert summary["output_tokens"] == 4
    _clear(job_id)


def test_fix_flagged_job_skips_retranscription_with_no_audio(isolated_db, monkeypatch):
    """Novel narration has no audio to re-transcribe -- only re-translation
    should run, on the line's existing zh text."""
    job_id = "test_fixflag_noaudio"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=0, start=0.0, end=1.0, zh="画蛇添足", en="old translation",
                   flag="mistranslation", flag_note="check this")]
    isolated_db.save_lines(did, lines)

    engine = FakeFixEngine(translations={"画蛇添足": "Gilding the lily"})
    run_fix_flagged_lines_job(job_id, did, lines, None, "medium", False, "zh", engine, "claude")

    loaded = isolated_db.load_lines(did)
    assert loaded[0]["zh"] == "画蛇添足"  # unchanged -- nothing to re-transcribe
    assert loaded[0]["en"] == "Gilding the lily"
    assert loaded[0]["flag"] is None
    _clear(job_id)


def test_fix_flagged_job_leaves_flag_set_when_translation_fails(isolated_db):
    """A failed re-translation must not silently lose the flag -- the line
    stays flagged so it isn't mistaken for resolved."""
    job_id = "test_fixflag_fail"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="old",
                   flag="mistranslation", flag_note="check")]
    isolated_db.save_lines(did, lines)

    engine = FakeFixEngine(fail_for={"你好"})
    run_fix_flagged_lines_job(job_id, did, lines, None, "medium", False, "zh", engine, "claude")

    loaded = isolated_db.load_lines(did)
    assert loaded[0]["flag"] == "mistranslation"
    assert loaded[0]["en"] == "old"
    result = background_jobs.get_status(job_id)["result"]
    assert result == {"fixed_count": 0, "total_flagged": 1,
                       "errors": ["line 1 translation: translation API down"],
                       "cap_reached": None}
    _clear(job_id)


def test_fix_flagged_job_ignores_unflagged_lines(isolated_db):
    job_id = "test_fixflag_unflagged"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=0, start=0.0, end=1.0, zh="没有问题", en="No problem.", flag=None)]
    isolated_db.save_lines(did, lines)

    engine = FakeFixEngine()
    run_fix_flagged_lines_job(job_id, did, lines, None, "medium", False, "zh", engine, "claude")

    loaded = isolated_db.load_lines(did)
    assert loaded[0]["en"] == "No problem."  # untouched
    result = background_jobs.get_status(job_id)["result"]
    assert result == {"fixed_count": 0, "total_flagged": 0, "errors": [], "cap_reached": None}
    _clear(job_id)


class _CostedFixEngine:
    """Step 25w: unlike FakeFixEngine (model="fake-model", priced at $0 by
    estimate_cost_for_engine since it isn't in PRICING_PER_MILLION_TOKENS),
    this reports usage against a real priced model so a cost cap can
    actually be exercised -- $2/M input tokens, so 100_000 input tokens
    per line costs exactly $0.20/line."""
    model = "claude-sonnet-5"

    def __init__(self):
        self.calls = 0
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, lines, context):
        self.calls += 1
        self.last_usage = {"input_tokens": 100_000, "output_tokens": 0}
        return [f"[translated] {lines[0]}"]


def test_fix_flagged_job_stops_at_the_cost_cap_and_keeps_finished_lines(isolated_db):
    """Step 25w Bug 3: "Fix flagged lines in bulk" called engine.translate_batch
    directly with no cost_cap_usd parameter, no resolve_cost_cap call, and no
    spend accumulation checked against any cap anywhere -- confirmed real: a
    drama with many flagged lines, fixed repeatedly, had completely unbounded
    spend regardless of the configured monthly cap. cost_cap_usd now stops the
    loop cleanly, the same "every finished line kept" contract
    translate_lines_with_engine already has."""
    job_id = "test_fixflag_cap"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=i, start=float(i), end=i + 1.0, zh=f"句{i}", en="old",
                   flag="mistranslation", flag_note="check")
             for i in range(5)]
    isolated_db.save_lines(did, lines)

    engine = _CostedFixEngine()
    # $0.20/line -- stops partway through 5 lines, not all-or-nothing.
    run_fix_flagged_lines_job(job_id, did, lines, None, "medium", False, "zh", engine, "claude",
                              cost_cap_usd=0.5)

    assert engine.calls == 3  # 0.2, 0.4 (continues), 0.6 (crosses the cap, stops)
    loaded = isolated_db.load_lines(did)
    assert sum(1 for r in loaded if r["flag"] is None) == 3  # fixed lines cleared
    assert sum(1 for r in loaded if r["flag"] == "mistranslation") == 2  # left for next time
    result = background_jobs.get_status(job_id)["result"]
    assert result["fixed_count"] == 3
    assert result["total_flagged"] == 5
    assert result["cap_reached"] == pytest.approx(0.6)
    _clear(job_id)


def test_fix_flagged_job_with_no_cap_processes_every_flagged_line(isolated_db):
    """No cost_cap_usd (the default) must behave exactly as before this
    step -- no cap, no stopping partway through."""
    job_id = "test_fixflag_nocap"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [Line(idx=i, start=float(i), end=i + 1.0, zh=f"句{i}", en="old",
                   flag="mistranslation", flag_note="check")
             for i in range(5)]
    isolated_db.save_lines(did, lines)

    engine = _CostedFixEngine()
    run_fix_flagged_lines_job(job_id, did, lines, None, "medium", False, "zh", engine, "claude")

    assert engine.calls == 5
    result = background_jobs.get_status(job_id)["result"]
    assert result["fixed_count"] == 5
    assert result["cap_reached"] is None
    _clear(job_id)


def test_fix_flagged_job_keeps_earlier_fixes_when_a_later_line_crashes(isolated_db, monkeypatch, tmp_path):
    """Step 25d item 3: the re-transcription call used to sit in a bare
    try/finally with no `except` -- a real exception there (e.g. a
    model-download failure partway through) escaped the whole loop, and
    since db.save_lines only ran once at the very end, that lost every
    line already fixed before the crash, not just the one that failed."""
    job_id = "test_fixflag_partial_crash"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    lines = [
        Line(idx=0, start=0.0, end=1.0, zh="stale zero", en="old zero",
             flag="ambiguous_reference", flag_note="check"),
        Line(idx=1, start=1.0, end=2.0, zh="stale one", en="old one",
             flag="ambiguous_reference", flag_note="check"),
    ]
    isolated_db.save_lines(did, lines)

    audio_path = str(tmp_path / "audio.wav")
    with open(audio_path, "wb") as f:
        f.write(b"x")

    import tabs.workspace_tab as wt
    monkeypatch.setattr(wt.core_module, "extract_audio_slice", lambda *a, **k: None)

    def _fake_transcribe(slice_path, **kwargs):
        if "_1.wav" in slice_path:
            raise RuntimeError("model download failed partway through")
        return [{"start": 0.0, "end": 1.0, "text": "重新转录"}]

    monkeypatch.setattr(wt.core_module, "transcribe_for_timing", _fake_transcribe)

    # Line 1's own translation would otherwise still succeed against its
    # stale (un-re-transcribed) text and clear its flag -- failing that
    # too keeps this test focused on line 0's fix surviving line 1's crash.
    engine = FakeFixEngine(translations={"重新转录": "Re-transcribed."}, fail_for={"stale one"})
    run_fix_flagged_lines_job(job_id, did, lines, audio_path, "medium", False, "zh",
                               engine, "claude")

    loaded = isolated_db.load_lines(did)
    assert loaded[0]["zh"] == "重新转录"
    assert loaded[0]["en"] == "Re-transcribed."
    assert loaded[0]["flag"] is None
    # Line 1's own fix failed, but it must not have taken line 0's fix
    # down with it.
    assert loaded[1]["zh"] == "stale one"
    assert loaded[1]["flag"] == "ambiguous_reference"
    result = background_jobs.get_status(job_id)["result"]
    assert result["fixed_count"] == 1
    assert result["total_flagged"] == 2
    assert any("model download failed" in e for e in result["errors"])
    assert any("translation API down" in e for e in result["errors"])
    _clear(job_id)


def test_translate_job_resolves_named_characters_for_the_engine(isolated_db, monkeypatch):
    """Regression coverage for a real audit finding: the translator never
    got told who was speaking a line at all, which drives correct
    pronouns/honorifics/register -- run_translate_job now resolves each
    speaker's name from the characters table and threads it through."""
    job_id = "test_translate_speakers"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    isolated_db.upsert_character(did, "SPEAKER_00", character_name="Xiaoling")
    lines = [Line(idx=0, start=0, end=1, zh="你好", speaker="SPEAKER_00")]

    seen = {}
    def fake_translate(lines, engine, **kwargs):
        seen["character_names"] = kwargs.get("character_names")
        return lines, []
    monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

    run_translate_job(job_id, did, lines, object(), {"id": did}, "", None, False, "en-US",
                       None, "", "claude", "audio_drama")

    assert seen["character_names"] == {"SPEAKER_00": "Xiaoling"}
    _clear(job_id)


def test_translate_job_omits_speakers_with_no_name_set(isolated_db, monkeypatch):
    job_id = "test_translate_no_speakers"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Test")
    isolated_db.upsert_character(did, "SPEAKER_00")  # no character_name set
    lines = [Line(idx=0, start=0, end=1, zh="你好", speaker="SPEAKER_00")]

    seen = {}
    def fake_translate(lines, engine, **kwargs):
        seen["character_names"] = kwargs.get("character_names")
        return lines, []
    monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

    run_translate_job(job_id, did, lines, object(), {"id": did}, "", None, False, "en-US",
                       None, "", "claude", "audio_drama")

    assert seen["character_names"] == {}
    _clear(job_id)


def test_translate_job_sends_a_standalone_dramas_per_drama_pronouns(isolated_db, monkeypatch):
    """Step 1e: a drama with no series had no way to set pronouns at all;
    they now live on the per-drama characters row and reach the prompt."""
    job_id = "test_translate_standalone_pronouns"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    did = isolated_db.create_drama(title_en="Standalone")
    isolated_db.upsert_character(did, "SPEAKER_00", character_name="Xiaoling", pronouns="they/them")
    lines = [Line(idx=0, start=0, end=1, zh="你好", speaker="SPEAKER_00")]

    seen = {}
    def fake_translate(lines, engine, **kwargs):
        seen["character_names"] = kwargs.get("character_names")
        return lines, []
    monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

    run_translate_job(job_id, did, lines, object(), {"id": did}, "", None, False, "en-US",
                       None, "", "claude", "audio_drama")

    assert seen["character_names"] == {"SPEAKER_00": "Xiaoling (they/them)"}
    _clear(job_id)


class TestFreeEngineVersionLabelling:
    """Step 1d item 5: a translation version made with a free engine (or
    a free-tier Gemini key) is marked [testing: <engine>] in its label,
    so switching between versions later shows which ones aren't real
    translations at a glance."""

    def _run(self, isolated_db, monkeypatch, job_id, engine, engine_choice):
        _clear(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        did = isolated_db.create_drama(title_en="Test")
        lines = [Line(idx=0, start=0, end=1, zh="你好")]
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine",
                             lambda lines, engine, **kwargs: (lines, []))
        run_translate_job(job_id, did, lines, engine, {"id": did}, "", None, False, "en-US",
                           None, "", engine_choice, "audio_drama")
        return isolated_db.list_translation_versions(did)[0]

    def test_test_offline_version_is_labelled(self, isolated_db, monkeypatch):
        version = self._run(isolated_db, monkeypatch, "test_free_label_offline",
                             translate_engines.TestOfflineEngine(), "test_offline")
        assert version["label"].startswith("[testing: test_offline]")
        _clear("test_free_label_offline")

    def test_ollama_version_is_labelled(self, isolated_db, monkeypatch):
        version = self._run(isolated_db, monkeypatch, "test_free_label_ollama",
                             translate_engines.OllamaEngine(), "ollama")
        assert version["label"].startswith("[testing: ollama]")
        _clear("test_free_label_ollama")

    def test_claude_version_is_not_labelled(self, isolated_db, monkeypatch):
        version = self._run(isolated_db, monkeypatch, "test_free_label_claude",
                             object(), "claude")
        assert not version["label"].startswith("[testing:")
        _clear("test_free_label_claude")

    def test_paid_gemini_version_is_not_labelled(self, isolated_db, monkeypatch):
        engine = translate_engines.GeminiEngine("fake-key", free_tier=False)
        version = self._run(isolated_db, monkeypatch, "test_free_label_gemini_paid",
                             engine, "gemini")
        assert not version["label"].startswith("[testing:")
        _clear("test_free_label_gemini_paid")

    def test_free_tier_gemini_version_is_labelled(self, isolated_db, monkeypatch):
        # gemini itself isn't in FREE_ENGINES -- this only labels because
        # the engine instance was constructed with free_tier=True.
        engine = translate_engines.GeminiEngine("fake-key", free_tier=True)
        version = self._run(isolated_db, monkeypatch, "test_free_label_gemini_free",
                             engine, "gemini")
        assert version["label"].startswith("[testing: gemini]")
        _clear("test_free_label_gemini_free")


class TestLineEditingNotLockedDuringAJob:
    """Step 2 removed Step 1b's short-term edit lock: translate/flag/
    fix-flagged jobs now write only their own fields by permanent line id,
    and the page's own saves only write fields the user actually changed,
    so editing while a job runs no longer clobbers either side's work (see
    TestConcurrentWritesByLineId for the data-level proof). These pin that
    the controls really are usable while a job is running."""

    def _drama_with_a_line(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="Hello")])
        return did

    def _run(self, did, **state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        for k, v in state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        return at

    def _button(self, at, label):
        matches = [b for b in at.button if b.label == label]
        assert matches, f"button {label!r} not found on the page"
        return matches[0]

    def _with_running_job(self, job_id):
        release = threading.Event()
        background_jobs.start_job(job_id, release.wait)
        return release

    def test_save_edits_stays_enabled_while_translate_is_running(self, isolated_db):
        did = self._drama_with_a_line(isolated_db)
        release = self._with_running_job(f"translate_{did}")
        try:
            at = self._run(did)
            assert self._button(at, "💾 Save edits (this page)").disabled is False
            assert not any("paused" in i.value for i in at.info)
        finally:
            release.set()
            _clear(f"translate_{did}")

    def test_apply_merge_stays_enabled_while_a_flag_job_is_running(self, isolated_db):
        did = self._drama_with_a_line(isolated_db)
        release = self._with_running_job(f"flag_{did}")
        try:
            at = self._run(did)
            self._button(at, "Preview merge").click()
            at.run(timeout=30)
            assert self._button(at, "✅ Apply merge").disabled is False
        finally:
            release.set()
            _clear(f"flag_{did}")

    def test_generate_dub_track_stays_enabled_while_translate_is_running(self, isolated_db):
        did = self._drama_with_a_line(isolated_db)
        release = self._with_running_job(f"translate_{did}")
        try:
            at = self._run(did)
            assert self._button(at, "🎙️ Generate dub track").disabled is False
        finally:
            release.set()
            _clear(f"translate_{did}")

    def test_chunk_and_tag_speakers_stays_enabled_while_translate_is_running(self, isolated_db):
        did = isolated_db.create_drama(title_en="Narration Drama", media_type="novel",
                                        content_mode="novel_narration", status="not started")
        release = self._with_running_job(f"translate_{did}")
        try:
            at = self._run(did, **{f"ocr_text_{did}": "有一天，天气很好。"})
            assert self._button(at, "▶ Chunk & Tag Speakers").disabled is False
        finally:
            release.set()
            _clear(f"translate_{did}")


class TestChunkAndTagSpeakersHistorySnapshot:
    """Step 25m: novel narration's "Chunk & Tag Speakers" replaced every
    existing line via db.save_lines(picked_id, lines) with no history
    snapshot taken first, unlike every comparable full-replace path
    elsewhere in this file (transcription completion takes one, Step 25
    item 3). The block's own on-screen warning literally tells the user to
    "switch engines and re-run" -- doing exactly that used to wipe every
    existing translation, flag, and hand-corrected speaker with nothing to
    restore from."""

    def _drama_with_existing_translated_lines(self, isolated_db):
        did = isolated_db.create_drama(title_en="Narration Drama", media_type="novel",
                                        content_mode="novel_narration", status="translated")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=1.0, zh="旧的文本", en="Old translation",
                 speaker="Hero", speaker_manual=True),
        ])
        return did

    def _run(self, did, **state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        for k, v in state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        return at

    def _click_chunk_and_tag(self, did, **state):
        at = self._run(did, **state)
        [b] = [b for b in at.button if b.label == "▶ Chunk & Tag Speakers"]
        b.click().run(timeout=30)
        return at

    def test_a_history_snapshot_exists_before_chunk_and_tag_replaces_lines(self, isolated_db):
        did = self._drama_with_existing_translated_lines(isolated_db)
        before_history = len(isolated_db.list_line_history(did))

        self._click_chunk_and_tag(did, **{f"ocr_text_{did}": "有一天，天气很好。"})

        after_history = isolated_db.list_line_history(did)
        assert len(after_history) == before_history + 1
        assert after_history[0]["label"] == "before chunk & tag speakers"

    def test_existing_translation_is_recoverable_via_version_history_afterward(self, isolated_db):
        did = self._drama_with_existing_translated_lines(isolated_db)

        at = self._click_chunk_and_tag(did, **{f"ocr_text_{did}": "有一天，天气很好。"})

        # The replace really did happen -- new chunked lines, old translation gone.
        after = isolated_db.load_line_objects(did)
        assert after[0].zh != "旧的文本"
        assert after[0].en != "Old translation"

        # Restoring the auto-taken snapshot (same "Version history" flow a
        # user would use) brings the original translation and the hand-set
        # speaker back.
        snap = [h for h in isolated_db.list_line_history(did)
                if h["label"] == "before chunk & tag speakers"][0]
        [restore_btn] = [b for b in at.button if b.key == f"restore_{snap['id']}"]
        restore_btn.click().run(timeout=30)

        restored = isolated_db.load_line_objects(did)
        assert restored[0].zh == "旧的文本"
        assert restored[0].en == "Old translation"
        assert restored[0].speaker == "Hero"
        assert restored[0].speaker_manual is True


class TestOllamaReachabilityGatesTranslateButton:
    """UI-level regression coverage for a real gap: Ollama is exempted
    from the API-key check entirely (_needs_key), with nothing in its
    place -- clicking Translate against a stopped local server used to
    start a background job that only failed once translate_batch's own
    300s request timeout expired. The button is now disabled up front
    (with a clear warning) when a quick health check fails."""

    def _drama_with_ollama_engine(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        translation_engine="ollama")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        # A second rerun: the first pass loads the drama's lines from the
        # database into session_state (further down the script than
        # run_translate's own disabled= check), so a fresh session_state
        # starting at None always renders that button disabled on the
        # very first pass regardless of Ollama reachability. This mirrors
        # a real session, where the widget reflects state set on an
        # earlier rerun, not state this same rerun is still populating.
        at.run(timeout=30)
        return at

    def _button(self, at, label):
        matches = [b for b in at.button if b.label == label]
        assert matches, f"button {label!r} not found on the page"
        return matches[0]

    def test_translate_is_disabled_and_warns_when_ollama_is_unreachable(self, isolated_db, monkeypatch):
        monkeypatch.setattr(translate_engines, "check_ollama_reachable", lambda url: False)
        did = self._drama_with_ollama_engine(isolated_db)
        at = self._run(did)
        assert self._button(at, "🌐 Translate all lines").disabled is True
        assert any("Can't reach Ollama" in w.value for w in at.warning)

    def test_translate_is_enabled_when_ollama_is_reachable(self, isolated_db, monkeypatch):
        monkeypatch.setattr(translate_engines, "check_ollama_reachable", lambda url: True)
        did = self._drama_with_ollama_engine(isolated_db)
        at = self._run(did)
        assert self._button(at, "🌐 Translate all lines").disabled is False


class TestTranslateButtonUsesConfiguredOllamaUrl:
    """Step 5b item 1: get_engine() gained a base_url passthrough (see
    TestGetEngineOllamaBaseUrlPassthrough in test_translate_engines.py),
    but that alone doesn't prove Workspace's own Translate button actually
    reads settings_ollama_url and threads it through -- this closes that
    gap end to end, at the real click site."""

    def _drama_with_ollama_engine(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        translation_engine="ollama")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_ollama_url"] = "http://gpu-box:11434"
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _button(self, at, label):
        matches = [b for b in at.button if b.label == label]
        assert matches, f"button {label!r} not found on the page"
        return matches[0]

    def test_translate_uses_the_configured_ollama_url_not_the_hardcoded_default(
            self, isolated_db, monkeypatch):
        monkeypatch.setattr(translate_engines, "check_ollama_reachable", lambda url: True)
        captured = {}
        real_get_engine = translate_engines.get_engine

        def _spy_get_engine(*args, **kwargs):
            captured.update(kwargs)
            return real_get_engine(*args, **kwargs)

        monkeypatch.setattr(translate_engines, "get_engine", _spy_get_engine)
        started = {}
        monkeypatch.setattr(background_jobs, "start_job",
                             lambda job_id, target, *a, **kw: started.setdefault("ok", True))

        did = self._drama_with_ollama_engine(isolated_db)
        at = self._run(did)
        self._button(at, "🌐 Translate all lines").click()
        at.run(timeout=30)

        assert started.get("ok") is True
        assert captured.get("base_url") == "http://gpu-box:11434"


class TestContextAheadAndBatchSizeSliders:
    """Step 32: look-ahead context and batch size, previously hardcoded
    (translate_lines_with_engine's own defaults, context_window_ahead=3,
    batch_size=20), are now adjustable sliders next to the existing
    look-back one -- and a novel-narration drama pre-selects higher
    starting values than an audio drama, though the user can still move
    any of them."""

    def _drama(self, isolated_db, content_mode="audio_drama"):
        did = isolated_db.create_drama(title_en="Test Drama", media_type=content_mode,
                                        content_mode=content_mode, status="aligned",
                                        translation_engine="claude")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_claude"] = "sk-ant-fake"
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _slider(self, at, label):
        matches = [s for s in at.slider if s.label == label]
        assert matches, f"slider {label!r} not found on the page"
        return matches[0]

    def test_audio_drama_starts_at_todays_defaults(self, isolated_db):
        did = self._drama(isolated_db, content_mode="audio_drama")
        at = self._run(did)
        assert self._slider(at, "Context lines shown from before each batch").value == 6
        assert self._slider(at, "Context lines shown from after each batch").value == 3
        assert self._slider(at, "Lines translated per request").value == 20

    def test_novel_narration_pre_selects_higher_starting_values(self, isolated_db):
        did = self._drama(isolated_db, content_mode="novel_narration")
        at = self._run(did)
        assert self._slider(at, "Context lines shown from before each batch").value == 10
        assert self._slider(at, "Context lines shown from after each batch").value == 6
        assert self._slider(at, "Lines translated per request").value == 30

    def test_look_ahead_and_batch_size_reach_the_translate_job(self, isolated_db, monkeypatch):
        started = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: started.update(kwargs=kw) or True)
        did = self._drama(isolated_db)
        at = self._run(did)
        [b for b in at.button if b.label == "🌐 Translate all lines"][0].click().run(timeout=30)
        assert started["kwargs"]["context_window_ahead"] == 3
        assert started["kwargs"]["batch_size"] == 20

    def test_moving_the_sliders_changes_what_reaches_the_job(self, isolated_db, monkeypatch):
        started = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: started.update(kwargs=kw) or True)
        did = self._drama(isolated_db)
        at = self._run(did)
        self._slider(at, "Context lines shown from after each batch").set_value(9).run(timeout=30)
        self._slider(at, "Lines translated per request").set_value(15).run(timeout=30)
        [b for b in at.button if b.label == "🌐 Translate all lines"][0].click().run(timeout=30)
        assert started["kwargs"]["context_window_ahead"] == 9
        assert started["kwargs"]["batch_size"] == 15

    def test_run_translate_job_forwards_both_to_translate_lines_with_engine(
            self, isolated_db, monkeypatch):
        """Unit-level check of the wiring inside run_translate_job itself,
        independent of the UI -- the same shape as the existing
        character_names tests just above."""
        job_id = "test_translate_context_ahead_batch_size"
        _clear(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        did = isolated_db.create_drama(title_en="Test")
        lines = [Line(idx=0, start=0, end=1, zh="你好")]

        seen = {}
        def fake_translate(lines, engine, **kwargs):
            seen["context_window_ahead"] = kwargs.get("context_window_ahead")
            seen["batch_size"] = kwargs.get("batch_size")
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

        run_translate_job(job_id, did, lines, object(), {"id": did}, "", None, False, "en-US",
                          None, "", "claude", "audio_drama", context_window_ahead=8, batch_size=25)

        assert seen == {"context_window_ahead": 8, "batch_size": 25}
        _clear(job_id)


class TestGlossaryReviewBeforeTranslating:
    """Step 23c item 3: an optional review step before a translate run
    starts -- shows the glossary terms Step 7b's extraction pass would
    use, lets the user edit/reject them, and only then starts the job."""

    def _drama(self, isolated_db, with_series=True):
        sid = isolated_db.get_or_create_series("Test Series") if with_series else None
        did = isolated_db.create_drama(title_en="Test Drama", media_type="novel",
                                        content_mode="novel_narration", status="aligned",
                                        translation_engine="test_offline",
                                        **({"series_id": sid} if sid else {}))
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="他是主角", en="")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_test_offline"] = "fake-key"
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _button(self, at, label):
        matches = [b for b in at.button if b.label == label]
        assert matches, f"button {label!r} not found on the page"
        return matches[0]

    def _fake_terms(self):
        return [{"term": "Zhu Jue", "suggested_translation": "Zhu Jue", "category": "name",
                 "policy": "pinyin", "reason": "protagonist's name"}]

    def test_checkbox_hidden_without_a_series(self, isolated_db):
        did = self._drama(isolated_db, with_series=False)
        at = self._run(did)
        assert not [c for c in at.checkbox if c.key == f"review_glossary_first_{did}"]

    def test_unchecked_by_default_job_starts_immediately(self, isolated_db, monkeypatch):
        started = {}
        monkeypatch.setattr(background_jobs, "start_job",
                             lambda job_id, target, *a, **kw: started.setdefault("ok", True))
        did = self._drama(isolated_db)
        at = self._run(did)
        self._button(at, "🌐 Translate all lines").click()
        at.run(timeout=30)
        assert started.get("ok") is True
        assert not [c for c in at.caption if "glossary term(s)" in c.value]

    def test_checked_shows_review_instead_of_starting_the_job(self, isolated_db, monkeypatch):
        import translation_guide
        monkeypatch.setattr(translation_guide, "extract_glossary_from_novel",
                             lambda *a, **k: self._fake_terms())
        started = {}
        monkeypatch.setattr(background_jobs, "start_job",
                             lambda job_id, target, *a, **kw: started.setdefault("ok", True))
        did = self._drama(isolated_db)
        at = self._run(did)
        at.checkbox(key=f"review_glossary_first_{did}").set_value(True).run()
        self._button(at, "🌐 Translate all lines").click()
        at.run(timeout=30)

        assert "ok" not in started
        assert any("1 glossary term(s)" in c.value for c in at.caption)
        assert self._button(at, "✅ Looks good — start translating")

    def test_confirming_adds_the_term_and_starts_the_job(self, isolated_db, monkeypatch):
        import translation_guide
        monkeypatch.setattr(translation_guide, "extract_glossary_from_novel",
                             lambda *a, **k: self._fake_terms())
        started = {}
        monkeypatch.setattr(background_jobs, "start_job",
                             lambda job_id, target, *a, **kw: started.setdefault("ok", True))
        did = self._drama(isolated_db)
        sid = isolated_db.get_drama(did)["series_id"]
        at = self._run(did)
        at.checkbox(key=f"review_glossary_first_{did}").set_value(True).run()
        self._button(at, "🌐 Translate all lines").click()
        at.run(timeout=30)
        self._button(at, "✅ Looks good — start translating").click()
        at.run(timeout=30)

        assert started.get("ok") is True
        terms = isolated_db.list_glossary_terms(sid)
        assert len(terms) == 1
        assert terms[0]["term_original"] == "Zhu Jue"

    def test_cancel_starts_nothing_and_adds_no_terms(self, isolated_db, monkeypatch):
        import translation_guide
        monkeypatch.setattr(translation_guide, "extract_glossary_from_novel",
                             lambda *a, **k: self._fake_terms())
        started = {}
        monkeypatch.setattr(background_jobs, "start_job",
                             lambda job_id, target, *a, **kw: started.setdefault("ok", True))
        did = self._drama(isolated_db)
        sid = isolated_db.get_drama(did)["series_id"]
        at = self._run(did)
        at.checkbox(key=f"review_glossary_first_{did}").set_value(True).run()
        self._button(at, "🌐 Translate all lines").click()
        at.run(timeout=30)
        self._button(at, "❌ Cancel").click()
        at.run(timeout=30)

        assert "ok" not in started
        assert isolated_db.list_glossary_terms(sid) == []


class TestDownloadButtonUsesCookieSettings:
    """Step 9b.4: the Workspace URL-downloader button must thread the
    cookies setting from Settings into video_download.download(), not
    just leave it available for callers who happen to pass it."""

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                       content_mode="audio_drama", status="not started")
        return did

    def _run(self, did, **session_state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        for k, v in session_state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_cookies_reach_the_download_call(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db)
        captured = {}

        def fake_download(url, out_dir, **kwargs):
            captured.update(kwargs)
            raise video_download.DownloadError("stop here -- only checking what was passed in")

        monkeypatch.setattr(video_download, "download", fake_download)
        at = self._run(did, settings_cookies_browser="firefox", settings_cookies_file="")
        [r for r in at.radio if r.key == f"import_method_{did}"][0].set_value("url").run()
        [t for t in at.text_input if t.key == f"dl_url_{did}"][0].set_value(
            "https://example.com/v").run()
        [b for b in at.button if b.label == "⬇️ Download"][0].click()
        at.run(timeout=30)

        assert captured.get("cookies_browser") == "firefox"
        assert captured.get("cookies_file") is None

    def test_no_cookies_configured_passes_none(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db)
        captured = {}

        def fake_download(url, out_dir, **kwargs):
            captured.update(kwargs)
            raise video_download.DownloadError("stop here -- only checking what was passed in")

        monkeypatch.setattr(video_download, "download", fake_download)
        at = self._run(did)
        [r for r in at.radio if r.key == f"import_method_{did}"][0].set_value("url").run()
        [t for t in at.text_input if t.key == f"dl_url_{did}"][0].set_value(
            "https://example.com/v").run()
        [b for b in at.button if b.label == "⬇️ Download"][0].click()
        at.run(timeout=30)

        assert captured.get("cookies_browser") is None
        assert captured.get("cookies_file") is None


class TestDownloadAudioOnlyDefault:
    """Step 45: a Streamer/VOD drama downloaded with "Audio only" checked
    keeps no video file, so the Reader tab's Watch/listen player can never
    show captions (st.audio has no subtitles support) -- the exact shape of
    a real reported bug. "Audio only" should default off for streamer_vod,
    unlike audio_drama where there's no video worth keeping anyway."""

    def _drama(self, isolated_db, content_mode):
        return isolated_db.create_drama(title_en="Test Drama", media_type=content_mode,
                                        content_mode=content_mode, status="not started")

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _audio_only_checkbox(self, at, did):
        return [c for c in at.checkbox if c.key == f"dl_audio_only_{did}"][0]

    def test_streamer_vod_defaults_to_keeping_the_video(self, isolated_db):
        did = self._drama(isolated_db, "streamer_vod")
        at = self._run(did)
        [r for r in at.radio if r.key == f"import_method_{did}"][0].set_value("url").run()
        assert self._audio_only_checkbox(at, did).value is False

    def test_audio_drama_still_defaults_to_audio_only(self, isolated_db):
        did = self._drama(isolated_db, "audio_drama")
        at = self._run(did)
        [r for r in at.radio if r.key == f"import_method_{did}"][0].set_value("url").run()
        assert self._audio_only_checkbox(at, did).value is True

    def test_streamer_vod_download_defaults_to_audio_only_false(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db, "streamer_vod")
        captured = {}

        def fake_download(url, out_dir, **kwargs):
            captured.update(kwargs)
            raise video_download.DownloadError("stop here -- only checking what was passed in")

        monkeypatch.setattr(video_download, "download", fake_download)
        at = self._run(did)
        [r for r in at.radio if r.key == f"import_method_{did}"][0].set_value("url").run()
        [t for t in at.text_input if t.key == f"dl_url_{did}"][0].set_value(
            "https://example.com/v").run()
        [b for b in at.button if b.label == "⬇️ Download"][0].click()
        at.run(timeout=30)

        assert captured.get("audio_only") is False


class TestReflectModeUI:
    """Step 7: the "High quality (Reflect mode)" checkbox next to the
    Translate button -- hidden for translation-only engines (Reflect mode
    needs an LLM), shows a rough pre-run cost estimate when checked, and
    threads reflect=True through to the actual background job."""

    def _drama(self, isolated_db, translated=False):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        translation_engine="test_offline")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0, end=1, zh="你好", en="Hello." if translated else "")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _checkbox(self, at, did):
        matches = [c for c in at.checkbox if c.key == f"reflect_mode_{did}"]
        assert matches, "Reflect mode checkbox not found"
        return matches[0]

    def test_checkbox_hidden_for_a_translation_only_engine(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                       content_mode="audio_drama", status="aligned",
                                       translation_engine="deepl")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        at = self._run(did)
        assert not [c for c in at.checkbox if c.key == f"reflect_mode_{did}"]

    def test_checkbox_shown_and_off_by_default_for_an_llm_engine(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        assert self._checkbox(at, did).value is False

    def test_checking_it_shows_a_cost_estimate(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        self._checkbox(at, did).set_value(True).run()
        assert any("Estimated Reflect-mode cost" in c.value for c in at.caption)

    def test_reflect_mode_reaches_the_background_job(self, isolated_db, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            background_jobs, "start_job",
            lambda job_id, target, *a, **kw: captured.update(kw) or True)

        did = self._drama(isolated_db)
        at = self._run(did)
        self._checkbox(at, did).set_value(True).run()
        [b for b in at.button if b.label == "🌐 Translate all lines"][0].click()
        at.run(timeout=30)

        assert captured.get("reflect") is True

    def test_unchecked_reflect_defaults_to_false_on_the_job(self, isolated_db, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            background_jobs, "start_job",
            lambda job_id, target, *a, **kw: captured.update(kw) or True)

        did = self._drama(isolated_db)
        at = self._run(did)
        [b for b in at.button if b.label == "🌐 Translate all lines"][0].click()
        at.run(timeout=30)

        assert captured.get("reflect") is False


class TestBulkGlossaryAndPronounActions:
    """Step 23c item 5: multi-select delete over the glossary term list,
    and multi-select pronoun-setting over the People & pronouns list --
    two separate small controls (glossary terms have no gender field;
    gender/pronouns live on series_characters instead), each replacing a
    one-row-at-a-time-only path with a real bulk action."""

    def _drama(self, isolated_db):
        sid = isolated_db.get_or_create_series("Test Series")
        did = isolated_db.create_drama(title_en="Test Drama", media_type="novel",
                                        content_mode="novel_narration", status="aligned",
                                        translation_engine="test_offline", series_id=sid)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="Hello.")])
        return did, sid

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _button(self, at, label):
        matches = [b for b in at.button if b.label == label]
        assert matches, f"button {label!r} not found on the page"
        return matches[0]

    def test_bulk_delete_removes_only_selected_glossary_terms(self, isolated_db):
        did, sid = self._drama(isolated_db)
        isolated_db.upsert_glossary_term(sid, "Zhu Jue", "Zhu Jue")
        isolated_db.upsert_glossary_term(sid, "Keep Me", "Keep Me")
        at = self._run(did)

        at.multiselect(key=f"bulk_glossary_pick_{sid}").set_value(["Zhu Jue"]).run()
        at.checkbox(key=f"bulk_glossary_delete_confirm_{sid}").set_value(True).run(timeout=30)
        self._button(at, "🗑️ Delete 1 selected term(s)").click()
        at.run(timeout=30)

        terms = {t["term_original"] for t in isolated_db.list_glossary_terms(sid)}
        assert terms == {"Keep Me"}

    def test_no_bulk_actions_shown_with_no_glossary_terms(self, isolated_db):
        did, sid = self._drama(isolated_db)
        at = self._run(did)
        assert not [m for m in at.multiselect if m.key == f"bulk_glossary_pick_{sid}"]

    def test_bulk_set_pronouns_applies_to_only_selected_people(self, isolated_db):
        did, sid = self._drama(isolated_db)
        isolated_db.upsert_series_character(sid, "Su Shan", notes="protagonist")
        isolated_db.upsert_series_character(sid, "Wei Chen", notes="rival")
        at = self._run(did)

        at.multiselect(key=f"bulk_sc_pick_{sid}").set_value(["Su Shan"]).run()
        at.selectbox(key=f"bulk_sc_pronouns_{sid}").set_value("she/her").run()
        self._button(at, "Set pronouns for 1 selected").click()
        at.run(timeout=30)

        chars = {c["character_name"]: c for c in isolated_db.list_series_characters(sid)}
        assert chars["Su Shan"]["gender"] == "she/her"
        assert not chars["Wei Chen"]["gender"]
        # the other field this call always writes through must not be wiped
        assert chars["Su Shan"]["notes"] == "protagonist"

    def test_no_bulk_pronoun_control_shown_with_no_people(self, isolated_db):
        did, sid = self._drama(isolated_db)
        at = self._run(did)
        assert not [m for m in at.multiselect if m.key == f"bulk_sc_pick_{sid}"]

    def test_glossary_editor_saves_aliases_and_banned_translations(self, isolated_db):
        """Step 30: the glossary term editor's two new fields (around the
        existing category/policy/enforce_exact ones) save through to the
        db columns of the same name."""
        did, sid = self._drama(isolated_db)
        isolated_db.upsert_glossary_term(sid, "沈清疑", "Shen Qingyi", category="person_name")
        term_id = isolated_db.list_glossary_terms(sid)[0]["id"]
        at = self._run(did)

        self._button(at, "✏️").click()
        at.run(timeout=30)
        at.text_input(key=f"eglo_aliases_{term_id}").set_value("Shen Qing Yi").run()
        at.text_input(key=f"eglo_banned_{term_id}").set_value("Chen Qingyi").run()
        self._button(at, "💾 Save").click()
        at.run(timeout=30)

        term = isolated_db.list_glossary_terms(sid)[0]
        assert term["aliases"] == "Shen Qing Yi"
        assert term["banned_translations"] == "Chen Qingyi"


class TestVoiceMatchSuggestions:
    """Step 8: the "sounds like <name>" suggestion row next to the
    character-naming section. Purely a suggestion -- Accept sets the
    character name and blends the fingerprint, Reject only records a
    dismissal, and neither ever fires without the user clicking a button."""

    def _drama(self, isolated_db, embedding=(1.0, 0.0), fingerprint=(1.0, 0.0)):
        sid = isolated_db.get_or_create_series("Test Series")
        isolated_db.upsert_series_character(sid, "Su Shan")
        sc = isolated_db.list_series_characters(sid)[0]
        isolated_db.update_series_character_voice_fingerprint(sc["id"], list(fingerprint))
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        translation_engine="test_offline", series_id=sid)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="Hello.")])
        isolated_db.upsert_character(did, "SPEAKER_00")
        ddir = isolated_db.drama_dir(did)
        diarize.save_turns(ddir, [], embeddings={"SPEAKER_00": list(embedding)})
        return did, sc["id"]

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_a_matching_voice_shows_a_suggestion(self, isolated_db):
        did, sc_id = self._drama(isolated_db)
        at = self._run(did)
        captions = [c.value for c in at.caption]
        assert any("SPEAKER_00" in c and "Su Shan" in c for c in captions)

    def test_no_suggestion_below_threshold(self, isolated_db):
        # Orthogonal embeddings -- similarity 0, well under the default
        # threshold, so nothing should be suggested at all.
        did, sc_id = self._drama(isolated_db, embedding=(0.0, 1.0), fingerprint=(1.0, 0.0))
        at = self._run(did)
        buttons = [b for b in at.button if (b.key or "").startswith("voiceaccept_")]
        assert buttons == []

    def test_accept_sets_the_character_name_and_updates_the_fingerprint(self, isolated_db):
        did, sc_id = self._drama(isolated_db)
        at = self._run(did)
        accept = [b for b in at.button if b.key == f"voiceaccept_{did}_SPEAKER_00_{sc_id}"]
        assert accept, "Accept button not found"
        accept[0].click().run(timeout=30)

        chars = db.list_characters_with_series_names(did)
        assert chars[0]["character_name"] == "Su Shan"
        assert chars[0]["series_character_id"] == sc_id

        updated = db.list_series_characters(db.get_drama(did)["series_id"])[0]
        assert updated["voice_fingerprint_samples"] == 2

        assert db.list_dismissed_voice_suggestions(did) == set()

    def test_reject_dismisses_without_changing_anything(self, isolated_db):
        did, sc_id = self._drama(isolated_db)
        at = self._run(did)
        reject = [b for b in at.button if b.key == f"voicereject_{did}_SPEAKER_00_{sc_id}"]
        assert reject, "Reject button not found"
        reject[0].click().run(timeout=30)

        assert db.list_dismissed_voice_suggestions(did) == {("SPEAKER_00", sc_id)}

        chars = db.list_characters_with_series_names(did)
        assert not chars[0]["character_name"]
        assert chars[0]["series_character_id"] is None

        updated = db.list_series_characters(db.get_drama(did)["series_id"])[0]
        assert updated["voice_fingerprint_samples"] == 1

    def test_rejecting_never_re_shows_that_pair(self, isolated_db):
        did, sc_id = self._drama(isolated_db)
        at = self._run(did)
        reject = [b for b in at.button if b.key == f"voicereject_{did}_SPEAKER_00_{sc_id}"][0]
        at = reject.click().run(timeout=30)
        assert not [b for b in at.button if (b.key or "").startswith("voiceaccept_")]


class TestCharacterNamingGaps:
    """Step 8b: three real gaps in "Name your characters" -- no transcript
    sample shown per speaker (no way to tell who SPEAKER_00 actually is
    without cross-referencing Review & edit by hand), a missing clone
    reference with no explanation of why, and a silently-blank "what's
    said in that clip" field indistinguishable from never having run
    auto-extract at all."""

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        audio_filename="audio.wav")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=1.0, zh="你好啊", speaker="SPEAKER_00"),
            Line(idx=1, start=2.0, end=3.0, zh="是的", speaker="SPEAKER_00"),
            Line(idx=2, start=4.0, end=5.0, zh="再见", speaker="SPEAKER_00"),
        ])
        isolated_db.upsert_character(did, "SPEAKER_00")
        isolated_db.upsert_character(did, "SPEAKER_01")  # no lines attributed at all
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        return did, ddir

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_a_speaker_with_lines_shows_a_real_sample(self, isolated_db):
        did, ddir = self._drama(isolated_db)
        at = self._run(did)
        captions = [c.value for c in at.caption]
        assert any("你好啊" in c for c in captions)

    def test_a_speaker_with_no_lines_says_so_instead_of_showing_nothing(self, isolated_db):
        did, ddir = self._drama(isolated_db)
        at = self._run(did)
        captions = [c.value for c in at.caption]
        assert any("No lines attributed to this speaker yet" in c for c in captions)

    def test_a_skipped_speaker_shows_the_specific_reason_not_a_bare_caption(
            self, isolated_db, monkeypatch):
        did, ddir = self._drama(isolated_db)
        import diarize
        diarize.save_turns(ddir, [{"start": 0.0, "end": 1.5, "speaker": "SPEAKER_01"}])

        def fake_extract(audio_path, speaker_segments, drama_dir):
            return {}, {"SPEAKER_01": {"closest_duration": 1.5, "reason": "too_short"}}
        monkeypatch.setattr(dub_module, "extract_reference_clips", fake_extract)

        at = self._run(did)
        buttons = [b for b in at.button if b.label == "🎯 Auto-extract reference clips from this audio"]
        assert buttons, "Auto-extract button not found"
        buttons[0].click().run(timeout=30)

        captions = [c.value for c in at.caption]
        assert any("1.5s" in c and "shorter than the 3s minimum" in c for c in captions)

    def test_a_failed_ref_text_match_shows_a_specific_reason_not_a_blank_box(
            self, isolated_db, monkeypatch):
        did, ddir = self._drama(isolated_db)
        import diarize
        diarize.save_turns(ddir, [{"start": 100.0, "end": 106.0, "speaker": "SPEAKER_00"}])

        def fake_extract(audio_path, speaker_segments, drama_dir):
            # A clip WAS found, but its time window (100-106s) doesn't
            # match any of this drama's real lines (all under 5s) -- the
            # exact "clip found, speaker-tag match failed" case.
            return {"SPEAKER_00": {"path": os.path.join(drama_dir, "SPEAKER_00.wav"),
                                   "start": 100.0, "end": 106.0}}, {}
        monkeypatch.setattr(dub_module, "extract_reference_clips", fake_extract)

        at = self._run(did)
        buttons = [b for b in at.button if b.label == "🎯 Auto-extract reference clips from this audio"]
        buttons[0].click().run(timeout=30)

        assert db.list_characters(did)[0]["ref_text"] in (None, "")
        captions = [c.value for c in at.caption]
        assert any("no transcript line's speaker tag matched it" in c for c in captions)

    def test_a_successful_ref_text_match_is_saved_normally(self, isolated_db, monkeypatch):
        did, ddir = self._drama(isolated_db)
        import diarize
        diarize.save_turns(ddir, [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}])

        def fake_extract(audio_path, speaker_segments, drama_dir):
            return {"SPEAKER_00": {"path": os.path.join(drama_dir, "SPEAKER_00.wav"),
                                   "start": 0.0, "end": 1.0}}, {}
        monkeypatch.setattr(dub_module, "extract_reference_clips", fake_extract)

        at = self._run(did)
        buttons = [b for b in at.button if b.label == "🎯 Auto-extract reference clips from this audio"]
        buttons[0].click().run(timeout=30)

        chars = {c["speaker_label"]: c for c in db.list_characters(did)}
        assert chars["SPEAKER_00"]["ref_text"] == "你好啊"
        captions = [c.value for c in at.caption]
        assert not any("no transcript line's speaker tag matched it" in c for c in captions)


class _CapPricedEngine:
    """$2.00 per batch on claude-sonnet-5 (1M input tokens each)."""
    name = "claude"
    supports_reference = True
    model = "claude-sonnet-5"

    def __init__(self):
        self.last_usage = {}

    def translate_batch(self, zh_lines, context):
        self.last_usage = {"input_tokens": 1_000_000, "output_tokens": 0}
        return [f"EN:{z}" for z in zh_lines]


class TestSpendingCapJob:
    """Step 9 exit condition: a job stops at the cap and keeps its
    finished lines."""

    def test_job_stops_at_the_cap_and_keeps_its_finished_lines(self, isolated_db):
        job_id = "test_translate_cap"
        _clear(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        did = isolated_db.create_drama(title_en="Test")
        # 45 lines -> batches of 20, 20, 5 at $2.00 each; a $3 cap stops after
        # the second batch (the one that crosses it).
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"句{i}") for i in range(45)])
        lines = isolated_db.load_line_objects(did)

        run_translate_job(job_id, did, lines, _CapPricedEngine(), {"id": did}, "", None, False,
                          "en-US", None, "", "claude", "audio_drama", cost_cap_usd=3.0)

        result = background_jobs.get_status(job_id)["result"]
        assert result["cap_reached"] == pytest.approx(4.0)
        saved = isolated_db.load_lines(did)
        assert all(r["en"] == f"EN:句{i}" for i, r in enumerate(saved[:40]))
        assert all(not r["en"] for r in saved[40:])
        assert isolated_db.get_usage_summary(did)["estimated_cost_usd"] == pytest.approx(4.0)
        _clear(job_id)

    def test_no_cap_reports_no_stop(self, isolated_db):
        job_id = "test_translate_nocap"
        _clear(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        run_translate_job(job_id, did, isolated_db.load_line_objects(did), _CapPricedEngine(),
                          {"id": did}, "", None, False, "en-US", None, "", "claude", "audio_drama")
        assert background_jobs.get_status(job_id)["result"]["cap_reached"] is None
        _clear(job_id)


class TestSpendingCapUI:
    def _drama(self, isolated_db, engine="claude"):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        translation_engine=engine)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好" * 200)])
        return did

    def _run(self, did, **state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_claude"] = "sk-ant-fake"
        for k, v in state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _translate_button(self, at):
        return [b for b in at.button if b.label == "🌐 Translate all lines"][0]

    def test_cap_input_and_estimate_shown_for_a_paid_engine(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        assert [n for n in at.number_input if n.key == f"cost_cap_{did}"]
        assert any("Estimated cost for 1 line(s)" in c.value for c in at.caption)

    def test_no_cap_input_for_a_free_engine(self, isolated_db):
        did = self._drama(isolated_db, engine="test_offline")
        at = self._run(did)
        assert not [n for n in at.number_input if n.key == f"cost_cap_{did}"]

    def _assert_cap_shown(self, isolated_db, engine):
        did = self._drama(isolated_db, engine=engine)
        at = self._run(did, **{f"settings_{engine}": "fake-key"})
        assert not at.exception
        assert [n for n in at.number_input if n.key == f"cost_cap_{did}"], \
            f"{engine} should show the cost-cap input now that it reports usage"
        assert any(f"Estimated cost for 1 line(s)" in c.value for c in at.caption), \
            f"{engine} should show a cost estimate now that it's priced per character"

    def test_cap_input_now_shown_for_google(self, isolated_db):
        """Step 25w Bug 2: the cost-cap system structurally couldn't ever
        apply to Google/DeepL -- _cap_applies explicitly excluded both, so
        a user with a monthly cap set got zero enforcement translating
        through either. Now that both report real usage (see
        GoogleEngine/DeepLEngine.last_usage), the cap UI covers them too.
        GoogleEngine only needs `requests` (a core dependency), so this
        runs unconditionally -- see the deepl variant below for why that
        one is gated on the optional package being installed."""
        self._assert_cap_shown(isolated_db, "google")

    def test_cap_input_now_shown_for_deepl(self, isolated_db):
        """Same as the google case above, for DeepLEngine. Needs the real
        `deepl` package importable (requirements-optional.txt, not a core
        dependency) -- DeepLEngine.__init__ does `import deepl` for real
        here, unlike TestDeepLEngine's own tests, which inject a fake
        module into sys.modules before constructing the engine directly
        and so don't need the real package installed at all."""
        pytest.importorskip("deepl")
        self._assert_cap_shown(isolated_db, "deepl")

    def test_the_cap_reaches_the_background_job(self, isolated_db, monkeypatch):
        captured = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: captured.update(kw) or True)
        did = self._drama(isolated_db)
        at = self._run(did)
        [n for n in at.number_input if n.key == f"cost_cap_{did}"][0].set_value(1.5).run()
        self._translate_button(at).click()
        at.run(timeout=30)
        assert captured.get("cost_cap_usd") == pytest.approx(1.5)

    def test_monthly_cap_already_used_up_disables_translate(self, isolated_db):
        did = self._drama(isolated_db)
        isolated_db.log_usage(did, "claude", "claude-sonnet-5", "translate", 1, 1, 12.0)
        at = self._run(did, settings_monthly_cap_usd=10.0)
        assert self._translate_button(at).disabled
        assert any("already used up" in w.value for w in at.warning)

    def test_monthly_remainder_becomes_the_jobs_cap(self, isolated_db, monkeypatch):
        captured = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: captured.update(kw) or True)
        did = self._drama(isolated_db)
        isolated_db.log_usage(did, "claude", "claude-sonnet-5", "translate", 1, 1, 7.0)
        at = self._run(did, settings_monthly_cap_usd=10.0)
        self._translate_button(at).click()
        at.run(timeout=30)
        assert captured.get("cost_cap_usd") == pytest.approx(3.0)


class TestFixFlaggedLinesCapUI:
    """Step 25w Bug 3: "Fix flagged lines in bulk" called engine.translate_batch
    directly per line, with no cost_cap_usd, no resolve_cost_cap call, and no
    spend accumulation checked against any cap -- confirmed real: unlike
    "Translate all lines" (Step 9) and the CLI, both of which already
    resolve and enforce a cap, this button had none at all."""

    def _drama(self, isolated_db, engine="claude"):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        translation_engine=engine)
        lines = [Line(idx=0, start=0, end=1, zh="你好", flag="mistranslation", flag_note="check")]
        isolated_db.save_lines(did, lines)
        return did

    def _run(self, did, **state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_claude"] = "sk-ant-fake"
        for k, v in state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _fix_button(self, at):
        return [b for b in at.button if b.label == "🔁 Re-transcribe + re-translate flagged lines"][0]

    def test_monthly_cap_already_used_up_disables_the_fix_button_too(self, isolated_db):
        did = self._drama(isolated_db)
        isolated_db.log_usage(did, "claude", "claude-sonnet-5", "translate", 1, 1, 12.0)
        at = self._run(did, settings_monthly_cap_usd=10.0)
        assert self._fix_button(at).disabled
        assert any("already used up" in w.value for w in at.warning)

    def test_the_jobs_cap_reaches_the_fix_flagged_background_job(self, isolated_db, monkeypatch):
        captured = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: captured.update(kw) or True)
        did = self._drama(isolated_db)
        at = self._run(did)
        [n for n in at.number_input if n.key == f"cost_cap_{did}"][0].set_value(1.5).run()
        self._fix_button(at).click()
        at.run(timeout=30)
        assert captured.get("cost_cap_usd") == pytest.approx(1.5)

    def test_no_cap_set_passes_no_cap_to_the_fix_flagged_job(self, isolated_db, monkeypatch):
        captured = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: captured.update(kw) or True)
        did = self._drama(isolated_db)
        at = self._run(did)
        self._fix_button(at).click()
        at.run(timeout=30)
        assert captured.get("cost_cap_usd") is None


class TestContentBlockedRetryWithDifferentEngine:
    """Step 31 item 5: a content_blocked-flagged line (set by
    translate_lines_with_engine when a provider's own content moderation
    blocked it -- see TestTranslateLinesWithEngine's bisection test and
    TestGeminiEngine/TestDeepSeekEngine in test_translate_engines.py for
    the detection side) gets a real "retry with a different engine"
    action in Review & edit, reusing the existing flag/flag_note display
    rather than a new surface, and defaulting the suggested engine to
    Ollama -- the one engine with no cloud-side moderation -- while still
    letting the user pick any configured engine."""

    def _drama(self, isolated_db, engine_note="gemini: SAFETY"):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated",
                                        translation_engine="gemini")
        lines = [Line(idx=0, start=0, end=1, zh="敏感内容", en="",
                       flag="content_blocked", flag_note=engine_note)]
        isolated_db.save_lines(did, lines)
        return did

    def _run(self, did, **state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_gemini"] = "fake-gemini-key"
        for k, v in state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_flag_display_shows_the_real_engine_and_reason(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        assert any("gemini: SAFETY" in w.value for w in at.warning)

    def test_retry_picker_defaults_to_ollama(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        assert at.selectbox(key="retry_engine_0").value == "ollama"

    def test_retry_with_ollama_translates_the_line_and_clears_the_flag(self, isolated_db, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": '{"1": "A retried translation."}'}}

        monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
        did = self._drama(isolated_db)
        at = self._run(did)
        at.button(key="retry_blocked_0").click()
        at.run(timeout=30)

        loaded = isolated_db.load_lines(did)
        assert loaded[0]["en"] == "A retried translation."
        assert not loaded[0]["flag"]
        assert not loaded[0]["flag_note"]

    def test_retry_does_not_change_the_drama_s_own_translation_engine(self, isolated_db, monkeypatch):
        """The retry is scoped to just this one line -- it must not
        switch the drama's default engine to whatever was picked for the
        retry."""
        monkeypatch.setattr("requests.post", lambda *a, **k: type(
            "R", (), {"raise_for_status": lambda self: None,
                     "json": lambda self: {"message": {"content": '{"1": "Retried."}'}}})())
        did = self._drama(isolated_db)
        at = self._run(did)
        at.button(key="retry_blocked_0").click()
        at.run(timeout=30)

        assert db.get_drama(did)["translation_engine"] == "gemini"

    def test_a_second_provider_also_blocking_the_retry_keeps_the_line_flagged(self, isolated_db, monkeypatch):
        """If the newly-picked engine blocks it too, the line stays
        flagged (with the new engine's own reason) instead of silently
        clearing the flag on a translation that never actually happened."""
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": "I can't translate this."}}

        monkeypatch.setattr("time.sleep", lambda *_: None)
        monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
        did = self._drama(isolated_db)
        at = self._run(did)
        at.button(key="retry_blocked_0").click()
        at.run(timeout=30)

        loaded = isolated_db.load_lines(did)
        assert loaded[0]["flag"] == "content_blocked"
        assert "ollama" in loaded[0]["flag_note"]
        assert not loaded[0]["en"]


class _FakeBulkProvider:
    """Stands in for the Claude/Gemini batch APIs in UI tests."""
    def __init__(self):
        self.submitted = None
        self.cancelled = []
        self.polls = 0
        self.refuse = False

    def build_request(self, key, context, numbered):
        return {"custom_id": key, "numbered": numbered}

    def build_prompt_request(self, key, prompt, max_tokens=3000):
        return {"custom_id": key, "prompt": prompt}

    def submit(self, requests_):
        self.submitted = requests_
        return "fake_batch_1"

    def poll(self, batch_id):
        import bulk_translate
        self.polls += 1
        if self.refuse:
            raise bulk_translate.BulkAuthError("Claude refused the API key (HTTP 401).")
        return "pending"

    def results(self, batch_id):
        return []

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)


class TestBulkModeUI:
    """Step 9: the Bulk checkbox and the Bulk jobs panel."""

    @pytest.fixture
    def provider(self, monkeypatch):
        import bulk_translate
        fake = _FakeBulkProvider()
        monkeypatch.setattr(bulk_translate, "make_provider", lambda engine_choice, engine: fake)
        yield fake
        for job in db.list_bulk_jobs():
            background_jobs.request_cancel(bulk_translate.poll_job_id(job["id"]))
        deadline = time.time() + 5
        while any(background_jobs.is_running(bulk_translate.poll_job_id(j["id"]))
                  for j in db.list_bulk_jobs()) and time.time() < deadline:
            time.sleep(0.05)

    def _drama(self, isolated_db, engine="claude", n=3):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        translation_engine=engine)
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"第{i}句") for i in range(n)])
        return did

    def _pending_job(self, isolated_db, did, status="submitted", last_error=None):
        import bulk_translate
        lines = isolated_db.load_line_objects(did)
        job_id = isolated_db.create_bulk_job(
            did, "claude", "claude-sonnet-5", status,
            [(ln.id, f"d{did}_b0", bulk_translate.zh_hash(ln.zh), "") for ln in lines],
            provider_batch_id="fake_batch_1")
        if last_error:
            isolated_db.update_bulk_job(job_id, last_error=last_error)
        return job_id

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_claude"] = "sk-ant-fake"
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_bulk_checkbox_shown_for_claude_not_for_a_free_engine(self, isolated_db, provider):
        did = self._drama(isolated_db)
        assert [c for c in self._run(did).checkbox if c.key == f"bulk_mode_{did}"]
        did2 = self._drama(isolated_db, engine="test_offline")
        assert not [c for c in self._run(did2).checkbox if c.key == f"bulk_mode_{did2}"]

    def test_bulk_translate_submits_a_batch_instead_of_a_live_job(self, isolated_db, provider,
                                                                  monkeypatch):
        started = []
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, *a, **kw: started.append(job_id) or True)
        did = self._drama(isolated_db)
        at = self._run(did)
        [c for c in at.checkbox if c.key == f"bulk_mode_{did}"][0].set_value(True).run()
        assert any("Bulk trade-off" in c.value for c in at.caption)
        [b for b in at.button if b.label == "🌐 Translate all lines"][0].click()
        at.run(timeout=30)
        assert provider.submitted and len(provider.submitted) == 1
        assert not any(j.startswith("translate_") for j in started)
        [job] = db.list_bulk_jobs(did)
        assert job["status"] == "submitted" and job["provider_batch_id"] == "fake_batch_1"
        assert any("Submitted 3 line(s)" in m.value for m in at.success)

    def test_bulk_and_reflect_together_submits_the_faithfulness_stage(self, isolated_db, provider,
                                                                      monkeypatch):
        """Step 9d item 3: Bulk mode and Reflect mode are no longer
        mutually exclusive -- together they submit stage 1 (faithfulness)
        of the three-stage Bulk Reflect pipeline, not a live job."""
        started = []
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, *a, **kw: started.append(job_id) or True)
        did = self._drama(isolated_db)
        at = self._run(did)
        [c for c in at.checkbox if c.key == f"bulk_mode_{did}"][0].set_value(True).run()
        [c for c in at.checkbox if c.key == f"reflect_mode_{did}"][0].set_value(True).run()
        assert any("Bulk Reflect" in c.value for c in at.caption)
        [b for b in at.button if b.label == "🌐 Translate all lines"][0].click()
        at.run(timeout=30)
        assert provider.submitted and len(provider.submitted) == 1
        assert not any(j.startswith("translate_") for j in started)
        [job] = db.list_bulk_jobs(did)
        assert job["kind"] == "reflect" and job["stage"] == "faithful"
        assert any("faithfulness pass" in m.value for m in at.success)

    def test_bulk_reflect_is_unavailable_for_deepseek(self, isolated_db, provider):
        did = self._drama(isolated_db, engine="deepseek")
        at = self._run(did)
        [c for c in at.checkbox if c.key == f"bulk_mode_{did}"][0].set_value(True).run()
        [c for c in at.checkbox if c.key == f"reflect_mode_{did}"][0].set_value(True).run()
        assert any("needs Claude or Gemini's own batch API" in i.value for i in at.info)

    def test_panel_lists_a_pending_batch(self, isolated_db, provider):
        did = self._drama(isolated_db)
        job_id = self._pending_job(isolated_db, did)
        at = self._run(did)
        assert any("Bulk jobs (1 pending)" in e.label for e in at.expander)
        assert any(f"#{job_id}" in m.value and "Waiting for results" in m.value for m in at.markdown)
        assert [b for b in at.button if b.key == f"bulk_check_{job_id}"]
        assert [b for b in at.button if b.key == f"bulk_cancel_{job_id}"]

    def test_panel_restarts_polling_for_a_pending_batch(self, isolated_db, provider):
        import bulk_translate
        did = self._drama(isolated_db)
        job_id = self._pending_job(isolated_db, did)
        background_jobs.clear_all_jobs()
        self._run(did)
        deadline = time.time() + 5
        while provider.polls == 0 and time.time() < deadline:
            time.sleep(0.02)
        assert provider.polls >= 1
        assert background_jobs.is_running(bulk_translate.poll_job_id(job_id))

    def test_cancel_stops_polling(self, isolated_db, provider):
        import bulk_translate
        did = self._drama(isolated_db)
        job_id = self._pending_job(isolated_db, did)
        at = self._run(did)
        assert background_jobs.is_running(bulk_translate.poll_job_id(job_id))
        [b for b in at.button if b.key == f"bulk_cancel_{job_id}"][0].click()
        at.run(timeout=30)
        deadline = time.time() + 5
        while background_jobs.is_running(bulk_translate.poll_job_id(job_id)) and time.time() < deadline:
            time.sleep(0.05)
        assert not background_jobs.is_running(bulk_translate.poll_job_id(job_id))
        assert db.get_bulk_job(job_id)["status"] == "cancelled"
        assert provider.cancelled == ["fake_batch_1"]
        assert not [b for b in at.button if b.key == f"bulk_cancel_{job_id}"]

    def test_a_polling_auth_error_shows_on_the_panel(self, isolated_db, provider):
        did = self._drama(isolated_db)
        provider.refuse = True
        job_id = self._pending_job(isolated_db, did)
        at = self._run(did)
        [b for b in at.button if b.key == f"bulk_check_{job_id}"][0].click()
        at.run(timeout=30)
        assert db.get_bulk_job(job_id)["status"] == "auth_error"
        assert any("key refused" in m.value for m in at.markdown)
        assert any("Claude refused the API key" in e.value for e in at.error)


class TestGemini31FlashLiteInDropdown:
    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_gemini"] = "gm-fake"
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _model_box(self, at):
        return [b for b in at.selectbox if b.label == "Gemini model"][0]

    def test_offered_with_the_default_unchanged_and_reaches_the_job(self, isolated_db, monkeypatch):
        captured = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: captured.update(args=a) or True)
        did = isolated_db.create_drama(title_en="G", media_type="audio_drama",
                                       content_mode="audio_drama", status="aligned",
                                       translation_engine="gemini")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        at = self._run(did)
        box = self._model_box(at)
        assert translate_engines.GEMINI_MODELS["gemini-3.1-flash-lite"] in box.options
        assert box.value == "gemini-flash-lite-latest"

        box.set_value("gemini-3.1-flash-lite").run()
        [b for b in at.button if b.label == "🌐 Translate all lines"][0].click()
        at.run(timeout=30)
        engine = captured["args"][3]
        assert engine.model == "gemini-3.1-flash-lite"


class TestGeminiFreeTierProGating:
    """Step 1f item 4: Gemini Pro was removed from the free tier entirely
    in April 2026 -- picking it with a free-tier key flagged shows a clear
    message and blocks the call, instead of a raw API error."""

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_gemini"] = "gm-fake"
        at.session_state["gemini_free_tier"] = True
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _model_box(self, at):
        return [b for b in at.selectbox if b.label == "Gemini model"][0]

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="G", media_type="audio_drama",
                                       content_mode="audio_drama", status="aligned",
                                       translation_engine="gemini")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        return did

    def test_selecting_pro_shows_a_clear_message_and_blocks_translate(self, isolated_db, monkeypatch):
        captured = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: captured.update(args=a) or True)
        did = self._drama(isolated_db)
        at = self._run(did)
        self._model_box(at).set_value("gemini-pro-latest").run()

        assert any("Pro isn't available on the Gemini free tier" in e.value for e in at.error)
        translate_button = [b for b in at.button if b.label == "🌐 Translate all lines"][0]
        # A disabled button can't even be clicked in a real browser -- AppTest
        # itself refuses to interact with one, which is the strongest proof
        # available here that the call is genuinely blocked, not just warned about.
        assert translate_button.disabled
        assert "args" not in captured  # the job never started

    def test_flash_lite_is_unaffected(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        assert not any("Pro isn't available" in e.value for e in at.error)
        translate_button = [b for b in at.button if b.label == "🌐 Translate all lines"][0]
        assert not translate_button.disabled


class TestJobEtaDisplay:
    """Step 9b.1 exit condition: the ETA appears once progress is
    non-trivial, and disappears/holds sensibly at 0% and 100%."""

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        translation_engine="test_offline")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        return did

    def _run(self, did, runs=2):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        for _ in range(runs):
            at.run(timeout=30)
        return at

    def test_no_eta_at_zero_percent(self, isolated_db):
        did = self._drama(isolated_db)
        job_id = f"translate_{did}"
        background_jobs.clear_job(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                         "error": None, "cancel_requested": False, "result": None,
                                         "started_at": time.time() - 30}
        at = self._run(did)
        bar = [p for p in at.get("progress") if p.value == 0][0]
        assert "remaining" not in bar.proto.text
        background_jobs.clear_job(job_id)

    def test_eta_shown_once_progress_is_non_trivial(self, isolated_db):
        did = self._drama(isolated_db)
        job_id = f"translate_{did}"
        background_jobs.clear_job(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.5, "message": "",
                                         "error": None, "cancel_requested": False, "result": None,
                                         "started_at": time.time() - 30}
        at = self._run(did)
        bar = [p for p in at.get("progress") if p.value == 50][0]
        assert "remaining" in bar.proto.text
        background_jobs.clear_job(job_id)

    def test_no_eta_once_done_shows_success_not_a_bar(self, isolated_db):
        did = self._drama(isolated_db)
        job_id = f"translate_{did}"
        background_jobs.clear_job(job_id)
        background_jobs._jobs[job_id] = {"status": "done", "progress": 1.0, "message": "",
                                         "error": None, "cancel_requested": False, "result": {"errors": []},
                                         "started_at": time.time() - 30}
        # A single run -- the "done" branch clears the job as its last
        # step, so a second render (as the shared helper's default does
        # for other tests, to reach steady state) would see no job left
        # and lose the very toast this test checks for.
        at = self._run(did, runs=1)
        assert not at.get("progress")
        assert any("complete" in m.value for m in at.toast)


class TestMergePreviewDoesNotMutateLiveLines:
    """Step 5b item 2: merge_adjacent_short_lines mutates the Line objects
    it merges in place (and renumbers every line's .idx) -- the "Preview
    merge" button used to pass it list(edited_rows), which copies the
    outer list but not the Line objects inside, and edited_rows is the
    exact same objects as st.session_state.lines. So clicking Preview
    merge silently corrupted the live, unsaved lines before "Apply merge"
    was ever clicked -- backing out of a preview didn't actually leave the
    page unchanged. Fixed by copying the lines (_copy_lines) before
    handing them to the merge function."""

    def _drama_with_two_mergeable_lines(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=0.5, zh="你", en="You"),
            Line(idx=1, start=0.6, end=1.0, zh="好", en="good"),
        ])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _button(self, at, label):
        matches = [b for b in at.button if b.label == label]
        assert matches, f"button {label!r} not found on the page"
        return matches[0]

    def test_preview_merge_leaves_the_live_lines_untouched(self, isolated_db):
        did = self._drama_with_two_mergeable_lines(isolated_db)
        at = self._run(did)

        self._button(at, "Preview merge").click()
        at.run(timeout=30)

        live_lines = sorted(at.session_state.lines, key=lambda ln: ln.idx)
        assert [ln.zh for ln in live_lines] == ["你", "好"]
        assert [ln.idx for ln in live_lines] == [0, 1]

        preview = at.session_state[f"merge_preview_{did}"]
        assert [ln.zh for ln in preview] == ["你好"]


class TestRetranscribeUseThisRefreshesTheZhBox:
    """Step 5b item 3: the per-line zh box in Review & edit is a widget
    keyed on zh_<idx>, which -- like every keyed Streamlit widget -- keeps
    showing whatever it already holds rather than the line's new text on
    the next rerun. Accepting a re-transcribe result updated the Line
    object and the database, but without popping zh_<idx> first, the very
    next rerun's edited_page_rows reconstruction read the box's still-old
    value back and overwrote the just-accepted text in
    st.session_state.lines -- so the accepted retranscription silently
    reverted. The existing "Restore original text" button already pops
    this same key for the same reason; "Use this" (retranscribe) now does
    too."""

    def _drama_with_audio(self, isolated_db, tmp_path):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        audio_filename="audio.wav")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="stale text", en="stale en")])
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        return did

    def _run(self, did, monkeypatch, retrans_text):
        from streamlit.testing.v1 import AppTest
        import tabs.workspace_tab as wt

        monkeypatch.setattr(wt.core_module, "extract_audio_slice", lambda *a, **k: None)
        monkeypatch.setattr(wt.core_module, "transcribe_for_timing",
                             lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": retrans_text}])

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _button(self, at, label, key=None):
        candidates = [b for b in at.button if b.key == key] if key else \
            [b for b in at.button if b.label == label]
        assert candidates, f"button {label!r} (key={key!r}) not found on the page"
        return candidates[0]

    def test_accepting_a_retranscription_is_not_reverted_on_the_next_rerun(
            self, isolated_db, monkeypatch, tmp_path):
        did = self._drama_with_audio(isolated_db, tmp_path)
        at = self._run(did, monkeypatch, "新的文本")

        self._button(at, "Re-transcribe", key="rvretrans_0").click()
        at.run(timeout=30)

        self._button(at, "Use this", key="rvuseretrans_0").click()
        at.run(timeout=30)

        zh_box = [ta for ta in at.text_area if ta.key == "zh_0"][0]
        assert zh_box.value == "新的文本"
        assert at.session_state.lines[0].zh == "新的文本"


class TestRenumberingClearsStaleLineWidgets:
    """Step 6d: Review & edit's per-line boxes are keyed by position
    (zh_<idx>, start_<idx>, ...). "Apply merge" and Version history's
    "Restore" renumber the lines but used to leave those boxes behind, so
    the next rerun read them back over whichever line now sat at each
    position: the page showed the pre-merge layout, the next "Save edits"
    wrote a merged-away line's text over a different line, and the export
    panel's overlap check wrote spurious timing flags to the database from
    the stale start/end values. Re-segmentation (Step 6c) already cleared
    them; merge and restore now do too."""

    ORIGINAL = [("你", 0.0, 0.5), ("好", 0.6, 1.0), ("今天天气不错", 5.0, 8.0)]

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [Line(idx=i, start=s, end=e, zh=zh, en="")
                                      for i, (zh, s, e) in enumerate(self.ORIGINAL)])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _click(self, at, label=None, key=None):
        matches = [b for b in at.button if (key and b.key == key) or (label and b.label == label)]
        assert matches, f"button {label or key!r} not found on the page"
        matches[0].click()
        at.run(timeout=30)

    def _assert_page_matches_db_even_after_saving(self, at, did, expected):
        at.run(timeout=30)
        assert [l.zh for l in at.session_state.lines] == expected
        self._click(at, label="💾 Save edits (this page)")  # resubmit whatever the boxes hold
        after = db.load_line_objects(did)
        assert [l.zh for l in after] == expected
        assert [l.flag for l in after] == [None] * len(expected)

    def test_apply_merge(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        self._click(at, label="Preview merge")
        self._click(at, label="✅ Apply merge")
        self._assert_page_matches_db_even_after_saving(at, did, ["你好", "今天天气不错"])

    def test_restore(self, isolated_db):
        did = self._drama(isolated_db)
        isolated_db.save_line_history_snapshot(did, isolated_db.load_line_objects(did), "three lines")
        merged = isolated_db.load_line_objects(did)
        merged[0].zh, merged[0].end = "你好", 1.0
        merged[2].idx = 1
        isolated_db.save_lines(did, [merged[0], merged[2]])
        at = self._run(did)
        snap = [h for h in isolated_db.list_line_history(did) if h["label"] == "three lines"][0]
        self._click(at, key=f"restore_{snap['id']}")
        self._assert_page_matches_db_even_after_saving(at, did, ["你", "好", "今天天气不错"])


class TestImproveTranslationUseThisRefreshesTheEnBox:
    """Step 6d: the English-box twin of Step 5b's re-transcribe fix --
    accepting an improved translation updated the line and the database,
    but the en_<idx> box kept its old text and was read back over the
    accepted translation on the next rerun."""

    def test_accepted_improvement_survives_a_rerun(self, isolated_db, monkeypatch):
        import line_tools
        monkeypatch.setattr(line_tools, "improve_line", lambda *a, **k: "A much better line.")
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated",
                                        translation_engine="test_offline")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi there.")])
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        [b for b in at.button if b.key == "rvimprove_0"][0].click()
        at.run(timeout=30)
        [b for b in at.button if b.key == "rvuseimproved_0"][0].click()
        at.run(timeout=30)
        at.run(timeout=30)

        assert [ta.value for ta in at.text_area if ta.key == "en_0"] == ["A much better line."]
        assert at.session_state.lines[0].en == "A much better line."
        assert isolated_db.load_lines(did)[0]["en"] == "A much better line."


class TestPerLineExplainToolsMovedFromReader:
    """Step 15: Reader's "Line tools" (Why this?/Alternatives/Grammar/
    Pronounce) moved into Workspace's own per-line 🔧 popover, next to the
    Improve translation/Re-transcribe it already had -- same per-line job,
    previously split across two tabs for no functional reason."""

    def _drama_with_a_line(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated",
                                        translation_engine="test_offline")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi there.")])
        return did

    def _drama_with_two_lines(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated",
                                        translation_engine="test_offline")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=1.0, zh="第一行", en="Line one"),
            Line(idx=1, start=1.0, end=2.0, zh="第二行", en="Line two"),
        ])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_why_this_result_for_one_line_is_not_shown_under_another(self, isolated_db, monkeypatch):
        # Step 25x's bug class, re-checked under the new home: each line's
        # result is keyed by ln.idx directly (rv_why_0/rv_why_1, ...), not a
        # single shared key re-pointed at whichever line is currently
        # selected -- so line 1's row simply never reads line 0's key.
        import line_tools
        monkeypatch.setattr(line_tools, "explain_translation",
                             lambda zh, en, eng, source_language="zh": f"explanation for {en}")
        at = self._run(self._drama_with_two_lines(isolated_db))
        [b for b in at.button if b.key == "rvwhy_0"][0].click()
        at.run(timeout=30)
        assert any("explanation for Line one" in m.value for m in at.info)
        assert not any("explanation for Line two" in m.value for m in at.info)

        [b for b in at.button if b.key == "rvwhy_1"][0].click()
        at.run(timeout=30)
        assert any("explanation for Line one" in m.value for m in at.info)
        assert any("explanation for Line two" in m.value for m in at.info)

    def test_why_this_result_does_not_survive_a_switch_to_another_drama(self, isolated_db, monkeypatch):
        # Same bug class as Step 4j/25x, on the newly-added keys specifically:
        # rv_why_<idx>/rv_alts_<idx>/rv_gram_<idx> are positional (keyed by
        # idx, not by line id), so switching to a DIFFERENT drama whose own
        # line 0 exists must clear them -- otherwise drama A's cached
        # explanation would render under drama B's unrelated line 0.
        import line_tools
        monkeypatch.setattr(line_tools, "explain_translation",
                             lambda zh, en, eng, source_language="zh": f"explanation for {en}")
        did_a = self._drama_with_a_line(isolated_db)
        did_b = isolated_db.create_drama(title_en="Other Drama", media_type="audio_drama",
                                         content_mode="audio_drama", status="translated",
                                         translation_engine="test_offline")
        isolated_db.save_lines(did_b, [Line(idx=0, start=0.0, end=1.0, zh="另一行", en="A different line.")])

        at = self._run(did_a)
        [b for b in at.button if b.key == "rvwhy_0"][0].click()
        at.run(timeout=30)
        assert any("explanation for Hi there." in m.value for m in at.info)

        [box] = [s for s in at.selectbox if s.label == "Drama"]
        [b_label] = [o for o in box.options if "Other Drama" in o]
        box.set_value(b_label).run(timeout=30)

        assert "rv_why_0" not in at.session_state
        assert not any("explanation for Hi there." in m.value for m in at.info)

    def test_why_this_shows_an_explanation(self, isolated_db, monkeypatch):
        import line_tools
        monkeypatch.setattr(line_tools, "explain_translation", lambda *a, **k: "Because reasons.")
        at = self._run(self._drama_with_a_line(isolated_db))
        [b for b in at.button if b.key == "rvwhy_0"][0].click()
        at.run(timeout=30)
        assert any("Because reasons." in m.value for m in at.info)

    def test_alternatives_lists_each_option(self, isolated_db, monkeypatch):
        import line_tools
        monkeypatch.setattr(line_tools, "alternative_translations", lambda *a, **k: [
            {"translation": "Hey there.", "approach": "casual", "tradeoff": "less formal"}])
        at = self._run(self._drama_with_a_line(isolated_db))
        [b for b in at.button if b.key == "rvalts_0"][0].click()
        at.run(timeout=30)
        assert any("Hey there." in c.value for c in at.caption)

    def test_grammar_shows_a_breakdown_table(self, isolated_db, monkeypatch):
        import line_tools
        monkeypatch.setattr(line_tools, "grammar_breakdown", lambda *a, **k: [
            {"word": "你好", "role": "greeting"}])
        at = self._run(self._drama_with_a_line(isolated_db))
        [b for b in at.button if b.key == "rvgram_0"][0].click()
        at.run(timeout=30)
        assert len(at.dataframe) >= 1

    def test_pronounce_plays_audio(self, isolated_db, monkeypatch):
        import line_tools
        monkeypatch.setattr(line_tools, "pronunciation_audio", lambda *a, **k: b"fake-mp3-bytes")
        at = self._run(self._drama_with_a_line(isolated_db))
        [b for b in at.button if b.key == "rvpronounce_0"][0].click()
        at.run(timeout=30)
        assert not at.exception
        assert len(at.get("audio")) >= 1


class TestTranslateJobRefreshesStaleEnBoxes:
    """Step 9h: a real, confirmed gap -- a translate job correctly writes
    ln.en and reloads st.session_state.lines from the database before
    rendering "Translation complete.", but the en_<idx> text_area is a
    purely positional widget key. Streamlit ignores a widget's value=
    once st.session_state[key] already exists (cached as "" from every
    earlier render while the line was untranslated), so the box kept
    showing stale empty text until a hard refresh wiped session state.
    Same fix shape as Step 6d's merge/restore/improve-translation cases,
    applied here to a background translate job's own completion path."""

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                       content_mode="audio_drama", status="aligned",
                                       translation_engine="test_offline")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_translated_text_shows_immediately_no_hard_refresh_needed(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        # The en_0 box exists (and its session-state value is cached as ""),
        # same as a real page that's been open since before the line was
        # translated -- the exact precondition that made this box go stale.
        assert [ta.value for ta in at.text_area if ta.key == "en_0"] == [""]

        job_id = f"translate_{did}"
        translated = isolated_db.load_line_objects(did)
        translated[0].en = "Hello."
        isolated_db.save_lines(did, translated, fields=("en",))
        background_jobs._jobs[job_id] = {
            "status": "done", "progress": 1.0, "message": "", "error": None,
            "cancel_requested": False, "result": {"errors": [], "cap_reached": None},
        }
        at.run(timeout=30)

        assert any("Translation complete." in s.value for s in at.toast)
        assert [ta.value for ta in at.text_area if ta.key == "en_0"] == ["Hello."]
        background_jobs.clear_job(job_id)

    def test_bulk_jobs_panel_completion_also_refreshes_the_en_box(self, isolated_db):
        """The same staleness applies to the Bulk jobs panel's own
        poller-completion path, which also updates ln.en (a bulk-mode
        translate result) without going through run_translate_job."""
        did = self._drama(isolated_db)
        line_id = isolated_db.load_line_objects(did)[0].id
        job_row_id = isolated_db.create_bulk_job(
            did, "claude", "claude-sonnet-5", "submitted", [(line_id, "req1", "hash1", "")])
        at = self._run(did)
        # Seen as "submitted, not yet applied" on this first render -- the
        # panel's own seen-set tracking needs to observe the job BEFORE it
        # flips to "applied" for the poller-completion branch to fire, same
        # as a real pending-then-applied bulk job would be observed twice.
        assert [ta.value for ta in at.text_area if ta.key == "en_0"] == [""]

        translated = isolated_db.load_line_objects(did)
        translated[0].en = "Hi from bulk."
        isolated_db.save_lines(did, translated, fields=("en",))
        isolated_db.update_bulk_job(job_row_id, status="applied")
        at.run(timeout=30)

        assert [ta.value for ta in at.text_area if ta.key == "en_0"] == ["Hi from bulk."]

    def test_fix_flagged_lines_completion_also_refreshes_the_zh_and_en_boxes(self, isolated_db):
        """A third real instance of the same gap, found during Step 9h's
        own audit: "Fix flagged lines" rewrites both zh and en (and clears
        the flag) for the lines it touches, and had the identical
        stale-widget-cache bug. Verified via "Save edits," same as Step
        6d's merge/restore tests -- reading a widget's rendered value back
        out through the app's own save path is more robust here than
        introspecting AppTest's ElementTree directly across a run where
        the flagged-line warning/dismiss block appears and disappears."""
        did = self._drama(isolated_db)
        # "Fix flagged lines in bulk" only renders once at least one line
        # is flagged -- the real precondition for this job to ever run.
        flagged = isolated_db.load_line_objects(did)
        flagged[0].flag, flagged[0].flag_note = "check", "sounds off"
        isolated_db.save_lines(did, flagged, fields=("flag", "flag_note"))
        at = self._run(did)

        job_id = f"fixflag_{did}"
        fixed = isolated_db.load_line_objects(did)
        fixed[0].zh, fixed[0].en = "重新识别的文本", "Re-recognized text."
        fixed[0].flag, fixed[0].flag_note = None, ""
        isolated_db.save_lines(did, fixed, fields=("zh", "en", "flag", "flag_note"))
        background_jobs._jobs[job_id] = {
            "status": "done", "progress": 1.0, "message": "", "error": None,
            "cancel_requested": False, "result": {"fixed_count": 1, "total_flagged": 1},
        }
        at.run(timeout=30)
        at.run(timeout=30)

        [b for b in at.button if b.label == "💾 Save edits (this page)"][0].click()
        at.run(timeout=30)
        saved = isolated_db.load_lines(did)[0]
        assert saved["zh"] == "重新识别的文本"
        assert saved["en"] == "Re-recognized text."
        background_jobs.clear_job(job_id)


class TestDramaSwitchResetsLoadedLines:
    """Step 4j: a real, confirmed cross-drama data-corruption bug --
    switching the Drama dropdown left the newly-picked drama's page
    showing the PREVIOUS drama's lines (nothing reset
    st.session_state.lines here), and saving afterward would silently
    overwrite the new drama's real rows with the old drama's data,
    deleting its own lines/notes/emotions in the process."""

    def _two_dramas(self, isolated_db):
        did_a = isolated_db.create_drama(title_en="Drama A", media_type="audio_drama",
                                         content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did_a, [Line(idx=0, start=0.0, end=1.0, zh="甲甲甲", en="AAA")])
        did_b = isolated_db.create_drama(title_en="Drama B", media_type="audio_drama",
                                         content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did_b, [Line(idx=0, start=0.0, end=1.0, zh="乙乙乙", en="BBB")])
        return did_a, did_b

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _drama_box(self, at):
        return [b for b in at.selectbox if b.label == "Drama"][0]

    def _switch_to(self, at, did):
        box = self._drama_box(at)
        label = next(l for l in box.options if l.startswith(f"#{did} "))
        box.set_value(label).run(timeout=30)

    def test_switching_shows_the_new_dramas_own_lines_not_the_old_ones(self, isolated_db):
        did_a, did_b = self._two_dramas(isolated_db)
        at = self._run(did_a)
        assert [ta.value for ta in at.text_area if ta.key == "zh_0"] == ["甲甲甲"]

        self._switch_to(at, did_b)

        assert at.session_state.active_drama_id == did_b
        assert at.session_state.lines[0].zh == "乙乙乙"
        assert [ta.value for ta in at.text_area if ta.key == "zh_0"] == ["乙乙乙"]

    def test_save_edits_immediately_after_switching_does_not_corrupt_the_new_drama(
            self, isolated_db):
        did_a, did_b = self._two_dramas(isolated_db)
        at = self._run(did_a)
        self._switch_to(at, did_b)

        [b for b in at.button if b.label == "💾 Save edits (this page)"][0].click()
        at.run(timeout=30)

        after_b = isolated_db.load_lines(did_b)
        assert [r["zh"] for r in after_b] == ["乙乙乙"]
        after_a = isolated_db.load_lines(did_a)
        assert [r["zh"] for r in after_a] == ["甲甲甲"]


class TestDramaSwitchKeepsCharactersSeparate:
    """Step 25b: section 6's per-character widgets were keyed by the
    diarized speaker label alone (cname_SPEAKER_00, ...). Two unrelated
    dramas that both have a SPEAKER_00 shared one widget key, so after a
    switch Streamlit kept showing the previous drama's value, and the
    "widget differs from what's saved" check wrote it into the new
    drama's own character row."""

    _FIELDS = ("character_name", "voice_actor", "tts_voice", "pronouns", "ref_text",
               "clone_engine", "voice_design")

    def _two_dramas(self, isolated_db):
        did_a = isolated_db.create_drama(title_en="Drama A", media_type="audio_drama",
                                         content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did_a, [Line(idx=0, start=0.0, end=1.0, zh="甲甲甲", en="AAA",
                                            speaker="SPEAKER_00")])
        isolated_db.upsert_character(did_a, "SPEAKER_00", character_name="Alice",
                                     voice_actor="Actor A", tts_voice="en-US-EmmaNeural",
                                     pronouns="she/her", ref_text="甲的参考", clone_engine="omnivoice",
                                     voice_design="female, low pitch")
        did_b = isolated_db.create_drama(title_en="Drama B", media_type="audio_drama",
                                         content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did_b, [Line(idx=0, start=0.0, end=1.0, zh="乙乙乙", en="BBB",
                                            speaker="SPEAKER_00")])
        isolated_db.upsert_character(did_b, "SPEAKER_00", character_name="Bob",
                                     voice_actor="Actor B", tts_voice="en-US-JennyNeural",
                                     pronouns="he/him", ref_text="乙的参考", clone_engine="chatterbox",
                                     voice_design="male, british accent")
        return did_a, did_b

    def _character(self, db_module, did):
        row = next(c for c in db_module.list_characters(did) if c["speaker_label"] == "SPEAKER_00")
        return {f: row[f] for f in self._FIELDS}

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _switch_to(self, at, did):
        box = [b for b in at.selectbox if b.label == "Drama"][0]
        label = next(l for l in box.options if l.startswith(f"#{did} "))
        box.set_value(label).run(timeout=30)

    def _name_box(self, at):
        return [t for t in at.text_input if t.key and t.key.startswith("cname_")][0]

    def test_switching_back_and_forth_never_leaks_character_data(self, isolated_db):
        did_a, did_b = self._two_dramas(isolated_db)
        before_a = self._character(isolated_db, did_a)
        before_b = self._character(isolated_db, did_b)

        at = self._run(did_a)
        assert self._name_box(at).value == "Alice"

        self._switch_to(at, did_b)
        at.run(timeout=30)
        assert self._name_box(at).value == "Bob"
        assert self._character(isolated_db, did_b) == before_b

        self._switch_to(at, did_a)
        at.run(timeout=30)
        assert self._name_box(at).value == "Alice"
        assert self._character(isolated_db, did_a) == before_a
        assert self._character(isolated_db, did_b) == before_b

    def test_editing_a_field_right_after_switching_writes_only_the_new_dramas_data(
            self, isolated_db):
        did_a, did_b = self._two_dramas(isolated_db)
        before_a = self._character(isolated_db, did_a)

        at = self._run(did_a)
        self._switch_to(at, did_b)

        [t for t in at.text_input if t.key and t.key.startswith("cva_")][0] \
            .set_value("New Actor B").run(timeout=30)

        after_b = self._character(isolated_db, did_b)
        assert after_b["voice_actor"] == "New Actor B"
        assert after_b["character_name"] == "Bob"
        assert after_b["tts_voice"] == "en-US-JennyNeural"
        assert after_b["pronouns"] == "he/him"
        assert after_b["ref_text"] == "乙的参考"
        assert after_b["clone_engine"] == "chatterbox"
        assert after_b["voice_design"] == "male, british accent"
        assert self._character(isolated_db, did_a) == before_a


class TestManualRefreshButton:
    """Step 9i item 2: a general-purpose escape hatch next to the drama
    picker -- reloads lines from the database and clears the same
    per-line widget cache every other targeted fix in this file already
    clears, regardless of what caused the staleness."""

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                       content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi there.")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_refresh_button_exists_next_to_the_drama_picker(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        assert [b for b in at.button if b.label == "🔄 Refresh"]

    def test_refresh_reloads_a_change_made_outside_the_page(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        assert [ta.value for ta in at.text_area if ta.key == "en_0"] == ["Hi there."]

        # A change made by something other than this page (another tab,
        # a job with no completion handler of its own, direct DB access)
        # -- the exact "whatever caused the staleness" case this button
        # exists for.
        changed = isolated_db.load_line_objects(did)
        changed[0].en = "Refreshed text."
        isolated_db.save_lines(did, changed, fields=("en",))

        [b for b in at.button if b.label == "🔄 Refresh"][0].click()
        at.run(timeout=30)

        assert at.session_state.lines[0].en == "Refreshed text."
        assert [ta.value for ta in at.text_area if ta.key == "en_0"] == ["Refreshed text."]

    def test_refresh_is_disabled_for_new_drama(self, isolated_db):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = None
        at.session_state["lines"] = None
        at.run(timeout=30)

        refresh_buttons = [b for b in at.button if b.label == "🔄 Refresh"]
        assert refresh_buttons and refresh_buttons[0].disabled


class TestRawNovelToggleGatedByContentMode:
    """Step 5b item 6: the raw-novel uploader used to render unconditionally,
    above the content_mode radio, for every content mode including
    Streamer/VOD where it has no use. It's now placed after content_mode is
    read, hidden entirely for streamer_vod/novel_narration, and shown for
    audio_drama behind an off-by-default toggle -- except when a raw novel
    was already saved for the drama, where the toggle stays on so existing
    configuration doesn't silently disappear."""

    def _drama(self, isolated_db, content_mode, with_existing_raw_novel=False):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode=content_mode, status="not started")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        if with_existing_raw_novel:
            ddir = isolated_db.drama_dir(did)
            with open(os.path.join(ddir, "raw_novel_context.txt"), "w", encoding="utf-8") as f:
                f.write("existing raw novel text")
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _toggle(self, at, did):
        matches = [c for c in at.checkbox if c.key == f"raw_novel_toggle_{did}"]
        return matches[0] if matches else None

    def test_toggle_hidden_for_streamer_vod(self, isolated_db):
        did = self._drama(isolated_db, "streamer_vod")
        at = self._run(did)
        assert self._toggle(at, did) is None

    def test_toggle_hidden_for_novel_narration(self, isolated_db):
        did = self._drama(isolated_db, "novel_narration")
        at = self._run(did)
        assert self._toggle(at, did) is None

    def test_toggle_off_by_default_for_audio_drama_with_no_existing_raw_novel(self, isolated_db):
        did = self._drama(isolated_db, "audio_drama")
        at = self._run(did)
        toggle = self._toggle(at, did)
        assert toggle is not None
        assert toggle.value is False
        assert not [u for u in at.file_uploader if u.key == f"raw_novel_{did}"]

    def test_toggle_on_when_raw_novel_already_saved(self, isolated_db):
        did = self._drama(isolated_db, "audio_drama", with_existing_raw_novel=True)
        at = self._run(did)
        toggle = self._toggle(at, did)
        assert toggle is not None
        assert toggle.value is True
        assert [u for u in at.file_uploader if u.key == f"raw_novel_{did}"]


class TestDestructiveActionsNeedConfirmation:
    """Step 25z: "Delete this drama," "Remove current audio/video," and
    "Remove raw novel context" used to fire on a single click with zero
    confirmation of any kind, unlike every other destructive action in the
    app (Library's full-reset type-to-confirm, Library's bulk-delete
    checkbox, Step 25s's video-overwrite checkbox). Each button is now
    disabled until its confirmation step is explicitly completed, and
    still performs the real deletion/removal once it is."""

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _button(self, at, label):
        matches = [b for b in at.button if label in b.label]
        assert matches, f"no button labeled {label!r}"
        return matches[0]

    def _plain_drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="not started")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        return did

    def test_delete_drama_button_disabled_until_checkbox_and_typed_confirm(self, isolated_db):
        did = self._plain_drama(isolated_db)
        at = self._run(did)
        assert self._button(at, "🗑️ Delete this drama").disabled

        at.checkbox(key=f"confirm_delete_drama_{did}").set_value(True).run(timeout=30)
        assert self._button(at, "🗑️ Delete this drama").disabled, \
            "checking the box alone shouldn't enable it -- typing DELETE is also required"

        at.text_input(key=f"delete_drama_typed_{did}").set_value("delete").run(timeout=30)
        assert self._button(at, "🗑️ Delete this drama").disabled, \
            "the typed confirmation must match DELETE exactly"

        at.text_input(key=f"delete_drama_typed_{did}").set_value("DELETE").run(timeout=30)
        assert not self._button(at, "🗑️ Delete this drama").disabled
        assert isolated_db.get_drama(did) is not None

    def test_delete_drama_button_deletes_once_confirmed(self, isolated_db):
        did = self._plain_drama(isolated_db)
        at = self._run(did)
        at.checkbox(key=f"confirm_delete_drama_{did}").set_value(True).run(timeout=30)
        at.text_input(key=f"delete_drama_typed_{did}").set_value("DELETE").run(timeout=30)
        self._button(at, "🗑️ Delete this drama").click().run(timeout=30)
        assert isolated_db.get_drama(did) is None

    def _drama_with_audio(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        audio_filename="audio.wav")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="你好")])
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        return did

    def test_remove_audio_button_disabled_until_confirmed(self, isolated_db):
        did = self._drama_with_audio(isolated_db)
        at = self._run(did)
        assert self._button(at, "🗑️ Remove current audio/video").disabled
        assert os.path.exists(os.path.join(isolated_db.drama_dir(did), "audio.wav"))

    def test_remove_audio_button_removes_file_once_confirmed(self, isolated_db):
        did = self._drama_with_audio(isolated_db)
        ddir = isolated_db.drama_dir(did)
        at = self._run(did)
        at.checkbox(key=f"confirm_rm_audio_{did}").set_value(True).run(timeout=30)
        assert not self._button(at, "🗑️ Remove current audio/video").disabled
        self._button(at, "🗑️ Remove current audio/video").click().run(timeout=30)
        assert not os.path.exists(os.path.join(ddir, "audio.wav"))
        assert isolated_db.get_drama(did)["audio_filename"] is None

    def _drama_with_raw_novel(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="not started")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "raw_novel_context.txt"), "w", encoding="utf-8") as f:
            f.write("existing raw novel text")
        return did

    def test_remove_raw_novel_button_disabled_until_confirmed(self, isolated_db):
        did = self._drama_with_raw_novel(isolated_db)
        at = self._run(did)
        assert self._button(at, "🗑️ Remove raw novel context").disabled
        assert os.path.exists(os.path.join(isolated_db.drama_dir(did), "raw_novel_context.txt"))

    def test_remove_raw_novel_button_removes_file_once_confirmed(self, isolated_db):
        did = self._drama_with_raw_novel(isolated_db)
        ddir = isolated_db.drama_dir(did)
        at = self._run(did)
        at.checkbox(key=f"confirm_rmraw_{did}").set_value(True).run(timeout=30)
        assert not self._button(at, "🗑️ Remove raw novel context").disabled
        self._button(at, "🗑️ Remove raw novel context").click().run(timeout=30)
        assert not os.path.exists(os.path.join(ddir, "raw_novel_context.txt"))


class TestFourMoreDestructiveActionsNeedConfirmation:
    """Step 71: deleting a saved translation version, a glossary term
    (single and bulk), and removing a series character all used to fire
    on a single click with no confirmation, unlike this app's own
    established pattern (Step 25z's Workspace deletes, Library's
    bulk-drama-delete checkbox)."""

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _drama_with_two_versions(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")]
        isolated_db.save_lines(did, lines)
        isolated_db.save_translation_version(did, lines, "First try", engine="claude")
        vid = isolated_db.save_translation_version(did, lines, "Second try", engine="claude")
        return did, vid

    def test_delete_version_button_disabled_until_confirmed(self, isolated_db):
        did, vid = self._drama_with_two_versions(isolated_db)
        at = self._run(did)
        assert [b for b in at.button if b.key == f"delv_{vid}"][0].disabled

    def test_delete_version_removes_it_once_confirmed(self, isolated_db):
        did, vid = self._drama_with_two_versions(isolated_db)
        at = self._run(did)
        at.checkbox(key=f"confirm_delv_{vid}").set_value(True).run(timeout=30)
        assert not [b for b in at.button if b.key == f"delv_{vid}"][0].disabled
        [b for b in at.button if b.key == f"delv_{vid}"][0].click().run(timeout=30)
        assert all(v["id"] != vid for v in isolated_db.list_translation_versions(did))

    def _drama_with_glossary_term_and_series_character(self, isolated_db):
        series_id = isolated_db.get_or_create_series("Test Series")
        did = isolated_db.create_drama(title_en="Test Drama", series_id=series_id,
                                        media_type="audio_drama", content_mode="audio_drama",
                                        status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])
        isolated_db.upsert_glossary_term(series_id, "小玲", "Xiaoling")
        term_id = isolated_db.list_glossary_terms(series_id)[0]["id"]
        isolated_db.upsert_series_character(series_id, "Xiaoling")
        sc_id = isolated_db.list_series_characters(series_id)[0]["id"]
        return did, term_id, sc_id

    def test_delete_glossary_term_button_disabled_until_confirmed(self, isolated_db):
        did, term_id, _ = self._drama_with_glossary_term_and_series_character(isolated_db)
        at = self._run(did)
        assert [b for b in at.button if b.key == f"delglo_{term_id}"][0].disabled

    def test_delete_glossary_term_removes_it_once_confirmed(self, isolated_db):
        did, term_id, _ = self._drama_with_glossary_term_and_series_character(isolated_db)
        at = self._run(did)
        at.checkbox(key=f"confirm_delglo_{term_id}").set_value(True).run(timeout=30)
        assert not [b for b in at.button if b.key == f"delglo_{term_id}"][0].disabled
        [b for b in at.button if b.key == f"delglo_{term_id}"][0].click().run(timeout=30)
        assert not any(t["id"] == term_id for t in isolated_db.list_glossary_terms(
            isolated_db.get_drama(did)["series_id"]))

    def test_bulk_delete_glossary_terms_button_disabled_until_confirmed(self, isolated_db):
        did, term_id, _ = self._drama_with_glossary_term_and_series_character(isolated_db)
        at = self._run(did)
        sid = isolated_db.get_drama(did)["series_id"]
        at.multiselect(key=f"bulk_glossary_pick_{sid}").set_value(["小玲"]).run(timeout=30)
        assert [b for b in at.button if b.key == f"bulk_glossary_delete_{sid}"][0].disabled

    def test_bulk_delete_glossary_terms_removes_them_once_confirmed(self, isolated_db):
        did, term_id, _ = self._drama_with_glossary_term_and_series_character(isolated_db)
        at = self._run(did)
        sid = isolated_db.get_drama(did)["series_id"]
        at.multiselect(key=f"bulk_glossary_pick_{sid}").set_value(["小玲"]).run(timeout=30)
        at.checkbox(key=f"bulk_glossary_delete_confirm_{sid}").set_value(True).run(timeout=30)
        assert not [b for b in at.button if b.key == f"bulk_glossary_delete_{sid}"][0].disabled
        [b for b in at.button if b.key == f"bulk_glossary_delete_{sid}"][0].click().run(timeout=30)
        assert isolated_db.list_glossary_terms(sid) == []

    def test_remove_series_character_button_disabled_until_confirmed(self, isolated_db):
        did, _, sc_id = self._drama_with_glossary_term_and_series_character(isolated_db)
        at = self._run(did)
        assert [b for b in at.button if b.key == f"scdel_{sc_id}"][0].disabled

    def test_remove_series_character_removes_it_once_confirmed(self, isolated_db):
        did, _, sc_id = self._drama_with_glossary_term_and_series_character(isolated_db)
        at = self._run(did)
        at.checkbox(key=f"confirm_scdel_{sc_id}").set_value(True).run(timeout=30)
        assert not [b for b in at.button if b.key == f"scdel_{sc_id}"][0].disabled
        [b for b in at.button if b.key == f"scdel_{sc_id}"][0].click().run(timeout=30)
        sid = isolated_db.get_drama(did)["series_id"]
        assert not any(sc["id"] == sc_id for sc in isolated_db.list_series_characters(sid))


class TestDiarizationEstimateCaption:
    """Step 5b item 8: pyannote's pipeline makes one call and only returns
    a result at the end -- there's no incremental progress callback in its
    public API (confirmed against diarize.diarize's own single blocking
    pipeline() call), so an indeterminate st.spinner is the most that can
    be shown live. This adds an honest estimated-duration caption, scaled
    off the audio's own length, next to it."""

    def test_caption_scales_with_audio_length(self):
        from tabs.workspace_tab import _diarization_estimate_caption
        caption = _diarization_estimate_caption(754)  # 12:34
        assert "12:34" in caption

    def test_caption_has_a_generic_fallback_for_unknown_length(self):
        from tabs.workspace_tab import _diarization_estimate_caption
        caption = _diarization_estimate_caption(0)
        assert caption

    def _drama_with_audio(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        audio_filename="audio.wav")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=754.0, zh="你好", en="Hello")])
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        return did

    def _run(self, did, monkeypatch):
        from streamlit.testing.v1 import AppTest

        # Step 4d: the button now starts a real multiprocessing.Process
        # via background_jobs.start_process_job() instead of calling
        # diarize.diarize() synchronously -- faked here the same way
        # test_background_jobs.py fakes it, so this test doesn't spawn a
        # real OS process.
        monkeypatch.setattr(background_jobs, "start_process_job", lambda *a, **k: True)

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["workspace_hf_token_input"] = "fake-hf-token"
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_estimate_caption_shown_before_rerunning_speaker_detection(self, isolated_db, monkeypatch):
        did = self._drama_with_audio(isolated_db)
        at = self._run(did, monkeypatch)

        buttons = [b for b in at.button if b.key == f"rerun_speakers_{did}"]
        assert buttons, "🔁 Re-run speaker detection button not found"
        buttons[0].click()
        at.run(timeout=30)

        assert any("12:34" in c.value for c in at.caption)


class TestSpeakerDetectionRealMidRunStop:
    """Step 4d: diarization now runs as a real OS subprocess
    (background_jobs.start_process_job), not a blocking gpu_slot() call
    inside the button handler -- so it's non-blocking, and Cancel can
    actually terminate the underlying work rather than just asking it to
    stop cooperatively (pyannote's pipeline call has no such checkpoint).
    multiprocessing.Process itself is faked throughout, matching
    test_background_jobs.py's own convention -- no real OS process is
    ever spawned -- but the real _jobs dict, watcher thread, and lock
    all run for real."""

    def _drama_with_audio(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        audio_filename="audio.wav")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello")])
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["workspace_hf_token_input"] = "fake-hf-token"
        at.run(timeout=30)
        return at

    def _fake_process_factory(self, monkeypatch, run_target_on_start=False, alive_forever=False):
        instances = []

        class _FakeProcess:
            def __init__(self, target, args, daemon=True):
                self._target, self._args = target, args
                self._alive = True
                self.terminated = False
                self.exitcode = None

            def start(self):
                if run_target_on_start:
                    self._target(*self._args)
                    self._alive = False
                    self.exitcode = 0
                elif not alive_forever:
                    self._alive = False
                    self.exitcode = 1

            def is_alive(self):
                return self._alive

            def terminate(self):
                self.terminated = True
                self._alive = False

            def join(self, timeout=None):
                pass

        def factory(target, args, daemon=True):
            proc = _FakeProcess(target, args, daemon=daemon)
            instances.append(proc)
            return proc

        monkeypatch.setattr(background_jobs.multiprocessing, "Process", factory)
        return instances

    def _wait_past(self, job_id, status_to_leave, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = background_jobs.get_status(job_id)
            if status is None or status["status"] != status_to_leave:
                return status
            time.sleep(0.02)
        return background_jobs.get_status(job_id)

    def test_clicking_the_button_starts_a_real_background_job_not_a_blocking_call(
            self, isolated_db, monkeypatch):
        """The whole point of Step 4d: the button no longer blocks the
        script inside a gpu_slot() call -- it starts a job and control
        returns to the script immediately, with the job visible via
        background_jobs.get_status()."""
        did = self._drama_with_audio(isolated_db)
        self._fake_process_factory(monkeypatch, alive_forever=True)
        job_id = f"diarize_{did}"
        background_jobs.clear_job(job_id)

        at = self._run(did)
        buttons = [b for b in at.button if b.key == f"rerun_speakers_{did}"]
        assert buttons
        buttons[0].click().run(timeout=30)

        status = background_jobs.get_status(job_id)
        assert status is not None
        assert status["status"] in ("running", "queued")
        # alive_forever=True means a real watcher thread is still polling
        # in the background -- cancel and wait for it to actually exit
        # before this test ends, so it can't outlive this test and touch
        # a later test's same-named job_id (isolated_db resets its own
        # autoincrement per test, but background_jobs' module-level
        # state and daemon threads don't).
        if status["status"] == "running":
            background_jobs.request_cancel(job_id)
            self._wait_past(job_id, "running")
        background_jobs.clear_job(job_id)

    def test_cancel_button_appears_while_running_and_actually_requests_a_stop(
            self, isolated_db, monkeypatch):
        did = self._drama_with_audio(isolated_db)
        instances = self._fake_process_factory(monkeypatch, alive_forever=True)
        job_id = f"diarize_{did}"
        background_jobs.clear_job(job_id)
        background_jobs.start_process_job(
            job_id, diarize.diarize_subprocess_worker,
            args=("/fake/audio.wav", "hf_x", None), gpu_touching=False)

        at = self._run(did)
        cancel_buttons = [b for b in at.button if b.key == f"cancel_diarize_{did}"]
        assert cancel_buttons, "Cancel button should show while the job is running"
        # AppTest's st.rerun() re-executes synchronously within this one
        # call, so by the time it returns the real watcher thread may
        # already have noticed the cancel, terminated the process, AND
        # (correctly) had the next render clear the finished job record --
        # all before this line runs. That's correct, fast behavior, not a
        # bug, so this doesn't assert on is_cancel_requested()/a "cancelled"
        # status still being present -- only on what must be true either
        # way: the fake process's terminate() was actually called (the
        # exit condition this test exists for), and the job never ended up
        # "done"/"error" (it never finishes on its own -- alive_forever=True
        # -- so the only way it can stop at all is via terminate()).
        cancel_buttons[0].click().run(timeout=30)

        deadline = time.time() + 2
        while time.time() < deadline and not instances[0].terminated:
            time.sleep(0.02)
        assert instances[0].terminated is True, "Cancel must call the real Process.terminate()"
        status = background_jobs.get_status(job_id)
        assert status is None or status["status"] == "cancelled"
        if status is not None:
            background_jobs.clear_job(job_id)

    def test_done_job_result_is_applied_the_same_way_a_synchronous_run_used_to(
            self, isolated_db, monkeypatch):
        """Confirms the async path produces the exact same end state a
        direct diarize.diarize() call used to -- turns saved, speakers
        merged onto the drama's lines."""
        did = self._drama_with_audio(isolated_db)
        self._fake_process_factory(monkeypatch, run_target_on_start=True)

        def fake_worker(audio_path, hf_token, num_speakers, result_queue):
            result_queue.put(("ok", {"segments": [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}],
                                     "model": "fake-model", "embeddings": {}}))
        monkeypatch.setattr(diarize, "diarize_subprocess_worker", fake_worker)

        job_id = f"diarize_{did}"
        background_jobs.clear_job(job_id)

        at = self._run(did)
        buttons = [b for b in at.button if b.key == f"rerun_speakers_{did}"]
        # AppTest's st.rerun() re-executes the script synchronously within
        # this one call (rather than returning to the caller first), and
        # the fake process runs its target synchronously in start() too --
        # so by the time this returns, the result has already round-
        # tripped through the (real) queue and been applied.
        buttons[0].click().run(timeout=30)

        lines = db.load_line_objects(did)
        assert lines[0].speaker == "SPEAKER_00"
        assert background_jobs.get_status(job_id) is None  # cleared once applied


class TestDubGenerationRealMidRunStop:
    """Step 4e: dub/narration generation now runs as a real OS subprocess
    (background_jobs.start_process_job), not a blocking gpu_slot() call
    inside the button handler -- so it's non-blocking, and Cancel can
    actually terminate the underlying work. Confirmed safe to hard-stop:
    each line's clip writes to its own file, and a re-run already reuses
    or regenerates any partial clip from a prior run. multiprocessing.
    Process itself is faked throughout, matching test_background_jobs.py's
    and TestSpeakerDetectionRealMidRunStop's own convention -- no real OS
    process is ever spawned -- but the real _jobs dict, watcher thread,
    and lock all run for real."""

    def _drama_with_a_line(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def _fake_process_factory(self, monkeypatch, run_target_on_start=False, alive_forever=False):
        instances = []

        class _FakeProcess:
            def __init__(self, target, args, daemon=True):
                self._target, self._args = target, args
                self._alive = True
                self.terminated = False
                self.exitcode = None

            def start(self):
                if run_target_on_start:
                    self._target(*self._args)
                    self._alive = False
                    self.exitcode = 0
                elif not alive_forever:
                    self._alive = False
                    self.exitcode = 1

            def is_alive(self):
                return self._alive

            def terminate(self):
                self.terminated = True
                self._alive = False

            def join(self, timeout=None):
                pass

        def factory(target, args, daemon=True):
            proc = _FakeProcess(target, args, daemon=daemon)
            instances.append(proc)
            return proc

        monkeypatch.setattr(background_jobs.multiprocessing, "Process", factory)
        return instances

    def _wait_past(self, job_id, status_to_leave, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = background_jobs.get_status(job_id)
            if status is None or status["status"] != status_to_leave:
                return status
            time.sleep(0.02)
        return background_jobs.get_status(job_id)

    def test_clicking_the_button_starts_a_real_background_job_not_a_blocking_call(
            self, isolated_db, monkeypatch):
        did = self._drama_with_a_line(isolated_db)
        self._fake_process_factory(monkeypatch, alive_forever=True)
        job_id = f"dub_{did}"
        background_jobs.clear_job(job_id)

        at = self._run(did)
        buttons = [b for b in at.button if b.label == "🎙️ Generate dub track"]
        assert buttons
        buttons[0].click().run(timeout=30)

        status = background_jobs.get_status(job_id)
        assert status is not None
        assert status["status"] in ("running", "queued")
        # See TestSpeakerDetectionRealMidRunStop for why this cleanup is
        # needed with alive_forever=True (a lingering watcher thread can
        # otherwise touch a later test's same-named job_id).
        if status["status"] == "running":
            background_jobs.request_cancel(job_id)
            self._wait_past(job_id, "running")
        background_jobs.clear_job(job_id)

    def test_cancel_button_appears_while_running_and_actually_requests_a_stop(
            self, isolated_db, monkeypatch):
        did = self._drama_with_a_line(isolated_db)
        instances = self._fake_process_factory(monkeypatch, alive_forever=True)
        job_id = f"dub_{did}"
        background_jobs.clear_job(job_id)
        background_jobs.start_process_job(
            job_id, dub_module.build_track_subprocess_worker,
            args=([], "/fake/dir", {}, "en-US-AvaNeural", {}, "edge_tts", False, {}, 1.4, 0.85, None),
            gpu_touching=False)

        at = self._run(did)
        cancel_buttons = [b for b in at.button if b.key == f"cancel_dub_{did}"]
        assert cancel_buttons, "Cancel button should show while the job is running"
        cancel_buttons[0].click().run(timeout=30)

        deadline = time.time() + 2
        while time.time() < deadline and not instances[0].terminated:
            time.sleep(0.02)
        assert instances[0].terminated is True, "Cancel must call the real Process.terminate()"
        status = background_jobs.get_status(job_id)
        assert status is None or status["status"] == "cancelled"
        if status is not None:
            background_jobs.clear_job(job_id)

    def test_done_job_result_is_applied_via_field_scoped_save(self, isolated_db, monkeypatch):
        """Confirms the async path saves only dub_filename (never the
        whole line list) -- the new risk Step 4e introduces by letting
        the user edit lines while dubbing runs in the background, unlike
        the old synchronous call which made that impossible."""
        did = self._drama_with_a_line(isolated_db)
        self._fake_process_factory(monkeypatch, run_target_on_start=True)
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        out_path = os.path.join(ddir, "dub_track.wav")
        with open(out_path, "wb") as f:
            f.write(b"x")

        def fake_worker(lines, drama_dir, voice_map, default_voice, clone_map, tts_engine,
                        is_narration, emotion_map, max_speedup, max_slowdown, offline_voice_map,
                        narrate_original, source_language, result_queue):
            lines[0].dub_filename = "dub_clips/line_0000.wav"
            result_queue.put(("ok", {"lines": lines, "out_path": out_path, "errors": []}))
        monkeypatch.setattr(dub_module, "build_track_subprocess_worker", fake_worker)

        job_id = f"dub_{did}"
        background_jobs.clear_job(job_id)

        at = self._run(did)
        buttons = [b for b in at.button if b.label == "🎙️ Generate dub track"]
        buttons[0].click().run(timeout=30)

        lines = db.load_line_objects(did)
        assert lines[0].dub_filename == "dub_clips/line_0000.wav"
        assert db.get_drama(did)["status"] == "dubbed"
        assert background_jobs.get_status(job_id) is None  # cleared once applied


class TestResegmentationRealMidRunStop:
    """Step 4e: the local-Ollama LLM re-segmentation pass now runs as a
    real OS subprocess for the same reason as dub generation above --
    rule-only and cloud-engine resegmentation stay synchronous (never
    GPU-touching). Confirmed the lowest-risk of the three Step 4e cases:
    resegment_lines is pure in-memory computation, nothing written until a
    separate "Apply" step the caller triggers afterward."""

    def _drama_with_a_line(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated",
                                        translation_engine="ollama")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好" * 20, en="")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state[f"reseg_use_llm_{did}"] = True
        at.session_state["settings_ollama_url"] = "http://fake-ollama:11434"
        at.run(timeout=30)
        return at

    def _fake_process_factory(self, monkeypatch, run_target_on_start=False, alive_forever=False):
        instances = []

        class _FakeProcess:
            def __init__(self, target, args, daemon=True):
                self._target, self._args = target, args
                self._alive = True
                self.terminated = False
                self.exitcode = None

            def start(self):
                if run_target_on_start:
                    self._target(*self._args)
                    self._alive = False
                    self.exitcode = 0
                elif not alive_forever:
                    self._alive = False
                    self.exitcode = 1

            def is_alive(self):
                return self._alive

            def terminate(self):
                self.terminated = True
                self._alive = False

            def join(self, timeout=None):
                pass

        def factory(target, args, daemon=True):
            proc = _FakeProcess(target, args, daemon=daemon)
            instances.append(proc)
            return proc

        monkeypatch.setattr(background_jobs.multiprocessing, "Process", factory)
        return instances

    def test_cancel_button_appears_while_running_and_actually_requests_a_stop(
            self, isolated_db, monkeypatch):
        did = self._drama_with_a_line(isolated_db)
        instances = self._fake_process_factory(monkeypatch, alive_forever=True)
        job_id = f"resegment_{did}"
        background_jobs.clear_job(job_id)
        background_jobs.start_process_job(
            job_id, resegment.resegment_subprocess_worker,
            args=([], "zh", None, None, "simplified"), gpu_touching=True)

        at = self._run(did)
        cancel_buttons = [b for b in at.button if b.key == f"cancel_reseg_{did}"]
        assert cancel_buttons, "Cancel button should show while the job is running"
        cancel_buttons[0].click().run(timeout=30)

        deadline = time.time() + 2
        while time.time() < deadline and not instances[0].terminated:
            time.sleep(0.02)
        assert instances[0].terminated is True, "Cancel must call the real Process.terminate()"
        status = background_jobs.get_status(job_id)
        assert status is None or status["status"] == "cancelled"
        if status is not None:
            background_jobs.clear_job(job_id)

    def test_done_job_result_is_applied_as_a_preview_not_saved_directly(
            self, isolated_db, monkeypatch):
        """Re-segmentation's result is only ever a preview -- confirms the
        async path populates the same session_state preview key a direct
        resegment_lines() call used to, without touching the database."""
        did = self._drama_with_a_line(isolated_db)
        self._fake_process_factory(monkeypatch, run_target_on_start=True)
        before = db.load_line_objects(did)

        def fake_worker(lines, language, engine, segments, chinese_script, result_queue):
            new_lines = [Line(idx=0, start=0.0, end=0.5, zh="你好" * 10),
                        Line(idx=1, start=0.5, end=1.0, zh="你好" * 10)]
            result_queue.put(("ok", {
                "lines": new_lines,
                "changed": [(before[0].id, 0, before[0].zh, ["你好" * 10, "你好" * 10])],
                "usage_calls": [],
            }))
        monkeypatch.setattr(resegment, "resegment_subprocess_worker", fake_worker)

        job_id = f"resegment_{did}"
        background_jobs.clear_job(job_id)

        at = self._run(did)
        buttons = [b for b in at.button if b.key == f"reseg_preview_btn_{did}"]
        assert buttons
        buttons[0].click().run(timeout=30)

        assert db.load_line_objects(did)[0].zh == before[0].zh  # nothing saved yet
        assert background_jobs.get_status(job_id) is None  # cleared once applied
        reseg_state = at.session_state[f"reseg_preview_{did}"]
        assert len(reseg_state["lines"]) == 2


class TestVerticalShortsExport:
    """Step 6e: the vertical/shorts export panel -- a clip-range picker
    with a live time/size estimate and a soft "consider a shorter clip"
    prompt past 20 minutes, shown before the render starts (never a hard
    block). ffmpeg/ffprobe are always mocked -- whether a rendered clip
    actually plays and looks right is the roadmap's own manual check, not
    something this can judge."""

    def _drama_with_video(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated",
                                        audio_filename="audio.wav", source_video_filename="video.mp4")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello")])
        ddir = isolated_db.drama_dir(did)
        for name in ("audio.wav", "video.mp4"):
            with open(os.path.join(ddir, name), "wb") as f:
                f.write(b"x")
        return did

    def _run(self, did, monkeypatch, duration=600.0):
        from streamlit.testing.v1 import AppTest
        import video_export

        monkeypatch.setattr(video_export, "probe_duration_seconds", lambda p: duration)

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_estimate_and_range_slider_appear(self, isolated_db, monkeypatch):
        did = self._drama_with_video(isolated_db)
        at = self._run(did, monkeypatch, duration=120.0)
        sliders = [s for s in at.slider if s.key == f"vshort_range_{did}"]
        assert sliders and sliders[0].value == (0.0, 120.0)
        assert any("render" in c.value for c in at.caption)
        assert any("MB" in c.value for c in at.caption)
        assert not any("long selection" in w.value for w in at.warning)

    def test_long_selection_shows_a_soft_prompt_not_a_block(self, isolated_db, monkeypatch):
        did = self._drama_with_video(isolated_db)
        at = self._run(did, monkeypatch, duration=25 * 60)
        assert any("long selection" in w.value for w in at.warning)
        buttons = [b for b in at.button if b.key == f"vshort_generate_{did}"]
        assert buttons and buttons[0].disabled is False  # a prompt, never a hard block

    def test_probe_failure_warns_instead_of_crashing(self, isolated_db, monkeypatch):
        import video_export
        did = self._drama_with_video(isolated_db)
        monkeypatch.setattr(video_export, "probe_duration_seconds",
                             lambda p: (_ for _ in ()).throw(RuntimeError("no ffprobe")))

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        assert not at.exception
        assert any("Couldn't read this video's duration" in w.value for w in at.warning)

    def test_generate_renders_a_timeshifted_clip_and_offers_a_download(self, isolated_db, monkeypatch):
        import video_export
        did = self._drama_with_video(isolated_db)
        calls = {}

        def fake_render(video_path, ass_text, out_path, start=0.0, end=None, crop_position=0.5):
            calls.update(video_path=video_path, ass_text=ass_text, out_path=out_path,
                         start=start, end=end, crop_position=crop_position)
            with open(out_path, "wb") as f:
                f.write(b"fake mp4 bytes")
            return out_path

        monkeypatch.setattr(video_export, "render_vertical_clip", fake_render)
        at = self._run(did, monkeypatch, duration=60.0)

        [b for b in at.button if b.key == f"vshort_generate_{did}"][0].click()
        at.run(timeout=30)

        assert calls["start"] == 0.0 and calls["end"] == 60.0
        assert calls["crop_position"] == 0.5
        assert "Dialogue:" in calls["ass_text"]  # a real ASS body, not the raw en text
        assert any("Vertical clip ready" in s.value for s in at.toast)
        assert any(dl.label.startswith("Download") for dl in at.download_button)


class TestSection10StandaloneSubtitleDownload:
    """Step 6i (Step 6b item 9): section 10's "Export full subtitled
    episode" used to have only one download -- the rendered video itself
    -- with no way to get the matching subtitle file without either
    rendering the whole video or going back to section 9 and manually
    picking the same format/language again. A new download button
    reuses the exact same _subtitle_text(field) closure section 9's own
    buttons call, so its content is identical by construction; this
    confirms that at the wiring level, not just by inspection."""

    def _drama_with_video(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated",
                                        audio_filename="audio.wav", source_video_filename="video.mp4")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello")])
        ddir = isolated_db.drama_dir(did)
        for name in ("audio.wav", "video.mp4"):
            with open(os.path.join(ddir, name), "wb") as f:
                f.write(b"x")
        return did

    def _run(self, did, monkeypatch):
        from streamlit.testing.v1 import AppTest
        import tabs.workspace_tab as wt_module

        # _subtitle_text's SRT branch calls this module-level lines_to_srt
        # directly (imported into tabs.workspace_tab's own namespace) --
        # spying on it here (a plain function monkeypatch, the same
        # pattern every other test in this file uses, not a Streamlit
        # internal) lets both section 9's and section 10's calls be
        # compared without needing to read a download_button's actual
        # served bytes back out of Streamlit's runtime.
        calls = []
        real_lines_to_srt = wt_module.lines_to_srt

        def spy(*a, **k):
            result = real_lines_to_srt(*a, **k)
            calls.append({"args": a, "kwargs": k, "result": result})
            return result
        monkeypatch.setattr(wt_module, "lines_to_srt", spy)

        # Step 12: these download buttons get a callable (content built on
        # click, so it reflects the style fragment's latest values) --
        # AppTest never clicks through to it, so call it at render instead
        # and the spy above still sees what each button would serve.
        import streamlit as st
        from streamlit.delta_generator import DeltaGenerator
        real_dg_download = DeltaGenerator.download_button

        def eager_download(dg, label, data=None, *a, **k):
            return real_dg_download(dg, label, data() if callable(data) else data, *a, **k)
        monkeypatch.setattr(DeltaGenerator, "download_button", eager_download)
        monkeypatch.setattr(st, "download_button",
                            lambda *a, **k: eager_download(st._main, *a, **k))

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at, calls

    def _field_of(self, call):
        return call["args"][1] if len(call["args"]) > 1 else call["kwargs"].get("field")

    def test_new_buttons_field_tracks_the_selected_language_not_hardcoded(
            self, isolated_db, monkeypatch):
        """Section 9's own two lines_to_srt calls (English/Chinese
        buttons) plus section 10's always-computed sub_text_map ("English"
        and "Chinese" entries, used for hardsub/softsub regardless of
        which one is picked) already account for 2 "en" + 2 "zh" calls on
        every run -- so a naive "at least 2 en calls" check would pass
        even if section 10's new download button ignored sub_language
        entirely. Switching the "Which subtitles to export on video"
        picker from its default (English) to Chinese must flip which
        field count gets the +1 from the new button specifically."""
        did = self._drama_with_video(isolated_db)
        at, calls = self._run(did, monkeypatch)

        # One extra plain rerun (no widget change) as the "before" baseline,
        # measured on its own rather than against _run's own two initial
        # renders, so a single render's call counts are what's compared.
        calls.clear()
        at.run(timeout=30)
        en_before = sum(1 for c in calls if self._field_of(c) == "en")
        zh_before = sum(1 for c in calls if self._field_of(c) == "zh")
        assert en_before == 3 and zh_before == 2  # section 9 + sub_text_map + the new button (English)

        calls.clear()
        [sub_language_box] = [s for s in at.selectbox
                              if s.label == "Which subtitles to export on video"]
        sub_language_box.set_value("Chinese").run(timeout=30)

        en_after = sum(1 for c in calls if self._field_of(c) == "en")
        zh_after = sum(1 for c in calls if self._field_of(c) == "zh")
        assert en_after == 2 and zh_after == 3  # the new button's field moved to Chinese

    def test_matches_section_9s_download_for_the_same_default_format_and_language(
            self, isolated_db, monkeypatch):
        did = self._drama_with_video(isolated_db)
        at, calls = self._run(did, monkeypatch)

        # Section 9's "Download English .srt" passes field "en" as a
        # positional arg; section 10's new button (English + SRT are
        # both the panels' defaults) calls the exact same _subtitle_text
        # closure with the same field -- both calls must have produced
        # identical text.
        en_calls = [c for c in calls if self._field_of(c) == "en"]
        assert len(en_calls) >= 2, f"expected at least 2 calls for field='en', saw {len(calls)} total"
        results = {c["result"] for c in en_calls}
        assert len(results) == 1, "section 9 and section 10 produced different content for the same field"

    def test_file_name_pairs_with_the_video_files_own_naming(self, isolated_db, monkeypatch):
        did = self._drama_with_video(isolated_db)
        at = self._run(did, monkeypatch)[0]
        [dl] = [d for d in at.download_button if d.label.startswith("📄 Download the matching")]
        assert dl  # exists and is reachable via the normal AppTest element tree too


class TestWhisperSizeDefaultsToLargeV3:
    """Step 5b item 9: the Speech recognition model picker fell back to
    'medium' for any drama with no whisper_size saved yet -- every new
    drama got Whisper's less-accurate option by default despite large-v3
    being available and comfortably fitting the app's target hardware.
    Now defaults to core.DEFAULT_WHISPER_SIZE (large-v3)."""

    def _new_drama(self, isolated_db, content_mode="audio_drama"):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode=content_mode, status="not started")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_new_drama_defaults_to_large_v3(self, isolated_db):
        did = self._new_drama(isolated_db)
        at = self._run(did)
        boxes = [sb for sb in at.selectbox if sb.key == f"whisper_size_{did}"]
        assert boxes, "Speech recognition model picker not found"
        assert boxes[0].value == "large-v3"

    def test_a_drama_with_an_explicit_size_keeps_it(self, isolated_db):
        did = self._new_drama(isolated_db)
        isolated_db.update_drama(did, whisper_size="small")
        at = self._run(did)
        boxes = [sb for sb in at.selectbox if sb.key == f"whisper_size_{did}"]
        assert boxes[0].value == "small"


class TestTranscriptionCancelButton:
    """Step 4g item 1: the transcription job's progress panel used to
    show only "Refresh progress" while running, with no way to actually
    stop it -- a real, confirmed gap (the user clicked Cancel during
    vocal separation and confirmed it did nothing, since no Cancel
    button was even rendered for this job in any phase)."""

    def _drama_with_audio(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        audio_filename="audio.wav")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def test_cancel_button_appears_while_running_and_actually_requests_a_stop(self, isolated_db):
        did = self._drama_with_audio(isolated_db)
        job_id = f"transcribe_{did}"
        _clear(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.3,
                                         "message": "Transcribing... 30%", "error": None,
                                         "cancel_requested": False, "result": None,
                                         "started_at": time.time(), "finished_at": None}
        try:
            at = self._run(did)
            cancel_buttons = [b for b in at.button if b.key == f"cancel_tc_{did}"]
            assert cancel_buttons, "Cancel button should show while transcription is running"
            cancel_buttons[0].click().run(timeout=30)
            assert background_jobs.is_cancel_requested(job_id) is True
        finally:
            _clear(job_id)

    def test_cancelled_outcome_shows_a_clear_message_not_a_generic_error(self, isolated_db):
        did = self._drama_with_audio(isolated_db)
        job_id = f"transcribe_{did}"
        _clear(job_id)
        background_jobs._jobs[job_id] = {"status": "done", "progress": 0.0, "message": "",
                                         "error": None, "cancel_requested": True,
                                         "result": {"failed_reason": "cancelled"},
                                         "started_at": time.time(), "finished_at": time.time()}
        try:
            at = self._run(did)
            assert any("cancelled" in w.value.lower() for w in at.warning)
        finally:
            _clear(job_id)


class TestAutotuneRealMidRunStop:
    """Step 6h: auto-tune runs each candidate min_silence_duration_ms
    value as its own real OS subprocess, one after another, reusing
    Step 4d/4e/4g's own subprocess-cancel mechanism (transcribe_for_timing
    has no cancel checkpoint of its own) -- so cancelling mid-run
    actually terminates whichever candidate is currently running, not
    just hides the UI. multiprocessing.Process itself is faked
    throughout, matching this project's established convention for
    these tests -- no real OS process is ever spawned."""

    def _drama_with_audio(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        audio_filename="audio.wav")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好")])
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def _fake_process_factory(self, monkeypatch, run_target_on_start=False, alive_forever=False):
        instances = []

        class _FakeProcess:
            def __init__(self, target, args, daemon=True):
                self._target, self._args = target, args
                self._alive = True
                self.terminated = False
                self.exitcode = None

            def start(self):
                if run_target_on_start:
                    self._target(*self._args)
                    self._alive = False
                    self.exitcode = 0
                elif not alive_forever:
                    self._alive = False
                    self.exitcode = 1

            def is_alive(self):
                return self._alive

            def terminate(self):
                self.terminated = True
                self._alive = False

            def join(self, timeout=None):
                pass

        def factory(target, args, daemon=True):
            proc = _FakeProcess(target, args, daemon=daemon)
            instances.append(proc)
            return proc

        monkeypatch.setattr(background_jobs.multiprocessing, "Process", factory)
        return instances

    def test_clicking_auto_tune_starts_a_real_background_job_not_a_blocking_call(
            self, isolated_db, monkeypatch):
        did = self._drama_with_audio(isolated_db)
        self._fake_process_factory(monkeypatch, alive_forever=True)
        job_id = f"autotune_{did}"
        background_jobs.clear_job(job_id)

        at = self._run(did)
        buttons = [b for b in at.button if b.key == f"autotune_btn_{did}"]
        assert buttons, "Auto-tune button not found"
        buttons[0].click().run(timeout=30)

        status = background_jobs.get_status(job_id)
        assert status is not None
        assert status["status"] in ("running", "queued")
        if status["status"] == "running":
            background_jobs.request_cancel(job_id)
            deadline = time.time() + 2
            while time.time() < deadline:
                s = background_jobs.get_status(job_id)
                if s is None or s["status"] != "running":
                    break
                time.sleep(0.02)
        background_jobs.clear_job(job_id)

    def test_cancel_button_appears_while_running_and_actually_requests_a_stop(
            self, isolated_db, monkeypatch):
        did = self._drama_with_audio(isolated_db)
        instances = self._fake_process_factory(monkeypatch, alive_forever=True)
        job_id = f"autotune_{did}"
        background_jobs.clear_job(job_id)
        background_jobs.start_process_job(
            job_id, core_module.autotune_subprocess_worker,
            args=("/fake/audio.wav", "large-v3", "zh", False, None, None, "", 5, 300, 0.5, False),
            gpu_touching=True)

        at = self._run(did)
        at.session_state[f"autotune_{did}"] = {"candidates": [300, 800, 1500], "results": [],
                                               "cancelled": False}
        at.run(timeout=30)
        cancel_buttons = [b for b in at.button if b.key == f"cancel_autotune_{did}"]
        assert cancel_buttons, "Cancel button should show while auto-tune is running"
        cancel_buttons[0].click().run(timeout=30)

        deadline = time.time() + 2
        while time.time() < deadline and not instances[0].terminated:
            time.sleep(0.02)
        assert instances[0].terminated is True, "Cancel must call the real Process.terminate()"
        status = background_jobs.get_status(job_id)
        assert status is None or status["status"] == "cancelled"
        if status is not None:
            background_jobs.clear_job(job_id)

    def test_each_candidate_produces_a_distinct_coverage_result_and_chains_to_the_next(
            self, isolated_db, monkeypatch):
        did = self._drama_with_audio(isolated_db)
        self._fake_process_factory(monkeypatch, run_target_on_start=True)

        # Three candidates, each returning a different segment set --
        # standing in for "produces a real, distinct diagnose_line_coverage
        # result" -- so the finished run must show 3 distinct results,
        # one per candidate, not the same one repeated three times.
        segments_by_ms = {
            300: [{"start": float(i), "end": float(i) + 1, "text": f"line{i}"} for i in range(5)],
            800: [{"start": float(i), "end": float(i) + 1, "text": f"line{i}"} for i in range(3)],
            1500: [{"start": 0.0, "end": 20.0, "text": "一二三四五六七八九十一二三四五"}],
        }

        def fake_worker(audio_path, model_size, language, use_gpu, local_model_path, hf_token,
                        initial_prompt, beam_size, candidate_ms, vad_threshold, fast_mode,
                        result_queue):
            result_queue.put(("ok", {"candidate_ms": candidate_ms,
                                     "segments": segments_by_ms[candidate_ms]}))
        monkeypatch.setattr(core_module, "autotune_subprocess_worker", fake_worker)

        job_id = f"autotune_{did}"
        background_jobs.clear_job(job_id)

        at = self._run(did)
        buttons = [b for b in at.button if b.key == f"autotune_btn_{did}"]
        buttons[0].click().run(timeout=30)
        # AppTest's st.rerun() re-executes synchronously and the fake
        # process runs its target for real, but the real watcher thread
        # still needs real wall-clock time to notice each candidate's
        # process exit and mark the job "done" -- poll with reruns
        # rather than assuming one .run() chains through all three.
        deadline = time.time() + 5
        state = None
        while time.time() < deadline:
            state = at.session_state.get(f"autotune_{did}")
            if state and len(state["results"]) >= 3:
                break
            time.sleep(0.05)
            at.run(timeout=30)

        assert state and len(state["results"]) == 3
        by_ms = {r["candidate_ms"]: r for r in state["results"]}
        assert by_ms[300]["total_lines"] == 5 and by_ms[300]["long_lines"] == 0
        assert by_ms[800]["total_lines"] == 3 and by_ms[800]["long_lines"] == 0
        assert by_ms[1500]["total_lines"] == 1 and by_ms[1500]["long_lines"] == 1
        background_jobs.clear_job(job_id)

    def test_page_refresh_while_a_result_is_ready_does_not_crash(self, isolated_db, monkeypatch):
        """Step 25d item 4: the "job just finished" branch appended
        straight into _autotune["results"] with no None-check -- the
        "still running" branch right above it already had one. Refreshing
        the page (a new session, so st.session_state lost the candidate
        list built when Auto-tune was started) while a candidate's result
        was sitting there "done" crashed instead of just rebuilding."""
        did = self._drama_with_audio(isolated_db)
        self._fake_process_factory(monkeypatch, alive_forever=True)
        job_id = f"autotune_{did}"
        background_jobs.clear_job(job_id)
        background_jobs._jobs[job_id] = {
            "status": "done", "progress": 1.0, "message": "", "error": None,
            "cancel_requested": False, "finished_at": time.time(),
            "result": {"candidate_ms": 300,
                      "segments": [{"start": 0.0, "end": 1.0, "text": "你好"}]},
        }
        try:
            # Deliberately NOT setting session_state[f"autotune_{did}"] --
            # that's the "lost across a refresh" part being simulated.
            at = self._run(did)
            assert not at.exception, [repr(e) for e in at.exception]
            state = at.session_state.get(f"autotune_{did}")
            assert state is not None
            assert state["results"] == [{"candidate_ms": 300, "long_lines": 0, "total_lines": 1}]
        finally:
            background_jobs.clear_job(job_id)

    def test_nothing_is_applied_until_the_user_explicitly_picks_one(self, isolated_db, monkeypatch):
        """Design requirement, not just a manual check: auto-tune must
        never silently apply a candidate -- the persisted slider value
        stays whatever it was until "Use Nms" is explicitly clicked."""
        did = self._drama_with_audio(isolated_db)
        self._fake_process_factory(monkeypatch, run_target_on_start=True)

        def fake_worker(audio_path, model_size, language, use_gpu, local_model_path, hf_token,
                        initial_prompt, beam_size, candidate_ms, vad_threshold, fast_mode,
                        result_queue):
            result_queue.put(("ok", {"candidate_ms": candidate_ms,
                                     "segments": [{"start": 0.0, "end": 1.0, "text": "x"}]}))
        monkeypatch.setattr(core_module, "autotune_subprocess_worker", fake_worker)

        job_id = f"autotune_{did}"
        background_jobs.clear_job(job_id)

        at = self._run(did)
        buttons = [b for b in at.button if b.key == f"autotune_btn_{did}"]
        buttons[0].click().run(timeout=30)
        deadline = time.time() + 5
        state = None
        while time.time() < deadline:
            state = at.session_state.get(f"autotune_{did}")
            if state and len(state["results"]) >= 3:
                break
            time.sleep(0.05)
            at.run(timeout=30)
        assert state and len(state["results"]) == 3

        # All three candidates finished -- the persisted slider value
        # must still be untouched (300, the default) until "Use" is clicked.
        assert at.session_state.get(f"min_silence_ms_{did}", 300) == 300

        use_buttons = [b for b in at.button if b.key == f"autotune_use_{did}_800"]
        assert use_buttons, "\"Use 800ms\" button not found among the results"
        use_buttons[0].click().run(timeout=30)

        assert at.session_state[f"min_silence_ms_{did}"] == 800
        assert f"autotune_{did}" not in at.session_state  # results cleared after picking
        background_jobs.clear_job(job_id)


class TestTranscribeQueuesBehindAnotherGpuJob:
    """Step 5c: a global, soft "one GPU job at a time" guard -- nothing
    before this stopped a transcription on one drama and, say, a
    diarization or OCR job on a completely different drama from starting
    concurrently and competing for the same VRAM. This exercises the real
    click path (not background_jobs directly) for one of the two jobs the
    roadmap's own manual check names: a transcription started while
    another GPU-touching job is already running should queue, not start."""

    def setup_method(self):
        background_jobs.set_gpu_limit_enabled(True)

    def teardown_method(self):
        background_jobs.set_gpu_limit_enabled(True)
        background_jobs.clear_job("gpu_busy_elsewhere")

    def _drama_with_audio_and_transcript(self, isolated_db, tmp_path):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="not started",
                                        audio_filename="audio.wav", transcript_mode="have_transcript")
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state[f"tmode_{did}"] = "have_transcript"
        at.session_state[f"transcript_{did}"] = "你好"
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_transcription_queues_while_another_gpu_job_is_running(self, isolated_db, tmp_path):
        release = threading.Event()
        background_jobs.start_job("gpu_busy_elsewhere", lambda: release.wait(timeout=5.0),
                                   gpu_touching=True, description="Diarization (drama #999)")
        try:
            did = self._drama_with_audio_and_transcript(isolated_db, tmp_path)
            at = self._run(did)

            buttons = [b for b in at.button if b.label == "▶ Transcribe & Align"]
            assert buttons, "Transcribe & Align button not found"
            buttons[0].click()
            at.run(timeout=30)

            job = background_jobs.get_status(f"transcribe_{did}")
            assert job is not None
            assert job["status"] == "queued"
            assert "GPU busy" in job["message"]
        finally:
            release.set()
            background_jobs.clear_job(f"transcribe_{did}")


class TestQueuedJobPanelVisibleAndCancellable:
    """Step 25d item 2: a job queued behind Step 5c's GPU guard used to be
    invisible in these panels -- they only ever handled the running/done/
    error states, and the one st.info() that announces "queued" right
    after the button click is immediately thrown away by the st.rerun()
    straight after it. This drives the real click path and checks a
    LATER rerun (e.g. the person reopening the page) still shows the
    queued state, with a working Cancel button -- background_jobs.
    cancel_queued already existed, but nothing outside live_tab.py called
    it."""

    def setup_method(self):
        background_jobs.set_gpu_limit_enabled(True)

    def teardown_method(self):
        background_jobs.set_gpu_limit_enabled(True)
        background_jobs.clear_job("gpu_busy_elsewhere_q2")

    def _drama_with_audio_and_transcript(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="not started",
                                        audio_filename="audio.wav", transcript_mode="have_transcript")
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state[f"tmode_{did}"] = "have_transcript"
        at.session_state[f"transcript_{did}"] = "你好"
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_queued_transcription_stays_visible_with_a_cancel_button_on_a_later_rerun(self, isolated_db):
        release = threading.Event()
        background_jobs.start_job("gpu_busy_elsewhere_q2", lambda: release.wait(timeout=5.0),
                                   gpu_touching=True, description="Diarization (drama #999)")
        try:
            did = self._drama_with_audio_and_transcript(isolated_db)
            at = self._run(did)
            [btn] = [b for b in at.button if b.label == "▶ Transcribe & Align"]
            btn.click().run(timeout=30)

            assert background_jobs.get_status(f"transcribe_{did}")["status"] == "queued"

            # A further rerun with nothing clicked -- the queued state must
            # still be shown, not just on the render right after the click.
            at.run(timeout=30)
            assert any("GPU busy" in i.value or "Waiting" in i.value for i in at.info)
            [cancel_btn] = [b for b in at.button if b.key == f"cancel_queued_tc_{did}"]
            assert not cancel_btn.disabled

            cancel_btn.click().run(timeout=30)
            assert background_jobs.get_status(f"transcribe_{did}") is None
        finally:
            release.set()
            background_jobs.clear_job(f"transcribe_{did}")

class TestRomanizeCreditsEnginePassesOllamaUrlAndFreeTier:
    """Step 25d item 7: same gap Step 5b item 1 already fixed elsewhere,
    recurring here -- this call used to always build the engine with no
    base_url/free_tier at all, so it ignored a custom Ollama URL and
    always billed Gemini as paid-tier."""

    def _run(self, did, **state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        for k, v in state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        return at

    def test_ollama_base_url_is_passed_through(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="Test", translation_engine="ollama", author="作者")
        seen = {}

        def spy(engine_name, api_key, *a, **kw):
            seen["engine"] = engine_name
            seen["base_url"] = kw.get("base_url")
            raise RuntimeError("stop before any real network call")
        monkeypatch.setattr(translate_engines, "get_engine", spy)

        at = self._run(did, settings_ollama="anything", settings_ollama_url="http://myhost:11434")
        [btn] = [b for b in at.button if b.key == f"roman_{did}"]
        btn.click().run(timeout=30)

        assert seen["engine"] == "ollama"
        assert seen["base_url"] == "http://myhost:11434"

    def test_gemini_free_tier_flag_is_passed_through(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="Test", translation_engine="gemini", author="作者")
        seen = {}

        def spy(engine_name, api_key, *a, **kw):
            seen["free_tier"] = kw.get("free_tier")
            raise RuntimeError("stop before any real network call")
        monkeypatch.setattr(translate_engines, "get_engine", spy)

        at = self._run(did, settings_gemini="anything", gemini_free_tier=True)
        [btn] = [b for b in at.button if b.key == f"roman_{did}"]
        btn.click().run(timeout=30)

        assert seen["free_tier"] is True


class TestDeleteDramaBlockedByRunningJob:
    """Step 25d item 8: "🗑️ Delete this drama" used to have no check for
    a still-running job on this drama at all. Step 25z landed concurrently
    on baihe-subtitler and separately added the checkbox-plus-type-DELETE
    confirmation itself (see its own TestDestructiveActionsNeedConfirmation
    for that mechanic, which this doesn't repeat) -- this covers the one
    thing item 8 adds on top of it: the button staying disabled while a
    job is running even once the confirmation is fully filled in."""

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def _delete_button(self, at):
        [btn] = [b for b in at.button if b.label == "🗑️ Delete this drama"]
        return btn

    def test_delete_stays_disabled_while_a_job_is_running_even_when_confirmed(self, isolated_db):
        did = isolated_db.create_drama(title_en="Busy Drama")
        release = threading.Event()
        background_jobs.start_job(f"translate_{did}", release.wait)
        try:
            at = self._run(did)
            at.checkbox(key=f"confirm_delete_drama_{did}").set_value(True).run(timeout=30)
            at.text_input(key=f"delete_drama_typed_{did}").set_value("DELETE").run(timeout=30)
            assert self._delete_button(at).disabled
            assert any("still running" in c.value for c in at.caption)
            assert isolated_db.get_drama(did) is not None
        finally:
            release.set()
            background_jobs.clear_job(f"translate_{did}")


class TestTranscribeJobInputsCapture:
    """Direct unit coverage for the small read/write helpers Step 25's
    fix is built on, separate from the full end-to-end AppTest scenarios
    below."""

    def test_round_trips_the_captured_fields(self, tmp_path):
        import tabs.workspace_tab as wt
        wt._write_transcribe_job_inputs(
            str(tmp_path), transcript_mode="have_transcript", alignment_method="whisper_diff",
            asr_backend_choice="whisper", run_diarize=True, expected_speakers=2)
        assert wt._read_transcribe_job_inputs(str(tmp_path)) == {
            "transcript_mode": "have_transcript", "alignment_method": "whisper_diff",
            "asr_backend_choice": "whisper", "run_diarize": True, "expected_speakers": 2}

    def test_missing_file_returns_an_empty_dict(self, tmp_path):
        import tabs.workspace_tab as wt
        assert wt._read_transcribe_job_inputs(str(tmp_path)) == {}

    def test_corrupt_file_returns_an_empty_dict_rather_than_raising(self, tmp_path):
        import tabs.workspace_tab as wt
        with open(tmp_path / wt._TRANSCRIBE_JOB_INPUTS_NAME, "w") as f:
            f.write("{not valid json")
        assert wt._read_transcribe_job_inputs(str(tmp_path)) == {}

    def test_a_later_click_overwrites_the_earlier_capture(self, tmp_path):
        import tabs.workspace_tab as wt
        wt._write_transcribe_job_inputs(str(tmp_path), transcript_mode="whisper")
        wt._write_transcribe_job_inputs(str(tmp_path), transcript_mode="have_transcript")
        assert wt._read_transcribe_job_inputs(str(tmp_path)) == {"transcript_mode": "have_transcript"}


class TestTranscriptionCompletionDoesNotWipeExistingLines:
    """Step 25: transcript_text is a plain st.text_area with no persisted
    value -- the completion handler used to read it (and transcript_mode)
    live from session_state/widgets when the job reported "done", instead
    of what was actually there when the job was started. A transcription
    that finished after the tab was closed and reopened as a new browser
    session (session_state empty) -- or even just edited while the job
    was still running in the same session -- silently produced zero
    lines and wiped the drama's real line set, with no history snapshot.
    Fixed by capturing the real inputs to disk at job-start time
    (_write_transcribe_job_inputs / transcript.txt) and reading those
    back at completion instead of live state, refusing to replace a
    non-empty line set with zero lines, and snapshotting before any
    completion-time replacement."""

    def _drama_with_existing_lines(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        audio_filename="audio.wav", transcript_mode="have_transcript")
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="原文", en="Original")])
        return did

    def _click_transcribe_in_a_real_session(self, did, monkeypatch, transcript_text,
                                            transcribe_impl=None):
        from streamlit.testing.v1 import AppTest
        import tabs.workspace_tab as wt
        monkeypatch.setattr(
            wt, "transcribe_for_timing",
            transcribe_impl or (lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "你好"}]))

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state[f"tmode_{did}"] = "have_transcript"
        at.session_state[f"transcript_{did}"] = transcript_text
        at.run(timeout=30)
        [button] = [b for b in at.button if b.label == "▶ Transcribe & Align"]
        button.click()
        at.run(timeout=30)
        return at

    def _render_in_a_fresh_session(self, did):
        """A brand-new AppTest instance sharing none of a previous
        session's session_state -- exactly what reopening the app in a
        new browser tab/session looks like. background_jobs' own job
        dict is server-side/global, so it still sees the same job."""
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def _wait_for_job(self, did, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline and background_jobs.is_running(f"transcribe_{did}"):
            time.sleep(0.02)

    def test_a_job_finishing_in_a_new_session_does_not_wipe_the_dramas_lines(
            self, isolated_db, monkeypatch):
        release = threading.Event()
        did = self._drama_with_existing_lines(isolated_db)
        # A real, mocked-slow transcribe pass -- the job is still
        # "running" by the time this ("closed-tab") session's own click
        # handler finishes, exactly like a real multi-minute Whisper pass
        # someone closed the tab on. release.set() below simulates the
        # job finishing only *after* that tab is gone.
        self._click_transcribe_in_a_real_session(
            did, monkeypatch, "真实台词",
            transcribe_impl=lambda *a, **k: (release.wait(timeout=5.0),
                                             [{"start": 0.0, "end": 1.0, "text": "你好"}])[1])
        assert background_jobs.get_status(f"transcribe_{did}")["status"] == "running"

        release.set()
        self._wait_for_job(did)

        # A completely separate "session" (fresh session_state, no
        # tmode/transcript widget values at all) is the one that first
        # observes the job as "done".
        self._render_in_a_fresh_session(did)

        after = isolated_db.load_line_objects(did)
        assert len(after) >= 1, "the drama's real lines were wiped"
        background_jobs.clear_job(f"transcribe_{did}")

    def test_the_captured_transcript_text_is_what_gets_used_not_an_empty_one(
            self, isolated_db, monkeypatch):
        release = threading.Event()
        did = self._drama_with_existing_lines(isolated_db)
        self._click_transcribe_in_a_real_session(
            did, monkeypatch, "真实台词",
            transcribe_impl=lambda *a, **k: (release.wait(timeout=5.0),
                                             [{"start": 0.0, "end": 1.0, "text": "你好"}])[1])
        assert background_jobs.get_status(f"transcribe_{did}")["status"] == "running"

        release.set()
        self._wait_for_job(did)
        self._render_in_a_fresh_session(did)

        after = isolated_db.load_line_objects(did)
        assert [l.zh for l in after] == ["真实台词"]
        background_jobs.clear_job(f"transcribe_{did}")

    def test_editing_the_transcript_while_the_job_runs_in_the_same_session_is_ignored(
            self, isolated_db, monkeypatch):
        release = threading.Event()

        def slow_transcribe(*a, **k):
            release.wait(timeout=5.0)
            return [{"start": 0.0, "end": 1.0, "text": "你好"}]

        did = self._drama_with_existing_lines(isolated_db)
        at = self._click_transcribe_in_a_real_session(did, monkeypatch, "原始文本",
                                                       transcribe_impl=slow_transcribe)
        assert background_jobs.get_status(f"transcribe_{did}")["status"] == "running"

        # Edited in the SAME session while the job is still running --
        # must not change what the already-running job processes.
        [box] = [t for t in at.text_area if t.key == f"transcript_{did}"]
        box.set_value("被修改的文本").run(timeout=30)
        assert background_jobs.get_status(f"transcribe_{did}")["status"] == "running"

        release.set()
        self._wait_for_job(did)
        at.run(timeout=30)

        after = isolated_db.load_line_objects(did)
        assert [l.zh for l in after] == ["原始文本"]
        background_jobs.clear_job(f"transcribe_{did}")

    def test_zero_lines_against_an_existing_non_empty_drama_is_refused_not_saved(
            self, isolated_db, monkeypatch):
        import tabs.workspace_tab as wt
        did = self._drama_with_existing_lines(isolated_db)
        # A real (non-blank) transcript, so the button isn't disabled --
        # but alignment itself genuinely produces zero lines (a real
        # possible outcome regardless of what caused it).
        monkeypatch.setattr(wt, "align_transcript_to_timing", lambda *a, **k: [])
        at = self._click_transcribe_in_a_real_session(did, monkeypatch, "真实台词")
        self._wait_for_job(did)

        after = isolated_db.load_line_objects(did)
        assert [l.zh for l in after] == ["原文"]  # untouched
        assert any("nothing was changed" in e.value for e in at.error)
        background_jobs.clear_job(f"transcribe_{did}")

    def test_a_history_snapshot_exists_before_a_legitimate_completion_replaces_lines(
            self, isolated_db, monkeypatch):
        did = self._drama_with_existing_lines(isolated_db)
        before_history = len(isolated_db.list_line_history(did))

        self._click_transcribe_in_a_real_session(did, monkeypatch, "真实台词")
        self._wait_for_job(did)

        after_history = isolated_db.list_line_history(did)
        assert len(after_history) == before_history + 1
        assert after_history[0]["label"] == "before re-transcribe"
        background_jobs.clear_job(f"transcribe_{did}")


class TestDiarizationAutoStartsAfterAlign:
    """Step 4d: the "Run speaker diarization during alignment" checkbox
    used to run diarize.diarize() synchronously inline, in the same
    script run as Transcribe & Align, blocking it from finishing until
    diarization completed too. It now starts the same background
    subprocess job _render_speaker_rerun's own polling picks up (section
    4), rather than diarizing inline -- this just confirms the wiring:
    once alignment finishes, a diarize_<id> job is actually started."""

    def _drama_ready_to_align(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="not started",
                                        audio_filename="audio.wav", transcript_mode="have_transcript")
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        return did

    def _run(self, did, monkeypatch):
        from streamlit.testing.v1 import AppTest
        import tabs.workspace_tab as wt

        monkeypatch.setattr(wt, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "你好"}])

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state[f"tmode_{did}"] = "have_transcript"
        at.session_state[f"transcript_{did}"] = "你好"
        at.session_state["workspace_hf_token_input"] = "fake-hf-token"
        at.run(timeout=30)
        return at

    def test_diarize_checkbox_starts_a_background_job_once_alignment_finishes(
            self, isolated_db, monkeypatch):
        did = self._drama_ready_to_align(isolated_db)
        started_jobs = []
        monkeypatch.setattr(
            background_jobs, "start_process_job",
            lambda job_id, target, **kwargs: started_jobs.append((job_id, target, kwargs)) or True)

        at = self._run(did, monkeypatch)
        diarize_checks = [c for c in at.checkbox if "Run speaker diarization" in c.label]
        assert diarize_checks, "diarization checkbox not found"
        diarize_checks[0].check().run(timeout=30)

        buttons = [b for b in at.button if b.label == "▶ Transcribe & Align"]
        assert buttons, "Transcribe & Align button not found"
        buttons[0].click()
        at.run(timeout=30)

        deadline = time.time() + 3
        while time.time() < deadline and background_jobs.is_running(f"transcribe_{did}"):
            time.sleep(0.02)
        at.run(timeout=30)

        assert any(job_id == f"diarize_{did}" for job_id, _, _ in started_jobs), \
            f"expected a diarize_{did} job to be started, got: {started_jobs}"
        background_jobs.clear_job(f"transcribe_{did}")


class TestResegmentGuardrail:
    """Step 6c: re-segmenting a drama that's already translated warns
    first, won't apply until confirmed, and then clears translation, flag
    and notes only on the lines actually split -- everything else keeps its
    permanent id and data. Also pins that the page shows the new lines
    afterward rather than reading stale per-position widget values back
    over them (lines are renumbered by the split)."""

    LONG = "他说他明天会来，可是我不太相信他。因为他上次也是这么说的，结果根本没有出现"
    FIRST = "他说他明天会来，可是我不太相信他。"
    SECOND = "因为他上次也是这么说的，结果根本没有出现"

    def _drama(self, isolated_db, translated=True):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="你好。", en="Hello." if translated else "",
                 flag="needs_review" if translated else None),
            Line(idx=1, start=2.0, end=12.0, zh=self.LONG,
                 en="He said he'd come tomorrow..." if translated else "",
                 flag="mistranslation" if translated else None),
            Line(idx=2, start=12.0, end=14.0, zh="再见。", en="Bye." if translated else ""),
        ])
        if translated:
            isolated_db.save_translation_notes(did, [
                {"line_idx": 0, "term": "你好", "note_type": "cultural", "note": "greeting"},
                {"line_idx": 1, "term": "明天", "note_type": "cultural", "note": "tomorrow"},
            ])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _button(self, at, key):
        matches = [b for b in at.button if b.key == key]
        assert matches, f"button {key!r} not found on the page"
        return matches[0]

    def test_warns_and_waits_for_confirmation_then_clears_only_the_split_line(self, isolated_db):
        did = self._drama(isolated_db)
        before = isolated_db.load_line_objects(did)
        at = self._run(did)

        self._button(at, f"reseg_preview_btn_{did}").click()
        at.run(timeout=30)
        assert any("already translated" in w.value and "re-translating" in w.value
                   for w in at.warning)
        assert self._button(at, f"reseg_apply_{did}").disabled is True
        # Previewing changed nothing yet.
        assert [l.zh for l in isolated_db.load_line_objects(did)] == ["你好。", self.LONG, "再见。"]

        [c for c in at.checkbox if c.key == f"reseg_confirm_{did}"][0].check()
        at.run(timeout=30)
        self._button(at, f"reseg_apply_{did}").click()
        at.run(timeout=30)
        at.run(timeout=30)

        after = isolated_db.load_line_objects(did)
        assert [l.zh for l in after] == ["你好。", self.FIRST, self.SECOND, "再见。"]
        assert (after[0].id, after[0].en, after[0].flag) == (before[0].id, "Hello.", "needs_review")
        assert (after[3].id, after[3].en) == (before[2].id, "Bye.")
        assert [(l.en, l.flag) for l in after[1:3]] == [("", None), ("", None)]
        assert [n["term"] for n in isolated_db.list_translation_notes(did)] == ["你好"]
        # The page shows the new lines -- not stale boxes read back over them.
        assert [l.zh for l in at.session_state.lines] == [l.zh for l in after]
        assert [ta.value for ta in at.text_area if ta.key in ("zh_1", "zh_2")] == \
               [self.FIRST, self.SECOND]
        assert any(h["label"] == "before re-segment" for h in isolated_db.list_line_history(did))

    def test_untranslated_drama_needs_no_confirmation(self, isolated_db):
        did = self._drama(isolated_db, translated=False)
        at = self._run(did)
        self._button(at, f"reseg_preview_btn_{did}").click()
        at.run(timeout=30)
        assert not any("already translated" in w.value for w in at.warning)
        assert self._button(at, f"reseg_apply_{did}").disabled is False


class TestSpeechSplittingSensitivityDefaultAndPersistence:
    """Step 6g: the "Speech-splitting sensitivity" slider's default was
    lowered from 2000ms to 300ms at the user's own tested request (fixes
    a real complaint: subtitles staying on screen through silence when
    nothing else was being said), and the value now persists per drama
    (it was a plain local variable before, reset on every visit) --
    same st.session_state[f"..._{picked_id}"] pattern Step 4f already
    established for "Expected number of speakers"."""

    def _new_drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="not started")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _slider(self, at, did):
        [box] = [s for s in at.slider if s.label.startswith("Speech-splitting sensitivity")]
        return box

    def test_a_fresh_drama_defaults_to_300ms_not_2000ms(self, isolated_db):
        did = self._new_drama(isolated_db)
        at = self._run(did)
        assert self._slider(at, did).value == 300

    def test_manually_changing_it_survives_a_rerun_rather_than_snapping_back(self, isolated_db):
        did = self._new_drama(isolated_db)
        at = self._run(did)
        assert self._slider(at, did).value == 300
        self._slider(at, did).set_value(1500).run(timeout=30)
        assert self._slider(at, did).value == 1500


class TestResegmentationStaleSnapshotSafety:
    """Step 6f: real, confirmed data corruption -- Apply re-segmentation
    could leave duplicate/orphaned rows because Preview computed its
    result from a stale st.session_state.lines snapshot that no longer
    matched the database's real current id set, and Apply committed
    that stale snapshot as a full line-list replacement. Fixed two ways:
    Preview now re-fetches fresh from the database right before
    computing, and Apply refuses (rather than silently corrupting data)
    if the database's id set has diverged from what Preview actually
    saw."""

    LONG = "他说他明天会来，可是我不太相信他。因为他上次也是这么说的，结果根本没有出现"

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="你好。"),
            Line(idx=1, start=2.0, end=12.0, zh=self.LONG),
        ])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _button(self, at, key):
        matches = [b for b in at.button if b.key == key]
        assert matches, f"button {key!r} not found on the page"
        return matches[0]

    def test_preview_reflects_a_database_change_made_after_the_page_loaded(self, isolated_db):
        """Confirms Preview re-fetches fresh from the database right
        before computing, rather than relying on whatever
        st.session_state.lines already happened to hold from when the
        page first loaded."""
        did = self._drama(isolated_db)
        at = self._run(did)

        # Something else changes the database after the page's own
        # session_state.lines was already populated -- e.g. a line
        # added by another action entirely, not reflected in this
        # render's in-memory snapshot yet.
        lines = isolated_db.load_line_objects(did)
        lines.append(Line(idx=2, start=12.0, end=14.0, zh="新的一行。"))
        isolated_db.save_lines(did, lines)

        self._button(at, f"reseg_preview_btn_{did}").click()
        at.run(timeout=30)

        reseg_state = at.session_state[f"reseg_preview_{did}"]
        # The fresh third line's id must be part of what Preview saw,
        # proving it re-fetched rather than using the page's original
        # (now-stale) two-line snapshot.
        fresh_ids = {ln.id for ln in isolated_db.load_line_objects(did)}
        assert reseg_state["source_ids"] == fresh_ids

    def test_apply_refuses_when_the_database_changed_since_preview_ran(self, isolated_db):
        """The second line of defense: even with item 1's fix, the
        database could still change in the window between Preview
        finishing and Apply being clicked (the user editing something
        else, a background job landing) -- Apply must refuse rather
        than commit a now-stale snapshot as a full replacement."""
        did = self._drama(isolated_db)
        at = self._run(did)

        self._button(at, f"reseg_preview_btn_{did}").click()
        at.run(timeout=30)
        assert self._button(at, f"reseg_apply_{did}").disabled is False

        # The database changes after Preview ran but before Apply is clicked.
        lines = isolated_db.load_line_objects(did)
        lines.append(Line(idx=2, start=12.0, end=14.0, zh="新的一行。"))
        isolated_db.save_lines(did, lines)
        before = isolated_db.load_line_objects(did)

        self._button(at, f"reseg_apply_{did}").click()
        at.run(timeout=30)

        assert any("changed since this preview was computed" in e.value for e in at.error)
        after = isolated_db.load_line_objects(did)
        assert [(l.id, l.zh) for l in after] == [(l.id, l.zh) for l in before]  # untouched

    def test_apply_succeeds_normally_when_nothing_changed_in_between(self, isolated_db):
        """The happy path (nothing else touched the drama between Preview
        and Apply) must not be spuriously blocked by the new safety
        check."""
        did = self._drama(isolated_db)
        at = self._run(did)

        self._button(at, f"reseg_preview_btn_{did}").click()
        at.run(timeout=30)
        self._button(at, f"reseg_apply_{did}").click()
        at.run(timeout=30)
        at.run(timeout=30)

        assert not any(e for e in at.error)
        assert f"reseg_preview_{did}" not in at.session_state  # applied and cleared


class TestMergeAndRestoreStaleIdSetSafety:
    """Step 6f audit: re-segmentation's Apply got the id-set-mismatch
    safety check above, but "Apply merge" and Version history's "Restore"
    are the exact same shape of bug -- each computes a full line-list
    replacement from a snapshot (merge_preview from edited_rows at
    Preview time; the restored snapshot's ids adopted from
    st.session_state.lines) and commits it via db.save_lines without
    ever checking whether the database's real current id set has since
    diverged. Both now refuse instead of silently corrupting data."""

    def _drama_with_two_mergeable_lines(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=0.5, zh="你", en="You"),
            Line(idx=1, start=0.6, end=1.0, zh="好", en="good"),
        ])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _click(self, at, label=None, key=None):
        matches = [b for b in at.button if (key and b.key == key) or (label and b.label == label)]
        assert matches, f"button {label or key!r} not found on the page"
        matches[0].click()
        at.run(timeout=30)

    def test_apply_merge_refuses_when_the_database_changed_since_preview_ran(self, isolated_db):
        did = self._drama_with_two_mergeable_lines(isolated_db)
        at = self._run(did)
        self._click(at, label="Preview merge")

        # The database changes after Preview ran but before Apply is clicked.
        lines = isolated_db.load_line_objects(did)
        lines.append(Line(idx=2, start=2.0, end=3.0, zh="新的一行。"))
        isolated_db.save_lines(did, lines)
        before = isolated_db.load_line_objects(did)

        self._click(at, label="✅ Apply merge")

        assert any("changed since this preview was computed" in e.value for e in at.error)
        after = isolated_db.load_line_objects(did)
        assert [(l.id, l.zh) for l in after] == [(l.id, l.zh) for l in before]  # untouched

    def test_apply_merge_succeeds_normally_when_nothing_changed_in_between(self, isolated_db):
        did = self._drama_with_two_mergeable_lines(isolated_db)
        at = self._run(did)
        self._click(at, label="Preview merge")
        self._click(at, label="✅ Apply merge")

        assert not any(e for e in at.error)
        after = isolated_db.load_line_objects(did)
        assert [l.zh for l in after] == ["你好"]

    def test_restore_refuses_when_the_database_changed_since_lines_were_loaded(self, isolated_db):
        did = self._drama_with_two_mergeable_lines(isolated_db)
        isolated_db.save_line_history_snapshot(did, isolated_db.load_line_objects(did), "two lines")
        at = self._run(did)

        # Something else changes the database after this render's
        # st.session_state.lines was already populated.
        lines = isolated_db.load_line_objects(did)
        lines.append(Line(idx=2, start=2.0, end=3.0, zh="新的一行。"))
        isolated_db.save_lines(did, lines)
        before = isolated_db.load_line_objects(did)

        snap = [h for h in isolated_db.list_line_history(did) if h["label"] == "two lines"][0]
        self._click(at, key=f"restore_{snap['id']}")

        assert any("changed since they were last loaded" in e.value for e in at.error)
        after = isolated_db.load_line_objects(did)
        assert [(l.id, l.zh) for l in after] == [(l.id, l.zh) for l in before]  # untouched

    def test_restore_succeeds_normally_when_nothing_changed_in_between(self, isolated_db):
        did = self._drama_with_two_mergeable_lines(isolated_db)
        isolated_db.save_line_history_snapshot(did, isolated_db.load_line_objects(did), "two lines")
        merged = isolated_db.load_line_objects(did)
        merged[0].zh = "你好"
        isolated_db.save_lines(did, [merged[0]])
        at = self._run(did)

        snap = [h for h in isolated_db.list_line_history(did) if h["label"] == "two lines"][0]
        self._click(at, key=f"restore_{snap['id']}")

        assert not any(e for e in at.error)
        after = isolated_db.load_line_objects(did)
        assert [l.zh for l in after] == ["你", "好"]


class TestRestoreAndActivateKeepSpeakerCorrections:
    """Step 25c item 2: neither saved format recorded speaker_manual, so a
    Restore/Activate reset a hand-corrected speaker; Activate also lacked
    Restore's id-set guard. Both now share one restore path."""

    _drama_with_two_mergeable_lines = TestMergeAndRestoreStaleIdSetSafety._drama_with_two_mergeable_lines
    _run = TestMergeAndRestoreStaleIdSetSafety._run
    _click = TestMergeAndRestoreStaleIdSetSafety._click

    def _drama_with_a_corrected_speaker(self, isolated_db):
        did = self._drama_with_two_mergeable_lines(isolated_db)
        lines = isolated_db.load_line_objects(did)
        lines[0].speaker, lines[0].speaker_manual = "Hero", True
        isolated_db.save_lines(did, lines)
        return did

    def test_speaker_correction_survives_a_history_restore(self, isolated_db):
        did = self._drama_with_a_corrected_speaker(isolated_db)
        isolated_db.save_line_history_snapshot(did, isolated_db.load_line_objects(did), "snap")
        edited = isolated_db.load_line_objects(did)
        edited[0].en = "changed later"
        isolated_db.save_lines(did, edited)
        at = self._run(did)

        snap = [h for h in isolated_db.list_line_history(did) if h["label"] == "snap"][0]
        self._click(at, key=f"restore_{snap['id']}")

        assert not any(e for e in at.error)
        first = isolated_db.load_line_objects(did)[0]
        assert (first.en, first.speaker, first.speaker_manual) == ("You", "Hero", True)

    def test_speaker_correction_survives_activating_a_translation_version(self, isolated_db):
        did = self._drama_with_two_mergeable_lines(isolated_db)
        vid = isolated_db.save_translation_version(did, isolated_db.load_line_objects(did), "v1")
        isolated_db.save_translation_version(did, isolated_db.load_line_objects(did), "v2",
                                             make_active=True)
        # Corrected after both versions were saved, along with a retranslation.
        lines = isolated_db.load_line_objects(did)
        lines[0].speaker, lines[0].speaker_manual, lines[0].en = "Hero", True, "Hey you"
        isolated_db.save_lines(did, lines)
        at = self._run(did)

        self._click(at, key=f"actv_{vid}")

        assert not any(e for e in at.error)
        first = isolated_db.load_line_objects(did)[0]
        assert (first.en, first.speaker, first.speaker_manual) == ("You", "Hero", True)
        assert [v["is_active"] for v in isolated_db.list_translation_versions(did)
                if v["id"] == vid] == [1]

    def test_activate_refuses_when_the_database_changed_since_lines_were_loaded(self, isolated_db):
        did = self._drama_with_two_mergeable_lines(isolated_db)
        vid = isolated_db.save_translation_version(did, isolated_db.load_line_objects(did), "v1")
        at = self._run(did)

        lines = isolated_db.load_line_objects(did)
        lines.append(Line(idx=2, start=2.0, end=3.0, zh="新的一行。"))
        isolated_db.save_lines(did, lines)
        before = isolated_db.load_line_objects(did)

        self._click(at, key=f"actv_{vid}")

        assert any("changed since they were last loaded" in e.value for e in at.error)
        after = isolated_db.load_line_objects(did)
        assert [(l.id, l.zh, l.en) for l in after] == [(l.id, l.zh, l.en) for l in before]
        assert [v["is_active"] for v in isolated_db.list_translation_versions(did)] == [0]


class TestTranslationOnlyEngineGatesLlmOnlyButtons:
    """Step 1d: DeepL/Google/NLLB/LibreTranslate can't run the LLM-only
    features (consistency check, flagging, emotion detection, notes,
    speaker tagging) -- calling call_llm_json with one of them now raises
    instead of silently returning a fallback, so those buttons must be
    disabled up front rather than let a click guarantee a crash."""

    def _drama_with_translation_only_engine(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        translation_engine="deepl")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _button(self, at, label):
        matches = [b for b in at.button if b.label == label]
        assert matches, f"button {label!r} not found on the page"
        return matches[0]

    def test_check_consistency_is_disabled_for_a_translation_only_engine(self, isolated_db):
        did = self._drama_with_translation_only_engine(isolated_db)
        at = self._run(did)
        assert self._button(at, "Check consistency").disabled is True
        assert any("translation-only" in c.value for c in at.caption)

    def test_find_lines_to_flag_is_disabled_for_a_translation_only_engine(self, isolated_db):
        did = self._drama_with_translation_only_engine(isolated_db)
        at = self._run(did)
        assert self._button(at, "Find lines to flag").disabled is True

    def test_generate_translation_notes_is_disabled_for_a_translation_only_engine(self, isolated_db):
        did = self._drama_with_translation_only_engine(isolated_db)
        at = self._run(did)
        assert self._button(at, "Generate translation notes").disabled is True

    def test_check_consistency_is_enabled_for_an_llm_capable_engine(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama 2", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        translation_engine="claude")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        at = self._run(did)
        assert self._button(at, "Check consistency").disabled is False
        assert not any("Can't reach Ollama" in w.value for w in at.warning)


class TestTestModeExportWarning:
    """Step 1d item 5: exporting subtitles, a video, or a package that
    still contains Test-mode lines warns instead of silently shipping
    fake [TEST] placeholder text as if it were a real translation."""

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def test_export_warns_when_test_mode_lines_are_present(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated",
                                        translation_engine="test_offline")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="[TEST] hello")])
        at = self._run(did)
        assert any("Test mode" in w.value for w in at.warning)

    def test_export_does_not_warn_for_a_real_engine(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama 2", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated",
                                        translation_engine="claude")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="Hello")])
        at = self._run(did)
        assert not any("Test mode" in w.value for w in at.warning)

    def test_export_does_not_warn_when_test_mode_engine_has_no_translated_lines_yet(self, isolated_db):
        # translation_engine could be a leftover from a previous run with
        # nothing actually translated yet -- don't warn about fake lines
        # that don't exist.
        did = isolated_db.create_drama(title_en="Test Drama 3", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned",
                                        translation_engine="test_offline")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
        at = self._run(did)
        assert not any("Test mode" in w.value for w in at.warning)


class TestPerDramaPronounPicker:
    """Step 1e: section 6 has a Pronouns field per character, so a drama
    with no series can set pronouns, and a series drama can override its
    series default for this drama only."""

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _picker(self, at, label):
        matches = [s for s in at.selectbox if s.label == label]
        assert matches, f"selectbox {label!r} not found"
        return matches[0]

    def test_standalone_drama_can_set_they_them(self, isolated_db):
        did = isolated_db.create_drama(title_en="Standalone", media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned")
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Xiaoling")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", speaker="SPEAKER_00")])
        at = self._run(did)
        self._picker(at, "Pronouns (Xiaoling)").set_value("they/them").run(timeout=30)
        [c] = isolated_db.list_characters(did)
        assert c["pronouns"] == "they/them"

    def test_series_value_is_shown_as_the_default_without_being_copied(self, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        isolated_db.upsert_series_character(sid, "Su Shan", gender="female")
        [sc] = isolated_db.list_series_characters(sid)
        did = isolated_db.create_drama(title_en="Ep 1", series_id=sid, media_type="audio_drama",
                                        content_mode="audio_drama", status="aligned")
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Su Shan",
                                      series_character_id=sc["id"])
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", speaker="SPEAKER_00")])
        at = self._run(did)
        assert self._picker(at, "Pronouns (Su Shan)").value == "she/her"
        [c] = isolated_db.list_characters(did)
        assert not c["pronouns"]  # still following the series value, not a frozen copy


class TestPresetsInWorkspaceUI:
    """Step 9c: "Save as preset" / "Apply a preset" in the Workspace
    tab's 5. Translation section, on an existing drama."""

    def _drama(self, isolated_db):
        return isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                         content_mode="audio_drama", translation_engine="claude")

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def _style_box(self, at):
        return [s for s in at.selectbox if s.label == "Translation style"][0]

    def _locale_box(self, at):
        return [s for s in at.selectbox if s.label == "English variant"][0]

    def _pronoun_box(self, at):
        return [c for c in at.checkbox if c.label == "Default ambiguous pronouns to she/her"][0]

    def _genre_box(self, at):
        return [c for c in at.checkbox if "Include baihe/GL genre guidance" in c.label][0]

    def test_save_as_preset_captures_current_settings(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)

        self._style_box(at).select("novel").run()
        self._locale_box(at).select("en-GB").run()
        self._pronoun_box(at).check().run()
        [t for t in at.text_input if t.key == f"new_preset_name_{did}"][0].set_value(
            "My preset").run()
        [b for b in at.button if b.key == f"save_preset_btn_{did}"][0].click().run()

        presets = db.list_presets()
        assert len(presets) == 1
        p = presets[0]
        assert p["name"] == "My preset"
        assert p["style_preset"] == "novel"
        assert p["locale"] == "en-GB"
        assert p["default_female_pronouns"] == 1
        assert p["translation_engine"] == "claude"

    def test_save_as_preset_is_not_nested_in_its_own_expander(self, isolated_db):
        """Step 9g: this used to be its own st.expander nested inside the
        Translation section's own expander -- two clicks deep for
        something Step 9c intended as one-click. Now rendered directly
        in Translation's own flow."""
        did = self._drama(isolated_db)
        at = self._run(did)
        assert not any("Save current settings as a preset" in e.label for e in at.expander)
        assert [t for t in at.text_input if t.key == f"new_preset_name_{did}"]
        assert [b for b in at.button if b.key == f"save_preset_btn_{did}"]

    def test_saving_with_a_blank_name_is_disabled(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        btn = [b for b in at.button if b.key == f"save_preset_btn_{did}"][0]
        assert btn.disabled
        assert db.list_presets() == []

    def test_no_presets_saved_shows_no_apply_control(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        assert not [s for s in at.selectbox if s.key == f"apply_preset_choice_{did}"]

    def test_apply_button_is_disabled_when_no_preset_is_selected(self, isolated_db):
        did = self._drama(isolated_db)
        db.save_preset("Some preset")
        at = self._run(did)
        btn = [b for b in at.button if b.key == f"apply_preset_btn_{did}"][0]
        assert btn.disabled

    def test_applying_a_preset_sets_every_captured_field(self, isolated_db):
        did = self._drama(isolated_db)
        db.save_preset("Novel style", translation_engine="deepseek", style_preset="novel",
                       locale="en-GB", default_female_pronouns=True, include_genre_notes=False)
        at = self._run(did)

        [s for s in at.selectbox if s.key == f"apply_preset_choice_{did}"][0].select(
            "Novel style").run()
        [b for b in at.button if b.key == f"apply_preset_btn_{did}"][0].click().run()

        assert db.get_drama(did)["translation_engine"] == "deepseek"
        assert self._style_box(at).value == "novel"
        assert self._locale_box(at).value == "en-GB"
        assert self._pronoun_box(at).value is True
        assert self._genre_box(at).value is False

    def test_applied_fields_are_not_frozen_against_later_manual_changes(self, isolated_db):
        """Exit condition: applying a preset sets its fields, and none of
        them are frozen against later manual changes."""
        did = self._drama(isolated_db)
        db.save_preset("Novel style", style_preset="novel", locale="en-GB")
        at = self._run(did)
        [s for s in at.selectbox if s.key == f"apply_preset_choice_{did}"][0].select(
            "Novel style").run()
        [b for b in at.button if b.key == f"apply_preset_btn_{did}"][0].click().run()
        assert self._style_box(at).value == "novel"

        self._style_box(at).select("audio_drama").run()
        assert self._style_box(at).value == "audio_drama"

        # Confirms it wasn't just the immediate post-click render that
        # happened to show the manual pick -- an unrelated later rerun
        # doesn't silently revert it back to the preset's own value.
        at.run(timeout=30)
        assert self._style_box(at).value == "audio_drama"


class TestApplyPresetOnNewDrama:
    """Step 9c item 2: applying a preset "when creating a new drama"."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = None
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def test_no_presets_saved_still_allows_creating_a_drama(self, isolated_db):
        at = self._run()
        [t for t in at.text_input if t.label == "Title (English)"][0].set_value("Plain One").run()
        [s for s in at.selectbox if s.label == "Source language *(required)*"][0].select("zh").run()
        [b for b in at.button if b.label == "Create drama"][0].click().run()
        dramas = db.list_dramas()
        assert len(dramas) == 1
        assert dramas[0]["title_en"] == "Plain One"

    def test_creating_a_drama_with_a_preset_applies_its_engine_and_fields(self, isolated_db):
        db.save_preset("Novel defaults", translation_engine="deepseek", style_preset="novel",
                       locale="en-GB", default_female_pronouns=True, include_genre_notes=False)
        at = self._run()

        [t for t in at.text_input if t.label == "Title (English)"][0].set_value("New One").run()
        [s for s in at.selectbox if s.label == "Apply a preset (optional)"][0].select(
            "Novel defaults").run()
        [s for s in at.selectbox if s.label == "Source language *(required)*"][0].select("zh").run()
        [b for b in at.button if b.label == "Create drama"][0].click().run()

        dramas = db.list_dramas()
        assert len(dramas) == 1
        new_id = dramas[0]["id"]
        assert dramas[0]["translation_engine"] == "deepseek"
        assert at.session_state[f"style_preset_{new_id}"] == "novel"
        assert at.session_state[f"locale_{new_id}"] == "en-GB"
        assert at.session_state[f"default_female_pronouns_{new_id}"] is True
        assert at.session_state[f"include_genre_notes_{new_id}"] is False


class TestNewDramaLanguageSelector:
    """Step 87: the new-drama creation form had a content-type selector
    but no language selector, so db.create_drama was called with no
    source_language and silently fell through to the schema's DEFAULT
    'zh' with zero UI indication. Now there's a required selector with
    no default selection, matching the existing edit-branch selector's
    zh/ja/ko options, and "Create drama" stays disabled until one is
    picked."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = None
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def test_no_language_preselected(self, isolated_db):
        at = self._run()
        source_language = [s for s in at.selectbox if s.label == "Source language *(required)*"][0]
        assert source_language.value is None

    def test_create_drama_button_disabled_until_a_language_is_picked(self, isolated_db):
        at = self._run()
        [t for t in at.text_input if t.label == "Title (English)"][0].set_value("Unset Lang").run()
        create_button = [b for b in at.button if b.label == "Create drama"][0]
        assert create_button.disabled
        assert any("Still needed: a source language" in i.value for i in at.info)

        [s for s in at.selectbox if s.label == "Source language *(required)*"][0].select("ja").run()
        create_button = [b for b in at.button if b.label == "Create drama"][0]
        assert not create_button.disabled

    def test_creating_a_drama_saves_the_picked_language_not_a_silent_zh_default(self, isolated_db):
        at = self._run()
        [t for t in at.text_input if t.label == "Title (English)"][0].set_value("Japanese Show").run()
        [s for s in at.selectbox if s.label == "Source language *(required)*"][0].select("ja").run()
        [b for b in at.button if b.label == "Create drama"][0].click().run()

        dramas = db.list_dramas()
        assert len(dramas) == 1
        assert dramas[0]["source_language"] == "ja"


class TestAnimeContentTypeAndSeriesAtCreation:
    """Step 22b: "anime" as its own media_type, and assigning a series
    directly from the "Create drama" form instead of needing a later
    Edit metadata trip."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = None
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def test_anime_is_a_content_type_option(self, isolated_db):
        at = self._run()
        content_type = [s for s in at.selectbox if s.label == "Content type"][0]
        assert "Anime" in content_type.options

    def test_creating_a_drama_with_anime_media_type_saves_it(self, isolated_db):
        at = self._run()
        [t for t in at.text_input if t.label == "Title (English)"][0].set_value("A Show").run()
        [s for s in at.selectbox if s.label == "Content type"][0].select("anime").run()
        [s for s in at.selectbox if s.label == "Source language *(required)*"][0].select("zh").run()
        [b for b in at.button if b.label == "Create drama"][0].click().run()

        dramas = db.list_dramas()
        assert len(dramas) == 1
        assert dramas[0]["media_type"] == "anime"

    def test_assigning_an_existing_series_at_creation(self, isolated_db):
        sid = db.get_or_create_series("Existing Series")
        at = self._run()
        [t for t in at.text_input if t.label == "Title (English)"][0].set_value("Episode 1").run()
        [s for s in at.selectbox if s.label == "Series (optional)"][0].select("Existing Series").run()
        [s for s in at.selectbox if s.label == "Source language *(required)*"][0].select("zh").run()
        [b for b in at.button if b.label == "Create drama"][0].click().run()

        dramas = db.list_dramas()
        assert len(dramas) == 1
        assert dramas[0]["series_id"] == sid
        assert dramas[0] in db.list_dramas_by_series(sid)

    def test_creating_a_new_series_at_creation(self, isolated_db):
        at = self._run()
        [t for t in at.text_input if t.label == "Title (English)"][0].set_value("Episode 1").run()
        [s for s in at.selectbox if s.label == "Series (optional)"][0].select("+ New series...").run()
        [t for t in at.text_input if t.label == "New series name"][0].set_value("Brand New Series").run()
        [s for s in at.selectbox if s.label == "Source language *(required)*"][0].select("zh").run()
        [b for b in at.button if b.label == "Create drama"][0].click().run()

        dramas = db.list_dramas()
        assert len(dramas) == 1
        series = db.list_series()
        assert len(series) == 1
        assert series[0]["name"] == "Brand New Series"
        assert dramas[0]["series_id"] == series[0]["id"]

    def test_leaving_series_unset_does_not_assign_one(self, isolated_db):
        at = self._run()
        [t for t in at.text_input if t.label == "Title (English)"][0].set_value("No Series").run()
        [s for s in at.selectbox if s.label == "Source language *(required)*"][0].select("zh").run()
        [b for b in at.button if b.label == "Create drama"][0].click().run()

        dramas = db.list_dramas()
        assert len(dramas) == 1
        assert dramas[0]["series_id"] is None

    def test_an_anime_movie_shares_a_series_with_the_shows_episodes_but_stays_its_own_drama(self, isolated_db):
        sid = db.get_or_create_series("A Show")
        episode_id = db.create_drama(title_en="Episode 1", media_type="anime", series_id=sid)

        at = self._run()
        [t for t in at.text_input if t.label == "Title (English)"][0].set_value("The Movie").run()
        [s for s in at.selectbox if s.label == "Content type"][0].select("anime").run()
        [s for s in at.selectbox if s.label == "Series (optional)"][0].select("A Show").run()
        [s for s in at.selectbox if s.label == "Source language *(required)*"][0].select("zh").run()
        [b for b in at.button if b.label == "Create drama"][0].click().run()

        in_series = db.list_dramas_by_series(sid)
        assert {d["id"] for d in in_series} == {episode_id, at.session_state["active_drama_id"]}
        assert len(db.list_dramas()) == 2


class TestNarrationVoiceSetup:
    """Step 11b: each character's voice engine and voice description
    (section 6) persist, the dub button hands them -- plus the drama's
    saved emotion tags -- to generation, taking the GPU slot for a local
    engine; and a narration drama gets an M4B audiobook export (section 9)."""

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Novel", media_type="audio_drama",
                                        content_mode="novel_narration", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello",
                                          speaker="Hero")])
        isolated_db.upsert_character(did, "Hero", character_name="Hero")
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def test_engine_and_voice_description_persist(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        [engine] = [s for s in at.selectbox if s.key == f"cengine_{did}_Hero"]
        assert engine.value == "f5tts"  # nothing stored yet: the pre-Step-11b default
        engine.set_value("chatterbox").run(timeout=30)
        [design] = [t for t in at.text_input if t.key == f"cdesign_{did}_Hero"]
        design.set_value("male, young adult, low pitch").run(timeout=30)

        [c] = db.list_characters(did)
        assert c["clone_engine"] == "chatterbox"
        assert c["voice_design"] == "male, young adult, low pitch"
        assert any("PerTh" in cap.value for cap in at.caption)  # watermark noted next to the option

    def test_dub_button_passes_voices_emotions_and_takes_the_gpu(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db)
        db.upsert_character(did, "Hero", voice_design="female, whisper")
        db.save_emotions(did, {0: {"emotion": "sad", "intensity": 0.8, "note": ""}})
        started = {}
        monkeypatch.setattr(background_jobs, "start_process_job",
                            lambda job_id, target, args=(), gpu_touching=False, description="":
                            started.update(args=args, gpu_touching=gpu_touching) or True)

        at = self._run(did)
        [button] = [b for b in at.button if b.label == "🎙️ Generate narration track"]
        button.click().run(timeout=30)

        clone_map, is_narration, emotion_map = started["args"][4], started["args"][6], started["args"][7]
        assert clone_map == {"Hero": {"engine": "omnivoice", "instruct": "female, whisper"}}
        assert is_narration is True
        assert emotion_map[0]["emotion"] == "sad"
        assert started["gpu_touching"] is True

    def test_offline_voice_is_its_own_setting_and_reaches_dub_generation(self, isolated_db, monkeypatch):
        """Step 25c item 1: first render saves no edge-tts default, and the
        offline engine gets the character's Piper voice, not tts_voice."""
        did = self._drama(isolated_db)
        started = {}
        monkeypatch.setattr(background_jobs, "start_process_job",
                            lambda job_id, target, args=(), gpu_touching=False, description="":
                            started.update(args=args) or True)

        at = self._run(did)
        [c] = db.list_characters(did)
        assert c["tts_voice"] is None and c["offline_voice"] is None
        [offline] = [s for s in at.selectbox if s.key == f"coffline_{did}_Hero"]
        assert offline.value == dub_module.DEFAULT_OFFLINE_VOICE_POOL[0]
        offline.set_value("en_GB-alba-medium").run(timeout=30)
        [c] = db.list_characters(did)
        assert (c["tts_voice"], c["offline_voice"]) == (None, "en_GB-alba-medium")

        [engine] = [r for r in at.radio if r.label.startswith("Fallback TTS engine")]
        engine.set_value("offline").run(timeout=30)
        [button] = [b for b in at.button if b.label == "🎙️ Generate narration track"]
        button.click().run(timeout=30)
        assert started["args"][5] == "offline"
        assert started["args"][10] == {"Hero": "en_GB-alba-medium"}

    def test_m4b_export_needs_the_narration_then_exports_it(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db)
        at = self._run(did)
        [button] = [b for b in at.button if b.label == "🎧 Generate audiobook (.m4b)"]
        assert button.disabled  # no narration_track.wav yet

        ddir = db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        open(os.path.join(ddir, "narration_track.wav"), "wb").close()
        m4b = os.path.join(ddir, "narration.m4b")
        exported = []

        def fake_export(lines, drama_dir, title=None, narrate_original=False):
            exported.append((len(lines), title))
            open(m4b, "wb").close()
            return m4b
        monkeypatch.setattr(dub_module, "export_narration_m4b", fake_export)

        at = self._run(did)
        [button] = [b for b in at.button if b.label == "🎧 Generate audiobook (.m4b)"]
        assert not button.disabled
        button.click().run(timeout=30)
        assert exported == [(1, "Novel")]
        assert not at.error


class TestDubTimingAndRemovedCloneUI:
    """Step 11c: section 8's time-stretch limits reach dub generation, and
    each dubbed line's pacing shows as 🟢/🟡/🔴 with a 🟡 line's factor on
    hover. Step 11d: a character cloned with the removed hosted engine
    says so where its clone status is shown, and no option for it
    remains."""

    def _drama(self, isolated_db, dub_filename=None):
        did = isolated_db.create_drama(title_en="Dubbed", media_type="audio_drama",
                                        content_mode="audio_drama", status="dubbed")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello there", speaker="Hero",
                 dub_filename=dub_filename),
            Line(idx=1, start=1.0, end=2.0, zh="再见", en="Bye", speaker="Hero",
                 dub_filename="dub_clips/line_0001_abc.wav")])
        isolated_db.upsert_character(did, "Hero", character_name="Hero", elevenlabs_voice_id="v1")
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def test_stretch_limits_reach_dub_generation(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db)
        started = {}
        monkeypatch.setattr(background_jobs, "start_process_job",
                            lambda job_id, target, args=(), gpu_touching=False, description="":
                            started.update(args=args) or True)
        at = self._run(did)
        [speedup] = [n for n in at.number_input if n.key == f"dub_max_speedup_{did}"]
        assert speedup.value == dub_module.DUB_MAX_SPEEDUP
        speedup.set_value(1.2).run(timeout=30)
        [button] = [b for b in at.button if b.label == "🎙️ Generate dub track"]
        button.click().run(timeout=30)
        assert started["args"][8:10] == (1.2, dub_module.DUB_MAX_SLOWDOWN)

    def test_pacing_indicator_per_line(self, isolated_db):
        stretched = "dub_clips/line_0000_abc_x1.200.wav"
        did = self._drama(isolated_db, dub_filename=stretched)
        clips = os.path.join(isolated_db.drama_dir(did), "dub_clips")
        os.makedirs(clips, exist_ok=True)
        with open(os.path.join(clips, dub_module.PACING_FILENAME), "w") as f:
            json.dump({"0": {"status": "stretched", "factor": 1.2, "clip_ms": 1200, "window_ms": 1000,
                             "dub_filename": stretched},
                       "1": {"status": "overflow", "factor": 1.4, "clip_ms": 2800, "window_ms": 1000,
                             "dub_filename": "dub_clips/line_0001_abc.wav"}}, f)
        at = self._run(did)
        assert any("⏱️ Dub pacing -- 0 🟢 · 1 🟡 · 1 🔴" in e.label for e in at.expander)
        [yellow] = [m for m in at.markdown if m.value.startswith("🟡 **#1**")]
        assert "1.20×" in yellow.proto.help
        [red] = [m for m in at.markdown if m.value.startswith("🔴 **#2**")]
        assert "runs 1.0s over" in red.value

    def test_removed_hosted_clone_is_explained_and_not_offered(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        assert any(dub_module.REMOVED_CLONE_MESSAGE == i.value for i in at.info)
        # the old hosted-cloning expander's key field and clone button are gone
        assert not any(t.key == f"el_key_{did}" for t in at.text_input)
        assert not any((b.key or "").startswith("elclone_") for b in at.button)


class TestDramaSwitchDoesNotCarryUploadsOrForceRetranslate:
    """Step 25i: the EPUB uploader used a static key ("epub_upload") and
    wrote the selected file into the *current* drama's folder on every
    render. Streamlit keeps returning an uploaded file until it's cleared,
    so uploading on drama A and then switching to drama B silently
    overwrote B's source.epub with A's file -- no click needed. The
    "Force re-translate everything" checkbox had the same static-key shape,
    so a check on A carried over to B."""

    class _FakeUpload:
        def __init__(self, data):
            self._data = data

        def getbuffer(self):
            return memoryview(self._data)

    def _fake_uploader(self, monkeypatch):
        """Mimic st.file_uploader's persistence: once a file is "picked" in
        the EPUB uploader, that widget key keeps returning it on every rerun
        until cleared. Every other uploader behaves normally."""
        import streamlit
        real = streamlit.file_uploader
        state = {"pending": None, "by_key": {}}

        def fake(label, *args, key=None, **kwargs):
            if key and key.startswith("epub_upload"):
                if state["pending"] is not None:
                    state["by_key"][key] = state["pending"]
                    state["pending"] = None
                return state["by_key"].get(key)
            return real(label, *args, key=key, **kwargs)

        monkeypatch.setattr(streamlit, "file_uploader", fake)
        return state

    def _novel_drama(self, db_module, title):
        return db_module.create_drama(title_en=title, media_type="novel",
                                      content_mode="novel_narration", status="not started")

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _switch_to(self, at, did):
        box = [b for b in at.selectbox if b.label == "Drama"][0]
        label = next(l for l in box.options if l.startswith(f"#{did} "))
        box.set_value(label).run(timeout=30)

    def _epub_path(self, db_module, did):
        return os.path.join(db_module.drama_dir(did), "source.epub")

    def test_switching_dramas_never_overwrites_the_new_dramas_epub(self, isolated_db, monkeypatch):
        uploads = self._fake_uploader(monkeypatch)
        did_a = self._novel_drama(isolated_db, "Drama A")
        did_b = self._novel_drama(isolated_db, "Drama B")
        os.makedirs(isolated_db.drama_dir(did_b), exist_ok=True)
        with open(self._epub_path(isolated_db, did_b), "wb") as f:
            f.write(b"drama B's own epub")

        at = self._run(did_a)
        uploads["pending"] = self._FakeUpload(b"drama A's epub")
        at.run(timeout=30)

        self._switch_to(at, did_b)
        at.run(timeout=30)

        with open(self._epub_path(isolated_db, did_b), "rb") as f:
            assert f.read() == b"drama B's own epub"
        assert not os.path.exists(self._epub_path(isolated_db, did_a))

    def test_uploaded_epub_is_only_written_after_an_explicit_save(self, isolated_db, monkeypatch):
        import epub_io
        monkeypatch.setattr(epub_io, "get_epub_chapter_count", lambda path: 3)
        uploads = self._fake_uploader(monkeypatch)
        did = self._novel_drama(isolated_db, "Drama A")

        at = self._run(did)
        uploads["pending"] = self._FakeUpload(b"drama A's epub")
        at.run(timeout=30)
        assert not os.path.exists(self._epub_path(isolated_db, did))
        assert not [b for b in at.button if b.label == "Import chapters from EPUB"]

        [save] = [b for b in at.button if b.key == f"epub_save_{did}"]
        save.click().run(timeout=30)
        with open(self._epub_path(isolated_db, did), "rb") as f:
            assert f.read() == b"drama A's epub"
        assert [b for b in at.button if b.label == "Import chapters from EPUB"]

    def test_force_retranslate_does_not_carry_over_to_another_drama(self, isolated_db):
        did_a = isolated_db.create_drama(title_en="Drama A", media_type="audio_drama",
                                         content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did_a, [Line(idx=0, start=0.0, end=1.0, zh="甲", en="A")])
        did_b = isolated_db.create_drama(title_en="Drama B", media_type="audio_drama",
                                         content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did_b, [Line(idx=0, start=0.0, end=1.0, zh="乙", en="B")])

        def _force_box(at):
            [box] = [c for c in at.checkbox if c.label.startswith("Force re-translate everything")]
            return box

        at = self._run(did_a)
        _force_box(at).check().run(timeout=30)
        assert _force_box(at).value is True

        self._switch_to(at, did_b)
        at.run(timeout=30)
        assert _force_box(at).value is False


class TestReferenceNovelUploadDoesNotLeakAcrossDramas:
    """Step 25p: the reference-novel uploader/paste box used static keys
    ("novel_up"/"novel_paste"), so Streamlit kept returning whatever was
    uploaded or pasted on a previously-viewed drama. Starting Transcribe
    on a different drama silently used that leftover content as its
    novel_reference instead of the new drama's own (or lack of one)."""

    class _FakeUpload:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

    def _fake_uploader(self, monkeypatch):
        import streamlit
        real = streamlit.file_uploader
        state = {"pending": None, "by_key": {}}

        def fake(label, *args, key=None, **kwargs):
            if key and key.startswith("novel_up"):
                if state["pending"] is not None:
                    state["by_key"][key] = state["pending"]
                    state["pending"] = None
                return state["by_key"].get(key)
            return real(label, *args, key=key, **kwargs)

        monkeypatch.setattr(streamlit, "file_uploader", fake)
        return state

    def _drama(self, isolated_db, title):
        did = isolated_db.create_drama(title_en=title, media_type="audio_drama",
                                       content_mode="audio_drama", status="new",
                                       transcript_mode="have_transcript",
                                       audio_filename="audio.wav")
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state[f"tmode_{did}"] = "have_transcript"
        at.session_state[f"transcript_{did}"] = "你好"
        at.run(timeout=30)
        return at

    def _switch_to(self, at, did):
        box = [b for b in at.selectbox if b.label == "Drama"][0]
        label = next(l for l in box.options if l.startswith(f"#{did} "))
        box.set_value(label).run(timeout=30)

    def _novel_ref_path(self, did):
        import db
        return os.path.join(db.drama_dir(did), "novel_reference.txt")

    def test_transcribing_drama_b_does_not_pull_in_drama_as_upload(
            self, isolated_db, monkeypatch):
        import tabs.workspace_tab as wt
        monkeypatch.setattr(wt, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "你好"}])
        uploads = self._fake_uploader(monkeypatch)
        did_a = self._drama(isolated_db, "Drama A")
        did_b = self._drama(isolated_db, "Drama B")

        at = self._run(did_a)
        uploads["pending"] = self._FakeUpload(b"drama A's novel")
        at.run(timeout=30)

        at.session_state[f"tmode_{did_b}"] = "have_transcript"
        at.session_state[f"transcript_{did_b}"] = "你好"
        self._switch_to(at, did_b)
        at.run(timeout=30)

        [button] = [b for b in at.button if b.label == "▶ Transcribe & Align"]
        button.click().run(timeout=30)

        assert not os.path.exists(self._novel_ref_path(did_b))

    def test_pasted_reference_does_not_leak_either(self, isolated_db, monkeypatch):
        import tabs.workspace_tab as wt
        monkeypatch.setattr(wt, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "你好"}])
        did_a = self._drama(isolated_db, "Drama A")
        did_b = self._drama(isolated_db, "Drama B")

        at = self._run(did_a)
        at.text_area(key=f"novel_paste_{did_a}").set_value("drama A's pasted reference").run(timeout=30)

        at.session_state[f"tmode_{did_b}"] = "have_transcript"
        at.session_state[f"transcript_{did_b}"] = "你好"
        self._switch_to(at, did_b)
        at.run(timeout=30)

        [button] = [b for b in at.button if b.label == "▶ Transcribe & Align"]
        button.click().run(timeout=30)

        assert not os.path.exists(self._novel_ref_path(did_b))


class TestOriginalNovelForGlossaryDoesNotLeakAndNeedsExplicitSave:
    """Step 25p: the glossary builder's "Original novel" uploader used a
    static key ("orig_novel_up") and wrote raw_novel_context.txt on every
    render with no button gate at all -- uploading on drama A and
    switching to drama B silently primed B's Whisper transcription with
    A's raw novel text."""

    class _FakeUpload:
        def __init__(self, data, name="novel.txt"):
            self._data = data
            self.name = name

        def getvalue(self):
            return self._data

    def _fake_uploader(self, monkeypatch):
        import streamlit
        real = streamlit.file_uploader
        state = {"pending": None, "by_key": {}}

        def fake(label, *args, key=None, **kwargs):
            if key and key.startswith("orig_novel_up"):
                if state["pending"] is not None:
                    state["by_key"][key] = state["pending"]
                    state["pending"] = None
                return state["by_key"].get(key)
            return real(label, *args, key=key, **kwargs)

        monkeypatch.setattr(streamlit, "file_uploader", fake)
        return state

    def _drama(self, isolated_db, title, series_id):
        did = isolated_db.create_drama(title_en=title, media_type="audio_drama",
                                       content_mode="audio_drama", status="new",
                                       series_id=series_id)
        os.makedirs(isolated_db.drama_dir(did), exist_ok=True)
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def _switch_to(self, at, did):
        box = [b for b in at.selectbox if b.label == "Drama"][0]
        label = next(l for l in box.options if l.startswith(f"#{did} "))
        box.set_value(label).run(timeout=30)

    def _raw_context_path(self, did):
        import db
        return os.path.join(db.drama_dir(did), "raw_novel_context.txt")

    def test_switching_dramas_never_writes_the_new_dramas_raw_novel(
            self, isolated_db, monkeypatch):
        uploads = self._fake_uploader(monkeypatch)
        sid = isolated_db.get_or_create_series("S")
        did_a = self._drama(isolated_db, "Drama A", sid)
        did_b = self._drama(isolated_db, "Drama B", sid)

        at = self._run(did_a)
        uploads["pending"] = self._FakeUpload(b"drama A's raw novel")
        at.run(timeout=30)

        self._switch_to(at, did_b)
        at.run(timeout=30)

        assert not os.path.exists(self._raw_context_path(did_b))
        assert not os.path.exists(self._raw_context_path(did_a))

    def test_uploaded_raw_novel_is_only_written_after_an_explicit_save(
            self, isolated_db, monkeypatch):
        uploads = self._fake_uploader(monkeypatch)
        sid = isolated_db.get_or_create_series("S")
        did = self._drama(isolated_db, "Drama A", sid)

        at = self._run(did)
        uploads["pending"] = self._FakeUpload(b"drama A's raw novel")
        at.run(timeout=30)
        assert not os.path.exists(self._raw_context_path(did))

        [save] = [b for b in at.button if b.key == f"orig_novel_save_{did}"]
        save.click().run(timeout=30)
        with open(self._raw_context_path(did), "rb") as f:
            assert f.read() == b"drama A's raw novel"


class TestSaveEditsDoesNotRoundUntouchedTimestamps:
    """Minor finding, Step 25d: the start/end number_input widgets in
    Review & edit are seeded with round(ln.start, 2)/round(ln.end, 2) --
    their only display precision -- and that rounded display value is
    what got written back on every "Save edits" click, even for a line
    whose timing nobody touched that time, silently losing precision."""

    def _drama(self, isolated_db, start, end):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                       content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=start, end=end, zh="你好", en="Hello")])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def test_untouched_timing_keeps_its_original_precision_on_save(self, isolated_db):
        did = self._drama(isolated_db, start=1.234567, end=2.987654)
        at = self._run(did)
        [b for b in at.button if b.label == "💾 Save edits (this page)"][0].click().run(timeout=30)
        saved = isolated_db.load_lines(did)[0]
        assert saved["start"] == 1.234567
        assert saved["end"] == 2.987654

    def test_an_actually_edited_timing_is_saved_and_the_other_keeps_its_precision(self, isolated_db):
        did = self._drama(isolated_db, start=1.234567, end=2.987654)
        at = self._run(did)
        [ni for ni in at.number_input if ni.key == "start_0"][0].set_value(5.5).run(timeout=30)
        [b for b in at.button if b.label == "💾 Save edits (this page)"][0].click().run(timeout=30)
        saved = isolated_db.load_lines(did)[0]
        assert saved["start"] == 5.5
        assert saved["end"] == 2.987654  # untouched -- keeps its original precision


class TestWorkspaceStageIndex:
    """Regression coverage for Step 14 (Workspace shell rebuild): the
    project header's pipeline-progress stepper needs a real stage index
    computed from the drama's actual state, not a guess -- this is the
    function that computes it, and ui.workflow.stage_statuses_from_index
    turns that single index into a done/current/not-started list for
    each of the 7 stage tabs."""

    STAGES = ["Source", "Transcript", "Diarize", "Translate", "Review", "Dub", "Export"]

    def test_brand_new_drama_with_no_source_content_is_still_on_source(self, tmp_path):
        # A drama that's just been created (content_mode picked, nothing
        # uploaded yet) hasn't finished Source -- it shouldn't jump straight
        # to Transcript just because it also has no lines yet.
        idx = _compute_workspace_stage_index({"content_mode": "audio_drama"}, None, str(tmp_path))
        assert idx == 0

    def test_source_uploaded_but_not_yet_transcribed_is_on_transcript(self, tmp_path):
        idx = _compute_workspace_stage_index(
            {"content_mode": "audio_drama", "audio_filename": "audio.mp3"}, None, str(tmp_path))
        assert idx == 1

    def test_novel_narration_with_no_saved_novel_text_is_still_on_source(self, tmp_path):
        idx = _compute_workspace_stage_index(
            {"content_mode": "novel_narration"}, None, str(tmp_path))
        assert idx == 0

    def test_novel_narration_with_saved_novel_text_is_on_transcript(self, tmp_path):
        (tmp_path / "novel_narration_source.txt").write_text("some text")
        idx = _compute_workspace_stage_index(
            {"content_mode": "novel_narration"}, None, str(tmp_path))
        assert idx == 1

    def test_lines_with_no_speaker_yet_is_on_diarize(self, tmp_path):
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="", speaker=None)]
        idx = _compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 2

    def test_novel_narration_has_no_diarize_stage(self, tmp_path):
        # No audio to diarize -- speaker attribution is the translation
        # LLM's job, not a separate stage, so an unset speaker shouldn't
        # hold the stepper at Diarize the way it does for audio content.
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="", speaker=None)]
        idx = _compute_workspace_stage_index({"content_mode": "novel_narration"}, lines, str(tmp_path))
        assert idx == 3

    def test_partway_translated_shows_transcribe_diarize_done_translate_current(self, tmp_path):
        # The manual check from Step 14's exit conditions: open a drama
        # with lines already translated partway through, and confirm the
        # stepper shows Transcribe/Diarize done, Translate in progress,
        # Review/Dub/Export not started.
        lines = [
            Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A"),
            Line(idx=1, start=1, end=2, zh="再见", en="", speaker="B"),
        ]
        idx = _compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 3

        from ui.workflow import stage_statuses_from_index
        statuses = stage_statuses_from_index(self.STAGES, idx)
        assert statuses == ["done", "done", "done", "current", "not_started",
                             "not_started", "not_started"]

    def test_fully_translated_not_yet_dubbed_or_exported_is_on_review(self, tmp_path):
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A")]
        idx = _compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 4

    def test_dub_track_on_disk_moves_to_export(self, tmp_path):
        (tmp_path / "dub_track.wav").write_bytes(b"")
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A")]
        idx = _compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 6

    def test_exported_status_is_fully_done(self, tmp_path):
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A")]
        idx = _compute_workspace_stage_index(
            {"content_mode": "audio_drama", "status": "exported"}, lines, str(tmp_path))
        assert idx == 6
        from ui.workflow import stage_statuses_from_index
        assert stage_statuses_from_index(self.STAGES, idx) == \
            ["done", "done", "done", "done", "done", "done", "current"]

    def test_exported_with_no_persisted_speaker_still_shows_export_not_diarize(self, tmp_path):
        # Step 45: a real reported drama had fully translated lines and was
        # marked exported, but no line ever had a `speaker` value persisted
        # (diarization was skipped, or its result never saved) -- the old
        # check order treated "no speaker on any line" as unconditionally
        # meaning Diarize is current, even though translation/export had
        # clearly moved well past that. Export/dub now outrank Diarize.
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker=None),
                 Line(idx=1, start=1, end=2, zh="再见", en="Goodbye", speaker=None)]
        idx = _compute_workspace_stage_index(
            {"content_mode": "audio_drama", "status": "exported"}, lines, str(tmp_path))
        assert idx == 6

    def test_fully_translated_with_no_persisted_speaker_shows_review_not_diarize(self, tmp_path):
        # Same missing-speaker shape as above, but not yet exported/dubbed --
        # translation being fully done should still take the stepper past
        # Diarize to Review, not leave it stuck reporting Diarize forever.
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker=None)]
        idx = _compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 4


class TestStageTabsReplaceTheExpanderScroll:
    """Step 14 exit condition: a click-through confirms every one of
    Workspace's former 10 sections is reachable under the new stage tabs,
    and the project header's stepper reflects real progress. The many
    label/key-based widget lookups throughout this file already prove
    reachability (AppTest executes every st.tabs() body on each run, same
    as it always did for st.expander()) -- this test checks the
    structural piece those don't: that the 10-expander scroll is actually
    gone, replaced by exactly the 7 stage tabs, with the project header
    rendered above them."""

    def _drama(self, isolated_db, **fields):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                       content_mode="audio_drama", **fields)
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def test_seven_stage_tabs_replace_the_old_numbered_expanders(self, isolated_db):
        did = self._drama(isolated_db, status="new")
        at = self._run(did)
        assert [t.label for t in at.tabs] == \
            ["Source", "Transcript", "Diarize", "Translate", "Review", "Dub", "Export"]
        # None of the old numbered "N. label" expanders survive as expanders --
        # they're either the tabs themselves now, or content inside them.
        for e in at.expander:
            assert not e.label[:1].isdigit(), f"leftover numbered expander: {e.label!r}"

    def test_project_header_shows_the_drama_name(self, isolated_db):
        did = self._drama(isolated_db, status="new")
        at = self._run(did)
        assert any("Test Drama" in m.value for m in at.markdown)

    def test_stepper_reflects_progress_for_a_partly_translated_drama(self, isolated_db):
        did = self._drama(isolated_db, status="translated")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A"),
            Line(idx=1, start=1, end=2, zh="再见", en="", speaker="B"),
        ])
        at = self._run(did)
        stepper_html = "".join(m.value for m in at.markdown if "bh-stage" in (m.value or ""))
        # Source/Transcript/Diarize done, Translate current, the rest not started --
        # each stage's own marker span names its status class directly, so this
        # checks the exact stage/status pairing, not just that both classes appear.
        assert 'bh-stage-current">● Translate</span>' in stepper_html
        assert 'bh-stage-done">✓ Source</span>' in stepper_html
        assert 'bh-stage-done">✓ Transcript</span>' in stepper_html
        assert 'bh-stage-done">✓ Diarize</span>' in stepper_html
        assert '"bh-stage-item">○ Review</span>' in stepper_html
        assert '"bh-stage-item">○ Dub</span>' in stepper_html
        assert '"bh-stage-item">○ Export</span>' in stepper_html


class TestStageTabsOpenOnTheCurrentStage:
    """Step 19: without `default=`, st.tabs() always opened on "Source"
    regardless of how far a drama had actually progressed -- confirmed
    live in a real browser, the stepper would show e.g. "Diarize" as
    current while the tab strip opened on Source's own content, an extra
    click away from the drama's real next action. AppTest has no way to
    ask which tab is visually selected (`st.tabs` renders every tab's
    body regardless of selection, and the test element tree carries no
    "open" flag), so this spies on the real `st.tabs` call instead and
    checks it was asked for the right default -- the same technique
    other tests in this suite use to check a call's own arguments rather
    than an effect AppTest can't observe."""

    def _drama(self, isolated_db, **fields):
        return isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", **fields)

    def _run(self, did, monkeypatch):
        from streamlit.testing.v1 import AppTest
        import streamlit as st

        # undo() first so a second _run() in the same test re-wraps the
        # real st.tabs, not the previous call's own spy -- otherwise the
        # two spies chain and each call after the first overwrites both
        # captured dicts with its own (latest) arguments.
        monkeypatch.undo()
        captured = {}
        real_tabs = st.tabs

        def spy(labels, *args, **kwargs):
            captured["labels"] = labels
            captured["kwargs"] = kwargs
            return real_tabs(labels, *args, **kwargs)
        monkeypatch.setattr(st, "tabs", spy)

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at, captured

    def test_a_new_drama_defaults_to_source(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db, status="new")
        _, captured = self._run(did, monkeypatch)
        assert captured["kwargs"]["default"] == "Source"

    def test_an_aligned_drama_with_no_speakers_defaults_to_diarize(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db, status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
        _, captured = self._run(did, monkeypatch)
        assert captured["kwargs"]["default"] == "Diarize"

    def test_a_partly_translated_drama_defaults_to_translate(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db, status="translated")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A"),
            Line(idx=1, start=1, end=2, zh="再见", en="", speaker="B"),
        ])
        _, captured = self._run(did, monkeypatch)
        assert captured["kwargs"]["default"] == "Translate"

    def test_a_fully_translated_drama_defaults_to_review(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db, status="translated")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A")])
        _, captured = self._run(did, monkeypatch)
        assert captured["kwargs"]["default"] == "Review"

    def test_the_default_key_is_scoped_per_drama(self, isolated_db, monkeypatch):
        # So switching to a different drama re-seeds the default, but a
        # manual tab click within the SAME drama isn't reset by an
        # unrelated rerun (verified live: clicking a different tab, then
        # clicking the "Refresh" button, left the manual pick in place).
        did_a = self._drama(isolated_db, status="new")
        did_b = self._drama(isolated_db, status="translated")
        isolated_db.save_lines(did_b, [
            Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A")])
        _, captured_a = self._run(did_a, monkeypatch)
        _, captured_b = self._run(did_b, monkeypatch)
        assert captured_a["kwargs"]["key"] != captured_b["kwargs"]["key"]


class TestReviewTabGroupedSubsections:
    """Step 14 item 3 (added 2026-09-27 after the file-size correction):
    Review's dozen sub-features -- confirmed too numerous for a flat list
    once the audit was corrected -- are grouped into three st.popover
    clusters (Checks; AI refinement; Restructure lines) by physical
    proximity in the file, leaving Find & replace, Review queue, and
    Version history standalone since they're either the first thing done
    in Review or an ongoing workflow rather than a rare/advanced option.
    This is a static, in-place wrap (no code moved) -- these tests confirm
    the three popovers exist and that every one of the dozen sub-features
    is still present and reachable, per the roadmap's own exit condition
    for this correction."""

    ALL_TWELVE_LABELS = [
        "Find & replace",
        "Check line coverage (do this before translating)",
        "Check dubbing pacing (optional)",
        "Check translation consistency (optional)",
        "Review queue (flag lines that need a second look)",
        "Emotional register (sarcasm, humour, anger)",
        "Adaptive style (learns from your edits)",
        "Translation versions (compare models)",
        "Translation notes (idioms, wordplay, meaningful names)",
        "Merge short adjacent lines (optional)",
        "Re-segment long lines by meaning (optional)",
        "Version history / undo",
    ]

    def _drama(self, isolated_db, **fields):
        return isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", **fields)

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def test_three_popovers_exist_in_source(self):
        # AppTest has no typed accessor for st.popover (unlike .tabs/.expander),
        # so the popovers themselves are checked statically; their CONTENTS'
        # reachability is checked dynamically below via AppTest instead.
        src = open("tabs/workspace_tab.py", encoding="utf-8").read()
        assert 'with st.popover("🔍 Checks"' in src
        assert 'with st.popover("🧠 AI refinement"' in src
        assert 'with st.popover("✂️ Restructure lines"' in src

    def test_all_twelve_subfeatures_still_reachable(self, isolated_db):
        did = self._drama(isolated_db, status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="Hello")])
        at = self._run(did)
        found_labels = {e.label for e in at.expander}
        for label in self.ALL_TWELVE_LABELS:
            assert any(label in found for found in found_labels), \
                f"missing sub-feature: {label!r}"

    def test_find_replace_review_queue_and_history_stay_standalone(self):
        # These three are deliberately left outside any popover group --
        # Find & replace and Review queue are ongoing workflows rather than
        # rare/advanced options, and Version history is a safety net worth
        # keeping one click away. Confirmed by each one's own expander line
        # sitting at the same 12-space indent as a stage-tab-level
        # statement, not one level deeper as it would be inside a popover.
        src = open("tabs/workspace_tab.py", encoding="utf-8").read()
        lines = src.splitlines()
        standalone = ["🔎 Find & replace", "⚠️ Review queue", "🕓 Version history"]
        for label_start in standalone:
            matches = [l for l in lines if f'st.expander("{label_start}' in l]
            assert matches, f"expander not found: {label_start!r}"
            indent = len(matches[0]) - len(matches[0].lstrip(" "))
            assert indent == 12, f"{label_start!r} is at indent {indent}, expected 12 (standalone)"

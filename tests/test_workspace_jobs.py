"""Workspace logic tests moved out of tests/test_workspace_tab.py (Streamlit
retirement): the
background-thread job targets in services/workspace_job_service.py behind the
Workspace's Transcribe & Align, Read Captions, Flag, Fix-flagged, Emotion,
Consistency, Notes and Translate actions, and the stage index in
services/workflow_service.py. None of them import the Streamlit tab.

Regression coverage for a real bug: the Whisper pass used to run as a
blocking call inside the button's click handler, with no progress and no
way to use the rest of the app while a long file processed. It now runs in
a background thread with real progress, and the known failure modes (model
download failure, no audio detected) are recorded on the job result instead
of raised. run_hardsub_ocr_job follows the same pattern."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import background_jobs
import translate_engines
from services.workspace_job_service import (run_transcribe_job, run_hardsub_ocr_job, run_flag_job,
                                 run_emotion_job, run_consistency_job, run_translation_notes_job,
                                 run_fix_flagged_lines_job, run_translate_job)
from services.workflow_service import compute_workspace_stage_index as _compute_workspace_stage_index
import core as core_module
from core import Line
from tests import fake_engine


def _clear(job_id):
    background_jobs.clear_job(job_id)


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


class FakeFlaggingEngineWithUsage(FakeFlaggingEngine):
    """Same fake, but with a response.usage -- regression coverage for a
    real gap: none of the review-queue/emotion/consistency-check features
    ever logged token usage anywhere, so the "Estimated spend" dashboard
    number only ever reflected the main Translate job, not any of this."""
    def create(self, model, max_tokens, messages):
        resp = super().create(model, max_tokens, messages)
        resp.usage = type("Usage", (), {"input_tokens": 30, "output_tokens": 12})()
        return resp


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


def test_success_stores_segments_and_no_gpu_fallback(monkeypatch):
    job_id = "test_transcribe_ok"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}

    fake_segments = [{"start": 0.0, "end": 1.0, "text": "hi"}]
    monkeypatch.setattr(
        "services.workspace_job_service.transcribe_for_timing",
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

    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing", fake_transcribe)

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

    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing", fake_transcribe)

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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing", fake_transcribe)

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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing", fake_transcribe)

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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing",
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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing",
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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing",
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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing",
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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing",
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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing",
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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing",
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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing", lambda *a, **k: [
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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing", fake_transcribe)

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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing",
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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing", lambda *a, **k: fake_segments)

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
    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing", lambda *a, **k: fake_segments)

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

    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing", fake_transcribe)

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

    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing", lambda *a, **k: [])

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

    monkeypatch.setattr("services.workspace_job_service.transcribe_for_timing", fake_transcribe)

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
                      tesseract_cmd=None, job_id=None, cancel_check=None):
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

    # wt.core_module was the plain `core` module; patched directly now.
    monkeypatch.setattr(core_module, "extract_audio_slice", lambda *a, **k: None)
    monkeypatch.setattr(core_module, "transcribe_for_timing",
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

    # wt.core_module was the plain `core` module; patched directly now.
    monkeypatch.setattr(core_module, "extract_audio_slice", lambda *a, **k: None)

    def _fake_transcribe(slice_path, **kwargs):
        if "_1.wav" in slice_path:
            raise RuntimeError("model download failed partway through")
        return [{"start": 0.0, "end": 1.0, "text": "重新转录"}]

    monkeypatch.setattr(core_module, "transcribe_for_timing", _fake_transcribe)

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
                             fake_engine.FakeEngine(), "fake")
        assert version["label"].startswith("[testing: fake]")
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


class TestContextAheadAndBatchSizeSliders:
    """Step 32: look-ahead context and batch size, previously hardcoded
    (translate_lines_with_engine's own defaults, context_window_ahead=3,
    batch_size=20), are now adjustable sliders next to the existing
    look-back one -- and a novel-narration drama pre-selects higher
    starting values than an audio drama, though the user can still move
    any of them."""

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


class TestMusicMediaType:
    """Step 89: add music alongside the existing anime/asmr entries."""

    def test_music_is_a_media_type_option(self):
        # Repointed to the service copy of the list (Streamlit retirement).
        from services import drama_service
        assert "music" in drama_service.MEDIA_TYPE_OPTIONS


class TestWorkspaceStageIndex:
    """Regression coverage for Step 14 (Workspace shell rebuild): the
    project header's pipeline-progress stepper needs a real stage index
    computed from the drama's actual state, not a guess -- this is the
    function that computes it."""

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

    def test_fully_translated_not_yet_dubbed_or_exported_is_on_review(self, tmp_path):
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A")]
        idx = _compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 4

    def test_dub_track_on_disk_moves_to_export(self, tmp_path):
        (tmp_path / "dub_track.wav").write_bytes(b"")
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A")]
        idx = _compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 6

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

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
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import background_jobs
import db
import translate_engines
from tabs.workspace_tab import (run_transcribe_job, run_hardsub_ocr_job, run_flag_job,
                                 run_emotion_job, run_consistency_job, run_translation_notes_job,
                                 run_fix_flagged_lines_job, run_translate_job)
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

    def fake_separate(in_path, out_path, backend="auto"):
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


def test_vocal_separation_failure_is_recorded_not_raised(monkeypatch, tmp_path):
    job_id = "test_transcribe_vocal_sep_fail"
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    audio_path = str(tmp_path / "audio.wav")

    import audio_preprocess

    def fake_separate(in_path, out_path, backend="auto"):
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
    assert result == {"issue_count": 1}
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

    run_translation_notes_job(job_id, did, lines, FakeNotesEngine(), "claude", "zh")

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
    assert result == {"fixed_count": 1, "total_flagged": 1}
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
    assert result == {"fixed_count": 0, "total_flagged": 1}
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
    assert result == {"fixed_count": 0, "total_flagged": 0}
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
        import diarize

        monkeypatch.setattr(diarize, "diarize", lambda *a, **k: ([], "fake-model"))
        monkeypatch.setattr(diarize, "save_turns", lambda *a, **k: None)
        monkeypatch.setattr(diarize, "manual_lines_that_would_change", lambda *a, **k: [])

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
        assert any("Vertical clip ready" in s.value for s in at.success)
        assert any(dl.label.startswith("Download") for dl in at.download_button)


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

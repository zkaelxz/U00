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

    def fake_separate(in_path, out_path, model="htdemucs"):
        seen["in_path"] = in_path
        seen["out_path"] = out_path
        return out_path
    monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_separate)

    def fake_transcribe(path, *a, **k):
        seen["transcribed_path"] = path
        return [{"start": 0.0, "end": 1.0, "text": "hi"}]
    monkeypatch.setattr("tabs.workspace_tab.transcribe_for_timing", fake_transcribe)

    run_transcribe_job(job_id, audio_path, "medium", "zh", False, None, None, "", 5, 2000,
                        separate_vocals_first=True)

    assert seen["in_path"] == audio_path
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

    def fake_separate(in_path, out_path, model="htdemucs"):
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

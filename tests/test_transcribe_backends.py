"""Tests for the non-Whisper transcription backends (Qwen3 and the removed Moss), speed calibration and missing-package outcomes."""

import os
import time

import pytest

import background_jobs
from jobs import job_launch, job_runner
import qwen3_native
import whisper_models
from services import transcribe_pipeline, transcribe_service, transcribe_speed_service
from services import settings_service
from services.qwen3_requirements_service import MISSING_QWEN_MESSAGE
from lib.errors import DependencyUnavailableError, InvalidInputError
from tests.test_transcribe_service import (  # noqa: F401  (_no_real_whisper_load is the autouse fixture)
    _capture_worker_start, _clear, _drama_with_audio, _no_real_whisper_load, _seed_running_job,
)


class TestQwen3Backends:
    """Slice 34: the stored qwen3_asr / qwen3_forced_align choices are honoured
    by the run. Fake backend classes only -- no model, GPU or network."""

    _AUDIO_ARGS = ("simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False,
                   None, None, None)

    def _run(self, did, ddir, mode, text=None, **kw):
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), mode, text, "zh", *self._AUDIO_ARGS, **kw)
        return job_id

    def _raw(self, ddir):
        import json
        with open(os.path.join(ddir, "raw_transcript.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_qwen3_asr_choice_replaces_text_keeps_timing_and_records_backend(
            self, isolated_db, monkeypatch):
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.5, "text": "whisper text"}])
        calls = []

        class FakeQwen3ASR:
            def transcribe(self, audio_path, language, whisper_segments, use_gpu=False,
                           batch_size=1, progress_cb=None, **_device_callbacks):
                calls.append((language, whisper_segments, use_gpu, batch_size))
                return [{"start": s["start"], "end": s["end"], "text": "qwen text"}
                        for s in whisper_segments]
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", FakeQwen3ASR)
        # Step 103: the saved batch size reaches the backend.
        from services import asr_options_service
        asr_options_service.set_asr_options(qwen_asr_batch_size=4)
        messages = []
        real_update = background_jobs.update_progress
        monkeypatch.setattr(background_jobs, "update_progress",
                            lambda j, f, m="": (messages.append(m), real_update(j, f, m)))

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr", use_gpu=True)

        assert len(calls) == 1 and calls[0][0] == "zh" and calls[0][2] is True
        assert calls[0][3] == 4
        saved = isolated_db.load_lines(did)
        assert [(r["zh"], r["start"], r["end"]) for r in saved] == [("qwen text", 0.0, 1.5)]
        raw = self._raw(ddir)
        assert (raw["backend"], raw["model"]) == ("qwen3_asr", "Qwen3-ASR")
        assert any("Qwen3-ASR" in m for m in messages)
        assert background_jobs.get_status(job_id)["result"]["asr_backend"] == "qwen3_asr"
        _clear(job_id)

    def test_qwen3_text_gets_the_same_screens_as_whisper_text(self, isolated_db, monkeypatch):
        """Whisper's text is screened inside transcribe_for_timing; Qwen's replaces it
        later, so an isolated credit line is dropped and an over-fast line is flagged."""
        import asr_backend
        import whisper_hallucination as wh
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", lambda *a, **k: [
            {"start": 0.0, "end": 1.5, "text": "一"}, {"start": 2.0, "end": 3.0, "text": "二"},
            {"start": 30.0, "end": 31.5, "text": "四"}, {"start": 60.0, "end": 61.0, "text": "三"}])
        qwen = {0.0: "你好啊。", 2.0: "这是一句一秒钟之内根本不可能说完的非常非常长的话。",
                30.0: "はい、はい、はい、はい、はい、はい", 60.0: "谢谢观看"}

        class FakeQwen3ASR:
            def transcribe(self, audio_path, language, whisper_segments, use_gpu=False,
                           batch_size=1, progress_cb=None, **_device_callbacks):
                return [{"start": s["start"], "end": s["end"], "text": qwen[s["start"]]}
                        for s in whisper_segments]
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", FakeQwen3ASR)

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr")

        saved = isolated_db.load_lines(did)
        assert [r["zh"] for r in saved] == ["你好啊。", qwen[2.0], qwen[30.0]]
        assert saved[0]["flag"] is None
        assert (saved[1]["flag"], saved[1]["flag_note"]) == (wh.IMPLAUSIBLE_FLAG, wh.IMPLAUSIBLE_RATE_NOTE)
        # Qwen writes 、 between short replies at ordinary kana speed: flagged, never dropped.
        assert (saved[2]["flag"], saved[2]["flag_note"]) == (wh.IMPLAUSIBLE_FLAG, wh.IMPLAUSIBLE_LIST_NOTE)
        _clear(job_id)

    def test_long_qwen3_segment_becomes_several_subtitle_lines(self, isolated_db, monkeypatch):
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 175.0, "end": 210.0, "text": "w"}])
        text = "好,你刚讲不要讲。哇,我先离开一下。好,OK。重来。哇,大家好哦!" * 3

        class FakeQwen3ASR:
            def transcribe(self, audio_path, language, whisper_segments, use_gpu=False,
                           batch_size=1, progress_cb=None, **_device_callbacks):
                return [{"start": s["start"], "end": s["end"], "text": text}
                        for s in whisper_segments]
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", FakeQwen3ASR)

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr")

        saved = isolated_db.load_lines(did)
        assert len(saved) > 3
        assert "".join(r["zh"] for r in saved) == text
        assert saved[0]["start"] == 175.0 and saved[-1]["end"] == 210.0
        assert all(a["end"] == b["start"] for a, b in zip(saved, saved[1:]))
        assert len(self._raw(ddir)["segments"]) == 1
        _clear(job_id)

    def test_progress_never_hits_100_before_done_and_never_goes_back(
            self, isolated_db, monkeypatch):
        """Whisper fills 0-85%, the Qwen3 step 85-99% (no percent until its
        first batch), and only completion reports 100%."""
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        seen = []
        real_update = background_jobs.update_progress

        def record(j, f, m=""):
            seen.append((f, m))
            real_update(j, f, m)
        monkeypatch.setattr(background_jobs, "update_progress", record)

        def fake_whisper(*a, progress_cb=None, **k):
            for f in (0.25, 1.0):
                progress_cb(f)
            return [{"start": 0.0, "end": 1.5, "text": "w"}, {"start": 2.0, "end": 3.0, "text": "w2"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", fake_whisper)

        class FakeQwen3ASR:
            def transcribe(self, audio_path, language, whisper_segments, use_gpu=False,
                           batch_size=1, progress_cb=None, **_device_callbacks):
                progress_cb(0.5)
                progress_cb(1.0)
                return [{"start": s["start"], "end": s["end"], "text": "q"} for s in whisper_segments]
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", FakeQwen3ASR)

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr")

        fracs = [f for f, m in seen if m]
        assert max(fracs) < 1.0
        whisper_fracs = [f for f, m in seen if m.startswith("Transcribing (step 1 of 2)")]
        assert whisper_fracs and max(whisper_fracs) == pytest.approx(0.85)
        qwen_fracs = [f for f, m in seen if "step 2 of 2)... " in m]
        assert qwen_fracs and min(qwen_fracs) > 0.85 and max(qwen_fracs) <= 0.99
        # Monotonic from the first Whisper percent onward (the ticker's own
        # no-percent message holds the value at the split).
        later = [f for f, m in seen if m.startswith(("Transcribing", "Re-transcribing"))]
        assert later == sorted(later)
        _clear(job_id)

    def test_whisper_only_run_stays_below_100_until_done(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        seen = []
        real_update = background_jobs.update_progress
        monkeypatch.setattr(background_jobs, "update_progress",
                            lambda j, f, m="": (seen.append(f), real_update(j, f, m)))

        def fake_whisper(*a, progress_cb=None, **k):
            progress_cb(1.0)
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", fake_whisper)
        job_id = self._run(did, ddir, "whisper")
        assert max(seen) < 1.0
        _clear(job_id)

    def test_song_with_one_whisper_segment_gives_one_qwen3_line_and_warns(
            self, isolated_db, monkeypatch):
        """Qwen3-ASR only sees the slices Whisper's speech detector found, so a
        song where Whisper finds one segment yields one line however long the
        audio is; the run reports the low coverage instead of looking fine."""
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 6.0, "text": "first line"}])
        monkeypatch.setattr(transcribe_pipeline, "_audio_duration_seconds", lambda p: 240.0)
        monkeypatch.setattr(asr_backend, "load_qwen3_asr", lambda **k: type(
            "M", (), {"transcribe": lambda self, audio, language: [
                type("R", (), {"text": "qwen first line"})()]})())
        monkeypatch.setattr(asr_backend, "extract_audio_slice",
                            lambda a, s, e, out: open(out, "wb").close())

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr")

        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["qwen first line"]
        warning = background_jobs.get_status(job_id)["result"]["coverage_warning"]
        assert warning.startswith("Only 2% of the audio has text")
        assert "Qwen3-ASR only re-transcribes" in warning
        _clear(job_id)

    def test_default_whisper_path_never_touches_qwen3(self, isolated_db, monkeypatch):
        import asr_backend
        import forced_align
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

        def boom(*a, **k):
            raise AssertionError("Qwen3 must not be used")
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", boom)
        monkeypatch.setattr(forced_align, "align_with_qwen3", boom)

        job_id = self._run(did, ddir, "whisper")

        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["hi"]
        assert self._raw(ddir)["backend"] == "whisper"
        result = background_jobs.get_status(job_id)["result"]
        assert result["asr_backend"] == "whisper" and result["alignment_method"] == "whisper_diff"
        _clear(job_id)

    def test_qwen3_asr_import_error_fails_job_without_saving(self, isolated_db, monkeypatch):
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

        class Broken:
            def transcribe(self, *a, **k):
                raise ImportError("No module named 'torchaudio_secret_module'")
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", Broken)

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr")

        result = background_jobs.get_status(job_id)["result"]
        assert result["failed_reason"] == "dependency_missing"
        assert result["detail"] == MISSING_QWEN_MESSAGE
        assert "torchaudio_secret_module" not in result["detail"]
        assert isolated_db.load_lines(did) == []
        assert not os.path.exists(os.path.join(ddir, "raw_transcript.json"))
        _clear(job_id)

    def test_a_too_old_transformers_at_load_time_says_what_to_update(self, isolated_db, monkeypatch):
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

        class TooOld:
            def transcribe(self, *a, **k):
                raise qwen3_native.TransformersUnavailableError(
                    "Qwen3-ASR needs transformers 5.15 or newer (this is 4.57.6); "
                    "update it in Diagnostics.")
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", TooOld)

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr")

        result = background_jobs.get_status(job_id)["result"]
        assert result["failed_reason"] == "dependency_missing"
        assert result["detail"].startswith("Qwen3-ASR needs transformers 5.15 or newer")
        assert "Traceback" not in result["detail"]
        _clear(job_id)

    def test_forced_align_choice_uses_true_alignment(self, isolated_db, monkeypatch):
        import forced_align
        from core import Line
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 2.0, "text": "x"}])
        calls = []

        def fake_align(audio_path, user_lines, segments, language, use_gpu=False, **_device_callbacks):
            calls.append((language, use_gpu, list(user_lines)))
            return [Line(idx=0, start=0.25, end=1.75, zh="hi there")]
        monkeypatch.setattr(forced_align, "align_with_qwen3", fake_align)

        job_id = self._run(did, ddir, "have_transcript", "hi there",
                           alignment_method="qwen3_forced_align")

        assert len(calls) == 1 and calls[0][0] == "zh"
        saved = isolated_db.load_lines(did)
        assert [(r["start"], r["end"]) for r in saved] == [(0.25, 1.75)]
        assert self._raw(ddir)["mode"] == "aligned_transcript"
        result = background_jobs.get_status(job_id)["result"]
        assert result["alignment_method"] == "qwen3_forced_align"
        assert result["forced_align_error"] is None
        _clear(job_id)

    def test_whisper_is_released_before_qwen_retranscribes(self, isolated_db, monkeypatch):
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.5, "text": "whisper text"}])
        order = []
        monkeypatch.setattr(whisper_models, "release_whisper_models", lambda: order.append("release"))

        class FakeQwen3ASR:
            def transcribe(self, audio_path, language, whisper_segments, **_kw):
                order.append("qwen")
                return [dict(s, text="qwen text") for s in whisper_segments]
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", FakeQwen3ASR)

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr", use_gpu=True)

        assert order == ["release", "qwen"]
        _clear(job_id)

    def test_whisper_is_released_before_forced_alignment(self, isolated_db, monkeypatch):
        import forced_align
        from core import Line
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 2.0, "text": "x"}])
        order = []
        monkeypatch.setattr(whisper_models, "release_whisper_models", lambda: order.append("release"))

        def fake_align(audio_path, user_lines, segments, language, **_kw):
            order.append("align")
            return [Line(idx=0, start=0.25, end=1.75, zh="hi there")]
        monkeypatch.setattr(forced_align, "align_with_qwen3", fake_align)

        job_id = self._run(did, ddir, "have_transcript", "hi there",
                           alignment_method="qwen3_forced_align")

        assert order == ["release", "align"]
        _clear(job_id)

    def test_forced_align_cancel_ends_cancelled_releases_gpu_and_keeps_lines(
            self, isolated_db, monkeypatch):
        import forced_align
        from core import Line
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="old")])
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 2.0, "text": "x"}])
        released = []
        monkeypatch.setattr(whisper_models, "release_gpu_models",
                            lambda: released.append(1))
        job_id = f"transcribe_{did}"

        def fake_align(*a, cancel_check=None, **k):
            background_jobs.request_cancel(job_id)
            cancel_check()
            raise AssertionError("cancel_check must raise once cancel is requested")
        monkeypatch.setattr(forced_align, "align_with_qwen3", fake_align)

        # _spawn turns this exception into a "cancelled" job.
        with pytest.raises(background_jobs.JobCancelled):
            self._run(did, ddir, "have_transcript", "hi there",
                      alignment_method="qwen3_forced_align")

        assert released
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["old"]
        _clear(job_id)

    def test_forced_align_value_error_falls_back_and_is_reported(self, isolated_db, monkeypatch):
        import forced_align
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 2.0, "text": "hi there"}])

        def too_long(*a, **k):
            raise ValueError("Line 0 spans 400s on its own")
        monkeypatch.setattr(forced_align, "align_with_qwen3", too_long)

        job_id = self._run(did, ddir, "have_transcript", "hi there",
                           alignment_method="qwen3_forced_align")

        result = background_jobs.get_status(job_id)["result"]
        assert result["line_count"] == 1
        assert "spans 400s" in result["forced_align_error"]
        assert result["alignment_method"] == "whisper_diff"
        _clear(job_id)

    def test_start_forced_align_without_transcript_is_invalid_input(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                   alignment_method="qwen3_forced_align")
        with pytest.raises(InvalidInputError, match="needs a transcript"):
            transcribe_service.start_transcribe_run(did)

    def test_start_missing_package_is_dependency_unavailable(self, isolated_db, monkeypatch):
        import importlib.util
        real_find = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, "find_spec",
                            lambda name, *a, **k: None if name == "torch" else real_find(name, *a, **k))
        monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: "5.19.0")
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                   asr_backend_choice="qwen3_asr")
        with pytest.raises(DependencyUnavailableError, match="Open Diagnostics to install it"):
            transcribe_service.start_transcribe_run(did)

    @pytest.mark.parametrize("version,message", [
        (None, "needs transformers 5.15 or newer, which isn't installed"),
        ("4.57.6", r"needs transformers 5.15 or newer \(this is 4.57.6\); update it in Diagnostics"),
        ("5.14.1", r"\(this is 5.14.1\)")])
    def test_start_with_missing_or_old_transformers_is_dependency_unavailable(
            self, isolated_db, monkeypatch, version, message):
        import importlib.util
        monkeypatch.setattr(importlib.util, "find_spec", lambda *a, **k: object())
        monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: version)
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                   asr_backend_choice="qwen3_asr")
        with pytest.raises(DependencyUnavailableError, match=message):
            transcribe_service.start_transcribe_run(did)
        did2, _ = _drama_with_audio(isolated_db, transcript_mode="have_transcript",
                                    alignment_method="qwen3_forced_align")
        with pytest.raises(DependencyUnavailableError, match="Qwen3 forced alignment needs transformers"):
            transcribe_service.start_transcribe_run(did2, transcript_text="hi")

    def test_start_passes_choices_to_the_job_when_packages_present(self, isolated_db, monkeypatch):
        import importlib.util
        monkeypatch.setattr(importlib.util, "find_spec", lambda *a, **k: object())
        monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: "5.19.0")
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                   asr_backend_choice="qwen3_asr")
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert (captured["asr_backend_choice"], captured["alignment_method"]) == (
            "qwen3_asr", "whisper_diff")


class TestRemovedMossBackend:
    """A title saved with the removed MOSS backend loads as its default
    backend with a notice; the saved value is never rewritten."""

    def test_config_shows_the_default_backend_and_a_notice(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", asr_backend_choice="moss_td",
                                       source_language="ko")
        config = transcribe_service.get_transcribe_config(did)
        assert config["asr_backend_choice"] == "whisper"
        assert "MOSS-Transcribe-Diarize" in config["asr_backend_notice"]
        assert isolated_db.get_drama(did)["asr_backend_choice"] == "moss_td"

    def test_other_backends_have_no_notice(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", asr_backend_choice="qwen3_asr")
        assert transcribe_service.get_transcribe_config(did)["asr_backend_notice"] is None

    def test_moss_cannot_be_chosen_any_more(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError, match="Unknown asr_backend_choice"):
            transcribe_service.update_transcribe_config(did, asr_backend_choice="moss_td")

    def test_a_run_uses_the_default_backend(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                   asr_backend_choice="moss_td", source_language="ko")
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert captured["asr_backend_choice"] == "whisper"
        assert isolated_db.get_drama(did)["asr_backend_choice"] == "moss_td"

    def test_a_raw_transcript_marked_moss_still_loads(self, isolated_db):
        import raw_transcript
        did = isolated_db.create_drama(title_en="D")
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        raw_transcript.write_raw_transcript(
            ddir, [{"start": 0.0, "end": 1.0, "text": "x", "speaker": "S01"}], [],
            backend="moss_td", model="MOSS-Transcribe-Diarize")
        assert raw_transcript.load_latest(ddir)["backend"] == "moss_td"


class TestTranscribeSpeedCalibration:
    def test_record_then_read_per_model_and_device(self, isolated_db):
        assert transcribe_speed_service.measured_transcribe_speed("large-v3", False) is None
        transcribe_speed_service.record_transcribe_speed("large-v3", False, 600, 300)
        assert transcribe_speed_service.measured_transcribe_speed("large-v3", False) == 2.0
        assert transcribe_speed_service.measured_transcribe_speed("large-v3", True) is None
        assert transcribe_speed_service.measured_transcribe_speed("small", False) is None

    def test_two_readings_give_their_midpoint(self, isolated_db):
        transcribe_speed_service.record_transcribe_speed("small", True, 600, 300)   # 2.0
        transcribe_speed_service.record_transcribe_speed("small", True, 600, 100)   # 6.0
        assert transcribe_speed_service.measured_transcribe_speed("small", True) == 4.0

    def test_the_median_of_the_last_five_runs_is_used(self, isolated_db):
        # One slow outlier (a busy PC) doesn't move the estimate; the oldest ages out.
        for work in (600, 300, 300, 3000, 300):   # 1, 2, 2, 0.2, 2 audio s per s
            transcribe_speed_service.record_transcribe_speed("small", False, 600, work)
        assert transcribe_speed_service.measured_transcribe_speed("small", False) == 2.0
        assert transcribe_speed_service.measured_transcribe_runs("small", False) == 5
        transcribe_speed_service.record_transcribe_speed("small", False, 600, 100)   # 6.0 pushes out the 1.0
        assert transcribe_speed_service.measured_transcribe_runs("small", False) == 5
        assert transcribe_speed_service.measured_transcribe_speed("small", False) == 2.0
        assert [r["speed"] for r in transcribe_speed_service._recorded_runs("small", False)] == [
            2.0, 2.0, 0.2, 2.0, 6.0]

    def test_a_single_value_from_an_older_version_still_counts(self, isolated_db):
        isolated_db.set_app_setting("transcribe_speed", {"base|cpu": 3.0, "tiny|cpu": "bad"})
        assert transcribe_speed_service.measured_transcribe_speed("base", False) == 3.0
        assert transcribe_speed_service.measured_transcribe_runs("base", False) == 1
        assert transcribe_speed_service.measured_transcribe_speed("tiny", False) is None
        transcribe_speed_service.record_transcribe_speed("base", False, 600, 100)   # 6.0
        assert transcribe_speed_service.measured_transcribe_speed("base", False) == 4.5

    def test_stage_seconds_are_kept_per_run_and_summarised_by_median(self, isolated_db):
        for decode, work in ((10, 300), (30, 300), (20, 300)):
            transcribe_speed_service.record_transcribe_speed(
                "small", False, 600, work,
                stage_seconds={"decode_vad": decode, "transcribe": work, "bogus": 5, "align": -1})
        assert transcribe_speed_service.measured_stage_seconds("small", False) == {
            "decode_vad": 20.0, "transcribe": 300.0}
        assert transcribe_speed_service.measured_stage_seconds("small", True) == {}

    def test_diarize_speed_needs_three_runs_and_is_kept_per_device(self, isolated_db):
        for work in (300, 100):                                   # 2.0 and 6.0 audio s per s
            transcribe_speed_service.record_diarize_speed(False, 600, work)
        assert transcribe_speed_service.measured_diarize_speed(False) is None
        assert transcribe_speed_service.measured_diarize_runs(False) == 2
        transcribe_speed_service.record_diarize_speed(False, 600, 200)  # 3.0
        assert transcribe_speed_service.measured_diarize_speed(False) == 3.0
        assert transcribe_speed_service.measured_diarize_speed(True) is None
        # It does not mix with a Whisper model's own history.
        assert transcribe_speed_service.measured_transcribe_speed("small", False) is None

    def test_old_records_without_diarize_or_stages_still_read(self, isolated_db):
        isolated_db.set_app_setting("transcribe_speed", {
            "base|cpu": 3.0, "small|cpu": {"runs": [{"speed": 2.0}]}})
        assert transcribe_speed_service.measured_transcribe_speed("base", False) == 3.0
        assert transcribe_speed_service.measured_stage_seconds("small", False) == {}
        assert transcribe_speed_service.measured_diarize_speed(False) is None
        did = isolated_db.create_drama(title_en="D")
        cfg = transcribe_service.get_transcribe_config(did)
        assert cfg["measured_stage_seconds"] == {} and cfg["measured_diarize_speed"] is None
        assert cfg["measured_diarize_runs"] == 0

    def test_config_reports_stage_medians_and_diarize_speed(self, isolated_db):
        settings_service.set_settings({"use_gpu": False})   # these assert the CPU path
        did = isolated_db.create_drama(title_en="D")
        model = whisper_models.DEFAULT_WHISPER_SIZE
        transcribe_speed_service.record_transcribe_speed(model, False, 600, 300, stage_seconds={"load": 12})
        for work in (300, 300, 300):
            transcribe_speed_service.record_diarize_speed(False, 600, work)
        cfg = transcribe_service.get_transcribe_config(did)
        assert cfg["measured_stage_seconds"] == {"load": 12.0}
        assert cfg["measured_diarize_speed"] == 2.0 and cfg["measured_diarize_runs"] == 3

    @pytest.mark.parametrize("audio,work", [
        (None, 100), (100, None), ("100", 50), (100, "50"), (True, 50), (100, True),
        (0, 100), (-5, 100), (100, 0), (100, 1.0),          # too little work to trust
        (float("nan"), 100), (100, float("inf")),
        (1.0, 1000.0), (1e9, 10.0),                         # outside the speed bounds
    ])
    def test_bad_readings_are_ignored(self, isolated_db, audio, work):
        transcribe_speed_service.record_transcribe_speed("base", False, audio, work)
        assert transcribe_speed_service.measured_transcribe_speed("base", False) is None

    def test_bad_stored_values_read_as_none(self, isolated_db):
        for stored in ("fast", {"base|cpu": "x"}, {"base|cpu": -3}, {"base|cpu": True}, [1]):
            isolated_db.set_app_setting("transcribe_speed", stored)
            assert transcribe_speed_service.measured_transcribe_speed("base", False) is None
        # A bad stored blob does not stop the next good reading from being kept.
        transcribe_speed_service.record_transcribe_speed("base", False, 600, 300)
        assert transcribe_speed_service.measured_transcribe_speed("base", False) == 2.0

    def test_a_settings_failure_never_raises(self, isolated_db, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("db locked")
        monkeypatch.setattr(isolated_db, "get_app_setting", boom)
        assert transcribe_speed_service.measured_transcribe_speed("base", False) is None
        transcribe_speed_service.record_transcribe_speed("base", False, 600, 300)

    def test_config_carries_the_gpu_setting_for_devices_that_cannot_read_settings(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        settings_service.set_settings({"use_gpu": True})
        assert transcribe_service.get_transcribe_config(did)["use_gpu"] is True
        settings_service.set_settings({"use_gpu": False})
        assert transcribe_service.get_transcribe_config(did)["use_gpu"] is False

    def test_config_reports_the_speed_for_the_stored_model_and_device(self, isolated_db):
        settings_service.set_settings({"use_gpu": False})   # these assert the CPU path
        did = isolated_db.create_drama(title_en="D")
        # An unsaved drama's model follows the GPU setting, so each device has its own default.
        transcribe_speed_service.record_transcribe_speed(whisper_models.DEFAULT_WHISPER_SIZE, False, 600, 300)
        assert transcribe_service.get_transcribe_config(did)["measured_speed"] == 2.0
        assert transcribe_service.get_transcribe_config(did)["measured_speed_runs"] == 1
        transcribe_speed_service.record_transcribe_speed(whisper_models.DEFAULT_WHISPER_SIZE, True, 3000, 100)
        isolated_db.set_app_setting("use_gpu", True)
        assert transcribe_service.get_transcribe_config(did)["measured_speed"] == 30.0

    def test_a_finished_whisper_job_records_its_speed(self, isolated_db, monkeypatch):
        # Only this module's clock is faked: first percent at t=100, done at t=220.
        now = {"t": 100.0}
        monkeypatch.setattr(transcribe_pipeline, "time", type("T", (), {"monotonic": staticmethod(lambda: now["t"])}))
        monkeypatch.setattr(transcribe_pipeline, "_audio_duration_seconds", lambda p: 600.0)
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")

        def fake_whisper(*a, progress_cb=None, **k):
            progress_cb(0.0)
            progress_cb(0.1)   # the clock starts here, at 10%
            now["t"] = 220.0
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", fake_whisper)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh",
            *TestQwen3Backends._AUDIO_ARGS)
        # 90% of 600 s of audio in 120 s of work.
        assert transcribe_speed_service.measured_transcribe_speed("medium", False) == pytest.approx(4.5)
        # The wait for the first percent is decode and VAD; the rest is the Whisper pass.
        assert transcribe_speed_service.measured_stage_seconds("medium", False)["decode_vad"] == 0.0
        assert transcribe_speed_service.measured_stage_seconds("medium", False)["transcribe"] == 120.0
        _clear(job_id)


def _app_log_text():
    import applog
    import db
    for h in applog.get_logger().handlers:
        h.flush()
    path = os.path.join(db.LIBRARY_DIR, "logs", "app.log")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestMissingPackageOutcome:
    def test_worker_turns_an_import_error_into_the_fixed_sentence(self, monkeypatch, tmp_path):
        import queue

        def boom(*a, **k):
            raise ModuleNotFoundError("No module named 'faster_whisper'")
        monkeypatch.setattr(transcribe_pipeline, "_transcribe_pipeline", boom)
        q = queue.Queue()
        transcribe_pipeline._transcribe_worker(
            "a.wav", "whisper", None, "zh", "simplified", "small", 5, 300, 0.5, False, "auto",
            False, False, False, "", False, "whisper", "whisper_diff", None, 1, False, False, 2.0, 0.35,
            False, False, "normal", "standard", None, None, None, str(tmp_path / "scratch"), q)
        kind, outcome = q.get_nowait()
        assert kind == "ok"
        # import_error is for the server log only; _apply_transcription takes it out.
        assert outcome.pop("import_error") == "ModuleNotFoundError: No module named 'faster_whisper'"
        assert outcome == {"failed_reason": "dependency_missing",
                           "detail": transcribe_pipeline.MISSING_TRANSCRIPTION_MESSAGE}
        assert "faster_whisper" not in outcome["detail"]

    def test_a_headroom_refusal_shows_its_own_sentence(self, monkeypatch, tmp_path):
        import queue
        import memory_headroom
        sentence = "Not loading Whisper large-v3: it needs about 3 GB of GPU memory, 1 GB is free."

        def refuse(*a, **k):
            raise memory_headroom.HeadroomError(sentence, vram_need_mb=3072.0)
        monkeypatch.setattr(transcribe_pipeline, "_transcribe_pipeline", refuse)
        q = queue.Queue()
        transcribe_pipeline._transcribe_worker(
            "a.wav", "whisper", None, "zh", "simplified", "small", 5, 300, 0.5, False, "auto",
            False, False, False, "", False, "whisper", "whisper_diff", None, 1, False, False, 2.0, 0.35,
            False, False, "normal", "standard", None, None, None, str(tmp_path / "scratch"), q)
        kind, outcome = q.get_nowait()
        # The need lets the GPU guard wait for room (jobs/gpu_guard.py).
        assert (kind, outcome) == ("ok", {"failed_reason": "headroom", "detail": sentence,
                                          "vram_need_mb": 3072.0})
        # The job ends Failed with the sentence alone, no "HeadroomError:" in front.
        assert job_runner._failed_outcome_error(outcome, "transcribe_1") == sentence

    def test_a_permission_refusal_on_the_audio_tells_the_user_what_to_do(self, monkeypatch, tmp_path):
        import queue

        def refuse(*a, **k):
            raise PermissionError(13, "Permission denied", "audio.wav")
        monkeypatch.setattr(transcribe_pipeline, "_transcribe_pipeline", refuse)
        q = queue.Queue()
        transcribe_pipeline._transcribe_worker(
            "a.wav", "whisper", None, "zh", "simplified", "small", 5, 300, 0.5, False, "auto",
            False, False, False, "", False, "whisper", "whisper_diff", None, 1, False, False, 2.0, 0.35,
            False, False, "normal", "standard", None, None, None, str(tmp_path / "scratch"), q)
        kind, outcome = q.get_nowait()
        assert (kind, outcome) == ("ok", {"failed_reason": "media_unreadable"})
        message = job_runner._failed_outcome_error(outcome, "transcribe_1")
        assert "run Setup again" in message and "audio.wav" not in message

    def test_the_hardsub_thread_job_shows_a_headroom_sentence_alone(self, isolated_db, monkeypatch):
        import memory_headroom
        sentence = "Not loading the OCR model: it needs about 2 GB of RAM, 1 GB is free."

        def refuse(*a, **k):
            raise memory_headroom.HeadroomError(sentence)
        monkeypatch.setattr(transcribe_pipeline, "_transcribe_pipeline", refuse)
        job_id = "transcribe_headroom"
        background_jobs.clear_job(job_id)
        job_launch.start_job(
            job_id, transcribe_service._run_transcribe_and_apply_job, job_id, 1, "a.wav",
            "hardsub_ocr", None, "zh", "simplified", "small", 5, 300, 0.5, False, "auto", False,
            False, False, None, None, None)
        for _ in range(200):
            status = background_jobs.get_status(job_id)
            if status["status"] != "running":
                break
            time.sleep(0.02)
        assert status["status"] == "error"
        assert status["error"] == sentence
        assert status["result"]["vram_need_mb"] is None   # a RAM refusal: nothing to wait for
        background_jobs.clear_job(job_id)

    def test_the_failed_import_is_logged_and_kept_out_of_the_result(self, isolated_db):
        outcome = {**transcribe_pipeline.missing_package_outcome(),
                   "import_error": "ImportError: DLL load failed while importing _ext"}
        result = transcribe_service._apply_on_done(
            "transcribe_x", dict(outcome), drama_id=7, source_language="zh", whisper_size="small",
            use_gpu=False, transcript_mode="whisper", alignment_method="whisper_diff",
            hf_token=None, diarize_audio_path=None, expected_speakers=None)
        assert result == {"failed_reason": "dependency_missing",
                          "detail": transcribe_pipeline.MISSING_TRANSCRIPTION_MESSAGE}
        log = _app_log_text()
        assert "transcription of title 7 could not import a package" in log
        assert "DLL load failed while importing _ext" in log

    def test_the_hardsub_thread_job_names_ocr_not_transcription(self, isolated_db, monkeypatch):
        def boom(*a, **k):
            raise ImportError("No module named 'cv2'")
        monkeypatch.setattr(transcribe_pipeline, "_transcribe_pipeline", boom)
        job_id = "transcribe_missing_pkg"
        background_jobs.clear_job(job_id)
        job_launch.start_job(
            job_id, transcribe_service._run_transcribe_and_apply_job, job_id, 1, "a.wav",
            "hardsub_ocr", None, "zh", "simplified", "small", 5, 300, 0.5, False, "auto", False,
            False, False, None, None, None)
        for _ in range(200):
            status = background_jobs.get_status(job_id)
            if status["status"] != "running":
                break
            time.sleep(0.02)
        assert status["status"] == "error"
        assert status["error"] == transcribe_pipeline.MISSING_OCR_MESSAGE
        assert "Transcription isn't installed" not in status["error"]
        assert "cv2" not in status["error"]
        assert "No module named 'cv2'" in _app_log_text()
        background_jobs.clear_job(job_id)


def test_sensitive_preset_lowers_the_qwen_speech_detector_threshold():
    import asr_backend
    from services.transcribe_pipeline import _tuning_for_preset
    for backend in ("qwen3_asr_vad", "qwen3_asr_long"):
        assert _tuning_for_preset(asr_backend.DEFAULT_TUNING, backend, "sensitive").vad_threshold == 0.35


def test_normal_preset_and_whisper_boundary_backends_keep_the_threshold():
    import asr_backend
    from services.transcribe_pipeline import _tuning_for_preset
    tuning = asr_backend.DEFAULT_TUNING
    assert _tuning_for_preset(tuning, "qwen3_asr_vad", "normal") is tuning
    assert _tuning_for_preset(tuning, "qwen3_asr", "sensitive") is tuning


def test_a_threshold_changed_in_settings_wins_over_the_sensitive_preset():
    import asr_backend
    from services.transcribe_pipeline import _tuning_for_preset
    tuning = asr_backend.DEFAULT_TUNING._replace(vad_threshold=0.6)
    assert _tuning_for_preset(tuning, "qwen3_asr_vad", "sensitive").vad_threshold == 0.6

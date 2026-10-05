"""The opt-in qwen3_asr_vad backend: VAD spans -> Qwen3-ASR text -> lines, with
optional forced-aligner timing. Models, audio and VAD are all faked."""
import sys

import numpy as np
import pytest

import asr_backend as ab
import background_jobs
import forced_align


class FakeResult:
    def __init__(self, text):
        self.text = text


class SeqModel:
    """Returns the queued texts in call order; records each call's input size."""
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []

    def transcribe(self, audio, language):
        items = audio if isinstance(audio, list) else [audio]
        self.calls.append(len(items))
        return [FakeResult(self.texts.pop(0)) for _ in items]


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(ab, "load_audio_16k", lambda path: np.zeros(16000 * 60, dtype="float32"))
    monkeypatch.setattr(ab, "extract_audio_slice", lambda a, s, e, out: open(out, "wb").close())
    holder = {}

    def use(texts):
        holder["model"] = SeqModel(texts)
        monkeypatch.setattr(ab, "load_qwen3_asr",
                            lambda use_gpu=False, model_size="1.7B": holder["model"])
        return holder["model"]
    return use


def vad(spans):
    return lambda audio, sr: spans


def run(vad_fn, **kw):
    return ab.Qwen3ASRVadBackend().transcribe("/a.wav", "zh", vad_fn=vad_fn, **kw)


def test_line_bounds_are_the_vad_spans_not_whisper(fakes):
    fakes(["你好", "再见"])
    out = run(vad([(1.0, 3.0), (10.0, 12.0)]))
    assert [s["text"] for s in out] == ["你好", "再见"]
    # speech_spans pads each span by 100 ms per side
    assert out[0]["start"] == pytest.approx(0.9) and out[0]["end"] == pytest.approx(3.1)
    assert out[1]["start"] == pytest.approx(9.9)


def test_long_spans_are_capped_and_tile_the_original(fakes):
    fakes(["a", "b"])
    out = run(vad([(0.0, 10.0), (10.1, 20.0)]))
    assert len(out) == 2 and all(s["end"] - s["start"] <= 15.0 for s in out)
    assert out[0]["end"] == pytest.approx(out[1]["start"])


def test_long_text_is_split_into_ordered_lines_inside_the_span(fakes):
    fakes(["你好你好你好你好你好。" * 6])
    out = run(vad([(0.0, 14.0)]))
    assert len(out) > 1
    assert out[0]["start"] >= 0.0 and out[-1]["end"] <= 14.2
    assert all(a["end"] <= b["start"] + 1e-9 for a, b in zip(out, out[1:]))


def test_repeated_phrase_loop_collapses_to_one_line_and_empty_text_is_skipped(fakes):
    fakes(["哈哈", "哈哈", "哈哈", "哈哈", "哈哈", ""])
    out = run(vad([(i * 4.0, i * 4.0 + 2.0) for i in range(6)]))
    assert [s["text"] for s in out] == ["哈哈"]


def test_a_short_pause_inside_a_sentence_does_not_split_it(fakes):
    model = fakes(["你好吗我很好"])
    out = run(vad([(1.0, 3.0), (3.8, 6.0)]))
    assert model.calls == [1] and len(out) == 1
    assert out[0]["start"] == pytest.approx(0.9) and out[0]["end"] == pytest.approx(6.1)


def test_a_long_pause_still_starts_a_new_line(fakes):
    model = fakes(["你好", "再见"])
    out = run(vad([(1.0, 3.0), (6.0, 8.0)]))
    assert model.calls == [1, 1] and [s["text"] for s in out] == ["你好", "再见"]


def test_model_hears_the_silence_around_a_span_but_the_line_keeps_the_span_times(
        fakes, monkeypatch):
    fakes(["你好"])
    cuts = []
    monkeypatch.setattr(ab, "extract_audio_slice",
                        lambda a, s, e, out: cuts.append((s, e)) or open(out, "wb").close())
    out = run(vad([(10.0, 12.0)]))
    assert cuts == [(pytest.approx(7.9), pytest.approx(14.1))]
    assert out[0]["start"] == pytest.approx(9.9) and out[0]["end"] == pytest.approx(12.1)


def test_context_windows_stay_inside_the_silence_between_spans():
    from vad_segments import Span, context_windows
    windows = context_windows([Span(1.0, 2.0), Span(3.0, 4.0), Span(20.0, 21.0)], 22.0, 2.0)
    # never into a neighbour's speech (a window may share silence with its neighbour),
    # never outside the file
    assert windows == [Span(0.0, 3.0), Span(2.0, 6.0), Span(18.0, 22.0)]


def test_no_speech_returns_nothing(fakes):
    assert run(vad([])) == []


def test_unsupported_language_raises_before_loading_audio(monkeypatch):
    monkeypatch.setattr(ab, "load_audio_16k", lambda p: pytest.fail("loaded"))
    with pytest.raises(ValueError, match="doesn't cover"):
        ab.Qwen3ASRVadBackend().transcribe("/a.wav", "xx")


def test_missing_vad_package_is_a_clear_error(monkeypatch):
    from vad_segments import VadNotInstalledError
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", None)
    with pytest.raises(VadNotInstalledError):
        ab.load_audio_16k("/a.wav")


def test_cancel_between_batches_stops_the_run(fakes):
    model = fakes(["a", "b", "c"])
    seen = []

    def check():
        seen.append(1)
        if len(seen) > 2:
            raise background_jobs.JobCancelled()
    with pytest.raises(background_jobs.JobCancelled):
        run(vad([(0, 2), (5, 7), (9, 11)]), cancel_check=check)
    assert model.texts  # the last span was never transcribed


def test_batch_size_follows_the_qwen_batching_rules(fakes, monkeypatch):
    model = fakes(["a", "b"])
    monkeypatch.setattr(ab, "installed_qwen_asr_version", lambda: ab.QWEN_ASR_BATCH_TESTED_VERSION)
    run(vad([(0, 2), (5, 7)]), batch_size=2)
    assert model.calls == [2]


class FakeUnit:
    def __init__(self, text, start, end):
        self.text, self.start_time, self.end_time = text, start, end


class FakeAligner:
    def __init__(self, units):
        self.units = units

    def align(self, audio, text, language):
        return [self.units]


def _aligner(monkeypatch, units):
    monkeypatch.setattr(forced_align, "load_qwen3_aligner", lambda use_gpu=False: FakeAligner(units))
    monkeypatch.setattr(forced_align, "_extract_audio_slice", lambda a, s, e, out: open(out, "wb").close())


def test_refine_timing_uses_aligner_and_flags_repaired_lines(monkeypatch):
    _aligner(monkeypatch, [FakeUnit("你", 0.5, 0.8), FakeUnit("好", 0.8, 0.8)])
    out = forced_align.refine_segment_timing(
        "/a.wav", [[{"start": 10.0, "end": 14.0, "text": "你好"}]], "zh")
    assert out[0]["start"] == pytest.approx(10.5)
    assert 10.5 < out[0]["end"] <= 14.0
    assert out[0]["flag"] == "timing_uncertain"


def test_refine_timing_keeps_span_times_when_aligner_gives_nothing(monkeypatch):
    _aligner(monkeypatch, [])
    out = forced_align.refine_segment_timing(
        "/a.wav", [[{"start": 10.0, "end": 14.0, "text": "你好"}]], "zh")
    assert (out[0]["start"], out[0]["end"]) == (10.0, 14.0)
    assert out[0]["flag"] == "timing_uncertain"


def test_refine_through_the_backend_is_off_unless_asked(fakes, monkeypatch):
    fakes(["你好"])
    monkeypatch.setattr(forced_align, "refine_segment_timing",
                        lambda *a, **k: pytest.fail("refined"))
    assert len(run(vad([(1.0, 3.0)]))) == 1


def test_backend_is_registered_and_not_experimental():
    assert isinstance(ab.get_backend("qwen3_asr_vad"), ab.Qwen3ASRVadBackend)
    assert "qwen3_asr_vad" not in ab.EXPERIMENTAL_BACKENDS


def test_refine_option_defaults_off_and_saves(isolated_db):
    from services import asr_options_service
    from services.service_errors import InvalidInputError
    assert asr_options_service.get_asr_options()["qwen_vad_refine_timing"] is False
    assert asr_options_service.set_asr_options(qwen_vad_refine_timing=True)[
        "qwen_vad_refine_timing"] is True
    with pytest.raises(InvalidInputError):
        asr_options_service.set_asr_options(qwen_vad_refine_timing="yes")


def test_run_start_and_validate_refuse_vad_backend_without_faster_whisper(isolated_db, monkeypatch):
    import importlib.util
    import os
    from services import transcribe_service
    from services.service_errors import DependencyUnavailableError
    did = isolated_db.create_drama(title_en="D", audio_filename="audio.wav",
                                   transcript_mode="whisper", asr_backend_choice="qwen3_asr_vad")
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, "audio.wav"), "wb").close()
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(transcribe_service, "require_qwen3_packages", lambda feature: None)
    monkeypatch.setattr(transcribe_service.importlib.util, "find_spec",
                        lambda name, *a: None if name == "faster_whisper" else real_find_spec(name, *a))
    with pytest.raises(DependencyUnavailableError, match="faster-whisper"):
        transcribe_service.start_transcribe_run(did)
    with pytest.raises(DependencyUnavailableError, match="faster-whisper"):
        transcribe_service.validate_transcribe_options(did)


def test_refine_trims_the_previous_line_when_a_fallback_line_would_overlap_it(monkeypatch):
    # Line 1 is aligned all the way to the span end; line 2 gets no units.
    _aligner(monkeypatch, [FakeUnit("你", 0.0, 2.0), FakeUnit("好", 2.0, 4.0)])
    out = forced_align.refine_segment_timing(
        "/a.wav", [[{"start": 10.0, "end": 12.0, "text": "你好"},
                    {"start": 12.0, "end": 14.0, "text": "再见"}]], "zh")
    assert out[0]["end"] <= out[1]["start"]
    assert out[1]["end"] == 14.0
    assert out[0]["flag"] == out[1]["flag"] == "timing_uncertain"


class _Rep:
    job_id = None

    def __init__(self, cancelled=False):
        self._cancelled = cancelled

    def progress(self, frac, message=""):
        pass

    def stage(self, message, frac=0.0):
        class T:
            def start(self):
                return self

            def stop(self):
                pass
        return T()

    def cancelled(self):
        return self._cancelled

    def raise_if_cancelled(self):
        if self._cancelled:
            raise background_jobs.JobCancelled()


def _pipeline(rep, tmp_path, monkeypatch, fake_transcribe):
    from services import transcribe_service
    monkeypatch.setattr(ab.Qwen3ASRVadBackend, "transcribe", fake_transcribe)
    monkeypatch.setattr(transcribe_service.core_module, "release_gpu_models", lambda: None)
    monkeypatch.setattr(transcribe_service, "_audio_duration_seconds", lambda p: 10.0)
    return transcribe_service._transcribe_pipeline(
        rep, str(tmp_path / "a.wav"), "whisper", None, "zh", "simplified", "medium", 5, 300, 0.5,
        False, "auto", False, False, False, None, "", False, "qwen3_asr_vad", "whisper_diff",
        vad_refine_timing=True)


def test_pipeline_reports_backend_and_keeps_span_times_and_flags(tmp_path, monkeypatch):
    seen = {}

    def fake(self, audio_path, language, **kw):
        seen.update(kw)
        return [{"start": 1.0, "end": 2.0, "text": "你好"},
                {"start": 3.0, "end": 4.0, "text": "再见", "flag": "timing_uncertain",
                 "flag_note": "n"}]
    out = _pipeline(_Rep(), tmp_path, monkeypatch, fake)
    assert out["raw_backend"] == "qwen3_asr_vad"
    assert [(ln.start, ln.end, ln.zh, ln.flag) for ln in out["lines"]] == [
        (1.0, 2.0, "你好", None), (3.0, 4.0, "再见", "timing_uncertain")]
    assert seen["refine_timing"] is True


def test_pipeline_missing_vad_is_a_dependency_error(tmp_path, monkeypatch):
    from vad_segments import VadNotInstalledError

    def fake(self, *a, **k):
        raise VadNotInstalledError("No module named 'faster_whisper'")
    out = _pipeline(_Rep(), tmp_path, monkeypatch, fake)
    assert out["failed_reason"] == "dependency_missing"
    assert "pip install faster-whisper" in out["detail"]


def test_pipeline_cancel_before_start_returns_cancelled(tmp_path, monkeypatch):
    out = _pipeline(_Rep(cancelled=True), tmp_path, monkeypatch,
                    lambda self, *a, **k: pytest.fail("ran"))
    assert out == {"failed_reason": "cancelled"}

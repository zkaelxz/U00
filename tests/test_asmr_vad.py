"""The optional ASMR voice detector: hysteresis, windowing, fallback and the
opt-in download. No onnxruntime, model or network: everything is faked."""
import hashlib

import numpy as np
import pytest

import asmr_vad
import asr_backend as ab
from services import asr_options_service
from services.service_errors import ConflictError, InvalidInputError


class FakeSession:
    """Returns the queued logit rows, one per 30 s window."""
    def __init__(self, rows):
        self.rows = list(rows)
        self.runs = 0

    def get_inputs(self):
        return [type("I", (), {"name": "input_features"})()]

    def run(self, _outputs, feeds):
        assert feeds["input_features"].shape == (1, 80, 3000)
        self.runs += 1
        return [np.array([self.rows.pop(0)])]


def detector(rows, **kw):
    det = asmr_vad.AsmrVad(FakeSession(rows), **kw)
    det._extractor = lambda window, padding: np.zeros((80, 3000), dtype=np.float32)
    return det


def logits(*runs):
    """1500 logits: each (n_frames, probability) run, then silence."""
    out = []
    for n, p in runs:
        out += [float(np.log(p / (1 - p)))] * n
    return out + [-6.0] * (1500 - len(out))


def test_hysteresis_keeps_a_dip_above_stop_inside_one_span():
    scores = [0.1, 0.6, 0.4, 0.6, 0.1]
    assert asmr_vad.hysteresis_spans(scores, 0.5, 0.35, frame_s=1.0) == [(1.0, 4.0)]
    # the same dip under a single threshold would have split it
    assert asmr_vad.hysteresis_spans(scores, 0.5, 0.5, frame_s=1.0) == [(1.0, 2.0), (3.0, 4.0)]


def test_a_span_open_at_the_end_closes_at_the_end():
    assert asmr_vad.hysteresis_spans([0.0, 0.9, 0.9], frame_s=1.0) == [(1.0, 3.0)]


def test_a_score_between_the_thresholds_never_starts_a_span():
    assert asmr_vad.hysteresis_spans([0.4, 0.45, 0.4], 0.5, 0.35, frame_s=1.0) == []


def test_stop_above_start_is_refused():
    with pytest.raises(ValueError):
        asmr_vad.AsmrVad(FakeSession([]), start=0.3, stop=0.5)


def test_speech_in_the_second_window_is_offset_by_thirty_seconds():
    det = detector([logits(), logits((50, 0.9))])
    spans = det(np.zeros(16000 * 45, dtype=np.float32), 16000)
    assert spans == [pytest.approx((30.0, 31.0))]


def test_frames_past_the_end_of_a_short_clip_are_ignored():
    # 1 s of audio = 50 frames; a loud padding region must not count as speech
    det = detector([logits((1500, 0.9))])
    spans = det(np.zeros(16000, dtype=np.float32), 16000)
    assert spans == [pytest.approx((0.0, 1.0))]


def test_other_sample_rates_are_refused():
    with pytest.raises(ValueError):
        detector([])(np.zeros(8000, dtype=np.float32), 8000)


def test_the_output_is_treated_as_logits():
    det = detector([[0.0] * 1500])  # sigmoid(0) = 0.5 sits exactly on the start threshold
    assert det.scores(np.zeros(16000, dtype=np.float32))[0] == pytest.approx(0.5)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
    return tmp_path


def _stub_faster_whisper(monkeypatch):
    import sys
    import types
    pkg = types.ModuleType("faster_whisper")
    pkg.feature_extractor = types.ModuleType("faster_whisper.feature_extractor")
    monkeypatch.setitem(sys.modules, "faster_whisper", pkg)
    monkeypatch.setitem(sys.modules, "faster_whisper.feature_extractor", pkg.feature_extractor)


def test_load_falls_back_when_the_model_is_missing(data_dir, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", type("M", (), {}))
    _stub_faster_whisper(monkeypatch)
    with pytest.raises(asmr_vad.AsmrVadUnavailable, match="not downloaded"):
        asmr_vad.load_detector()


def test_load_falls_back_when_onnxruntime_is_missing(data_dir, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", None)
    with pytest.raises(asmr_vad.AsmrVadUnavailable, match="onnxruntime"):
        asmr_vad.load_detector()


def test_a_corrupt_model_file_falls_back_instead_of_raising(data_dir, monkeypatch):
    class Ort:
        @staticmethod
        def InferenceSession(*a, **k):
            raise RuntimeError("/secret/path bad protobuf")
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", Ort)
    _stub_faster_whisper(monkeypatch)
    path = asmr_vad.model_path()
    __import__("os").makedirs(__import__("os").path.dirname(path))
    open(path, "wb").write(b"x")
    notes = []
    assert asmr_vad.vad_fn_or_fallback(notes.append) is None
    assert notes and "secret" not in notes[0] and "Standard" in notes[0]


class FakeResponse:
    def __init__(self, status=200, body=b"", location=None):
        self.status_code, self.body = status, body
        self.headers = {"Location": location} if location else {}

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def close(self):
        pass


@pytest.fixture
def small_model(monkeypatch):
    body = b"model-bytes" * 10
    monkeypatch.setattr(asmr_vad, "MODEL_BYTES", len(body))
    monkeypatch.setattr(asmr_vad, "MODEL_SHA256", hashlib.sha256(body).hexdigest())
    return body


def test_download_follows_the_cdn_redirect_and_installs_a_verified_file(
        data_dir, small_model, monkeypatch):
    import requests
    calls = []

    def fake_get(url, **kw):
        calls.append((url, kw))
        if len(calls) == 1:
            return FakeResponse(302, location="https://us.aws.cdn.hf.co/blob?sig=1")
        return FakeResponse(200, small_model)
    monkeypatch.setattr(requests, "get", fake_get)
    progress = []
    asmr_vad.download_model(progress.append)
    assert open(asmr_vad.model_path(), "rb").read() == small_model
    assert progress[-1] == pytest.approx(1.0)
    assert all(kw["timeout"] and kw["allow_redirects"] is False for _u, kw in calls)
    assert not (data_dir / "model_cache" / "asmr_vad" / "model.onnx.part").exists()


def test_a_checksum_mismatch_installs_nothing(data_dir, small_model, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResponse(200, b"x" * len(small_model)))
    with pytest.raises(asmr_vad.AsmrVadUnavailable, match="checksum"):
        asmr_vad.download_model()
    assert not __import__("os").path.exists(asmr_vad.model_path())
    assert not __import__("os").path.exists(asmr_vad.model_path() + ".part")


@pytest.mark.parametrize("target", ["http://huggingface.co/x", "https://evil.example/x",
                                    "https://huggingface.co.evil.example/x",
                                    "https://user@huggingface.co/x"])
def test_a_redirect_off_the_allowed_hosts_is_refused(data_dir, small_model, monkeypatch, target):
    import requests
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        return FakeResponse(302, location=target)
    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(asmr_vad.AsmrVadUnavailable, match="redirected"):
        asmr_vad.download_model()
    assert len(seen) == 1


def test_a_network_error_message_has_no_url(data_dir, small_model, monkeypatch):
    import requests

    def boom(url, **kw):
        raise requests.ConnectionError("https://huggingface.co/secret?token=abc")
    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(asmr_vad.AsmrVadUnavailable) as err:
        asmr_vad.download_model()
    assert "token" not in str(err.value) and "http" not in str(err.value)


def test_coverage_reports_each_detector(monkeypatch):
    audio = np.zeros(16000 * 10, dtype=np.float32)
    monkeypatch.setattr(ab, "load_audio_16k", lambda path: audio)
    import vad_segments
    monkeypatch.setattr(vad_segments, "_silero_spans", lambda *a: [(0.0, 2.0)])
    out = asmr_vad.coverage("a.wav", load_asmr=lambda: (lambda a, sr: [(1.0, 6.0)]))
    assert out["total_s"] == 10.0
    assert out["standard"]["speech_s"] == pytest.approx(2.1)  # 100 ms padding each side, clamped to the clip
    assert out["asmr"]["speech_s"] == pytest.approx(5.2)
    assert out["asmr"]["coverage"] == pytest.approx(0.52)


def test_coverage_names_a_detector_that_cannot_run(monkeypatch):
    monkeypatch.setattr(ab, "load_audio_16k", lambda path: np.zeros(16000, dtype=np.float32))
    import vad_segments
    monkeypatch.setattr(vad_segments, "_silero_spans", lambda *a: [])

    def missing():
        raise asmr_vad.AsmrVadUnavailable("not downloaded")
    out = asmr_vad.coverage("a.wav", load_asmr=missing)
    assert out["asmr"] == {"unavailable": "not downloaded"}
    assert out["standard"]["speech_s"] == 0


class TestBackendDetector:
    @pytest.fixture(autouse=True)
    def _fakes(self, monkeypatch):
        monkeypatch.setattr(ab, "load_audio_16k", lambda path: np.zeros(16000 * 60, dtype="float32"))
        monkeypatch.setattr(ab, "extract_audio_slice", lambda a, s, e, out: open(out, "wb").close())
        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B":
                            type("M", (), {"transcribe": lambda self, audio, language: [
                                type("R", (), {"text": "x"})()
                                for _ in (audio if isinstance(audio, list) else [audio])]})())

    def test_asmr_uses_its_spans_and_standard_is_not_consulted(self, monkeypatch):
        import vad_segments
        monkeypatch.setattr(vad_segments, "_silero_spans",
                            lambda *a: pytest.fail("Silero must not run"))
        monkeypatch.setattr(asmr_vad, "load_detector", lambda: (lambda a, sr: [(5.0, 8.0)]))
        out = ab.Qwen3ASRVadBackend().transcribe("/a.wav", "ja", detector="asmr")
        assert out[0]["start"] == pytest.approx(4.9)

    def test_a_missing_model_falls_back_to_silero_with_a_notice(self, monkeypatch):
        import vad_segments

        def missing():
            raise asmr_vad.AsmrVadUnavailable("The ASMR voice detector model is not downloaded.")
        monkeypatch.setattr(asmr_vad, "load_detector", missing)
        monkeypatch.setattr(vad_segments, "_silero_spans", lambda *a: [(1.0, 3.0)])
        notes = []
        out = ab.Qwen3ASRVadBackend().transcribe("/a.wav", "ja", detector="asmr",
                                                 on_notice=notes.append)
        assert out and out[0]["start"] == pytest.approx(0.9)
        assert len(notes) == 1 and "not downloaded" in notes[0] and "Standard" in notes[0]

    def test_standard_never_touches_the_asmr_module(self, monkeypatch):
        import vad_segments
        monkeypatch.setattr(asmr_vad, "load_detector", lambda: pytest.fail("not asked for"))
        monkeypatch.setattr(vad_segments, "_silero_spans", lambda *a: [(1.0, 3.0)])
        assert ab.Qwen3ASRVadBackend().transcribe("/a.wav", "ja")


class TestOptions:
    @staticmethod
    def _ready(monkeypatch, ready=True):
        monkeypatch.setattr(asmr_vad, "status", lambda: {"onnxruntime_installed": ready,
                                                         "model_downloaded": ready})

    def test_auto_uses_asmr_only_for_a_japanese_asmr_title_with_everything_present(
            self, isolated_db, monkeypatch):
        self._ready(monkeypatch)
        assert asr_options_service.get_voice_detector() == "auto"
        resolve = asr_options_service.resolve_voice_detector
        assert resolve({"media_type": "asmr"}, "ja") == "auto_asmr"
        assert resolve({"media_type": "asmr", "source_language": "ja"}) == "auto_asmr"
        assert resolve({"media_type": "audio_drama"}, "ja") == "standard"
        assert resolve({}, "ja") == "standard"

    def test_auto_stays_standard_for_chinese_and_korean_asmr(self, isolated_db, monkeypatch):
        self._ready(monkeypatch)
        for language in ("zh", "ko"):
            assert asr_options_service.resolve_voice_detector(
                {"media_type": "asmr"}, language) == "standard"

    def test_auto_stays_standard_when_the_model_or_onnxruntime_is_missing(
            self, isolated_db, monkeypatch):
        self._ready(monkeypatch, ready=False)
        assert asr_options_service.resolve_voice_detector({"media_type": "asmr"}, "ja") == "standard"
        monkeypatch.setattr(asmr_vad, "status", lambda: {"onnxruntime_installed": True,
                                                         "model_downloaded": False})
        assert asr_options_service.resolve_voice_detector({"media_type": "asmr"}, "ja") == "standard"

    def test_an_explicit_choice_wins_over_the_media_type(self, isolated_db):
        asr_options_service.set_asr_options(voice_detector="standard")
        assert asr_options_service.resolve_voice_detector({"media_type": "asmr"}, "ja") == "standard"
        asr_options_service.set_asr_options(voice_detector="asmr")
        assert asr_options_service.resolve_voice_detector({"media_type": "audio_drama"}, "zh") == "asmr"

    def test_an_unknown_choice_is_refused_and_a_bad_stored_one_reads_as_auto(self, isolated_db):
        with pytest.raises(InvalidInputError):
            asr_options_service.set_asr_options(voice_detector="loud")
        import db
        db.set_app_setting(asr_options_service.VOICE_DETECTOR_KEY, "loud")
        assert asr_options_service.get_voice_detector() == "auto"

    def test_options_expose_booleans_only(self, isolated_db, data_dir):
        opts = asr_options_service.get_asr_options()
        assert opts["asmr_vad_model_downloaded"] is False
        assert str(data_dir) not in repr(opts)

    def test_download_is_refused_when_the_model_is_already_there(self, isolated_db, data_dir):
        path = asmr_vad.model_path()
        __import__("os").makedirs(__import__("os").path.dirname(path))
        open(path, "wb").write(b"x")
        with pytest.raises(ConflictError):
            asr_options_service.start_asmr_vad_download()

    def test_download_starts_a_job_that_calls_the_downloader(self, isolated_db, data_dir, monkeypatch):
        import background_jobs
        called = []
        monkeypatch.setattr(asmr_vad, "download_model", lambda **kw: called.append(kw))
        job_id = asr_options_service.ASMR_VAD_JOB_ID
        background_jobs.clear_job(job_id)
        assert asr_options_service.start_asmr_vad_download() == {"job_id": job_id, "started": True}
        for _ in range(100):
            if not background_jobs.is_running(job_id):
                break
            __import__("time").sleep(0.05)
        assert called


class _Rep:
    job_id = None

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
        return False

    def raise_if_cancelled(self):
        pass


class TestRunOutcome:
    """The whole run: the voice detector the options pick, the notice it may
    raise, and what that does to the job's outcome."""

    @pytest.fixture(autouse=True)
    def _fakes(self, monkeypatch, tmp_path):
        from services import transcribe_pipeline, transcribe_service
        import vad_segments
        monkeypatch.setattr(ab, "load_audio_16k", lambda path: np.zeros(16000 * 60, dtype="float32"))
        monkeypatch.setattr(ab, "extract_audio_slice", lambda a, s, e, out: open(out, "wb").close())
        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B", **_k:
                            type("M", (), {"transcribe": lambda self, audio, language: [
                                type("R", (), {"text": "x"})()
                                for _ in (audio if isinstance(audio, list) else [audio])]})())
        monkeypatch.setattr(vad_segments, "_silero_spans", lambda *a: [(1.0, 20.0)])
        monkeypatch.setattr(transcribe_service.core_module, "release_gpu_models", lambda: None)
        monkeypatch.setattr(transcribe_pipeline, "_audio_duration_seconds", lambda p: 30.0)
        self.tmp_path = tmp_path

    def _run(self, isolated_db, drama, language, choice="auto"):
        from services import jobs_service, transcribe_pipeline
        asr_options_service.set_asr_options(voice_detector=choice)
        detector = asr_options_service.resolve_voice_detector(drama, language)
        out = transcribe_pipeline._transcribe_pipeline(
            _Rep(), str(self.tmp_path / "a.wav"), "whisper", None, language, "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "", False,
            "qwen3_asr_vad", "whisper_diff", voice_detector=detector)
        return out, jobs_service.derive_outcome("done", None, out)[0]

    def test_auto_without_the_model_is_silent_and_the_outcome_is_ok(self, isolated_db, data_dir):
        out, outcome = self._run(isolated_db, {"media_type": "asmr"}, "ja")
        assert out["lines"] and not out["coverage_warning"]
        assert outcome == "ok"

    def test_auto_with_the_model_on_japanese_uses_the_asmr_detector(
            self, isolated_db, monkeypatch):
        monkeypatch.setattr(asmr_vad, "status", lambda: {"onnxruntime_installed": True,
                                                         "model_downloaded": True})
        monkeypatch.setattr(asmr_vad, "load_detector", lambda: (lambda a, sr: [(5.0, 28.0)]))
        out, outcome = self._run(isolated_db, {"media_type": "asmr"}, "ja")
        assert out["lines"][0].start == pytest.approx(4.9)
        assert outcome == "ok"

    def test_auto_on_chinese_uses_the_standard_detector(self, isolated_db, monkeypatch):
        monkeypatch.setattr(asmr_vad, "status", lambda: {"onnxruntime_installed": True,
                                                         "model_downloaded": True})
        monkeypatch.setattr(asmr_vad, "load_detector", lambda: pytest.fail("Japanese only"))
        out, outcome = self._run(isolated_db, {"media_type": "asmr"}, "zh")
        assert out["lines"][0].start == pytest.approx(0.9)
        assert outcome == "ok"

    def test_an_explicit_asmr_choice_without_the_model_warns(self, isolated_db, monkeypatch):
        def missing():
            raise asmr_vad.AsmrVadUnavailable("The ASMR voice detector model is not downloaded.")
        monkeypatch.setattr(asmr_vad, "load_detector", missing)
        out, outcome = self._run(isolated_db, {"media_type": "asmr"}, "ja", choice="asmr")
        assert "not downloaded" in out["coverage_warning"] and "Standard" in out["coverage_warning"]
        assert outcome == "partial"

    def test_auto_picked_detector_that_breaks_while_scoring_falls_back_silently(
            self, isolated_db, monkeypatch):
        monkeypatch.setattr(asmr_vad, "status", lambda: {"onnxruntime_installed": True,
                                                         "model_downloaded": True})
        monkeypatch.setattr(asmr_vad, "load_detector", lambda: _broken_detector())
        out, outcome = self._run(isolated_db, {"media_type": "asmr"}, "ja")
        assert out["lines"][0].start == pytest.approx(1.0 - 0.1)
        assert not out["coverage_warning"] and outcome == "ok"

    def test_explicit_asmr_that_breaks_while_scoring_falls_back_with_a_notice(
            self, isolated_db, monkeypatch):
        monkeypatch.setattr(asmr_vad, "load_detector", lambda: _broken_detector())
        out, outcome = self._run(isolated_db, {"media_type": "asmr"}, "ja", choice="asmr")
        assert out["lines"]
        assert "failed while running" in out["coverage_warning"]
        assert "Standard" in out["coverage_warning"] and outcome == "partial"


def _broken_detector():
    class Session:
        def get_inputs(self):
            return [type("I", (), {"name": "input_features"})()]

        def run(self, *_a):
            raise RuntimeError("/secret/path shape mismatch")
    det = asmr_vad.AsmrVad(Session())
    det._extractor = lambda window, padding: np.zeros((80, 3000), dtype=np.float32)
    return det


def test_a_scoring_failure_never_leaks_paths_and_asks_for_silero(monkeypatch):
    import vad_segments
    notes = []
    det = _broken_detector()
    det.on_failure = notes.append
    with pytest.raises(vad_segments.VadFnFailed):
        det(np.zeros(16000, dtype=np.float32), 16000)
    assert notes == ["The ASMR voice detector failed while running."]


def test_load_runs_one_silent_window_so_a_bad_extractor_is_caught_at_load(
        data_dir, monkeypatch):
    class Session:
        def get_inputs(self):
            return [type("I", (), {"name": "input_features"})()]

        def run(self, *_a):
            return [np.zeros((1, 1500), dtype=np.float32)]

    class Ort:
        InferenceSession = staticmethod(lambda *a, **k: Session())
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", Ort)
    _stub_faster_whisper(monkeypatch)
    path = asmr_vad.model_path()
    __import__("os").makedirs(__import__("os").path.dirname(path))
    open(path, "wb").write(b"x")

    def bad_features(self, window):
        raise ValueError("3001 mel frames")
    monkeypatch.setattr(asmr_vad.AsmrVad, "_features", bad_features)
    with pytest.raises(asmr_vad.AsmrVadUnavailable, match="could not be loaded"):
        asmr_vad.load_detector()
    monkeypatch.undo()


def test_coverage_names_a_detector_that_breaks_while_scoring(monkeypatch):
    monkeypatch.setattr(ab, "load_audio_16k", lambda path: np.zeros(16000, dtype=np.float32))
    import vad_segments
    monkeypatch.setattr(vad_segments, "_silero_spans", lambda *a: [])
    out = asmr_vad.coverage("a.wav", load_asmr=_broken_detector)
    assert out["asmr"] == {"unavailable": "The ASMR voice detector failed while running."}


def test_two_downloads_never_share_a_temp_file(data_dir, monkeypatch):
    import os
    body = b"abc"
    monkeypatch.setattr(asmr_vad, "MODEL_BYTES", len(body))
    monkeypatch.setattr(asmr_vad, "MODEL_SHA256", hashlib.sha256(body).hexdigest())
    parts = []

    class Resp:
        status_code = 200
        headers = {}

        def iter_content(self, _n):
            parts.append([f for f in os.listdir(os.path.dirname(asmr_vad.model_path()))
                          if f.endswith(".part")])
            yield body

        def close(self):
            pass
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    asmr_vad.download_model()
    asmr_vad.download_model()
    assert len(parts) == 2 and all(len(p) == 1 for p in parts)

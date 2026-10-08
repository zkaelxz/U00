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
    def test_auto_picks_asmr_only_for_the_asmr_media_type(self, isolated_db):
        assert asr_options_service.get_voice_detector() == "auto"
        assert asr_options_service.resolve_voice_detector({"media_type": "asmr"}) == "asmr"
        assert asr_options_service.resolve_voice_detector({"media_type": "audio_drama"}) == "standard"
        assert asr_options_service.resolve_voice_detector({}) == "standard"

    def test_an_explicit_choice_wins_over_the_media_type(self, isolated_db):
        asr_options_service.set_asr_options(voice_detector="standard")
        assert asr_options_service.resolve_voice_detector({"media_type": "asmr"}) == "standard"
        asr_options_service.set_asr_options(voice_detector="asmr")
        assert asr_options_service.resolve_voice_detector({"media_type": "audio_drama"}) == "asmr"

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

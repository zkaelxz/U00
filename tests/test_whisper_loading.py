"""Tests for choosing and loading the Whisper model: size, download errors and tokens, the cache, the device and GPU fallback."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import whisper_models



class TestModelDownloadErrorHandling:
    """A failed model download is a network problem, not an audio problem.
    Misclassifying it sends people debugging the wrong thing -- this is
    regression cover for the real error seen in the field."""

    def test_classifies_the_real_windows_dns_failure(self):
        from whisper_models import is_network_error
        exc = Exception("Got: ConnectError: [Errno 11004] getaddrinfo failed")
        assert is_network_error(exc) is True

    def test_classifies_common_network_failures(self):
        from whisper_models import is_network_error
        for msg in ("httpx.ConnectError", "Max retries exceeded",
                    "LocalEntryNotFoundError", "Connection timed out",
                    "Temporary failure in name resolution", "proxy error"):
            assert is_network_error(Exception(msg)) is True, msg

    def test_does_not_misclassify_real_audio_or_gpu_errors(self):
        from whisper_models import is_network_error
        for msg in ("Invalid audio file format", "CUDA out of memory",
                    "unsupported sample rate"):
            assert is_network_error(Exception(msg)) is False, msg

    def test_model_download_error_is_a_runtime_error(self):
        from whisper_models import ModelDownloadError
        assert issubclass(ModelDownloadError, RuntimeError)

    def test_cache_check_returns_bool_and_never_raises(self):
        from whisper_models import is_whisper_model_cached
        assert isinstance(is_whisper_model_cached("medium"), bool)
        assert isinstance(is_whisper_model_cached("nonexistent-size"), bool)


class TestDefaultWhisperSize:
    """large-v3 is the default (the owner's readability preference) and its label says
    so; turbo's label calls it the fast option, without the old claim that it is
    weaker on Japanese/Korean (the benchmarks don't show it)."""

    def test_default_whisper_size_is_large_v3(self):
        from whisper_models import DEFAULT_WHISPER_SIZE, WHISPER_MODELS
        assert DEFAULT_WHISPER_SIZE == "large-v3"
        assert DEFAULT_WHISPER_SIZE in WHISPER_MODELS
        assert "default" in WHISPER_MODELS["large-v3"]
        assert "default" not in WHISPER_MODELS["large-v3-turbo"]
        assert "default" not in WHISPER_MODELS["medium"]
        assert "weaker" not in WHISPER_MODELS["large-v3-turbo"]


class TestDnsDiagnosis:
    """A DNS blocker (Pi-hole, AdGuard) returns 0.0.0.0 for blocked domains
    rather than failing, which looks identical to a broken connection from
    inside a stack trace. Naming the difference matters: one is fixed by
    whitelisting a domain, the other by fixing your network."""

    def test_loopback_hostname_not_flagged_as_blocked(self):
        from whisper_models import diagnose_hostname
        assert diagnose_hostname("localhost")["status"] == "ok"

    def test_unresolvable_hostname_reports_no_dns(self):
        from whisper_models import diagnose_hostname
        assert diagnose_hostname("definitely-not-real-xyz123.invalid")["status"] == "no_dns"

    def test_result_always_has_expected_keys(self):
        from whisper_models import diagnose_hostname
        for host in ("localhost", "not-real-xyz123.invalid"):
            r = diagnose_hostname(host)
            for key in ("status", "hostname", "addresses", "detail"):
                assert key in r

    def test_status_is_one_of_three_known_values(self):
        from whisper_models import diagnose_hostname
        assert diagnose_hostname("localhost")["status"] in ("ok", "blocked", "no_dns")


class TestGpuInferenceFailureFallback:
    """Regression cover for a real, previously-uncaught crash: ctranslate2
    (which faster-whisper wraps) defers all CUDA initialization until the
    first actual transcription call -- constructing WhisperModel(device=
    "cuda") never touches the GPU, it just stores config. The earlier GPU
    fallback wrapped model CONSTRUCTION, which is why it never caught a
    real cuBLAS/CUDA failure: that can only ever surface at inference
    time. transcribe_for_timing now catches it there instead."""

    def test_classifies_the_exact_reported_error(self):
        from whisper_models import is_gpu_error
        exc = RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        assert is_gpu_error(exc) is True

    def test_classifies_common_cuda_failures(self):
        from whisper_models import is_gpu_error
        for msg in ("CUDA error: no kernel image is available",
                    "cuDNN error", "no CUDA-capable device is detected",
                    "CUDA out of memory"):
            assert is_gpu_error(RuntimeError(msg)) is True, msg

    def test_does_not_misclassify_network_or_genuine_errors(self):
        from whisper_models import is_gpu_error
        assert is_gpu_error(RuntimeError("Connection timed out")) is False
        assert is_gpu_error(ValueError("Invalid audio file format")) is False

    def _stub_faster_whisper(self):
        import sys, types
        calls = {"cuda_construct": 0, "cuda_transcribe": 0,
                 "cpu_construct": 0, "cpu_transcribe": 0}

        class FakeSegment:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        class FakeCudaModel:
            def __init__(self, *a, **k):
                calls["cuda_construct"] += 1
            def transcribe(self, audio_path, **kwargs):
                calls["cuda_transcribe"] += 1
                def gen():
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                    yield
                return gen(), None

        class FakeCpuModel:
            def __init__(self, *a, **k):
                calls["cpu_construct"] += 1
            def transcribe(self, audio_path, **kwargs):
                calls["cpu_transcribe"] += 1
                def gen():
                    yield FakeSegment(0.0, 1.0, "第一句")
                    yield FakeSegment(1.0, 2.0, "第二句")
                return gen(), None

        fake_fw = types.ModuleType("faster_whisper")
        def FakeWhisperModel(model_size, device="cpu", compute_type="int8"):
            return FakeCudaModel() if device == "cuda" else FakeCpuModel()
        fake_fw.WhisperModel = FakeWhisperModel
        sys.modules["faster_whisper"] = fake_fw
        return calls

    def test_gpu_inference_failure_falls_back_to_cpu_and_returns_real_segments(self):
        import whisper_transcribe
        calls = self._stub_faster_whisper()
        whisper_models._whisper_model_cache.clear()

        result = whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", use_gpu=True)

        assert result == [{"start": 0.0, "end": 1.0, "text": "第一句"},
                           {"start": 1.0, "end": 2.0, "text": "第二句"}]
        assert calls["cuda_construct"] == 1
        assert calls["cuda_transcribe"] == 1  # attempted, and failed
        assert calls["cpu_construct"] == 1    # fell back
        assert calls["cpu_transcribe"] == 1   # and actually transcribed there

    def test_the_broken_gpu_model_is_not_kept_cached(self):
        """A model whose CUDA failed at inference must not stay cached (and
        hold VRAM) for the next run to fail on again."""
        import whisper_transcribe
        self._stub_faster_whisper()
        whisper_models._whisper_model_cache.clear()
        # Another test in the same worker can leave a GPU entry here; only this run's is checked.
        whisper_models._whisper_device_info.clear()

        whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", use_gpu=True)

        assert not any(k.endswith("_gpu") for k in whisper_models._whisper_model_cache)
        assert not any(k.endswith("_gpu") for k in whisper_models._whisper_device_info)

    def test_the_gpu_model_is_freed_before_the_cpu_retry_runs(self):
        """The CPU retry can take an hour: the failed GPU model's VRAM must not
        stay held by the exception's traceback meanwhile."""
        import gc
        import sys
        import weakref
        import whisper_transcribe
        self._stub_faster_whisper()
        whisper_models._whisper_model_cache.clear()
        build = sys.modules["faster_whisper"].WhisperModel
        gpu_refs, alive_during_cpu_run = [], []

        def tracking_build(model_size, device="cpu", compute_type="int8"):
            model = build(model_size, device=device, compute_type=compute_type)
            if device == "cuda":
                gpu_refs.append(weakref.ref(model))

                def transcribe_on_gpu(audio_path, **kwargs):
                    # faster-whisper's segment generator holds its model, as here.
                    def gen(owner=model):
                        raise RuntimeError("CUDA failed with error out of memory")
                        yield owner
                    return gen(), None
                model.transcribe = transcribe_on_gpu
            else:
                real = model.transcribe

                def transcribe(audio_path, **kwargs):
                    gc.collect()
                    alive_during_cpu_run.append(gpu_refs[0]() is not None)
                    return real(audio_path, **kwargs)
                model.transcribe = transcribe
            return model
        sys.modules["faster_whisper"].WhisperModel = tracking_build

        result = whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", use_gpu=True)

        assert [s["text"] for s in result] == ["第一句", "第二句"]
        assert alive_during_cpu_run == [False]

    def test_caller_is_notified_when_fallback_occurs(self):
        import whisper_transcribe
        self._stub_faster_whisper()
        whisper_models._whisper_model_cache.clear()

        notified = []
        whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", use_gpu=True,
                                    on_gpu_fallback=lambda exc: notified.append(str(exc)))
        assert len(notified) == 1
        assert "cublas64_12.dll" in notified[0]

    def test_no_fallback_when_gpu_was_never_requested(self):
        import sys, types
        calls = {"cpu_transcribe": 0}

        class FakeSegment:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        class FakeCpuModel:
            def __init__(self, *a, **k):
                pass
            def transcribe(self, audio_path, **kwargs):
                calls["cpu_transcribe"] += 1
                def gen():
                    yield FakeSegment(0.0, 1.0, "text")
                return gen(), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeCpuModel()
        sys.modules["faster_whisper"] = fake_fw

        import whisper_transcribe
        whisper_models._whisper_model_cache.clear()
        notified = []
        whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", use_gpu=False,
                                    on_gpu_fallback=lambda exc: notified.append(exc))
        assert notified == []
        assert calls["cpu_transcribe"] == 1

    def test_non_gpu_error_at_inference_time_still_propagates(self):
        import sys, types
        class FakeModel:
            def __init__(self, *a, **k):
                pass
            def transcribe(self, audio_path, **kwargs):
                def gen():
                    raise ValueError("corrupt audio stream")
                    yield
                return gen(), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeModel()
        sys.modules["faster_whisper"] = fake_fw

        import whisper_transcribe
        whisper_models._whisper_model_cache.clear()
        try:
            whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", use_gpu=True)
            assert False, "a genuine non-GPU error must still propagate, not be swallowed"
        except ValueError as e:
            assert "corrupt audio stream" in str(e)

    def test_a_file_pyav_cannot_decode_fails_with_a_plain_sentence(self, monkeypatch):
        import sys, types
        # Stands in for av.error.InvalidDataError, whose text is "[Errno 1094995529] Invalid
        # data found when processing input: '...'".
        InvalidDataError = type("InvalidDataError", (ValueError,), {"__module__": "av.error"})

        class FakeModel:
            def __init__(self, *a, **k):
                pass
            def transcribe(self, audio_path, **kwargs):
                raise InvalidDataError(1094995529, "Invalid data found when processing input")

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeModel()
        monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

        import whisper_transcribe
        whisper_models._whisper_model_cache.clear()
        with pytest.raises(whisper_transcribe.UnreadableAudioError) as caught:
            whisper_transcribe.transcribe_for_timing("/fake/audio.wav", use_gpu=False)
        assert str(caught.value) == whisper_transcribe.UNREADABLE_AUDIO
        assert "Errno" not in str(caught.value)

    def test_a_missing_file_is_not_called_damaged(self, monkeypatch):
        import sys, types
        AvFileNotFound = type("FileNotFoundError", (FileNotFoundError,), {"__module__": "av.error"})

        class FakeModel:
            def __init__(self, *a, **k):
                pass
            def transcribe(self, audio_path, **kwargs):
                raise AvFileNotFound(2, "No such file or directory")

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeModel()
        monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

        import whisper_transcribe
        whisper_models._whisper_model_cache.clear()
        with pytest.raises(FileNotFoundError):
            whisper_transcribe.transcribe_for_timing("/fake/audio.wav", use_gpu=False)


class TestWhisperDeviceReporting:
    def _stub_faster_whisper(self, monkeypatch, cuda_error):
        import sys, types

        class FakeWhisperModel:
            def __init__(self, target, device, compute_type):
                if device == "cuda" and cuda_error:
                    raise RuntimeError(cuda_error)
                self.device = device

        mod = types.ModuleType("faster_whisper")
        mod.WhisperModel = FakeWhisperModel
        monkeypatch.setitem(sys.modules, "faster_whisper", mod)
        # A card with float16 by default, so a real ctranslate2 on the test
        # PC (a GTX 10xx has no float16) cannot change the answer.
        ct2 = types.ModuleType("ctranslate2")
        ct2.get_supported_compute_types = lambda device: {"float32", "int8", "int8_float32",
                                                          "float16", "int8_float16"}
        monkeypatch.setitem(sys.modules, "ctranslate2", ct2)
        monkeypatch.setattr(whisper_models, "_whisper_model_cache", {})
        monkeypatch.setattr(whisper_models, "_whisper_device_info", {})

    def test_gpu_load_success_reports_gpu(self, monkeypatch):
        self._stub_faster_whisper(monkeypatch, None)
        whisper_models.load_whisper_model("tiny", use_gpu=True)
        info = whisper_models.get_whisper_device_info("tiny", use_gpu=True)
        assert info == {"device": "cuda", "compute_type": "float16", "gpu_error": None}
        assert whisper_models.describe_whisper_device(info) == "Using GPU (float16)"

    @pytest.mark.parametrize("supported, chosen", [
        ({"float32", "int8", "int8_float32"}, "int8_float32"),   # Pascal, compute 6.x
        ({"float32", "int8", "int8_float32", "float16", "int8_float16"}, "float16"),
        ({"float32"}, "float32"),
    ])
    def test_gpu_load_uses_a_compute_type_the_card_supports(self, monkeypatch, supported, chosen):
        import sys, types
        self._stub_faster_whisper(monkeypatch, None)
        built = []
        real = sys.modules["faster_whisper"].WhisperModel
        sys.modules["faster_whisper"].WhisperModel = (
            lambda target, device, compute_type, **k: built.append(compute_type) or real(
                target, device, compute_type))
        ct2 = types.ModuleType("ctranslate2")
        ct2.get_supported_compute_types = lambda device: supported if device == "cuda" else set()
        monkeypatch.setitem(sys.modules, "ctranslate2", ct2)
        whisper_models.load_whisper_model("tiny", use_gpu=True)
        assert built == [chosen]
        assert whisper_models.get_whisper_device_info("tiny", use_gpu=True)["compute_type"] == chosen

    def test_compute_type_probe_failure_keeps_float16(self, monkeypatch):
        import sys, types
        ct2 = types.ModuleType("ctranslate2")
        ct2.get_supported_compute_types = lambda device: (_ for _ in ()).throw(RuntimeError("no driver"))
        monkeypatch.setitem(sys.modules, "ctranslate2", ct2)
        assert whisper_models.gpu_compute_type() == "float16"

    def test_gpu_failure_falls_back_and_reports_redacted_reason(self, monkeypatch):
        self._stub_faster_whisper(
            monkeypatch, "Library cublas64_12.dll is not found key=sk-abcdefghijklmnopqrstuvwx")
        model = whisper_models.load_whisper_model("tiny", use_gpu=True)
        assert model.device == "cpu"
        info = whisper_models.get_whisper_device_info("tiny", use_gpu=True)
        assert info["device"] == "cpu"
        assert "cublas64_12.dll" in info["gpu_error"]
        assert "sk-abcdefghijklmnopqrstuvwx" not in info["gpu_error"]
        text = whisper_models.describe_whisper_device(info)
        assert text.startswith("GPU unavailable (") and text.endswith("); using CPU")

    def test_default_setting_on_a_machine_without_a_gpu_still_transcribes_on_cpu(
            self, isolated_db, monkeypatch):
        from services import settings_service
        self._stub_faster_whisper(
            monkeypatch, "CUDA failed with error CUDA driver version is insufficient")
        use_gpu = settings_service.get("use_gpu")
        assert use_gpu is True
        model = whisper_models.load_whisper_model("tiny", use_gpu=use_gpu)
        assert model.device == "cpu"
        info = whisper_models.get_whisper_device_info("tiny", use_gpu=use_gpu)
        assert info["device"] == "cpu" and info["gpu_error"]

    def test_cpu_request_reports_cpu(self, monkeypatch):
        self._stub_faster_whisper(monkeypatch, None)
        whisper_models.load_whisper_model("tiny", use_gpu=False)
        assert whisper_models.describe_whisper_device(
            whisper_models.get_whisper_device_info("tiny")) == "Using CPU (int8)"

    def test_gpu_status_never_raises_and_reports_both_probes(self, monkeypatch):
        import sys, types
        ct2 = types.ModuleType("ctranslate2")
        ct2.get_cuda_device_count = lambda: 1
        torch = types.ModuleType("torch")
        torch.cuda = types.SimpleNamespace(is_available=lambda: False)
        monkeypatch.setitem(sys.modules, "ctranslate2", ct2)
        monkeypatch.setitem(sys.modules, "torch", torch)
        assert whisper_models.gpu_status() == {"ctranslate2_cuda_devices": 1,
                                     "torch_cuda_available": False, "errors": []}
        ct2.get_cuda_device_count = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        status = whisper_models.gpu_status()
        assert status["ctranslate2_cuda_devices"] is None
        assert status["errors"] and "boom" in status["errors"][0]


class TestWhisperModelCache:
    @staticmethod
    def _hub(monkeypatch, tmp_path):
        monkeypatch.setenv("HF_HOME", str(tmp_path))
        hub = tmp_path / "hub"
        hub.mkdir()
        return hub

    @staticmethod
    def _repo(hub, name, weights=True):
        import model_pins
        repo = name.removeprefix("models--").replace("--", "/")
        snap = hub / name / "snapshots" / model_pins.REVISIONS.get(repo, "abc123")
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}")
        if weights:
            from lib.ct2_repo import CT2_REQUIRED
            for name in CT2_REQUIRED:
                (snap / name).write_bytes(b"x")

    def test_turbo_alone_does_not_make_large_v3_cached(self, monkeypatch, tmp_path):
        hub = self._hub(monkeypatch, tmp_path)
        self._repo(hub, "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo")
        assert whisper_models.is_whisper_model_cached("large-v3-turbo")
        assert not whisper_models.is_whisper_model_cached("large-v3")

    def test_only_the_repo_the_loader_uses_is_cached(self, monkeypatch, tmp_path):
        hub = self._hub(monkeypatch, tmp_path)
        self._repo(hub, "models--someone--faster-whisper-large-v3")
        assert not whisper_models.is_whisper_model_cached("large-v3")
        self._repo(hub, "models--Systran--faster-whisper-large-v3")
        assert whisper_models.is_whisper_model_cached("large-v3")

    def test_partial_download_is_not_cached(self, monkeypatch, tmp_path):
        hub = self._hub(monkeypatch, tmp_path)
        self._repo(hub, "models--Systran--faster-whisper-small", weights=False)
        (hub / "models--Systran--faster-whisper-medium").mkdir()
        assert not whisper_models.is_whisper_model_cached("small")
        assert not whisper_models.is_whisper_model_cached("medium")


class TestWhisperDownloadToken:
    def _stub(self, monkeypatch, snapshot_download):
        import types
        built = []
        fw = types.ModuleType("faster_whisper")
        fw.WhisperModel = lambda target, device, compute_type: built.append(target) or object()
        monkeypatch.setitem(sys.modules, "faster_whisper", fw)
        monkeypatch.setitem(sys.modules, "huggingface_hub",
                            types.SimpleNamespace(snapshot_download=snapshot_download))
        monkeypatch.setattr(whisper_models, "_download_model", whisper_models._download_model.real)
        monkeypatch.setattr(whisper_models, "_whisper_model_cache", {})
        monkeypatch.setattr(whisper_models, "_whisper_device_info", {})
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        return built

    def test_token_goes_to_the_download_not_the_environment(self, monkeypatch):
        calls = []

        def snapshot_download(repo, token=None, **kw):
            calls.append((repo, token))
            return "/cache/" + repo
        built = self._stub(monkeypatch, snapshot_download)
        whisper_models.load_whisper_model("small", hf_token="hf_first")
        assert calls == [("Systran/faster-whisper-small", "hf_first")]
        assert built == ["/cache/Systran/faster-whisper-small"]
        assert "HF_TOKEN" not in os.environ

    def test_a_changed_token_is_used_without_a_restart(self, monkeypatch):
        tokens = []
        self._stub(monkeypatch, lambda repo, token=None, **kw: tokens.append(token) or "/m")
        whisper_models.load_whisper_model("small", hf_token="hf_first")
        monkeypatch.setattr(whisper_models, "_whisper_model_cache", {})
        whisper_models.load_whisper_model("small", hf_token="hf_second")
        assert tokens == ["hf_first", "hf_second"]

    def test_no_token_still_downloads_the_pinned_revision(self, monkeypatch):
        # faster-whisper's own download would fetch the branch head.
        import model_pins
        calls = []
        built = self._stub(monkeypatch, lambda repo, **kw: calls.append(kw) or "/m")
        whisper_models.load_whisper_model("small")
        assert [(c["token"], c["revision"]) for c in calls] == [
            (None, model_pins.REVISIONS["Systran/faster-whisper-small"])]
        assert built == ["/m"]


class TestWhisperGpuRuntime:
    @staticmethod
    def _windows_with_gpu(monkeypatch, devices=1):
        import sys, types
        monkeypatch.setattr(whisper_models.sys, "platform", "win32")
        ct2 = types.ModuleType("ctranslate2")
        ct2.get_cuda_device_count = lambda: devices
        monkeypatch.setitem(sys.modules, "ctranslate2", ct2)

    def test_present(self, monkeypatch):
        self._windows_with_gpu(monkeypatch)
        loaded = []
        assert whisper_models.whisper_gpu_runtime(loader=loaded.append) == {"status": "ok", "reason": None}
        assert loaded == ["cublas64_12.dll"]

    def test_missing_reason_has_no_path_or_raw_error(self, monkeypatch):
        self._windows_with_gpu(monkeypatch)

        def loader(name):
            raise OSError(r"[WinError 126] C:\Users\me\AppData\cublas64_12.dll not found")

        result = whisper_models.whisper_gpu_runtime(loader=loader)
        assert result["status"] == "missing"
        assert result["reason"] == whisper_models.GPU_RUNTIME_MISSING_REASON
        assert "\\" not in result["reason"] and ":" not in result["reason"] and "WinError" not in result["reason"]

    def test_unknown_off_windows(self, monkeypatch):
        monkeypatch.setattr(whisper_models.sys, "platform", "linux")
        assert whisper_models.whisper_gpu_runtime(loader=lambda n: 1 / 0) == {"status": "unknown", "reason": None}

    def test_unknown_without_ctranslate2(self, monkeypatch):
        import sys
        monkeypatch.setattr(whisper_models.sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "ctranslate2", None)
        assert whisper_models.whisper_gpu_runtime(loader=lambda n: 1 / 0)["status"] == "unknown"

    def test_unknown_without_cuda_device(self, monkeypatch):
        self._windows_with_gpu(monkeypatch, devices=0)
        assert whisper_models.whisper_gpu_runtime(loader=lambda n: 1 / 0)["status"] == "unknown"

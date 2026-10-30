"""
tests/test_asr_backend.py -- asr_backend.py's pluggable transcription
backends: WhisperBackend's passthrough to the existing (unchanged)
core.transcribe_for_timing(), and Qwen3ASRBackend's segment-reuse/
re-transcription logic, exercised against fake qwen3_native/torch modules
and a monkeypatched audio slicer, since the real model needs a GPU/
network this sandbox doesn't have.
"""
import whisper_models
import os
import sys
import types
import importlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeResult:
    def __init__(self, text, language=None):
        self.text = text
        self.language = language


class TestWhisperBackendIsAPureWrapper:
    def test_forwards_all_arguments_to_transcribe_for_timing(self, monkeypatch):
        import asr_backend as ab

        captured = {}

        def fake_transcribe_for_timing(audio_path, model_size, **kwargs):
            captured["audio_path"] = audio_path
            captured["model_size"] = model_size
            captured.update(kwargs)
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]

        monkeypatch.setattr(ab, "transcribe_for_timing", fake_transcribe_for_timing)
        backend = ab.WhisperBackend()
        result = backend.transcribe(
            "/fake.wav", "zh", whisper_size="large-v3", use_gpu=True,
            initial_prompt="张三", beam_size=8, min_silence_duration_ms=800,
        )
        assert result == [{"start": 0.0, "end": 1.0, "text": "hi"}]
        assert captured["audio_path"] == "/fake.wav"
        assert captured["model_size"] == "large-v3"
        assert captured["language"] == "zh"
        assert captured["use_gpu"] is True
        assert captured["initial_prompt"] == "张三"
        assert captured["beam_size"] == 8
        assert captured["min_silence_duration_ms"] == 800

    def test_name_attribute(self):
        import asr_backend as ab
        assert ab.WhisperBackend.name == "whisper"


class TestQwen3ASRBackendValidation:
    def test_rejects_unsupported_language(self):
        import asr_backend as ab
        backend = ab.Qwen3ASRBackend()
        with pytest.raises(ValueError, match="doesn't cover language"):
            backend.transcribe("/fake.wav", "en", whisper_segments=[{"start": 0, "end": 1, "text": "hi"}])

    def test_rejects_empty_segments(self):
        import asr_backend as ab
        backend = ab.Qwen3ASRBackend()
        with pytest.raises(ValueError, match="re-transcribes Whisper's VAD segments"):
            backend.transcribe("/fake.wav", "zh", whisper_segments=[])


class TestQwen3ASRBackendTranscription:
    def _stub_audio_slicing(self, monkeypatch, ab):
        monkeypatch.setattr(
            ab, "extract_audio_slice",
            lambda audio_path, start, end, out_path: open(out_path, "wb").close(),
        )

    def test_replaces_text_but_keeps_whisper_timing(self, monkeypatch):
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)

        whisper_segments = [
            {"start": 0.0, "end": 2.0, "text": "whisper guessed this wrong"},
            {"start": 2.0, "end": 5.0, "text": "and this too"},
        ]
        calls = []

        class FakeModel:
            def transcribe(self, audio, language):
                calls.append(language)
                return [FakeResult("qwen3 text")]

        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B": FakeModel())

        backend = ab.Qwen3ASRBackend()
        result = backend.transcribe("/fake.wav", "ja", whisper_segments=whisper_segments)

        # Timing is untouched -- only text changes (see module docstring on why).
        assert [(r["start"], r["end"]) for r in result] == [(0.0, 2.0), (2.0, 5.0)]
        assert all(r["text"] == "qwen3 text" for r in result)
        assert calls == ["Japanese", "Japanese"]

    def test_rewritten_text_drops_the_whisper_words_that_no_longer_match_it(self, monkeypatch):
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)
        words = [{"start": 0.0, "end": 1.0, "word": "whisper"}]
        whisper_segments = [{"start": 0.0, "end": 2.0, "text": "whisper", "words": words}]

        class FakeModel:
            def transcribe(self, audio, language):
                return [FakeResult("qwen3 text")]

        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B": FakeModel())
        result = ab.Qwen3ASRBackend().transcribe("/fake.wav", "ja", whisper_segments=whisper_segments)
        assert result == [{"start": 0.0, "end": 2.0, "text": "qwen3 text"}]

    def test_oversized_segment_falls_back_to_whisper_text_for_that_segment_only(self, monkeypatch):
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)

        whisper_segments = [
            {"start": 0.0, "end": 400.0, "text": "probably a VAD-merge artifact"},
            {"start": 400.0, "end": 402.0, "text": "a normal short line"},
        ]
        calls = []

        class FakeModel:
            def transcribe(self, audio, language):
                calls.append(audio)
                return [FakeResult("qwen3 text")]

        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B": FakeModel())

        backend = ab.Qwen3ASRBackend()
        result = backend.transcribe("/fake.wav", "zh", whisper_segments=whisper_segments)

        # Oversized segment kept its original Whisper text untouched...
        assert result[0]["text"] == "probably a VAD-merge artifact"
        # ...but the normal one was re-transcribed, and the model was only
        # ever called once (not for the skipped oversized segment).
        assert result[1]["text"] == "qwen3 text"
        assert len(calls) == 1

    def test_progress_cb_reports_the_share_of_segments_done_per_batch(self, monkeypatch):
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)
        segs = [{"start": float(i), "end": i + 1.0, "text": "w"} for i in range(3)]

        class FakeModel:
            def transcribe(self, audio, language):
                return [FakeResult("q")]

        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B": FakeModel())
        seen = []
        ab.Qwen3ASRBackend().transcribe("/fake.wav", "zh", whisper_segments=segs,
                                        progress_cb=seen.append)
        assert seen == pytest.approx([1 / 3, 2 / 3, 1.0])

    def test_empty_model_result_keeps_whispers_text(self, monkeypatch):
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)

        class EmptyModel:
            def transcribe(self, audio, language):
                return []

        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B": EmptyModel())
        backend = ab.Qwen3ASRBackend()
        result = backend.transcribe("/fake.wav", "ko",
                                     whisper_segments=[{"start": 0.0, "end": 1.0, "text": "x"}])
        assert result[0]["text"] == "x"

    def test_empty_model_result_flags_the_line_it_kept_whispers_text_for(self, monkeypatch):
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)

        class MixedModel:
            def transcribe(self, audio, language):
                return [FakeResult("")] if audio.endswith("seg_0.wav") else [FakeResult("qwen")]

        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B": MixedModel())
        result = ab.Qwen3ASRBackend().transcribe(
            "/fake.wav", "ko", whisper_segments=[{"start": 0.0, "end": 1.0, "text": "x"},
                                                 {"start": 1.0, "end": 2.0, "text": "y"}])
        assert (result[0]["text"], result[0]["flag"]) == ("x", "source_suspect")
        assert result[0]["flag_note"]
        assert "flag" not in result[1]


    def test_a_phrase_qwen_loops_on_is_cut_to_one_copy(self, monkeypatch):
        """Qwen3-ASR gets stuck on a phrase like Whisper does; its text gets
        the same in-segment loop collapse Whisper's text already has."""
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)

        class LoopingModel:
            def transcribe(self, audio, language):
                return [FakeResult("好的我知道了" * 6)]

        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B": LoopingModel())
        result = ab.Qwen3ASRBackend().transcribe(
            "/fake.wav", "zh", whisper_segments=[{"start": 0.0, "end": 5.0, "text": "好的"}])
        assert result[0]["text"] == "好的我知道了"


    def test_gpu_out_of_memory_mid_run_finishes_on_the_cpu(self, monkeypatch):
        """A load can fit and a long segment still run the card out of
        memory: the rest of the run moves to the CPU instead of failing."""
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)
        loads, fallbacks, devices = [], [], []

        class GpuModel:
            def transcribe(self, audio, language):
                if audio.endswith("seg_1.wav"):
                    raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
                return [FakeResult("gpu text")]

        class CpuModel:
            def transcribe(self, audio, language):
                return [FakeResult("cpu text")]

        def load(use_gpu=False, model_size="1.7B", on_device=None, on_gpu_fallback=None):
            loads.append(use_gpu)
            if on_device:
                on_device("GPU" if use_gpu else "CPU")
            return GpuModel() if use_gpu else CpuModel()

        monkeypatch.setattr(ab, "load_qwen3_asr", load)
        segs = [{"start": float(i), "end": i + 1.0, "text": "w"} for i in range(3)]
        result = ab.Qwen3ASRBackend().transcribe(
            "/fake.wav", "zh", whisper_segments=segs, use_gpu=True,
            on_device=lambda task, label: devices.append(label),
            on_gpu_fallback=lambda task, exc: fallbacks.append((task, str(exc))))

        assert [r["text"] for r in result] == ["gpu text", "cpu text", "cpu text"]
        assert loads == [True, False]
        assert devices == ["GPU", "CPU"]
        assert fallbacks and fallbacks[0][0] == ab.GPU_FAILED_MID_RUN_TASK
        assert "out of memory" in fallbacks[0][1]
        assert whisper_models.gpu_fallback_notice(fallbacks[0][0], "x").startswith(
            "Part of Qwen3-ASR ran on the CPU")

    def test_the_gpu_model_is_let_go_before_the_cpu_copy_loads(self, monkeypatch):
        """The card is shared with other programs: a GPU model still
        referenced (by a caller's variable or the error's traceback) would keep
        its memory for the rest of the run."""
        import gc
        import weakref
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)
        gpu_ref = []

        class GpuModel:
            def transcribe(self, audio, language):
                raise RuntimeError("CUDA out of memory")

        class CpuModel:
            def transcribe(self, audio, language):
                return [FakeResult("cpu text")]

        def load(use_gpu=False, model_size="1.7B", **k):
            if use_gpu:
                model = GpuModel()
                gpu_ref.append(weakref.ref(model))
                ab._asr_model_cache[f"{model_size}_gpu"] = model
                return model
            gc.collect()
            assert gpu_ref[0]() is None, "the GPU model was still referenced"
            return CpuModel()

        monkeypatch.setattr(ab, "_asr_model_cache", {})
        monkeypatch.setattr(ab, "load_qwen3_asr", load)
        result = ab.Qwen3ASRBackend().transcribe(
            "/fake.wav", "zh", whisper_segments=[{"start": 0.0, "end": 1.0, "text": "w"}],
            use_gpu=True)
        assert [r["text"] for r in result] == ["cpu text"]
        assert "1.7B_gpu" not in ab._asr_model_cache

    def test_a_run_that_already_loaded_on_the_cpu_never_reports_a_second_fallback(
            self, monkeypatch):
        """The GPU was asked for, but the load already fell back: an error
        that looks like a GPU one is not a reason to reload or report again."""
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)
        cpu_model = type("CpuModel", (), {"transcribe": lambda self, audio, language: (
            _ for _ in ()).throw(RuntimeError("CUDA error: device-side assert"))})()
        loads, fallbacks = [], []

        def load(use_gpu=False, model_size="1.7B", **k):
            loads.append(use_gpu)
            ab._asr_model_cache[f"{model_size}_cpu"] = cpu_model
            return cpu_model

        monkeypatch.setattr(ab, "_asr_model_cache", {})
        monkeypatch.setattr(ab, "load_qwen3_asr", load)
        with pytest.raises(RuntimeError, match="device-side"):
            ab.Qwen3ASRBackend().transcribe(
                "/fake.wav", "zh", whisper_segments=[{"start": 0.0, "end": 1.0, "text": "w"}],
                use_gpu=True, on_gpu_fallback=lambda task, exc: fallbacks.append(task))
        assert loads == [True] and fallbacks == []

    def test_a_non_gpu_error_mid_run_still_fails_the_run(self, monkeypatch):
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)

        class BrokenModel:
            def transcribe(self, audio, language):
                raise ValueError("bad audio")

        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B", **k: BrokenModel())
        with pytest.raises(ValueError):
            ab.Qwen3ASRBackend().transcribe(
                "/fake.wav", "zh", whisper_segments=[{"start": 0.0, "end": 1.0, "text": "w"}],
                use_gpu=True)


class TestLoadQwen3Asr:
    def _install_fake_qwen_asr(self, from_pretrained):
        """from_pretrained keeps the (model_id, dtype, device_map, max_new_tokens)
        shape the tests below were written with; the native loader is called
        with (model_id, device, dtype)."""
        fake_torch = types.ModuleType("torch")
        fake_torch.bfloat16 = "bfloat16"
        fake_torch.float16 = "float16"
        fake_torch.cuda = types.SimpleNamespace(is_bf16_supported=lambda including_emulation=True: True)
        sys.modules["torch"] = fake_torch

        import qwen3_native
        self._native_saved = (qwen3_native.NativeQwen3ASR.__dict__["from_pretrained"],
                              qwen3_native.installed_transformers_version)
        qwen3_native.NativeQwen3ASR.from_pretrained = staticmethod(
            lambda model_id, device, dtype, max_new_tokens=256:
            from_pretrained(model_id, dtype, device, max_new_tokens))
        qwen3_native.installed_transformers_version = lambda: "5.19.0"

    def teardown_method(self):
        sys.modules.pop("torch", None)
        saved = getattr(self, "_native_saved", None)
        if saved:
            import qwen3_native
            qwen3_native.NativeQwen3ASR.from_pretrained = saved[0]
            qwen3_native.installed_transformers_version = saved[1]
        import asr_backend
        asr_backend._asr_model_cache.clear()

    def test_loads_and_caches_per_model_size_and_device(self):
        calls = []

        def from_pretrained(model_id, dtype, device_map, max_new_tokens):
            calls.append((model_id, device_map))
            return object()

        self._install_fake_qwen_asr(from_pretrained)
        import asr_backend
        importlib.reload(asr_backend)
        asr_backend._asr_model_cache.clear()

        m1 = asr_backend.load_qwen3_asr(use_gpu=False, model_size="1.7B")
        m2 = asr_backend.load_qwen3_asr(use_gpu=False, model_size="1.7B")
        m3 = asr_backend.load_qwen3_asr(use_gpu=False, model_size="0.6B")
        assert m1 is m2
        assert m1 is not m3
        assert calls == [("Qwen/Qwen3-ASR-1.7B-hf", "cpu"), ("Qwen/Qwen3-ASR-0.6B-hf", "cpu")]

    def test_gpu_error_falls_back_to_cpu(self):
        calls = []

        def from_pretrained(model_id, dtype, device_map, max_new_tokens):
            calls.append(device_map)
            if device_map == "cuda:0":
                raise RuntimeError("no CUDA-capable device is detected")
            return object()

        self._install_fake_qwen_asr(from_pretrained)
        import asr_backend
        importlib.reload(asr_backend)
        asr_backend._asr_model_cache.clear()

        model = asr_backend.load_qwen3_asr(use_gpu=True)
        assert model is not None
        assert calls == ["cuda:0", "cpu"]

    def _load(self, from_pretrained, use_gpu):
        self._install_fake_qwen_asr(from_pretrained)
        import asr_backend
        importlib.reload(asr_backend)
        asr_backend._asr_model_cache.clear()
        seen = {"device": [], "fallback": []}
        asr_backend.load_qwen3_asr(use_gpu=use_gpu, on_device=seen["device"].append,
                                   on_gpu_fallback=seen["fallback"].append)
        return seen

    def test_cuda_shaped_error_reports_cpu_and_the_redacted_reason(self):
        def from_pretrained(model_id, dtype, device_map, max_new_tokens):
            if device_map == "cuda:0":
                raise RuntimeError("CUBLAS_STATUS_NOT_INITIALIZED at C:\\Users\\me\\x.dll "
                                   "key=sk-abcdefghijklmnopqrstuvwxyz123456")
            return object()
        seen = self._load(from_pretrained, use_gpu=True)
        assert seen["device"] == ["CPU"]
        reason = whisper_models.short_reason(seen["fallback"][0])
        assert "CUBLAS_STATUS_NOT_INITIALIZED" in reason
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in reason
        assert "\n" not in reason and len(reason) <= 200

    def test_gpu_that_loads_reports_gpu_with_no_fallback(self):
        seen = self._load(lambda model_id, dtype, device_map, max_new_tokens: object(), use_gpu=True)
        assert seen == {"device": ["GPU"], "fallback": []}

    def test_cpu_request_reports_cpu_with_no_fallback(self):
        seen = self._load(lambda model_id, dtype, device_map, max_new_tokens: object(), use_gpu=False)
        assert seen == {"device": ["CPU"], "fallback": []}

    def test_a_cached_model_still_reports_its_device(self):
        import asr_backend
        seen = self._load(lambda model_id, dtype, device_map, max_new_tokens: object(), use_gpu=True)
        again = []
        asr_backend.load_qwen3_asr(use_gpu=True, on_device=again.append)
        assert seen["device"] == again == ["GPU"]

    def test_network_error_raises_model_download_error(self):
        def from_pretrained(model_id, dtype, device_map, max_new_tokens):
            raise OSError("Connection timed out while downloading from huggingface.co")

        self._install_fake_qwen_asr(from_pretrained)
        import asr_backend
        importlib.reload(asr_backend)
        asr_backend._asr_model_cache.clear()

        from whisper_models import ModelDownloadError
        with pytest.raises(ModelDownloadError):
            asr_backend.load_qwen3_asr(use_gpu=False)


class TestWholeFileAudioGuard:
    """The speech detectors read the whole file as 16 kHz float32 (about
    550 MB an hour at the decoding peak). A file too long for the free RAM is refused up front in
    plain words instead of running out of memory mid-job."""

    def _setup(self, monkeypatch, seconds, ram):
        import asr_backend as ab
        import media_inspect
        import memory_headroom
        decoded = []
        monkeypatch.setattr(media_inspect, "duration_seconds", lambda path, **k: seconds)
        monkeypatch.setattr(memory_headroom, "_read_ram_mb", lambda: ram)
        fake = types.ModuleType("faster_whisper.audio")
        fake.decode_audio = lambda path, sampling_rate: decoded.append(path) or "audio"
        monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
        monkeypatch.setitem(sys.modules, "faster_whisper.audio", fake)
        return ab, decoded

    def test_a_file_too_long_for_free_ram_is_refused_before_decoding(self, monkeypatch):
        import memory_headroom
        ab, decoded = self._setup(monkeypatch, 10 * 3600, (16000.0, 2000.0))
        with pytest.raises(memory_headroom.HeadroomError, match="about 10.0 hours"):
            ab.load_audio_16k("/long.wav")
        assert decoded == []

    def test_a_file_that_fits_in_the_free_ram_is_decoded(self, monkeypatch):
        # An hour needs about 550 MB at the peak; 600 MB free is enough.
        ab, decoded = self._setup(monkeypatch, 3600, (16000.0, 600.0))
        assert ab.load_audio_16k("/hour.wav") == "audio" and decoded == ["/hour.wav"]

    def test_the_length_is_not_probed_when_the_ram_cannot_be_read(self, monkeypatch):
        import media_inspect
        ab, decoded = self._setup(monkeypatch, 36000, None)
        monkeypatch.setattr(media_inspect, "duration_seconds",
                            lambda path, **k: pytest.fail("ran ffprobe for nothing"))
        assert ab.load_audio_16k("/x.wav") == "audio"

    def test_the_refusal_only_suggests_splitting(self, monkeypatch):
        import memory_headroom
        ab, decoded = self._setup(monkeypatch, 10 * 3600, (16000.0, 2000.0))
        with pytest.raises(memory_headroom.HeadroomError) as info:
            ab.load_audio_16k("/long.wav")
        assert "Split it into shorter files" in str(info.value)
        assert "Whisper" not in str(info.value)

    def test_a_file_that_fits_is_decoded(self, monkeypatch):
        ab, decoded = self._setup(monkeypatch, 3600, (16000.0, 8000.0))
        assert ab.load_audio_16k("/hour.wav") == "audio" and decoded == ["/hour.wav"]

    @pytest.mark.parametrize("seconds,ram", [(None, (16000.0, 100.0)), (36000, None)])
    def test_an_unknown_length_or_ram_reading_never_blocks(self, monkeypatch, seconds, ram):
        ab, decoded = self._setup(monkeypatch, seconds, ram)
        assert ab.load_audio_16k("/x.wav") == "audio"

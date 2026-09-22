"""
tests/test_asr_backend.py -- asr_backend.py's pluggable transcription
backends: WhisperBackend's passthrough to the existing (unchanged)
core.transcribe_for_timing(), and Qwen3ASRBackend's segment-reuse/
re-transcription logic, exercised against fake qwen_asr/torch modules
and a monkeypatched audio slicer, since the real model needs a GPU/
network this sandbox doesn't have.
"""
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

    def test_empty_model_result_becomes_empty_text(self, monkeypatch):
        import asr_backend as ab
        self._stub_audio_slicing(monkeypatch, ab)

        class EmptyModel:
            def transcribe(self, audio, language):
                return []

        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B": EmptyModel())
        backend = ab.Qwen3ASRBackend()
        result = backend.transcribe("/fake.wav", "ko",
                                     whisper_segments=[{"start": 0.0, "end": 1.0, "text": "x"}])
        assert result[0]["text"] == ""


class TestLoadQwen3Asr:
    def _install_fake_qwen_asr(self, from_pretrained):
        fake_torch = types.ModuleType("torch")
        fake_torch.bfloat16 = "bfloat16"
        sys.modules["torch"] = fake_torch

        fake_module = types.ModuleType("qwen_asr")

        class FakeModel:
            pass
        FakeModel.from_pretrained = staticmethod(from_pretrained)
        fake_module.Qwen3ASRModel = FakeModel
        sys.modules["qwen_asr"] = fake_module

    def teardown_method(self):
        sys.modules.pop("torch", None)
        sys.modules.pop("qwen_asr", None)
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
        assert calls == [("Qwen/Qwen3-ASR-1.7B", "cpu"), ("Qwen/Qwen3-ASR-0.6B", "cpu")]

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

    def test_network_error_raises_model_download_error(self):
        def from_pretrained(model_id, dtype, device_map, max_new_tokens):
            raise OSError("Connection timed out while downloading from huggingface.co")

        self._install_fake_qwen_asr(from_pretrained)
        import asr_backend
        importlib.reload(asr_backend)
        asr_backend._asr_model_cache.clear()

        from core import ModelDownloadError
        with pytest.raises(ModelDownloadError):
            asr_backend.load_qwen3_asr(use_gpu=False)

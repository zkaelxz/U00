"""
tests/test_audio_preprocess.py -- audio_preprocess.py, Demucs-based
vocal separation.

Demucs itself isn't installed in this sandbox (no GPU, no network, heavy
model download) -- its Python API (demucs.api.Separator/save_audio) is
faked at that exact boundary, the same way other tests here fake
faster_whisper.WhisperModel or paddleocr.PaddleOCR: this exercises
separate_vocals_demucs()'s own logic (error handling, stem selection, path
plumbing) for real, without needing the actual neural network.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_preprocess


def _install_fake_demucs(monkeypatch, stems=("drums", "bass", "other", "vocals"),
                          separate_exc=None):
    captured = {}

    class FakeSeparator:
        def __init__(self, model="htdemucs"):
            captured["model"] = model
            self.samplerate = 44100

        def separate_audio_file(self, path):
            captured["separate_path"] = path
            if separate_exc:
                raise separate_exc
            return "origin", {s: f"tensor-for-{s}" for s in stems}

    def fake_save_audio(tensor, path, samplerate=None):
        captured["saved_tensor"] = tensor
        captured["saved_path"] = path
        captured["samplerate"] = samplerate

    fake_api = types.ModuleType("demucs.api")
    fake_api.Separator = FakeSeparator
    fake_api.save_audio = fake_save_audio
    fake_demucs = types.ModuleType("demucs")
    fake_demucs.api = fake_api
    monkeypatch.setitem(sys.modules, "demucs", fake_demucs)
    monkeypatch.setitem(sys.modules, "demucs.api", fake_api)
    return captured


class TestSeparateVocals:
    def test_writes_the_vocals_stem_to_out_path(self, monkeypatch):
        captured = _install_fake_demucs(monkeypatch)
        result = audio_preprocess.separate_vocals_demucs("/fake/audio.wav", "/fake/vocals.wav")

        assert result == "/fake/vocals.wav"
        assert captured["separate_path"] == "/fake/audio.wav"
        assert captured["saved_tensor"] == "tensor-for-vocals"
        assert captured["saved_path"] == "/fake/vocals.wav"
        assert captured["samplerate"] == 44100

    def test_uses_the_requested_model(self, monkeypatch):
        captured = _install_fake_demucs(monkeypatch)
        audio_preprocess.separate_vocals_demucs("/fake/audio.wav", "/fake/vocals.wav",
                                          model="htdemucs_ft")
        assert captured["model"] == "htdemucs_ft"

    def test_missing_demucs_raises_a_clear_actionable_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "demucs", None)
        monkeypatch.setitem(sys.modules, "demucs.api", None)
        with pytest.raises(audio_preprocess.VocalSeparationError, match="pip install demucs"):
            audio_preprocess.separate_vocals_demucs("/fake/audio.wav", "/fake/vocals.wav")

    def test_separation_failure_is_wrapped_not_raised_raw(self, monkeypatch):
        _install_fake_demucs(monkeypatch, separate_exc=RuntimeError("out of memory"))
        with pytest.raises(audio_preprocess.VocalSeparationError, match="out of memory"):
            audio_preprocess.separate_vocals_demucs("/fake/audio.wav", "/fake/vocals.wav")

    def test_a_model_with_no_vocals_stem_raises_a_clear_error(self, monkeypatch):
        _install_fake_demucs(monkeypatch, stems=("drums", "bass"))
        with pytest.raises(audio_preprocess.VocalSeparationError, match="vocals stem"):
            audio_preprocess.separate_vocals_demucs("/fake/audio.wav", "/fake/vocals.wav")


class TestModelDirRespectsPortableModeOverride:
    """Step 10: BAIHE_AUDIO_SEP_MODEL_DIR (set by portable.py's
    activate_portable_mode() under portable mode) overrides the default
    ~/.cache/audio-separator-models -- _MODEL_DIR is a module-level
    constant, so this reimports the module with the env var already set,
    the same as it would be by the time a real process reaches this
    import."""

    def test_env_var_set_overrides_the_default(self, monkeypatch):
        import importlib
        monkeypatch.setenv("BAIHE_AUDIO_SEP_MODEL_DIR", "/portable/model_cache/audio-separator-models")
        try:
            importlib.reload(audio_preprocess)
            assert audio_preprocess._MODEL_DIR == "/portable/model_cache/audio-separator-models"
        finally:
            monkeypatch.delenv("BAIHE_AUDIO_SEP_MODEL_DIR", raising=False)
            importlib.reload(audio_preprocess)

    def test_no_env_var_falls_back_to_the_home_cache_dir(self, monkeypatch):
        import importlib
        monkeypatch.delenv("BAIHE_AUDIO_SEP_MODEL_DIR", raising=False)
        importlib.reload(audio_preprocess)
        assert audio_preprocess._MODEL_DIR == os.path.join(
            os.path.expanduser("~"), ".cache", "audio-separator-models")

"""transcribe_for_timing's decode_randomness: what reaches faster-whisper's transcribe()."""
import types

import pytest

import whisper_models
import whisper_transcribe


class _Model:
    def __init__(self):
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append(kwargs)
        return iter([]), types.SimpleNamespace(duration=1.0)


@pytest.fixture
def model(monkeypatch):
    m = _Model()
    monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: m)
    monkeypatch.setattr(whisper_transcribe, "_fit_to_speech", lambda *a, **k: None)
    monkeypatch.setattr(whisper_transcribe, "_decode_whole", lambda path: None)
    return m


def _run(**kw):
    whisper_transcribe.transcribe_for_timing("a.wav", model_size="large-v3", **kw)


def test_default_passes_no_temperature(model):
    _run()
    assert "temperature" not in model.calls[0]


def test_temperature_0_is_passed_to_the_decode(model):
    _run(decode_randomness="temperature_0")
    assert model.calls[0]["temperature"] == 0.0


def test_unknown_choice_is_refused(model):
    with pytest.raises(ValueError):
        _run(decode_randomness="wild")


def test_the_file_is_decoded_once_for_the_decode_and_the_fit_to_speech(monkeypatch):
    import numpy as np
    heard, decoded = [], []

    class Model:
        def transcribe(self, audio, **kwargs):
            heard.append(audio)
            return iter([]), types.SimpleNamespace(duration=1.0)

    samples = np.zeros(16000, dtype=np.float32)
    monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: Model())
    monkeypatch.setattr(whisper_transcribe, "_decode_whole", lambda path: decoded.append(path) or samples)
    monkeypatch.setattr(whisper_transcribe, "_fit_to_speech",
                        lambda result, path, trim_starts=True, audio=None: heard.append(audio))
    _run()
    assert decoded == ["a.wav"]
    assert heard[0] is samples and heard[1] is samples

"""transcribe_for_timing's decode_randomness: what reaches faster-whisper's transcribe()."""
import sys
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
    return m


def _run(**kw):
    whisper_transcribe.transcribe_for_timing("a.wav", model_size="large-v3", **kw)


def test_default_passes_no_temperature(model):
    _run()
    assert "temperature" not in model.calls[0]


def test_temperature_0_is_passed_to_the_decode(model):
    _run(decode_randomness="temperature_0")
    assert model.calls[0]["temperature"] == 0.0


def test_seeded_sets_the_seed_and_keeps_the_ladder(model, monkeypatch):
    seeds = []
    fake = types.SimpleNamespace(set_random_seed=seeds.append)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    _run(decode_randomness="seeded")
    assert seeds == [whisper_transcribe.DECODE_SEED]
    assert "temperature" not in model.calls[0]


def test_unknown_choice_is_refused(model):
    with pytest.raises(ValueError):
        _run(decode_randomness="wild")

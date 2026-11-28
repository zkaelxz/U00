"""transcribe_for_timing's decode_randomness and the names prompt's temperature: what reaches
faster-whisper's transcribe()."""
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


def _seg(start, end, text, logprob=-0.3):
    return types.SimpleNamespace(start=start, end=end, text=text, words=None, avg_logprob=logprob,
                                 no_speech_prob=0.0, compression_ratio=1.2)


class _Heard:
    """The main decode returns `main`; a clip_timestamps decode returns `again`."""
    def __init__(self, main, again=()):
        self.main, self.again, self.calls = main, list(again), []

    def transcribe(self, path, **kwargs):
        self.calls.append(kwargs)
        return iter(self.again if "clip_timestamps" in kwargs else self.main), types.SimpleNamespace(duration=20.0)


def _primed_run(monkeypatch, model, prompt="苏杉。"):
    monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: model)
    monkeypatch.setattr(whisper_transcribe, "_fit_to_speech", lambda *a, **k: None)
    monkeypatch.setattr(whisper_transcribe, "_decode_whole", lambda path: None)
    out = whisper_transcribe.transcribe_for_timing("a.wav", model_size="large-v3", initial_prompt=prompt)
    return [s["text"] for s in out]


def test_a_segment_that_would_fall_back_is_heard_again_without_the_prompt(monkeypatch):
    model = _Heard([_seg(0.0, 3.0, "苏杉你回来了"), _seg(4.0, 7.0, "我不知道", logprob=-1.4)],
                   again=[_seg(4.0, 7.0, "我真的不知道", logprob=-0.4)])
    assert _primed_run(monkeypatch, model) == ["苏杉你回来了", "我真的不知道"]
    main, again = model.calls
    assert main["temperature"] == 0.0 and main["initial_prompt"] == "苏杉。"
    assert "initial_prompt" not in again and "temperature" not in again
    assert again["clip_timestamps"] == [4.0, 7.0]


def test_a_rehearing_less_sure_than_the_primed_one_keeps_the_primed_text(monkeypatch):
    model = _Heard([_seg(0.0, 3.0, "苏杉你回来了", logprob=-1.2)], again=[_seg(0.0, 3.0, "书山你回来了", logprob=-1.6)])
    assert _primed_run(monkeypatch, model) == ["苏杉你回来了"]


def test_clean_segments_keep_the_primed_decode(monkeypatch):
    model = _Heard([_seg(0.0, 3.0, "苏杉你回来了"), _seg(4.0, 7.0, "我不知道")])
    assert _primed_run(monkeypatch, model) == ["苏杉你回来了", "我不知道"]
    assert len(model.calls) == 1


def test_a_run_without_a_prompt_keeps_the_temperature_ladder_and_hears_once(monkeypatch):
    model = _Heard([_seg(0.0, 3.0, "我不知道", logprob=-1.4)], again=[_seg(0.0, 3.0, "别的")])
    assert _primed_run(monkeypatch, model, prompt="") == ["我不知道"]
    assert len(model.calls) == 1 and "temperature" not in model.calls[0]

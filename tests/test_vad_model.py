"""The v6.2 gate adds only the stand-alone stretches v6.2 hears, on the gated thread, and never when its file is not intact."""
import sys
import threading
import types

import pytest

import vad_model

SR = 16000


def _s(start_s, end_s):
    return {"start": int(start_s * SR), "end": int(end_s * SR)}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(vad_model, "_installed", False)
    monkeypatch.setattr(vad_model, "_pinned", None)


def _fake_faster_whisper(monkeypatch, installed, pinned):
    """A faster-whisper whose detector returns `installed` on the stock model and `pinned` on the v6.2 one."""
    loaded = []

    class SileroVADModel:
        def __init__(self, path):
            loaded.append(path)

    stock = object()
    vad = types.ModuleType("faster_whisper.vad")
    vad.SileroVADModel = SileroVADModel
    vad.get_vad_model = lambda: stock

    def get_speech_timestamps(audio, vad_options=None, sampling_rate=16000, **kwargs):
        return list(installed if vad.get_vad_model() is stock else pinned)

    transcribe = types.ModuleType("faster_whisper.transcribe")
    transcribe.get_speech_timestamps = get_speech_timestamps
    fw = types.ModuleType("faster_whisper")
    fw.vad, fw.transcribe = vad, transcribe
    for name, mod in (("faster_whisper", fw), ("faster_whisper.vad", vad), ("faster_whisper.transcribe", transcribe)):
        monkeypatch.setitem(sys.modules, name, mod)
    return transcribe, loaded


def test_the_shipped_file_matches_its_pinned_hash():
    assert vad_model._sha256(vad_model.PINNED_PATH) == vad_model.PINNED_SHA256


def test_only_extras_far_from_installed_speech_are_added():
    installed = [_s(10, 14), _s(30, 33)]
    pinned = [_s(9, 15),          # widens installed speech: the laughter-and-noise case
              _s(15, 16),         # touches it after a short pause
              _s(20, 22),         # on its own
              _s(28.6, 29.4),     # 0.6 s before installed speech
              _s(40, 40.3)]       # on its own but too short to be a line
    assert vad_model.standalone_extras(installed, pinned, SR) == [_s(10, 14), _s(20, 22), _s(30, 33)]


def test_a_gated_run_hears_the_extras_and_an_ungated_one_does_not(monkeypatch):
    transcribe, loaded = _fake_faster_whisper(monkeypatch, [_s(0, 5)], [_s(0, 6), _s(20, 23)])
    with vad_model.gated() as on:
        assert on is True
        assert transcribe.get_speech_timestamps(None, None) == [_s(0, 5), _s(20, 23)]
    assert transcribe.get_speech_timestamps(None, None) == [_s(0, 5)]
    with vad_model.gated():
        pass
    assert loaded == [vad_model.PINNED_PATH]


def test_another_threads_run_is_not_gated(monkeypatch):
    transcribe, _ = _fake_faster_whisper(monkeypatch, [_s(0, 5)], [_s(20, 23)])
    other = []
    with vad_model.gated():
        worker = threading.Thread(target=lambda: other.append(transcribe.get_speech_timestamps(None, None)))
        worker.start()
        worker.join()
    assert other == [[_s(0, 5)]]


@pytest.mark.parametrize("contents", [None, b"not the pinned weights"])
def test_a_missing_or_altered_file_leaves_the_detector_alone(monkeypatch, tmp_path, contents):
    transcribe, loaded = _fake_faster_whisper(monkeypatch, [_s(0, 5)], [_s(20, 23)])
    path = tmp_path / "v62.onnx"
    if contents is not None:
        path.write_bytes(contents)
    monkeypatch.setattr(vad_model, "PINNED_PATH", str(path))
    with vad_model.gated() as on:
        assert on is False
        assert transcribe.get_speech_timestamps(None, None) == [_s(0, 5)]
    assert loaded == []


def test_a_faster_whisper_without_the_hooks_is_not_an_error(monkeypatch):
    _fake_faster_whisper(monkeypatch, [], [])
    del sys.modules["faster_whisper.vad"].SileroVADModel
    with vad_model.gated() as on:
        assert on is False

"""ONNX Runtime's usage reporting is switched off before a session is made."""
import sys
import types

import pytest

from lib import ort_telemetry


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(ort_telemetry, "_done", False)


def _fake_ort(monkeypatch, **attrs):
    mod = types.ModuleType("onnxruntime")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, "onnxruntime", mod)
    return mod


def test_calls_the_switch_once(monkeypatch):
    calls = []
    _fake_ort(monkeypatch, disable_telemetry_events=lambda: calls.append(1))
    assert ort_telemetry.disable() is True
    assert ort_telemetry.disable() is True
    assert calls == [1]


def test_a_build_without_the_switch_is_not_an_error(monkeypatch):
    _fake_ort(monkeypatch)
    assert ort_telemetry.disable() is False


def test_missing_onnxruntime_is_not_an_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    assert ort_telemetry.disable() is False


def test_a_failing_switch_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("no")
    _fake_ort(monkeypatch, disable_telemetry_events=boom)
    assert ort_telemetry.disable() is False


def test_the_asmr_detector_switches_it_off_before_building_its_session(monkeypatch, tmp_path):
    import asmr_vad
    order = []
    mod = _fake_ort(monkeypatch, disable_telemetry_events=lambda: order.append("off"))

    class _Session:
        def __init__(self, path, providers=None):
            order.append("session")

        def get_inputs(self):
            return [types.SimpleNamespace(name="x")]

    mod.InferenceSession = _Session
    model = tmp_path / "model.onnx"
    model.write_bytes(b"x")
    monkeypatch.setattr(asmr_vad, "model_path", lambda: str(model))
    monkeypatch.setattr(asmr_vad.AsmrVad, "scores", lambda self, audio: [])
    parent = types.ModuleType("faster_whisper")
    fe = types.ModuleType("faster_whisper.feature_extractor")
    parent.feature_extractor = fe
    monkeypatch.setitem(sys.modules, "faster_whisper", parent)
    monkeypatch.setitem(sys.modules, "faster_whisper.feature_extractor", fe)
    asmr_vad.load_detector()
    assert order == ["off", "session"]

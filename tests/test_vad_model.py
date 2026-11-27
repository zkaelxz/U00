"""The pinned Silero v6.2 file is the file faster-whisper's detector loads, and only when it is intact."""
import sys
import types

import pytest

import vad_model


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(vad_model, "_done", False)


def _fake_vad(monkeypatch, tmp_path, stock_bytes=b"older weights"):
    pkg = tmp_path / "faster_whisper"
    (pkg / "assets").mkdir(parents=True)
    (pkg / "assets" / "silero_vad_v6.onnx").write_bytes(stock_bytes)
    loaded = []

    class SileroVADModel:
        def __init__(self, path):
            loaded.append(path)

    vad = types.ModuleType("faster_whisper.vad")
    vad.__file__ = str(pkg / "vad.py")
    vad.SileroVADModel = SileroVADModel
    vad.get_vad_model = lambda: "stock"
    fw = types.ModuleType("faster_whisper")
    fw.vad = vad
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad)
    return vad, loaded


def test_the_shipped_file_matches_its_pinned_hash():
    assert vad_model._sha256(vad_model.PINNED_PATH) == vad_model.PINNED_SHA256


def test_the_detector_loads_the_pinned_file_once(monkeypatch, tmp_path):
    vad, loaded = _fake_vad(monkeypatch, tmp_path)
    assert vad_model.use_pinned_model() is True
    first, second = vad.get_vad_model(), vad.get_vad_model()
    assert first is second
    assert loaded == [vad_model.PINNED_PATH]


def test_an_altered_file_keeps_the_stock_detector(monkeypatch, tmp_path):
    vad, loaded = _fake_vad(monkeypatch, tmp_path)
    altered = tmp_path / "altered.onnx"
    altered.write_bytes(b"not the pinned weights")
    monkeypatch.setattr(vad_model, "PINNED_PATH", str(altered))
    assert vad_model.use_pinned_model() is False
    assert vad.get_vad_model() == "stock"
    assert loaded == []


def test_a_missing_file_keeps_the_stock_detector(monkeypatch, tmp_path):
    vad, _ = _fake_vad(monkeypatch, tmp_path)
    monkeypatch.setattr(vad_model, "PINNED_PATH", str(tmp_path / "gone.onnx"))
    assert vad_model.use_pinned_model() is False
    assert vad.get_vad_model() == "stock"


def test_a_faster_whisper_that_already_ships_the_file_is_left_alone(monkeypatch, tmp_path):
    with open(vad_model.PINNED_PATH, "rb") as fh:
        vad, loaded = _fake_vad(monkeypatch, tmp_path, stock_bytes=fh.read())
    assert vad_model.use_pinned_model() is True
    assert vad.get_vad_model() == "stock"
    assert loaded == []


def test_a_faster_whisper_without_the_hook_is_not_an_error(monkeypatch, tmp_path):
    vad, _ = _fake_vad(monkeypatch, tmp_path)
    del vad.SileroVADModel
    assert vad_model.use_pinned_model() is False

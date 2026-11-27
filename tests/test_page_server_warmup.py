"""The extension bridge loads the comic models ahead of the first page, from weights already on
disk only, once per process, holding the pipeline lock. Model loading itself is faked."""
import threading

import pytest

from services import page_server
from scanlate import scanlate
from scanlate import scanlate_detect
from scanlate import scanlate_lama
@pytest.fixture
def models(monkeypatch):
    import ocr
    state = {"detector_cached": True, "eraser_cached": True, "reader_cached": False, "detect": [],
             "erase": [], "read": [], "manga_cached": False, "manga": []}
    monkeypatch.setattr(scanlate, "_chinese_reader_on_disk", lambda: state["reader_cached"])
    monkeypatch.setattr(ocr, "manga_ocr_on_disk", lambda: state["manga_cached"])
    monkeypatch.setattr(ocr, "load_manga_ocr", lambda: state["manga"].append(1))
    monkeypatch.setattr(ocr, "_run_rapidocr",
                        lambda key, source, allow_download=True: state["read"].append((key, allow_download)))
    monkeypatch.setattr(scanlate_detect, "bubble_ml_weights_cached", lambda: state["detector_cached"])
    monkeypatch.setattr(scanlate_lama, "lama_ml_weights_cached", lambda: state["eraser_cached"])
    monkeypatch.setattr(scanlate, "detect_bubbles",
                        lambda path, backend="auto", image=None: state["detect"].append(backend) or [])
    monkeypatch.setattr(scanlate_lama, "_run_ml_inpaint",
                        lambda roi, mask, allow_download=True: state["erase"].append(allow_download) or roi)
    return state


def test_both_models_on_disk_are_loaded_without_downloading(models):
    assert scanlate.warm_models("auto") == ["balloon detector", "text eraser"]
    assert models["detect"] == ["ml"] and models["erase"] == [False]


def test_the_chinese_text_reader_is_loaded_when_it_is_on_disk(models):
    models["reader_cached"] = True
    assert scanlate.warm_models("auto") == ["balloon detector", "text eraser", "Chinese text reader"]
    assert models["read"] == [("medium", False)], "warming never downloads the GPU weights"


def test_the_japanese_text_reader_is_loaded_when_it_is_on_disk(models):
    models["manga_cached"] = True
    assert scanlate.warm_models("auto")[-1] == "Japanese text reader"
    assert models["manga"] == [1]


def test_a_manga_ocr_model_already_on_disk_is_used_without_asking_the_server(tmp_path, monkeypatch):
    import ocr
    from lib import model_offline
    monkeypatch.setattr(model_offline, "hf_snapshot", lambda repo, revision=None: str(tmp_path))
    monkeypatch.setattr(model_offline, "active", lambda: False)
    assert ocr._manga_ocr_source() == (str(tmp_path),)


def test_the_chinese_text_reader_counts_as_on_disk_only_with_both_files(tmp_path, monkeypatch):
    import ocr
    monkeypatch.setattr(ocr, "backend_problem", lambda backend: None)
    monkeypatch.setattr(ocr, "rapidocr_model_path", lambda spec: str(tmp_path / spec["file"]))
    (tmp_path / ocr.RAPIDOCR_MEDIUM_DET_MODEL["file"]).write_bytes(b"x")
    assert scanlate._chinese_reader_on_disk() is False
    (tmp_path / ocr.RAPIDOCR_MEDIUM_REC_MODEL["file"]).write_bytes(b"x")
    assert scanlate._chinese_reader_on_disk() is True


@pytest.mark.parametrize("backend, detector_cached, eraser_cached, expected", [
    ("cv", True, True, ["text eraser"]),          # the heuristic detector has no model
    ("auto", False, True, ["text eraser"]),       # not downloaded: auto would use the heuristic
    ("auto", True, False, ["balloon detector"]),
])
def test_a_model_not_on_disk_or_not_chosen_is_left_alone(models, backend, detector_cached, eraser_cached,
                                                          expected):
    models["detector_cached"], models["eraser_cached"] = detector_cached, eraser_cached
    assert scanlate.warm_models(backend) == expected


def test_the_bridge_loads_them_once_under_the_pipeline_lock(models, monkeypatch, isolated_db):
    monkeypatch.setattr(page_server, "_warm_started", False)
    held = []
    done = threading.Event()

    def warm(backend):
        held.append(page_server.PIPELINE_LOCK.locked())
        done.set()
        return ["balloon detector"]
    monkeypatch.setattr(scanlate, "warm_models", warm)
    warm_soon = page_server.warm_models_soon.real
    assert warm_soon() is True
    assert done.wait(5)
    assert warm_soon() is False
    for thread in threading.enumerate():
        if thread.name == "page-server-warmup":
            thread.join(5)
    assert held == [True]


def test_a_failed_load_is_only_logged(models, monkeypatch, isolated_db):
    monkeypatch.setattr(page_server, "_warm_started", False)

    def boom(backend):
        raise RuntimeError("C:\\Users\\me\\model.safetensors is corrupt")
    monkeypatch.setattr(scanlate, "warm_models", boom)
    assert page_server.warm_models_soon.real() is True
    for thread in threading.enumerate():
        if thread.name == "page-server-warmup":
            thread.join(5)
    assert not page_server.PIPELINE_LOCK.locked()


def test_a_text_reader_that_fails_to_load_is_reported_to_the_caller(models, monkeypatch):
    import ocr
    models["reader_cached"] = True

    def broken(key, source, allow_download=True):
        raise RuntimeError("no engine")

    monkeypatch.setattr(ocr, "_run_rapidocr", broken)
    with pytest.raises(RuntimeError):
        scanlate.warm_models("auto")

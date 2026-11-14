"""The extension bridge loads the comic models ahead of the first page, from weights already on
disk only, once per process, holding the pipeline lock. Model loading itself is faked."""
import threading

import pytest

import page_server
import scanlate
import scanlate_detect
import scanlate_inpaint


@pytest.fixture
def models(monkeypatch):
    state = {"detector_cached": True, "eraser_cached": True, "detect": [], "erase": []}
    monkeypatch.setattr(scanlate_detect, "bubble_ml_weights_cached", lambda: state["detector_cached"])
    monkeypatch.setattr(scanlate_inpaint, "lama_ml_weights_cached", lambda: state["eraser_cached"])
    monkeypatch.setattr(scanlate, "detect_bubbles",
                        lambda path, backend="auto", image=None: state["detect"].append(backend) or [])
    monkeypatch.setattr(scanlate_inpaint, "_run_ml_inpaint",
                        lambda roi, mask, allow_download=True: state["erase"].append(allow_download) or roi)
    return state


def test_both_models_on_disk_are_loaded_without_downloading(models):
    assert scanlate.warm_models("auto") == ["balloon detector", "text eraser"]
    assert models["detect"] == ["ml"] and models["erase"] == [False]


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

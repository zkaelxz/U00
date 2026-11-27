"""The comic balloon detector runs a page on the GPU only when Settings allows it and it fits;
otherwise, and whenever the GPU fails, the CPU copy does the work. torch is faked: these tests
check the choice, not the model."""
import contextlib

import pytest

from scanlate import scanlate_detect


class _OutOfMemory(RuntimeError):
    pass


class _Tensor:
    def __init__(self, device="cpu"):
        self.device = device

    def to(self, device):
        return _Tensor(device)

    def cpu(self):
        return _Tensor("cpu")


class _Model:
    def __init__(self):
        self.device = "cpu"
        self.gpu_error = None

    def to(self, device):
        self.device = device
        return self

    def __call__(self, pixel_values):
        assert pixel_values.device == self.device
        if self.device == "cuda" and self.gpu_error is not None:
            raise self.gpu_error
        return {"logits": _Tensor(self.device), "pred_boxes": _Tensor(self.device), "ran_on": self.device}


class _Cuda:
    OutOfMemoryError = _OutOfMemory

    def __init__(self):
        self.available = True

    def is_available(self):
        return self.available

    def empty_cache(self):
        pass


class _Torch:
    def __init__(self):
        self.cuda = _Cuda()

    def no_grad(self):
        return contextlib.nullcontext()


@pytest.fixture
def gpu(monkeypatch):
    import vram
    import stored_settings
    state = {"use_gpu": True, "fits": True, "asked": 0}
    monkeypatch.setattr(stored_settings, "read",
                        lambda key, choices_of=None: state[key] if key == "use_gpu" else None)

    def check_fits(what, required_mb=None, free_mb=None):
        state["asked"] += 1
        if not state["fits"]:
            raise vram.InsufficientVramError("full")
    monkeypatch.setattr(vram, "check_fits", check_fits)
    monkeypatch.setattr(scanlate_detect, "_bubble_ml_gpu", {})
    monkeypatch.setattr(scanlate_detect, "_bubble_ml_gpu_broken", [])
    monkeypatch.setattr(scanlate_detect, "_bubble_ml_gpu_off_until", 0.0)
    return state


def _run(model, torch):
    outputs = scanlate_detect._detector_outputs(model, {"pixel_values": _Tensor()}, torch)
    assert outputs["logits"].device == "cpu" and outputs["pred_boxes"].device == "cpu"
    return outputs["ran_on"]


def test_a_page_runs_on_the_gpu_and_the_copy_is_kept(gpu):
    master, torch = _Model(), _Torch()
    assert _run(master, torch) == "cuda"
    copy = scanlate_detect._bubble_ml_gpu["cuda"]
    assert _run(master, torch) == "cuda"
    assert scanlate_detect._bubble_ml_gpu["cuda"] is copy and master.device == "cpu"
    assert gpu["asked"] == 1   # room is checked once, for the load


@pytest.mark.parametrize("change", ["no_gpu", "setting_off", "too_full"])
def test_without_a_free_gpu_the_page_runs_on_the_cpu(gpu, change):
    torch = _Torch()
    if change == "no_gpu":
        torch.cuda.available = False
    elif change == "setting_off":
        gpu["use_gpu"] = False
    else:
        gpu["fits"] = False
    assert _run(_Model(), torch) == "cpu"
    assert scanlate_detect._bubble_ml_gpu == {}


def test_a_full_gpu_costs_one_page_and_a_broken_one_the_next_few_minutes(gpu):
    master, torch = _Model(), _Torch()
    _run(master, torch)
    scanlate_detect._bubble_ml_gpu["cuda"].gpu_error = _OutOfMemory("CUDA out of memory")
    assert _run(master, torch) == "cpu"
    scanlate_detect._bubble_ml_gpu["cuda"].gpu_error = None
    assert _run(master, torch) == "cuda"
    scanlate_detect._bubble_ml_gpu["cuda"].gpu_error = RuntimeError("driver fault")
    assert _run(master, torch) == "cpu"
    assert scanlate_detect._bubble_ml_gpu == {} and _run(master, torch) == "cpu"


def test_releasing_the_detector_drops_its_gpu_copy(gpu):
    _run(_Model(), _Torch())
    scanlate_detect.release_bubble_model()
    assert scanlate_detect._bubble_ml_gpu == {}


def test_the_gpu_comes_back_after_a_fault_once_the_pause_is_over(gpu, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(scanlate_detect.time, "monotonic", lambda: now[0])
    master, torch = _Model(), _Torch()
    _run(master, torch)
    scanlate_detect._bubble_ml_gpu["cuda"].gpu_error = RuntimeError("driver fault")
    assert _run(master, torch) == "cpu"
    now[0] += scanlate_detect._GPU_OFF_SECONDS - 1
    assert _run(master, torch) == "cpu"
    now[0] += 2
    assert _run(master, torch) == "cuda"
    assert scanlate_detect._bubble_ml_gpu_broken == ["RuntimeError"], "the fault stays in the log"

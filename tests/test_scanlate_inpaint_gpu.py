"""The comic text eraser (LaMa-manga) runs a region on the GPU only when Settings allows it and
the region clearly fits; otherwise, and whenever the GPU fails, the CPU copy does the work.
torch is faked: these tests check the choice, not the model."""
import contextlib

import pytest

from scanlate import scanlate_lama
class _OutOfMemory(RuntimeError):
    pass


class _Out:
    def __init__(self, device):
        self.device = device

    def cpu(self):
        return _Out("cpu from " + self.device)


class _Tensor:
    def __init__(self, h, w, device="cpu"):
        self.shape = (1, 4, h, w)
        self.device = device

    def to(self, device):
        return _Tensor(self.shape[2], self.shape[3], device)


class _Generator:
    def __init__(self):
        self.device = "cpu"
        self.gpu_error = None

    def to(self, device):
        self.device = device
        return self

    def __call__(self, tensor):
        assert tensor.device == self.device
        if self.device == "cuda" and self.gpu_error is not None:
            raise self.gpu_error
        return [_Out(self.device)]


class _Cuda:
    OutOfMemoryError = _OutOfMemory

    def __init__(self):
        self.available = True
        self.emptied = 0

    def is_available(self):
        return self.available

    def empty_cache(self):
        self.emptied += 1


class _Torch:
    def __init__(self):
        self.cuda = _Cuda()

    def no_grad(self):
        return contextlib.nullcontext()


@pytest.fixture
def gpu(monkeypatch):
    import vram
    import stored_settings
    state = {"use_gpu": True, "fits": True, "asked_mb": []}
    monkeypatch.setattr(stored_settings, "read",
                        lambda key, choices_of=None: state[key] if key == "use_gpu" else None)

    def check_fits(what, required_mb=None, free_mb=None):
        state["asked_mb"].append(required_mb)
        if not state["fits"]:
            raise vram.InsufficientVramError("full")
    monkeypatch.setattr(vram, "check_fits", check_fits)
    monkeypatch.setattr(scanlate_lama, "_lama_gpu", {})
    monkeypatch.setattr(scanlate_lama, "_lama_gpu_broken", [])
    monkeypatch.setattr(scanlate_lama, "_lama_gpu_off_until", 0.0)
    return state


def _run(generator, torch, h=200, w=300):
    return scanlate_lama._generate(generator, _Tensor(h, w), torch).device


def test_a_region_that_fits_runs_on_the_gpu_and_the_copy_is_kept(gpu):
    master, torch = _Generator(), _Torch()
    assert _run(master, torch) == "cpu from cuda"
    copy = scanlate_lama._lama_gpu["cuda"]
    assert _run(master, torch) == "cpu from cuda"
    assert scanlate_lama._lama_gpu["cuda"] is copy and master.device == "cpu"
    # The first region also pays for loading the weights; later ones only for their own size.
    assert gpu["asked_mb"][0] > gpu["asked_mb"][1] > 0


@pytest.mark.parametrize("change", ["no_gpu", "setting_off", "too_full"])
def test_otherwise_the_cpu_copy_does_the_work(gpu, change):
    master, torch = _Generator(), _Torch()
    torch.cuda.available = change != "no_gpu"
    gpu["use_gpu"] = change != "setting_off"
    gpu["fits"] = change != "too_full"
    assert _run(master, torch) == "cpu"
    assert scanlate_lama._lama_gpu == {}


def test_a_region_over_a_megapixel_uses_the_gpu_when_it_fits_and_asks_for_more_room(gpu):
    master, torch = _Generator(), _Torch()
    assert _run(master, torch, 2000, 1000) == "cpu from cuda"
    assert gpu["asked_mb"][0] > 800 + 2 * 3000 - 1
    gpu["fits"] = False
    assert _run(master, torch, 2000, 1000) == "cpu"


def test_a_full_gpu_costs_only_that_region(gpu):
    master, torch = _Generator(), _Torch()
    _run(master, torch)
    scanlate_lama._lama_gpu["cuda"].gpu_error = _OutOfMemory("CUDA out of memory")
    assert _run(master, torch) == "cpu"
    assert torch.cuda.emptied == 1
    scanlate_lama._lama_gpu["cuda"].gpu_error = None
    assert _run(master, torch) == "cpu from cuda"


def test_any_other_gpu_fault_keeps_the_cpu_for_a_few_minutes(gpu):
    master, torch = _Generator(), _Torch()
    _run(master, torch)
    scanlate_lama._lama_gpu["cuda"].gpu_error = RuntimeError("CUDA error: an illegal memory access")
    assert _run(master, torch) == "cpu"
    assert scanlate_lama._lama_gpu == {}
    assert _run(master, torch) == "cpu"


def test_the_gpu_copy_is_freed_and_listed_with_the_apps_other_gpu_models(gpu):
    import whisper_models
    from services import loaded_models_service
    master, torch = _Generator(), _Torch()
    _run(master, torch)
    names = [m["name"] for m in loaded_models_service._app_rows()["models"]]
    assert "LaMa-manga" in names
    whisper_models.release_gpu_models()
    assert scanlate_lama._lama_gpu == {}
    assert "LaMa-manga" not in [m["name"] for m in loaded_models_service._app_rows()["models"]]


def test_loading_and_running_the_eraser_hold_its_own_lock(monkeypatch):
    """The extension's overlay redraw runs it outside the pipeline lock, beside Scanlate's render."""
    pytest.importorskip("torch")
    pytest.importorskip("cv2")
    import numpy as np
    held = []

    def load(**kwargs):
        held.append(("load", scanlate_lama._LAMA_LOCK.locked()))
        return object()

    def generate(generator, tensor, torch):
        held.append(("run", scanlate_lama._LAMA_LOCK.locked()))
        return torch.zeros(3, tensor.shape[-2], tensor.shape[-1])
    monkeypatch.setattr(scanlate_lama, "_load_lama_generator", load)
    monkeypatch.setattr(scanlate_lama, "_generate", generate)
    roi = np.full((16, 16, 3), 255, dtype=np.uint8)
    scanlate_lama._run_ml_inpaint(roi, np.zeros((16, 16), dtype=np.uint8))
    assert held == [("load", True), ("run", True)]


def test_the_gpu_comes_back_after_a_fault_once_the_pause_is_over(gpu, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(scanlate_lama.time, "monotonic", lambda: now[0])
    master, torch = _Generator(), _Torch()
    _run(master, torch)
    scanlate_lama._lama_gpu["cuda"].gpu_error = RuntimeError("CUDA error: an illegal memory access")
    assert _run(master, torch) == "cpu"
    now[0] += scanlate_lama._GPU_OFF_SECONDS - 1
    assert _run(master, torch) == "cpu"
    now[0] += 2
    assert _run(master, torch) == "cpu from cuda"
    assert scanlate_lama._lama_gpu_broken == ["RuntimeError"], "the fault stays in the log"


@pytest.mark.parametrize("fits", [True, False])
def test_a_region_over_a_megapixel_goes_by_the_free_memory(gpu, fits):
    gpu["fits"] = fits
    assert _run(_Generator(), _Torch(), 2000, 1000) == ("cpu from cuda" if fits else "cpu")
    assert gpu["asked_mb"][0] > 6000, "the need still grows with the region"

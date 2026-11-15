"""The comic text eraser (LaMa-manga) runs a region on the GPU only when Settings allows it and
the region clearly fits; otherwise, and whenever the GPU fails, the CPU copy does the work.
torch is faked: these tests check the choice, not the model."""
import contextlib

import pytest

import scanlate_inpaint


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
    from services import settings_service, vram_service
    state = {"use_gpu": True, "fits": True, "asked_mb": []}
    monkeypatch.setattr(settings_service, "get", lambda key: state[key] if key == "use_gpu" else None)

    def check_fits(what, required_mb=None, free_mb=None):
        state["asked_mb"].append(required_mb)
        if not state["fits"]:
            raise vram_service.InsufficientVramError("full")
    monkeypatch.setattr(vram_service, "check_fits", check_fits)
    monkeypatch.setattr(scanlate_inpaint, "_lama_gpu", {})
    monkeypatch.setattr(scanlate_inpaint, "_lama_gpu_broken", [])
    return state


def _run(generator, torch, h=200, w=300):
    return scanlate_inpaint._generate(generator, _Tensor(h, w), torch).device


def test_a_region_that_fits_runs_on_the_gpu_and_the_copy_is_kept(gpu):
    master, torch = _Generator(), _Torch()
    assert _run(master, torch) == "cpu from cuda"
    copy = scanlate_inpaint._lama_gpu["cuda"]
    assert _run(master, torch) == "cpu from cuda"
    assert scanlate_inpaint._lama_gpu["cuda"] is copy and master.device == "cpu"
    # The first region also pays for loading the weights; later ones only for their own size.
    assert gpu["asked_mb"][0] > gpu["asked_mb"][1] > 0


@pytest.mark.parametrize("change", ["no_gpu", "setting_off", "too_full", "too_large"])
def test_otherwise_the_cpu_copy_does_the_work(gpu, change):
    master, torch = _Generator(), _Torch()
    torch.cuda.available = change != "no_gpu"
    gpu["use_gpu"] = change != "setting_off"
    gpu["fits"] = change != "too_full"
    h, w = (2000, 1000) if change == "too_large" else (200, 300)
    assert _run(master, torch, h, w) == "cpu"
    assert scanlate_inpaint._lama_gpu == {}


def test_a_full_gpu_costs_only_that_region(gpu):
    master, torch = _Generator(), _Torch()
    _run(master, torch)
    scanlate_inpaint._lama_gpu["cuda"].gpu_error = _OutOfMemory("CUDA out of memory")
    assert _run(master, torch) == "cpu"
    assert torch.cuda.emptied == 1
    scanlate_inpaint._lama_gpu["cuda"].gpu_error = None
    assert _run(master, torch) == "cpu from cuda"


def test_any_other_gpu_fault_keeps_the_cpu_for_the_rest_of_the_run(gpu):
    master, torch = _Generator(), _Torch()
    _run(master, torch)
    scanlate_inpaint._lama_gpu["cuda"].gpu_error = RuntimeError("CUDA error: an illegal memory access")
    assert _run(master, torch) == "cpu"
    assert scanlate_inpaint._lama_gpu == {}
    assert _run(master, torch) == "cpu"


def test_the_gpu_copy_is_freed_and_listed_with_the_apps_other_gpu_models(gpu):
    import whisper_models
    from services import loaded_models_service
    master, torch = _Generator(), _Torch()
    _run(master, torch)
    names = [m["name"] for m in loaded_models_service._app_rows()["models"]]
    assert "LaMa-manga" in names
    whisper_models.release_gpu_models()
    assert scanlate_inpaint._lama_gpu == {}
    assert "LaMa-manga" not in [m["name"] for m in loaded_models_service._app_rows()["models"]]

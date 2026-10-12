"""CUDA libraries for GPU Whisper: the path helper, the preflight that uses it,
and the Diagnostics install row. Fakes only; Windows is simulated."""

import os
import sys
import types

import pytest
from fastapi.testclient import TestClient

import cuda_runtime_path as crp
import whisper_models
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import cuda_runtime_service
from services import diagnostics_gaps_service as gaps
from services import diagnostics_installs_service as installs


HANDLES = []


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A fake Windows site-packages; PATH holds only a harmless folder."""
    root = tmp_path / "site-packages"
    root.mkdir()
    monkeypatch.setattr(crp.sys, "platform", "win32")
    monkeypatch.setattr(crp.sysconfig, "get_paths", lambda: {"purelib": str(root), "platlib": str(root)})
    monkeypatch.setattr(crp.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setenv("PATH", str(tmp_path / "elsewhere"))
    HANDLES.clear()
    monkeypatch.setattr(os, "add_dll_directory", lambda d: HANDLES.append(d) or object(), raising=False)
    monkeypatch.setattr(crp, "_dll_handles", [])
    monkeypatch.setattr(crp, "_added_dirs", set())
    return root


def _put(folder, *names):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_bytes(b"")


CUBLAS = ("cublas64_12.dll",)
CUDNN = ("cudnn64_9.dll", "cudnn_ops64_9.dll")


def test_torch_lib_is_added_to_the_dll_search_and_path(site):
    _put(site / "torch" / "lib", *CUBLAS, *CUDNN)

    result = crp.ensure_cuda_runtime_on_path()

    assert result == {"status": "ok", "found": ["torch"], "added": True}
    assert HANDLES == [str(site / "torch" / "lib")]
    assert os.environ["PATH"].split(os.pathsep)[0] == str(site / "torch" / "lib")


def test_nvidia_wheels_are_found_in_their_bin_folders(site):
    _put(site / "nvidia" / "cublas" / "bin", *CUBLAS)
    _put(site / "nvidia" / "cudnn" / "bin", *CUDNN)

    result = crp.ensure_cuda_runtime_on_path()

    assert result["status"] == "ok"
    assert result["found"] == ["nvidia-cublas", "nvidia-cudnn"]
    assert set(HANDLES) == {str(site / "nvidia" / "cublas" / "bin"), str(site / "nvidia" / "cudnn" / "bin")}


def test_a_folder_already_on_path_counts(site, tmp_path, monkeypatch):
    elsewhere = tmp_path / "cuda-bin"
    _put(elsewhere, *CUBLAS, *CUDNN)
    monkeypatch.setenv("PATH", str(elsewhere))

    assert crp.ensure_cuda_runtime_on_path()["found"] == ["PATH"]


def test_cudnn_without_its_companion_dll_is_not_enough(site):
    _put(site / "torch" / "lib", *CUBLAS, "cudnn64_9.dll")

    result = crp.ensure_cuda_runtime_on_path()

    assert result["status"] == "missing"


def test_nothing_found_is_missing_and_holds_no_path(site):
    before = os.environ["PATH"]

    result = crp.ensure_cuda_runtime_on_path()

    assert result == {"status": "missing", "found": [], "added": False}
    assert os.environ["PATH"] == before and HANDLES == []
    assert str(site) not in repr(result)


def test_it_is_idempotent(site):
    _put(site / "torch" / "lib", *CUBLAS, *CUDNN)
    crp.ensure_cuda_runtime_on_path()
    path_after_first = os.environ["PATH"]

    second = crp.ensure_cuda_runtime_on_path()

    assert second == {"status": "ok", "found": ["torch"], "added": False}
    assert os.environ["PATH"] == path_after_first and len(HANDLES) == 1


def test_off_windows_it_changes_nothing(site, monkeypatch):
    _put(site / "torch" / "lib", *CUBLAS, *CUDNN)
    monkeypatch.setattr(crp.sys, "platform", "linux")
    before = os.environ["PATH"]

    assert crp.ensure_cuda_runtime_on_path() == {"status": "unknown", "found": [], "added": False}
    assert os.environ["PATH"] == before and HANDLES == []


def test_it_never_raises(site, monkeypatch):
    monkeypatch.setattr(crp, "_candidates", lambda: 1 / 0)

    assert crp.ensure_cuda_runtime_on_path()["status"] == "unknown"


def test_it_does_not_import_torch_or_ctranslate2(site):
    _put(site / "torch" / "lib", *CUBLAS, *CUDNN)
    for name in ("torch", "ctranslate2"):
        sys.modules.pop(name, None)

    crp.ensure_cuda_runtime_on_path()

    assert "torch" not in sys.modules and "ctranslate2" not in sys.modules


# --- the preflight and the model loader use it --------------------------------

def test_the_preflight_fixes_the_path_before_it_loads_the_dll(monkeypatch):
    calls = []
    monkeypatch.setattr(whisper_models.sys, "platform", "win32")
    monkeypatch.setattr(whisper_models.cuda_runtime_path, "ensure_cuda_runtime_on_path",
                        lambda: calls.append("ensure"))
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))

    result = whisper_models.whisper_gpu_runtime(loader=lambda name: calls.append("load"))

    assert calls == ["ensure", "load"]
    assert result == {"status": "ok", "reason": None}


def test_the_preflight_reason_stays_fixed_text(monkeypatch):
    monkeypatch.setattr(whisper_models.sys, "platform", "win32")
    monkeypatch.setattr(whisper_models.cuda_runtime_path, "ensure_cuda_runtime_on_path", lambda: None)
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))

    def refuse(name):
        raise OSError(r"C:\secret\folder\cublas64_12.dll")

    result = whisper_models.whisper_gpu_runtime(loader=refuse)

    assert result == {"status": "missing", "reason": whisper_models.GPU_RUNTIME_MISSING_REASON}


def test_loading_a_model_fixes_the_path_before_faster_whisper_is_used(monkeypatch):
    order = []
    monkeypatch.setattr(whisper_models.cuda_runtime_path, "ensure_cuda_runtime_on_path",
                        lambda: order.append("ensure"))

    class FakeModel:
        def __init__(self, *a, **k):
            order.append("model")
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(whisper_models, "_whisper_model_cache", {})

    whisper_models.load_whisper_model("tiny", use_gpu=False)

    assert order[:2] == ["ensure", "model"]


# --- the install job ----------------------------------------------------------

def test_the_install_runs_cublas_then_cudnn_and_then_fixes_the_path(monkeypatch):
    order = []
    monkeypatch.setattr(gaps, "install_dependency",
                        lambda name, confirm, job_id=None: order.append(name) or {"ok": True})
    monkeypatch.setattr(installs.cuda_runtime_path, "ensure_cuda_runtime_on_path",
                        lambda: order.append("ensure"))

    assert installs._install_cuda_runtime()["ok"] is True
    assert order == ["nvidia-cublas-cu12", "nvidia-cudnn-cu12", "ensure"]


def test_a_failed_first_package_stops_the_install(monkeypatch):
    order = []
    monkeypatch.setattr(gaps, "install_dependency",
                        lambda name, confirm, job_id=None: order.append(name) or {"ok": False})
    monkeypatch.setattr(installs.cuda_runtime_path, "ensure_cuda_runtime_on_path",
                        lambda: order.append("ensure"))

    assert installs._install_cuda_runtime()["ok"] is False
    assert order == ["nvidia-cublas-cu12"]


def test_both_wheels_are_installable_packages():
    assert set(installs.CUDA_RUNTIME_PACKAGES) <= gaps.installable_packages()


# --- the Diagnostics row ------------------------------------------------------

@pytest.fixture
def row(monkeypatch):
    state = {"status": "missing", "gpu": True}
    monkeypatch.setattr(cuda_runtime_service.cuda_runtime_path, "ensure_cuda_runtime_on_path",
                        lambda: {"status": state["status"], "found": [], "added": False})
    monkeypatch.setattr(cuda_runtime_service.shutil, "which",
                        lambda name: "nvidia-smi" if state["gpu"] else None)
    installs._DEPENDENCY.update(kind=None, package=None, last=None)
    return state


def test_the_row_appears_only_when_the_libraries_are_missing(row):
    assert cuda_runtime_service.get_status()["show"] is True
    row["status"] = "ok"
    assert cuda_runtime_service.get_status()["show"] is False
    row["status"] = "unknown"
    assert cuda_runtime_service.get_status()["show"] is False


def test_the_row_is_hidden_without_an_nvidia_gpu(row):
    row["gpu"] = False

    assert cuda_runtime_service.get_status()["show"] is False


def test_the_row_stays_after_an_install_to_show_its_result(row):
    installs._DEPENDENCY.update(kind="cuda_runtime", package="nvidia-cublas-cu12",
                                last={"ok": True, "package": "nvidia-cublas-cu12"})
    row["status"] = "ok"

    status = cuda_runtime_service.get_status()

    assert status["show"] is True and status["missing"] is False


def test_install_is_pc_only(isolated_db, row):
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url="https://example.test", raise_server_exceptions=False)
    admin = auth_service.create_session(auth_service.grant_admin_local("a@example.com")["id"])
    headers = {"Cookie": f"{api_auth.COOKIE_NAME}={admin['session_token']}",
               api_auth.CSRF_HEADER: admin["csrf_token"]}

    assert remote.get("/api/diagnostics/cuda-runtime", headers=headers).status_code == 200
    assert remote.post("/api/diagnostics/cuda-runtime/install", json={"confirm": True},
                       headers=headers).status_code == 403

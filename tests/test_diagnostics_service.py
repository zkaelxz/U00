"""
Tests for services/diagnostics_service.py -- Migration Slice 5's
read-only Diagnostics overview, shared by the FastAPI /api/diagnostics
route and (later) the Streamlit Diagnostics tab.

No admin action is exposed by this service (install/upgrade/delete stay
Streamlit-only) -- these tests exist to confirm the overview's shape and
that job/log data is redacted before leaving the service, the same
scope guarantee tests/test_reader_service.py checks for Slice 4.
"""

import os
import sys

import pytest

import background_jobs
from services import diagnostics_service


def test_overview_has_every_expected_section(isolated_db):
    overview = diagnostics_service.get_diagnostics_overview()
    assert set(overview) == {
        "dependencies", "file_completeness", "library_writable", "gpu",
        "model_engine_versions", "running_jobs", "recent_log_lines",
    }
    assert isinstance(overview["dependencies"], dict)
    assert isinstance(overview["model_engine_versions"], list)
    assert isinstance(overview["running_jobs"], list)
    assert isinstance(overview["recent_log_lines"], list)


def test_library_writable_is_true_for_a_real_isolated_library(isolated_db):
    assert diagnostics_service.get_diagnostics_overview()["library_writable"] is True


def test_running_jobs_reflects_real_job_state(isolated_db):
    background_jobs.start_job("diag-svc-test-job", lambda: None, description="test job")
    overview = diagnostics_service.get_diagnostics_overview()
    job_ids = [j["job_id"] for j in overview["running_jobs"]]
    assert "diag-svc-test-job" in job_ids or not background_jobs.is_running("diag-svc-test-job")
    background_jobs.clear_job("diag-svc-test-job")


def test_job_message_and_error_are_redacted(isolated_db, monkeypatch):
    fake_job = {
        "status": "running", "progress": 0.5,
        "message": "calling with key sk-ABCDEFGHIJKLMNOP12345", "error": None,
        "description": "d", "started_at": 0.0, "finished_at": None,
        "cancel_requested": False, "result": None, "gpu_touching": False, "kind": "thread",
    }
    monkeypatch.setattr(background_jobs, "list_running_jobs", lambda: {"j1": fake_job})
    overview = diagnostics_service.get_diagnostics_overview()
    assert len(overview["running_jobs"]) == 1
    assert "sk-ABCDEFGHIJKLMNOP12345" not in overview["running_jobs"][0]["message"]


def test_result_field_is_never_exposed(isolated_db, monkeypatch):
    fake_job = {
        "status": "finished", "progress": 1.0, "message": "done", "error": None,
        "description": "d", "started_at": 0.0, "finished_at": 1.0,
        "cancel_requested": False, "result": {"secret": "should never leave the service"},
        "gpu_touching": False, "kind": "thread",
    }
    monkeypatch.setattr(background_jobs, "list_running_jobs", lambda: {"j1": fake_job})
    overview = diagnostics_service.get_diagnostics_overview()
    assert "result" not in overview["running_jobs"][0]


@pytest.fixture
def fake_gpu_modules(monkeypatch, tmp_path):
    """A fake torch (8 GB GPU) and ctranslate2. Fakes on PYTHONPATH reach the
    GPU child too, so a GPU answer proves it came from there."""
    import diagnostics_torch
    (tmp_path / "torch").mkdir()
    (tmp_path / "torch" / "__init__.py").write_text(
        "import types\n"
        "version = types.SimpleNamespace(cuda='12.8')\n"
        "_props = types.SimpleNamespace(name='Fake GPU', total_memory=8 * 1024 ** 3)\n"
        "cuda = types.SimpleNamespace(is_available=lambda: True, current_device=lambda: 0,\n"
        "                             get_device_properties=lambda i: _props,\n"
        "                             memory_allocated=lambda i: 0)\n")
    (tmp_path / "ctranslate2.py").write_text("def get_cuda_device_count():\n    return 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        [str(tmp_path)] + [p for p in [os.environ.get("PYTHONPATH")] if p]))
    for name in ("torch", "ctranslate2"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(diagnostics_torch, "_gpu_status_cache", {"at": None, "value": None})


def test_overview_reads_the_gpu_without_loading_torch_or_ctranslate2(
        isolated_db, fake_gpu_modules):
    """Loading either in the server keeps CUDA DLLs locked, so GPU PyTorch
    setup can't replace them."""
    gpu = diagnostics_service.get_diagnostics_overview()["gpu"]

    assert "torch" not in sys.modules and "ctranslate2" not in sys.modules
    assert gpu["available"] is True and gpu["name"] == "Fake GPU"
    assert gpu["whisper"]["ctranslate2_cuda_devices"] == 1


def test_setup_checks_and_bug_report_read_the_gpu_without_loading_torch(
        isolated_db, fake_gpu_modules, monkeypatch):
    """The Diagnostics page fetches the setup checks alongside the overview,
    and a bug report includes them; pyannote 4 adds the low-VRAM check."""
    import diagnostics
    from services import bug_report_service, diagnostics_gaps_service
    real_version = diagnostics.get_installed_version
    monkeypatch.setattr(diagnostics, "get_installed_version",
                        lambda n: "4.0.1" if n == "pyannote.audio" else real_version(n))
    monkeypatch.setattr(bug_report_service, "_CACHE", {})

    checks = diagnostics_gaps_service.get_setup_checks()
    summary = bug_report_service._setup_summary()

    assert "torch" not in sys.modules and "ctranslate2" not in sys.modules
    assert checks["cuda"] == {"torch_installed": True, "cuda_available": True}
    assert any("less than 12 GB" in w for w in checks["warnings"])
    assert "CUDA available" in summary


def test_check_cuda_isolated_tells_cpu_only_torch_from_a_failed_check(monkeypatch):
    import diagnostics
    import diagnostics_torch
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
    for gpu, expected in [({"available": True}, True),
                          ({"available": False, "torch_cuda_version": None}, False),
                          ({"available": False, "message": "GPU info unavailable."}, None)]:
        monkeypatch.setattr(diagnostics_torch, "get_gpu_status_isolated", lambda g=gpu: g)
        assert diagnostics_torch.check_cuda_isolated() == {
            "torch_installed": True, "cuda_available": expected}


def test_no_gpu_child_runs_while_an_install_holds_the_library(monkeypatch):
    """A child importing torch while setup replaces its files can lock them."""
    import diagnostics_torch
    calls = []
    monkeypatch.setattr(diagnostics_torch, "_gpu_status_cache", {"at": None, "value": None})
    monkeypatch.setattr(diagnostics_torch, "_gpu_status_from_child",
                        lambda: calls.append(1) or {"available": True, "whisper": {},
                                                    "probed": True})
    assert background_jobs.acquire_exclusive("GPU PyTorch setup")
    try:
        paused = diagnostics_torch.get_gpu_status_isolated()
    finally:
        background_jobs.release_exclusive()
    assert paused["available"] is False
    assert paused["message"] == diagnostics_torch.GPU_CHECK_PAUSED_MESSAGE
    assert calls == []

    assert diagnostics_torch.get_gpu_status_isolated()["available"] is True
    monkeypatch.setitem(diagnostics_torch._gpu_status_cache, "at", -1e9)
    assert background_jobs.acquire_exclusive("GPU PyTorch setup")
    try:
        # A stale answer beats none while the install runs.
        assert diagnostics_torch.get_gpu_status_isolated()["available"] is True
    finally:
        background_jobs.release_exclusive()
    assert calls == [1]


def test_a_good_gpu_answer_is_kept_until_an_install(monkeypatch):
    import time
    import diagnostics_torch
    from services import diagnostics_gaps_service
    calls = []
    monkeypatch.setattr(diagnostics_torch, "_gpu_status_cache", {"at": None, "value": None})
    monkeypatch.setattr(diagnostics_torch, "_gpu_status_from_child",
                        lambda: calls.append(1) or {"available": True, "whisper": {},
                                                    "probed": True})
    assert "probed" not in diagnostics_torch.get_gpu_status_isolated()
    monkeypatch.setitem(diagnostics_torch._gpu_status_cache, "at",
                        time.monotonic() - diagnostics_torch.GPU_STATUS_RETRY_SECONDS - 1)
    diagnostics_torch.get_gpu_status_isolated()
    assert len(calls) == 1
    diagnostics_gaps_service._clear_update_cache()
    diagnostics_torch.get_gpu_status_isolated()
    assert len(calls) == 2


def test_a_failed_gpu_check_is_retried_soon(monkeypatch):
    import time
    import diagnostics_torch
    calls = []
    monkeypatch.setattr(diagnostics_torch, "_gpu_status_cache", {"at": None, "value": None})
    monkeypatch.setattr(diagnostics_torch, "_gpu_status_from_child",
                        lambda: calls.append(1) or diagnostics_torch._gpu_status_unavailable())
    diagnostics_torch.get_gpu_status_isolated()
    monkeypatch.setitem(diagnostics_torch._gpu_status_cache, "at",
                        time.monotonic() - diagnostics_torch.GPU_STATUS_RETRY_SECONDS - 1)
    diagnostics_torch.get_gpu_status_isolated()
    assert len(calls) == 2


def test_gpu_status_is_cached_briefly(monkeypatch):
    import diagnostics_torch
    calls = []
    monkeypatch.setattr(diagnostics_torch, "_gpu_status_cache", {"at": None, "value": None})
    monkeypatch.setattr(diagnostics_torch, "_gpu_status_from_child",
                        lambda: calls.append(1) or {"available": False, "whisper": {}})
    diagnostics_torch.get_gpu_status_isolated()
    diagnostics_torch.get_gpu_status_isolated()["available"] = True
    assert diagnostics_torch.get_gpu_status_isolated()["available"] is False
    assert len(calls) == 1

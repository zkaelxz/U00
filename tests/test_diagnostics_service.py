"""
Tests for services/diagnostics_service.py -- Migration Slice 5's
read-only Diagnostics overview, shared by the FastAPI /api/diagnostics
route and (later) the Streamlit Diagnostics tab.

No admin action is exposed by this service (install/upgrade/delete stay
Streamlit-only) -- these tests exist to confirm the overview's shape and
that job/log data is redacted before leaving the service, the same
scope guarantee tests/test_reader_service.py checks for Slice 4.
"""

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


def test_overview_reads_the_gpu_without_loading_torch_or_ctranslate2(
        isolated_db, monkeypatch, tmp_path):
    """Loading either in the server keeps CUDA DLLs locked, so GPU PyTorch
    setup can't replace them. Fakes on PYTHONPATH reach the child too, which
    proves the GPU block came from there."""
    import os
    import sys
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

    gpu = diagnostics_service.get_diagnostics_overview()["gpu"]

    assert "torch" not in sys.modules and "ctranslate2" not in sys.modules
    assert gpu["available"] is True and gpu["name"] == "Fake GPU"
    assert gpu["whisper"]["ctranslate2_cuda_devices"] == 1


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

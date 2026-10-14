"""
tests/test_install_buttons.py -- Step 18c: in-app "Install" buttons for
optional dependencies, plus the one-click "Install GPU PyTorch" action.
Everything here that would otherwise shell out to a real `pip` is
driven through a fake `subprocess.Popen`, so no test here ever makes a
real network call or actually installs anything.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diagnostics
import diagnostics_torch
from lib.proc import CapturedRun


class TestParseRequirementsFile:
    def test_skips_comments_and_blank_lines(self, tmp_path):
        p = tmp_path / "reqs.txt"
        p.write_text("# a header comment\n\nrequests>=2.32\n\n# section\nurllib3>=2.6\n")
        assert diagnostics.parse_requirements_file(str(p)) == ["requests>=2.32", "urllib3>=2.6"]

    def test_strips_trailing_inline_comments(self, tmp_path):
        p = tmp_path / "reqs.txt"
        p.write_text("requests>=2.31  # needed for X\n")
        assert diagnostics.parse_requirements_file(str(p)) == ["requests>=2.31"]

    def test_a_fully_commented_out_line_is_never_returned(self, tmp_path):
        # requirements-optional.txt's own pattern: three TTS engines whose
        # deps conflict, only one ever uncommented at a time.
        p = tmp_path / "reqs.txt"
        p.write_text("# omnivoice>=0.2\nchatterbox-tts>=0.9\n")
        assert diagnostics.parse_requirements_file(str(p)) == ["chatterbox-tts>=0.9"]

    def test_missing_file_returns_empty_list(self, tmp_path):
        assert diagnostics.parse_requirements_file(str(tmp_path / "nope.txt")) == []


class TestExternalGpuLoad:
    """Step 26d: real GPU load straight from nvidia-smi, independent of
    anything Baihe itself is tracking -- the only way to see a different
    application (Jellyfin's hardware-accelerated transcoding on the same
    card, say) using the same physical GPU."""

    @pytest.fixture(autouse=True)
    def _fresh_driver_cache(self):
        diagnostics_torch.forget_driver_load()

    def _fake_run(self, stdout):
        def run(cmd, timeout):
            assert cmd[0] == "nvidia-smi"
            return CapturedRun(0, stdout, "", False, False)
        return run

    def test_none_when_nvidia_smi_not_on_path(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: None)
        assert diagnostics_torch.external_gpu_load() is None
        assert diagnostics_torch.external_gpu_is_busy() is False

    def test_parses_a_real_nvidia_smi_response(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch, "run_captured",
                            self._fake_run("72, 9500, 12288\n"))
        load = diagnostics_torch.external_gpu_load()
        assert load == {"utilization_percent": 72.0, "memory_used_mb": 9500.0,
                        "memory_total_mb": 12288.0, "memory_free_mb": 2788.0}

    def test_none_on_a_failed_or_malformed_query(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

        def raises(*a, **kw):
            raise OSError("nvidia-smi vanished")
        monkeypatch.setattr(diagnostics_torch, "run_captured", raises)
        assert diagnostics_torch.external_gpu_load() is None
        assert diagnostics_torch.external_gpu_is_busy() is False  # never blocks when it can't tell

    def test_busy_when_utilization_crosses_the_threshold(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch, "run_captured",
                            self._fake_run("55, 2000, 12288\n"))  # busy: high util, plenty free
        assert diagnostics_torch.external_gpu_is_busy() is True

    def test_busy_when_free_vram_is_low_even_at_low_utilization(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch, "run_captured",
                            self._fake_run("5, 11800, 12288\n"))  # idle compute, almost no VRAM left
        assert diagnostics_torch.external_gpu_is_busy() is True

    def test_not_busy_when_idle_and_plenty_free(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch, "run_captured",
                            self._fake_run("3, 500, 12288\n"))
        assert diagnostics_torch.external_gpu_is_busy() is False

    def test_none_on_a_nonzero_exit_or_timeout(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        for run in (CapturedRun(9, "", "driver mismatch", False, False), CapturedRun(None, "", "", True, False)):
            monkeypatch.setattr(diagnostics_torch, "run_captured", lambda cmd, timeout, r=run: r)
            assert diagnostics_torch.external_gpu_load() is None

    def test_overview_reads_memory_in_use_from_the_driver_not_torch(self, monkeypatch):
        cached = {"available": True, "name": "RTX", "vram_used_gb": 0.0, "vram_total_gb": 12.0}
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch, "run_captured", self._fake_run("90, 6144, 12288\n"))
        assert diagnostics_torch.with_driver_vram_used(dict(cached))["vram_used_gb"] == 6.0

    def test_overview_total_and_used_come_from_the_same_nvidia_smi_card(self, monkeypatch):
        # torch's current_device can be a different card than nvidia-smi's first row.
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch, "run_captured", self._fake_run("90, 6144, 24576\n"))
        out = diagnostics_torch.with_driver_vram_used({"available": True, "name": "RTX", "vram_total_gb": 8.0})
        assert (out["vram_used_gb"], out["vram_total_gb"]) == (6.0, 24.0)

    def test_overview_name_and_memory_come_from_the_same_row_on_a_two_gpu_pc(self, monkeypatch):
        # Two cards; torch's current_device could be the second, but the memory
        # shown is row 0's, so the name must be row 0's too.
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch, "run_captured", self._fake_run(
            "90, 6144, 24576, NVIDIA GeForce RTX 4090\n4, 300, 8192, NVIDIA GeForce RTX 3060\n"))
        out = diagnostics_torch.with_driver_vram_used(
            {"available": True, "name": "NVIDIA GeForce RTX 3060", "vram_total_gb": 8.0})
        assert (out["name"], out["vram_used_gb"], out["vram_total_gb"]) == (
            "NVIDIA GeForce RTX 4090", 6.0, 24.0)

    def test_gpu_reading_is_name_and_memory_of_one_card(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch, "run_captured", self._fake_run(
            "90, 6144, 24576, NVIDIA GeForce RTX 4090\n4, 300, 8192, NVIDIA GeForce RTX 3060\n"))
        assert diagnostics_torch.gpu_reading() == {
            "found": True, "name": "NVIDIA GeForce RTX 4090",
            "vram_used_gb": 6.0, "vram_total_gb": 24.0}

    def test_gpu_reading_without_nvidia_smi_is_not_found(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: None)
        assert diagnostics_torch.gpu_reading() == {
            "found": False, "name": None, "vram_used_gb": None, "vram_total_gb": None}

    def test_overview_reads_share_one_nvidia_smi_run_within_the_cache_window(self, monkeypatch):
        calls = []
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch, "run_captured",
                            lambda cmd, timeout: calls.append(1) or CapturedRun(0, "1, 2048, 8192\n", "", False, False))
        clock = {"t": 1000.0}
        monkeypatch.setattr(diagnostics_torch.time, "monotonic", lambda: clock["t"])
        diagnostics_torch.forget_driver_load()
        gpu = {"available": True, "name": "RTX", "vram_total_gb": 8.0}
        diagnostics_torch.with_driver_vram_used(dict(gpu))
        diagnostics_torch.with_driver_vram_used(dict(gpu))
        assert len(calls) == 1
        clock["t"] += diagnostics_torch.DRIVER_LOAD_CACHE_SECONDS + 0.1
        diagnostics_torch.with_driver_vram_used(dict(gpu))
        assert len(calls) == 2

    def test_a_failed_reading_is_not_retried_within_the_window(self, monkeypatch):
        calls = []
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch, "run_captured",
                            lambda cmd, timeout: calls.append(1) or CapturedRun(None, "", "", True, False))
        monkeypatch.setattr(diagnostics_torch.time, "monotonic", lambda: 5000.0)
        diagnostics_torch.forget_driver_load()
        for _ in range(3):
            out = diagnostics_torch.with_driver_vram_used({"available": True, "name": "RTX", "vram_total_gb": 8.0})
        assert len(calls) == 1 and out["vram_used_gb"] is None

    def test_overview_says_not_available_when_nvidia_smi_is_missing(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: None)
        out = diagnostics_torch.with_driver_vram_used({"available": True, "name": "RTX", "vram_total_gb": 12.0})
        assert out["vram_used_gb"] is None
        assert "not available" in out["message"]
        assert "/" not in out["message"] and "Traceback" not in out["message"]

    def test_overview_leaves_an_unavailable_gpu_alone(self):
        gpu = {"available": False, "message": "GPU info unavailable."}
        assert diagnostics_torch.with_driver_vram_used(dict(gpu)) == gpu




def test_gpu_route_is_local_only_and_returns_just_the_reading(monkeypatch, isolated_db):
    from fastapi.testclient import TestClient
    from api.server import create_app
    from api.api_config import ApiSettings
    from services import diagnostics_service
    monkeypatch.setattr(diagnostics_service, "get_gpu_reading",
                        lambda: {"found": True, "name": "RTX", "vram_used_gb": 1.0,
                                 "vram_total_gb": 8.0})
    local = TestClient(create_app(ApiSettings()), base_url="http://127.0.0.1:8600",
                       client=("127.0.0.1", 5000),
                       headers={"X-Baihe-Local": "1"})
    r = local.get("/api/diagnostics/gpu")
    assert r.status_code == 200, r.text
    assert r.json() == {"found": True, "name": "RTX", "vram_used_gb": 1.0, "vram_total_gb": 8.0}
    remote = TestClient(create_app(ApiSettings()), base_url="http://127.0.0.1:8600",
                        client=("203.0.113.9", 5000))
    assert remote.get("/api/diagnostics/gpu").status_code in (401, 403)

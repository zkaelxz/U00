"""
tests/test_install_buttons.py -- Step 18c: in-app "Install" buttons for
optional dependencies, plus the one-click "Install GPU PyTorch" action.
Everything here that would otherwise shell out to a real `pip` is
driven through a fake `subprocess.Popen`, so no test here ever makes a
real network call or actually installs anything.
"""
import os
import sys

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

    def test_overview_says_not_available_when_nvidia_smi_is_missing(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: None)
        out = diagnostics_torch.with_driver_vram_used({"available": True, "name": "RTX", "vram_total_gb": 12.0})
        assert out["vram_used_gb"] is None
        assert "not available" in out["message"]
        assert "/" not in out["message"] and "Traceback" not in out["message"]

    def test_overview_leaves_an_unavailable_gpu_alone(self):
        gpu = {"available": False, "message": "GPU info unavailable."}
        assert diagnostics_torch.with_driver_vram_used(dict(gpu)) == gpu



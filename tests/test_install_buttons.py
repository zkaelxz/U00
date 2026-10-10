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


class TestGpuTorchMismatch:
    def test_false_when_nvidia_smi_not_on_path(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: None)
        assert diagnostics_torch.gpu_torch_mismatch() is False

    def test_true_when_gpu_present_but_torch_is_cpu_only(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which",
                            lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
        monkeypatch.setattr(diagnostics, "check_cuda",
                            lambda: {"torch_installed": True, "cuda_available": False})
        assert diagnostics_torch.gpu_torch_mismatch() is True

    def test_false_when_cuda_is_available(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics, "check_cuda",
                            lambda: {"torch_installed": True, "cuda_available": True})
        assert diagnostics_torch.gpu_torch_mismatch() is False

    def test_false_when_torch_isnt_installed_at_all(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics, "check_cuda",
                            lambda: {"torch_installed": False, "cuda_available": None})
        assert diagnostics_torch.gpu_torch_mismatch() is False


class TestExternalGpuLoad:
    """Step 26d: real GPU load straight from nvidia-smi, independent of
    anything Baihe itself is tracking -- the only way to see a different
    application (Jellyfin's hardware-accelerated transcoding on the same
    card, say) using the same physical GPU."""

    def _fake_run(self, stdout):
        def run(cmd, capture_output, text, timeout, check, errors):
            assert cmd[0] == "nvidia-smi"
            class _Result:
                pass
            r = _Result()
            r.stdout = stdout
            return r
        return run

    def test_none_when_nvidia_smi_not_on_path(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: None)
        assert diagnostics_torch.external_gpu_load() is None
        assert diagnostics_torch.external_gpu_is_busy() is False

    def test_parses_a_real_nvidia_smi_response(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch.subprocess, "run",
                            self._fake_run("72, 9500, 12288\n"))
        load = diagnostics_torch.external_gpu_load()
        assert load == {"utilization_percent": 72.0, "memory_used_mb": 9500.0,
                        "memory_total_mb": 12288.0, "memory_free_mb": 2788.0}

    def test_none_on_a_failed_or_malformed_query(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

        def raises(*a, **kw):
            raise diagnostics_torch.subprocess.SubprocessError("nvidia-smi timed out")
        monkeypatch.setattr(diagnostics_torch.subprocess, "run", raises)
        assert diagnostics_torch.external_gpu_load() is None
        assert diagnostics_torch.external_gpu_is_busy() is False  # never blocks when it can't tell

    def test_busy_when_utilization_crosses_the_threshold(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch.subprocess, "run",
                            self._fake_run("55, 2000, 12288\n"))  # busy: high util, plenty free
        assert diagnostics_torch.external_gpu_is_busy() is True

    def test_busy_when_free_vram_is_low_even_at_low_utilization(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch.subprocess, "run",
                            self._fake_run("5, 11800, 12288\n"))  # idle compute, almost no VRAM left
        assert diagnostics_torch.external_gpu_is_busy() is True

    def test_not_busy_when_idle_and_plenty_free(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics_torch.subprocess, "run",
                            self._fake_run("3, 500, 12288\n"))
        assert diagnostics_torch.external_gpu_is_busy() is False



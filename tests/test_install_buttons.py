"""
tests/test_install_buttons.py -- Step 18c: in-app "Install" buttons for
optional dependencies, plus the one-click "Install GPU PyTorch" action.
Everything here that would otherwise shell out to a real `pip` is
driven through a fake `subprocess.Popen`, so no test here ever makes a
real network call or actually installs anything.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diagnostics


class _FakePopen:
    """Just enough of subprocess.Popen's interface for the streaming
    functions under test: an iterable `.stdout` and a `.wait()` that
    returns the recorded return code."""

    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self._returncode = returncode

    def wait(self):
        return self._returncode


class TestStreamPipInstall:
    def test_streams_lines_then_a_final_done_item(self, monkeypatch):
        monkeypatch.setattr(diagnostics.subprocess, "Popen",
                            lambda cmd, **kw: _FakePopen(
                                ["Collecting foo\n", "Successfully installed foo\n"], 0))
        items = list(diagnostics.stream_pip_install(["foo"]))
        assert items[0] == {"line": "Collecting foo"}
        assert items[1] == {"line": "Successfully installed foo"}
        assert items[-1] == {"done": True, "ok": True, "returncode": 0}

    def test_targets_the_running_interpreter_not_a_bare_pip(self, monkeypatch):
        captured = {}

        def fake_popen(cmd, **kw):
            captured["cmd"] = cmd
            return _FakePopen([], 0)
        monkeypatch.setattr(diagnostics.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(diagnostics.sys, "executable", "/venv/bin/python3.14")

        list(diagnostics.stream_pip_install(["bar"]))
        assert captured["cmd"] == ["/venv/bin/python3.14", "-m", "pip", "install",
                                   "--no-cache-dir", "--disable-pip-version-check", "bar"]

    def test_explicit_interpreter_overrides_sys_executable(self, monkeypatch):
        captured = {}

        def fake_popen(cmd, **kw):
            captured["cmd"] = cmd
            return _FakePopen([], 0)
        monkeypatch.setattr(diagnostics.subprocess, "Popen", fake_popen)

        list(diagnostics.stream_pip_install(["bar"], python_executable="/other/python"))
        assert captured["cmd"][0] == "/other/python"

    def test_failure_surfaces_the_real_error_text_not_a_generic_message(self, monkeypatch):
        monkeypatch.setattr(diagnostics.subprocess, "Popen",
                            lambda cmd, **kw: _FakePopen(
                                ["ERROR: build failed for foo (diffq-fixed Cython error)\n"], 1))
        items = list(diagnostics.stream_pip_install(["foo"]))
        assert {"line": "ERROR: build failed for foo (diffq-fixed Cython error)"} in items
        assert items[-1] == {"done": True, "ok": False, "returncode": 1}


class TestStreamPipUninstall:
    def test_passes_dash_y_and_targets_the_running_interpreter(self, monkeypatch):
        captured = {}

        def fake_popen(cmd, **kw):
            captured["cmd"] = cmd
            return _FakePopen([], 0)
        monkeypatch.setattr(diagnostics.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(diagnostics.sys, "executable", "/venv/bin/python")

        list(diagnostics.stream_pip_uninstall(["torch", "torchaudio"]))
        assert captured["cmd"] == ["/venv/bin/python", "-m", "pip", "uninstall", "-y",
                                   "torch", "torchaudio"]


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
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
        assert diagnostics.gpu_torch_mismatch() is False

    def test_true_when_gpu_present_but_torch_is_cpu_only(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which",
                            lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
        monkeypatch.setattr(diagnostics, "check_cuda",
                            lambda: {"torch_installed": True, "cuda_available": False})
        assert diagnostics.gpu_torch_mismatch() is True

    def test_false_when_cuda_is_available(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics, "check_cuda",
                            lambda: {"torch_installed": True, "cuda_available": True})
        assert diagnostics.gpu_torch_mismatch() is False

    def test_false_when_torch_isnt_installed_at_all(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics, "check_cuda",
                            lambda: {"torch_installed": False, "cuda_available": None})
        assert diagnostics.gpu_torch_mismatch() is False


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
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
        assert diagnostics.external_gpu_load() is None
        assert diagnostics.external_gpu_is_busy() is False

    def test_parses_a_real_nvidia_smi_response(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics.subprocess, "run",
                            self._fake_run("72, 9500, 12288\n"))
        load = diagnostics.external_gpu_load()
        assert load == {"utilization_percent": 72.0, "memory_used_mb": 9500.0,
                        "memory_total_mb": 12288.0, "memory_free_mb": 2788.0}

    def test_none_on_a_failed_or_malformed_query(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

        def raises(*a, **kw):
            raise diagnostics.subprocess.SubprocessError("nvidia-smi timed out")
        monkeypatch.setattr(diagnostics.subprocess, "run", raises)
        assert diagnostics.external_gpu_load() is None
        assert diagnostics.external_gpu_is_busy() is False  # never blocks when it can't tell

    def test_busy_when_utilization_crosses_the_threshold(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics.subprocess, "run",
                            self._fake_run("55, 2000, 12288\n"))  # busy: high util, plenty free
        assert diagnostics.external_gpu_is_busy() is True

    def test_busy_when_free_vram_is_low_even_at_low_utilization(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics.subprocess, "run",
                            self._fake_run("5, 11800, 12288\n"))  # idle compute, almost no VRAM left
        assert diagnostics.external_gpu_is_busy() is True

    def test_not_busy_when_idle_and_plenty_free(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(diagnostics.subprocess, "run",
                            self._fake_run("3, 500, 12288\n"))
        assert diagnostics.external_gpu_is_busy() is False


class TestGpuTorchCudaIndex:
    def test_python_3_14_uses_cu128(self, monkeypatch):
        monkeypatch.setattr(diagnostics.sys, "version_info",
                            types.SimpleNamespace(major=3, minor=14))
        assert diagnostics.gpu_torch_cuda_index() == "cu128"

    def test_unlisted_version_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setattr(diagnostics.sys, "version_info",
                            types.SimpleNamespace(major=3, minor=11))
        assert diagnostics.gpu_torch_cuda_index() == diagnostics.GPU_TORCH_CUDA_INDEX_DEFAULT


class TestStreamGpuTorchReinstall:
    def test_uninstalls_then_installs_with_index_and_constraints(self, monkeypatch, tmp_path):
        constraints_file = tmp_path / "constraints.txt"
        constraints_file.write_text("torch<3\ntorchaudio<3\n")

        calls = []

        def fake_popen(cmd, **kw):
            calls.append(cmd)
            return _FakePopen(["ok\n"], 0)
        monkeypatch.setattr(diagnostics.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(diagnostics, "gpu_torch_cuda_index", lambda: "cu128")

        items = list(diagnostics.stream_gpu_torch_reinstall(
            python_executable="/venv/bin/python", project_root=str(tmp_path)))

        assert calls[0] == ["/venv/bin/python", "-m", "pip", "uninstall", "-y",
                            "torch", "torchaudio"]
        install_cmd = calls[1]
        assert install_cmd[:4] == ["/venv/bin/python", "-m", "pip", "install"]
        assert "--index-url" in install_cmd
        assert "https://download.pytorch.org/whl/cu128" in install_cmd
        assert "-c" in install_cmd
        assert str(constraints_file) in install_cmd

    def test_missing_constraints_file_is_skipped_cleanly(self, monkeypatch, tmp_path):
        calls = []

        def fake_popen(cmd, **kw):
            calls.append(cmd)
            return _FakePopen([], 0)
        monkeypatch.setattr(diagnostics.subprocess, "Popen", fake_popen)

        list(diagnostics.stream_gpu_torch_reinstall(
            python_executable="/venv/bin/python", project_root=str(tmp_path)))
        assert "-c" not in calls[1]

    def test_only_the_last_yielded_item_carries_done(self, monkeypatch, tmp_path):
        def fake_popen(cmd, **kw):
            return _FakePopen(["a line\n"], 0)
        monkeypatch.setattr(diagnostics.subprocess, "Popen", fake_popen)

        items = list(diagnostics.stream_gpu_torch_reinstall(
            python_executable="/venv/bin/python", project_root=str(tmp_path)))
        done_items = [i for i in items if i.get("done")]
        assert len(done_items) == 1
        assert items[-1] is done_items[0]

    def test_a_failed_uninstall_step_still_surfaces_its_own_lines(self, monkeypatch, tmp_path):
        calls = []

        def fake_popen(cmd, **kw):
            calls.append(cmd)
            if "uninstall" in cmd:
                return _FakePopen(["nothing to uninstall\n"], 1)
            return _FakePopen(["Successfully installed torch\n"], 0)
        monkeypatch.setattr(diagnostics.subprocess, "Popen", fake_popen)

        items = list(diagnostics.stream_gpu_torch_reinstall(
            python_executable="/venv/bin/python", project_root=str(tmp_path)))
        assert {"line": "nothing to uninstall"} in items
        assert items[-1]["ok"] is True  # only the install step's result is the final "done"



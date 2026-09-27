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
        assert captured["cmd"] == ["/venv/bin/python3.14", "-m", "pip", "install", "bar"]

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


_FAKE_RESULTS = {
    "python": {"version": "3.11.0", "ok": True},
    "ffmpeg": {"found": True, "path": "/usr/bin/ffmpeg", "version": "ffmpeg version x"},
    "js_runtime": {"found": True, "name": "deno", "path": "/usr/bin/deno"},
    "dependencies": {
        "fixture_required": {"installed": True, "powers": "core thing", "tier": "required"},
        "fixture_dev_missing": {"installed": False, "powers": "dev tool", "tier": "dev"},
        "fixture_feature_missing": {"installed": False, "powers": "a feature", "tier": "feature"},
        "fixture_engine_missing": {"installed": False, "powers": "an engine", "tier": "engine"},
        "fixture_feature_installed": {"installed": True, "powers": "already there", "tier": "feature"},
    },
    "files": {"missing_top_level": [], "missing_tabs": [], "all_present": True},
    "library_writable": True,
    "api_keys": {"claude": True},
}


class TestDiagnosticsTabInstallButtonGating:
    """UI-level: the Install button must appear for a not-yet-installed
    feature/engine-tier dependency, and never for required/dev-tier or
    an already-installed one."""

    def _run(self, **state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        at.session_state["diagnostics_results"] = _FAKE_RESULTS
        for k, v in state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        return at

    def test_exactly_the_missing_feature_and_engine_deps_get_a_button(self, isolated_db):
        # Scoped to "Check my setup" -- Step 47 added its own, separate
        # "⬇️ Install" buttons to the Model & engine versions panel below,
        # so filtering the whole page's buttons would also catch those.
        at = self._run()
        setup = next(e for e in at.expander if e.label == "🩺 Check my setup")
        install_buttons = [b for b in setup.button if b.label == "⬇️ Install"]
        assert len(install_buttons) == 2
        keys = {b.key for b in install_buttons}
        assert keys == {"install_dep_btn_fixture_feature_missing",
                        "install_dep_btn_fixture_engine_missing"}


class TestDiagnosticsTabUpgradeButtonGating:
    """Step 27: the Upgrade button must appear only for an installed
    feature/engine-tier dependency the version check flagged outdated --
    never for an up-to-date one, one the check hasn't looked at yet, or a
    required/dev-tier dependency (same tier gating as Install)."""

    def _run(self, version_results):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        at.session_state["diagnostics_results"] = _FAKE_RESULTS
        at.session_state["dependency_version_results"] = version_results
        at.run(timeout=30)
        return at

    def test_upgrade_button_appears_for_an_outdated_feature_tier_dependency(self, isolated_db):
        at = self._run({"fixture_feature_installed": {
            "installed_version": "1.0.0", "latest_version": "2.0.0", "outdated": True}})
        upgrade_buttons = [b for b in at.button if b.label == "⬆️ Upgrade"]
        assert len(upgrade_buttons) == 1
        assert upgrade_buttons[0].key == "upgrade_dep_btn_fixture_feature_installed"

    def test_no_upgrade_button_when_up_to_date(self, isolated_db):
        at = self._run({"fixture_feature_installed": {
            "installed_version": "2.0.0", "latest_version": "2.0.0", "outdated": False}})
        assert not [b for b in at.button if b.label == "⬆️ Upgrade"]

    def test_no_upgrade_button_for_required_tier_even_if_flagged_outdated(self, isolated_db):
        at = self._run({"fixture_required": {
            "installed_version": "1.0.0", "latest_version": "2.0.0", "outdated": True}})
        assert not [b for b in at.button if b.label == "⬆️ Upgrade"]

    def test_no_upgrade_button_before_the_check_has_ever_run(self, isolated_db):
        at = self._run({})
        assert not [b for b in at.button if b.label == "⬆️ Upgrade"]

    def test_clicking_upgrade_calls_pip_install_with_the_upgrade_flag_and_streams_output(
            self, isolated_db, monkeypatch):
        captured = {}

        def fake_stream_pip_install(pip_args, python_executable=None):
            captured["pip_args"] = pip_args
            yield {"line": "Successfully installed fixture-feature-installed-2.0.0"}
            yield {"done": True, "ok": True, "returncode": 0}
        monkeypatch.setattr(diagnostics, "stream_pip_install", fake_stream_pip_install)
        monkeypatch.setattr(diagnostics, "run_full_diagnostics", lambda *a, **k: _FAKE_RESULTS)

        at = self._run({"fixture_feature_installed": {
            "installed_version": "1.0.0", "latest_version": "2.0.0", "outdated": True}})
        at.button(key="upgrade_dep_btn_fixture_feature_installed").click().run(timeout=30)

        assert not at.exception
        # --upgrade, not a bare install -- and the real package name, not
        # a generic placeholder.
        assert captured["pip_args"][:2] == ["--upgrade", "fixture_feature_installed"]
        # A successful upgrade drops the now-stale version-check result
        # rather than silently re-showing "outdated" for the new version.
        assert ("dependency_version_results" not in at.session_state
                or at.session_state["dependency_version_results"] is None
                or "fixture_feature_installed" not in at.session_state["dependency_version_results"])


class TestDiagnosticsTabCheckForUpdatesButton:
    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        at.session_state["diagnostics_results"] = _FAKE_RESULTS
        at.run(timeout=30)
        return at

    def test_button_present_and_makes_no_network_call_until_clicked(self, isolated_db, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("should not touch the network before the button is clicked")
        monkeypatch.setattr("requests.get", boom)

        at = self._run()
        assert [b for b in at.button if b.label == "🔍 Check for dependency updates"]
        assert "dependency_version_results" not in at.session_state \
            or at.session_state["dependency_version_results"] is None

    def test_clicking_it_populates_version_results(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_dependency_versions",
                            lambda deps, timeout=10.0: {"fixture_feature_installed": {
                                "installed_version": "1.0.0", "latest_version": "2.0.0",
                                "outdated": True}})
        at = self._run()
        [btn] = [b for b in at.button if b.label == "🔍 Check for dependency updates"]
        btn.click().run(timeout=30)
        assert not at.exception
        assert at.session_state["dependency_version_results"][
            "fixture_feature_installed"]["outdated"] is True


class TestDiagnosticsTabGpuTorchButtonVisibility:
    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        at.session_state["diagnostics_results"] = _FAKE_RESULTS
        at.run(timeout=30)
        return at

    def test_button_appears_when_a_real_mismatch_is_detected(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "gpu_torch_mismatch", lambda: True)
        at = self._run()
        assert [b for b in at.button if b.label == "⚡ Install GPU PyTorch"]

    def test_button_absent_with_no_mismatch(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "gpu_torch_mismatch", lambda: False)
        at = self._run()
        assert not [b for b in at.button if b.label == "⚡ Install GPU PyTorch"]

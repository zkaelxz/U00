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


class TestParseRequirementsFile:
    def test_skips_comments_and_blank_lines(self, tmp_path):
        p = tmp_path / "reqs.txt"
        p.write_text("# a header comment\n\nstreamlit>=1.49\n\n# section\npandas>=2.0\n")
        assert diagnostics.parse_requirements_file(str(p)) == ["streamlit>=1.49", "pandas>=2.0"]

    def test_strips_trailing_inline_comments(self, tmp_path):
        p = tmp_path / "reqs.txt"
        p.write_text("requests>=2.31  # needed for X\n")
        assert diagnostics.parse_requirements_file(str(p)) == ["requests>=2.31"]

    def test_a_fully_commented_out_line_is_never_returned(self, tmp_path):
        # requirements-optional.txt's own pattern: three TTS engines whose
        # deps conflict, only one ever uncommented at a time.
        p = tmp_path / "reqs.txt"
        p.write_text("# omnivoice>=0.2\nf5-tts>=0.9\n")
        assert diagnostics.parse_requirements_file(str(p)) == ["f5-tts>=0.9"]

    def test_missing_file_returns_empty_list(self, tmp_path):
        assert diagnostics.parse_requirements_file(str(tmp_path / "nope.txt")) == []


class TestStreamBulkInstall:
    def test_installs_every_package_and_reports_all_results(self, monkeypatch, tmp_path):
        p = tmp_path / "reqs.txt"
        p.write_text("foo>=1\nbar>=2\n")
        popen_calls = []

        def fake_popen(cmd, **kw):
            popen_calls.append(cmd)
            ok = "bar" not in cmd[-1]
            return _FakePopen([f"installing {cmd[-1]}\n"], 0 if ok else 1)
        monkeypatch.setattr(diagnostics.subprocess, "Popen", fake_popen)

        items = list(diagnostics.stream_bulk_install(str(p)))
        assert len(popen_calls) == 2  # bar's failure didn't stop foo... or itself
        bulk_done = [i for i in items if i.get("bulk_done")][0]
        assert bulk_done["results"] == {"foo>=1": True, "bar>=2": False}

    def test_tags_every_event_with_its_own_package(self, monkeypatch, tmp_path):
        p = tmp_path / "reqs.txt"
        p.write_text("foo>=1\n")
        monkeypatch.setattr(diagnostics.subprocess, "Popen",
                            lambda cmd, **kw: _FakePopen(["a line\n"], 0))
        items = list(diagnostics.stream_bulk_install(str(p)))
        assert {"package": "foo>=1", "start": True} in items
        assert {"package": "foo>=1", "line": "a line"} in items
        assert {"package": "foo>=1", "done": True, "ok": True} in items

    def test_empty_file_yields_only_the_bulk_done_summary(self, tmp_path):
        p = tmp_path / "reqs.txt"
        p.write_text("# nothing real in here\n")
        assert list(diagnostics.stream_bulk_install(str(p))) == [{"bulk_done": True, "results": {}}]


class TestStreamDenoInstall:
    def test_already_on_path_does_nothing(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/deno")
        items = list(diagnostics.stream_deno_install())
        assert items[-1] == {"done": True, "ok": True, "on_path": True, "needs_restart": False}

    def test_prefers_winget_on_windows_when_available(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which",
                            lambda name: None if name == "deno" else "/x/winget")
        monkeypatch.setattr(diagnostics.platform, "system", lambda: "Windows")
        captured = {}

        def fake_popen(cmd, **kw):
            captured["cmd"] = cmd
            return _FakePopen([], 0)
        monkeypatch.setattr(diagnostics.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(diagnostics.os.path, "exists", lambda p: True)

        list(diagnostics.stream_deno_install())
        assert captured["cmd"][:2] == ["winget", "install"]

    def test_falls_back_to_the_powershell_script_on_windows_without_winget(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
        monkeypatch.setattr(diagnostics.platform, "system", lambda: "Windows")
        captured = {}

        def fake_popen(cmd, **kw):
            captured["cmd"] = cmd
            return _FakePopen([], 0)
        monkeypatch.setattr(diagnostics.subprocess, "Popen", fake_popen)

        list(diagnostics.stream_deno_install())
        assert "deno.land/install.ps1" in captured["cmd"][-1]

    def test_uses_the_shell_script_on_linux_and_mac(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
        monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
        captured = {}

        def fake_popen(cmd, **kw):
            captured["cmd"] = cmd
            return _FakePopen([], 0)
        monkeypatch.setattr(diagnostics.subprocess, "Popen", fake_popen)

        list(diagnostics.stream_deno_install())
        assert captured["cmd"][0] == "sh"
        assert "deno.land/install.sh" in captured["cmd"][-1]

    def test_success_but_not_yet_on_path_reports_needs_restart(self, monkeypatch, tmp_path):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
        monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
        monkeypatch.setattr(diagnostics.subprocess, "Popen",
                            lambda cmd, **kw: _FakePopen(["installed to ~/.deno/bin\n"], 0))
        monkeypatch.setattr(diagnostics, "_deno_default_install_path", lambda: str(tmp_path / "deno"))
        (tmp_path / "deno").write_text("")

        items = list(diagnostics.stream_deno_install())
        assert items[-1] == {"done": True, "ok": True, "on_path": False, "needs_restart": True}

    def test_failed_install_reports_not_ok(self, monkeypatch, tmp_path):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
        monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
        monkeypatch.setattr(diagnostics.subprocess, "Popen",
                            lambda cmd, **kw: _FakePopen(["curl: command not found\n"], 127))
        monkeypatch.setattr(diagnostics, "_deno_default_install_path", lambda: str(tmp_path / "deno"))

        items = list(diagnostics.stream_deno_install())
        assert items[-1]["ok"] is False
        assert items[-1]["needs_restart"] is False


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
        def run(cmd, capture_output, text, timeout, check):
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
        # "Install" buttons to the Model & engine versions panel below,
        # so filtering the whole page's buttons would also catch those.
        at = self._run()
        setup = next(e for e in at.expander if e.label == "🩺 Check my setup")
        install_buttons = [b for b in setup.button if b.label == "Install"]
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
        upgrade_buttons = [b for b in at.button if b.label == "Upgrade"]
        assert len(upgrade_buttons) == 1
        assert upgrade_buttons[0].key == "upgrade_dep_btn_fixture_feature_installed"

    def test_no_upgrade_button_when_up_to_date(self, isolated_db):
        at = self._run({"fixture_feature_installed": {
            "installed_version": "2.0.0", "latest_version": "2.0.0", "outdated": False}})
        assert not [b for b in at.button if b.label == "Upgrade"]

    def test_no_upgrade_button_for_required_tier_even_if_flagged_outdated(self, isolated_db):
        at = self._run({"fixture_required": {
            "installed_version": "1.0.0", "latest_version": "2.0.0", "outdated": True}})
        assert not [b for b in at.button if b.label == "Upgrade"]

    def test_no_upgrade_button_before_the_check_has_ever_run(self, isolated_db):
        at = self._run({})
        assert not [b for b in at.button if b.label == "Upgrade"]

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


class TestModelPanelRedundantTtsConfirm:
    """Step 47 item 4: installing a heavy local TTS backend when a
    functionally-equivalent one is already installed asks first (a full
    second click), rather than either silently proceeding or blocking
    outright."""

    def _run(self, **state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        at.session_state["diagnostics_results"] = _FAKE_RESULTS
        monkeypatch = state.pop("_monkeypatch")
        monkeypatch.setattr(diagnostics, "get_model_engine_versions", lambda ollama_model=None: [
            {"name": "Chatterbox", "version": "1.0.0", "url": "https://example.com/chatterbox",
             "installed": True, "package": "chatterbox-tts",
             "help": "A local voice-cloning engine."},
            {"name": "OmniVoice", "version": "not installed", "url": "https://example.com/omnivoice",
             "installed": False, "package": "omnivoice", "help": "Another local voice-cloning engine."},
        ])
        for k, v in state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        return at

    def test_first_click_shows_a_warning_instead_of_installing(self, isolated_db, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(diagnostics, "stream_dependency_install",
                            lambda *a, **k: called.__setitem__("n", called["n"] + 1) or iter(
                                [{"done": True, "ok": True, "returncode": 0}]))
        at = self._run(_monkeypatch=monkeypatch)
        at.button(key="install_model_btn_OmniVoice").click().run(timeout=30)
        assert not at.exception
        assert called["n"] == 0
        warnings = [w.value for w in at.warning]
        assert any("Chatterbox" in w and "OmniVoice" in w for w in warnings)
        assert [b for b in at.button if b.label == "Install anyway"]
        assert [b for b in at.button if b.label == "Cancel"]

    def test_install_anyway_actually_installs(self, isolated_db, monkeypatch):
        captured = {}

        def fake_stream(name, python_executable=None, project_root=None):
            captured["name"] = name
            yield {"line": "Successfully installed omnivoice"}
            yield {"done": True, "ok": True, "returncode": 0}
        monkeypatch.setattr(diagnostics, "stream_dependency_install", fake_stream)
        at = self._run(_monkeypatch=monkeypatch)
        at.button(key="install_model_btn_OmniVoice").click().run(timeout=30)
        at.button(key="install_model_btn_OmniVoice__proceed").click().run(timeout=30)
        assert not at.exception
        assert captured["name"] == "omnivoice"

    def test_cancel_clears_the_confirmation_without_installing(self, isolated_db, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(diagnostics, "stream_dependency_install",
                            lambda *a, **k: called.__setitem__("n", called["n"] + 1) or iter(
                                [{"done": True, "ok": True, "returncode": 0}]))
        at = self._run(_monkeypatch=monkeypatch)
        at.button(key="install_model_btn_OmniVoice").click().run(timeout=30)
        at.button(key="install_model_btn_OmniVoice__cancel").click().run(timeout=30)
        assert not at.exception
        assert called["n"] == 0
        assert not [b for b in at.button if b.label == "Install anyway"]
        # back to a plain, unconfirmed Install button
        assert [b for b in at.button if b.label == "Install"]

    def test_no_confirmation_needed_when_nothing_redundant_is_installed(self, isolated_db, monkeypatch):
        from streamlit.testing.v1 import AppTest
        captured = {}

        def fake_stream(name, python_executable=None, project_root=None):
            captured["name"] = name
            yield {"done": True, "ok": True, "returncode": 0}
        monkeypatch.setattr(diagnostics, "stream_dependency_install", fake_stream)
        monkeypatch.setattr(diagnostics, "get_model_engine_versions", lambda ollama_model=None: [
            {"name": "OmniVoice", "version": "not installed", "url": "https://example.com/omnivoice",
             "installed": False, "package": "omnivoice", "help": "A local voice-cloning engine."},
        ])

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        at.session_state["diagnostics_results"] = _FAKE_RESULTS
        at.run(timeout=30)
        at.button(key="install_model_btn_OmniVoice").click().run(timeout=30)
        assert not at.exception
        assert captured.get("name") == "omnivoice"


class TestDependenciesUpgradeExplainsWhenBlocked:
    """Step 47 item 5: an Upgrade action that can't reach the real latest
    release for a known reason explains why instead of offering a
    doomed-to-fail upgrade."""

    def _run(self, version_results, monkeypatch, python_version=(3, 14, 0, "final", 0)):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(diagnostics.sys, "version_info", python_version)

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        fake_results = dict(_FAKE_RESULTS)
        fake_results["dependencies"] = dict(_FAKE_RESULTS["dependencies"])
        fake_results["dependencies"]["audio-separator"] = {
            "installed": True, "powers": "background-music removal", "tier": "feature"}
        at.session_state["diagnostics_results"] = fake_results
        at.session_state["dependency_version_results"] = version_results
        at.run(timeout=30)
        return at

    def test_known_python_314_limitation_shows_a_reason_not_a_button(self, isolated_db, monkeypatch):
        at = self._run({"audio-separator": {
            "installed_version": "0.2.0", "latest_version": "0.3.0", "outdated": True}}, monkeypatch)
        assert not at.exception
        upgrade_buttons = [b for b in at.button if b.label == "Upgrade"
                          and b.key == "upgrade_dep_btn_audio-separator"]
        assert not upgrade_buttons
        captions = " ".join(c.value for c in at.caption)
        assert "audio-separator" in captions
        assert "3.14" in captions

    def test_upgrade_button_appears_normally_on_a_different_python_version(self, isolated_db, monkeypatch):
        at = self._run({"audio-separator": {
            "installed_version": "0.2.0", "latest_version": "0.3.0", "outdated": True}}, monkeypatch,
            python_version=(3, 11, 0, "final", 0))
        assert not at.exception
        assert [b for b in at.button if b.key == "upgrade_dep_btn_audio-separator"]


class TestNotInstalledExplainsAKnownLimitationUpFront:
    """Step 61: a package known not to install at all on this Python
    version says so right in its "not installed" row, before the user
    ever clicks Install and hits a raw pip/Cython traceback -- and still
    offers the button, since the known case might not apply (a different
    machine, or upstream having since fixed it)."""

    def _run(self, monkeypatch, python_version=(3, 14, 0, "final", 0)):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(diagnostics.sys, "version_info", python_version)

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        fake_results = dict(_FAKE_RESULTS)
        fake_results["dependencies"] = dict(_FAKE_RESULTS["dependencies"])
        fake_results["dependencies"]["audio-separator"] = {
            "installed": False, "powers": "background-music removal", "tier": "feature"}
        at.session_state["diagnostics_results"] = fake_results
        at.run(timeout=30)
        return at

    def test_known_python_314_limitation_shown_in_the_caption(self, isolated_db, monkeypatch):
        at = self._run(monkeypatch)
        captions = " ".join(c.value for c in at.caption)
        assert "audio-separator" in captions and "3.14" in captions and "diffq-fixed" in captions

    def test_install_button_still_offered_despite_the_known_limitation(self, isolated_db, monkeypatch):
        at = self._run(monkeypatch)
        assert [b for b in at.button if b.key == "install_dep_btn_audio-separator"]

    def test_no_known_limitation_mentioned_on_a_different_python_version(self, isolated_db, monkeypatch):
        at = self._run(monkeypatch, python_version=(3, 11, 0, "final", 0))
        # Scoped to the audio-separator row's own caption specifically --
        # Step 62's "Bulk install a whole tier" section separately mentions
        # "diffq-fixed" page-wide as an illustrative example regardless of
        # Python version, which isn't what this test is checking for.
        row_captions = [c.value for c in at.caption if "audio-separator" in c.value]
        assert row_captions
        assert "diffq-fixed" not in row_captions[0]

    def test_a_failed_install_adds_the_known_reason_after_the_real_traceback(
            self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics.sys, "version_info", (3, 14, 0, "final", 0))

        def fake_stream(name, project_root=None):
            yield {"line": "ERROR: Failed building wheel for diffq-fixed"}
            yield {"done": True, "ok": False, "returncode": 1}
        monkeypatch.setattr(diagnostics, "stream_dependency_install", fake_stream)

        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        fake_results = dict(_FAKE_RESULTS)
        fake_results["dependencies"] = dict(_FAKE_RESULTS["dependencies"])
        fake_results["dependencies"]["audio-separator"] = {
            "installed": False, "powers": "background-music removal", "tier": "feature"}
        at.session_state["diagnostics_results"] = fake_results
        at.run(timeout=30)
        [b for b in at.button if b.key == "install_dep_btn_audio-separator"][0].click()
        at.run(timeout=30)

        assert not at.exception
        infos = " ".join(i.value for i in at.info)
        assert "known issue" in infos and "diffq-fixed" in infos
        # The real pip/Cython output is never hidden, only supplemented --
        # _run_pip_stream writes every line into the status box via
        # st.write(), which AppTest surfaces as markdown.
        written = " ".join(m.value for m in at.markdown)
        assert "Failed building wheel for diffq-fixed" in written


class TestBulkTierInstallUI:
    """Step 62 item 1: real "install everything in this tier" buttons,
    reusing stream_bulk_install so one bad package never aborts the rest
    (Step 61's own audio-separator/diffq-fixed case is exactly why)."""

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

    def test_all_three_tier_buttons_exist(self, isolated_db):
        at = self._run()
        labels = {b.label for b in at.button}
        assert "Install everything in requirements-core.txt" in labels
        assert "Install everything in requirements-media.txt" in labels
        assert "Install everything in requirements-optional.txt" in labels

    def test_clicking_shows_a_per_package_result_summary(self, isolated_db, monkeypatch):
        def fake_stream(path):
            yield {"package": "streamlit>=1.49", "start": True}
            yield {"package": "streamlit>=1.49", "done": True, "ok": True}
            yield {"package": "pandas>=2.0", "start": True}
            yield {"package": "pandas>=2.0", "done": True, "ok": False}
            yield {"bulk_done": True, "results": {"streamlit>=1.49": True, "pandas>=2.0": False}}
        monkeypatch.setattr(diagnostics, "stream_bulk_install", fake_stream)

        at = self._run()
        [b for b in at.button
         if b.label == "Install everything in requirements-core.txt"][0].click().run(timeout=30)

        assert not at.exception
        assert [s for s in at.success if "streamlit>=1.49" in s.value]
        assert [e for e in at.error if "pandas>=2.0" in e.value]

    def test_a_later_rerun_keeps_showing_the_last_results(self, isolated_db, monkeypatch):
        # The status box's own streamed log only exists during the click's
        # own run; the summary below it is what has to survive afterward.
        monkeypatch.setattr(diagnostics, "stream_bulk_install", lambda path: iter(
            [{"package": "foo>=1", "start": True}, {"package": "foo>=1", "done": True, "ok": True},
             {"bulk_done": True, "results": {"foo>=1": True}}]))
        at = self._run()
        [b for b in at.button
         if b.label == "Install everything in requirements-media.txt"][0].click().run(timeout=30)
        at.run(timeout=30)
        assert [s for s in at.success if "foo>=1" in s.value]


class TestDenoInstallUI:
    """Step 62 item 2: a real Deno install action next to the existing
    "no JS runtime found" warning, instead of only a text link."""

    def _run(self, found=False, **state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        fake_results = dict(_FAKE_RESULTS)
        fake_results["js_runtime"] = {"found": found, "name": "deno" if found else None,
                                      "path": "/usr/bin/deno" if found else None}
        at.session_state["diagnostics_results"] = fake_results
        for k, v in state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        return at

    def test_install_button_appears_only_when_no_runtime_found(self, isolated_db):
        assert [b for b in self._run(found=False).button if b.key == "install_deno_btn"]
        assert not [b for b in self._run(found=True).button if b.key == "install_deno_btn"]

    def test_success_and_already_on_path_offers_no_restart_notice(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "stream_deno_install", lambda: iter(
            [{"line": "downloading..."},
             {"done": True, "ok": True, "on_path": True, "needs_restart": False}]))
        at = self._run(found=False)
        at.button(key="install_deno_btn").click().run(timeout=30)
        assert not at.exception
        assert not [i for i in at.info if "restart" in i.value]

    def test_success_but_not_on_path_shows_a_restart_notice(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "stream_deno_install", lambda: iter(
            [{"line": "downloading..."},
             {"done": True, "ok": True, "on_path": False, "needs_restart": True}]))
        at = self._run(found=False)
        at.button(key="install_deno_btn").click().run(timeout=30)
        assert not at.exception
        assert [i for i in at.info if "restart" in i.value.lower()]

    def test_failed_install_shows_the_failure_not_a_restart_notice(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "stream_deno_install", lambda: iter(
            [{"line": "curl: command not found"},
             {"done": True, "ok": False, "on_path": False, "needs_restart": False}]))
        at = self._run(found=False)
        at.button(key="install_deno_btn").click().run(timeout=30)
        assert not at.exception
        assert not [i for i in at.info if "restart" in i.value.lower()]


class TestUpgradeCheckUI:
    """Step 66: a "Test first" check next to Upgrade, and a row that's never
    been checked for this exact version reads "untested", not safe."""

    OUTDATED = {"fixture_feature_installed": {
        "installed_version": "1.0.0", "latest_version": "2.0.0", "outdated": True}}

    def _run(self, **state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        at.session_state["diagnostics_results"] = _FAKE_RESULTS
        at.session_state["dependency_version_results"] = self.OUTDATED
        for k, v in state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        return at

    def _captions(self, at):
        return " ".join(c.value for c in at.caption)

    def test_outdated_row_reads_untested_and_offers_the_check(self, isolated_db):
        at = self._run()
        assert "upgrade untested" in self._captions(at)
        assert [b for b in at.button if b.key == "test_upgrade_btn_fixture_feature_installed"]

    def test_a_broken_result_is_shown_with_the_failing_tests(self, isolated_db, monkeypatch):
        calls = {}

        def fake_check(name, version=None, project_root=None):
            calls["args"] = (name, version)
            yield {"line": "FAILED tests/test_x.py::test_y - boom"}
            yield {"done": True, "ok": False, "verdict": "broken", "version": "2.0.0",
                   "reason": "1 test(s) that pass on the current version fail against "
                             "fixture_feature_installed 2.0.0",
                   "new_failures": ["tests/test_x.py::test_y"], "preexisting_failures": []}
        monkeypatch.setattr(diagnostics, "check_upgrade_candidate", fake_check)
        at = self._run()
        at.button(key="test_upgrade_btn_fixture_feature_installed").click().run(timeout=30)

        assert not at.exception
        assert calls["args"] == ("fixture_feature_installed", "2.0.0")
        assert [e for e in at.error if "fail against fixture_feature_installed 2.0.0" in e.value]
        assert "breaks this app's tests" in self._captions(at)
        assert any("tests/test_x.py::test_y" in c.value for c in at.code)

    def test_a_safe_result_is_shown_as_such(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_upgrade_candidate", lambda *a, **k: iter([
            {"done": True, "ok": True, "verdict": "safe", "version": "2.0.0",
             "reason": "every test passed", "new_failures": [], "preexisting_failures": []}]))
        at = self._run()
        at.button(key="test_upgrade_btn_fixture_feature_installed").click().run(timeout=30)
        assert [s for s in at.success if "every test passed" in s.value]
        assert "no new test failures" in self._captions(at)

    def test_an_incomplete_check_is_neither_safe_nor_broken(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_upgrade_candidate", lambda *a, **k: iter([
            {"done": True, "ok": False, "verdict": "incomplete", "version": "2.0.0",
             "reason": "not enough disk space for the throwaway environment",
             "new_failures": [], "preexisting_failures": []}]))
        at = self._run()
        at.button(key="test_upgrade_btn_fixture_feature_installed").click().run(timeout=30)
        assert [w for w in at.warning if "disk space" in w.value]
        assert "didn't complete" in self._captions(at)

    def test_a_result_for_an_older_target_version_still_reads_untested(self, isolated_db):
        at = self._run(upgrade_check_results={"fixture_feature_installed": {
            "verdict": "safe", "target": "1.5.0", "reason": "old"}})
        assert "upgrade untested" in self._captions(at)
        assert not [s for s in at.success if "old" in s.value]

    def test_a_conflict_result_is_a_warning_with_pips_own_report(self, isolated_db, monkeypatch):
        line = ("transformers 5.17.0 requires huggingface-hub<2.0,>=1.5.0, but you have "
                "huggingface-hub 2.0.0 which is incompatible.")
        monkeypatch.setattr(diagnostics, "check_upgrade_candidate", lambda *a, **k: iter([
            {"done": True, "ok": False, "verdict": "conflict", "version": "2.0.0",
             "reason": "every test passed, but pip reports 1 installed package(s) that declare "
                       "they don't support it", "new_failures": [], "preexisting_failures": [],
             "conflicts": [line]}]))
        at = self._run()
        at.button(key="test_upgrade_btn_fixture_feature_installed").click().run(timeout=30)
        assert not at.exception
        assert [w for w in at.warning if "don't support it" in w.value]
        assert not [s for s in at.success if "Upgrade test" in s.value]
        assert "conflicts with installed packages" in self._captions(at)
        assert any(line in c.value for c in at.code)

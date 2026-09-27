"""
tests/test_diagnostics_regrouping.py -- Step 18 (Diagnostics narrowing):
every section becomes individually collapsible, the three routine-health
sections open by default and everything else collapsed, the "Check my
setup" button/results live directly under their own header, the Model &
engine versions panel reads as a real table with a visible install
signal, and the log view gets a keyword filter.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diagnostics


def _run(**session_state):
    from streamlit.testing.v1 import AppTest

    def _render():
        import tabs.diagnostics_tab as dt
        dt.render_diagnostics_tab()

    at = AppTest.from_function(_render)
    for k, v in session_state.items():
        at.session_state[k] = v
    at.run(timeout=30)
    return at


class TestSectionsAreAllCollapsible:
    _ROUTINE_LABELS = {"🩺 Check my setup", "🏃 Running jobs", "🧩 Model & engine versions"}
    _COLLAPSED_LABELS = {
        "💾 Downloaded model cache", "🔒 pyannote gated model access", "🤖 App Assistant",
        "📋 Copy diagnostics for support", "📜 Log", "🎯 Accuracy benchmark", "☠️ Danger zone",
    }

    def test_every_top_level_section_is_an_expander(self, isolated_db):
        at = _run()
        labels = {e.label for e in at.expander}
        assert self._ROUTINE_LABELS <= labels
        assert self._COLLAPSED_LABELS <= labels

    def test_routine_sections_open_by_default(self, isolated_db):
        at = _run()
        for e in at.expander:
            if e.label in self._ROUTINE_LABELS:
                assert e.proto.expanded is True, e.label

    def test_everything_else_collapsed_by_default(self, isolated_db):
        at = _run()
        for e in at.expander:
            if e.label in self._COLLAPSED_LABELS:
                assert e.proto.expanded is False, e.label

    def test_danger_zone_reset_button_not_expanded_by_default(self, isolated_db):
        """The specific exit condition: a routine visit never shows the
        danger-zone reset control without an extra (in fact two) clicks."""
        at = _run()
        danger = next(e for e in at.expander if e.label == "☠️ Danger zone")
        assert danger.proto.expanded is False
        inner = next(e for e in danger.expander if e.label == "Reset the entire library")
        assert inner.proto.expanded is False


class TestCheckMySetupWiredDirectlyUnderItsHeader:
    def test_run_diagnostics_button_lives_inside_check_my_setup(self, isolated_db):
        at = _run()
        setup = next(e for e in at.expander if e.label == "🩺 Check my setup")
        assert [b for b in setup.button if b.label == "🔍 Run diagnostics"]

    def test_results_render_inside_the_same_expander_once_run(self, isolated_db):
        at = _run()
        setup = next(e for e in at.expander if e.label == "🩺 Check my setup")
        [run_btn] = [b for b in setup.button if b.label == "🔍 Run diagnostics"]
        run_btn.click().run(timeout=30)
        setup = next(e for e in at.expander if e.label == "🩺 Check my setup")
        assert any(sh.value == "Core requirements" for sh in setup.subheader)


class TestModelEngineVersionsTable:
    def test_table_shows_a_clickable_name_link_and_an_install_icon(self, isolated_db):
        at = _run()
        panel = next(e for e in at.expander if e.label == "🧩 Model & engine versions")
        table_markdown = "\n".join(m.value for m in panel.markdown)
        assert "[**Whisper (faster-whisper)**](https://github.com/SYSTRAN/faster-whisper)" in table_markdown
        # the raw URL is never spelled out as separate visible text
        assert "https://github.com/SYSTRAN/faster-whisper)" in table_markdown
        assert table_markdown.count("https://github.com/SYSTRAN/faster-whisper") == 1
        assert "✅" in table_markdown or "❌" in table_markdown

    def test_not_installed_row_shows_the_muted_icon_not_installed_text(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "get_model_engine_versions", lambda ollama_model=None: [
            {"name": "Fixture Engine", "version": "not installed",
             "url": "https://example.com/fixture", "installed": False},
        ])
        at = _run()
        panel = next(e for e in at.expander if e.label == "🧩 Model & engine versions")
        table_markdown = "\n".join(m.value for m in panel.markdown)
        assert "❌" in table_markdown
        assert "*not installed*" in table_markdown


class TestModelEngineVersionsInstallAndHelp:
    """Step 47: a per-row Install action for a "not installed" model/engine
    row, reusing Step 18c's own stream_dependency_install machinery (keyed
    directly off the registry's own "package" pip name, not a join against
    OPTIONAL_DEPENDENCIES -- see get_model_engine_versions()'s own
    docstring for why that join wouldn't reliably match), plus a "?"
    popover per row explaining what it does and which feature uses it."""

    def test_install_button_appears_for_a_not_installed_package_row(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "get_model_engine_versions", lambda ollama_model=None: [
            {"name": "Fixture Engine", "version": "not installed",
             "url": "https://example.com/fixture", "installed": False,
             "package": "fixture-engine", "help": "A fixture engine, for testing."},
        ])
        at = _run()
        panel = next(e for e in at.expander if e.label == "🧩 Model & engine versions")
        install_buttons = [b for b in panel.button if b.label == "⬇️ Install"]
        assert len(install_buttons) == 1
        assert install_buttons[0].key == "install_model_btn_Fixture Engine"

    def test_no_install_button_for_a_repo_or_service_row(self, isolated_db):
        # pyannote diarization model is a "repo" and GPT-SoVITS a "service"
        # -- neither has a real "not installed" state (both always report
        # installed=True), so neither should ever grow an Install button.
        at = _run()
        panel = next(e for e in at.expander if e.label == "🧩 Model & engine versions")
        install_keys = {b.key for b in panel.button if b.label == "⬇️ Install"}
        assert "install_model_btn_pyannote diarization model" not in install_keys
        assert "install_model_btn_GPT-SoVITS" not in install_keys

    def test_no_install_button_when_package_name_is_missing(self, isolated_db, monkeypatch):
        # A row shaped like an older/mocked fixture with no "package" key
        # at all must not crash, and must not show a button with nothing
        # to install.
        monkeypatch.setattr(diagnostics, "get_model_engine_versions", lambda ollama_model=None: [
            {"name": "Fixture Engine", "version": "not installed",
             "url": "https://example.com/fixture", "installed": False},
        ])
        at = _run()
        assert not at.exception
        panel = next(e for e in at.expander if e.label == "🧩 Model & engine versions")
        assert not [b for b in panel.button if b.label == "⬇️ Install"]

    def test_clicking_install_calls_stream_dependency_install_with_the_registry_package_name(
            self, isolated_db, monkeypatch):
        captured = {}

        def fake_stream_dependency_install(name, python_executable=None, project_root=None):
            captured["name"] = name
            yield {"line": "Successfully installed fixture-engine"}
            yield {"done": True, "ok": True, "returncode": 0}
        monkeypatch.setattr(diagnostics, "stream_dependency_install", fake_stream_dependency_install)
        monkeypatch.setattr(diagnostics, "get_model_engine_versions", lambda ollama_model=None: [
            {"name": "Fixture Engine", "version": "not installed",
             "url": "https://example.com/fixture", "installed": False,
             "package": "fixture-engine", "help": "A fixture engine, for testing."},
        ])
        at = _run()
        at.button(key="install_model_btn_Fixture Engine").click().run(timeout=30)
        assert not at.exception
        assert captured["name"] == "fixture-engine"

    def test_help_popover_present_with_distinct_plain_english_text_per_row(self, isolated_db):
        # Exit criterion: the "?" on at least three different rows shows a
        # distinct, accurate description, not a generic placeholder.
        # AppTest has no typed accessor for st.popover (same gap noted in
        # test_workspace_tab.py's own popover tests), so the popover call
        # itself is checked statically and its caption CONTENT dynamically.
        src = open("tabs/diagnostics_tab.py", encoding="utf-8").read()
        assert 'row_c1.popover("❓")' in src

        at = _run()
        panel = next(e for e in at.expander if e.label == "🧩 Model & engine versions")
        captions = {c.value for c in panel.caption}
        expect_snippets = [
            "transcribe dialogue",         # Whisper (faster-whisper)
            "alternative speech-to-text",  # Qwen3-ASR
            "clone a character's voice",   # F5-TTS
        ]
        for snippet in expect_snippets:
            assert any(snippet in c for c in captions), snippet
        # each snippet lands in its own distinct caption, not one shared placeholder
        assert len({c for c in captions if any(s in c for s in expect_snippets)}) == len(expect_snippets)


class TestGpuStatusDisplay:
    def test_shows_a_clean_message_with_no_exception_when_gpu_unavailable(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "get_gpu_status", lambda: {
            "available": False, "message": "GPU info unavailable -- no CUDA-capable GPU detected."})
        at = _run()
        assert not at.exception
        panel = next(e for e in at.expander if e.label == "🧩 Model & engine versions")
        assert any("GPU info unavailable" in c.value for c in panel.caption)

    def test_shows_real_name_and_vram_when_available(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "get_gpu_status", lambda: {
            "available": True, "name": "NVIDIA GeForce RTX 3080 Ti",
            "vram_used_gb": 2.0, "vram_total_gb": 12.0, "torch_cuda_version": "12.8"})
        at = _run()
        assert not at.exception
        panel = next(e for e in at.expander if e.label == "🧩 Model & engine versions")
        joined = "\n".join(c.value for c in panel.caption)
        assert "NVIDIA GeForce RTX 3080 Ti" in joined
        assert "2.0 GB / 12.0 GB" in joined


class TestLogKeywordFilter:
    def test_filter_narrows_the_shown_log_lines(self, isolated_db, monkeypatch):
        import applog
        monkeypatch.setattr(applog, "tail", lambda n=50: [
            "INFO starting job", "ERROR job failed", "INFO job done"])
        at = _run(diagnostics_log_filter="ERROR")
        panel = next(e for e in at.expander if e.label == "📜 Log")
        codes = [c.value for c in panel.code]
        assert codes == ["ERROR job failed"]

    def test_blank_filter_shows_every_line(self, isolated_db, monkeypatch):
        import applog
        monkeypatch.setattr(applog, "tail", lambda n=50: ["a", "b", "c"])
        at = _run()
        panel = next(e for e in at.expander if e.label == "📜 Log")
        codes = [c.value for c in panel.code]
        assert codes == ["a\nb\nc"]

    def test_no_match_says_so_rather_than_showing_nothing(self, isolated_db, monkeypatch):
        import applog
        monkeypatch.setattr(applog, "tail", lambda n=50: ["a", "b"])
        at = _run(diagnostics_log_filter="zzz-no-match")
        panel = next(e for e in at.expander if e.label == "📜 Log")
        assert not panel.code
        assert any("No log lines match" in c.value for c in panel.caption)


class TestTypeScaleScope:
    def test_diagnostics_tab_opts_into_the_type_scale(self):
        import ast
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tabs", "diagnostics_tab.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        [func] = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "render_diagnostics_tab"]
        assert ast.unparse(func.body[0]) == "ui_theme.type_scale_scope()"

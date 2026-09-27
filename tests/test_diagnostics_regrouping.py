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

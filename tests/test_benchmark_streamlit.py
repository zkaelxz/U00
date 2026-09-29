"""Streamlit widget, AppTest and tab-source tests split out of tests/test_benchmark.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_benchmark.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCompareEnginesInDiagnostics:
    def _run(self, **session_state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        for k, v in session_state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        return at

    def test_shows_both_engines_outputs_and_leaves_history_alone(self, isolated_db, monkeypatch):
        import translate_engines

        class Fake:
            def __init__(self, name):
                self.name = name

            def translate_batch(self, texts, context):
                return [{"claude": "Hello world", "deepseek": "Hi planet"}[self.name]]

        monkeypatch.setattr(translate_engines, "get_engine", lambda name, key, *a, **k: Fake(name))
        cid = isolated_db.create_benchmark_case("Excerpt", "translation", "novel",
                                                source_text="你好世界", reference_text="Hello world")
        isolated_db.save_benchmark_run(cid, {"output_text": "prior", "score": 0.9, "duration_seconds": 1.0})
        at = self._run(settings_claude="k1", settings_deepseek="k2")
        at.selectbox(key="bm_cmp_a_translation").select("claude")
        at.selectbox(key="bm_cmp_b_translation").select("deepseek")
        at.button(key="bm_cmp_run").click().run(timeout=120)

        codes = [c.value for c in at.code]
        assert "Hello world" in codes and "Hi planet" in codes
        assert "100%" in [m.value for m in at.metric]
        assert [r["output_text"] for r in isolated_db.list_benchmark_runs(cid)] == ["prior"]

    def test_missing_api_key_warns_instead_of_running(self, isolated_db):
        isolated_db.create_benchmark_case("Excerpt", "translation", "novel", source_text="你好")
        at = self._run()
        at.selectbox(key="bm_cmp_a_translation").select("claude")
        at.selectbox(key="bm_cmp_b_translation").select("test_offline")
        at.button(key="bm_cmp_run").click().run(timeout=120)
        assert any("no API key set for 'claude'" in w.value for w in at.warning)
        assert "bm_cmp_results" not in at.session_state

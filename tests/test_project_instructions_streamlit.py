"""Streamlit widget, AppTest and tab-source tests split out of tests/test_project_instructions.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_project_instructions.py."""

import pytest


class TestWorkflowTiers:

    EXPECTED = {
        "draft": ("deepseek", None, False, False),
        "standard": ("claude", "claude-sonnet-5", False, False),
        "release": ("claude", "claude-opus-4-8", True, True),
    }

    @pytest.mark.parametrize("tier", ["draft", "standard", "release"])
    def test_applying_a_tier_in_workspace_sets_engine_reflect_and_auto_qc(self, isolated_db, tier):
        pytest.importorskip("streamlit")
        from streamlit.testing.v1 import AppTest

        did = isolated_db.create_drama(title_en="Tiers", status="aligned",
                                       translation_engine="gemini")

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        assert not at.exception
        at.selectbox(key=f"workflow_tier_choice_{did}").set_value(tier).run(timeout=30)
        at.button(key=f"apply_workflow_tier_btn_{did}").click().run(timeout=30)
        assert not at.exception

        engine, model, reflect, auto_qc = self.EXPECTED[tier]
        assert isolated_db.get_drama(did)["translation_engine"] == engine
        engine_box = next(s for s in at.selectbox if s.label == "Translation engine")
        assert engine_box.value == engine
        if model:
            assert at.session_state["settings_claude_model"] == model
        assert at.checkbox(key=f"auto_qc_{did}").value is auto_qc
        reflect_boxes = [c for c in at.checkbox if c.key == f"reflect_mode_{did}"]
        assert reflect_boxes and reflect_boxes[0].value is reflect


class TestInstructionsUi:
    def test_typing_instructions_in_workspace_saves_them(self, isolated_db):
        pytest.importorskip("streamlit")
        from streamlit.testing.v1 import AppTest

        did = isolated_db.create_drama(title_en="UI", status="aligned")

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.text_area(key=f"project_instructions_{did}").input("Keep it formal.\nNo slang.").run(timeout=30)
        assert not at.exception
        assert isolated_db.get_drama(did)["project_instructions"] == "Keep it formal.\nNo slang."
        # Reloading shows the saved text rather than an empty box.
        at2 = AppTest.from_function(_render)
        at2.session_state["active_drama_id"] = did
        at2.session_state["lines"] = None
        at2.run(timeout=30)
        assert at2.text_area(key=f"project_instructions_{did}").value == "Keep it formal.\nNo slang."

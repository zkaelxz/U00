"""
tests/test_ui_components.py -- Step 13: the new ui/ component layer
(project_header, workflow, status) and the unified project-state model.

These components aren't wired into any tab yet (that's Steps 14-19); this
just checks they render correctly in isolation against a fake
project-state fixture, per the step's own exit conditions.
"""
import os
import sys

import pytest
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest

from ui import project_state, workflow


@pytest.fixture(autouse=True)
def clean_session_state():
    st.session_state.clear()
    yield
    st.session_state.clear()


# --------------------------------------------------------------------- #
# ui/project_state.py -- the unified st.session_state.project model
# --------------------------------------------------------------------- #

class TestProjectState:
    def test_get_project_creates_defaults(self):
        p = project_state.get_project()
        assert p == {
            "drama_id": None, "current_stage": None, "stage_status": {},
            "selected_line_id": None, "active_job_id": None,
            "recent_errors": [], "recent_warnings": [],
        }

    def test_get_project_returns_the_same_dict_object(self):
        p1 = project_state.get_project()
        p1["drama_id"] = 5
        p2 = project_state.get_project()
        assert p2 is p1
        assert p2["drama_id"] == 5

    def test_round_trips_drama_id_and_current_stage(self):
        project_state.set_drama(42)
        project_state.set_stage(2)
        p = project_state.get_project()
        assert p["drama_id"] == 42
        assert p["current_stage"] == 2

    def test_stage_status_selection_and_active_job_round_trip(self):
        project_state.set_drama(1)
        project_state.set_stage_status("translate", "done")
        project_state.set_stage_status("dub", "current")
        project_state.set_selected_line(17)
        project_state.set_active_job("translate_1")
        p = project_state.get_project()
        assert p["stage_status"] == {"translate": "done", "dub": "current"}
        assert p["selected_line_id"] == 17
        assert p["active_job_id"] == "translate_1"

    def test_errors_and_warnings_accumulate_and_clear(self):
        project_state.add_error("translation failed")
        project_state.add_warning("glossary term missing")
        p = project_state.get_project()
        assert p["recent_errors"] == ["translation failed"]
        assert p["recent_warnings"] == ["glossary term missing"]
        project_state.clear_errors_and_warnings()
        assert project_state.get_project()["recent_errors"] == []
        assert project_state.get_project()["recent_warnings"] == []

    def test_switching_drama_clears_stage_and_selection_but_not_drama_id(self):
        project_state.set_drama(1)
        project_state.set_stage(3)
        project_state.set_selected_line(99)
        project_state.set_stage_status("translate", "done")
        project_state.set_drama(2)
        p = project_state.get_project()
        assert p["drama_id"] == 2
        assert p["current_stage"] is None
        assert p["selected_line_id"] is None
        assert p["stage_status"] == {}

    def test_resetting_the_same_drama_id_keeps_its_state(self):
        project_state.set_drama(1)
        project_state.set_stage(3)
        project_state.set_drama(1)
        assert project_state.get_project()["current_stage"] == 3

    def test_reset_project_gives_back_fresh_defaults(self):
        project_state.set_drama(1)
        project_state.set_stage(3)
        project_state.add_error("boom")
        project_state.reset_project()
        assert project_state.get_project() == project_state._new_state()

    def test_new_state_dicts_are_never_shared(self):
        p1 = project_state.get_project()
        p1["recent_errors"].append("boom")
        p1["stage_status"]["x"] = "done"
        st.session_state.clear()
        p2 = project_state.get_project()
        assert p2["recent_errors"] == []
        assert p2["stage_status"] == {}

    def test_does_not_touch_narrowly_scoped_keys(self):
        """The step's own scope: this only consolidates project-identity
        state. Reader display prefs, settings_* keys and widget-local
        keys (like reader_drama_pick) must be left completely alone."""
        st.session_state["active_drama_id"] = 7
        st.session_state["reader_drama_pick"] = "Some Drama"
        st.session_state["reader_resume_pending"] = "Some Drama"
        st.session_state["reader_resume_banner"] = "Some Drama"
        st.session_state["settings_hf_token"] = "hf_abc123"
        st.session_state["app_dark_mode"] = True

        project_state.set_drama(1)
        project_state.set_stage(2)
        project_state.set_stage_status("translate", "done")
        project_state.set_selected_line(5)
        project_state.set_active_job("translate_1")
        project_state.add_error("oops")
        project_state.add_warning("careful")

        assert st.session_state["active_drama_id"] == 7
        assert st.session_state["reader_drama_pick"] == "Some Drama"
        assert st.session_state["reader_resume_pending"] == "Some Drama"
        assert st.session_state["reader_resume_banner"] == "Some Drama"
        assert st.session_state["settings_hf_token"] == "hf_abc123"
        assert st.session_state["app_dark_mode"] is True
        # and every project-identity write landed under the one new key
        assert set(st.session_state.keys()) == {
            "active_drama_id", "reader_drama_pick", "reader_resume_pending",
            "reader_resume_banner", "settings_hf_token", "app_dark_mode",
            "project",
        }


# --------------------------------------------------------------------- #
# ui/workflow.py -- the pipeline-stage stepper
# --------------------------------------------------------------------- #

class TestWorkflowStepper:
    def test_render_stepper_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError):
            workflow.render_stepper(["A", "B"], ["done"])

    def _render_app(self, stages, statuses):
        def _render(stages, statuses):
            from ui.workflow import render_stepper
            render_stepper(stages, statuses)
        at = AppTest.from_function(_render, args=(stages, statuses))
        at.run(timeout=30)
        return at

    def test_render_stepper_shows_a_check_for_done_stages(self):
        at = self._render_app(["Source", "Translate", "Export"],
                              ["done", "current", "not_started"])
        html = at.markdown[0].value
        assert "bh-stage-done" in html
        assert "✓ Source" in html

    def test_render_stepper_shows_a_dot_for_the_current_stage(self):
        at = self._render_app(["Source", "Translate", "Export"],
                              ["done", "current", "not_started"])
        html = at.markdown[0].value
        assert "bh-stage-current" in html
        assert "● Translate" in html

    def test_render_stepper_shows_a_circle_for_not_started_stages(self):
        at = self._render_app(["Source", "Translate", "Export"],
                              ["done", "current", "not_started"])
        html = at.markdown[0].value
        assert "○ Export" in html

    def test_render_stepper_from_index_matches_render_stepper(self):
        def _render():
            from ui.workflow import render_stepper_from_index
            render_stepper_from_index(["Source", "Translate", "Export"], 1)
        at = AppTest.from_function(_render)
        at.run(timeout=30)
        html = at.markdown[0].value
        assert "✓ Source" in html and "● Translate" in html and "○ Export" in html


# --------------------------------------------------------------------- #
# ui/project_header.py -- the always-visible project header
# --------------------------------------------------------------------- #

class TestProjectHeader:
    def test_shows_the_drama_name_and_stepper(self):
        def _render():
            from ui.project_header import render_project_header
            render_project_header({"current_stage": 1}, {"id": 5, "title_en": "Test Drama"},
                                  stages=["Source", "Translate", "Export"])
        at = AppTest.from_function(_render)
        at.run(timeout=30)
        assert any("Test Drama" in m.value for m in at.markdown)
        assert any("bh-stage-current" in m.value for m in at.markdown)

    def test_falls_back_to_title_zh_then_drama_id(self):
        def _render():
            from ui.project_header import render_project_header
            render_project_header({}, {"id": 9, "title_en": "", "title_zh": "你好"})
        at = AppTest.from_function(_render)
        at.run(timeout=30)
        assert any("你好" in m.value for m in at.markdown)

    def test_shows_a_placeholder_when_no_drama_is_open(self):
        def _render():
            from ui.project_header import render_project_header
            render_project_header({}, None)
        at = AppTest.from_function(_render)
        at.run(timeout=30)
        assert any("No project open" in m.value for m in at.markdown)

    def test_omits_the_stepper_when_no_stages_given(self):
        def _render():
            from ui.project_header import render_project_header
            render_project_header({"current_stage": 1}, {"id": 1, "title_en": "D"})
        at = AppTest.from_function(_render)
        at.run(timeout=30)
        assert not any("bh-stage" in m.value for m in at.markdown)

    def test_settings_shortcut_click_is_reported_back_to_the_caller(self):
        def _render():
            import streamlit as st
            from ui.project_header import render_project_header
            clicked = render_project_header({}, {"id": 1, "title_en": "D"})
            st.session_state["clicked_result"] = clicked
        at = AppTest.from_function(_render)
        at.run(timeout=30)
        assert at.session_state["clicked_result"] is False
        at.button[0].click().run(timeout=30)
        assert at.session_state["clicked_result"] is True


# --------------------------------------------------------------------- #
# ui/status.py -- the shared background-job status block
# --------------------------------------------------------------------- #

def _job(status, **extra):
    base = {"status": status, "progress": 0.0, "message": "", "error": None,
            "traceback": None, "started_at": None, "finished_at": None}
    base.update(extra)
    return base


class TestStatusBlock:
    def test_no_job_renders_nothing(self):
        def _render():
            from ui.status import render_job_status
            render_job_status(None)
        at = AppTest.from_function(_render)
        at.run(timeout=30)
        assert not at.get("progress") and not at.error and not at.get("success") \
            and not at.get("info")

    def test_queued_job_shows_an_info_message(self):
        def _render(job):
            from ui.status import render_job_status
            render_job_status(job)
        at = AppTest.from_function(
            _render, args=(_job("queued", message="Waiting -- GPU busy with dub"),))
        at.run(timeout=30)
        assert any("Waiting -- GPU busy with dub" in i.value for i in at.get("info"))

    def test_running_job_shows_a_progress_bar_with_its_message(self):
        def _render(job):
            from ui.status import render_job_status
            render_job_status(job)
        at = AppTest.from_function(
            _render, args=(_job("running", progress=0.4, message="Translating line 12/30"),))
        at.run(timeout=30)
        [bar] = at.get("progress")
        assert bar.value == 40
        assert "Translating line 12/30" in bar.proto.text

    def test_running_job_falls_back_to_the_running_label(self):
        def _render(job):
            from ui.status import render_job_status
            render_job_status(job, running_label="Working on it...")
        at = AppTest.from_function(_render, args=(_job("running", progress=0.1),))
        at.run(timeout=30)
        [bar] = at.get("progress")
        assert "Working on it..." in bar.proto.text

    def test_done_job_shows_success_only_when_a_message_is_given(self):
        def _render(job):
            from ui.status import render_job_status
            render_job_status(job, done_message="Translation complete.")
        at = AppTest.from_function(_render, args=(_job("done"),))
        at.run(timeout=30)
        assert any("Translation complete." in s.value for s in at.get("success"))

    def test_done_job_with_no_message_stays_silent(self):
        def _render(job):
            from ui.status import render_job_status
            render_job_status(job)
        at = AppTest.from_function(_render, args=(_job("done"),))
        at.run(timeout=30)
        assert not at.get("success")

    def test_error_job_shows_a_plain_reason_and_hides_the_traceback_by_default(self):
        def _render(job):
            from ui.status import render_job_status
            render_job_status(job)
        at = AppTest.from_function(_render, args=(_job(
            "error",
            error="ConnectionError: could not reach translation API",
            traceback="Traceback (most recent call last):\n  ...\n"
                     "ConnectionError: could not reach translation API",
        ),))
        at.run(timeout=30)
        [err] = at.error
        assert "could not reach translation API" in err.value
        assert "ConnectionError" not in err.value  # de-technicalized headline
        [exp] = at.expander
        assert exp.label == "Advanced details"
        assert exp.proto.expanded is False  # collapsed by default
        [code] = at.code
        assert "ConnectionError" in code.value  # raw detail still reachable

    def test_explicit_reason_overrides_the_default_de_technicalization(self):
        def _render(job):
            from ui.status import render_job_status
            render_job_status(job, reason="The glossary file couldn't be read.")
        at = AppTest.from_function(
            _render, args=(_job("error", error="ValueError: bad glossary term"),))
        at.run(timeout=30)
        assert "The glossary file couldn't be read." in at.error[0].value

    def test_retry_button_shown_and_wired_only_when_on_retry_is_given(self):
        def _render(job):
            import streamlit as st
            from ui.status import render_job_status
            st.session_state.setdefault("retried", False)

            def _retry():
                st.session_state["retried"] = True

            render_job_status(job, on_retry=_retry, retry_label="Try again")
        at = AppTest.from_function(_render, args=(_job("error", error="X: y"),))
        at.run(timeout=30)
        [btn] = at.button
        assert btn.label == "Try again"
        assert at.session_state["retried"] is False
        btn.click().run(timeout=30)
        assert at.session_state["retried"] is True

    def test_no_retry_button_when_retry_is_not_safe(self):
        def _render(job):
            from ui.status import render_job_status
            render_job_status(job)
        at = AppTest.from_function(_render, args=(_job("error", error="X: y"),))
        at.run(timeout=30)
        assert not at.button

    def test_render_failure_card_directly(self):
        def _render(job):
            from ui.status import render_failure_card
            render_failure_card(job, reason="The server took too long to respond.")
        at = AppTest.from_function(
            _render, args=(_job("error", error="TimeoutError: no response"),))
        at.run(timeout=30)
        assert "The server took too long to respond." in at.error[0].value
        assert at.expander[0].proto.expanded is False

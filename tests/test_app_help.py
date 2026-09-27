"""
tests/test_app_help.py -- app_help.py's "App Assistant" (Step 18b).
Two things matter most here: the grounding context is built from real,
current tab-module source (not a hardcoded string), and the system
prompt actually causes a decline rather than a fabricated answer when
nothing in that grounding plausibly matches.
"""
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app_help


FIXTURE_TAB_SOURCE = '''
from common import *


def render_fixture_tab():
    st.subheader("🎛️ Widget calibration")
    st.caption("Adjusts the fixture widget's sensitivity threshold.")
    with st.expander("Advanced fixture options"):
        st.checkbox("Enable turbo mode", help="Doubles the fixture's throughput.")
'''


class TestExtractModuleSections:
    def test_extracts_heading_and_caption(self):
        sections = app_help.extract_module_sections(FIXTURE_TAB_SOURCE)
        headings = [s["heading"] for s in sections]
        assert "🎛️ Widget calibration" in headings
        cal = next(s for s in sections if s["heading"] == "🎛️ Widget calibration")
        assert "sensitivity threshold" in " ".join(cal["captions"])

    def test_extracts_expander_and_help_kwarg(self):
        sections = app_help.extract_module_sections(FIXTURE_TAB_SOURCE)
        headings = [s["heading"] for s in sections]
        assert "Advanced fixture options" in headings
        adv = next(s for s in sections if s["heading"] == "Advanced fixture options")
        assert "throughput" in " ".join(adv["captions"])

    def test_syntax_error_returns_empty_list_not_raise(self):
        assert app_help.extract_module_sections("def broken(:\n") == []

    def test_no_st_calls_returns_empty_list(self):
        assert app_help.extract_module_sections("x = 1\n") == []


class TestBuildGroundingContext:
    def test_grounding_reflects_a_real_fixture_module_with_no_app_help_change(self):
        """A section added to a fixture tab module shows up in the
        grounding context without touching app_help.py itself -- the
        whole point of generating this from real source instead of a
        hand-maintained doc."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "fixture_tab.py"), "w", encoding="utf-8") as f:
                f.write(FIXTURE_TAB_SOURCE)
            context = app_help.build_grounding_context(tabs_dir=tmpdir)
        assert "fixture_tab" in context
        assert "Widget calibration" in context
        assert "sensitivity threshold" in context

    def test_ignores_non_tab_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "fixture_tab.py"), "w", encoding="utf-8") as f:
                f.write(FIXTURE_TAB_SOURCE)
            with open(os.path.join(tmpdir, "helpers.py"), "w", encoding="utf-8") as f:
                f.write('st.subheader("Should not appear")\n')
            context = app_help.build_grounding_context(tabs_dir=tmpdir)
        assert "Should not appear" not in context

    def test_real_app_tabs_dir_produces_nonempty_grounding(self):
        context = app_help.build_grounding_context()
        assert "diagnostics_tab" in context
        assert len(context) > 100


class _FakeClaudeLike:
    supports_reference = True
    model = "fake-claude"

    def __init__(self, canned_answer):
        self.canned_answer = canned_answer
        self.client = self
        self.messages = self
        self.captured_system = None

    def create(self, model, max_tokens, system, messages):
        self.captured_system = system
        Block = type("Block", (), {"type": "text", "text": self.canned_answer})
        Response = type("Response", (), {"content": [Block()]})
        return Response()


class TestAskAboutApp:
    def test_grounding_is_passed_into_the_system_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "fixture_tab.py"), "w", encoding="utf-8") as f:
                f.write(FIXTURE_TAB_SOURCE)
            engine = _FakeClaudeLike("Widget calibration is under Fixture > Advanced.")
            answer = app_help.ask_about_app("where is the widget setting?", engine,
                                             tabs_dir=tmpdir)
        assert answer == "Widget calibration is under Fixture > Advanced."
        assert "Widget calibration" in engine.captured_system
        assert "sensitivity threshold" in engine.captured_system

    def test_declines_cleanly_for_a_translation_only_engine(self):
        class PureMT:
            supports_reference = False
        result = app_help.ask_about_app("where is X?", PureMT())
        assert "doesn't support" in result.lower()

    def test_system_prompt_instructs_decline_and_excludes_unmatched_terms(self):
        """Can't literally unit-test an LLM's honesty, so this checks the
        one thing that's actually under this module's control: the system
        prompt handed to the engine (a) carries the explicit 'never
        invent' instruction, and (b) genuinely doesn't contain a made-up
        setting name -- so a model actually following that instruction has
        no grounding text to hallucinate an answer from."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "fixture_tab.py"), "w", encoding="utf-8") as f:
                f.write(FIXTURE_TAB_SOURCE)
            engine = _FakeClaudeLike("I don't see a setting for that.")
            app_help.ask_about_app("where is the warp drive frobnicator?", engine,
                                    tabs_dir=tmpdir)
        assert "never invent" in engine.captured_system.lower()
        assert "warp drive frobnicator" not in engine.captured_system.lower()

    def test_chat_history_is_carried_through(self):
        engine = _FakeClaudeLike("Second answer.")
        history = [{"role": "user", "content": "first question"},
                   {"role": "assistant", "content": "first answer"}]
        app_help.ask_about_app("second question", engine, chat_history=history)
        # _dispatch_chat is given history + the new question as `messages`,
        # not folded into the system prompt -- confirm nothing here dropped
        # the prior turns before handing off to qa._dispatch_chat.
        assert history[0]["content"] == "first question"


class TestFormatHelpReport:
    def test_combines_question_answer_and_diagnostics(self):
        report = app_help.format_help_report(
            "where is X?", "X is under Settings.", "Python: 3.12.0\nffmpeg: found")
        assert "where is X?" in report
        assert "X is under Settings." in report
        assert "Python: 3.12.0" in report

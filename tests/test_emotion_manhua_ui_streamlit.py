"""Streamlit widget, AppTest and tab-source tests split out of tests/test_emotion_manhua_ui.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_emotion_manhua_ui.py."""

import sys, os




class TestDarkModeCoverage:
    """The dark toggle only covered a subset of what the app actually uses
    -- checkboxes, radios, the toggle itself, sliders, file uploaders, and
    critically the success/info/warning/error alert boxes (used constantly
    throughout the app) had no dark styling at all, leaving them white
    against a dark background."""

    def test_covers_every_widget_type_the_app_actually_uses(self):
        import inspect
        import ui_theme as ui
        src = inspect.getsource(ui.inject_dark_css)
        required = [
            "stAlert", "stCheckbox", "stRadio", "stToggle", "stSlider",
            "stFileUploaderDropzone", "stProgress", "popover",
            "stChatMessage", "stChatInput", "stDataEditor",
        ]
        missing = [r for r in required if r not in src]
        assert missing == [], f"still uncovered: {missing}"

    def test_previously_covered_selectors_still_present(self):
        # Regression guard: expanding the sheet must not have dropped
        # anything that was already working.
        import inspect
        import ui_theme as ui
        src = inspect.getsource(ui.inject_dark_css)
        for required in ("stExpander", "stMetric", "stTextInput", "stButton",
                         "stTabs", "stDataFrame"):
            assert required in src, f"regression: {required} was dropped"

    def test_dark_palette_has_all_expected_keys(self):
        import ui_theme as ui
        for key in ("bg", "surface", "ink", "muted", "border", "accent", "accent_soft"):
            assert key in ui.DARK
            assert ui.DARK[key].startswith("#")

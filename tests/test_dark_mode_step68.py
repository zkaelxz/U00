"""
tests/test_dark_mode_step68.py -- Step 68: the dark-mode gaps that survived
Step 46, found by auditing the real rendered DOM in a headless browser on
Streamlit 1.64 (and 1.58 for the older markup), not by re-reading the CSS.

The root cause behind most of them: Streamlit 1.59 replaced BaseWeb in its
input widgets (selectbox, multiselect, tooltips, popover panels) with
react-aria components that carry no `data-baseweb` attribute at all, so the
existing `[data-baseweb=...]` rules matched nothing on a current install.
The earlier dark-mode tests only asserted that a widget's *name* appeared
somewhere in the stylesheet -- which is exactly how a dead selector passed.
These pin the specific selectors the live audit showed are needed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest

import ui_theme


def _dark_css():
    def _render():
        import ui_theme
        ui_theme.inject_dark_css()
    at = AppTest.from_function(_render)
    at.run(timeout=30)
    [md] = at.markdown
    return md.value


def _rule_for(css, selector):
    """The declaration block of the first rule whose selector list
    contains `selector`."""
    idx = css.index(selector)
    return css[css.index("{", idx): css.index("}", idx)]


@pytest.fixture(scope="module")
def css():
    return _dark_css()


class TestReactAriaWidgetsAreCovered:
    def test_selectbox_and_multiselect_field(self, css):
        for sel in ('[data-testid="stSelectbox"] [role="group"]',
                    '[data-testid="stMultiSelect"] [role="group"]'):
            assert sel in css
            assert "background:" in _rule_for(css, sel)

    def test_the_older_baseweb_select_rule_is_kept_too(self, css):
        # Streamlit <= 1.58 still renders the BaseWeb markup.
        assert '.stSelectbox div[data-baseweb="select"] > div' in css

    def test_open_option_list(self, css):
        for sel in ('[data-testid="stSelectboxVirtualDropdown"]',
                    '[data-testid="stMultiSelectDropdown"]'):
            assert sel in css
            assert "background:" in _rule_for(css, sel)
        assert '[role="listbox"] [role="option"]' in css

    def test_text_number_and_chat_input_surfaces(self, css):
        for sel in ('[data-testid="stTextInputRootElement"]', '[data-testid="stTextAreaRootElement"]',
                    '[data-testid="stNumberInputStepUp"]', '[data-testid="stChatInput"] > div'):
            assert sel in css
            assert "background:" in _rule_for(css, sel)

    def test_number_input_error_state_keeps_its_red_tint(self, css):
        # The container turns red-tinted when the value is out of range; an
        # unconditional dark background would hide that signal.
        assert ('[data-testid="stNumberInputContainer"]:not(:has([aria-invalid="true"]))'
                in css)


class TestPanelsRenderedOutsideTheApp:
    def test_popover_content_panel(self, css):
        # Step 46 only styled the trigger button; the panel stayed white.
        assert '[data-testid="stPopoverBody"]' in css
        assert "background:" in _rule_for(css, '[data-testid="stPopoverBody"]')

    def test_help_and_error_tooltips(self, css):
        # White text (from the blanket `p` rule) on the tooltip's own white
        # panel -- the "Lines per page" out-of-range error was unreadable.
        for sel in ('[data-testid="stTooltipContent"]', '[data-testid="stTooltipErrorContent"]'):
            assert sel in css
            assert "background:" in _rule_for(css, sel)


class TestOtherGapsFromTheAudit:
    def test_st_text_is_readable(self, css):
        # Translate tab's History renders both columns with st.text().
        assert '[data-testid="stText"]' in css
        assert "color:" in _rule_for(css, '[data-testid="stText"]')

    def test_file_uploader_rule_is_not_tag_qualified(self, css):
        # The dropzone is a <section>; a div[...] selector never matched it.
        assert 'div[data-testid="stFileUploaderDropzone"]' not in css
        assert "background:" in _rule_for(css, '[data-testid="stFileUploaderDropzone"]')

    def test_non_st_button_secondary_buttons(self, css):
        for sel in ('[data-testid="stBaseButton-secondary"]', '[data-testid="stBaseLinkButton-secondary"]',
                    '[data-testid="stBaseButton-secondaryFormSubmit"]'):
            assert sel in css
        assert "background:" in _rule_for(css, '[data-testid="stBaseButton-secondary"]')

    def test_top_header_strip(self, css):
        assert "background:" in _rule_for(css, 'header[data-testid="stHeader"]')


class TestReaderFollowsAppDarkMode:
    """The Reader's line table is its own iframe document -- unreachable by
    inject_dark_css() -- and used to read a separate reader_theme defaulting
    to "light", so it stayed light with Dark mode on."""

    @pytest.mark.parametrize("choice, dark, expected", [
        ("match app", True, "dark"), ("match app", False, "light"),
        ("sepia", True, "sepia"), ("light", True, "light"), ("dark", False, "dark"),
        (None, True, "dark"), ("something-stale", False, "light"),
    ])
    def test_resolve_reader_theme(self, choice, dark, expected):
        assert ui_theme.resolve_reader_theme(choice, dark) == expected

    def test_match_app_is_the_default_setting(self, isolated_db):
        def _render():
            from tabs.settings_tab import render_settings_sidebar
            render_settings_sidebar()
        at = AppTest.from_function(_render)
        at.run(timeout=30)
        [theme] = [s for s in at.selectbox if s.label == "Theme"]
        assert theme.value == "match app"
        assert at.session_state["reader_theme"] == "match app"

    def test_reader_table_is_rendered_dark_when_the_app_is(self, isolated_db, monkeypatch):
        from core import Line
        import reader
        import segment
        monkeypatch.setattr(segment, "segment_and_annotate",
                            lambda text, language, chinese_script="simplified": [(text, "")])
        seen = {}
        real = reader.build_reader_html

        def spy(*args, **kwargs):
            seen["theme"] = kwargs.get("theme")
            return real(*args, **kwargs)
        monkeypatch.setattr(reader, "build_reader_html", spy)
        did = isolated_db.create_drama(title_en="Dark Reader Drama", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])

        def _render():
            import tabs.reader_tab as rt
            rt.render_reader_tab()
        at = AppTest.from_function(_render)
        at.session_state["app_dark_mode"] = True
        at.run(timeout=30)
        assert not at.exception
        assert seen.get("theme") == "dark"


class TestPopoverScrollAndHeight:
    """Not colour issues, but found in the same pass: a popover panel is a
    position:fixed element outside the main scroll container, so the wheel
    over it went nowhere ("can't scroll down while Checks is open"); and it
    re-anchored/flipped while an expander inside it animated, which read as
    the page jumping (Reader's "Story tools")."""

    @pytest.mark.parametrize("dark", [False, True])
    def test_wheel_passthrough_is_installed_in_both_themes(self, dark, monkeypatch):
        calls = []
        import streamlit as st
        real_html = st.html
        monkeypatch.setattr(st, "html", lambda body, **kw: (calls.append((body, kw)), real_html(body, **kw))[1])

        def _render(dark):
            import streamlit as st
            import ui_theme
            st.session_state["app_dark_mode"] = dark
            ui_theme.inject_css()
        at = AppTest.from_function(_render, kwargs={"dark": dark})
        at.run(timeout=30)
        assert not at.exception
        scripts = [(b, kw) for b, kw in calls if "stPopoverBody" in b]
        assert len(scripts) == 1
        body, kw = scripts[0]
        assert kw.get("unsafe_allow_javascript") is True
        assert "window.__bhPopoverWheel" in body  # installed once, not per rerun
        assert '"wheel"' in body and "stMain" in body

    def test_older_streamlit_without_the_js_flag_does_not_crash(self, monkeypatch):
        import streamlit as st

        def _old_html(body, **kwargs):
            if kwargs:
                raise TypeError("html() got an unexpected keyword argument 'unsafe_allow_javascript'")
        monkeypatch.setattr(st, "html", _old_html)
        ui_theme.inject_popover_scroll_passthrough()  # must not raise

    def test_popovers_with_sections_get_a_constant_height(self):
        def _render():
            import streamlit as st
            import ui_theme
            st.session_state["app_dark_mode"] = False
            ui_theme.inject_css()
        at = AppTest.from_function(_render)
        at.run(timeout=30)
        base_css = "".join(m.value for m in at.markdown)
        sel = '[data-testid="stPopoverBody"]:has([data-testid="stExpander"])'
        assert sel in base_css
        assert "height:" in _rule_for(base_css, sel)


def test_discover_embed_iframe_is_documented_as_deliberately_unthemed():
    """Step 68 item 6(b): every st.iframe either follows dark mode (the
    Reader, above) or says why it can't. Discover's embed shows a live
    third-party site the app can't restyle."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tabs", "discover_tab.py")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    idx = text.index("st.iframe(embed_url")
    assert "Deliberately not dark-mode themed" in text[max(0, idx - 400): idx]

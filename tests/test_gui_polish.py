"""
tests/test_gui_polish.py -- Step 12: the subtitle style controls run as an
@st.fragment, so a slider/colour change reruns only that block.

AppTest always reruns the whole script (it doesn't reproduce a
fragment-scoped rerun), so the scoping itself is checked statically; what
the rest of the page depends on -- the current style landing in session
state, in place -- is checked by driving the real widgets.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _decorators(path, func_name):
    with open(os.path.join(PROJECT_ROOT, path), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    [fn] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func_name]
    return [ast.unparse(d) for d in fn.decorator_list]


def test_style_controls_are_a_fragment():
    assert "st.fragment" in _decorators("tabs/workspace_tab.py", "_subtitle_style_fragment")


def _style_app():
    def _render():
        from core import Line
        from tabs.workspace_tab import _subtitle_style_fragment
        _subtitle_style_fragment(7, [Line(idx=0, start=0, end=1, zh="你好", en="Hello")],
                                 {}, "top-center", {"en": 42})
    at = AppTest.from_function(_render)
    at.run(timeout=30)
    return at


def test_current_style_is_published_to_session_state():
    at = _style_app()
    current = at.session_state["sub_style_current_7"]
    assert current["style"]["size"] == 24  # the "Clean" preset's own size
    assert current["style"]["notes_alignment"] == "top-center"
    assert current["wrap_chars"] == {"en": 42}


def test_a_control_change_updates_the_same_dict_in_place():
    """Download buttons outside the fragment capture this dict on a full
    run and read it when clicked -- so a fragment-only rerun has to update
    that same object, not swap in a new one."""
    at = _style_app()
    before = at.session_state["sub_style_current_7"]
    at.slider(key="sub_size_7_Clean").set_value(40).run(timeout=30)
    after = at.session_state["sub_style_current_7"]
    assert after is before
    assert after["style"]["size"] == 40


def test_library_scanlate_live_and_discover_opt_into_the_type_scale():
    """Step 12 item 6: the scale is scoped to these tabs (the rest are
    rebuilt by Steps 14-18), via a marker each one drops first thing.
    discover_tab.py joined this list in Step 17."""
    for path, fn in (("tabs/library_tab.py", "render_library_tab"),
                     ("tabs/scanlate_tab.py", "render_scanlate_tab"),
                     ("tabs/live_tab.py", "render_live_tab"),
                     ("tabs/discover_tab.py", "render_discover_tab")):
        with open(os.path.join(PROJECT_ROOT, path), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        [func] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fn]
        assert ast.unparse(func.body[0]) == "ui_theme.type_scale_scope()", path


def test_type_scale_marker_reaches_the_page():
    """An empty-looking st.markdown block renders as a placeholder, which
    silently drops the marker -- it has to go out through st.html."""
    def _render():
        import ui_theme
        ui_theme.type_scale_scope()
    at = AppTest.from_function(_render)
    at.run(timeout=30)
    assert not at.markdown
    assert 'class="bh-typescale"' in at.get("html")[0].proto.body

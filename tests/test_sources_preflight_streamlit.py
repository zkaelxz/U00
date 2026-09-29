"""Streamlit widget, AppTest and tab-source tests split out of tests/test_sources_preflight.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_sources_preflight.py."""



class TestItIsActuallyReachableInTheApp:
    def test_the_front_door_offers_the_check(self):
        """Every other test here calls `preflight()` directly, which would
        still pass if no button ever called it -- a working function
        nobody can reach, with a green suite. This pins the wiring."""
        import ast

        import tabs.sources_tab as sources_tab
        tree = ast.parse(open(sources_tab.__file__, encoding="utf-8").read())
        front_door_fn = next(node for node in ast.walk(tree)
                             if isinstance(node, ast.FunctionDef)
                             and node.name == "_render_front_door")
        called = {ast.unparse(node.func) for node in ast.walk(front_door_fn)
                  if isinstance(node, ast.Call)}
        assert "src_preflight.preflight" in called

    def test_the_button_key_is_not_the_state_key(self):
        """A real bug this caught: `st.button(key=K)` writes its own
        boolean into `st.session_state[K]` on every rerun, so reusing K to
        hold the preflight result meant the next render found a bool and
        the whole Sources tab crashed with "'bool' object has no
        attribute 'ok'". The widget key and the result key must differ --
        the neighbouring Preview button already does this (src_fd_preview
        vs src_fd_result)."""
        import tabs.sources_tab as sources_tab
        source = open(sources_tab.__file__, encoding="utf-8").read()
        assert 'key="src_fd_preflight_btn"' in source
        assert 'key="src_fd_preflight"' not in source

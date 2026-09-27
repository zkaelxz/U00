"""
tests/test_streamlit_floor.py -- Step 73: requirements said streamlit>=1.49,
but on 1.49.1 itself Workspace crashed (st.tabs(default=/key=)) and Read &
Watch crashed (st.iframe doesn't exist there). The floor is now the real
minimum, found by checking every Streamlit call in the app against each
release's actual signatures and confirmed in a real browser: 1.56.0 runs
every tab, 1.55.0 still crashes Read & Watch.
"""
import ast
import glob
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The newest-API uses that set the floor, with the first Streamlit release
# that has each (verified against 1.49.1-1.56.0's real signatures).
FEATURE_MIN_VERSION = {
    ("iframe", None): (1, 56),
    ("tabs", "key"): (1, 55),
    ("button", "shortcut"): (1, 52),
    ("tabs", "default"): (1, 50),
}


def _floor(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*streamlit\s*>=\s*([0-9.]+)", line)
            if m:
                return tuple(int(p) for p in m.group(1).split("."))
    raise AssertionError(f"no streamlit>= line in {path}")


def _app_files():
    files = [os.path.join(ROOT, "app.py"), os.path.join(ROOT, "ui_theme.py"),
             os.path.join(ROOT, "common.py")]
    files += glob.glob(os.path.join(ROOT, "tabs", "*.py"))
    files += glob.glob(os.path.join(ROOT, "ui", "*.py"))
    return [f for f in files if os.path.exists(f)]


def _streamlit_calls():
    """(file:line, method name, keyword names, called as st.x) for every
    call to a Streamlit element method -- st.x(...) or container.x(...)."""
    st_names = {n for n in dir(st) if not n.startswith("_")}
    for path in _app_files():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            name = node.func.attr
            is_st = isinstance(node.func.value, ast.Name) and node.func.value.id == "st"
            if not is_st and not (name in st_names and hasattr(DeltaGenerator, name)):
                continue
            where = f"{os.path.relpath(path, ROOT)}:{node.lineno}"
            yield where, name, [k.arg for k in node.keywords if k.arg], is_st


def test_requirements_txt_has_no_floor_of_its_own_to_drift():
    """Step 75 turned requirements.txt into a thin `-r` pointer over the
    three tiered files -- it no longer declares streamlit (or anything
    else) directly, so there's nothing for it to drift out of sync with.
    Confirms that structure holds rather than re-asserting a floor match
    that can't exist anymore (superseded by Step 75's own
    test_requirements_txt_is_a_thin_pointer_not_a_fifth_package_list)."""
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        lines = [l.split("#", 1)[0].strip() for l in f]
    lines = [l for l in lines if l]
    assert all(l.startswith("-r ") for l in lines)


def test_floor_is_at_least_what_st_tabs_default_needs():
    # The exit condition named in the roadmap: the version where st.tabs
    # accepts default=, which Workspace's stage tabs use.
    assert _floor("requirements-core.txt") >= FEATURE_MIN_VERSION[("tabs", "default")]


def test_floor_covers_every_newer_feature_the_app_actually_uses():
    floor = _floor("requirements-core.txt")
    used = set()
    for _where, name, kwargs, _is_st in _streamlit_calls():
        used.add((name, None))
        used.update((name, k) for k in kwargs)
    too_new = {feat: ver for feat, ver in FEATURE_MIN_VERSION.items()
               if feat in used and ver > floor}
    assert not too_new, f"requirements floor {floor} is below what these need: {too_new}"
    # And the table isn't stale: the feature that sets the floor is still used.
    assert ("iframe", None) in used


def test_every_streamlit_call_exists_in_the_installed_streamlit():
    """Catches the same class of bug going forward: a new call or keyword
    the installed Streamlit doesn't have fails here, not in a user's tab."""
    problems = []
    for where, name, kwargs, is_st in _streamlit_calls():
        target = getattr(st, name, None) if is_st else getattr(DeltaGenerator, name, None)
        if target is None:
            problems.append(f"{where}: st.{name} doesn't exist")
            continue
        try:
            params = inspect.signature(target).parameters
        except (TypeError, ValueError):
            continue
        if any(p.kind == p.VAR_KEYWORD for p in params.values()):
            continue
        problems += [f"{where}: {name}({k}=) unsupported" for k in kwargs if k not in params]
    assert not problems, "\n".join(problems)

"""The merge helper must keep both sides of a conflict inside an api/schemas/ module."""

import importlib.util
import os

PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "migration", "resolve_slice.py")


def _load():
    spec = importlib.util.spec_from_file_location("resolve_slice", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # must not run the merge on import
    return mod


def test_keep_both_puts_base_then_branch(tmp_path):
    f = tmp_path / "review.py"
    f.write_text("a = 1\n<<<<<<< HEAD\nBRANCH = 1\n=======\nBASE = 1\n>>>>>>> origin/baihe-subtitler\nz = 2\n")
    _load().keep_both(str(f))
    out = f.read_text()
    assert "<<<<<<<" not in out and ">>>>>>>" not in out
    assert out.index("BASE = 1") < out.index("BRANCH = 1")

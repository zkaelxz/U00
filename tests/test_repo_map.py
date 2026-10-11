"""tools/repo_map.py: the on-demand symbol map for small-context models."""

import ast
import contextlib
import importlib.util
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("repo_map", ROOT / "tools" / "repo_map.py")
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)

# A single listing must fit a small model's read: about 10k tokens at ~4 bytes each.
MAX_LISTING_BYTES = 40_000


def _sig(source):
    return rm.render_def(ast.parse(source).body[0])


def _run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rm.main(list(argv))
    return buf.getvalue()


@pytest.mark.parametrize("source, expected", [
    ("def f(a, b=2, *args, c, d=4, **kw): pass", "def f(a, b=2, *args, c, d=4, **kw)"),
    ("def f(a, /, b, *, c): pass", "def f(a, /, b, *, c)"),
    ("def f(x: int = 3, y: 'str' = None) -> bool: pass", "def f(x: int = 3, y: 'str' = None) -> bool"),
    ("async def f(*, timeout: float = 5.0): pass", "async def f(*, timeout: float = 5.0)"),
    ("@router.get('/x', dependencies=[p('a')])\n@cache\ndef f(): pass",
     "@router.get('/x', dependencies=[p('a')]) @cache def f()"),
    ("class C(Base, metaclass=M): pass", "class C(Base, metaclass=M)"),
])
def test_signature_rendering(source, expected):
    assert _sig(source) == expected


def test_long_defaults_are_clipped_and_secrets_and_machine_paths_masked():
    sig = _sig("def f(key='sk-abcdefghijklmnop1234', p='/home/someone/x.db', t='" + "x" * 80 + "'): pass")
    assert "sk-abc" not in sig and "<redacted>" in sig
    assert "/home/someone" not in sig and "<path>" in sig
    assert "x" * 50 not in sig


def test_token_like_runs_are_masked_but_long_identifiers_are_not():
    assert rm.redact("token aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3") == "token <redacted>"
    assert rm.redact("see request_translations_with_retry_and_parse") == "see request_translations_with_retry_and_parse"


def _tree(tmp_path, monkeypatch, files):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(rm, "ROOT", tmp_path)
    return tmp_path


SAMPLE = '''"""sample.py -- a sample module.

More detail that the map does not show.
"""
LIMIT = 3


class Outer(Base):
    """Holds things."""
    name: str
    size: int = 0

    class Inner:
        def deep(self): ...

    def method(self, x=1):
        """Does the method thing."""

    async def amethod(self): ...


def helper(a, *rest, flag=False, **opts):
    """Help. Second sentence is dropped."""
    def nested(): ...
'''


def test_module_listing_shows_classes_methods_nested_classes_fields_and_constants(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, {"sample.py": SAMPLE})
    out = _run("sample")
    assert out.splitlines()[0] == "## sample.py -- a sample module."
    assert "8-19 class Outer(Base)  # Holds things." in out
    assert "  fields: name, size" in out
    assert "  13-14 class Outer.Inner" in out
    assert "    14-14 def Outer.Inner.deep(self)" in out
    assert "  16-17 def Outer.method(self, x=1)  # Does the method thing." in out
    assert "async def Outer.amethod(self)" in out
    assert "def helper(a, *rest, flag=False, **opts)  # Help." in out
    assert "nested" not in out and "Second sentence" not in out
    assert "constants: LIMIT:5" in out


def test_find_searches_names_signatures_and_docstrings(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, {"sample.py": SAMPLE, "pkg/other.py": "def flag_it(): pass\n"})
    out = _run("--find", "flag")
    lines = out.splitlines()
    assert lines[0] == "pkg/other.py:1-1 def flag_it()"
    assert lines[1].startswith("sample.py:22-24 def helper(")
    assert _run("--find", "method thing").startswith("sample.py:16-17 def Outer.method")
    assert "no match" in _run("--find", "nothing-like-this")
    assert "more; add a word" in _run("--find", "e", "--limit", "1")


def test_excluded_paths_are_never_read(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, {
        "keep.py": "def kept(): pass\n",
        "tests/test_x.py": "def in_tests(): pass\n",
        "frontend/node_modules/x/y.py": "def in_node_modules(): pass\n",
        "frontend/src/api/dub.ts": "export const dubApi = {\n  run: (id: number) => post(`/api/dub/${id}/run`),\n}\n",
        "frontend/src/api/dub.test.ts": "export const inTest = 1\n",
        "library/x.py": "def in_library(): pass\n",
        "docs/archive/old.py": "def archived(): pass\n",
        "big.lock.py": "def locked(): pass\n",
        ".claudeignore": "# comment\ndocs/archive/\n*.lock.py\n",
    })
    assert rm.source_files() == ["frontend/src/api/dub.ts", "keep.py"]
    assert "run -> /api/dub/{}/run" in _run("frontend/src/api/dub")


def test_output_is_deterministic_and_never_shows_the_checkout_path(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, {"b.py": SAMPLE, "a.py": '"""a -- first."""\n', "services/s_service.py": ""})
    first = _run(), _run("b"), _run("--find", "o")
    assert first == (_run(), _run("b"), _run("--find", "o"))
    assert all(str(tmp_path) not in out for out in first)
    assert first[0].index("a.py") < first[0].index("b.py")


def test_oversized_module_is_split_at_banner_sections(tmp_path, monkeypatch):
    body = "".join(f"def topic_one_{i}(argument_number_{i}=None): pass\n" for i in range(30))
    body += "\n# " + "-" * 40 + "\n# Second topic\n# " + "-" * 40 + "\n"
    body += "".join(f"def topic_two_{i}(argument_number_{i}=None): pass\n" for i in range(30))
    _tree(tmp_path, monkeypatch, {"big.py": body})
    monkeypatch.setattr(rm, "BUDGET_BYTES", 1500)
    index = _run("big")
    assert "Too large to list at once" in index
    assert ". Second topic [" in index
    parts = [_run("big", "--part", str(n)) for n in range(1, index.count("defs]") + 1)]
    listed = [ln for p in parts for ln in p.splitlines() if " def topic_" in ln]
    assert len(listed) == 60
    assert all(len(p.encode()) <= 1500 * 1.2 for p in parts)


def test_unknown_target_and_bad_part_exit_cleanly(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, {"a.py": "def f(): pass\n"})
    with pytest.raises(SystemExit):
        _run("nope")
    with pytest.raises(SystemExit):
        _run("a", "--part", "9")


def test_real_repo_smoke_every_listing_fits_the_budget(monkeypatch):
    files = rm.source_files()
    monkeypatch.setattr(rm, "source_files", lambda: files)  # walk the checkout once, not per listing
    assert "db/__init__.py" in files and "services/drama_service.py" in files
    assert not any(f.startswith(("tests/", "docs/archive/")) or "node_modules" in f for f in files)
    outputs = {"map": _run()}
    for rel in files:
        mod = rm.load_module(rel)
        assert not mod.error, rel
        outputs[rel] = _run(rel)
        if "Too large to list at once" in outputs[rel]:
            for n in range(1, len(rm.split_parts(mod)) + 1):
                outputs[f"{rel} part {n}"] = _run(rel, "--part", str(n))
    for folder in sorted({f.rsplit("/", 1)[0] for f in files if "/" in f}):
        outputs[folder + "/"] = _run(folder)
    assert "Too large to list at once" in outputs["db/__init__.py"]
    too_big = {k: len(v.encode()) for k, v in outputs.items() if len(v.encode()) > MAX_LISTING_BYTES}
    assert too_big == {}
    assert all(str(ROOT) not in v for v in outputs.values())
    assert "filter_hallucinated_segments" in _run("--find", "hallucinat")

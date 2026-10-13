"""Every Python source compiles without warnings.

An invalid escape such as backslash-d in a non-raw string is only a
SyntaxWarning/DeprecationWarning today, but becomes a SyntaxError in a later
Python, and in a script run via `python -c` it is never seen by the test suite.
"""
import os
import warnings

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKIP = {"frontend", "node_modules", ".git", "venv", ".venv", "__pycache__", "library"}


def _compile_strictly(source: str, name: str):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(source, name, "exec")


def _python_files():
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def test_every_python_file_compiles_without_warnings():
    problems = {}
    for path in _python_files():
        rel = os.path.relpath(path, PROJECT_ROOT)
        with open(path, encoding="utf-8") as f:
            source = f.read()
        try:
            _compile_strictly(source, rel)
        except (SyntaxError, SyntaxWarning, DeprecationWarning) as exc:
            problems[rel] = f"{type(exc).__name__}: {exc}"
    assert problems == {}


def test_embedded_scripts_compile_without_warnings():
    import diagnostics_torch
    _compile_strictly(diagnostics_torch.TORCH_VERIFY_SCRIPT, "<TORCH_VERIFY_SCRIPT>")


def test_checker_catches_an_invalid_escape():
    bad = 'import re\nre.compile("\\d+")\n'
    try:
        _compile_strictly(bad, "<bad>")
    except (SyntaxError, SyntaxWarning, DeprecationWarning):
        return
    raise AssertionError("invalid escape sequence was not flagged")

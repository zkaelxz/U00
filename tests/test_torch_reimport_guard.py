"""
tests/test_torch_reimport_guard.py -- the suite must never leave the real
torch evicted from `sys.modules`.

The bug this exists to stop: torch registers C++ operators when its
module body runs, so running that body twice in one process raises

    RuntimeError: Only a single TORCH_LIBRARY can be used to register
    the namespace triton

Several tests here legitimately install a fake torch to exercise the
"not installed" path. That is fine. The damage is done when the real
module is left evicted afterwards, because the next honest `import torch`
re-executes it and dies -- failing whichever unlucky test did the
importing, for a reason that has nothing to do with it.

It only bites when torch is genuinely installed, which is exactly why it
went unnoticed for so long: the suite was red for every contributor with
the ML bubble-detection stack set up, and green for everyone else.

These tests exercise the guard's own logic directly rather than relying
on test ordering, which would make them fragile in the same way the bug
was invisible.
"""
import sys
import types

import pytest

from .conftest import (_is_real_module, _PROTECTED, remember_real_modules,
                       restore_real_modules)

torch = pytest.importorskip("torch", reason="the guard only matters when torch is real")


class TestTellingARealModuleFromAStandIn:
    def test_the_real_torch_is_recognised(self):
        assert _is_real_module(sys.modules["torch"]) is True

    def test_a_bare_module_object_is_not(self):
        assert _is_real_module(types.ModuleType("torch")) is False

    def test_none_is_not(self):
        assert _is_real_module(None) is False

    def test_a_mock_without_a_file_is_not(self):
        fake = types.SimpleNamespace(cuda=None)
        assert _is_real_module(fake) is False


class TestRestoringWhatATestLeftBehind:
    def test_a_replaced_module_is_put_back(self):
        remember_real_modules()
        real = sys.modules["torch"]
        sys.modules["torch"] = types.ModuleType("torch")
        try:
            assert "torch" in restore_real_modules()
            assert sys.modules["torch"] is real
        finally:
            sys.modules["torch"] = real

    def test_a_deleted_module_is_put_back(self):
        """The case that actually caused the crash: evicted entirely, so
        the next `import torch` re-runs the module body."""
        remember_real_modules()
        real = sys.modules["torch"]
        del sys.modules["torch"]
        try:
            assert "torch" in restore_real_modules()
            assert sys.modules["torch"] is real
        finally:
            sys.modules["torch"] = real

    def test_nothing_is_touched_when_nothing_moved(self):
        remember_real_modules()
        assert restore_real_modules() == []

    def test_importing_after_a_restore_does_not_re_execute(self):
        """The whole point: after the guard runs, `import torch` must
        hand back the module already in memory rather than running its
        body again (which is what raises the duplicate-registration
        error)."""
        remember_real_modules()
        real = sys.modules["torch"]
        del sys.modules["torch"]
        restore_real_modules()
        import torch as reimported
        assert reimported is real


class TestTheGuardCoversTheOtherRegisteringLibraries:
    def test_torchvision_and_torchaudio_are_protected_too(self):
        """They register operators the same way, so they would fail the
        same way; better protected now than discovered later."""
        assert "torch" in _PROTECTED
        assert "torchvision" in _PROTECTED
        assert "torchaudio" in _PROTECTED


class TestTheProbeThatUsedToCrash:
    def test_asking_whether_torch_is_installed_never_imports_it(self):
        """`find_spec` answers from the import system without executing
        the module, so a test that only wants to know "is it installed?"
        can't trigger a re-import."""
        import importlib.util
        real = sys.modules["torch"]
        del sys.modules["torch"]
        try:
            spec = importlib.util.find_spec("torch")
            assert spec is not None
            # Crucially: asking did not put it back / run it.
            assert "torch" not in sys.modules
        finally:
            sys.modules["torch"] = real

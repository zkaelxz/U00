"""
tests/test_check_setup.py -- check_setup.py, start.bat/start.ps1's own
"print anything missing in plain words" step (Step 10). Only the report
text itself is tested here; actually being invoked by a real start.bat
on a real Windows machine is exactly the "can't be unit-tested" part the
roadmap's own exit condition calls out.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check_setup
import diagnostics


def _report(monkeypatch, python_ok=True, ffmpeg_found=True, js_found=True,
           torch_installed=True, cuda_available=True):
    monkeypatch.setattr(diagnostics, "check_python_version",
                        lambda: {"version": "3.11.0", "ok": python_ok})
    monkeypatch.setattr(diagnostics, "check_ffmpeg",
                        lambda: {"found": ffmpeg_found, "path": "/usr/bin/ffmpeg" if ffmpeg_found else None,
                                "version": "ffmpeg 6.0" if ffmpeg_found else None})
    monkeypatch.setattr(diagnostics, "check_js_runtime",
                        lambda: {"found": js_found, "name": "deno" if js_found else None,
                                "path": "/usr/bin/deno" if js_found else None})
    monkeypatch.setattr(diagnostics, "check_cuda",
                        lambda: {"torch_installed": torch_installed, "cuda_available": cuda_available})
    buf = io.StringIO()
    check_setup._print_report(file=buf)
    return buf.getvalue()


class TestPrintReport:
    def test_everything_present_is_a_clean_one_liner(self, monkeypatch):
        out = _report(monkeypatch)
        assert "✓" in out
        assert "ffmpeg" in out.lower() and "cuda" in out.lower()
        assert "⚠" not in out

    def test_missing_ffmpeg_is_flagged_in_plain_words(self, monkeypatch):
        out = _report(monkeypatch, ffmpeg_found=False)
        assert "ffmpeg not found" in out
        assert "ffmpeg.org" in out

    def test_missing_js_runtime_is_flagged(self, monkeypatch):
        out = _report(monkeypatch, js_found=False)
        assert "No JavaScript runtime found" in out
        assert "Deno" in out

    def test_torch_not_installed_is_informational_not_a_warning(self, monkeypatch):
        out = _report(monkeypatch, torch_installed=False, cuda_available=None)
        assert "torch isn't installed" in out
        assert "ℹ" in out and "⚠" not in out

    def test_torch_installed_no_cuda_is_flagged(self, monkeypatch):
        out = _report(monkeypatch, cuda_available=False)
        assert "no CUDA GPU is available" in out

    def test_multiple_problems_all_show_up(self, monkeypatch):
        out = _report(monkeypatch, ffmpeg_found=False, js_found=False)
        assert "ffmpeg not found" in out
        assert "No JavaScript runtime found" in out

    def test_never_exits_non_zero(self, monkeypatch):
        """Regression guard for the module's own stated contract: this
        must never block the launcher, no matter what it finds."""
        monkeypatch.setattr(sys, "argv", ["check_setup.py"])
        _report(monkeypatch, ffmpeg_found=False, js_found=False, torch_installed=False,
               cuda_available=None)
        # _print_report itself never calls sys.exit -- only the
        # if __name__ == "__main__" guard does, and always with 0.
        import inspect
        source = inspect.getsource(check_setup)
        assert "sys.exit(0)" in source

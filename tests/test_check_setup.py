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


class _Cp1252Stream(io.TextIOBase):
    """A stream that behaves like a real Windows cmd.exe console on its
    legacy default codepage -- .encoding says "cp1252", and writing
    anything cp1252 can't represent (⚠, ℹ, ✓ are all outside it) raises
    UnicodeEncodeError exactly like the real console would, instead of
    io.StringIO's always-permissive behavior."""
    encoding = "cp1252"

    def __init__(self):
        self.parts = []

    def write(self, s):
        s.encode(self.encoding)  # raises UnicodeEncodeError, same as a real cp1252 console
        self.parts.append(s)
        return len(s)

    def getvalue(self):
        return "".join(self.parts)


class TestPrintReportOnANonUnicodeConsole:
    """Step 10b follow-up (2026-09-26): a real, confirmed crash on
    Windows -- cp1252 (a real Windows console's legacy default codepage)
    can't encode U+26A0 (⚠), so this report used to crash outright with
    a UnicodeEncodeError before ever finishing. Found on this project's
    own Windows CI job, not just a hypothetical console."""

    def _report_cp1252(self, monkeypatch, **kwargs):
        monkeypatch.setattr(diagnostics, "check_python_version",
                            lambda: {"version": "3.11.0", "ok": kwargs.get("python_ok", True)})
        monkeypatch.setattr(diagnostics, "check_ffmpeg",
                            lambda: {"found": kwargs.get("ffmpeg_found", True), "path": None, "version": None})
        monkeypatch.setattr(diagnostics, "check_js_runtime",
                            lambda: {"found": kwargs.get("js_found", True), "name": None, "path": None})
        monkeypatch.setattr(diagnostics, "check_cuda",
                            lambda: {"torch_installed": kwargs.get("torch_installed", True),
                                    "cuda_available": kwargs.get("cuda_available", True)})
        stream = _Cp1252Stream()
        check_setup._print_report(file=stream)  # would raise UnicodeEncodeError before the fix
        return stream.getvalue()

    def test_the_all_clear_line_does_not_crash_and_stays_readable(self, monkeypatch):
        out = self._report_cp1252(monkeypatch)
        assert "[OK]" in out
        assert "ffmpeg" in out.lower()

    def test_a_warning_does_not_crash_and_stays_readable(self, monkeypatch):
        out = self._report_cp1252(monkeypatch, ffmpeg_found=False)
        assert "[!]" in out
        assert "ffmpeg not found" in out

    def test_an_info_line_does_not_crash_and_stays_readable(self, monkeypatch):
        out = self._report_cp1252(monkeypatch, torch_installed=False, cuda_available=None)
        assert "[i]" in out
        assert "torch isn't installed" in out

    def test_utf8_console_still_gets_the_real_symbols(self, monkeypatch):
        """The fallback is per-encoding, not a blanket downgrade -- a
        console that CAN handle the real symbols still gets them."""
        monkeypatch.setattr(diagnostics, "check_python_version", lambda: {"version": "3.11.0", "ok": True})
        monkeypatch.setattr(diagnostics, "check_ffmpeg", lambda: {"found": False, "path": None, "version": None})
        monkeypatch.setattr(diagnostics, "check_js_runtime", lambda: {"found": True, "name": None, "path": None})
        monkeypatch.setattr(diagnostics, "check_cuda", lambda: {"torch_installed": True, "cuda_available": True})

        class _Utf8Stream(io.StringIO):
            encoding = "utf-8"

        stream = _Utf8Stream()
        check_setup._print_report(file=stream)
        assert "⚠" in stream.getvalue()
        assert "[!]" not in stream.getvalue()

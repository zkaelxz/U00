"""Which OCR backends can run on this PC, and the readable refusal when one
can't. Fakes only: no real OCR, GPU or Tesseract."""
import pytest

import ocr
from lib.proc import CapturedRun


def _present(monkeypatch, modules):
    monkeypatch.setattr(ocr.importlib.util, "find_spec",
                        lambda name: object() if name in modules else None)


def test_every_backend_names_the_missing_package(monkeypatch):
    _present(monkeypatch, set())
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: "/usr/bin/tesseract")
    assert ocr.backend_problem("manga_ocr").package == "manga-ocr"
    assert ocr.backend_problem("paddle").package == "paddleocr"
    assert ocr.backend_problem("tesseract").package == "pytesseract"
    assert ocr.backend_problem("paddle_vl_manga").package == "transformers"


def test_paddle_needs_the_paddlepaddle_wheel_too(monkeypatch):
    _present(monkeypatch, {"paddleocr"})
    assert ocr.backend_problem("paddle").package == "paddlepaddle"
    _present(monkeypatch, {"paddleocr", "paddle"})
    assert ocr.backend_problem("paddle") is None


def test_pytesseract_alone_is_not_tesseract(monkeypatch):
    _present(monkeypatch, {"pytesseract"})
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: None)
    problem = ocr.backend_problem("tesseract")
    assert problem.package == "tesseract"
    assert "/" not in problem.message and "\\" not in problem.message
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: "/usr/bin/tesseract")
    assert ocr.backend_problem("tesseract") is None


def test_configured_tesseract_path_is_the_program_looked_up(monkeypatch):
    _present(monkeypatch, {"pytesseract"})
    seen = []
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: seen.append(cmd) or None)
    assert ocr.backend_problem("tesseract", tesseract_cmd="/opt/t/tesseract") is not None
    assert seen == ["/opt/t/tesseract"]


def test_probe_runs_the_program_with_a_timeout(monkeypatch):
    _present(monkeypatch, {"pytesseract"})
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: "/usr/bin/tesseract")
    calls = []

    def fake_run(cmd, timeout, **kw):
        calls.append((cmd, timeout))
        return CapturedRun(1, "", "boom /secret/path", False, False)

    monkeypatch.setattr(ocr.proc, "run_captured", fake_run)
    problem = ocr.backend_problem("tesseract", probe=True)
    assert problem.package == "tesseract" and "/secret" not in problem.message
    assert calls and calls[0][0][-1] == "--version" and calls[0][1] > 0
    monkeypatch.setattr(ocr.proc, "run_captured",
                        lambda cmd, timeout, **kw: CapturedRun(0, "tesseract 5", "", False, False))
    assert ocr.backend_problem("tesseract", probe=True) is None


def test_probe_timeout_is_a_problem_not_a_hang(monkeypatch):
    _present(monkeypatch, {"pytesseract"})
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr.proc, "run_captured",
                        lambda cmd, timeout, **kw: CapturedRun(None, "", "", True, False))
    assert ocr.backend_problem("tesseract", probe=True).package == "tesseract"


def test_paddle_vl_manga_keeps_its_own_transformers_message(monkeypatch):
    _present(monkeypatch, {"transformers"})
    monkeypatch.setattr(ocr, "paddle_vl_manga_problem", lambda: "update transformers")
    problem = ocr.backend_problem("paddle_vl_manga")
    assert problem.message == "update transformers"


def test_unknown_backend_has_no_problem():
    assert ocr.backend_problem("auto") is None


def test_require_backend_raises_the_readable_error(monkeypatch):
    from lib.errors import DependencyUnavailableError
    _present(monkeypatch, set())
    with pytest.raises(DependencyUnavailableError) as exc:
        ocr.require_backend("manga_ocr")
    assert exc.value.details["package"] == "manga-ocr"
    assert "manga-ocr" in str(exc.value)

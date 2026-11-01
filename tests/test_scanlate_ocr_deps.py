"""Scanlate refuses to start without its OCR/OpenCV dependencies and no
longer treats a failed OCR as a finished page. Fakes only."""
import importlib.util
import os

import pytest

pytest.importorskip("cv2")
pytest.importorskip("PIL")

import background_jobs
import db
import ocr
import scanlate
from services import scanlate_pages_service as pages_svc
from services import scanlate_run_service as run_svc
from lib.errors import DependencyUnavailableError
from tests.job_helpers import wait_for_job


def _drama_with_pages(n=1):
    from PIL import Image
    did = db.create_drama(title_en="Comic", source_language="zh")
    folder = os.path.join(db.drama_dir(did), "pages")
    os.makedirs(folder, exist_ok=True)
    pids = []
    for i in range(n):
        name = f"page_{i:04d}.png"
        Image.new("RGB", (120, 80), (255, 255, 255)).save(os.path.join(folder, name))
        pids.append(db.create_page(did, i, f"pages/{name}", 120, 80))
    return did, pids


def _region(text, translated=""):
    return {"x": 0, "y": 10, "w": 40, "h": 30, "source_text": text, "translated_text": translated,
            "kind": "bubble", "font_size": 12, "skip": False, "include_sfx": False,
            "language": "zh", "orientation": "horizontal", "panel_id": None,
            "kind_confidence": 0.5, "confidence": None, "font_category": "regular"}


@pytest.fixture
def usable_ocr(monkeypatch):
    monkeypatch.setattr(ocr, "require_backend", lambda backend, cmd=None: None)


# --- (1) every backend, (3) tesseract program, (4) paddlepaddle ----------------

@pytest.mark.parametrize("backend,package", [("manga_ocr", "manga-ocr"), ("paddle", "paddleocr"),
                                            ("tesseract", "pytesseract")])
def test_start_refuses_a_missing_ocr_package_and_names_it(isolated_db, monkeypatch, backend, package):
    did, _ = _drama_with_pages()
    monkeypatch.setattr(run_svc.settings_service, "resolve_ocr_backend", lambda lang: backend)
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda name: None if name != "cv2" else object())
    with pytest.raises(DependencyUnavailableError) as exc:
        run_svc.start_run(did, engine="fake")
    assert package in str(exc.value) and exc.value.details["package"] == package


def test_start_refuses_tesseract_without_its_program(isolated_db, monkeypatch):
    did, _ = _drama_with_pages()
    monkeypatch.setattr(run_svc.settings_service, "resolve_ocr_backend", lambda lang: "tesseract")
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: None)
    with pytest.raises(DependencyUnavailableError) as exc:
        run_svc.start_run(did, engine="fake")
    assert exc.value.details["package"] == "tesseract"


def test_ocr_backend_installed_needs_the_tesseract_program(monkeypatch):
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: None)
    assert pages_svc.ocr_backend_installed("tesseract") is False
    monkeypatch.setattr(ocr.shutil, "which", lambda cmd: "/usr/bin/tesseract")
    assert pages_svc.ocr_backend_installed("tesseract") is True


# --- (2) cv2 --------------------------------------------------------------------

def test_start_refuses_without_opencv_before_any_page_runs(isolated_db, monkeypatch, usable_ocr):
    did, _ = _drama_with_pages()
    real = importlib.util.find_spec
    monkeypatch.setattr(run_svc.importlib.util, "find_spec",
                        lambda name: None if name == "cv2" else real(name))
    with pytest.raises(DependencyUnavailableError) as exc:
        run_svc.start_run(did, engine="fake")
    assert exc.value.details["package"] == "opencv-python"
    assert "opencv-python" in str(exc.value)
    assert background_jobs.get_status(pages_svc.job_id(did)) is None


# --- (5) pypdf flag -------------------------------------------------------------

@pytest.mark.parametrize("present", [True, False])
def test_pdf_flag_reflects_whether_pypdf_is_importable(monkeypatch, present):
    monkeypatch.setattr(pages_svc.importlib.util, "find_spec",
                        lambda name: object() if (present and name == "pypdf") else None)
    assert pages_svc.upload_limits()["pdf"] is present


# --- (6) OCR errors are not swallowed ------------------------------------------

def test_ocr_error_is_reported_not_written_as_empty_text(monkeypatch):
    monkeypatch.setattr(scanlate, "decode_page", lambda p: (_ for _ in ()).throw(ValueError()))
    monkeypatch.setattr(scanlate, "detect_bubbles", lambda *a, **k: [{"x": 0, "y": 0, "w": 10, "h": 10}])

    class R:
        def to_bubble(self):
            return _region("")
    monkeypatch.setattr(scanlate, "analyze_page_regions", lambda *a, **k: [R(), R()])

    def boom(*a, **k):
        raise OSError("/home/secret/model.bin missing")
    monkeypatch.setattr(scanlate, "ocr_box_region", boom)
    monkeypatch.setattr(scanlate, "sample_text_style", lambda *a, **k: {"suggested_style": "regular"})
    bubbles, notes = scanlate.detect_and_ocr_page("page.png", "zh")
    errors = [m for level, m in notes if level == "error"]
    assert len(errors) == 1 and "2 of 2" in errors[0]
    assert "secret" not in errors[0] and "/" not in errors[0]


def test_failed_ocr_page_is_failed_and_not_saved_as_existing(isolated_db, monkeypatch, usable_ocr):
    did, (p1, p2) = _drama_with_pages(2)

    def detect(path, lang, page_id=None, **kw):
        if page_id == p1:
            return [_region("")], [("error", "OCR failed on 1 of 1 regions (OSError).")]
        return [_region("你好")], []
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    run_svc.start_run(did, engine="fake")
    st = wait_for_job(pages_svc.job_id(did), timeout=20)
    assert "1 failed" in st["message"], st
    assert db.load_bubbles(p1) == []
    assert db.load_bubbles(p2)
    notes = db.get_page(p1)["run_notes"]
    assert "OCR" in notes and "/" not in notes.replace("\\/", "")


def test_translate_all_reruns_pages_whose_regions_have_no_text(isolated_db, monkeypatch, usable_ocr):
    did, (empty, texted, manual) = _drama_with_pages(3)
    db.save_bubbles(empty, [_region("")])
    db.save_bubbles(texted, [_region("你好")])
    db.save_bubbles(manual, [_region("", translated="typed by hand")])
    seen = []

    def detect(path, lang, page_id=None, **kw):
        seen.append(page_id)
        return [_region("再见")], []
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    run_svc.start_run(did, engine="fake")
    wait_for_job(pages_svc.job_id(did), timeout=20)
    assert seen == [empty]
    assert db.load_bubbles(empty)[0]["source_text"] == "再见"

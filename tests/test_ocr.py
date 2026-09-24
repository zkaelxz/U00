"""
tests/test_ocr.py -- ocr.py's resolve_tesseract_lang(), the Simplified/
Traditional Chinese language-pack selection.

Whisper transcription and LLM translation don't care which Chinese script
the source uses, but Tesseract needs the right language pack (chi_sim vs
chi_tra) or it OCRs Traditional text badly -- these lock in that the
selection logic picks the right one and leaves Japanese/Korean untouched.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import ocr


class TestResolveTesseractLang:
    def test_simplified_chinese_default(self):
        assert ocr.resolve_tesseract_lang("zh") == "chi_sim"

    def test_simplified_explicit(self):
        assert ocr.resolve_tesseract_lang("zh", chinese_script="simplified") == "chi_sim"

    def test_traditional_chinese(self):
        assert ocr.resolve_tesseract_lang("zh", chinese_script="traditional") == "chi_tra"

    def test_japanese_ignores_chinese_script(self):
        assert ocr.resolve_tesseract_lang("ja", chinese_script="traditional") == "jpn"

    def test_korean_ignores_chinese_script(self):
        assert ocr.resolve_tesseract_lang("ko", chinese_script="traditional") == "kor"

    def test_unknown_language_falls_back_to_chi_sim(self):
        assert ocr.resolve_tesseract_lang("xx") == "chi_sim"


class TestExtractTextTesseractUsesExplicitPSM:
    """Regression test for a real bug found via direct testing: with no
    explicit config=, pytesseract.image_to_string() falls back to
    Tesseract's own default page-segmentation mode (PSM 3, "fully
    automatic page segmentation"), which is tuned for a whole scanned
    page -- not a small, pre-cropped single region like a hardsub caption
    band or a page-scan chunk. Confirmed by direct testing that PSM 3 can
    garble short single-line CJK text that PSM 6 ("single uniform block
    of text") reads correctly. Locks in that the explicit PSM is actually
    sent to Tesseract, and can still be overridden by a caller that knows
    better (e.g. PSM 7 for a caption region known to be a single line).
    """
    def _blank_image(self, tmp_path):
        from PIL import Image
        path = tmp_path / "blank.png"
        Image.new("L", (10, 10), color=255).save(path)
        return str(path)

    def test_default_psm_is_six(self, monkeypatch, tmp_path):
        import pytesseract
        captured = {}

        def fake_image_to_string(image, lang=None, config=None):
            captured["lang"] = lang
            captured["config"] = config
            return "text"

        monkeypatch.setattr(pytesseract, "image_to_string", fake_image_to_string)
        ocr.extract_text_tesseract(self._blank_image(tmp_path), lang="jpn")

        assert captured["lang"] == "jpn"
        assert captured["config"] == "--psm 6"

    def test_psm_can_be_overridden(self, monkeypatch, tmp_path):
        import pytesseract
        captured = {}

        def fake_image_to_string(image, lang=None, config=None):
            captured["config"] = config
            return "text"

        monkeypatch.setattr(pytesseract, "image_to_string", fake_image_to_string)
        ocr.extract_text_tesseract(self._blank_image(tmp_path), psm=7)

        assert captured["config"] == "--psm 7"


class TestExtractTextTesseractCustomBinaryPath:
    """On Windows, the Tesseract installer doesn't always add itself to
    PATH, and pytesseract has no way to tell that apart from Tesseract
    genuinely not being installed -- both raise the same
    TesseractNotFoundError. tesseract_cmd lets a person point at the
    binary directly (Settings -> OCR) instead of editing a system PATH
    variable by hand."""

    def _blank_image(self, tmp_path):
        from PIL import Image
        path = tmp_path / "blank.png"
        Image.new("L", (10, 10), color=255).save(path)
        return str(path)

    def test_sets_pytesseract_tesseract_cmd_when_given(self, monkeypatch, tmp_path):
        import pytesseract
        monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: "text")
        monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract", raising=False)

        ocr.extract_text_tesseract(self._blank_image(tmp_path),
                                    tesseract_cmd=r"C:\Program Files\Tesseract-OCR\tesseract.exe")

        assert pytesseract.pytesseract.tesseract_cmd == r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    def test_leaves_default_tesseract_cmd_alone_when_not_given(self, monkeypatch, tmp_path):
        import pytesseract
        monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: "text")
        monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract", raising=False)

        ocr.extract_text_tesseract(self._blank_image(tmp_path))

        assert pytesseract.pytesseract.tesseract_cmd == "tesseract"


class TestExtractTextPaddleUsesCurrentAPI:
    """Regression test for a real bug found via direct testing against an
    actually-installed paddleocr: `pip install paddleocr` today gives 3.x,
    which dropped use_angle_cls and the .ocr(path, cls=True) call entirely
    (TypeError: PaddleOCR.predict() got an unexpected keyword argument
    'cls') and changed the result shape from
    [[(box, (text, confidence)), ...]] per page to a list of dict-like
    result objects with a rec_texts list. Locks in the 3.x call shape so
    this doesn't silently rot again the next time the package updates.
    """
    @pytest.fixture(autouse=True)
    def reset_paddle_instance(self):
        ocr.__dict__.pop("_paddle_instance", None)
        yield
        ocr.__dict__.pop("_paddle_instance", None)

    def test_calls_predict_not_the_removed_ocr_cls_api(self, monkeypatch):
        paddleocr = pytest.importorskip("paddleocr")  # optional, heavy dependency
        captured = {}

        class FakePaddleOCR:
            def __init__(self, **kwargs):
                captured["init_kwargs"] = kwargs

            def predict(self, image_path):
                captured["predict_path"] = image_path
                return [{"rec_texts": ["你好", "世界"]}]

        monkeypatch.setattr(paddleocr, "PaddleOCR", FakePaddleOCR)

        result = ocr.extract_text_paddle("/fake/page.png")

        assert result == "你好\n世界"
        assert captured["predict_path"] == "/fake/page.png"
        assert "use_angle_cls" not in captured["init_kwargs"]


class TestExtractTextFromImagesUsesResolvedLang:
    def test_traditional_script_selects_chi_tra_backend(self, monkeypatch):
        captured = {}

        def fake_tesseract(image_path, lang="chi_sim", tesseract_cmd=None):
            captured["lang"] = lang
            return "extracted text"

        monkeypatch.setattr(ocr, "extract_text_tesseract", fake_tesseract)
        result = ocr.extract_text_from_images(
            ["/fake/page1.png"], backend="tesseract", source_language="zh",
            chinese_script="traditional")

        assert captured["lang"] == "chi_tra"
        assert result == "extracted text"

    def test_simplified_is_the_default(self, monkeypatch):
        captured = {}

        def fake_tesseract(image_path, lang="chi_sim", tesseract_cmd=None):
            captured["lang"] = lang
            return "x"

        monkeypatch.setattr(ocr, "extract_text_tesseract", fake_tesseract)
        ocr.extract_text_from_images(["/fake/page1.png"], source_language="zh")

        assert captured["lang"] == "chi_sim"

    def test_paddle_backend_ignores_chinese_script(self, monkeypatch):
        # PaddleOCR's "ch" model handles both scripts itself -- chinese_script
        # shouldn't need to (and doesn't) affect which function runs.
        called = []
        monkeypatch.setattr(ocr, "extract_text_paddle", lambda p: called.append(p) or "x")
        ocr.extract_text_from_images(["/fake/page1.png"], backend="paddle",
                                       source_language="zh", chinese_script="traditional")
        assert called == ["/fake/page1.png"]

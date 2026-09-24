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


class TestExtractTextFromImagesUsesResolvedLang:
    def test_traditional_script_selects_chi_tra_backend(self, monkeypatch):
        captured = {}

        def fake_tesseract(image_path, lang="chi_sim"):
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

        def fake_tesseract(image_path, lang="chi_sim"):
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

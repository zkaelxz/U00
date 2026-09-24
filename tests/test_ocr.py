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

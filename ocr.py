"""
ocr.py -- extracts text from image-based novel/manga pages (some
platforms serve chapters as image scans rather than selectable text,
specifically to block copy/paste). Feeds into the novel-narration
pipeline as if it were pasted text.

Backends:
  - tesseract: general-purpose, supports Chinese/Japanese/Korean with
    the right language pack. Easiest to install.
  - paddle: higher-accuracy Chinese specifically.
  - manga_ocr: purpose-built for Japanese manga speech bubbles/vertical
    text (what koharu uses under the hood) -- noticeably better than
    Tesseract on stylized fonts and bubble layouts, Japanese only.
"""

import os

# Tesseract language codes per source language. "zh" defaults to
# Simplified -- see resolve_tesseract_lang() for the Traditional variant.
TESSERACT_LANG = {"zh": "chi_sim", "ja": "jpn", "ko": "kor"}
TESSERACT_LANG_ZH_TRADITIONAL = "chi_tra"


def resolve_tesseract_lang(source_language: str, chinese_script: str = "simplified") -> str:
    """chinese_script only matters for "zh" -- ja/ko ignore it. Traditional
    Chinese content (Taiwan, Hong Kong) needs Tesseract's chi_tra language
    pack; running chi_sim against it OCRs badly since the two scripts share
    only some characters.
      Ubuntu: sudo apt install tesseract-ocr-chi-tra
      macOS/Windows: the tesseract-lang / language-selection installer
        already covers this -- select Chinese (Traditional) during setup.
    """
    if source_language == "zh" and chinese_script == "traditional":
        return TESSERACT_LANG_ZH_TRADITIONAL
    return TESSERACT_LANG.get(source_language, "chi_sim")


def extract_text_tesseract(image_path: str, lang: str = "chi_sim", psm: int = 6) -> str:
    """Requires: `pip install pytesseract pillow` + the Tesseract binary
    itself installed system-wide, with the matching language pack.
      macOS:   brew install tesseract tesseract-lang
      Ubuntu:  sudo apt install tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-jpn tesseract-ocr-kor
      Windows: https://github.com/UB-Mannheim/tesseract/wiki (select
               the languages you need during install)

    psm=6 ("single uniform block of text") is set explicitly rather than
    left at Tesseract's own default (PSM 3, "fully automatic page
    segmentation"): confirmed by direct testing that PSM 3 can silently
    drop the last character of a short, single-line CJK image -- exactly
    the shape of a cropped hardsub caption band, and not rare enough on
    novel/manga page scans either to leave on the default.
    """
    import pytesseract
    from PIL import Image
    return pytesseract.image_to_string(Image.open(image_path), lang=lang,
                                        config=f"--psm {psm}")


def extract_text_paddle(image_path: str) -> str:
    """Higher-accuracy alternative for Chinese text specifically.
    Requires: `pip install paddleocr paddlepaddle` (heavier install,
    downloads its own detection/recognition models on first use)."""
    from paddleocr import PaddleOCR
    global _paddle_instance
    if "_paddle_instance" not in globals():
        globals()["_paddle_instance"] = PaddleOCR(use_angle_cls=True, lang="ch")
    result = globals()["_paddle_instance"].ocr(image_path, cls=True)
    lines = []
    for page in result:
        for _box, (text, _confidence) in page:
            lines.append(text)
    return "\n".join(lines)


def extract_text_manga_ocr(image_path: str) -> str:
    """Purpose-built for Japanese manga: trained specifically on speech
    bubbles and vertical/stylized text layouts, so it handles the kind
    of pages Tesseract struggles with. Japanese only.
    Requires: `pip install manga-ocr` (downloads its model on first use).
    Note: designed for single speech-bubble crops, not full pages --
    for best results, crop to one bubble/text block per image. Whole-
    page results will be noisier."""
    from manga_ocr import MangaOcr
    global _manga_ocr_instance
    if "_manga_ocr_instance" not in globals():
        globals()["_manga_ocr_instance"] = MangaOcr()
    return globals()["_manga_ocr_instance"](image_path)


def extract_text_from_images(image_paths, backend: str = "tesseract",
                              source_language: str = "zh",
                              chinese_script: str = "simplified") -> str:
    """Runs OCR over multiple page images (e.g. a whole chapter's worth
    of screenshots) in order and joins them into one block of text,
    ready to feed into the novel-narration pipeline."""
    if backend == "manga_ocr":
        fn = extract_text_manga_ocr
    elif backend == "paddle":
        fn = extract_text_paddle
    else:
        lang = resolve_tesseract_lang(source_language, chinese_script)
        fn = lambda p: extract_text_tesseract(p, lang=lang)

    chunks = []
    for path in image_paths:
        text = fn(path).strip()
        if text:
            chunks.append(text)
    return "\n\n".join(chunks)

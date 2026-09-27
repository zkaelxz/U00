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

# Tesseract language codes per source language. "zh" defaults to
# Simplified -- see resolve_tesseract_lang() for the Traditional variant.
TESSERACT_LANG = {"zh": "chi_sim", "ja": "jpn", "ko": "kor"}
TESSERACT_LANG_ZH_TRADITIONAL = "chi_tra"

# Shared with the UI: Settings' own default-backend picker and Scanlate's
# per-page override both offer the same choices, from this one list.
OCR_BACKEND_OPTIONS = ["auto", "manga_ocr", "paddle", "paddle_vl_manga", "tesseract"]


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


def extract_text_tesseract(image_path: str, lang: str = "chi_sim", psm: int = 6,
                            tesseract_cmd: str = None) -> str:
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

    tesseract_cmd: optional full path to the tesseract binary
    (e.g. "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"). The Windows
    installer doesn't always add itself to PATH, which pytesseract has no
    way to detect on its own -- it just raises TesseractNotFoundError
    with no hint of the actual cause. Set once in Settings -> OCR rather
    than editing a system PATH variable by hand.
    """
    import pytesseract
    from PIL import Image
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    return pytesseract.image_to_string(Image.open(image_path), lang=lang,
                                        config=f"--psm {psm}")


_PADDLE_LANG_BY_SOURCE = {"zh": "ch", "ko": "korean"}


def extract_text_paddle(image_path: str, lang: str = "ch") -> str:
    """Higher-accuracy alternative to Tesseract for Chinese (lang="ch",
    the default) and, since Step 11's OCR auto-routing added it as
    Korean's own default backend, Korean (lang="korean") too.
    Requires: `pip install paddleocr paddlepaddle` (heavier install,
    downloads its own detection/recognition models on first use).

    PaddleOCR rewrote its API in 3.x: PaddleOCR(...).ocr(path, cls=True)
    returning [[(box, (text, confidence)), ...]] per page is gone --
    `pip install paddleocr` installs 3.x today, and the old call raises
    TypeError immediately (confirmed by direct testing). 3.x instead
    exposes .predict(path), returning a list of result objects with a
    rec_texts list. enable_mkldnn=False works around a separate, real
    inference crash (NotImplementedError from the oneDNN backend on at
    least one tested CPU) -- costs some speed, but avoids failing outright
    on affected machines.

    A separate PaddleOCR instance is cached per language, since the
    language is fixed at construction time, not passed per call.
    """
    from paddleocr import PaddleOCR
    global _paddle_instances
    if "_paddle_instances" not in globals():
        globals()["_paddle_instances"] = {}
    instances = globals()["_paddle_instances"]
    if lang not in instances:
        instances[lang] = PaddleOCR(
            use_doc_orientation_classify=False, use_doc_unwarping=False,
            use_textline_orientation=True, lang=lang, enable_mkldnn=False)
    result = instances[lang].predict(image_path)
    lines = []
    for page in result:
        lines.extend(page.get("rec_texts", []))
    return "\n".join(lines)


def extract_text_paddle_vl_manga(image_path: str) -> str:
    """Opt-in second Japanese OCR backend: jzhang533/PaddleOCR-VL-For-Manga
    (Hugging Face, Apache-2.0) -- a manga-specific fine-tune of
    PaddleOCR-VL (1.0B params, BF16), aimed specifically at the
    vertical-Japanese-text degradation that hurts general OCR models.
    70% full-sentence accuracy on Manga109-s crops vs. base
    PaddleOCR-VL's 27%, per its own model card -- but that card only
    benchmarks against base PaddleOCR-VL, not against manga_ocr (already
    Baihe's default Japanese backend), so this isn't a default swap; see
    auto_ocr_backend()'s own docstring. A real head-to-head against
    manga_ocr on your own pages is what decides whether to switch (see
    the Scanlate tab's manual comparison).

    Requires: `pip install transformers huggingface_hub torch`
    Downloads the model on first use (needs internet once; cached after).

    NOTE: same honesty as scanlate.detect_bubbles_ml()'s own docstring --
    written against the documented `transformers` VLM-loading pattern
    (AutoProcessor + AutoModelForCausalLM, trust_remote_code=True, the
    common shape for a HF-hosted vision-language OCR model), not run
    end-to-end in this environment (no network/GPU here). Sanity-check
    against manga_ocr's output on a real page before relying on it.
    """
    from PIL import Image
    from transformers import AutoModelForCausalLM, AutoProcessor

    repo_id = "jzhang533/PaddleOCR-VL-For-Manga"
    global _paddle_vl_manga_model, _paddle_vl_manga_processor
    if "_paddle_vl_manga_model" not in globals():
        globals()["_paddle_vl_manga_processor"] = AutoProcessor.from_pretrained(
            repo_id, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(repo_id, trust_remote_code=True)
        model.eval()
        globals()["_paddle_vl_manga_model"] = model

    model = globals()["_paddle_vl_manga_model"]
    processor = globals()["_paddle_vl_manga_processor"]
    image = Image.open(image_path).convert("RGB")
    inputs = processor(images=image, text="OCR:", return_tensors="pt")
    output_ids = model.generate(**inputs, max_new_tokens=256)
    return processor.batch_decode(output_ids, skip_special_tokens=True)[0].strip()


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
                              chinese_script: str = "simplified",
                              tesseract_cmd: str = None) -> str:
    """Runs OCR over multiple page images (e.g. a whole chapter's worth
    of screenshots) in order and joins them into one block of text,
    ready to feed into the novel-narration pipeline."""
    if backend == "manga_ocr":
        fn = extract_text_manga_ocr
    elif backend == "paddle_vl_manga":
        fn = extract_text_paddle_vl_manga
    elif backend == "paddle":
        paddle_lang = _PADDLE_LANG_BY_SOURCE.get(source_language, "ch")
        fn = lambda p: extract_text_paddle(p, lang=paddle_lang)
    else:
        lang = resolve_tesseract_lang(source_language, chinese_script)
        fn = lambda p: extract_text_tesseract(p, lang=lang, tesseract_cmd=tesseract_cmd)

    chunks = []
    for path in image_paths:
        text = fn(path).strip()
        if text:
            chunks.append(text)
    return "\n\n".join(chunks)

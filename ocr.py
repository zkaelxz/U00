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

import threading

# Tesseract language codes per source language. "zh" defaults to
# Simplified -- see resolve_tesseract_lang() for the Traditional variant.
TESSERACT_LANG = {"zh": "chi_sim", "ja": "jpn", "ko": "kor"}
TESSERACT_LANG_ZH_TRADITIONAL = "chi_tra"

# Shared with the UI: Settings' own default-backend picker and Scanlate's
# per-page override both offer the same choices, from this one list.
OCR_BACKEND_OPTIONS = ["auto", "manga_ocr", "paddle", "paddle_vl_manga", "tesseract"]

# Burned-in video captions only: manga_ocr and paddle_vl_manga are trained on
# manga bubbles, so they are not offered here.
HARDSUB_OCR_BACKEND_OPTIONS = ["tesseract", "paddle", "auto"]


def default_hardsub_backend(source_language: str) -> str:
    """PaddleOCR for Chinese (confirmed more accurate on
    stylized/small captions), Tesseract otherwise."""
    return "paddle" if source_language == "zh" else "tesseract"


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


_TESSERACT_CMD_LOCK = threading.Lock()


# One page should take seconds; a tesseract that hangs on a damaged image
# would otherwise stall a whole chapter job that Cancel cannot reach.
TESSERACT_PAGE_TIMEOUT_SECONDS = 120


def extract_text_tesseract(image_path: str, lang: str = "chi_sim", psm: int = 6,
                            tesseract_cmd: str = None, on_timeout=None) -> str:
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

    on_timeout: called when the page timed out and was dropped.

    tesseract_cmd: optional full path to the tesseract binary
    (e.g. "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"). The Windows
    installer doesn't always add itself to PATH, which pytesseract has no
    way to detect on its own -- it just raises TesseractNotFoundError
    with no hint of the actual cause. Set once in Settings -> OCR rather
    than editing a system PATH variable by hand.
    """
    import pytesseract
    from PIL import Image
    # pytesseract only reads a module-level tesseract_cmd. Set it for this
    # call and put it back after, under a lock, so one run's path never
    # leaks into a later or concurrent run.
    with _TESSERACT_CMD_LOCK:
        previous = pytesseract.pytesseract.tesseract_cmd
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        try:
            return pytesseract.image_to_string(Image.open(image_path), lang=lang,
                                                config=f"--psm {psm}",
                                                timeout=TESSERACT_PAGE_TIMEOUT_SECONDS)
        except RuntimeError as exc:
            # pytesseract signals a timeout as a bare RuntimeError; the page
            # is lost but the rest of the chapter is still worth reading.
            if "timeout" not in str(exc).lower():
                raise
            if on_timeout is not None:
                on_timeout()
            return ""
        finally:
            pytesseract.pytesseract.tesseract_cmd = previous


_PADDLE_LANG_BY_SOURCE = {"zh": "ch", "ko": "korean"}

# Pinned for lang="ch" so the real-model check can tell, without loading
# anything, whether the exact models this call needs are already on disk;
# left to PaddleX the choice shifts with its version and a leftover folder
# from another version would look like the right one.
_PADDLE_CH_MODELS = {
    "text_detection_model_name": "PP-OCRv5_server_det",
    "text_recognition_model_name": "PP-OCRv5_server_rec",
    "textline_orientation_model_name": "PP-LCNet_x1_0_textline_ori",
}


def extract_text_paddle(image_path: str, lang: str = "ch") -> str:
    """Higher-accuracy alternative to Tesseract for Chinese (lang="ch",
    the default) and, since OCR auto-routing added it as
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
            use_textline_orientation=True, lang=lang, enable_mkldnn=False,
            **(_PADDLE_CH_MODELS if lang == "ch" else {}))
    result = instances[lang].predict(image_path)
    lines = []
    for page in result:
        lines.extend(page.get("rec_texts", []))
    return "\n".join(lines)


_PADDLE_VL_MANGA_REPO = "jzhang533/PaddleOCR-VL-For-Manga"
# The manga repo's tokenizer files lack `image_token`, so its own processor
# fails to load; the base repo's processor is compatible with the manga weights.
_PADDLE_VL_PROCESSOR_REPO = "PaddlePaddle/PaddleOCR-VL"
_PADDLE_VL_MIN_TRANSFORMERS_MAJOR = 5
_PADDLE_VL_OLD_TRANSFORMERS = (
    "PaddleOCR-VL-For-Manga needs transformers 5 or newer. Update transformers in Diagnostics.")


def paddle_vl_manga_problem():
    """Fixed-text reason the PaddleOCR-VL-For-Manga backend can't run
    here, or None. Shared by the extractor and the Scanlate start check so
    both show the same message."""
    import importlib.metadata
    try:
        installed = importlib.metadata.version("transformers")
    except importlib.metadata.PackageNotFoundError:
        return "PaddleOCR-VL-For-Manga needs transformers and torch. Install them in Diagnostics."
    digits = "".join(ch for ch in installed.split(".")[0] if ch.isdigit())
    if int(digits or 0) < _PADDLE_VL_MIN_TRANSFORMERS_MAJOR:
        return _PADDLE_VL_OLD_TRANSFORMERS
    return None


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

    Requires: `pip install "transformers>=5" huggingface_hub torch`
    Downloads the model on first use (needs internet once; cached after).

    Uses transformers' native `paddleocr_vl` model, not the repo's remote
    modeling code: that code targets transformers 4.57 and fails on 5.x
    (KeyError: 'default' in its RoPE init), needs einops, and would run
    downloaded code.

    Verified (transformers 5.19.0, CPU torch, real weights): a rendered
    crop of "今日はいい天気ですね" OCRs to exactly that string; loading prints
    a harmless warning about tied lm_head weights. NOT verified: real
    manga pages, vertical text, the GPU/bfloat16 path, speed, or the
    256-token limit. Compare against manga_ocr on a real page before
    relying on it.
    """
    problem = paddle_vl_manga_problem()
    if problem:
        from lib.errors import DependencyUnavailableError
        raise DependencyUnavailableError(problem)
    from PIL import Image
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor

    global _paddle_vl_manga_model, _paddle_vl_manga_processor
    if "_paddle_vl_manga_model" not in globals():
        cuda = torch.cuda.is_available()
        if cuda:
            from services import vram_service
            vram_service.check_fits("PaddleOCR-VL-For-Manga")
        globals()["_paddle_vl_manga_processor"] = AutoProcessor.from_pretrained(
            _PADDLE_VL_PROCESSOR_REPO, trust_remote_code=False)
        model = AutoModelForImageTextToText.from_pretrained(
            _PADDLE_VL_MANGA_REPO, trust_remote_code=False,
            dtype=torch.bfloat16 if cuda else torch.float32)
        if cuda:
            model = model.to("cuda")
        model.eval()
        globals()["_paddle_vl_manga_model"] = model

    model = globals()["_paddle_vl_manga_model"]
    processor = globals()["_paddle_vl_manga_processor"]
    image = Image.open(image_path).convert("RGB")
    messages = [{"role": "user", "content": [{"type": "image", "image": image},
                                             {"type": "text", "text": "OCR:"}]}]
    inputs = processor.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True, return_dict=True,
        return_tensors="pt").to(model.device, dtype=model.dtype)
    output_ids = model.generate(**inputs, max_new_tokens=256)
    # generate() returns prompt + answer; decoding the prompt too would put
    # the "OCR:" instruction in the extracted text.
    generated = output_ids[:, inputs["input_ids"].shape[1]:]
    return processor.batch_decode(generated, skip_special_tokens=True)[0].strip()


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
                              tesseract_cmd: str = None, before_page=None, on_skip=None) -> str:
    """Runs OCR over multiple page images (e.g. a whole chapter's worth
    of screenshots) in order and joins them into one block of text,
    ready to feed into the novel-narration pipeline. before_page is called
    ahead of each page so a caller can stop the run by raising; on_skip(path)
    is called for each page Tesseract timed out on."""
    if backend == "manga_ocr":
        fn = extract_text_manga_ocr
    elif backend == "paddle_vl_manga":
        fn = extract_text_paddle_vl_manga
    elif backend == "paddle":
        paddle_lang = _PADDLE_LANG_BY_SOURCE.get(source_language, "ch")
        fn = lambda p: extract_text_paddle(p, lang=paddle_lang)
    else:
        lang = resolve_tesseract_lang(source_language, chinese_script)
        fn = lambda p: extract_text_tesseract(
            p, lang=lang, tesseract_cmd=tesseract_cmd,
            **({"on_timeout": lambda: on_skip(p)} if on_skip is not None else {}))

    chunks = []
    for path in image_paths:
        if before_page is not None:
            before_page()
        text = fn(path).strip()
        if text:
            chunks.append(text)
    return "\n\n".join(chunks)

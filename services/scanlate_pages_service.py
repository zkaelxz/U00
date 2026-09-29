"""
services/scanlate_pages_service.py -- Scanlate pages for the API
(docs/specs/scanlate-api-spec.md S1 and S2): the panel's config, one page's
detail (regions keyed by their stable id, the page rev and its run notes),
and page import (upload or a link import, SO06) with fixed limits.

Also holds what the Scanlate job services share: the per-drama job id
(`scanlate_<drama_id>`, one job at a time per drama), the per-drama upload
claim a job refuses to start under (and vice versa), the pipeline lock
shared with the extension bridge, and the note cleaner (secrets redacted,
paths stripped) every stored or returned note goes through.

Import limits (user-set 2026-09-29; adjust them here, nowhere else):
PNG/JPEG/WebP/PDF only (the file's own bytes decide, the extension must
agree); per image 30 MB and 100 megapixels (checked from the header before
any decode); per PDF 300 MB and 500 pages (each embedded image also
pixel-capped before decode); per request 300 files and 1 GB. Images taller
than 3x their width are sliced into pages (on by default). EXIF orientation
is applied and every page is re-encoded from its pixels (no metadata kept,
WebP stored as PNG). Nothing is stored under a client name; files land by
atomic rename; idx is MAX(idx)+1 under a per-drama lock.

No FastAPI or Streamlit import. Plain dicts; errors from service_errors.
"""
import contextlib
import importlib.util
import json
import os
import re
import shutil
import tempfile
import threading
import warnings

import background_jobs
import db
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)

# --- Import limits (the one place to change them) -------------------------
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
PDF_EXTENSIONS = (".pdf",)
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_IMAGE_PIXELS = 100_000_000
MAX_PDF_BYTES = 300 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_FILES_PER_UPLOAD = 300
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
STRIP_SLICE_RATIO = 3          # slice when height > STRIP_SLICE_RATIO x width
SLICE_STRIPS_DEFAULT = True

JOB_PREFIX = "scanlate_"
DETECT_BACKENDS = ("auto", "cv", "ml")
_OCR_MODULES = {"manga_ocr": "manga_ocr", "paddle": "paddleocr",
                "paddle_vl_manga": "transformers", "tesseract": "pytesseract"}
_CHUNK = 1024 * 1024
_MAX_NOTES = 20
_MAX_NOTE_CHARS = 500

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_BUSY_JOB = "A Scanlate job is running for this drama. Wait for it or cancel it first."
_BUSY_UPLOAD = "Pages are being added to this drama. Try again when that finishes."


# --- Shared by the job services ------------------------------------------

def job_id(drama_id: int) -> str:
    return f"{JOB_PREFIX}{drama_id}"


def pipeline_lock():
    """The lock every Scanlate model run holds: scanlate's and ocr's model
    caches are unlocked module globals (spec §4). It is the extension
    bridge's own _PIPELINE_LOCK, so the bridge and the API jobs in this
    process queue behind each other instead of racing on first load."""
    import page_server
    return page_server._PIPELINE_LOCK


_claims_lock = threading.Lock()
_uploading = set()
_page_locks = {}


def _page_lock(drama_id: int) -> threading.Lock:
    with _claims_lock:
        return _page_locks.setdefault(drama_id, threading.Lock())


def job_busy(drama_id: int) -> bool:
    job = background_jobs.get_status(job_id(drama_id))
    return bool(job and job["status"] in ("running", "queued"))


@contextlib.contextmanager
def upload_claim(drama_id: int):
    """Held for the whole import: refused (409) while this drama's Scanlate
    job runs or another import is adding pages to it."""
    with _claims_lock:
        if job_busy(drama_id):
            raise ConflictError(_BUSY_JOB)
        if drama_id in _uploading:
            raise ConflictError(_BUSY_UPLOAD)
        _uploading.add(drama_id)
    try:
        yield
    finally:
        with _claims_lock:
            _uploading.discard(drama_id)


def start_drama_job(drama_id: int, target, *args, description: str) -> dict:
    """Starts the drama's one Scanlate job (gpu_touching: OCR and the ML
    detector/LaMa may use the GPU). 409 while it or an import runs."""
    jid = job_id(drama_id)
    with _claims_lock:
        if drama_id in _uploading:
            raise ConflictError(_BUSY_UPLOAD)
        if not background_jobs.start_job(jid, target, *args, gpu_touching=True,
                                         description=description):
            raise ConflictError(_BUSY_JOB)
    return {"job_id": jid}


_PATH_RE = re.compile(r"(?<![\w.:/\\])(?:[A-Za-z]:[\\/]|\\\\|/)[^\s'\"<>()]+")


def clean_note(text) -> str:
    """Redacts anything key-like and replaces absolute paths, so a note is
    safe to store and return (spec §4 secrets and paths)."""
    from translate_engines import redact_secrets
    text = redact_secrets(str(text or ""))
    text = _PATH_RE.sub("<path>", text).replace("**", "")
    return text.strip()[:_MAX_NOTE_CHARS]


def notes_to_json(notes) -> str:
    """[(level, message) | (level, code, message)] -> stored run_notes JSON."""
    out = []
    for n in list(notes or [])[:_MAX_NOTES]:
        level, message = n[0], n[-1]
        out.append({"level": level if level in ("info", "warning", "error") else "warning",
                    "message": clean_note(message)})
    return json.dumps(out, ensure_ascii=False)


def _parse_notes(raw) -> list:
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [{"level": str(n.get("level", "warning")), "message": clean_note(n.get("message"))}
            for n in data if isinstance(n, dict)][:_MAX_NOTES] if isinstance(data, list) else []


# --- S1: read -------------------------------------------------------------

def require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def require_page(drama_id: int, page_id: int) -> dict:
    page = db.get_page(page_id, drama_id=drama_id)
    if page is None:
        raise NotFoundError("No such page in this drama.")
    return page


def ocr_backend_installed(backend: str) -> bool:
    module = _OCR_MODULES.get(backend)
    return bool(module and importlib.util.find_spec(module) is not None)


def _weights_cached():
    try:
        import scanlate
        return scanlate.bubble_ml_weights_cached(), scanlate.lama_ml_weights_cached()
    except Exception:
        return False, False


def get_config(drama_id: int) -> dict:
    """What the React panel needs: engines with key_configured booleans
    (never keys), the default engine, whether the ML detector and LaMa
    weights are cached, the saved OCR backend (and whether it is
    installed), the upload limits, page counts and the job state."""
    from services import settings_service, translate_service
    drama = require_drama(drama_id)
    lang = drama.get("source_language") or "zh"
    ocr_backend = settings_service.resolve_ocr_backend(lang)
    ml_cached, lama_cached = _weights_cached()
    pages = db.list_pages(drama_id)
    with_regions = {b["page_id"] for b in db.list_bubbles_for_drama(drama_id)}
    job = background_jobs.get_status(job_id(drama_id))
    return {
        "drama_id": drama_id,
        "source_language": lang,
        "engines": [{"name": e["name"], "label": e["label"], "free": e["free"],
                     "key_configured": e["key_configured"]}
                    for e in translate_service.list_engines()],
        "default_engine": settings_service.get_default_engine(),
        "detect_backends": list(DETECT_BACKENDS),
        "ml_weights_cached": ml_cached,
        "lama_weights_cached": lama_cached,
        "ocr_backend": ocr_backend,
        "ocr_backend_installed": ocr_backend_installed(ocr_backend),
        "page_count": len(pages),
        "pages_with_regions": sum(1 for p in pages if p["id"] in with_regions),
        "pages_rendered": sum(1 for p in pages if p.get("rendered_filename")),
        "job_id": job_id(drama_id),
        "job_running": bool(job and job["status"] in ("running", "queued")),
        "upload_limits": upload_limits(),
    }


def upload_limits() -> dict:
    return {"image_types": ["png", "jpg", "jpeg", "webp"], "pdf": True,
            "max_image_mb": MAX_IMAGE_BYTES // (1024 * 1024),
            "max_image_megapixels": MAX_IMAGE_PIXELS // 1_000_000,
            "max_pdf_mb": MAX_PDF_BYTES // (1024 * 1024), "max_pdf_pages": MAX_PDF_PAGES,
            "max_files": MAX_FILES_PER_UPLOAD,
            "max_total_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "strip_slice_ratio": STRIP_SLICE_RATIO,
            "slice_strips_default": SLICE_STRIPS_DEFAULT}


def _region_view(b: dict) -> dict:
    return {"id": b["id"], "idx": int(b.get("idx") or 0),
            "x": int(b.get("x") or 0), "y": int(b.get("y") or 0),
            "w": int(b.get("w") or 0), "h": int(b.get("h") or 0),
            "source_text": b.get("source_text") or "",
            "translated_text": b.get("translated_text") or "",
            "font_size": int(b.get("font_size") or 18),
            "font_category": b.get("font_category") or "regular",
            "kind": b.get("kind") or "bubble",
            "skip": bool(b.get("skip")), "include_sfx": bool(b.get("include_sfx")),
            "language": b.get("language"), "orientation": b.get("orientation")}


def get_page_detail(drama_id: int, page_id: int) -> dict:
    """One page: every region (skipped and SFX included, keyed by stable
    id), rev, whether it has a typeset image, and its last run notes. No
    file names or paths."""
    require_drama(drama_id)
    page = require_page(drama_id, page_id)
    ordinal = next((i + 1 for i, p in enumerate(db.list_pages(drama_id))
                    if p["id"] == page_id), 0)
    return {"id": page["id"], "drama_id": drama_id, "ordinal": ordinal,
            "width": int(page.get("width") or 0), "height": int(page.get("height") or 0),
            "rev": int(page.get("rev") or 0),
            "has_rendered": bool(page.get("rendered_filename")),
            "regions": [_region_view(b) for b in db.load_bubbles(page_id)],
            "run_notes": _parse_notes(page.get("run_notes"))}


def list_run_notes(drama_id: int) -> dict:
    """Every page's last run notes (pages with none left out)."""
    require_drama(drama_id)
    out = []
    for i, p in enumerate(db.list_pages(drama_id)):
        notes = _parse_notes(p.get("run_notes"))
        if notes:
            out.append({"page_id": p["id"], "ordinal": i + 1, "notes": notes})
    return {"drama_id": drama_id, "pages": out}


# --- S2: import -------------------------------------------------------------

def _safe_extension(name) -> str:
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    if any(ord(c) < 32 or ord(c) == 127 for c in base):
        raise InvalidInputError("Unsupported file type. Upload PNG, JPEG, WebP or PDF files.")
    ext = os.path.splitext(base)[1].lower()
    if ext not in IMAGE_EXTENSIONS + PDF_EXTENSIONS:
        raise InvalidInputError("Unsupported file type. Upload PNG, JPEG, WebP or PDF files.")
    return ext


def _sniff(head: bytes):
    if head.startswith(_PNG):
        return "png"
    if head.startswith(_JPEG):
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"%PDF-"):
        return "pdf"
    return None


def _open_image(path: str):
    """Pillow image, header only (no decode yet), limited to PNG/JPEG/WebP."""
    from PIL import Image
    return Image.open(path, formats=("PNG", "JPEG", "WEBP"))


def _check_pixels(width: int, height: int, what: str):
    if width < 1 or height < 1:
        raise InvalidInputError(f"{what} is empty or corrupt.")
    if width * height > MAX_IMAGE_PIXELS:
        raise InvalidInputError(
            f"{what} is too large (at most {MAX_IMAGE_PIXELS // 1_000_000} megapixels).")


def _normalise(img):
    """EXIF orientation applied, then a fresh pixel-only image (no EXIF,
    ICC or text chunks) in a mode PNG/JPEG can store."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
    return Image.frombytes(img.mode, img.size, img.tobytes())


def _stage_image(src: str, kind: str, out_dir: str, stem: str, slice_strips: bool) -> list:
    """Checked, re-encoded page file(s) in out_dir, in reading order."""
    what = "An image"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")      # DecompressionBombWarning: capped below
            with _open_image(src) as img:
                _check_pixels(*img.size, what)
                img.load()
                clean = _normalise(img)
    except InvalidInputError:
        raise
    except Exception:
        raise InvalidInputError("An image could not be read (corrupt or unsupported).") from None
    w, h = clean.size
    keep_jpeg = kind == "jpeg" and clean.mode in ("RGB", "L")
    if slice_strips and h > STRIP_SLICE_RATIO * w:
        import scanlate
        strip = os.path.join(out_dir, f"{stem}_strip.png")
        clean.save(strip, "PNG")
        slice_dir = os.path.join(out_dir, f"{stem}_slices")
        os.makedirs(slice_dir)
        try:
            return scanlate.slice_webtoon_to_files(strip, slice_dir)
        except Exception:
            raise InvalidInputError("A tall strip could not be sliced into pages.") from None
        finally:
            os.remove(strip)
    out = os.path.join(out_dir, f"{stem}.{'jpg' if keep_jpeg else 'png'}")
    if keep_jpeg:
        clean.save(out, "JPEG", quality=95)
    else:
        clean.save(out, "PNG")
    return [out]


def _pdf_best_image(page):
    """(pixels, name, w, h) of a PDF page's largest image XObject, read from
    its dictionary without decoding it; None if it has none."""
    best = None
    res = page.get("/Resources")
    res = res.get_object() if res is not None else None
    xobjs = res.get("/XObject") if res is not None else None
    xobjs = xobjs.get_object() if xobjs is not None else None
    for name in (xobjs or {}):
        obj = xobjs[name].get_object()
        if obj.get("/Subtype") != "/Image":
            continue
        w, h = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
        if best is None or w * h > best[0]:
            best = (w * h, name, w, h)
    return best


def _stage_pdf(src: str, out_dir: str, stem: str) -> tuple:
    """(page files, pages skipped for having no image). A scanned-manga PDF
    is one raster image per page; the largest one is the page (a small
    logo or watermark may ride along), as scanlate.pdf_to_page_images."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DependencyUnavailableError(
            "PDF import needs the pypdf package (install it from Diagnostics).") from None
    try:
        reader = PdfReader(src)
        if reader.is_encrypted:
            raise InvalidInputError("That PDF is password-protected.")
        count = len(reader.pages)
    except InvalidInputError:
        raise
    except Exception:
        raise InvalidInputError("A PDF could not be read (corrupt or unsupported).") from None
    if count > MAX_PDF_PAGES:
        raise InvalidInputError(f"A PDF has too many pages (at most {MAX_PDF_PAGES}).")
    outputs, skipped = [], 0
    for i in range(count):
        try:
            page = reader.pages[i]
            best = _pdf_best_image(page)
        except Exception:
            best = None
        if best is None:
            skipped += 1
            continue
        _check_pixels(best[2], best[3], f"PDF page {i + 1}'s image")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                img = page.images[best[1]].image
                _check_pixels(*img.size, f"PDF page {i + 1}'s image")
                clean = _normalise(img)
        except InvalidInputError:
            raise
        except Exception:
            skipped += 1
            continue
        out = os.path.join(out_dir, f"{stem}_p{i:04d}.png")
        clean.convert("RGB").save(out, "PNG")
        outputs.append(out)
    return outputs, skipped


def _copy_capped(fileobj, dest: str, limit: int, budget: list) -> int:
    size = 0
    with open(dest, "wb") as out:
        while True:
            chunk = fileobj.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            budget[0] -= len(chunk)
            if size > limit:
                raise InvalidInputError(
                    f"A file is too large (at most {limit // (1024 * 1024)} MB).")
            if budget[0] < 0:
                raise InvalidInputError(
                    f"The upload is too large (at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
                    "in total).")
            out.write(chunk)
    if size == 0:
        raise InvalidInputError("A file is empty.")
    return size


def _unique_page_name(pages_dir: str, idx: int, ext: str) -> str:
    name = f"page_{idx:04d}{ext}"
    n = 1
    while os.path.lexists(os.path.join(pages_dir, name)):
        name = f"page_{idx:04d}_{n}{ext}"
        n += 1
    return name


def add_page_images(drama_id: int, files, slice_strips: bool = SLICE_STRIPS_DEFAULT) -> dict:
    """Adds `files` as the drama's next pages, in order. `files` is a list
    of (client_name, binary file-like) pairs; only the name's extension is
    read (and must be a whitelisted one agreeing with the file's bytes).
    Every file is checked and converted before any page is added, so a bad
    file adds nothing (422). 409 while this drama's Scanlate job or another
    import runs. Returns {added, page_ids, pdf_pages_skipped, strips_sliced}.
    The shared entry point for the upload route and link imports (SO06)."""
    require_drama(drama_id)
    files = list(files or [])
    if not files:
        raise InvalidInputError("Choose at least one page image or PDF.")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise InvalidInputError(f"Too many files (at most {MAX_FILES_PER_UPLOAD} at once).")
    exts = [_safe_extension(name) for name, _f in files]
    with upload_claim(drama_id):
        staging = tempfile.mkdtemp(prefix="baihe_scanlate_")
        try:
            staged, pdf_skipped, sliced = [], 0, 0
            budget = [MAX_UPLOAD_BYTES]
            for n, ((_name, fileobj), ext) in enumerate(zip(files, exts)):
                is_pdf = ext in PDF_EXTENSIONS
                raw = os.path.join(staging, f"in_{n:04d}{'.pdf' if is_pdf else '.img'}")
                _copy_capped(fileobj, raw, MAX_PDF_BYTES if is_pdf else MAX_IMAGE_BYTES, budget)
                with open(raw, "rb") as f:
                    kind = _sniff(f.read(16))
                if kind is None or (kind == "pdf") != is_pdf:
                    raise InvalidInputError(
                        "A file's contents don't match its type. Upload PNG, JPEG, WebP or PDF.")
                if is_pdf:
                    outs, skipped = _stage_pdf(raw, staging, f"f{n:04d}")
                    pdf_skipped += skipped
                else:
                    outs = _stage_image(raw, kind, staging, f"f{n:04d}", slice_strips)
                    sliced += 1 if len(outs) > 1 else 0
                os.remove(raw)
                staged.extend(outs)
            page_ids = _commit_pages(drama_id, staged)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return {"added": len(page_ids), "page_ids": page_ids,
            "pdf_pages_skipped": pdf_skipped, "strips_sliced": sliced}


def _commit_pages(drama_id: int, staged: list) -> list:
    pages_dir = os.path.join(db.drama_dir(drama_id), "pages")
    os.makedirs(pages_dir, exist_ok=True)
    page_ids = []
    with _page_lock(drama_id):
        idx = db.next_page_idx(drama_id)
        for path in staged:
            from PIL import Image
            with Image.open(path) as im:
                w, h = im.size
            ext = os.path.splitext(path)[1].lower()
            name = _unique_page_name(pages_dir, idx, ext)
            fd, part = tempfile.mkstemp(prefix=".page_", suffix=".part", dir=pages_dir)
            try:
                with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
                    shutil.copyfileobj(src, out)
                os.replace(part, os.path.join(pages_dir, name))
            except BaseException:
                if os.path.exists(part):
                    os.remove(part)
                raise
            page_ids.append(db.create_page(drama_id, idx, f"pages/{name}", w, h))
            idx += 1
    return page_ids
